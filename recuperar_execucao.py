"""
Reconstroi uma execucao perdida a partir do que ela gravou no Supabase.

Quando serve:
    A pasta historico/<run_id> era o unico lugar onde a execucao existia. Se o
    volume nao estava montado, um redeploy a apagou. Mas a varredura grava os
    achados em rdo_analisados e duplicatas_rdo enquanto roda — e essas tabelas
    tem criado_em. Da para separar as linhas daquela execucao pela data e
    remontar os grupos.

O que volta e o que nao volta:
    volta     os grupos de imagens repetidas, com INEP, fornecedor, UF, PDF,
              pagina e miniatura de cada ocorrencia — o suficiente para o painel
              abrir a execucao e montar os pares.
    nao volta as metricas originais (quantos PDFs foram baixados, quanto tempo
              levou) e as imagens que NAO tinham duplicata. A execucao
              reconstruida e marcada com `reconstruida: true` no meta, para
              ninguem confundir com uma varredura completa.

Uso:
    python recuperar_execucao.py --dia 2026-10-27 --run-id fase5_todos_20261027_144554 --simular
    python recuperar_execucao.py --dia 2026-10-27 --run-id fase5_todos_20261027_144554
"""

import argparse
import json
import sys
import time
import urllib.request
from collections import defaultdict

import analisar_fase as motor
import execucoes_db
from config import SUPABASE_URL, SERVICE_KEY

