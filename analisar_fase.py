"""
Extracao de imagens duplicadas nos PDFs (RDO) das escolas de uma FASE.

Este modulo e o motor usado pela aba "Analise por Fase" da interface web
(endpoints /api/scan-fase e /api/historico em app.py) e tambem funciona pela
linha de comando.

Como funciona:
    1. Busca no Supabase todas as escolas da fase escolhida (e, opcionalmente,
       de um fornecedor especifico).
    2. Baixa cada PDF em paralelo e extrai as imagens embutidas.
    3. Compara as imagens por SHA-256 (identicas) e por pHash (visualmente
       iguais) e agrupa as que aparecem em escolas (INEPs) diferentes do
       MESMO fornecedor (ver --qualquer-fornecedor para relaxar o criterio).
    4. Salva a execucao no historico em disco e grava o resultado no Supabase.

Uso pela linha de comando:
    python analisar_fase.py                        # fase 5, todos os fornecedores
    python analisar_fase.py --fase 4.1             # outra fase
    python analisar_fase.py --fornecedor BRISANET  # recorte por fornecedor
    python analisar_fase.py --amostra 30           # teste rapido com 30 escolas
    python analisar_fase.py --workers 48           # ajusta o paralelismo
    python analisar_fase.py --sem-supabase         # nao grava nas tabelas
    python analisar_fase.py --so-analise           # so reanalisa o cache local
    python analisar_fase.py --zip                  # gera tambem o ZIP das imagens
    python analisar_fase.py --qualquer-fornecedor  # nao exige mesmo fornecedor
    python analisar_fase.py --com-phash            # inclui as visualmente parecidas
    python analisar_fase.py --com-templates        # nao descarta logos/templates

Saidas:
    historico/<run_id>/meta.json          metricas da execucao
    historico/<run_id>/grupos.json        grupos de duplicatas (usado pela tela)
    historico/<run_id>/analisados.jsonl   todos os PDFs auditados na execucao
    historico/<run_id>/imagens_duplicadas.zip   gerado sob demanda
    cache_extracao/<fase>/                cache retomavel dos downloads
    extracted_images/                     miniaturas servidas pela interface
"""

import os

# Trava as bibliotecas nativas em 1 thread ANTES de importar o numpy.
#
# O pHash usa DCT do numpy, e o OpenBLAS abre um pool de 16 threads por thread
# chamadora. Com 32 workers baixando PDFs em paralelo, isso vira até 512 threads
# nativas, cada uma com seu buffer — foi o que esgotou o commit da máquina e
# derrubou o servidor com "OpenBLAS error: Memory allocation still failed".
#
# Não há perda de desempenho: as DCTs do pHash são 32x32 (thread única é mais
# rápida que coordenar 16), e a comparação em bloco de pHashes é feita com
# operações bit a bit elemento por elemento, que não passam pelo BLAS.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_var, "1")

import io
import sys
import json
import time
import shutil
import hashlib
import argparse
import threading
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import fitz  # PyMuPDF
import imagehash
import numpy as np
from PIL import Image

from config import SUPABASE_URL, SERVICE_KEY, exigir_credenciais  # noqa: E402
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "extracted_images")

MIN_WIDTH = 120
MIN_HEIGHT = 120
PHASH_THRESHOLD = 8
LOGO_SHA = "90cb2766e81912dd996d8387e2406a32e333836543dd88fc1845aa188e5bdce4"
THUMB_MAX = 512  # miniatura/pHash nao precisam da foto em resolucao plena

# Prefixo SHA[:16] -> nome da miniatura ja gerada em extracted_images/
_CACHED_PREFIXES = {}

_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


# --------------------------------------------------------------------------- #
# Supabase
# --------------------------------------------------------------------------- #

def _sb_request(url, data=None, method="GET", extra_headers=None):
    # Sem a chave, o Supabase responde 401 seco e a tela mostra "Unauthorized"
    # sem dizer o motivo. Falhar aqui deixa claro que falta configurar o ambiente.
    exigir_credenciais()
    headers = {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        headers=headers,
        method=method,
    )
    return urllib.request.urlopen(req, timeout=90)


def fetch_fases():
    """Lista as fases existentes com a contagem de escolas de cada uma."""
    contagem = defaultdict(int)
    offset = 0
    while True:
        url = f"{SUPABASE_URL}/escolas_conectadas?select=fase&limit=1000&offset={offset}"
        with _sb_request(url) as resp:
            batch = json.loads(resp.read().decode())
        if not batch:
            break
        for row in batch:
            if row.get("fase"):
                contagem[str(row["fase"])] += 1
        if len(batch) < 1000:
            break
        offset += 1000
    return sorted(({"fase": f, "escolas": n} for f, n in contagem.items()),
                  key=lambda x: x["escolas"], reverse=True)


def fetch_schools_by_fase(fase, fornecedor=None):
    """Busca todas as escolas de uma fase, paginando de 1000 em 1000."""
    schools = []
    offset = 0
    fase_enc = urllib.parse.quote(str(fase))
    filtro_forn = ""
    if fornecedor and fornecedor.strip():
        filtro_forn = "&fornecedor=ilike." + urllib.parse.quote(f"*{fornecedor.strip()}*")
    while True:
        url = (
            f"{SUPABASE_URL}/escolas_conectadas"
            f"?select=escola_id_bubble,inep,uf,fornecedor,tipo_fornecedor,fase,books"
            f"&fase=eq.{fase_enc}{filtro_forn}&order=inep.asc&limit=1000&offset={offset}"
        )
        with _sb_request(url) as resp:
            batch = json.loads(resp.read().decode())
        if not batch:
            break
        schools.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return schools


