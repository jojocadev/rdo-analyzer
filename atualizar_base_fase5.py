"""
Atualiza no Supabase apenas as escolas da FASE 5, a partir de um CSV novo.

Por que não dá para reusar o import_schools_to_supabase.py:
    Aquele script deriva a chave de conflito da POSIÇÃO da linha no CSV
    (`escola_id_bubble = bubble_row_{idx+1}`). Rodá-lo com um CSV só de fase 5
    faria o upsert mirar em bubble_row_1..6953, que na base atual pertencem às
    fases 3, 4.1, 2, 4.2 e 4.3 — sobrescreveria 6.287 escolas de outras fases.

Como este script faz:
    1. Salva um backup local de todas as linhas de fase 5 antes de tocar no banco.
    2. Descobre a chave `escola_id_bubble` que cada INEP de fase 5 já tem no banco.
    3. Monta os registros com o MESMO mapeamento de campos do script original
       (fornecedor RI/RE, datas, books) e faz upsert por `escola_id_bubble`:
       quem já existe é atualizado, quem é novo entra com a chave `fase5_<INEP>`.

Não há DELETE: as outras fases e as escolas de fase 5 ausentes do CSV ficam intactas.

Uso:
    python atualizar_base_fase5.py --simular    # não grava, só mostra o que faria
    python atualizar_base_fase5.py              # aplica
"""

import argparse
import json
import os
import sys
import time
import urllib.request

import dateutil.parser
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PADRAO = os.path.join(
    BASE_DIR, "Banco de Escolas", "Novo Banco de escolas",
    "export_Base-RDO_2026-09-01_14-17-12.csv")

from config import SUPABASE_URL, SERVICE_KEY  # noqa: E402
FASE = "5"
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


def buscar_fase5():
    """Todas as linhas de fase 5 do banco, com todos os campos (serve de backup)."""
    linhas, off = [], 0
    while True:
        url = f"{SUPABASE_URL}/escolas_conectadas?select=*&fase=eq.{FASE}&limit=1000&offset={off}"
        with urllib.request.urlopen(_req(url), timeout=120) as r:
            lote = json.loads(r.read().decode())
        if not lote:
            break
        linhas += lote
        if len(lote) < 1000:
            break
        off += 1000
    return linhas


def parse_date(val):
    if val is None or pd.isna(val) or str(val).strip().lower() in ("", "nan"):
        return None
    try:
        return dateutil.parser.parse(str(val)).isoformat()
    except Exception:
        return None


def parse_books(books_raw):
    """Mesma lógica do script original: lista separada por vírgula, `//` -> `https://`."""
    if books_raw is None or pd.isna(books_raw):
        return []
    urls = [u.strip() for u in str(books_raw).split(",") if u.strip()]
    return ["https:" + u if u.startswith("//") else u for u in urls]


def montar_registro(row, chave):
    """Aplica o mesmo mapeamento de campos usado na carga original."""
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

    # O CSV novo traz "Status Geral"; a carga original gravava "Conectada" fixo.
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
        "ativacao_rede_externa": parse_date(row.get("Ativação Rede externa")),
        "ativacao_rede_interna": parse_date(row.get("Ativação Rede interna")),
    }


def enviar(registros):
    """Upsert por escola_id_bubble: atualiza quem existe, insere quem não existe."""
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
                    help="Não grava nada; só relata o que seria feito")
    args = ap.parse_args()

    print("=" * 66)
    print(f">>> ATUALIZACAO DA BASE — FASE {FASE}" + ("  [SIMULACAO]" if args.simular else ""))
    print("=" * 66)

    # 1. Estado atual + backup
    print("\n>>> [1/4] Lendo a fase 5 atual do Supabase...")
    atuais = buscar_fase5()
    por_inep = {r["inep"]: r for r in atuais if r.get("inep")}
    print(f"    {len(atuais)} linhas de fase 5 no banco ({len(por_inep)} INEPs distintos)")

    bkp = os.path.join(BASE_DIR, f"backup_fase5_supabase_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(bkp, "w", encoding="utf-8") as fh:
        json.dump(atuais, fh, ensure_ascii=False)
    print(f"    backup salvo: {os.path.basename(bkp)}")

    # 2. CSV novo
    print("\n>>> [2/4] Lendo o CSV novo...")
    df = pd.read_csv(args.csv, low_memory=False)
    print(f"    {len(df)} linhas em {os.path.basename(args.csv)}")

    fases_csv = set(str(x).strip().rstrip(".0") or "0" for x in df["FASE"].dropna())
    if fases_csv - {FASE}:
        print(f"    [ABORTADO] o CSV tem fases fora da {FASE}: {sorted(fases_csv)}")
        return 1

    # 3. Registros, reaproveitando a chave que o INEP já tem no banco
    registros, atualizar, inserir = [], 0, 0
    for _, row in df.iterrows():
        inep = None if pd.isna(row.get("INEP")) else int(row["INEP"])
        existente = por_inep.get(inep)
        if existente:
            chave = existente["escola_id_bubble"]
            atualizar += 1
        else:
            chave = f"fase{FASE}_{inep}"
            inserir += 1
        registros.append(montar_registro(row, chave))

    orfaos = sorted(set(por_inep) - {r["inep"] for r in registros})
    print(f"\n>>> [3/4] Registros montados: {len(registros)}")
    print(f"    atualizar (INEP ja no banco) : {atualizar}")
    print(f"    inserir (INEP novo)          : {inserir}")
    print(f"    fase 5 no banco fora do CSV  : {len(orfaos)} (mantidos como estao)")
    if orfaos:
        print(f"      {orfaos[:12]}")

    if args.simular:
        print("\n[SIMULACAO] Nada foi gravado.")
        ex = registros[0]
        print("\nexemplo de registro que seria enviado:")
        for k, v in ex.items():
            print(f"    {k:24s} {str(v)[:66]}")
        return 0

    # 4. Envio
    print(f"\n>>> [4/4] Enviando em lotes de {LOTE} (upsert por escola_id_bubble)...")
    t0 = time.time()
    n = enviar(registros)
    print(f"\n    {n}/{len(registros)} registros enviados em {time.time() - t0:.0f}s")

    depois = buscar_fase5()
    print("\n" + "=" * 66)
    print(f">>> CONCLUIDO — fase {FASE}: {len(atuais)} -> {len(depois)} linhas")
    print(f"    backup do estado anterior: {os.path.basename(bkp)}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