_H = {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"}


def _get(caminho, timeout=180):
    req = urllib.request.Request(SUPABASE_URL + caminho, headers=_H)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _paginar(tabela, campos, filtro, ordem="id", limite=1000):
    linhas, off = [], 0
    while True:
        lote = _get(f"/{tabela}?select={campos}&{filtro}"
                    f"&order={ordem}&limit={limite}&offset={off}")
        if not lote:
            break
        linhas += lote
        if len(lote) < limite:
            break
        off += limite
        print(f"      ...{len(linhas)}", end="\r", flush=True)
    return linhas


def _faixa(dia):
    """Filtro de um dia inteiro em criado_em."""
    seguinte = time.strftime(
        "%Y-%m-%d", time.localtime(time.mktime(time.strptime(dia, "%Y-%m-%d")) + 86400))
    return f"criado_em=gte.{dia}&criado_em=lt.{seguinte}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dia", required=True, help="Dia da execucao, AAAA-MM-DD")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--fase", default="5")
    ap.add_argument("--simular", action="store_true")
    args = ap.parse_args()

    print("=" * 70)
    print(f">>> RECUPERAR {args.run_id}" + ("  [SIMULACAO]" if args.simular else ""))
    print("=" * 70)

    filtro = _faixa(args.dia)

    print(f"\n>>> [1/4] Duplicatas gravadas em {args.dia}...")
    dups = _paginar("duplicatas_rdo", "*", filtro)
    print(f"    {len(dups)} pares")
    if not dups:
        print("    Nada a recuperar: nenhuma duplicata com essa data.")
        return 1

    # Cada linha de duplicatas_rdo e um par (A, B) de uma mesma imagem. Juntar
    # por sha256 devolve o grupo: todas as escolas que tem aquela imagem.
    print("\n>>> [2/4] Agrupando por imagem...")
    por_sha = defaultdict(dict)
    for d in dups:
        for lado in ("a", "b"):
            inep = d.get(f"inep_{lado}")
            if not inep:
                continue
            por_sha[d["sha256"]][(inep, d.get(f"pagina_{lado}"))] = {
                "inep": inep,
                "uf": d.get(f"uf_{lado}"),
                "fornecedor": d.get(f"fornecedor_{lado}"),
                "pdf_filename": d.get(f"pdf_filename_{lado}"),
                "pagina": d.get(f"pagina_{lado}"),
                "thumb_url": d.get(f"thumb_url_{lado}"),
                "sha256": d["sha256"],
                "phash": None,
                "width": None,
                "height": None,
                "pdf_url": None,
            }
    print(f"    {len(por_sha)} imagens distintas")

    # O pdf_url nao esta em duplicatas_rdo, e e ele que o botao "abrir PDF" usa.
    #
    # Busca por sha256 em lotes, e nao por data: rdo_analisados tem quase 2
    # milhoes de linhas e nenhum indice em criado_em, entao filtrar por dia faz
    # varredura completa e estoura o tempo do PostgREST. Por sha256 tambem nao
    # ha indice, mas o lote de 40 devolve em ~6 s — minuto e pouco no total.
    print("\n>>> [3/4] Buscando o endereco dos PDFs em rdo_analisados...")
    shas = sorted(por_sha)
    analisados = []
    for i in range(0, len(shas), 40):
        lote = shas[i:i + 40]
        analisados += _get(
            "/rdo_analisados?select=sha256,inep,pagina,pdf_url"
            "&sha256=in.(" + ",".join(lote) + ")&limit=5000", timeout=300)
        print(f"      {min(i + 40, len(shas))}/{len(shas)} imagens", end="\r", flush=True)
    print(f"    {len(analisados)} registros encontrados          ")

    urls = {}
    for a in analisados:
        urls[(a["sha256"], a["inep"], a.get("pagina"))] = a.get("pdf_url")
        urls.setdefault((a["sha256"], a["inep"], None), a.get("pdf_url"))

    achou = 0
    for sha, membros in por_sha.items():
        for chave, m in membros.items():
            url = urls.get((sha, m["inep"], m["pagina"])) or urls.get((sha, m["inep"], None))
            if url:
                m["pdf_url"] = url
                achou += 1
    print(f"    endereco encontrado para {achou} de "
          f"{sum(len(v) for v in por_sha.values())} ocorrencias")

    # Monta os grupos no mesmo formato de find_duplicate_groups.
    grupos = []
    for sha, membros in por_sha.items():
        lista = sorted(membros.values(),
                       key=lambda m: (m["inep"] or 0, m["pdf_filename"] or "",
                                      m["pagina"] or 0))
        ineps = sorted({m["inep"] for m in lista if m["inep"]})
        if len(ineps) < 2:
            continue
        fornecedores = sorted({m["fornecedor"] for m in lista if m["fornecedor"]})
        comuns = set.intersection(*[set(motor.nomes_fornecedor(m["fornecedor"]))
                                    for m in lista if m["fornecedor"]]) \
            if all(m["fornecedor"] for m in lista) else set()
        grupos.append({
            "grupo_id": "",
            "tipo": "Exata (100%)",
            "fornecedor": " / ".join(sorted(comuns)) if comuns else (
                fornecedores[0] if fornecedores else ""),
            "ineps": ineps,
            "qtd_ineps": len(ineps),
            "qtd_ocorrencias": len(lista),
            "qtd_shas": 1,
            "fornecedores": fornecedores,
            "ufs": sorted({m["uf"] for m in lista if m["uf"]}),
            "membros": lista,
        })

    grupos.sort(key=lambda g: (g["qtd_ineps"], g["qtd_ocorrencias"]), reverse=True)
    for i, g in enumerate(grupos, 1):
        g["grupo_id"] = f"G{i:05d}"

    escolas = {i for g in grupos for i in g["ineps"]}
    print(f"\n>>> [4/4] {len(grupos)} grupos | {len(escolas)} escolas")
    print("    maiores:")
    for g in grupos[:5]:
        print(f"      {g['grupo_id']} {g['qtd_ineps']} escolas | "
              f"{g['fornecedor'][:40]}")

    meta = {
        "run_id": args.run_id,
        "fase": args.fase,
        "fornecedor": "",
        "status": "concluido",
        "iniciado_em": f"{args.dia} 00:00:00",
        "concluido_em": f"{args.dia} 23:59:59",
        # Marca explicita: nao e uma varredura completa, e uma reconstrucao a
        # partir do que foi gravado no banco. As metricas de download e as
        # imagens sem duplicata nao existem aqui.
        "reconstruida": True,
        "reconstruida_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        "reconstruida_de": "duplicatas_rdo + rdo_analisados",
        "metricas": {
            "grupos_duplicatas": len(grupos),
            "escolas_afetadas": len(escolas),
            "imagens_duplicadas": sum(g["qtd_ocorrencias"] for g in grupos),
            "pares": len(dups),
            "fornecedores_afetados": len({f for g in grupos for f in g["fornecedores"]}),
        },
    }

    if args.simular:
        print("\n[SIMULACAO] Nada foi gravado.")
        return 0

    print("\n>>> Gravando em disco e no Supabase...")
    motor.salvar_run(args.run_id, meta, grupos)
    if execucoes_db.disponivel(forcar=True):
        execucoes_db.salvar(meta, grupos)
        print("    banco: OK")
    else:
        print(f"    banco: {execucoes_db.motivo_indisponivel()}")
    print(f"    disco: {motor.caminho_run(args.run_id)}")

    print("\n" + "=" * 70)
    print(">>> RECUPERADA. Abra no painel para conferir.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
