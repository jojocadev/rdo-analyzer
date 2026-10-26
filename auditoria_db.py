"""
Persistencia da auditoria no Supabase.

O disco continua gravando (serve de cache de leitura e de rede de seguranca
quando o banco esta fora do ar), mas o banco e a fonte da verdade: e o unico
lugar que sobrevive a um redeploy da VPS, e o unico que guarda o par por inteiro
em vez de um ponteiro para `historico/<run_id>/grupos.json`.

Quando a gravacao no banco falha, a operacao NAO e dada como concluida em
silencio: o chamador recebe o erro e o registro entra em `auditoria/_pendentes/`
para ser reenviado depois. Perder movimentacao e o unico desfecho inaceitavel
aqui.

As tabelas sao criadas por sql/auditoria.sql — nao da para faze-lo por aqui,
porque o PostgREST nao executa DDL.
"""

import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from config import SUPABASE_URL, SERVICE_KEY

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIR_PENDENTES = os.path.join(BASE_DIR, "auditoria", "_pendentes")

BUCKET = "auditoria-docs"
_STORAGE = SUPABASE_URL.replace("/rest/v1", "") + "/storage/v1"

TABELAS = ("auditoria_envios", "auditoria_envio_pares",
           "auditoria_documentos", "auditoria_eventos")


class BancoIndisponivel(RuntimeError):
    """O banco nao respondeu, ou as tabelas ainda nao existem."""


