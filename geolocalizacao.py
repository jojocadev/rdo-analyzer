"""
Conferência geográfica das fotos de um RDO.

As fotos dos RDOs trazem a coordenada carimbada sobre o pixel pelo app de câmera.
Ela não está no EXIF (o PDF descarta ao embutir) nem no texto do PDF — só na
imagem. Este módulo lê a coordenada por OCR e, para cada PDF, verifica se todas
as fotos foram tiradas dentro de um raio da escola.

OCR: RapidOCR (Apache 2.0), local e gratuito, sem API nem cota.

Centro do raio: a MEDIANA das coordenadas das fotos do PDF, não a foto da
fachada. A mediana é robusta — se a própria fachada vier com coordenada errada,
usá-la como centro deslocaria o raio e marcaria todas as outras fotos por engano.
A distância entre a fachada e a mediana é reportada à parte, para conferência.

Uso pela linha de comando:
    python geolocalizacao.py --fase 5 --amostra 20    # demonstração
    python geolocalizacao.py --fase 5                 # tudo
"""

import argparse
import io
import json
import math
import os
import re
import statistics
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIR_THUMBS = os.path.join(BASE_DIR, "extracted_images")

RAIO_METROS = 50.0

# Acima disto a coordenada e tratada como leitura suspeita, nao como foto fora do
# lugar: ninguem fotografa a escola a 5 km dela. Marcar erro de OCR como
# irregularidade faria o auditor perder a confianca na flag.
LIMITE_SUSPEITA_M = 5000.0

# O OCR troca a vírgula separadora por outro caractere com frequência (já vi o
# CJK "、"), então o separador aceita qualquer coisa que não seja dígito ou sinal.
_COORD = re.compile(
    r"(-?\d{1,2}[.,]\d{3,})\s*°?\s*([SN])?[^\d+-]{1,4}(-?\d{1,3}[.,]\d{3,})\s*°?\s*([WOE])?",
    re.I)

_ocr = None
_ocr_lock = threading.Lock()


def _motor():
    """Carrega o OCR uma vez só (o modelo leva alguns segundos para subir)."""
    global _ocr
    if _ocr is None:
        with _ocr_lock:
            if _ocr is None:
                from rapidocr_onnxruntime import RapidOCR
                _ocr = RapidOCR()
    return _ocr


def extrair_coordenada(textos):
    """
    Encontra um par lat/long no texto lido pelo OCR.

    Trata duas imperfeições comuns: o separador vem como caractere estranho e o
    sinal de menos se perde. Como o Brasil inteiro tem latitude e longitude
    negativas, o sinal pode ser reposto quando a magnitude cai na faixa do país.
    """
    junto = " ".join(textos)
    for m in _COORD.finditer(junto):
        try:
            lat = float(m.group(1).replace(",", "."))
            lon = float(m.group(3).replace(",", "."))
        except ValueError:
            continue

        if m.group(2) and m.group(2).upper() == "S":
            lat = -abs(lat)
        if m.group(4) and m.group(4).upper() in ("W", "O"):
            lon = -abs(lon)
        if 0 < lat <= 34:
            lat = -lat
        if 0 < lon <= 74:
            lon = -lon

        # Brasil continental
        if -34 <= lat <= 5.5 and -74 <= lon <= -34:
            return round(lat, 6), round(lon, 6)
    return None


def ler_coordenada(caminho):
    """Roda o OCR numa imagem e devolve (lat, lon) ou None."""
    try:
        resultado, _ = _motor()(caminho)
    except Exception:
        return None
    return extrair_coordenada([linha[1] for linha in (resultado or [])])


def reparar_digito(coord, centro):
    """
    Repoe um digito que o OCR perdeu na coordenada.

    O erro mais comum e a dezena sumir da latitude: o OCR le -3.7015 onde esta
    escrito -23.7015, e a longitude vem certa. Quando somar 10, 20 ou 30 ao
    valor absoluto traz o ponto para perto do centro do PDF, era isso mesmo.
    """
    if centro is None:
        return coord, False
    melhor, menor = coord, distancia_metros(centro, coord)
    for eixo in (0, 1):
        for dez in (10, 20, 30, 40, 50, 60, 70):
            tentativa = list(coord)
            tentativa[eixo] = -(abs(coord[eixo]) + dez)
            limite = 5.5 if eixo == 0 else 34
            if abs(tentativa[eixo]) > (34 if eixo == 0 else 74):
                continue
            d = distancia_metros(centro, tuple(tentativa))
            if d < menor:
                melhor, menor = tuple(tentativa), d
    reparado = melhor != coord and menor < 1000
    return (melhor, True) if reparado else (coord, False)


def distancia_metros(a, b):
    """Distância entre dois pontos (lat, lon), em metros — fórmula de haversine."""
    R = 6371000.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(h)))


# --------------------------------------------------------------------------- #
# Cache das coordenadas, por SHA da imagem
# --------------------------------------------------------------------------- #

def caminho_cache(fase):
    return os.path.join(BASE_DIR, "cache_extracao",
                        f"fase{str(fase).replace('.', '_')}_todos", "coordenadas.jsonl")


