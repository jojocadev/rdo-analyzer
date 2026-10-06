"""
Atualiza a tabela `escolas_conectadas` do Supabase a partir de um CSV novo da base.

O que faz:
    - INEP que ja existe no banco  -> UPDATE (reaproveita o escola_id_bubble dele)
    - INEP que ainda nao existe    -> INSERT (chave nova `inep_<INEP>`)
    - INEP no banco e fora do CSV  -> fica intacto (nao ha DELETE em nenhum caso)

Por que nao da para usar o import_schools_to_supabase.py:
    aquele script deriva a chave de conflito da POSICAO da linha no CSV
    (`bubble_row_{idx+1}`), o que embaralha escolas quando a ordem do arquivo muda.
    Aqui a chave e sempre ancorada no INEP.

Uso:
    python atualizar_base.py --simular   # nao grava, so mostra o diff
    python atualizar_base.py             # aplica
"""

import argparse
import json
import os
import sys
import time
import urllib.request

import dateutil.parser
import pandas as pd

from config import SUPABASE_URL, SERVICE_KEY

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PADRAO = os.path.join(
    BASE_DIR, "Banco de Escolas", "Novo Banco de escolas", "Baseescola06-10-26.csv")
LOTE = 500


def _req(url, data=None, method="GET", extra=None):
    headers = {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if extra:
        headers.update(extra)
    return urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        headers=headers, method=method)


def buscar_tudo():
    """Tabela inteira, com todos os campos - serve de backup antes de gravar."""
    linhas, off = [], 0
    while True:
        url = (f"{SUPABASE_URL}/escolas_conectadas?select=*"
               f"&order=escola_id_bubble&limit=1000&offset={off}")
        with urllib.request.urlopen(_req(url), timeout=120) as r:
            lote = json.loads(r.read().decode())
        if not lote:
            break
        linhas += lote
        if len(lote) < 1000:
            break
        off += 1000
    return linhas


def coluna(df, pedaco):
    """Acha a coluna pelo pedaco do nome (o CSV vem com acento)."""
    for c in df.columns:
        if pedaco.lower() in c.lower():
            return c
    raise KeyError(pedaco)


def parse_date(val):
    if val is None or pd.isna(val) or str(val).strip().lower() in ("", "nan"):
        return None
    try:
        return dateutil.parser.parse(str(val)).isoformat()
    except Exception:
        return None


def parse_books(books_raw):
    """Lista separada por virgula; `//cdn...` vira `https://cdn...`."""
    if books_raw is None or pd.isna(books_raw):
        return []
    urls = [u.strip() for u in str(books_raw).split(",") if u.strip()]
    return ["https:" + u if u.startswith("//") else u for u in urls]


def montar_registro(row, chave, cols):
    f_ri = "" if pd.isna(row.get("fornecedor_ri")) else str(row["fornecedor_ri"]).strip()
    f_re = "" if pd.isna(row.get("fornecedor_re")) else str(row["fornecedor_re"]).strip()

    if f_ri and f_re:
        tipo, nome = "RI / RE", f"{f_ri} (RI) / {f_re} (RE)"
    elif f_ri:
        tipo, nome = "RI", f_ri
    elif f_re:
        tipo, nome = "RE", f_re
    else:
        tipo, nome = None, None

    fase_val = None
    if not pd.isna(row.get("FASE")):
        fase_val = str(row["FASE"]).strip()
        if fase_val.endswith(".0"):
            fase_val = fase_val[:-2]

    status = row.get("Status Geral")
    status = "Conectada" if pd.isna(status) or not str(status).strip() else str(status).strip()

    return {
        "escola_id_bubble": chave,
        "inep": int(row["INEP"]) if not pd.isna(row.get("INEP")) else None,
        "uf": str(row["UF"]).strip().upper() if not pd.isna(row.get("UF")) else None,
        "fornecedor": nome,
        "tipo_fornecedor": tipo,
        "status_geral": status,
        "fase": fase_val,
        "books": parse_books(row.get("Books")),
        "atualizacao": parse_date(row.get("Atualizacao")),
        "ativacao_rede_externa": parse_date(row[cols["externa"]]),
        "ativacao_rede_interna": parse_date(row[cols["interna"]]),
    }


# Campos comparados para decidir se a linha realmente mudou.
_COMPARAR = ("inep", "uf", "fornecedor", "tipo_fornecedor", "status_geral",
             "fase", "books", "atualizacao", "ativacao_rede_externa",
             "ativacao_rede_interna")


def diferencas(novo, atual):
    """Nomes dos campos que mudam ao gravar `novo` por cima de `atual`."""
    mudou = []
    for campo in _COMPARAR:
        a, b = atual.get(campo), novo.get(campo)
        if campo == "books":
            a, b = list(a or []), list(b or [])
        elif campo == "atualizacao" or campo.startswith("ativacao"):
            # o banco devolve com timezone; compara so a parte estavel
            a = (a or "")[:19] or None
            b = (b or "")[:19] or None
        if a != b:
            mudou.append(campo)
    return mudou


