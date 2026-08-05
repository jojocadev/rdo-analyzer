import os
import io
import json
import time
import hashlib
import urllib.request
import urllib.parse
from typing import List, Dict, Any, Tuple
import fitz  # PyMuPDF
from PIL import Image
import imagehash
import pandas as pd

SUPABASE_URL = "https://jclwfskzstjwmfskbanz.supabase.co/rest/v1"
SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImpjbHdmc2t6c3Rqd21mc2tiYW56Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NTgzNzQ1NSwiZXhwIjoyMTAxNDEzNDU1fQ.B6PzIbTON-AToumtXbCwcrmPlJwMZhrCekrXRkbKZMU"

class SupabaseRDOAnalyzer:
    def __init__(self, min_width: int = 120, min_height: int = 120, phash_threshold: int = 8, cache_dir: str = None):
        self.min_width = min_width
        self.min_height = min_height
        self.phash_threshold = phash_threshold
        self.cache_dir = cache_dir or os.path.join(os.path.dirname(__file__), "extracted_images")
        os.makedirs(self.cache_dir, exist_ok=True)
        
        self.extracted_images: List[Dict[str, Any]] = []
        self.duplicate_pairs: List[Dict[str, Any]] = []
        self.schools_analyzed: List[Dict[str, Any]] = []

    def reset(self):
        self.extracted_images.clear()
        self.duplicate_pairs.clear()
        self.schools_analyzed.clear()

    def fetch_schools_from_supabase(self, fornecedor_filter: str = None, limit: int = 27000) -> List[Dict[str, Any]]:
        """Busca escolas no Supabase por fornecedor ou toda a base."""
        url = f"{SUPABASE_URL}/escolas_conectadas?select=id,escola_id_bubble,inep,uf,fornecedor,tipo_fornecedor,fase,books"
        
        if fornecedor_filter and fornecedor_filter.strip():
            encoded_f = urllib.parse.quote(f"*{fornecedor_filter.strip()}*")
            url += f"&fornecedor=ilike.{encoded_f}"

        url += f"&limit={limit}"

        req = urllib.request.Request(url, headers={
            'apikey': SERVICE_KEY,
            'Authorization': f'Bearer {SERVICE_KEY}'
        })

        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode())
                self.schools_analyzed = data
                return data
        except Exception as e:
            print(f"Erro ao buscar escolas no Supabase: {e}")
            return []

    def process_school_pdfs(self, school_records: List[Dict[str, Any]], progress_callback=None) -> int:
        """Baixa os PDFs das escolas e extrai todas as imagens válidas."""
        total_schools = len(school_records)
        
        for idx, school in enumerate(school_records):
            if progress_callback:
                progress_callback(idx + 1, total_schools, school.get("fornecedor", ""))

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

            for pdf_idx, pdf_url in enumerate(books):
                if not pdf_url or not isinstance(pdf_url, str):
                    continue

                clean_url = "https:" + pdf_url if pdf_url.startswith("//") else pdf_url
                pdf_filename = clean_url.split("/")[-1].split("?")[0]
                unquoted_filename = urllib.parse.unquote(pdf_filename)

                try:
                    req = urllib.request.Request(clean_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        pdf_bytes = resp.read()

                    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                    for page_index in range(len(doc)):
                        page = doc[page_index]
                        image_list = page.get_images(full=True)

                        for img_idx, img_info in enumerate(image_list):
                            xref = img_info[0]
                            base_image = doc.extract_image(xref)
                            image_bytes = base_image["image"]
                            image_ext = base_image["ext"]

                            try:
                                pil_img = Image.open(io.BytesIO(image_bytes))
                                width, height = pil_img.size

                                if width < self.min_width or height < self.min_height:
                                    continue

                                pil_img_rgb = pil_img.convert("RGB") if pil_img.mode not in ("RGB", "L") else pil_img

                                # Ignorar imagens totalmente brancas/uniformes (templates em branco)
                                sha256 = hashlib.sha256(image_bytes).hexdigest()
                                if sha256 == "90cb2766e81912dd996d8387e2406a32e333836543dd88fc1845aa188e5bdce4":
                                    continue

                                extrema = pil_img_rgb.getextrema()
                                if extrema and all(r[0] == r[1] for r in extrema):
                                    continue  # Imagem de cor única 100% sólida/branca

                                phash_val = str(imagehash.phash(pil_img_rgb))

                                thumb_filename = f"{sha256[:16]}_{page_index+1}_{img_idx}.jpg"
                                thumb_path = os.path.join(self.cache_dir, thumb_filename)
                                if not os.path.exists(thumb_path):
                                    pil_img_rgb.save(thumb_path, "JPEG", quality=85)

                                img_record = {
                                    "id": f"{inep or idx}_p{page_index+1}_i{img_idx}",
                                    "escola_id_bubble": school.get("escola_id_bubble"),
                                    "inep": int(inep) if inep else None,
                                    "uf": str(uf) if uf else None,
                                    "fornecedor": str(fornecedor) if fornecedor else "Não informado",
                                    "tipo_fornecedor": str(tipo_fornecedor) if tipo_fornecedor else None,
                                    "fase": str(fase) if fase else None,
                                    "pdf_url": clean_url,
                                    "pdf_filename": unquoted_filename,
                                    "page": int(page_index + 1),
                                    "width": int(width),
                                    "height": int(height),
                                    "sha256": str(sha256),
                                    "phash": str(phash_val),
                                    "thumb_filename": thumb_filename
                                }

                                self.extracted_images.append(img_record)

                            except Exception as e:
                                continue
                    doc.close()
                except Exception as e:
                    print(f"Erro ao baixar/processar PDF {clean_url}: {e}")
                    continue

        return len(self.extracted_images)

    def analyze_duplicates(self) -> Dict[str, Any]:
        """Compara imagens extraídas e agrupa duplicatas por Fornecedor e INEP."""
        total_images = len(self.extracted_images)
        if total_images == 0:
            return {
                "total_schools_analyzed": len(self.schools_analyzed),
                "total_images": 0,
                "exact_duplicate_pairs": 0,
                "visual_duplicate_pairs": 0,
                "total_duplicate_pairs": 0,
                "affected_ineps_count": 0,
                "affected_ineps": [],
                "by_supplier": [],
                "duplicate_pairs": []
            }

        # 1. Duplicatas Exatas (SHA256)
        sha_map: Dict[str, List[Dict[str, Any]]] = {}
        for img in self.extracted_images:
            sha = img["sha256"]
            if sha not in sha_map:
                sha_map[sha] = []
            sha_map[sha].append(img)

        exact_pairs = []
        # Agrupamos primeiro por INEP para evitar comparações dentro do mesmo INEP
        inep_seen: Dict[str, set] = {}  # sha -> set de INEPs que já têm essa imagem
        for sha, img_list in sha_map.items():
            if len(img_list) > 1:
                # Encontrar pares de imagens com INEPs DIFERENTES
                for i in range(len(img_list)):
                    for j in range(i + 1, len(img_list)):
                        imgA = img_list[i]
                        imgB = img_list[j]
                        # CRITÉRIO: só flagrar se INEPs forem diferentes
                        inep_a = imgA.get("inep")
                        inep_b = imgB.get("inep")
                        if inep_a != inep_b:  # INEPs diferentes = foto reutilizada em outra escola!
                            exact_pairs.append({
                                "type": "Exata (100%)",
                                "similarity": 100.0,
                                "distance": 0,
                                "imgA": imgA,
                                "imgB": imgB
                            })

        # 2. Duplicatas Visuais (pHash)
        unique_sha_imgs = [img_list[0] for img_list in sha_map.values()]
        visual_pairs = []
        hash_objects = [imagehash.hex_to_hash(img["phash"]) for img in unique_sha_imgs]

        num_unique = len(unique_sha_imgs)
        for i in range(num_unique):
            hashA = hash_objects[i]
            imgA_rep = unique_sha_imgs[i]
            
            for j in range(i + 1, num_unique):
                hashB = hash_objects[j]
                distance = int(hashA - hashB)
                
                if distance <= self.phash_threshold:
                    similarity = float(max(0.0, round((1.0 - (distance / 64.0)) * 100, 1)))
                    membersA = sha_map[imgA_rep["sha256"]]
                    membersB = sha_map[unique_sha_imgs[j]["sha256"]]
                    
                    for itemA in membersA:
                        for itemB in membersB:
                            # CRITÉRIO: só flagrar se INEPs forem diferentes
                            inep_a = itemA.get("inep")
                            inep_b = itemB.get("inep")
                            if inep_a != inep_b:
                                visual_pairs.append({
                                    "type": "Visual (Perceptual)",
                                    "similarity": similarity,
                                    "distance": distance,
                                    "imgA": itemA,
                                    "imgB": itemB
                                })

        all_pairs = exact_pairs + visual_pairs
        all_pairs.sort(key=lambda x: x["similarity"], reverse=True)
        self.duplicate_pairs = all_pairs

        # 3. Agrupar Resultados Por Fornecedor
        supplier_map: Dict[str, Dict[str, Any]] = {}
        for p in all_pairs:
            forn_a = p["imgA"]["fornecedor"] or "Não informado"
            forn_b = p["imgB"]["fornecedor"] or "Não informado"
            
            for f_name in set([forn_a, forn_b]):
                if f_name not in supplier_map:
                    supplier_map[f_name] = {
                        "fornecedor": f_name,
                        "total_pairs": 0,
                        "exact_pairs": 0,
                        "visual_pairs": 0,
                        "ineps": set(),
                        "pairs": []
                    }
                supplier_map[f_name]["total_pairs"] += 1
                if p["type"] == "Exata (100%)":
                    supplier_map[f_name]["exact_pairs"] += 1
                else:
                    supplier_map[f_name]["visual_pairs"] += 1
                
                if p["imgA"]["inep"]: supplier_map[f_name]["ineps"].add(p["imgA"]["inep"])
                if p["imgB"]["inep"]: supplier_map[f_name]["ineps"].add(p["imgB"]["inep"])
                
                if p not in supplier_map[f_name]["pairs"]:
                    supplier_map[f_name]["pairs"].append(p)

        by_supplier = []
        for f_name, info in supplier_map.items():
            by_supplier.append({
                "fornecedor": f_name,
                "total_pairs": int(info["total_pairs"]),
                "exact_pairs": int(info["exact_pairs"]),
                "visual_pairs": int(info["visual_pairs"]),
                "affected_ineps_count": int(len(info["ineps"])),
                "affected_ineps": [int(i) for i in info["ineps"] if str(i).isdigit()],
                "pairs": info["pairs"]
            })

        by_supplier.sort(key=lambda x: x["total_pairs"], reverse=True)

        # Detalhar INEPs e Fornecedores afetados
        affected_ineps_map: Dict[Any, Dict[str, Any]] = {}
        for p in all_pairs:
            for key in ("imgA", "imgB"):
                inep = p[key]["inep"] or "Sem INEP"
                fornecedor = p[key]["fornecedor"] or "Não informado"
                uf = p[key]["uf"] or "-"
                pdf_fn = p[key]["pdf_filename"]

                if inep not in affected_ineps_map:
                    affected_ineps_map[inep] = {
                        "inep": inep,
                        "uf": uf,
                        "fornecedor": fornecedor,
                        "duplicate_count": 0,
                        "pdfs_affected": set()
                    }
                affected_ineps_map[inep]["duplicate_count"] += 1
                affected_ineps_map[inep]["pdfs_affected"].add(pdf_fn)

        affected_ineps_list = []
        for inep, info in affected_ineps_map.items():
            affected_ineps_list.append({
                "inep": info["inep"],
                "uf": info["uf"],
                "fornecedor": info["fornecedor"],
                "duplicate_count": int(info["duplicate_count"]),
                "pdfs_count": len(info["pdfs_affected"]),
                "pdfs_list": list(info["pdfs_affected"])
            })

        affected_ineps_list.sort(key=lambda x: x["duplicate_count"], reverse=True)

        result = {
            "total_schools_analyzed": len(self.schools_analyzed),
            "total_images": total_images,
            "exact_duplicate_pairs": len(exact_pairs),
            "visual_duplicate_pairs": len(visual_pairs),
            "total_duplicate_pairs": len(all_pairs),
            "affected_ineps_count": len(affected_ineps_list),
            "affected_ineps": affected_ineps_list,
            "by_supplier": by_supplier,
            "duplicate_pairs": all_pairs
        }

        # Salvar resultados automaticamente no Supabase
        self.save_duplicates_to_supabase(all_pairs)

        return result

    def save_duplicates_to_supabase(self, pairs: List[Dict[str, Any]]):
        """Salva os pares de duplicatas diretamente na tabela duplicatas_rdo do Supabase."""
        if not pairs:
            return

        records = []
        for p in pairs:
            imgA = p.get("imgA", {})
            imgB = p.get("imgB", {})
            records.append({
                "inep_a": imgA.get("inep"),
                "uf_a": imgA.get("uf"),
                "fornecedor_a": imgA.get("fornecedor"),
                "pdf_filename_a": imgA.get("pdf_filename"),
                "pagina_a": imgA.get("page"),
                "inep_b": imgB.get("inep"),
                "uf_b": imgB.get("uf"),
                "fornecedor_b": imgB.get("fornecedor"),
                "pdf_filename_b": imgB.get("pdf_filename"),
                "pagina_b": imgB.get("page"),
                "tipo_duplicata": p.get("type"),
                "similaridade": p.get("similarity"),
                "distancia_hamming": p.get("distance"),
                "sha256": imgA.get("sha256"),
                "thumb_url_a": imgA.get("thumb_filename"),
                "thumb_url_b": imgB.get("thumb_filename")
            })

        try:
            url = f"{SUPABASE_URL}/duplicatas_rdo"
            req = urllib.request.Request(
                url,
                data=json.dumps(records).encode("utf-8"),
                headers={
                    "apikey": SERVICE_KEY,
                    "Authorization": f"Bearer {SERVICE_KEY}",
                    "Content-Type": "application/json"
                },
                method="POST"
            )
            with urllib.request.urlopen(req) as resp:
                print(f"[Supabase Persist] {len(records)} duplicatas salvas na tabela duplicatas_rdo!")
        except Exception as e:
            print(f"[Supabase Persist Error] Falha ao salvar duplicatas: {e}")


    def generate_excel_report(self, output_path: str) -> str:
        """Gera relatório Excel completo com abas de Resumo, INEPs Afetados e Pares."""
        rows = []
        for pair in self.duplicate_pairs:
            imgA = pair["imgA"]
            imgB = pair["imgB"]
            rows.append({
                "Tipo Duplicata": pair["type"],
                "Similaridade (%)": f"{pair['similarity']}%",
                "Distância Hamming": pair["distance"],
                "INEP A": imgA["inep"],
                "UF A": imgA["uf"],
                "Fornecedor A": imgA["fornecedor"],
                "PDF A": imgA["pdf_filename"],
                "Página A": imgA["page"],
                "Dimensões A": f"{imgA['width']}x{imgA['height']}",
                "URL A": imgA["pdf_url"],
                "INEP B": imgB["inep"],
                "UF B": imgB["uf"],
                "Fornecedor B": imgB["fornecedor"],
                "PDF B": imgB["pdf_filename"],
                "Página B": imgB["page"],
                "Dimensões B": f"{imgB['width']}x{imgB['height']}",
                "URL B": imgB["pdf_url"],
                "SHA256 Imagem": imgA["sha256"]
            })

        df_pairs = pd.DataFrame(rows)

        summary_rows = [
            {"Métrica": "Total de Escolas Analisadas", "Valor": len(self.schools_analyzed)},
            {"Métrica": "Total de Imagens Extraídas", "Valor": len(self.extracted_images)},
            {"Métrica": "Pares de Duplicatas Encontrados", "Valor": len(self.duplicate_pairs)},
            {"Métrica": "Duplicatas Exatas (100%)", "Valor": sum(1 for p in self.duplicate_pairs if p['similarity'] == 100.0)},
            {"Métrica": "Duplicatas Visuais (<100%)", "Valor": sum(1 for p in self.duplicate_pairs if p['similarity'] < 100.0)},
        ]
        df_summary = pd.DataFrame(summary_rows)

        # Aba de INEPs Afetados
        inep_map = {}
        for p in self.duplicate_pairs:
            for key in ("imgA", "imgB"):
                i = p[key]["inep"]
                f = p[key]["fornecedor"]
                u = p[key]["uf"]
                if i not in inep_map:
                    inep_map[i] = {"inep": i, "uf": u, "fornecedor": f, "count": 0}
                inep_map[i]["count"] += 1

        inep_rows = list(inep_map.values())
        inep_rows.sort(key=lambda x: x["count"], reverse=True)
        df_ineps = pd.DataFrame(inep_rows)

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            df_summary.to_excel(writer, sheet_name="Resumo Geral", index=False)
            df_ineps.to_excel(writer, sheet_name="INEPs Afetados", index=False)
            df_pairs.to_excel(writer, sheet_name="Duplicatas Detectadas", index=False)

        return output_path