def _cabecalhos(extra=None, json_body=True):
    h = {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"}
    if json_body:
        h["Content-Type"] = "application/json"
    if extra:
        h.update(extra)
    return h


def _chamar(url, data=None, method="GET", extra=None, binario=None, timeout=60):
    corpo = binario if binario is not None else (
        json.dumps(data).encode("utf-8") if data is not None else None)
    req = urllib.request.Request(
        url, data=corpo, method=method,
        headers=_cabecalhos(extra, json_body=binario is None))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            bruto = r.read().decode("utf-8") or "null"
            return json.loads(bruto) if bruto.strip().startswith(("{", "[")) else bruto
    except urllib.error.HTTPError as e:
        detalhe = ""
        try:
            detalhe = e.read().decode("utf-8")[:400]
        except Exception:
            pass
        raise BancoIndisponivel(f"HTTP {e.code} em {url.split('?')[0]}: {detalhe}") from e
    except Exception as e:
        raise BancoIndisponivel(f"{type(e).__name__}: {e}") from e


# --------------------------------------------------------------------------- #
# Disponibilidade
# --------------------------------------------------------------------------- #

_estado = {"checado_em": 0, "ok": False, "motivo": ""}
_VALIDADE_CHECAGEM = 30  # segundos


def disponivel(forcar=False):
    """
    Diz se da para gravar no banco agora. O resultado fica em cache por 30 s
    para a tela nao disparar uma ida ao Supabase a cada clique.
    """
    agora = time.time()
    if not forcar and agora - _estado["checado_em"] < _VALIDADE_CHECAGEM:
        return _estado["ok"]

    _estado["checado_em"] = agora
    if not SERVICE_KEY:
        _estado.update(ok=False, motivo="SUPABASE_SERVICE_KEY não está definida.")
        return False
    try:
        for tabela in TABELAS:
            _chamar(f"{SUPABASE_URL}/{tabela}?select=1&limit=1", timeout=20)
        _estado.update(ok=True, motivo="")
    except BancoIndisponivel as e:
        falta = "PGRST205" in str(e)
        _estado.update(ok=False, motivo=(
            "As tabelas da auditoria ainda não existem. Rode sql/auditoria.sql "
            "no SQL Editor do Supabase." if falta else str(e)))
    return _estado["ok"]


def motivo_indisponivel():
    return _estado["motivo"]


# --------------------------------------------------------------------------- #
# Fila de pendentes
# --------------------------------------------------------------------------- #

def _guardar_pendente(tipo, carga):
    """Guarda em disco o que nao entrou no banco, para reenvio posterior."""
    os.makedirs(DIR_PENDENTES, exist_ok=True)
    nome = f"{time.strftime('%Y%m%d_%H%M%S')}_{tipo}_{int(time.time() * 1000) % 100000}.json"
    with io.open(os.path.join(DIR_PENDENTES, nome), "w", encoding="utf-8") as fh:
        json.dump({"tipo": tipo, "carga": carga, "criado_em": time.time()},
                  fh, ensure_ascii=False)
    return nome


def listar_pendentes():
    if not os.path.isdir(DIR_PENDENTES):
        return []
    return sorted(n for n in os.listdir(DIR_PENDENTES) if n.endswith(".json"))


def reenviar_pendentes():
    """Tenta regravar o que ficou para tras. Devolve (enviados, restantes)."""
    if not disponivel(forcar=True):
        return 0, len(listar_pendentes())

    enviados = 0
    for nome in listar_pendentes():
        caminho = os.path.join(DIR_PENDENTES, nome)
        try:
            with io.open(caminho, encoding="utf-8") as fh:
                item = json.load(fh)
            _gravar(item["tipo"], item["carga"])
            os.remove(caminho)
            enviados += 1
        except Exception:
            continue
    return enviados, len(listar_pendentes())


_TABELA_DO_TIPO = {
    "envio": "auditoria_envios",
    "pares": "auditoria_envio_pares",
    "documento": "auditoria_documentos",
    "evento": "auditoria_eventos",
}

# `pares` e `evento` nao tem chave natural unica que o upsert possa usar sem
# risco de apagar historico, entao vao como insert simples; o resto faz merge.
_CONFLITO = {
    "envio": "envio_id",
    "pares": "envio_id,par_id",
    "documento": "doc_id",
}


def _gravar(tipo, carga):
    tabela = _TABELA_DO_TIPO[tipo]
    url = f"{SUPABASE_URL}/{tabela}"
    extra = {"Prefer": "return=minimal"}
    if tipo in _CONFLITO:
        url += f"?on_conflict={_CONFLITO[tipo]}"
        extra["Prefer"] = "resolution=merge-duplicates,return=minimal"
    return _chamar(url, data=carga, method="POST", extra=extra)


# --------------------------------------------------------------------------- #
# Escrita
# --------------------------------------------------------------------------- #

def _campo(par, lado, chave):
    return (par.get(f"escola_{lado}") or {}).get(chave)


def _linha_do_par(envio_id, par):
    """Colunas consultaveis + o par inteiro, para nao depender do disco."""
    return {
        "envio_id": envio_id,
        "par_id": par.get("par_id"),
        "inep_a": _campo(par, "a", "inep"),
        "inep_b": _campo(par, "b", "inep"),
        "uf_a": _campo(par, "a", "uf"),
        "uf_b": _campo(par, "b", "uf"),
        "municipio_a": _campo(par, "a", "municipio"),
        "municipio_b": _campo(par, "b", "municipio"),
        "escola_a": _campo(par, "a", "escola"),
        "escola_b": _campo(par, "b", "escola"),
        "fornecedores": par.get("fornecedores") or [],
        "qtd_imagens": par.get("qtd_imagens"),
        "qtd_fotos": par.get("qtd_fotos_camera"),
        "mesmo_pdf": bool(par.get("mesmo_pdf")),
        "pdf_divergente": bool(par.get("pdf_divergente")),
        "fora_do_raio": par.get("fora_do_raio") or 0,
        "distancia_max_m": par.get("distancia_max_m"),
        "snapshot": par,
    }


def salvar_envio(envio, pares):
    """
    Grava o envio e a copia integral dos pares.

    Levanta BancoIndisponivel se nao conseguir — e enfileira para reenvio, para
    que a movimentacao nao se perca nem quando o Supabase esta fora.
    """
    cabecalho = {
        "envio_id": envio["envio_id"],
        "run_id": envio["run_id"],
        "fase": envio.get("fase") or None,
        "criado_em": envio.get("criado_em"),
        "autor": envio.get("autor") or None,
        "observacao": envio.get("observacao") or None,
        "status": envio.get("status") or "enviado",
        "resumo": envio.get("resumo") or {},
    }
    linhas = [_linha_do_par(envio["envio_id"], p) for p in pares]

    try:
        _gravar("envio", [cabecalho])
        if linhas:
            _gravar("pares", linhas)
        registrar_evento("envio_criado", envio_id=envio["envio_id"],
                         detalhe={"pares": len(linhas),
                                  "ineps": envio.get("resumo", {}).get("ineps", [])})
        return True
    except BancoIndisponivel:
        _guardar_pendente("envio", [cabecalho])
        if linhas:
            _guardar_pendente("pares", linhas)
        raise


def registrar_evento(tipo, envio_id=None, par_id=None, doc_id=None,
                     inep=None, detalhe=None):
    """Trilha append-only. Nunca levanta: um evento perdido nao pode derrubar
    a operacao que ele descreve — mas fica pendente para reenvio."""
    linha = {"tipo": tipo, "envio_id": envio_id, "par_id": par_id,
             "doc_id": doc_id, "inep": inep, "detalhe": detalhe or {}}
    try:
        _gravar("evento", [linha])
        return True
    except BancoIndisponivel:
        _guardar_pendente("evento", [linha])
        return False


# --------------------------------------------------------------------------- #
# Storage dos documentos
# --------------------------------------------------------------------------- #

def garantir_bucket():
    """Cria o bucket privado dos documentos se ainda nao existir."""
    try:
        existentes = _chamar(f"{_STORAGE}/bucket", timeout=30)
        if any(b.get("name") == BUCKET for b in existentes or []):
            return True
        _chamar(f"{_STORAGE}/bucket", method="POST",
                data={"name": BUCKET, "id": BUCKET, "public": False})
        return True
    except BancoIndisponivel:
        return False


def subir_arquivo(caminho_no_bucket, conteudo, tipo_mime):
    """Sobe o arquivo para o Storage. `upsert` para reenvio nao duplicar."""
    garantir_bucket()
    url = f"{_STORAGE}/object/{BUCKET}/{urllib.parse.quote(caminho_no_bucket)}"
    _chamar(url, method="POST", binario=conteudo, timeout=180,
            extra={"Content-Type": tipo_mime or "application/octet-stream",
                   "x-upsert": "true"})
    return caminho_no_bucket


def baixar_arquivo(caminho_no_bucket):
    """Conteudo do arquivo, ou None se nao estiver la."""
    url = f"{_STORAGE}/object/{BUCKET}/{urllib.parse.quote(caminho_no_bucket)}"
    req = urllib.request.Request(url, headers=_cabecalhos(json_body=False))
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.read()
    except Exception:
        return None


def salvar_documento(doc, conteudo):
    """Sobe o arquivo e grava o metadado. Enfileira e levanta em caso de falha."""
    linha = {
        "doc_id": doc["doc_id"],
        "envio_id": doc["envio_id"],
        "inep": doc["inep"],
        "par_id": doc.get("par_id"),
        "arquivo_nome": doc["arquivo_nome"],
        "arquivo_path": doc["arquivo_path"],
        "bucket": BUCKET,
        "tamanho_bytes": doc.get("tamanho_bytes"),
        "tipo_mime": doc.get("tipo_mime"),
        "observacao": doc.get("observacao"),
        "enviado_por": doc.get("enviado_por"),
        "criado_em": doc.get("criado_em"),
    }
    try:
        subir_arquivo(doc["arquivo_path"], conteudo, doc.get("tipo_mime"))
        _gravar("documento", [linha])
        registrar_evento("documento_anexado", envio_id=doc["envio_id"],
                         doc_id=doc["doc_id"], inep=doc["inep"],
                         par_id=doc.get("par_id"),
                         detalhe={"arquivo": doc["arquivo_nome"],
                                  "bytes": doc.get("tamanho_bytes")})
        return True
    except BancoIndisponivel:
        _guardar_pendente("documento", [linha])
        raise


# --------------------------------------------------------------------------- #
# Leitura
# --------------------------------------------------------------------------- #

def listar_envios():
    dados = _chamar(f"{SUPABASE_URL}/auditoria_envios"
                    f"?select=*&order=criado_em.desc&limit=500")
    return dados or []


def carregar_envio(envio_id):
    alvo = urllib.parse.quote(envio_id)
    dados = _chamar(f"{SUPABASE_URL}/auditoria_envios?select=*&envio_id=eq.{alvo}&limit=1")
    return (dados or [None])[0]


def pares_do_envio(envio_id):
    """Pares reconstruidos do snapshot — nao toca no historico em disco."""
    alvo = urllib.parse.quote(envio_id)
    linhas = _chamar(f"{SUPABASE_URL}/auditoria_envio_pares"
                     f"?select=snapshot&envio_id=eq.{alvo}&order=id&limit=5000")
    return [l["snapshot"] for l in (linhas or []) if l.get("snapshot")]


def listar_documentos(envio_id):
    alvo = urllib.parse.quote(envio_id)
    linhas = _chamar(f"{SUPABASE_URL}/auditoria_documentos"
                     f"?select=*&envio_id=eq.{alvo}&order=criado_em.desc&limit=1000")
    return linhas or []


def excluir_documento(envio_id, doc_id):
    alvo = urllib.parse.quote(doc_id)
    _chamar(f"{SUPABASE_URL}/auditoria_documentos?doc_id=eq.{alvo}", method="DELETE",
            extra={"Prefer": "return=minimal"})
    registrar_evento("documento_removido", envio_id=envio_id, doc_id=doc_id)
    return True


def excluir_envio(envio_id):
    """Remove o envio; a trilha de eventos permanece, por ser o registro de que
    ele existiu."""
    alvo = urllib.parse.quote(envio_id)
    registrar_evento("envio_removido", envio_id=envio_id)
    _chamar(f"{SUPABASE_URL}/auditoria_envios?envio_id=eq.{alvo}", method="DELETE",
            extra={"Prefer": "return=minimal"})
    return True