def ler_cache(fase):
    caminho = caminho_cache(fase)
    cache = {}
    if not os.path.exists(caminho):
        return cache
    with io.open(caminho, encoding="utf-8") as fh:
        for linha in fh:
            linha = linha.strip()
            if not linha:
                continue
            try:
                r = json.loads(linha)
                cache[r["s"]] = (r["lat"], r["lon"]) if r.get("lat") is not None else None
            except Exception:
                continue
    return cache


def _gravar(caminho, sha, coord):
    with io.open(caminho, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"s": sha,
                             "lat": coord[0] if coord else None,
                             "lon": coord[1] if coord else None}) + "\n")


def processar(registros, fase, workers=6, progresso=None):
    """
    Lê a coordenada de cada imagem ainda não processada.

    O cache é por SHA: a mesma foto aparece em vários RDOs e só passa pelo OCR
    uma vez. Cada leitura é gravada na hora, então o trabalho é retomável.
    """
    cache = ler_cache(fase)
    caminho = caminho_cache(fase)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)

    pendentes = []
    vistos = set()
    for r in registros:
        sha = r.get("sha256")
        if sha and sha not in cache and sha not in vistos:
            vistos.add(sha)
            pendentes.append(r)

    if not pendentes:
        return cache, 0

    trava = threading.Lock()
    feitos = [0]

    def um(r):
        img = os.path.join(DIR_THUMBS, r["thumb_url"])
        coord = ler_coordenada(img) if os.path.exists(img) else None
        with trava:
            cache[r["sha256"]] = coord
            _gravar(caminho, r["sha256"], coord)
            feitos[0] += 1
            if progresso and feitos[0] % 25 == 0:
                progresso(feitos[0], len(pendentes))
        return coord

    _motor()   # carrega o modelo antes de abrir as threads
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(um, pendentes))

    if progresso:
        progresso(feitos[0], len(pendentes))
    return cache, len(pendentes)


# --------------------------------------------------------------------------- #
# Análise por PDF
# --------------------------------------------------------------------------- #

def analisar_pdf(imagens, cache, raio=RAIO_METROS):
    """
    Confere as fotos de UM PDF contra o raio em torno da escola.

    Em tres passos: tira uma mediana provisoria, repara as coordenadas em que o
    OCR perdeu um digito, e refaz a mediana com os valores corrigidos. Sem o
    reparo, uma unica leitura errada entra como foto a 2.000 km da escola.

    :return: dict com centro, contagens e a lista de fotos.
    """
    lidas = [(r, cache[r["sha256"]]) for r in imagens
             if cache.get(r.get("sha256"))]
    total = len(imagens)
    if not lidas:
        return {"centro": None, "com_coordenada": 0, "total": total,
                "fora_do_raio": 0, "suspeitas": 0, "fotos": [],
                "sem_referencia": True}

    def mediana(pontos):
        return (statistics.median(p[0] for p in pontos),
                statistics.median(p[1] for p in pontos))

    centro = mediana([c for _, c in lidas])
    corrigidas, reparos = [], 0
    for r, c in lidas:
        novo_c, reparado = reparar_digito(c, centro)
        reparos += 1 if reparado else 0
        corrigidas.append((r, novo_c, reparado))

    centro = mediana([c for _, c, _ in corrigidas])

    fotos, fora, suspeitas = [], 0, 0
    for r, c, reparado in corrigidas:
        d = distancia_metros(centro, c)
        if d > LIMITE_SUSPEITA_M:
            situacao, suspeitas = "suspeita", suspeitas + 1
        elif d > raio:
            situacao, fora = "fora", fora + 1
        else:
            situacao = "dentro"
        fotos.append({
            "sha256": r["sha256"], "thumb_url": r.get("thumb_url"),
            "pagina": r.get("pagina"), "lat": c[0], "lon": c[1],
            "distancia_m": round(d, 1), "situacao": situacao,
            "fora_do_raio": situacao == "fora", "reparado": reparado,
        })

    confiaveis = [f for f in fotos if f["situacao"] != "suspeita"]
    fachada = fotos[0] if fotos else None

    return {
        "centro": {"lat": round(centro[0], 6), "lon": round(centro[1], 6)},
        "raio_m": raio,
        "total": total,
        "com_coordenada": len(fotos),
        "sem_coordenada": total - len(fotos),
        "fora_do_raio": fora,
        "suspeitas": suspeitas,
        "reparadas": reparos,
        "distancia_max_m": round(max((f["distancia_m"] for f in confiaveis),
                                     default=0), 1),
        "fachada_pagina": fachada["pagina"] if fachada else None,
        "fachada_distancia_m": fachada["distancia_m"] if fachada else None,
        "fotos": fotos,
        "sem_referencia": False,
    }


