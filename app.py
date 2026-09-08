import os

# Trava as bibliotecas nativas em 1 thread ANTES de qualquer import que carregue o
# numpy (analyzer.py puxa imagehash e pandas). Sem isso, o OpenBLAS abre 16 threads
# por thread chamadora: com 32 workers na varredura, viram centenas de threads
# nativas com buffer cada, esgotam o commit da máquina e derrubam o servidor.
# Ver a nota equivalente no topo de analisar_fase.py.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_var, "1")

import glob
import time
import threading

from config import SUPABASE_URL, SERVICE_KEY, exigir_credenciais
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from analyzer import RDOImageAnalyzer

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

# Ferramenta local em evolução: sem isso o navegador serve app.js/fase.js do cache e
# a tela continua com a versão antiga até um Ctrl+Shift+R.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


@app.after_request
def _sem_cache(resp):
    if request.path.endswith((".js", ".css", ".html")) or request.path == "/":
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
    return resp

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
    file_path = os.path.join(CACHE_DIR, filename)
    if os.path.exists(file_path):
        return send_from_directory(CACHE_DIR, filename)
    
    # Busca flexível por SHA256 prefix
    sha_prefix = filename.split("_")[0]
    if sha_prefix:
        matches = [f for f in os.listdir(CACHE_DIR) if f.startswith(sha_prefix)]
        if matches:
            return send_from_directory(CACHE_DIR, matches[0])
            
    return send_from_directory("static", "index.html"), 404

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
    supabase_url = f"{SUPABASE_URL}/duplicatas_rdo?select=*&limit=5000"
    
    try:
        req = urllib.request.Request(supabase_url, headers={"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"})
        with urllib.request.urlopen(req) as resp:
            rows = json_lib.loads(resp.read().decode())
        
        if not rows:
            return

        duplicate_pairs = []
        affected_ineps_map = {}

        for r in rows:
            sha = r.get("sha256", "")
            thumb_a = r.get("thumb_url_a") or (f"{sha[:16]}_1_0.jpg" if sha else "default.jpg")
            thumb_b = r.get("thumb_url_b") or (f"{sha[:16]}_1_1.jpg" if sha else "default.jpg")

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
                    "thumb_filename": thumb_a
                },
                "imgB": {
                    "inep": r.get("inep_b"),
                    "uf": r.get("uf_b"),
                    "fornecedor": r.get("fornecedor_b"),
                    "pdf_filename": r.get("pdf_filename_b"),
                    "page": r.get("pagina_b") or 1,
                    "thumb_filename": thumb_b
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

@app.route("/api/db-duplicates", methods=["GET"])
def get_db_duplicates():
    """Consulta diretamente a tabela duplicatas_rdo no Supabase com paginação e busca por fornecedor."""
    import urllib.request, json as json_lib, urllib.parse as up
    supplier = request.args.get("fornecedor", "").strip()
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 50))
    offset = (page - 1) * limit

    supabase_url = f"{SUPABASE_URL}/duplicatas_rdo?select=*"
    
    if supplier:
        encoded_sup = up.quote(supplier)
        supabase_url += f"&or=(fornecedor_a.ilike.*{encoded_sup}*,fornecedor_b.ilike.*{encoded_sup}*)"
        
    supabase_url += f"&order=similaridade.desc&limit={limit}&offset={offset}"

    try:
        req = urllib.request.Request(supabase_url, headers={
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}",
            "Prefer": "count=exact"
        })
        with urllib.request.urlopen(req) as resp:
            rows = json_lib.loads(resp.read().decode())
            content_range = resp.headers.get("content-range")
            total = int(content_range.split("/")[-1]) if content_range and "/" in content_range else len(rows)

        duplicate_pairs = []
        for r in rows:
            sha = r.get("sha256", "")
            thumb_a = r.get("thumb_url_a") or (f"{sha[:16]}_1_0.jpg" if sha else "default.jpg")
            thumb_b = r.get("thumb_url_b") or (f"{sha[:16]}_1_1.jpg" if sha else "default.jpg")
            duplicate_pairs.append({
                "type": r.get("tipo_duplicata") or "Exata (100%)",
                "similarity": float(r.get("similaridade") or 100.0),
                "distance": r.get("distancia_hamming") or 0,
                "imgA": {
                    "inep": r.get("inep_a"),
                    "uf": r.get("uf_a"),
                    "fornecedor": r.get("fornecedor_a"),
                    "pdf_filename": r.get("pdf_filename_a"),
                    "pdf_url": r.get("pdf_url_a"),
                    "page": r.get("pagina_a") or 1,
                    "thumb_filename": thumb_a
                },
                "imgB": {
                    "inep": r.get("inep_b"),
                    "uf": r.get("uf_b"),
                    "fornecedor": r.get("fornecedor_b"),
                    "pdf_filename": r.get("pdf_filename_b"),
                    "pdf_url": r.get("pdf_url_b"),
                    "page": r.get("pagina_b") or 1,
                    "thumb_filename": thumb_b
                }
            })

        return jsonify({
            "total": total,
            "page": page,
            "limit": limit,
            "duplicate_pairs": duplicate_pairs
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/analisados", methods=["GET"])
def get_analisados():
    """Retorna lista de todos os PDFs/imagens analisados salvos na tabela rdo_analisados."""
    import urllib.request, json as json_lib, urllib.parse as up
    supplier = request.args.get("fornecedor", "").strip()
    dup_filter = request.args.get("tem_duplicata", "").strip().lower()
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 50))
    offset = (page - 1) * limit

    supabase_url = f"{SUPABASE_URL}/rdo_analisados?select=*"

    if supplier:
        encoded_sup = up.quote(supplier)
        supabase_url += f"&fornecedor=ilike.*{encoded_sup}*"

    if dup_filter in ["true", "1"]:
        supabase_url += "&tem_duplicata=eq.true"
    elif dup_filter in ["false", "0"]:
        supabase_url += "&tem_duplicata=eq.false"

    supabase_url += f"&order=criado_em.desc&limit={limit}&offset={offset}"

    try:
        req = urllib.request.Request(supabase_url, headers={
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}",
            "Prefer": "count=exact"
        })
        with urllib.request.urlopen(req) as resp:
            rows = json_lib.loads(resp.read().decode())
            content_range = resp.headers.get("content-range")
            total = int(content_range.split("/")[-1]) if content_range and "/" in content_range else len(rows)

        return jsonify({
            "total": total,
            "page": page,
            "limit": limit,
            "analisados": rows
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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

def run_supabase_analysis_task(fornecedor_filter: str, limit: int = 100, use_test_table: bool = False):
    global processing_state
    
    if use_test_table:
        supabase_analyzer.table_analisados = "rdo_analisados_test"
        supabase_analyzer.table_duplicates = "duplicatas_rdo_test"
    else:
        supabase_analyzer.table_analisados = "rdo_analisados"
        supabase_analyzer.table_duplicates = "duplicatas_rdo"

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

    # 2. Processar PDFs das escolas com 20 workers simultâneos
    def progress_cb(current, total, forn):
        with processing_lock:
            processing_state["processed_files"] = current
            pct = 10.0 + ((current / max(1, total)) * 75.0)
            processing_state["progress_pct"] = round(pct, 1)
            processing_state["current_file"] = f"Analisando PDFs da Escola {current}/{total} ({forn[:30]})..."

    total_imgs = supabase_analyzer.process_school_pdfs(schools, progress_callback=progress_cb, max_workers=20)

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
    url = f"{SUPABASE_URL}/escolas_conectadas?select=fornecedor&fornecedor=not.is.null&limit=27000"
    try:
        req = urllib.request.Request(url, headers={
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}"
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

def process_single_supplier_task(supplier_name: str, limit_per_supplier: int, table_analisados: str, table_duplicates: str):
    """Worker isolado para processar um único fornecedor de forma independente e thread-safe."""
    local_analyzer = SupabaseRDOAnalyzer(table_analisados=table_analisados, table_duplicates=table_duplicates, cache_dir=CACHE_DIR)
    schools = local_analyzer.fetch_schools_from_supabase(fornecedor_filter=supplier_name, limit=limit_per_supplier)
    if not schools:
        return 0, [], []
    imgs_count = local_analyzer.process_school_pdfs(schools, max_workers=10)
    res = local_analyzer.analyze_duplicates()
    return imgs_count, res.get("duplicate_pairs", []), res.get("affected_ineps", [])

def run_all_suppliers_task(limit_per_supplier: int = 5000, use_test_table: bool = True):
    global processing_state
    
    tbl_analisados = "rdo_analisados_test" if use_test_table else "rdo_analisados"
    tbl_duplicates = "duplicatas_rdo_test" if use_test_table else "duplicatas_rdo"
    
    import urllib.request, json as json_lib
    # 1. Buscar lista de fornecedores únicos
    url = f"{SUPABASE_URL}/escolas_conectadas?select=fornecedor&fornecedor=not.is.null&limit=27000"
    try:
        req = urllib.request.Request(url, headers={"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"})
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
            processing_state["error_message"] = f"Erro ao buscar lista de fornecedores: {e}"
        return

    total_suppliers = len(suppliers)
    
    with processing_lock:
        processing_state["status"] = "processing"
        processing_state["total_files"] = total_suppliers
        processing_state["processed_files"] = 0
        processing_state["progress_pct"] = 1.0
        processing_state["start_time"] = time.time()
        processing_state["current_file"] = f"Iniciando varredura PARALELA de {total_suppliers} Fornecedores SIMULTÂNEOS..."
        processing_state["results"] = None

    accumulated_pairs = []
    accumulated_ineps = {}
    total_images_extracted = 0

    # 2. Executar múltiplos Fornecedores em PARALELO (Pool de 8 Fornecedores Simultâneos)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    completed_num = 0
    with ThreadPoolExecutor(max_workers=8) as supplier_executor:
        future_to_supplier = {
            supplier_executor.submit(process_single_supplier_task, supplier, limit_per_supplier, tbl_analisados, tbl_duplicates): supplier
            for supplier in suppliers
        }

        for future in as_completed(future_to_supplier):
            completed_num += 1
            supplier = future_to_supplier[future]
            try:
                imgs_count, supplier_pairs, affected = future.result()
                total_images_extracted += imgs_count
                accumulated_pairs.extend(supplier_pairs)

                for item in affected:
                    key = str(item.get("inep") or item.get("filename"))
                    if key not in accumulated_ineps:
                        accumulated_ineps[key] = item

                with processing_lock:
                    processing_state["processed_files"] = completed_num
                    pct = round((completed_num / total_suppliers) * 100, 1)
                    processing_state["progress_pct"] = max(2.0, pct)
                    processing_state["total_images"] = total_images_extracted
                    processing_state["current_file"] = f"Concluídos {completed_num}/{total_suppliers} Fornecedores (Mais recente: '{supplier[:30]}')"

            except Exception as err:
                print(f"Erro ao processar fornecedor '{supplier}': {err}")

    # Finalizar varredura de todos os fornecedores
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
    data = request.get_json(silent=True) or {}
    use_test_table = data.get("use_test_table", True)
    thread = threading.Thread(target=run_all_suppliers_task, kwargs={"use_test_table": use_test_table})
    thread.daemon = True
    thread.start()
    tbl = "rdo_analisados_test" if use_test_table else "rdo_analisados"
    return jsonify({"message": f"Iniciando análise otimizada (20 threads) para TODOS os fornecedores (Tabela: {tbl})..."})

@app.route("/api/scan-supabase-supplier", methods=["POST"])
def scan_supabase_supplier():
    data = request.get_json(silent=True) or {}
    fornecedor = data.get("fornecedor", "").strip()
    limit = data.get("limit", 27000)
    use_test_table = data.get("use_test_table", True)

    # fornecedor vazio = analisar todos os fornecedores da base
    thread = threading.Thread(target=run_supabase_analysis_task, args=(fornecedor, limit), kwargs={"use_test_table": use_test_table})
    thread.daemon = True
    thread.start()

    tbl = "rdo_analisados_test" if use_test_table else "rdo_analisados"
    label = f"'{fornecedor}'" if fornecedor else "BASE COMPLETA"
    return jsonify({"message": f"Iniciando análise otimizada (20 threads) para {label} no Supabase (Tabela: {tbl})..."})

# --------------------------------------------------------------------------- #
# Análise por Fase (com histórico persistente em disco)
# --------------------------------------------------------------------------- #

import analisar_fase as fase_engine

# Estado da análise por fase, separado do estado das outras abas para que uma
# varredura longa por fase não seja sobrescrita por um upload avulso.
fase_state = {
    "status": "idle",          # idle, processing, completed, error, cancelled
    "run_id": "",
    "fase": "",
    "fornecedor": "",
    "etapa": "",
    "mensagem": "",
    "progress_pct": 0.0,
    "start_time": 0,
    "elapsed_time": 0,
    "erro": "",
    "metricas": {},
}
fase_lock = threading.Lock()
fase_cancel = threading.Event()


@app.route("/api/fases", methods=["GET"])
def get_fases():
    """Lista as fases disponíveis com a contagem de escolas de cada uma."""
    try:
        exigir_credenciais()
        return jsonify(fase_engine.fetch_fases())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def run_fase_task(fase, fornecedor, workers, gravar_supabase, amostra,
                  mesmo_fornecedor, somente_identicas, ignorar_templates):
    def progress(pct, mensagem, etapa):
        with fase_lock:
            fase_state["progress_pct"] = pct
            fase_state["mensagem"] = mensagem
            fase_state["etapa"] = etapa
            fase_state["elapsed_time"] = round(time.time() - fase_state["start_time"], 1)

    try:
        meta = fase_engine.run_analysis(
            fase=fase, fornecedor=fornecedor, workers=workers,
            amostra=amostra, gravar_supabase=gravar_supabase,
            mesmo_fornecedor=mesmo_fornecedor,
            somente_identicas=somente_identicas,
            ignorar_templates=ignorar_templates,
            progress=progress, cancelado=fase_cancel.is_set,
        )
        with fase_lock:
            fase_state["status"] = "completed"
            fase_state["run_id"] = meta["run_id"]
            fase_state["metricas"] = meta.get("metricas", {})
            fase_state["progress_pct"] = 100.0
            fase_state["elapsed_time"] = round(time.time() - fase_state["start_time"], 1)
    except Exception as e:
        with fase_lock:
            fase_state["status"] = "cancelled" if fase_cancel.is_set() else "error"
            fase_state["erro"] = str(e)
            fase_state["elapsed_time"] = round(time.time() - fase_state["start_time"], 1)


@app.route("/api/scan-fase", methods=["POST"])
def scan_fase():
    """Dispara a varredura de uma fase inteira (o botão da interface)."""
    with fase_lock:
        if fase_state["status"] == "processing":
            return jsonify({"error": "Já existe uma análise por fase em andamento.",
                            "run_id": fase_state["run_id"]}), 409

    data = request.get_json(silent=True) or {}
    fase = str(data.get("fase", "5")).strip()
    fornecedor = (data.get("fornecedor") or "").strip()
    workers = int(data.get("workers", 32))
    amostra = int(data.get("amostra", 0))
    gravar_supabase = bool(data.get("gravar_supabase", True))
    mesmo_fornecedor = bool(data.get("mesmo_fornecedor", True))
    somente_identicas = bool(data.get("somente_identicas", True))
    ignorar_templates = bool(data.get("ignorar_templates", True))

    run_id = fase_engine.novo_run_id(fase, fornecedor)
    fase_cancel.clear()

    with fase_lock:
        fase_state.update({
            "status": "processing", "run_id": run_id, "fase": fase,
            "fornecedor": fornecedor, "etapa": "inicio",
            "mensagem": "Iniciando análise...", "progress_pct": 0.0,
            "start_time": time.time(), "elapsed_time": 0, "erro": "", "metricas": {},
        })

    thread = threading.Thread(
        target=run_fase_task,
        args=(fase, fornecedor, workers, gravar_supabase, amostra,
              mesmo_fornecedor, somente_identicas, ignorar_templates),
        daemon=True,
    )
    thread.start()

    alvo = fornecedor or "todos os fornecedores"
    return jsonify({"message": f"Análise da fase {fase} ({alvo}) iniciada.", "run_id": run_id})


@app.route("/api/fase-status", methods=["GET"])
def get_fase_status():
    with fase_lock:
        state = sanitize_json(dict(fase_state))
    if state["status"] == "processing" and state["start_time"]:
        state["elapsed_time"] = round(time.time() - state["start_time"], 1)
    return jsonify(state)


@app.route("/api/cancelar-fase", methods=["POST"])
def cancelar_fase():
    fase_cancel.set()
    with fase_lock:
        em_andamento = fase_state["status"] == "processing"
    return jsonify({"message": "Cancelamento solicitado. Os PDFs já baixados ficam no "
                               "cache e a próxima execução retoma de onde parou."
                    if em_andamento else "Nenhuma análise em andamento."})


@app.route("/api/historico", methods=["GET"])
def get_historico():
    """Lista todas as execuções já realizadas (o histórico não é apagado)."""
    try:
        runs = fase_engine.listar_runs()
        with fase_lock:
            em_andamento = fase_state["run_id"] if fase_state["status"] == "processing" else None

        # Uma execução gravada como "processando" que não é a atual ficou órfã (queda
        # do servidor, reinício do Flask). Sem isso ela apareceria "Em andamento" para
        # sempre; os PDFs baixados continuam no cache e uma nova execução retoma.
        for r in runs:
            if r.get("status") == "processando" and r.get("run_id") != em_andamento:
                r["status"] = "interrompido"
                r["erro"] = r.get("erro") or ("Execução interrompida antes de terminar. "
                                              "Rode de novo: o download já feito está em cache.")

        return jsonify({"runs": runs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/historico/<run_id>", methods=["GET"])
def get_historico_run(run_id):
    """Recarrega na tela o resultado visual completo de uma execução do histórico."""
    meta = fase_engine.carregar_run(run_id)
    if not meta:
        return jsonify({"error": "Execução não encontrada no histórico."}), 404

    grupos = meta.pop("grupos", []) or []

    # Paginação dos grupos: uma varredura grande pode ter milhares deles.
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 40))
    busca = (request.args.get("busca") or "").strip().lower()
    min_escolas = int(request.args.get("min_escolas", 0))

    if busca or min_escolas:
        filtrados = []
        for g in grupos:
            if min_escolas and g.get("qtd_ineps", 0) < min_escolas:
                continue
            if busca:
                alvo = " ".join([
                    " ".join(str(i) for i in g.get("ineps", [])),
                    " ".join(g.get("fornecedores", [])),
                    " ".join(g.get("ufs", [])),
                ]).lower()
                if busca not in alvo:
                    continue
            filtrados.append(g)
        grupos = filtrados

    total = len(grupos)
    inicio = (page - 1) * limit
    meta["grupos"] = grupos[inicio:inicio + limit]
    meta["grupos_total"] = total
    meta["page"] = page
    meta["limit"] = limit
    return jsonify(sanitize_json(meta))


@app.route("/api/historico/<run_id>", methods=["DELETE"])
def delete_historico_run(run_id):
    if fase_engine.excluir_run(run_id):
        return jsonify({"message": f"Execução {run_id} removida do histórico."})
    return jsonify({"error": "Execução não encontrada."}), 404


@app.route("/api/historico/<run_id>/analisados", methods=["GET"])
def get_historico_analisados(run_id):
    """
    Lista todos os PDFs/imagens auditados nesta execução.

    É a mesma visão da aba "Todos os PDFs Analisados", mas escopada à execução do
    histórico, com os mesmos filtros de fornecedor e status de duplicata.
    """
    itens = fase_engine.ler_analisados(run_id)
    if not itens and not os.path.isdir(fase_engine.caminho_run(run_id)):
        return jsonify({"error": "Execução não encontrada no histórico."}), 404

    fornecedor = (request.args.get("fornecedor") or "").strip().lower()
    dup_filter = (request.args.get("tem_duplicata") or "").strip().lower()
    busca = (request.args.get("busca") or "").strip().lower()
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 50))

    if fornecedor:
        itens = [i for i in itens if fornecedor in (i.get("fornecedor") or "").lower()]
    if dup_filter in ("true", "1"):
        itens = [i for i in itens if i.get("tem_duplicata")]
    elif dup_filter in ("false", "0"):
        itens = [i for i in itens if not i.get("tem_duplicata")]
    if busca:
        itens = [i for i in itens if busca in " ".join([
            str(i.get("inep") or ""), i.get("uf") or "",
            i.get("fornecedor") or "", i.get("pdf_filename") or "",
        ]).lower()]

    # Os com duplicata primeiro: é o que interessa auditar.
    itens.sort(key=lambda i: (not i.get("tem_duplicata"), i.get("inep") or 0))

    total = len(itens)
    inicio = (page - 1) * limit
    return jsonify({
        "total": total,
        "com_duplicata": sum(1 for i in itens if i.get("tem_duplicata")),
        "page": page,
        "limit": limit,
        "analisados": itens[inicio:inicio + limit],
    })


@app.route("/api/historico/<run_id>/zip", methods=["GET"])
def download_historico_zip(run_id):
    """Monta (ou reaproveita) o ZIP com as imagens duplicadas, uma pasta por grupo."""
    zip_path = os.path.join(fase_engine.caminho_run(run_id), "imagens_duplicadas.zip")
    try:
        if not os.path.exists(zip_path):
            if fase_engine.montar_zip_imagens(run_id) is None:
                return jsonify({"error": "Execução sem imagens duplicadas."}), 404
    except Exception as e:
        return jsonify({"error": f"Falha ao montar o ZIP: {e}"}), 500

    return send_file(zip_path, as_attachment=True,
                     download_name=f"Imagens_Duplicadas_{run_id}.zip",
                     mimetype="application/zip")


# --------------------------------------------------------------------------- #
# Painel de Duplicatas — visão por par de escolas (página /duplicatas)
# --------------------------------------------------------------------------- #

import pares_duplicatas

# Montar os pares relê grupos.json inteiro; a tela chama a cada mudança de filtro,
# então o resultado fica em cache por execução até a marcação de falso positivo mudar.
_pares_cache = {}
_pares_lock = threading.Lock()


def _obter_pares(run_id, invalidar=False):
    with _pares_lock:
        if invalidar:
            _pares_cache.pop(run_id, None)
        if run_id in _pares_cache:
            return _pares_cache[run_id]

    meta, pares = pares_duplicatas.montar_pares(run_id)
    if meta is None:
        return None, None

    with _pares_lock:
        _pares_cache[run_id] = (meta, pares)
    return meta, pares


@app.route("/duplicatas")
def painel_duplicatas():
    return send_from_directory("static", "duplicatas.html")


@app.route("/api/painel/execucoes", methods=["GET"])
def painel_execucoes():
    """Execuções concluídas disponíveis para abrir no painel."""
    try:
        runs = [r for r in fase_engine.listar_runs() if r.get("status") == "concluido"]
        return jsonify({"runs": runs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/painel/<run_id>", methods=["GET"])
def painel_pares(run_id):
    """Pares INEP A × INEP B com filtros, métricas e opções dos seletores."""
    meta, pares = _obter_pares(run_id)
    if meta is None:
        return jsonify({"error": "Execução não encontrada no histórico."}), 404

    filtros = {k: request.args.get(k, "") for k in (
        "busca", "contem", "nao_contem", "uf", "municipio", "fornecedor",
        "tipo", "falso_positivo", "min_imagens", "somente_fotos", "mesmo_municipio",
        "gravidade")}

    filtrados = pares_duplicatas.aplicar_filtros(pares, filtros)

    page = max(1, int(request.args.get("page", 1)))
    limit = min(200, max(1, int(request.args.get("limit", 25))))
    inicio = (page - 1) * limit

    return jsonify(sanitize_json({
        "run": {k: v for k, v in meta.items() if k != "grupos"},
        # Resumo do recorte filtrado, e o total geral para mostrar o quanto foi filtrado
        "resumo": pares_duplicatas.resumo(filtrados),
        "resumo_geral": pares_duplicatas.resumo(pares),
        "opcoes": pares_duplicatas.opcoes_filtro(pares),
        "total": len(filtrados),
        "page": page,
        "limit": limit,
        "pares": filtrados[inicio:inicio + limit],
    }))


@app.route("/api/painel/<run_id>/falso-positivo", methods=["POST"])
def painel_marcar_falso_positivo(run_id):
    """Liga/desliga o falso positivo de um par (persiste em disco)."""
    data = request.get_json(silent=True) or {}
    par_id = (data.get("par_id") or "").strip()
    if not par_id:
        return jsonify({"error": "par_id é obrigatório."}), 400

    try:
        marcas = pares_duplicatas.marcar(run_id, par_id, bool(data.get("falso_positivo")))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    _obter_pares(run_id, invalidar=True)
    return jsonify({"par_id": par_id,
                    "falso_positivo": bool(data.get("falso_positivo")),
                    "total_marcados": len(marcas)})


@app.route("/api/painel/<run_id>/csv", methods=["GET"])
def painel_csv(run_id):
    """Exporta o recorte filtrado em CSV (uma linha por par)."""
    import csv
    import io as _io

    meta, pares = _obter_pares(run_id)
    if meta is None:
        return jsonify({"error": "Execução não encontrada."}), 404

    filtros = {k: request.args.get(k, "") for k in (
        "busca", "contem", "nao_contem", "uf", "municipio", "fornecedor",
        "tipo", "falso_positivo", "min_imagens", "somente_fotos", "mesmo_municipio",
        "gravidade")}
    filtrados = pares_duplicatas.aplicar_filtros(pares, filtros)

    buf = _io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["INEP A", "Escola A", "UF A", "Municipio A",
                "INEP B", "Escola B", "UF B", "Municipio B",
                "Fornecedor", "Imagens", "Exatas", "Fotos de camera",
                "Mesmo municipio", "Mesmo PDF nos dois", "PDF de outra escola",
                "Falso positivo", "Grupos", "PDF A", "PDF B"])
    for p in filtrados:
        a, b = p["escola_a"], p["escola_b"]
        w.writerow([
            a["inep"], a["escola"], a["uf"], a["municipio"],
            b["inep"], b["escola"], b["uf"], b["municipio"],
            " / ".join(p["fornecedores"]), p["qtd_imagens"], p["qtd_exatas"],
            p["qtd_fotos_camera"], "sim" if p["mesmo_municipio"] else "nao",
            "sim" if p.get("mesmo_pdf") else "nao",
            "sim" if p.get("pdf_divergente") else "nao",
            "sim" if p["falso_positivo"] else "nao", " ".join(p["grupos"]),
            " | ".join(x["pdf_filename"] or "" for x in p["pdfs_a"]),
            " | ".join(x["pdf_filename"] or "" for x in p["pdfs_b"]),
        ])

    resp = app.response_class(
        "﻿" + buf.getvalue(),           # BOM para o Excel abrir com acentos certos
        mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = f'attachment; filename="Duplicatas_{run_id}.csv"'
    return resp


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
