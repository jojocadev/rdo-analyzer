import urllib.request
import json
import time

from config import SUPABASE_URL, SERVICE_KEY  # noqa: E402
def fetch_all_analisados():
    """Busca todos os registros salvos na tabela rdo_analisados com paginação."""
    all_records = []
    limit = 1000
    offset = 0
    
    print(">>> Buscando registros da tabela rdo_analisados no Supabase...")
    while True:
        url = f"{SUPABASE_URL}/rdo_analisados?select=*&limit={limit}&offset={offset}"
        req = urllib.request.Request(
            url,
            headers={
                "apikey": SERVICE_KEY,
                "Authorization": f"Bearer {SERVICE_KEY}"
            }
        )
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode())
                if not data:
                    break
                all_records.extend(data)
                print(f"    Carregados {len(all_records)} registros de rdo_analisados...")
                if len(data) < limit:
                    break
                offset += limit
        except Exception as e:
            print(f"Erro ao buscar rdo_analisados: {e}")
            break
            
    return all_records

def generate_and_save_duplicates(records):
    """Agrupa por SHA-256 e gera pares de duplicatas exatas entre INEPs diferentes."""
    if not records:
        print("Nenhum registro encontrado em rdo_analisados para processar.")
        return

    print(f"\n>>> Analisando {len(records)} registros para encontrar duplicatas exatas...")
    
    sha_map = {}
    for r in records:
        sha = r.get("sha256")
        if not sha:
            continue
        if sha not in sha_map:
            sha_map[sha] = []
        sha_map[sha].append(r)

    duplicate_pairs = []
    seen_inep_pairs = set()

    for sha, img_list in sha_map.items():
        if len(img_list) > 1:
            for i in range(len(img_list)):
                for j in range(i + 1, len(img_list)):
                    imgA = img_list[i]
                    imgB = img_list[j]
                    inep_a = imgA.get("inep")
                    inep_b = imgB.get("inep")

                    if inep_a and inep_b and inep_a != inep_b:
                        pair_key = (min(str(inep_a), str(inep_b)), max(str(inep_a), str(inep_b)), sha)
                        if pair_key not in seen_inep_pairs:
                            seen_inep_pairs.add(pair_key)
                            duplicate_pairs.append({
                                "inep_a": imgA.get("inep"),
                                "uf_a": imgA.get("uf"),
                                "fornecedor_a": imgA.get("fornecedor"),
                                "pdf_filename_a": imgA.get("pdf_filename"),
                                "pagina_a": imgA.get("pagina"),
                                "inep_b": imgB.get("inep"),
                                "uf_b": imgB.get("uf"),
                                "fornecedor_b": imgB.get("fornecedor"),
                                "pdf_filename_b": imgB.get("pdf_filename"),
                                "pagina_b": imgB.get("pagina"),
                                "tipo_duplicata": "Exata (100%)",
                                "similaridade": 100.0,
                                "distancia_hamming": 0,
                                "sha256": sha,
                                "thumb_url_a": imgA.get("thumb_url"),
                                "thumb_url_b": imgB.get("thumb_url")
                            })

    print(f"Encontrados {len(duplicate_pairs)} pares de duplicatas exatas!")

    if not duplicate_pairs:
        return

    # Salvar na tabela duplicatas_rdo
    batch_size = 200
    total_saved = 0
    for b in range(0, len(duplicate_pairs), batch_size):
        batch = duplicate_pairs[b:b+batch_size]
        url = f"{SUPABASE_URL}/duplicatas_rdo"
        req = urllib.request.Request(
            url,
            data=json.dumps(batch).encode("utf-8"),
            headers={
                "apikey": SERVICE_KEY,
                "Authorization": f"Bearer {SERVICE_KEY}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                total_saved += len(batch)
                print(f"    [{total_saved}/{len(duplicate_pairs)}] Salvo lote de {len(batch)} duplicatas na tabela duplicatas_rdo!")
        except Exception as e:
            print(f"Erro ao salvar lote de duplicatas: {e}")

    print(f"\n[OK] Re-população concluída com sucesso! Total de {total_saved} duplicatas salvas no Supabase.")

if __name__ == "__main__":
    records = fetch_all_analisados()
    generate_and_save_duplicates(records)