def save_to_supabase(table, records, batch_size=500):
    """Insere registros em lotes. Retorna quantos foram gravados."""
    inserted = 0
    for b in range(0, len(records), batch_size):
        batch = records[b:b + batch_size]
        try:
            with _sb_request(f"{SUPABASE_URL}/{table}", data=batch, method="POST"):
                inserted += len(batch)
        except Exception as e:
            log(f"    [Supabase] Falha ao gravar lote em {table}: {e}")
    return inserted


# --------------------------------------------------------------------------- #
# Extração
# --------------------------------------------------------------------------- #

# Memo global SHA-256 -> (phash, largura, altura, thumb). A mesma foto reaparece em
# dezenas de RDOs; sem isso, cada ocorrencia pagaria decode + pHash de novo.
_memo = {}
_memo_lock = threading.Lock()
_thumb_lock = threading.Lock()


# Campos cujo valor se repete muito entre registros: a URL e o nome do PDF repetem
# por imagem do mesmo arquivo (~9 em média), fornecedor e UF por milhares de escolas.
# Internar essas strings corta ~24% da memória do cache (541 MB -> 410 MB em 264 mil
# registros), o que importa porque a lista inteira fica em RAM durante a varredura.
_CAMPOS_REPETIDOS = ("pdf_url", "pdf_filename", "fornecedor", "uf", "fase",
                     "thumb_url", "phash", "sha256")


def _internar(rec):
    for k in _CAMPOS_REPETIDOS:
        v = rec.get(k)
        if type(v) is str:
            rec[k] = sys.intern(v)
    return rec


def load_phash_memo(path):
    """Carrega o memo persistido de execucoes anteriores."""
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                _memo[r["s"]] = (r["p"], r["w"], r["h"], r["t"])
            except Exception:
                continue
    return len(_memo)


def index_existing_thumbs():
    """
    Indexa as miniaturas já presentes em extracted_images/ por prefixo SHA[:16].

    O sistema já acumulou centenas de milhares de miniaturas em execuções anteriores;
    reaproveitá-las evita gravar o mesmo JPEG de novo com outro sufixo de página.
    """
    try:
        nomes = os.listdir(CACHE_DIR)
    except Exception:
        return 0
    for nome in nomes:
        if nome.lower().endswith(".jpg"):
            _CACHED_PREFIXES.setdefault(nome.split("_")[0][:16], nome)
    return len(_CACHED_PREFIXES)


def _decode_for_hash(image_bytes):
    """
    Abre a imagem e devolve (pil_reduzida, largura_real, altura_real).

    O pHash so precisa de 32x32, entao usamos o modo draft do JPEG para decodificar
    direto em escala reduzida (1/2, 1/4, 1/8) - varias vezes mais rapido que decodificar
    a foto inteira. As dimensoes reportadas continuam sendo as originais.
    """
    pil = Image.open(io.BytesIO(image_bytes))
    width, height = pil.size
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return None, width, height
    try:
        pil.draft("RGB", (THUMB_MAX, THUMB_MAX))
    except Exception:
        pass
    pil = pil if pil.mode in ("RGB", "L") else pil.convert("RGB")
    pil.load()
    return pil, width, height