def analisar_execucao(registros, fase, raio=RAIO_METROS):
    """Roda analisar_pdf para cada PDF, devolvendo {pdf_url: analise}."""
    cache = ler_cache(fase)
    por_pdf = defaultdict(list)
    for r in registros:
        por_pdf[r.get("pdf_url")].append(r)

    saida = {}
    for url, imagens in por_pdf.items():
        imagens = sorted(imagens, key=lambda x: (x.get("pagina") or 0))
        analise = analisar_pdf(imagens, cache, raio)
        if analise["com_coordenada"]:
            analise.pop("fotos", None)      # o resumo basta para o painel
            saida[url] = analise
    return saida


def salvar_analise(fase, analise):
    caminho = os.path.join(BASE_DIR, "cache_extracao",
                           f"fase{str(fase).replace('.', '_')}_todos", "geo_por_pdf.json")
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with io.open(caminho, "w", encoding="utf-8") as fh:
        json.dump(analise, fh, ensure_ascii=False)
    return caminho


def ler_analise(fase):
    caminho = os.path.join(BASE_DIR, "cache_extracao",
                           f"fase{str(fase).replace('.', '_')}_todos", "geo_por_pdf.json")
    if not os.path.exists(caminho):
        return {}
    try:
        with io.open(caminho, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


# --------------------------------------------------------------------------- #

def _e_foto(r):
    """Só fotos de câmera têm carimbo; screenshots de painel não."""
    w, h = r.get("width") or 0, r.get("height") or 0
    if not w or not h:
        return False
    razao = w / h
    return abs(razao - 4 / 3) < 0.02 or abs(razao - 3 / 4) < 0.02


def main():
    import analisar_fase as motor

    ap = argparse.ArgumentParser()
    ap.add_argument("--fase", default="5")
    ap.add_argument("--amostra", type=int, default=0,
                    help="Processa apenas N PDFs (demonstração)")
    ap.add_argument("--dos-pares", default="",
                    help="Processa so os PDFs dos pares duplicados desta execucao")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--raio", type=float, default=RAIO_METROS)
    args = ap.parse_args()

    cache_imgs = os.path.join(BASE_DIR, "cache_extracao",
                              f"fase{args.fase.replace('.', '_')}_todos",
                              "cache_imagens.jsonl")
    print("=" * 70)
    print(f">>> CONFERENCIA GEOGRAFICA — FASE {args.fase} (raio {args.raio:.0f} m)")
    print("=" * 70)

    registros = motor.ler_cache_imagens(cache_imgs)
    fotos = [r for r in registros
             if _e_foto(r) and re.match(r"^\d{8}", r.get("pdf_filename") or "")]
    print(f"\n  imagens no cache : {len(registros):,}".replace(",", "."))
    print(f"  fotos de camera  : {len(fotos):,}".replace(",", "."))

    if args.dos_pares:
        # Demonstracao util: so os PDFs que aparecem no painel de duplicatas.
        import pares_duplicatas
        _meta, pares = pares_duplicatas.montar_pares(args.dos_pares)
        urls = {x["pdf_url"] for p in pares
                for lado in ("pdfs_a", "pdfs_b") for x in p[lado]}
        fotos = [r for r in fotos if r["pdf_url"] in urls]
        print(f"  [PARES] {len(urls)} PDFs do painel, {len(fotos):,} fotos"
              .replace(",", "."))

    if args.amostra:
        por_pdf = defaultdict(list)
        for r in fotos:
            por_pdf[r["pdf_url"]].append(r)
        # PDFs com mais fotos primeiro: demonstram melhor
        escolhidos = sorted(por_pdf.items(), key=lambda kv: -len(kv[1]))[:args.amostra]
        fotos = [r for _, v in escolhidos for r in v]
        print(f"  [AMOSTRA] {args.amostra} PDFs, {len(fotos):,} fotos".replace(",", "."))

    t0 = time.time()

    def prog(feitos, total):
        el = time.time() - t0
        resta = (total - feitos) / max(feitos / el, 0.01) / 60
        print(f"    OCR {feitos}/{total} ({el/60:.1f} min, restam ~{resta:.0f} min)",
              flush=True)

    print(f"\n>>> Lendo coordenadas por OCR ({args.workers} threads)...")
    cache, novos = processar(fotos, args.fase, args.workers, prog)
    print(f"    {novos} imagens processadas em {(time.time()-t0)/60:.1f} min")

    lidas = sum(1 for r in fotos if cache.get(r["sha256"]))
    print(f"    coordenada lida em {lidas}/{len(fotos)} fotos "
          f"({lidas/max(len(fotos),1)*100:.0f}%)")

    print(f"\n>>> Conferindo o raio por PDF...")
    analise = analisar_execucao(fotos, args.fase, args.raio)
    caminho = salvar_analise(args.fase, analise)

    com_fora = [a for a in analise.values() if a["fora_do_raio"]]
    print(f"    PDFs com coordenada : {len(analise)}")
    print(f"    PDFs com foto fora  : {len(com_fora)}")
    if com_fora:
        tops = sorted(com_fora, key=lambda a: -a["distancia_max_m"])[:8]
        print(f"\n    maiores distancias:")
        for a in tops:
            print(f"      {a['fora_do_raio']}/{a['com_coordenada']} fotos fora | "
                  f"max {a['distancia_max_m']:.0f} m")
    print(f"\n    salvo em {os.path.basename(caminho)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
