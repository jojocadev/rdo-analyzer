import os
import re
import json
import time
import urllib.parse
import urllib.request
import pandas as pd
import dateutil.parser

CSV_PATH = r"c:\Projetos AI\Sisop\RDO images\Banco de Escolas\Correta.csv"
from config import SUPABASE_URL, SERVICE_KEY  # noqa: E402
def parse_date(val):
    if not val or pd.isna(val) or str(val).strip().lower() == 'nan':
        return None
    try:
        dt = dateutil.parser.parse(str(val))
        return dt.isoformat()
    except Exception:
        return None

def parse_books(books_raw):
    if not books_raw or pd.isna(books_raw):
        return []
    urls_raw = [u.strip() for u in str(books_raw).split(",") if u.strip()]
    return ["https:" + u if u.startswith("//") else u for u in urls_raw]

def upload_batch(records_batch):
    data = json.dumps(records_batch).encode('utf-8')
    req = urllib.request.Request(
        SUPABASE_URL,
        data=data,
        method='POST',
        headers={
            'apikey': SERVICE_KEY,
            'Authorization': f'Bearer {SERVICE_KEY}',
            'Content-Type': 'application/json',
            'Prefer': 'resolution=merge-duplicates'
        }
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status

def main():
    print("=====================================================")
    print(">>> INICIANDO CARGA DO BANCO COMPLETO (CORRETA.CSV) ")
    print("=====================================================")
    
    start_time = time.time()
    df = pd.read_csv(CSV_PATH, low_memory=False)
    total_rows = len(df)
    print(f"Total de registros lidos do Correta.csv: {total_rows}")

    records = []
    for idx, row in df.iterrows():
        f_ri = str(row.get('fornecedor_ri', '')).strip() if not pd.isna(row.get('fornecedor_ri')) else ''
        f_re = str(row.get('fornecedor_re', '')).strip() if not pd.isna(row.get('fornecedor_re')) else ''
        
        # Mapeamento do nome do fornecedor e tipo
        if f_ri and f_re:
            tipo_forn = "RI / RE"
            fornecedor_nome = f"{f_ri} (RI) / {f_re} (RE)"
        elif f_ri:
            tipo_forn = "RI"
            fornecedor_nome = f_ri
        elif f_re:
            tipo_forn = "RE"
            fornecedor_nome = f_re
        else:
            tipo_forn = None
            fornecedor_nome = None

        inep_val = None
        if not pd.isna(row.get('INEP')):
            try:
                inep_val = int(row['INEP'])
            except ValueError:
                pass

        fase_val = None
        if not pd.isna(row.get('FASE')):
            fase_val = str(row['FASE']).strip()
            if fase_val.endswith('.0'):
                fase_val = fase_val[:-2]  # Converte '3.0' em '3'

        uf_val = str(row.get('UF', '')).strip().upper() if not pd.isna(row.get('UF')) else None
        urls = parse_books(row.get('Books'))

        record = {
            "escola_id_bubble": f"bubble_row_{idx+1}",
            "inep": inep_val,
            "uf": uf_val,
            "fornecedor": fornecedor_nome,
            "tipo_fornecedor": tipo_forn,
            "status_geral": "Conectada",
            "fase": fase_val,
            "books": urls,
            "atualizacao": parse_date(row.get('Atualizacao')),
            "ativacao_rede_externa": parse_date(row.get('Ativação Rede externa')),
            "ativacao_rede_interna": parse_date(row.get('Ativação Rede interna'))
        }
        records.append(record)

    print(f"Formatados {len(records)} registros completos com sucesso. Enviando em lotes de 500...")

    batch_size = 500
    uploaded_count = 0
    
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        try:
            upload_batch(batch)
            uploaded_count += len(batch)
            pct = round((uploaded_count / total_rows) * 100, 1)
            elapsed = round(time.time() - start_time, 1)
            print(f"[{uploaded_count}/{total_rows}] ({pct}%) salvos em {elapsed}s...")
        except Exception as e:
            print(f"Erro no lote {i}-{i+batch_size}: {e}")
            time.sleep(1)

    total_elapsed = round(time.time() - start_time, 1)
    print("=====================================================")
    print(f"[OK] CARGA CONCLUIDA! {uploaded_count} escolas completas salvas no Supabase em {total_elapsed}s.")
    print("=====================================================")

if __name__ == "__main__":
    main()