def process_single_pdf(task):
    """
    Extrai as imagens validas de UM PDF.

    A unidade de trabalho e o PDF, nao a escola: os RDOs vao de ~30 KB a ~25 MB, e
    agrupar por escola deixaria uma thread presa nos arquivos gigantes enquanto as
    outras ficam ociosas.
    """
    school, clean_url = task
    inep = school.get("inep")
    uf = school.get("uf")
    fornecedor = school.get("fornecedor") or "Nao informado"
    fase = school.get("fase")
    pdf_filename = urllib.parse.unquote(clean_url.split("/")[-1].split("?")[0])

    try:
        req = urllib.request.Request(clean_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            pdf_bytes = resp.read()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return clean_url, []

    images = []
    try:
        # Um mesmo objeto de imagem (xref) costuma ser reaproveitado em varias paginas
        # do RDO: um relatorio com 570 aparicoes tem, na pratica, ~22 imagens distintas.
        # Como extract_image() responde por ~97% do custo de CPU, ele e chamado uma
        # unica vez por xref e as paginas onde a imagem aparece ficam registradas juntas.
        xref_paginas = {}
        for page_index in range(len(doc)):
            for img_info in doc[page_index].get_images(full=True):
                xref_paginas.setdefault(img_info[0], []).append(page_index + 1)

        for xref, paginas in xref_paginas.items():
            try:
                image_bytes = doc.extract_image(xref)["image"]
                sha256 = hashlib.sha256(image_bytes).hexdigest()
                if sha256 == LOGO_SHA:
                    continue

                with _memo_lock:
                    hit = _memo.get(sha256)

                if hit is not None:
                    if hit[0] is None:
                        continue  # ja avaliada e descartada (pequena ou cor uniforme)
                    phash_val, width, height, thumb_filename = hit
                else:
                    pil, width, height = _decode_for_hash(image_bytes)
                    descartar = pil is None
                    if not descartar:
                        extrema = pil.getextrema()
                        # Cor totalmente uniforme = fundo/placeholder, nao e foto
                        if extrema and all(r[0] == r[1] for r in extrema):
                            descartar = True

                    if descartar:
                        with _memo_lock:
                            _memo[sha256] = (None, width, height, None)
                        continue

                    phash_val = str(imagehash.phash(pil))
                    thumb_filename = f"{sha256[:16]}_{paginas[0]}_{xref}.jpg"
                    thumb_path = os.path.join(CACHE_DIR, thumb_filename)
                    if not os.path.exists(thumb_path):
                        # Reaproveita qualquer miniatura ja gerada para o mesmo SHA
                        with _thumb_lock:
                            existente = _CACHED_PREFIXES.get(sha256[:16])
                        if existente:
                            thumb_filename = existente
                        else:
                            try:
                                pil.save(thumb_path, "JPEG", quality=85)
                            except Exception:
                                pass
                            with _thumb_lock:
                                _CACHED_PREFIXES[sha256[:16]] = thumb_filename

                    with _memo_lock:
                        _memo[sha256] = (phash_val, width, height, thumb_filename)

                images.append({
                    "inep": int(inep) if inep else None,
                    "uf": uf,
                    "fornecedor": fornecedor,
                    "fase": str(fase) if fase else None,
                    "pdf_filename": pdf_filename,
                    "pdf_url": clean_url,
                    "pagina": paginas[0],
                    "paginas": paginas,
                    "ocorrencias_no_pdf": len(paginas),
                    "width": width,
                    "height": height,
                    "sha256": sha256,
                    "phash": phash_val,
                    "thumb_url": thumb_filename,
                })
            except Exception:
                continue
    finally:
        doc.close()

    return clean_url, images


def build_tasks(schools):
    """Achata as escolas em uma lista de tarefas (escola, url_do_pdf)."""
    tasks = []
    for school in schools:
        books = school.get("books") or []
        if isinstance(books, str):
            try:
                books = json.loads(books)
            except Exception:
                books = [books]
        for pdf_url in books:
            if pdf_url and isinstance(pdf_url, str):
                tasks.append((school, "https:" + pdf_url if pdf_url.startswith("//") else pdf_url))
    return tasks


def extract_all(schools, out_dir, workers, progress=None, cancelado=None):
    """Extrai imagens de todos os PDFs, com cache retomavel por PDF."""
    cache_path = os.path.join(out_dir, "cache_imagens.jsonl")
    memo_path = os.path.join(out_dir, "phash_memo.jsonl")

    n_memo = load_phash_memo(memo_path)
    if n_memo:
        log(f"    [memo] {n_memo} SHA-256 ja com pHash calculado (decode sera evitado).")

    n_thumbs = index_existing_thumbs()
    log(f"    [cache] {n_thumbs} miniaturas ja em disco reaproveitaveis.")

    todas_tasks = build_tasks(schools)
    urls_selecionadas = {t[1] for t in todas_tasks}

    done_urls = set()
    records = []
    fora_do_recorte = 0
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("_pdf_ok"):
                    if rec["_pdf_ok"] in urls_selecionadas:
                        done_urls.add(rec["_pdf_ok"])
                elif not rec.get("_escola_concluida"):
                    # O cache é compartilhado pela fase inteira; um recorte menor
                    # (amostra ou fornecedor) só pode considerar os PDFs que pediu.
                    if rec.get("pdf_url") in urls_selecionadas:
                        records.append(_internar(rec))
                    else:
                        fora_do_recorte += 1
        log(f"    [cache] {len(records)} imagens de {len(done_urls)} PDFs ja extraidos"
            + (f" ({fora_do_recorte} imagens do cache sao de fora deste recorte)."
               if fora_do_recorte else "."))

    tasks = [t for t in todas_tasks if t[1] not in done_urls]
    if not tasks:
        log("    [cache] Nada pendente - extracao ja estava completa.")
        return records, cache_path

    log(f"    PDFs pendentes: {len(tasks)} (workers={workers})")

    t0 = time.time()
    completed = 0
    write_lock = threading.Lock()
    cache_fh = open(cache_path, "a", encoding="utf-8")
    memo_fh = open(memo_path, "a", encoding="utf-8")
    memo_gravado = set(_memo.keys())

    # Passo de atualização proporcional ao volume: uma amostra de 170 PDFs precisa
    # avisar a cada poucos itens, uma varredura de 28 mil não precisa a cada um.
    passo = max(1, min(100, len(tasks) // 60))

    interrompido = False
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(process_single_pdf, t) for t in tasks]
            for future in as_completed(futures):
                completed += 1

                if cancelado and cancelado() and not interrompido:
                    # Cancela o que ainda não começou; o que já baixou continua no cache,
                    # então uma nova execução retoma de onde parou.
                    interrompido = True
                    for f in futures:
                        f.cancel()
                    log("    [cancelado] Interrompendo a extração a pedido do usuário.")

                try:
                    url, imgs = future.result()
                except Exception:
                    url, imgs = None, []

                with write_lock:
                    for rec in imgs:
                        cache_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        sha = rec["sha256"]
                        if sha not in memo_gravado:
                            memo_gravado.add(sha)
                            memo_fh.write(json.dumps({
                                "s": sha, "p": rec["phash"], "w": rec["width"],
                                "h": rec["height"], "t": rec["thumb_url"]}) + "\n")
                    if url:
                        cache_fh.write(json.dumps({"_pdf_ok": url}) + "\n")
                    if completed % 200 == 0:
                        cache_fh.flush()
                        memo_fh.flush()
                records.extend(imgs)

                if completed % passo == 0 or completed == len(tasks):
                    elapsed = time.time() - t0
                    rate = completed / max(elapsed, 0.01)
                    eta = (len(tasks) - completed) / max(rate, 0.01)
                    if progress:
                        progress(completed, len(tasks), len(records), eta / 60)
                    if completed % 500 == 0 or completed == len(tasks):
                        log(f"    [{completed}/{len(tasks)}] {completed / len(tasks) * 100:5.1f}% | "
                            f"{len(records)} imagens | {elapsed / 60:.1f} min | {rate:.1f} pdf/s | "
                            f"ETA {eta / 60:.1f} min")
    finally:
        cache_fh.flush(); cache_fh.close()
        memo_fh.flush(); memo_fh.close()

    return records, cache_path


# --------------------------------------------------------------------------- #
# Detecção de duplicatas
# --------------------------------------------------------------------------- #

def nomes_fornecedor(fornecedor):
    """
    Separa o campo fornecedor nos nomes individuais que ele contém.

    O cadastro traz o RI e o RE no mesmo campo, como
    "STEIN TELECOM (RI) / STEIN TELECOM FILIAL PR (RE)". Para decidir se duas
    escolas são "do mesmo fornecedor" é preciso comparar os nomes individuais, e
    não a string inteira — senão duas escolas atendidas pela mesma empresa na
    rede interna deixariam de casar só porque o RE é diferente.
    """
    if not fornecedor:
        return []
    if " (RI)" in fornecedor or " (RE)" in fornecedor:
        nomes = []
        for parte in fornecedor.split("/"):
            nome = parte.strip().replace(" (RI)", "").replace(" (RE)", "").strip()
            if nome:
                nomes.append(nome)
        return nomes
    nome = fornecedor.strip()
    return [nome] if nome else []


def chave_registro(rec):
    """Identidade de uma imagem dentro de um PDF de uma escola."""
    return (rec.get("inep"), rec.get("pdf_url"), rec.get("sha256"))


def _e_template_do_sistema(membros):
    """
    Diz se uma imagem é template/logo do sistema, e não foto reaproveitada.

    Uma foto que um fornecedor reusou aparece apenas em escolas DELE — pode haver
    vários nomes no campo (o RE varia de escola para escola), mas o nome do
    fornecedor responsável está em todas. Já o logo da EACE e a página "Aviso de
    desativação de escola" aparecem em escolas de fornecedores completamente
    distintos, sem nenhum nome em comum.

    Logo: se existe um nome de fornecedor presente em TODAS as escolas que contêm a
    imagem, é reaproveitamento; se nenhum nome cobre todas, é template do sistema.
    Testar a interseção (e não a contagem de nomes) evita descartar por engano uma
    reutilização real só porque as escolas têm parceiros de rede externa diferentes.

    Usada apenas quando mesmo_fornecedor=False: exigir fornecedor em comum já aplica
    este mesmo teste.
    """
    por_escola = defaultdict(set)
    for m in membros:
        if m.get("inep"):
            por_escola[m["inep"]].update(nomes_fornecedor(m.get("fornecedor")))
    if len(por_escola) < 2:
        return False
    comuns = set.intersection(*por_escola.values())
    return not comuns


def find_duplicate_groups(records, mesmo_fornecedor=True, somente_identicas=True,
                          ignorar_templates=True):
    """
    Agrupa as imagens em GRUPOS de duplicatas entre escolas (INEPs) diferentes.

    Um grupo reúne todas as ocorrências de uma mesma imagem (SHA-256 idêntico) e
    também imagens visualmente iguais (pHash com distância <= PHASH_THRESHOLD),
    ligadas transitivamente por union-find. Sai um grupo por imagem, creditando
    todos os fornecedores corresponsáveis.

    :param mesmo_fornecedor: quando True (padrão), a duplicata só é apontada se os
        INEPs diferentes pertencerem ao MESMO fornecedor — é o caso de auditoria que
        interessa, o fornecedor reaproveitando a mesma foto em escolas distintas.
        Um grupo é emitido por fornecedor implicado. Quando False, basta haver dois
        INEPs diferentes, sem olhar o fornecedor.
    :param somente_identicas: quando True (padrão), só agrupa imagens byte-a-byte
        idênticas (SHA-256) e não roda a comparação perceptual. O pHash encadeia
        transitivamente: 1.206 heatmaps distintos do UniFi viram um único "grupo" de
        754 escolas só porque a interface é igual. Ligue o pHash para pegar recortes
        e recompressões, ciente desse efeito.
    :param ignorar_templates: descarta imagens que são template do sistema (logo da
        EACE, página "Aviso de desativação"). Só tem efeito quando
        mesmo_fornecedor=False — exigir fornecedor em comum já descarta os templates,
        que por definição atravessam fornecedores. Ver _e_template_do_sistema.
    """
    sha_map = defaultdict(list)
    for rec in records:
        sha_map[rec["sha256"]].append(rec)

    shas = list(sha_map.keys())
    n = len(shas)
    log(f"    Imagens: {len(records)} | SHA-256 únicos: {n}")

    # Union-find sobre os SHAs únicos
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    # Une SHAs visualmente semelhantes. Processa em blocos para não estourar a RAM
    # (a matriz completa de 180k x 180k uint64 passaria de 250 GB).
    if n >= 2 and not somente_identicas:
        phashes = np.array([int(sha_map[s][0]["phash"], 16) for s in shas], dtype=np.uint64)
        block = 4000
        for i0 in range(0, n, block):
            i1 = min(i0 + block, n)
            block_i = phashes[i0:i1]
            for j0 in range(i0, n, block):
                j1 = min(j0 + block, n)
                dist = _hamming_matrix_cross(block_i, phashes[j0:j1])
                ii, jj = np.nonzero(dist <= PHASH_THRESHOLD)
                for a, b in zip(ii + i0, jj + j0):
                    if a < b:
                        union(int(a), int(b))
            log(f"      pHash: {i1}/{n} hashes comparados...")

    # Monta os grupos
    clusters = defaultdict(list)
    for idx, sha in enumerate(shas):
        clusters[find(idx)].extend(sha_map[sha])

    def montar_grupo(membros, fornecedor_alvo):
        ineps = {m["inep"] for m in membros if m["inep"]}
        membros = sorted(membros, key=lambda m: (m["inep"] or 0, m["pdf_filename"], m["pagina"]))
        distinct_shas = {m["sha256"] for m in membros}
        return {
            "grupo_id": "",
            "tipo": "Exata (100%)" if len(distinct_shas) == 1 else "Exata + Visual",
            "fornecedor": fornecedor_alvo or "",
            "ineps": sorted(ineps),
            "qtd_ineps": len(ineps),
            "qtd_ocorrencias": len(membros),
            "qtd_shas": len(distinct_shas),
            "fornecedores": sorted({m["fornecedor"] for m in membros if m["fornecedor"]}),
            "ufs": sorted({m["uf"] for m in membros if m["uf"]}),
            "membros": membros,
        }

    groups = []
    descartados_template = 0
    descartados_fornecedor = 0
    for members in clusters.values():
        # Nomes de fornecedor de cada escola que contém a imagem
        por_escola = defaultdict(set)
        for m in members:
            if m.get("inep"):
                por_escola[m["inep"]].update(nomes_fornecedor(m.get("fornecedor")))

        if len(por_escola) < 2:
            continue  # repetição dentro da mesma escola não é duplicata entre escolas

        comuns = set.intersection(*por_escola.values())

        if mesmo_fornecedor:
            # A interseção resolve os dois casos de uma vez: se existe fornecedor
            # presente em TODAS as escolas, é reaproveitamento dele; se nenhum cobre
            # todas, as escolas são de fornecedores distintos — o que também descreve
            # os templates do sistema (logo da EACE, "Aviso de desativação").
            if not comuns:
                descartados_fornecedor += 1
                continue
            # Um único grupo por imagem, creditando todos os corresponsáveis, em vez
            # de um grupo por nome: RI e RE da mesma empresa geravam dois achados
            # idênticos para as mesmas escolas.
            groups.append(montar_grupo(members, " / ".join(sorted(comuns))))
        else:
            if ignorar_templates and not comuns:
                descartados_template += 1
                continue
            groups.append(montar_grupo(members, " / ".join(sorted(comuns))))

    if descartados_fornecedor:
        log(f"    {descartados_fornecedor} imagens descartadas: as escolas nao tem "
            f"fornecedor em comum (inclui os templates do sistema).")
    if descartados_template:
        log(f"    {descartados_template} imagens descartadas por serem template do sistema.")

    groups.sort(key=lambda g: (g["qtd_ineps"], g["qtd_ocorrencias"]), reverse=True)
    for i, g in enumerate(groups, 1):
        g["grupo_id"] = f"G{i:05d}"
    return groups


def _hamming_matrix_cross(a, b):
    """Distância de Hamming entre dois blocos de pHashes."""
    arr = np.bitwise_xor.outer(a, b)
    arr = arr - ((arr >> np.uint64(1)) & np.uint64(0x5555555555555555))
    arr = (arr & np.uint64(0x3333333333333333)) + ((arr >> np.uint64(2)) & np.uint64(0x3333333333333333))
    arr = (arr + (arr >> np.uint64(4))) & np.uint64(0x0F0F0F0F0F0F0F0F)
    return (arr * np.uint64(0x0101010101010101)) >> np.uint64(56)


def groups_to_pairs(groups):
    """
    Converte grupos em pares (inep_a, inep_b) no formato da tabela duplicatas_rdo.

    Os pares são encadeados (escola 1 x 2, 2 x 3, 3 x 4, ...), não combinatórios.
    Um grupo com k escolas gera k-1 linhas em vez de k(k-1)/2: na fase 5 completa
    isso é a diferença entre ~28 mil linhas e 6,1 milhões — que além de inviável
    de gravar, não acrescenta informação, já que basta agrupar por `sha256` para
    recuperar todas as escolas envolvidas.
    """
    pairs = []
    for g in groups:
        by_inep = {}
        for m in g["membros"]:
            by_inep.setdefault(m["inep"], m)  # 1 representante por escola
        reps = sorted(by_inep.values(), key=lambda m: m["inep"] or 0)
        for i in range(len(reps) - 1):
            a, b = reps[i], reps[i + 1]
            if a["sha256"] == b["sha256"]:
                dist, sim, tipo = 0, 100.0, "Exata (100%)"
            else:
                dist = int(bin(int(a["phash"], 16) ^ int(b["phash"], 16)).count("1"))
                sim = round(max(0.0, (1 - dist / 64) * 100), 1)
                tipo = "Visual (Perceptual)"
            pairs.append({
                "grupo_id": g["grupo_id"],
                "inep_a": a["inep"], "uf_a": a["uf"], "fornecedor_a": a["fornecedor"],
                "pdf_filename_a": a["pdf_filename"], "pagina_a": a["pagina"],
                "inep_b": b["inep"], "uf_b": b["uf"], "fornecedor_b": b["fornecedor"],
                "pdf_filename_b": b["pdf_filename"], "pagina_b": b["pagina"],
                "tipo_duplicata": tipo, "similaridade": sim, "distancia_hamming": dist,
                "sha256": a["sha256"],
                "thumb_url_a": a["thumb_url"], "thumb_url_b": b["thumb_url"],
            })
    return pairs


# --------------------------------------------------------------------------- #
# Historico de execucoes
# --------------------------------------------------------------------------- #

HISTORICO_DIR = os.path.join(BASE_DIR, "historico")


def novo_run_id(fase, fornecedor=None):
    """Identificador estavel e legivel para uma execucao."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    alvo = "todos" if not fornecedor else "".join(
        c if c.isalnum() else "_" for c in fornecedor)[:24].strip("_").lower()
    return f"fase{str(fase).replace('.', '_')}_{alvo}_{stamp}"


def caminho_run(run_id):
    return os.path.join(HISTORICO_DIR, run_id)


def salvar_run(run_id, meta, groups=None):
    """
    Grava (ou atualiza) uma execucao no historico em disco.

    O historico fica em arquivos, nao em memoria: reiniciar o servidor nao apaga
    nenhum resultado ja produzido.
    """
    run_dir = caminho_run(run_id)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    if groups is not None:
        with open(os.path.join(run_dir, "grupos.json"), "w", encoding="utf-8") as fh:
            json.dump(groups, fh, ensure_ascii=False)
    return run_dir


def salvar_analisados(run_id, analisados):
    """
    Grava a lista de PDFs/imagens analisados desta execucao (um JSON por linha).

    E o mesmo conteudo da aba "Todos os PDFs Analisados", mas escopado a execucao,
    para que o historico mostre exatamente o que foi auditado naquele momento.
    """
    run_dir = caminho_run(run_id)
    os.makedirs(run_dir, exist_ok=True)
    caminho = os.path.join(run_dir, "analisados.jsonl")
    with open(caminho, "w", encoding="utf-8") as fh:
        for a in analisados:
            fh.write(json.dumps(a, ensure_ascii=False) + "\n")
    return caminho


def ler_analisados(run_id):
    """Le a lista de PDFs analisados de uma execucao do historico."""
    caminho = os.path.join(caminho_run(run_id), "analisados.jsonl")
    if not os.path.exists(caminho):
        return []
    itens = []
    with open(caminho, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    itens.append(json.loads(line))
                except Exception:
                    continue
    return itens


def carregar_run(run_id, com_grupos=True):
    run_dir = caminho_run(run_id)
    meta_path = os.path.join(run_dir, "meta.json")
    if not os.path.exists(meta_path):
        return None
    with open(meta_path, "r", encoding="utf-8") as fh:
        meta = json.load(fh)
    if com_grupos:
        grupos_path = os.path.join(run_dir, "grupos.json")
        if os.path.exists(grupos_path):
            with open(grupos_path, "r", encoding="utf-8") as fh:
                meta["grupos"] = json.load(fh)
    return meta


def listar_runs():
    """Lista as execucoes do historico, da mais recente para a mais antiga."""
    if not os.path.isdir(HISTORICO_DIR):
        return []
    runs = []
    for run_id in os.listdir(HISTORICO_DIR):
        meta = carregar_run(run_id, com_grupos=False)
        if meta:
            runs.append(meta)
    runs.sort(key=lambda m: m.get("iniciado_em", ""), reverse=True)
    return runs


def excluir_run(run_id):
    run_dir = caminho_run(run_id)
    if os.path.isdir(run_dir):
        shutil.rmtree(run_dir, ignore_errors=True)
        return True
    return False


# --------------------------------------------------------------------------- #
# Motor (usado pela interface web e pela linha de comando)
# --------------------------------------------------------------------------- #

def run_analysis(fase="5", fornecedor=None, workers=32, amostra=0, so_analise=False,
                 gravar_supabase=True, tabela_analisados="rdo_analisados",
                 tabela_duplicatas="duplicatas_rdo", progress=None, run_id=None,
                 cancelado=None, mesmo_fornecedor=True, somente_identicas=True,
                 ignorar_templates=True):
    """
    Executa a analise completa de uma fase e registra o resultado no historico.

    :param progress: callback progress(pct, mensagem, etapa) para a barra da interface.
    :param cancelado: callable que devolve True se o usuario pediu cancelamento.
    :param mesmo_fornecedor: aponta duplicata so entre INEPs diferentes do mesmo
        fornecedor (padrao). Ver find_duplicate_groups, que documenta tambem
        somente_identicas e ignorar_templates.
    :return: dict com o meta da execucao (metricas, caminhos e grupos).
    """
    def prog(pct, msg, etapa=""):
        log(f"    {msg}")
        if progress:
            progress(round(pct, 1), msg, etapa)

    run_id = run_id or novo_run_id(fase, fornecedor)
    t_start = time.time()
    # O cache de download e por fase+fornecedor, entao execucoes repetidas do mesmo
    # recorte reaproveitam tudo o que ja foi baixado.
    out_dir = os.path.join(BASE_DIR, "cache_extracao", run_id.rsplit("_", 2)[0])
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(HISTORICO_DIR, exist_ok=True)

    meta = {
        "run_id": run_id,
        "fase": str(fase),
        "fornecedor": fornecedor or "",
        "status": "processando",
        "iniciado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        "concluido_em": "",
        "duracao_min": 0,
        "metricas": {},
        "erro": "",
    }
    salvar_run(run_id, meta)

    try:
        # 1. Escolas
        prog(2, f"Buscando escolas da fase {fase} no Supabase...", "escolas")
        schools = fetch_schools_by_fase(fase, fornecedor)
        if not schools:
            raise RuntimeError(f"Nenhuma escola encontrada para fase '{fase}'"
                               + (f" e fornecedor '{fornecedor}'" if fornecedor else ""))
        if amostra:
            schools = schools[:amostra]
        total_pdfs = sum(len(s.get("books") or []) for s in schools)
        prog(5, f"{len(schools)} escolas e {total_pdfs} PDFs a processar.", "escolas")

        # 2. Extracao
        if so_analise:
            records = ler_cache_imagens(os.path.join(out_dir, "cache_imagens.jsonl"))
            prog(70, f"{len(records)} imagens lidas do cache local.", "extracao")
        else:
            def prog_extracao(feitos, total, imagens, eta_min=0):
                restante = f" - restam ~{eta_min:.0f} min" if eta_min >= 1 else ""
                prog(5 + (feitos / max(total, 1)) * 70,
                     f"Analisando PDFs: {feitos} de {total} "
                     f"({imagens:,} imagens extraidas){restante}".replace(",", "."),
                     "extracao")

            records, _ = extract_all(schools, out_dir, workers,
                                     progress=prog_extracao, cancelado=cancelado)

        if cancelado and cancelado():
            raise RuntimeError("Analise cancelada pelo usuario.")
        if not records:
            raise RuntimeError("Nenhuma imagem valida foi extraida dos PDFs.")

        # 3. Duplicatas
        prog(78, f"Comparando {len(records)} imagens (SHA-256 + pHash)...", "duplicatas")
        groups = find_duplicate_groups(records, mesmo_fornecedor=mesmo_fornecedor,
                                       somente_identicas=somente_identicas,
                                       ignorar_templates=ignorar_templates)
        pairs = groups_to_pairs(groups)
        criterio = " + ".join([
            "INEPs diferentes do mesmo fornecedor" if mesmo_fornecedor
            else "INEPs diferentes (qualquer fornecedor)",
            "somente identicas (SHA-256)" if somente_identicas else "identicas + visuais (pHash)",
        ] + (["sem templates do sistema"] if ignorar_templates else []))
        prog(88, f"{len(groups)} grupos de duplicatas ({criterio}).", "duplicatas")

        # 4. Relatorio: a lista de PDFs analisados desta execucao, no mesmo formato
        # da aba "Todos os PDFs Analisados".
        prog(90, "Montando a lista de PDFs analisados...", "relatorio")
        run_dir = caminho_run(run_id)
        os.makedirs(run_dir, exist_ok=True)

        # tem_duplicata e por ocorrencia (escola + PDF + imagem), nao apenas pelo SHA:
        # o mesmo SHA pode ser duplicata num fornecedor e ocorrencia unica em outro.
        chaves_dup = {chave_registro(m) for g in groups for m in g["membros"]}
        grupos_por_chave = defaultdict(list)
        # Com QUAIS outras escolas cada imagem casou. Sem isso a tabela mostra várias
        # linhas do mesmo INEP marcadas "Duplicada" e parece estar apontando a própria
        # escola, quando o par está em outro INEP.
        # Guarda o PDF e a página da contraparte, e não só o número do INEP, para que a
        # tela possa abrir direto o RDO da outra escola.
        pares_por_chave = defaultdict(dict)
        for g in groups:
            por_inep = {}
            for m in g["membros"]:
                por_inep.setdefault(m.get("inep"), m)
            for m in g["membros"]:
                chave = chave_registro(m)
                grupos_por_chave[chave].append(g["grupo_id"])
                for inep, outro in por_inep.items():
                    if inep and inep != m.get("inep"):
                        pares_por_chave[chave][inep] = {
                            "inep": inep,
                            "uf": outro.get("uf"),
                            "pagina": outro.get("pagina"),
                            "pdf_filename": outro.get("pdf_filename"),
                            "pdf_url": outro.get("pdf_url"),
                            "thumb_url": outro.get("thumb_url"),
                        }

        analisados = [{
            "inep": r["inep"], "uf": r["uf"], "fornecedor": r["fornecedor"],
            "fase": r.get("fase"),
            "pdf_filename": r["pdf_filename"], "pdf_url": r["pdf_url"],
            "pagina": r["pagina"], "sha256": r["sha256"], "thumb_url": r["thumb_url"],
            "width": r.get("width"), "height": r.get("height"),
            "tem_duplicata": chave_registro(r) in chaves_dup,
            "grupos": sorted(set(grupos_por_chave.get(chave_registro(r), []))),
            "duplicada_com": sorted(pares_por_chave.get(chave_registro(r), {}).values(),
                                    key=lambda x: x["inep"]),
        } for r in records]

        salvar_analisados(run_id, analisados)

        # 5. Supabase
        if gravar_supabase:
            prog(94, f"Gravando resultado em {tabela_analisados} e {tabela_duplicatas}...", "supabase")
            registros_sb = [{k: v for k, v in a.items()
                             if k in ("inep", "uf", "fornecedor", "pdf_filename", "pdf_url",
                                      "pagina", "sha256", "thumb_url", "tem_duplicata")}
                            for a in analisados]
            n1 = save_to_supabase(tabela_analisados, registros_sb)
            dup_records = [{k: v for k, v in p.items()
                            if not k.startswith("_") and k != "grupo_id"} for p in pairs]
            n2 = save_to_supabase(tabela_duplicatas, dup_records)
            prog(98, f"Supabase: {n1} analisados e {n2} pares gravados.", "supabase")
        else:
            n1 = n2 = 0

        # Metricas finais
        escolas_afetadas = {m["inep"] for g in groups for m in g["membros"] if m["inep"]}
        fornecedores_afetados = ({g["fornecedor"] for g in groups if g.get("fornecedor")}
                                 if mesmo_fornecedor
                                 else {m["fornecedor"] for g in groups for m in g["membros"]})
        meta["criterio"] = criterio
        meta["mesmo_fornecedor"] = bool(mesmo_fornecedor)
        meta["somente_identicas"] = bool(somente_identicas)
        meta["ignorar_templates"] = bool(ignorar_templates)
        meta["metricas"] = {
            "escolas_selecionadas": len(schools),
            "pdfs_selecionados": total_pdfs,
            "pdfs_com_imagem": len({r["pdf_url"] for r in records}),
            "imagens_extraidas": len(records),
            "imagens_unicas": len({r["sha256"] for r in records}),
            "grupos_duplicatas": len(groups),
            # Conta as linhas marcadas na tabela de analisados, para que o número da
            # métrica seja exatamente o que o usuário vê ao filtrar "Com Duplicata".
            "imagens_duplicadas": sum(1 for a in analisados if a["tem_duplicata"]),
            "escolas_afetadas": len(escolas_afetadas),
            "pares": len(pairs),
            "pares_exatos": sum(1 for p in pairs if p["similaridade"] == 100.0),
            "pares_visuais": sum(1 for p in pairs if p["similaridade"] < 100.0),
            "fornecedores_afetados": len(fornecedores_afetados),
            "supabase_analisados": n1,
            "supabase_pares": n2,
        }
        meta["status"] = "concluido"
        meta["concluido_em"] = time.strftime("%Y-%m-%d %H:%M:%S")
        meta["duracao_min"] = round((time.time() - t_start) / 60, 1)
        salvar_run(run_id, meta, groups)
        meta["grupos"] = groups
        prog(100, f"Concluido em {meta['duracao_min']} min - {len(groups)} grupos encontrados.", "fim")
        return meta

    except Exception as e:
        meta["status"] = "erro"
        meta["erro"] = str(e)
        meta["concluido_em"] = time.strftime("%Y-%m-%d %H:%M:%S")
        meta["duracao_min"] = round((time.time() - t_start) / 60, 1)
        salvar_run(run_id, meta)
        log(f"    [ERRO] {e}")
        if progress:
            progress(100, f"Erro: {e}", "erro")
        raise


def ler_cache_imagens(cache_path):
    records = []
    if not os.path.exists(cache_path):
        return records
    with open(cache_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not rec.get("_pdf_ok") and not rec.get("_escola_concluida"):
                records.append(_internar(rec))
    return records


def montar_zip_imagens(run_id, destino=None):
    """Monta sob demanda um ZIP com as imagens duplicadas, uma pasta por grupo."""
    import zipfile
    meta = carregar_run(run_id)
    if not meta or not meta.get("grupos"):
        return None
    destino = destino or os.path.join(caminho_run(run_id), "imagens_duplicadas.zip")
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for g in meta["grupos"]:
            for m in g["membros"]:
                src = os.path.join(CACHE_DIR, m["thumb_url"])
                if not os.path.exists(src):
                    continue
                safe = "".join(c if c.isalnum() or c in " -_." else "_"
                               for c in m["pdf_filename"])[:70]
                zf.write(src, f"{g['grupo_id']}/INEP{m['inep']}_{m['uf']}_p{m['pagina']}_{safe}.jpg")
    return destino


# --------------------------------------------------------------------------- #
# Linha de comando
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="Extrai imagens duplicadas dos PDFs de uma fase.")
    ap.add_argument("--fase", default="5", help="Fase a analisar (padrao: 5)")
    ap.add_argument("--fornecedor", default=None, help="Filtra por fornecedor (padrao: todos)")
    ap.add_argument("--amostra", type=int, default=0, help="Analisa apenas N escolas (teste)")
    ap.add_argument("--workers", type=int, default=32, help="Downloads simultaneos (padrao: 32)")
    ap.add_argument("--sem-supabase", action="store_true", help="Nao gravar nas tabelas do Supabase")
    ap.add_argument("--qualquer-fornecedor", action="store_true",
                    help="Aponta duplicata entre INEPs diferentes mesmo de fornecedores distintos")
    ap.add_argument("--com-phash", action="store_true",
                    help="Inclui as visualmente parecidas (pHash), nao so as identicas")
    ap.add_argument("--com-templates", action="store_true",
                    help="Nao descarta logos e paginas de template do sistema")
    ap.add_argument("--so-analise", action="store_true", help="Nao baixar nada, so reanalisar o cache")
    ap.add_argument("--zip", action="store_true", help="Gerar tambem o ZIP das imagens duplicadas")
    ap.add_argument("--tabela-analisados", default="rdo_analisados")
    ap.add_argument("--tabela-duplicatas", default="duplicatas_rdo")
    args = ap.parse_args()

    log("=" * 70)
    log(f">>> EXTRACAO DE IMAGENS DUPLICADAS - FASE {args.fase}")
    log("=" * 70)

    try:
        meta = run_analysis(
            fase=args.fase, fornecedor=args.fornecedor, workers=args.workers,
            amostra=args.amostra, so_analise=args.so_analise,
            gravar_supabase=not args.sem_supabase,
            tabela_analisados=args.tabela_analisados,
            tabela_duplicatas=args.tabela_duplicatas,
            mesmo_fornecedor=not args.qualquer_fornecedor,
            somente_identicas=not args.com_phash,
            ignorar_templates=not args.com_templates,
        )
    except Exception:
        return 1

    m = meta["metricas"]
    log("\n" + "=" * 70)
    log(f">>> CONCLUIDO em {meta['duracao_min']} min")
    for k, v in m.items():
        log(f"    {k:24s} {v}")
    log(f"    Criterio:  {meta.get('criterio')}")
    log(f"    Analisados: {os.path.join(caminho_run(meta['run_id']), 'analisados.jsonl')}")
    if args.zip:
        z = montar_zip_imagens(meta["run_id"])
        log(f"    ZIP:       {z}")
    log("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
