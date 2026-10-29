"""
Re-extrai dos PDFs as miniaturas que sumiram do disco.

Quando serve:
    extracted_images nao vai para o git e e grande demais para o banco. Se o
    volume nao estiver montado, um deploy leva as miniaturas junto — e o painel
    passa a mostrar a analise com quadradinhos de imagem quebrada.

    Mas cada imagem e identificada por sha256, e o registro da execucao guarda o
    endereco do PDF e a pagina. Da para baixar o PDF de novo, achar a imagem com
    aquele sha256 e regravar o arquivo com o nome exato que o painel procura.

So as imagens que aparecem em algum grupo sao refeitas: sao as evidencias, umas
centenas por execucao. As demais sao subproduto da extracao.

Uso:
    python recuperar_miniaturas.py --run fase5_todos_20261027_144554 --simular
    python recuperar_miniaturas.py --run fase5_todos_20261027_144554
"""

import argparse
import hashlib
import io
import os
import sys
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import fitz
from PIL import Image

import analisar_fase as motor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIR_THUMBS = os.path.join(BASE_DIR, "extracted_images")
LADO_MAX = 1280


def faltantes(run_id):
    """{(sha256, pdf_url, pagina): nome_do_arquivo} do que nao esta no disco."""
    meta = motor.carregar_run(run_id)
    if not meta:
        return None, {}

    alvo = {}
    for g in meta.get("grupos") or []:
        for m in g.get("membros") or []:
            nome = m.get("thumb_url")
            if not nome or os.path.exists(os.path.join(DIR_THUMBS, nome)):
                continue
            if not m.get("pdf_url"):
                continue
            alvo[nome] = (m["sha256"], m["pdf_url"], m.get("pagina"))
    return meta, alvo


def _baixar(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def processar_pdf(item):
    """
    Abre um PDF e grava as miniaturas pedidas dele.

    Procura pelo sha256 do byte original da imagem — e assim que o sistema a
    identifica — e nao pela pagina, porque a mesma imagem pode aparecer em
    varias paginas e o xref do nome vem da primeira.
    """
    pdf_url, pedidos = item
    gravadas, erro = [], ""
    try:
        bruto = _baixar(pdf_url)
        doc = fitz.open(stream=bruto, filetype="pdf")
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"

    try:
        quero = {sha: nome for nome, (sha, _u, _p) in pedidos.items()}
        vistos = set()
        for pagina in range(len(doc)):
            for info in doc[pagina].get_images(full=True):
                xref = info[0]
                if xref in vistos:
                    continue
                vistos.add(xref)
                try:
                    dados = doc.extract_image(xref)["image"]
                except Exception:
                    continue
                sha = hashlib.sha256(dados).hexdigest()
                nome = quero.get(sha)
                if not nome:
                    continue
                try:
                    pil = Image.open(io.BytesIO(dados)).convert("RGB")
                    if max(pil.size) > LADO_MAX:
                        fator = LADO_MAX / max(pil.size)
                        pil = pil.resize((int(pil.width * fator), int(pil.height * fator)))
                    pil.save(os.path.join(DIR_THUMBS, nome), "JPEG", quality=85)
                    gravadas.append(nome)
                except Exception:
                    continue
    finally:
        doc.close()
    return gravadas, erro


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--simular", action="store_true")
    args = ap.parse_args()

    print("=" * 70)
    print(f">>> RECUPERAR MINIATURAS — {args.run}"
          + ("  [SIMULACAO]" if args.simular else ""))
    print("=" * 70)

    meta, alvo = faltantes(args.run)
    if meta is None:
        print("\nExecucao nao encontrada.")
        return 1
    if not alvo:
        print("\nNenhuma miniatura faltando. Nada a fazer.")
        return 0

    por_pdf = defaultdict(dict)
    for nome, (sha, url, pag) in alvo.items():
        por_pdf[url][nome] = (sha, url, pag)

    print(f"\n    miniaturas faltando : {len(alvo)}")
    print(f"    PDFs a baixar       : {len(por_pdf)}")

    if args.simular:
        print("\n[SIMULACAO] Nada foi baixado.")
        return 0

    os.makedirs(DIR_THUMBS, exist_ok=True)
    t0 = time.time()
    total, erros = 0, 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, (gravadas, erro) in enumerate(
                pool.map(processar_pdf, por_pdf.items()), 1):
            total += len(gravadas)
            if erro:
                erros += 1
            print(f"    {i}/{len(por_pdf)} PDFs | {total} miniaturas | "
                  f"{time.time()-t0:.0f}s", end="\r", flush=True)

    print(f"\n\n    {total} de {len(alvo)} miniaturas recuperadas em "
          f"{time.time()-t0:.0f}s" + (f" | {erros} PDFs falharam" if erros else ""))

    ainda = [n for n in alvo if not os.path.exists(os.path.join(DIR_THUMBS, n))]
    if ainda:
        print(f"    {len(ainda)} continuam ausentes (imagem saiu do PDF de origem)")

    # A classificacao foi feita sem essas imagens: refazer agora que elas existem.
    caminho_classes = os.path.join(motor.caminho_run(args.run), "classes_imagens.json")
    if os.path.exists(caminho_classes):
        os.remove(caminho_classes)
        print("    cache de classificacao limpo — sera refeito no proximo acesso")

    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
