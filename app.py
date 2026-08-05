import os
import glob
import time
import threading
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from analyzer import RDOImageAnalyzer

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

# Diretórios base
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
CACHE_DIR = os.path.join(BASE_DIR, "extracted_images")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

analyzer = RDOImageAnalyzer(cache_dir=CACHE_DIR)

# Estado global de processamento
processing_state = {
    "status": "idle",  # idle, processing, completed, error
    "total_files": 0,
    "processed_files": 0,
    "current_file": "",
    "total_images": 0,
    "progress_pct": 0,
    "start_time": 0,
    "elapsed_time": 0,
    "error_message": "",
    "results": None
}

processing_lock = threading.Lock()

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/extracted_images/<path:filename>")
def serve_thumb(filename):
    return send_from_directory(CACHE_DIR, filename)

def sanitize_json(obj):
    """Converte recursivamente numpy int64/float64 e outros objetos para tipos Python nativos."""
    if isinstance(obj, dict):
        return {k: sanitize_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_json(v) for v in obj]
    elif hasattr(obj, "item"):  # Trata numpy.int64, numpy.float64, etc.
        return obj.item()
    return obj

def load_results_from_supabase():
    """Carrega o último resultado salvo na tabela duplicatas_rdo do Supabase caso a memória do servidor esteja vazia."""
    global processing_state
    if processing_state["results"] is not None:
        return

    import urllib.request, json as json_lib
    service_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImpjbHdmc2t6c3Rqd21mc2tiYW56Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NTgzNzQ1NSwiZXhwIjoyMTAxNDEzNDU1fQ.B6PzIbTON-AToumtXbCwcrmPlJwMZhrCekrXRkbKZMU"
    supabase_url = "https://jclwfskzstjwmfskbanz.supabase.co/rest/v1/duplicatas_rdo?select=*&limit=5000"
    
    try:
        req = urllib.request.Request(supabase_url, headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"})
        with urllib.request.urlopen(req) as resp:
            rows = json_lib.loads(resp.read().decode())
        
        if not rows:
            return

        duplicate_pairs = []
        affected_ineps_map = {}

        for r in rows:
            pair = {
                "type": r.get("tipo_duplicata") or "Exata (100%)",
                "similarity": float(r.get("similaridade") or 100.0),
                "distance": r.get("distancia_hamming") or 0,
                "imgA": {
                    "inep": r.get("inep_a"),
                    "uf": r.get("uf_a"),
                    "fornecedor": r.get("fornecedor_a"),
                    "pdf_filename": r.get("pdf_filename_a"),
                    "page": r.get("pagina_a") or 1,
                    "thumb_filename": f"{r.get('sha256', '')[:16]}_1_0.jpg"
                },
                "imgB": {
                    "inep": r.get("inep_b"),
                    "uf": r.get("uf_b"),
                    "fornecedor": r.get("fornecedor_b"),
                    "pdf_filename": r.get("pdf_filename_b"),
                    "page": r.get("pagina_b") or 1,
                    "thumb_filename": f"{r.get('sha256', '')[:16]}_1_1.jpg"
                }
            }
            duplicate_pairs.append(pair)

            for key in ("imgA", "imgB"):
                inep = pair[key]["inep"]
                if inep:
                    if inep not in affected_ineps_map:
                        affected_ineps_map[inep] = {
                            "inep": inep,
                            "uf": pair[key]["uf"],
                            "fornecedor": pair[key]["fornecedor"],
                            "duplicate_count": 0
                        }
                    affected_ineps_map[inep]["duplicate_count"] += 1

        results = {
            "total_schools_analyzed": 26965,
            "total_images": len(rows) * 2,
            "exact_duplicate_pairs": len([p for p in duplicate_pairs if p["similarity"] == 100]),
            "visual_duplicate_pairs": len([p for p in duplicate_pairs if p["similarity"] < 100]),
            "total_duplicate_pairs": len(duplicate_pairs),
            "affected_ineps_count": len(affected_ineps_map),
            "affected_ineps": list(affected_ineps_map.values()),
            "duplicate_pairs": duplicate_pairs
        }

        with processing_lock:
            processing_state["status"] = "completed"
            processing_state["progress_pct"] = 100.0
            processing_state["results"] = results
            processing_state["excel_path"] = os.path.join(REPORTS_DIR, "Relatorio_Duplicatas_Supabase.xlsx")
    except Exception as e:
        print(f"Erro ao carregar do Supabase: {e}")

@app.route("/api/status", methods=["GET"])
def get_status():
    global processing_state
    load_results_from_supabase()
    with processing_lock:
        state = sanitize_json(processing_state.copy())
        if state["status"] == "processing" and state["start_time"] > 0:
            state["elapsed_time"] = round(time.time() - state["start_time"], 1)
        return jsonify(state)

def run_analysis_task(pdf_files: list):
    global processing_state
    
    with processing_lock:
        processing_state["status"] = "processing"
        processing_state["total_files"] = len(pdf_files)
        processing_state["processed_files"] = 0
        processing_state["total_images"] = 0
        processing_state["progress_pct"] = 0
        processing_state["start_time"] = time.time()
        processing_state["error_message"] = ""
        processing_state["results"] = None

    analyzer.reset()
    
    # 1. Extração de imagens dos PDFs
    for idx, pdf_path in enumerate(pdf_files):
        with processing_lock:
            processing_state["current_file"] = os.path.basename(pdf_path)
            processing_state["processed_files"] = idx + 1
            processing_state["progress_pct"] = round(((idx + 1) / len(pdf_files)) * 80, 1)

        imgs = analyzer.process_pdf_file(pdf_path)
        with processing_lock:
            processing_state["total_images"] += len(imgs)

    # 2. Análise de duplicatas
    with processing_lock:
        processing_state["current_file"] = "Analisando similaridades perceptuais..."
        processing_state["progress_pct"] = 85.0

    def progress_cb(current, total):
        with processing_lock:
            pct = 85.0 + ((current / max(1, total)) * 14.0)
            processing_state["progress_pct"] = round(pct, 1)

    results = analyzer.analyze_duplicates(progress_callback=progress_cb)
    
    # 3. Gerar relatório Excel
    excel_path = os.path.join(REPORTS_DIR, "Relatorio_Duplicatas_RDO.xlsx")
    analyzer.generate_excel_report(excel_path)

    with processing_lock:
        processing_state["status"] = "completed"
        processing_state["progress_pct"] = 100.0
        processing_state["elapsed_time"] = round(time.time() - processing_state["start_time"], 1)
        processing_state["results"] = results
        processing_state["excel_path"] = excel_path

@app.route("/api/upload", methods=["POST"])
def upload_files():
    if "files" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    files = request.files.getlist("files")
    if not files or files[0].filename == "":
        return jsonify({"error": "Nenhum arquivo selecionado"}), 400

    saved_paths = []
    for file in files:
        if file.filename.lower().endswith(".pdf"):
            save_path = os.path.join(UPLOAD_DIR, file.filename)
            file.save(save_path)
            saved_paths.append(save_path)

    if not saved_paths:
        return jsonify({"error": "Nenhum arquivo PDF válido encontrado"}), 400

    # Iniciar processamento em background
    thread = threading.Thread(target=run_analysis_task, args=(saved_paths,))
    thread.daemon = True
    thread.start()

    return jsonify({"message": f"{len(saved_paths)} arquivos recebidos. Processamento iniciado.", "count": len(saved_paths)})

from supabase_analyzer import SupabaseRDOAnalyzer

supabase_analyzer = SupabaseRDOAnalyzer(cache_dir=CACHE_DIR)

def run_supabase_analysis_task(fornecedor_filter: str, limit: int = 100):
    global processing_state
    
    with processing_lock:
        processing_state["status"] = "processing"
        processing_state["total_files"] = 0
        processing_state["processed_files"] = 0
        processing_state["total_images"] = 0
        processing_state["progress_pct"] = 5.0
        processing_state["start_time"] = time.time()
        processing_state["error_message"] = ""
        processing_state["results"] = None

    supabase_analyzer.reset()
    
    # 1. Buscar escolas no Supabase
    with processing_lock:
        processing_state["current_file"] = f"Buscando escolas para o fornecedor: '{fornecedor_filter}' no Supabase..."
    
    schools = supabase_analyzer.fetch_schools_from_supabase(fornecedor_filter=fornecedor_filter, limit=limit)
    total_schools = len(schools)

    if total_schools == 0:
        with processing_lock:
            processing_state["status"] = "error"
            processing_state["error_message"] = f"Nenhuma escola encontrada no Supabase para o fornecedor '{fornecedor_filter}'."
        return

    with processing_lock:
        processing_state["total_files"] = total_schools

    # 2. Processar PDFs das escolas
    def progress_cb(current, total, forn):
        with processing_lock:
            processing_state["processed_files"] = current
            pct = 10.0 + ((current / max(1, total)) * 75.0)
            processing_state["progress_pct"] = round(pct, 1)
            processing_state["current_file"] = f"Analisando PDFs da Escola {current}/{total} ({forn[:30]})..."

    total_imgs = supabase_analyzer.process_school_pdfs(schools, progress_callback=progress_cb)

    with processing_lock:
        processing_state["total_images"] = total_imgs
        processing_state["progress_pct"] = 88.0
        processing_state["current_file"] = "Executando comparação de similaridade e hashes..."

    # 3. Análise de duplicatas
    results = supabase_analyzer.analyze_duplicates()

    # 4. Gerar Relatório Excel
    excel_path = os.path.join(REPORTS_DIR, "Relatorio_Duplicatas_Supabase.xlsx")
    supabase_analyzer.generate_excel_report(excel_path)

    with processing_lock:
        processing_state["status"] = "completed"
        processing_state["progress_pct"] = 100.0
        processing_state["elapsed_time"] = round(time.time() - processing_state["start_time"], 1)
        processing_state["results"] = results
        processing_state["excel_path"] = excel_path

@app.route("/api/suppliers", methods=["GET"])
def get_suppliers():
    """Retorna lista de fornecedores únicos cadastrados no Supabase."""
    import urllib.request, json as json_lib, urllib.parse as up
    service_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImpjbHdmc2t6c3Rqd21mc2tiYW56Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NTgzNzQ1NSwiZXhwIjoyMTAxNDEzNDU1fQ.B6PzIbTON-AToumtXbCwcrmPlJwMZhrCekrXRkbKZMU"
    url = "https://jclwfskzstjwmfskbanz.supabase.co/rest/v1/escolas_conectadas?select=fornecedor&fornecedor=not.is.null&limit=27000"
    try:
        req = urllib.request.Request(url, headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}"
        })
        with urllib.request.urlopen(req) as resp:
            data = json_lib.loads(resp.read().decode())

        def extract_names(f):
            if " (RI)" in f or " (RE)" in f:
                names = []
                for part in f.split("/"):
                    name = part.strip().replace(" (RI)", "").replace(" (RE)", "").strip()
                    if name:
                        names.append(name)
                return names
            return [f.strip()]

        all_names = set()
        for row in data:
            forn = row.get("fornecedor") or ""
            for name in extract_names(forn):
                if name:
                    all_names.add(name)

        return jsonify(sorted(all_names))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def run_all_suppliers_task(limit_per_supplier: int = 5000):
    global processing_state
    
    # 1. Buscar lista de fornecedores
    import urllib.request, json as json_lib
    service_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImpjbHdmc2t6c3Rqd21mc2tiYW56Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NTgzNzQ1NSwiZXhwIjoyMTAxNDEzNDU1fQ.B6PzIbTON-AToumtXbCwcrmPlJwMZhrCekrXRkbKZMU"
    url = "https://jclwfskzstjwmfskbanz.supabase.co/rest/v1/escolas_conectadas?select=fornecedor&fornecedor=not.is.null&limit=27000"
    
    try:
        req = urllib.request.Request(url, headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"})
        with urllib.request.urlopen(req) as resp:
            data = json_lib.loads(resp.read().decode())
        
        all_names = set()
        for row in data:
            forn = row.get("fornecedor") or ""
            if " (RI)" in forn or " (RE)" in forn:
                for part in forn.split("/"):
                    name = part.strip().replace(" (RI)", "").replace(" (RE)", "").strip()
                    if name: all_names.add(name)
            elif forn.strip():
                all_names.add(forn.strip())
        
        suppliers = sorted(all_names)
    except Exception as e:
        with processing_lock:
            processing_state["status"] = "error"
            processing_state["error_message"] = f"Erro ao buscar fornecedores: {e}"
        return

    total_suppliers = len(suppliers)
    accumulated_pairs = []
    accumulated_ineps = {}
    total_images_extracted = 0

    with processing_lock:
        processing_state["status"] = "processing"
        processing_state["total_files"] = total_suppliers
        processing_state["processed_files"] = 0
        processing_state["start_time"] = time.time()

    for idx, supplier in enumerate(suppliers):
        with processing_lock:
            processing_state["processed_files"] = idx + 1
            pct = round(((idx) / total_suppliers) * 100, 1)
            processing_state["progress_pct"] = max(5.0, pct)
            processing_state["current_file"] = f"Fornecedor {idx+1}/{total_suppliers}: '{supplier}'"

        try:
            supabase_analyzer.reset()
            schools = supabase_analyzer.fetch_schools_from_supabase(fornecedor_filter=supplier, limit=limit_per_supplier)
            if schools:
                imgs_count = supabase_analyzer.process_school_pdfs(schools)
                total_images_extracted += imgs_count
                res = supabase_analyzer.analyze_duplicates()
                
                accumulated_pairs.extend(res.get("duplicate_pairs", []))
                for item in res.get("affected_ineps", []):
                    key = str(item.get("inep") or item.get("filename"))
                    if key not in accumulated_ineps:
                        accumulated_ineps[key] = item

                with processing_lock:
                    processing_state["total_images"] = total_images_extracted
        except Exception as err:
            print(f"Erro ao processar fornecedor {supplier}: {err}")

    # Finalizar
    affected_list = list(accumulated_ineps.values())
    final_results = {
        "total_schools_analyzed": total_suppliers,
        "total_images": total_images_extracted,
        "total_duplicate_pairs": len(accumulated_pairs),
        "exact_duplicate_pairs": len([p for p in accumulated_pairs if p.get("similarity") == 100]),
        "visual_duplicate_pairs": len([p for p in accumulated_pairs if p.get("similarity", 0) < 100]),
        "affected_ineps_count": len(affected_list),
        "affected_ineps": affected_list,
        "duplicate_pairs": accumulated_pairs
    }

    excel_path = os.path.join(REPORTS_DIR, "Relatorio_Duplicatas_Todos_Fornecedores.xlsx")
    supabase_analyzer.duplicate_pairs = accumulated_pairs
    supabase_analyzer.generate_excel_report(excel_path)

    with processing_lock:
        processing_state["status"] = "completed"
        processing_state["progress_pct"] = 100.0
        processing_state["elapsed_time"] = round(time.time() - processing_state["start_time"], 1)
        processing_state["results"] = final_results
        processing_state["excel_path"] = excel_path

@app.route("/api/scan-all-suppliers", methods=["POST"])
def scan_all_suppliers():
    thread = threading.Thread(target=run_all_suppliers_task)
    thread.daemon = True
    thread.start()
    return jsonify({"message": "Iniciando análise sequencial de TODOS os fornecedores no servidor em segundo plano..."})

@app.route("/api/scan-supabase-supplier", methods=["POST"])
def scan_supabase_supplier():
    data = request.get_json() or {}
    fornecedor = data.get("fornecedor", "").strip()
    limit = data.get("limit", 27000)

    # fornecedor vazio = analisar todos os fornecedores da base
    thread = threading.Thread(target=run_supabase_analysis_task, args=(fornecedor, limit))
    thread.daemon = True
    thread.start()

    label = f"'{fornecedor}'" if fornecedor else "BASE COMPLETA (todos os fornecedores)"
    return jsonify({"message": f"Iniciando análise no Supabase para {label}..."})

@app.route("/api/results", methods=["GET"])
def get_results():
    with processing_lock:
        if processing_state["results"] is None:
            return jsonify({"error": "Nenhum resultado disponível. Execute uma análise primeiro."}), 404
        return jsonify(sanitize_json(processing_state["results"]))

@app.route("/api/download-report", methods=["GET"])
def download_report():
    excel_path = os.path.join(REPORTS_DIR, "Relatorio_Duplicatas_RDO.xlsx")
    if not os.path.exists(excel_path):
        return jsonify({"error": "Relatório Excel ainda não foi gerado."}), 404

    return send_file(
        excel_path,
        as_attachment=True,
        download_name="Relatorio_Duplicatas_RDO.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

if __name__ == "__main__":
    print("=====================================================")
    print(">>> Servidor RDO Image Analyzer iniciado na porta 5000")
    print(">>> Acesse no navegador: http://localhost:5000")
    print("=====================================================")
    app.run(host="0.0.0.0", port=5000, debug=True)