def enviar(registros):
    url = f"{SUPABASE_URL}/escolas_conectadas?on_conflict=escola_id_bubble"
    gravados = 0
    for i in range(0, len(registros), LOTE):
        lote = registros[i:i + LOTE]
        req = _req(url, data=lote, method="POST",
                   extra={"Prefer": "resolution=merge-duplicates"})
        try:
            with urllib.request.urlopen(req, timeout=180):
                gravados += len(lote)
            print(f"    [{gravados}/{len(registros)}] enviados...", flush=True)
        except Exception as e:
            corpo = e.read().decode()[:300] if hasattr(e, "read") else ""
            print(f"    ERRO no lote {i}-{i + len(lote)}: {e} {corpo}", flush=True)
    return gravados


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=CSV_PADRAO)
    ap.add_argument("--simular", action="store_true",
                    help="Nao grava nada; so relata o diff")
    ap.add_argument("--tudo", dest="so_mudancas", action="store_false", default=True,
                    help="Envia todas as linhas do CSV, nao so as que mudaram")
    args = ap.parse_args()

    print("=" * 70)
    print(">>> ATUALIZACAO DA BASE DE ESCOLAS" + ("  [SIMULACAO]" if args.simular else ""))
    print("=" * 70)

    # 1. Estado atual + backup
    print("\n>>> [1/4] Lendo a tabela atual do Supabase...")
    atuais = buscar_tudo()
    por_inep = {r["inep"]: r for r in atuais if r.get("inep")}
    print(f"    {len(atuais)} linhas no banco ({len(por_inep)} INEPs distintos)")

    bkp = os.path.join(BASE_DIR, f"backup_escolas_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(bkp, "w", encoding="utf-8") as fh:
        json.dump(atuais, fh, ensure_ascii=False)
    print(f"    backup salvo: {os.path.basename(bkp)}")

    # 2. CSV novo
    print("\n>>> [2/4] Lendo o CSV novo...")
    df = pd.read_csv(args.csv, low_memory=False, encoding="utf-8")
    cols = {"externa": coluna(df, "Rede externa"), "interna": coluna(df, "Rede interna")}
    print(f"    {len(df)} linhas em {os.path.basename(args.csv)}")

    # 3. Diff
    novos, mudados, iguais = [], [], 0
    campos_mudados, books_mudados, ineps_csv = {}, 0, set()
    for _, row in df.iterrows():
        inep = None if pd.isna(row.get("INEP")) else int(row["INEP"])
        ineps_csv.add(inep)
        atual = por_inep.get(inep)
        chave = atual["escola_id_bubble"] if atual else f"inep_{inep}"
        reg = montar_registro(row, chave, cols)

        if atual is None:
            novos.append(reg)
            continue
        delta = diferencas(reg, atual)
        if not delta:
            iguais += 1
            continue
        mudados.append(reg)
        if "books" in delta:
            books_mudados += 1
        for c in delta:
            campos_mudados[c] = campos_mudados.get(c, 0) + 1

    orfaos = set(por_inep) - ineps_csv

    print("\n>>> [3/4] Diff contra o banco")
    print(f"    escolas novas (INSERT)      : {len(novos)}")
    print(f"    escolas alteradas (UPDATE)  : {len(mudados)}")
    print(f"      destas, com books novos   : {books_mudados}")
    print(f"    escolas sem mudanca         : {iguais}")
    print(f"    no banco e fora do CSV      : {len(orfaos)} (mantidas como estao)")
    if campos_mudados:
        print("\n    campos que mudam:")
        for c, n in sorted(campos_mudados.items(), key=lambda kv: -kv[1]):
            print(f"      {c:24s} {n:>6}")

    por_fase = {}
    for r in novos:
        por_fase[r["fase"]] = por_fase.get(r["fase"], 0) + 1
    if por_fase:
        print("\n    escolas novas por fase:")
        for f, n in sorted(por_fase.items(), key=lambda kv: str(kv[0])):
            print(f"      fase {str(f):8s} {n:>6}")

    if args.so_mudancas:
        enviar_lista = mudados + novos
    else:
        enviar_lista = []
        for _, row in df.iterrows():
            inep = None if pd.isna(row.get("INEP")) else int(row["INEP"])
            atual = por_inep.get(inep)
            chave = atual["escola_id_bubble"] if atual else f"inep_{inep}"
            enviar_lista.append(montar_registro(row, chave, cols))

    if args.simular:
        print(f"\n[SIMULACAO] Nada foi gravado. Seriam enviadas {len(enviar_lista)} linhas.")
        if mudados:
            print("\nexemplo de linha alterada:")
            for k, v in mudados[0].items():
                print(f"    {k:24s} {str(v)[:66]}")
        return 0

    # 4. Envio
    print(f"\n>>> [4/4] Enviando {len(enviar_lista)} linhas em lotes de {LOTE}...")
    t0 = time.time()
    n = enviar(enviar_lista)
    print(f"    {n}/{len(enviar_lista)} registros enviados em {time.time() - t0:.0f}s")

    depois = buscar_tudo()
    print("\n" + "=" * 70)
    print(f">>> CONCLUIDO - tabela: {len(atuais)} -> {len(depois)} linhas")
    print(f"    backup do estado anterior: {os.path.basename(bkp)}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
