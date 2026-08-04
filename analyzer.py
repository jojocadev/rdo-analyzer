import os
import io
import glob
import hashlib
import time
from typing import List, Dict, Any, Tuple
import fitz  # PyMuPDF
from PIL import Image
import imagehash
import pandas as pd

class RDOImageAnalyzer:
    def __init__(self, min_width: int = 120, min_height: int = 120, phash_threshold: int = 8, cache_dir: str = None):
        """
        :param min_width: Largura mínima para considerar como foto (descarta logotipos/ícones pequenos).
        :param min_height: Altura mínima para considerar como foto.
        :param phash_threshold: Distância Hamming máxima para considerar duplicata visual (0-8, onde 0 = idêntico).
        :param cache_dir: Pasta para salvar as miniaturas extraídas.
        """
        self.min_width = min_width
        self.min_height = min_height
        self.phash_threshold = phash_threshold
        self.cache_dir = cache_dir or os.path.join(os.path.dirname(__file__), "extracted_images")
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Estruturas de resultado
        self.extracted_images: List[Dict[str, Any]] = []
        self.exact_duplicate_groups: Dict[str, List[Dict[str, Any]]] = {}
        self.duplicate_pairs: List[Dict[str, Any]] = []

    def reset(self):
        self.extracted_images.clear()
        self.exact_duplicate_groups.clear()
        self.duplicate_pairs.clear()

    def process_pdf_file(self, pdf_path: str) -> List[Dict[str, Any]]:
        """Extrai todas as imagens válidas de um único arquivo PDF."""
        images_found = []
        filename = os.path.basename(pdf_path)
        
        try:
            doc = fitz.open(pdf_path)
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
                        
                        # Filtrar miniaturas/ícones irrelevantes
                        if width < self.min_width or height < self.min_height:
                            continue

                        # Converter para RGB se necessário para cálculo do pHash
                        if pil_img.mode not in ("RGB", "L"):
                            pil_img_rgb = pil_img.convert("RGB")
                        else:
                            pil_img_rgb = pil_img

                        # Cálculos de Hash
                        sha256 = hashlib.sha256(image_bytes).hexdigest()
                        phash_val = str(imagehash.phash(pil_img_rgb))
                        dhash_val = str(imagehash.dhash(pil_img_rgb))

                        # Salvar miniatura localmente para exibição na Web
                        thumb_filename = f"{sha256[:16]}_{page_index+1}_{img_idx}.jpg"
                        thumb_path = os.path.join(self.cache_dir, thumb_filename)
                        if not os.path.exists(thumb_path):
                            pil_img_rgb.save(thumb_path, "JPEG", quality=85)

                        img_record = {
                            "id": f"{filename}_p{page_index+1}_i{img_idx}",
                            "pdf_path": str(pdf_path),
                            "filename": str(filename),
                            "page": int(page_index + 1),
                            "img_index": int(img_idx),
                            "width": int(width),
                            "height": int(height),
                            "ext": str(image_ext),
                            "sha256": str(sha256),
                            "phash": str(phash_val),
                            "dhash": str(dhash_val),
                            "thumb_filename": str(thumb_filename),
                            "thumb_path": str(thumb_path)
                        }

                        images_found.append(img_record)
                        self.extracted_images.append(img_record)

                    except Exception as e:
                        print(f"Erro ao processar imagem {xref} da página {page_index+1} em {filename}: {e}")
                        continue
            doc.close()
        except Exception as e:
            print(f"Erro ao abrir PDF {pdf_path}: {e}")

        return images_found

    def analyze_duplicates(self, progress_callback=None) -> Dict[str, Any]:
        """Compara todas as imagens extraídas para encontrar duplicatas exatas e visuais."""
        total_images = len(self.extracted_images)
        if total_images == 0:
            return {
                "total_images": 0,
                "unique_hashes": 0,
                "exact_duplicate_pairs": 0,
                "visual_duplicate_pairs": 0,
                "total_duplicate_pairs": 0,
                "affected_files_count": 0,
                "duplicate_pairs": []
            }

        # 1. Agrupamento por SHA-256 (Duplicatas Exatas)
        sha_map: Dict[str, List[Dict[str, Any]]] = {}
        for img in self.extracted_images:
            sha = img["sha256"]
            if sha not in sha_map:
                sha_map[sha] = []
            sha_map[sha].append(img)

        exact_pairs = []
        for sha, img_list in sha_map.items():
            if len(img_list) > 1:
                # Existem múltiplos arquivos/páginas com esta exata mesma imagem
                for i in range(len(img_list)):
                    for j in range(i + 1, len(img_list)):
                        imgA = img_list[i]
                        imgB = img_list[j]
                        # Apenas registrar se forem de arquivos diferentes OU páginas diferentes
                        if imgA["filename"] != imgB["filename"] or imgA["page"] != imgB["page"]:
                            exact_pairs.append({
                                "type": "Exata (100%)",
                                "similarity": 100.0,
                                "distance": 0,
                                "imgA": imgA,
                                "imgB": imgB
                            })

        # 2. Comparação Perceptual (pHash) para Duplicatas Visuais (Redimensionadas, comprimidas)
        unique_sha_imgs = [img_list[0] for img_list in sha_map.values()]
        visual_pairs = []
        
        # Converter phashes para objetos imagehash
        hash_objects = [imagehash.hex_to_hash(img["phash"]) for img in unique_sha_imgs]

        num_unique = len(unique_sha_imgs)
        for i in range(num_unique):
            if progress_callback and i % 50 == 0:
                progress_callback(i, num_unique)
                
            hashA = hash_objects[i]
            imgA_rep = unique_sha_imgs[i]
            
            for j in range(i + 1, num_unique):
                hashB = hash_objects[j]
                distance = int(hashA - hashB)  # Converter numpy.int64 -> Python int
                
                if distance <= self.phash_threshold:
                    # Similaridade percentual aproximada (64 bits no pHash)
                    similarity = float(max(0.0, round((1.0 - (distance / 64.0)) * 100, 1)))
                    
                    # Expandir todos os membros do grupo SHA de A contra o grupo SHA de B
                    membersA = sha_map[imgA_rep["sha256"]]
                    membersB = sha_map[unique_sha_imgs[j]["sha256"]]
                    
                    for itemA in membersA:
                        for itemB in membersB:
                            if itemA["filename"] != itemB["filename"]:
                                visual_pairs.append({
                                    "type": "Visual (Perceptual)",
                                    "similarity": similarity,
                                    "distance": distance,
                                    "imgA": itemA,
                                    "imgB": itemB
                                })

        # Combinar todos os pares
        all_pairs = exact_pairs + visual_pairs
        
        # Ordenar por similaridade decrescente
        all_pairs.sort(key=lambda x: x["similarity"], reverse=True)
        self.duplicate_pairs = all_pairs

        # Contar e detalhar arquivos envolvidos em duplicatas
        affected_files_map: Dict[str, Dict[str, Any]] = {}
        for p in all_pairs:
            for key in ("imgA", "imgB"):
                fn = p[key]["filename"]
                pth = p[key]["pdf_path"]
                pg = p[key]["page"]
                if fn not in affected_files_map:
                    affected_files_map[fn] = {
                        "filename": fn,
                        "pdf_path": pth,
                        "duplicate_count": 0,
                        "pages": set()
                    }
                affected_files_map[fn]["duplicate_count"] += 1
                affected_files_map[fn]["pages"].add(pg)

        affected_files_list = []
        for fn, info in affected_files_map.items():
            affected_files_list.append({
                "filename": fn,
                "pdf_path": info["pdf_path"],
                "duplicate_count": int(info["duplicate_count"]),
                "impacted_pages": [int(p) for p in sorted(info["pages"])]
            })
        
        # Ordenar arquivos com mais duplicatas primeiro
        affected_files_list.sort(key=lambda x: x["duplicate_count"], reverse=True)

        total_pdfs = len(set(img["filename"] for img in self.extracted_images))

        return {
            "total_pdfs_analyzed": int(total_pdfs),
            "total_images": int(total_images),
            "unique_hashes": int(len(sha_map)),
            "exact_duplicate_pairs": int(len(exact_pairs)),
            "visual_duplicate_pairs": int(len(visual_pairs)),
            "total_duplicate_pairs": int(len(all_pairs)),
            "affected_files_count": int(len(affected_files_list)),
            "affected_files": affected_files_list,
            "duplicate_pairs": all_pairs
        }

    def generate_excel_report(self, output_path: str) -> str:
        """Gera um arquivo Excel completo com a lista de duplicatas e arquivos afetados."""
        if not self.duplicate_pairs:
            # Gerar relatório mesmo se vazio
            df_empty = pd.DataFrame(columns=[
                "Tipo Duplicata", "Similaridade (%)", 
                "Arquivo A", "Página A", "Dimensões A",
                "Arquivo B", "Página B", "Dimensões B"
            ])
            df_empty.to_excel(output_path, index=False)
            return output_path

        rows = []
        for pair in self.duplicate_pairs:
            imgA = pair["imgA"]
            imgB = pair["imgB"]
            rows.append({
                "Tipo de Duplicata": pair["type"],
                "Similaridade (%)": f"{pair['similarity']}%",
                "Distância Hamming": pair["distance"],
                "Arquivo A": imgA["filename"],
                "Página A": imgA["page"],
                "Dimensões A": f"{imgA['width']}x{imgA['height']}",
                "Caminho A": imgA["pdf_path"],
                "Arquivo B": imgB["filename"],
                "Página B": imgB["page"],
                "Dimensões B": f"{imgB['width']}x{imgB['height']}",
                "Caminho B": imgB["pdf_path"],
                "SHA256 Imagem": imgA["sha256"]
            })

        df_pairs = pd.DataFrame(rows)
        
        # Aba 1: Resumo
        total_pdfs = len(set(img["filename"] for img in self.extracted_images))
        affected_files_map = {}
        for pair in self.duplicate_pairs:
            affected_files_map[pair["imgA"]["filename"]] = affected_files_map.get(pair["imgA"]["filename"], 0) + 1
            affected_files_map[pair["imgB"]["filename"]] = affected_files_map.get(pair["imgB"]["filename"], 0) + 1

        summary_rows = [
            {"Métrica": "Total de PDFs Analisados", "Valor": total_pdfs},
            {"Métrica": "PDFs Afetados com Duplicatas", "Valor": len(affected_files_map)},
            {"Métrica": "Total de Imagens Extraídas", "Valor": len(self.extracted_images)},
            {"Métrica": "Pares de Duplicatas Encontrados", "Valor": len(self.duplicate_pairs)},
            {"Métrica": "Duplicatas Exatas (100%)", "Valor": sum(1 for p in self.duplicate_pairs if p['similarity'] == 100.0)},
            {"Métrica": "Duplicatas Visuais (<100%)", "Valor": sum(1 for p in self.duplicate_pairs if p['similarity'] < 100.0)},
        ]
        df_summary = pd.DataFrame(summary_rows)

        # Aba 2: PDFs Afetados
        affected_rows = []
        for fn, count in sorted(affected_files_map.items(), key=lambda x: x[1], reverse=True):
            affected_rows.append({
                "Nome do PDF Afetado": fn,
                "Ocorrências de Duplicatas": count
            })
        df_affected = pd.DataFrame(affected_rows)

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            df_summary.to_excel(writer, sheet_name="Resumo Geral", index=False)
            df_affected.to_excel(writer, sheet_name="PDFs Afetados", index=False)
            df_pairs.to_excel(writer, sheet_name="Duplicatas Detectadas", index=False)

        return output_path
