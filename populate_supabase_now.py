import sys
import os
import io
import time
import json
import hashlib
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import fitz
from PIL import Image
import imagehash

from config import SUPABASE_URL, SERVICE_KEY  # noqa: E402
CACHE_DIR = r"c:\Projetos AI\Sisop\RDO images\extracted_images"
os.makedirs(CACHE_DIR, exist_ok=True)

def fetch_all_schools():
    all_schools = []
    limit = 1000
    offset = 0
    print(">>> [1/3] Carregando a lista completa de 26.965 escolas do Supabase...")
    while True:
        url = f"{SUPABASE_URL}/escolas_conectadas?select=*&limit={limit}&offset={offset}"
        req = urllib.request.Request(url, headers={"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"})
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode())
                if not data:
                    break
                all_schools.extend(data)
                if len(data) < limit:
                    break
                offset += limit
        except Exception as e:
            print(f"Erro ao buscar escolas: {e}")
            break
    print(f"    [OK] Carregadas {len(all_schools)} escolas com sucesso!\n")
    return all_schools

def process_single_school(school, idx):
    inep = school.get("inep")
    uf = school.get("uf")
    fornecedor = school.get("fornecedor")
    tipo_fornecedor = school.get("tipo_fornecedor")
    fase = school.get("fase")
    books = school.get("books") or []

    if isinstance(books, str):
        try:
            books = json.loads(books)
        except Exception:
            books = [books]

    school_images = []
    for pdf_idx, pdf_url in enumerate(books):
        if not pdf_url or not isinstance(pdf_url, str):
            continue

        clean_url = "https:" + pdf_url if pdf_url.startswith("//") else pdf_url
        pdf_filename = clean_url.split("/")[-1].split("?")[0]
        unquoted_filename = urllib.parse.unquote(pdf_filename)

        try:
            req = urllib.request.Request(clean_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                pdf_bytes = resp.read()

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            for page_index in range(len(doc)):
                page = doc[page_index]
                image_list = page.get_images(full=True)

                for img_idx, img_info in enumerate(image_list):
                    xref = img_info[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]

                    try:
                        pil_img = Image.open(io.BytesIO(image_bytes))
                        width, height = pil_img.size

                        if width < 100 or height < 100:
                            continue

                        pil_img_rgb = pil_img.convert("RGB") if pil_img.mode not in ("RGB", "L") else pil_img

                        sha256 = hashlib.sha256(image_bytes).hexdigest()
                        if sha256 == "90cb2766e81912dd996d8387e2406a32e333836543dd88fc1845aa188e5bdce4":
                            continue

                        extrema = pil_img_rgb.getextrema()
                        if extrema and all(r[0] == r[1] for r in extrema):
                            continue

                        phash_val = str(imagehash.phash(pil_img_rgb))

                        thumb_filename = f"{sha256[:16]}_{page_index+1}_{img_idx}.jpg"
                        thumb_path = os.path.join(CACHE_DIR, thumb_filename)
                        if not os.path.exists(thumb_path):
                            pil_img_rgb.save(thumb_path, "JPEG", quality=85)

                        img_record = {
                            "inep": int(inep) if inep else None,
                            "uf": str(uf) if uf else None,
                            "fornecedor": str(fornecedor) if fornecedor else "Não informado",
                            "pdf_filename": unquoted_filename,
                            "pdf_url": clean_url,
                            "pagina": int(page_index + 1),
                            "sha256": str(sha256),
                            "thumb_url": thumb_filename,
                            "tem_duplicata": False,
                            "_phash": str(phash_val)
                        }

                        school_images.append(img_record)

                    except Exception:
                        continue
            doc.close()
        except Exception:
            continue

    return school_images

def save_to_supabase(table_name, records):
    if not records:
        return
    batch_size = 500
    for b in range(0, len(records), batch_size):
        batch = records[b:b+batch_size]
        url = f"{SUPABASE_URL}/{table_name}"
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
                pass
        except Exception as e:
            print(f"Erro ao salvar em {table_name}: {e}")

def main():
    print("=========================================================")
    print(">>> INICIANDO RECUPERAÇÃO EM STREAMING PARA O SUPABASE ")
    print("=========================================================")

    schools = fetch_all_schools()
    total_schools = len(schools)

    all_extracted_records = []
    buffer_records = []
    total_inserted = 0
    t0 = time.time()

    print(">>> [2/3] Baixando/Extraindo imagens com 32 Threads Paralelas e enviando ao Supabase em tempo real...")

    with ThreadPoolExecutor(max_workers=32) as executor:
        future_to_school = {
            executor.submit(process_single_school, school, idx): (idx, school)
            for idx, school in enumerate(schools)
        }

        completed = 0
        for future in as_completed(future_to_school):
            completed += 1
            try:
                imgs = future.result()
                if imgs:
                    all_extracted_records.extend(imgs)
                    
                    # Preparar registro limpo para rdo_analisados (sem o campo interno _phash)
                    for item in imgs:
                        record_clean = {k: v for k, v in item.items() if k != "_phash"}
                        buffer_records.append(record_clean)

                    # Quando acumular 500 registros no buffer, envia direto ao Supabase!
                    if len(buffer_records) >= 500:
                        save_to_supabase("rdo_analisados", buffer_records)
                        total_inserted += len(buffer_records)
                        buffer_records.clear()

            except Exception as e:
                pass

            if completed % 1000 == 0 or completed == total_schools:
                elapsed = round(time.time() - t0, 1)
                pct = round((completed / total_schools) * 100, 1)
                print(f"    [{completed}/{total_schools}] ({pct}%) escolas processadas em {elapsed}s... (Total de registros salvos no Supabase: {total_inserted + len(buffer_records)})")

    # Salvar últimos registros pendentes no buffer
    if buffer_records:
        save_to_supabase("rdo_analisados", buffer_records)
        total_inserted += len(buffer_records)
        buffer_records.clear()

    total_time = round(time.time() - t0, 1)
    print(f"\n[OK] Fase 2 Concluída! Total de {total_inserted} registros recuperados e salvos na tabela rdo_analisados do Supabase em {total_time}s!")

    # 3. Analisar duplicatas e popular duplicatas_rdo
    print("\n>>> [3/3] Analisando pares de duplicatas entre INEPs diferentes...")
    sha_map = {}
    for r in all_extracted_records:
        sha = r.get("sha256")
        if sha:
            if sha not in sha_map: sha_map[sha] = []
            sha_map[sha].append(r)

    duplicate_pairs = []
    seen_pairs = set()

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
                        if pair_key not in seen_pairs:
                            seen_pairs.add(pair_key)
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
    if duplicate_pairs:
        save_to_supabase("duplicatas_rdo", duplicate_pairs)
        print(f"[OK] {len(duplicate_pairs)} pares de duplicatas salvos na tabela duplicatas_rdo do Supabase!")

    print("\n=========================================================")
    print(">>> PROCESSO DE RECUPERAÇÃO CONCLUÍDO COM ÉXITO!")
    print("=========================================================")

if __name__ == "__main__":
    main()
