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

@app.route("/api/status", methods=["GET"])
def get_status():
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
