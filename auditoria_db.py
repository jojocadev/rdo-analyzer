"""
Persistencia da auditoria no Supabase.

O disco continua gravando (serve de cache de leitura e de rede de seguranca
quando o Supabase esta fora do ar), mas o Supabase e a fonte da verdade: e o
unico lugar que sobrevive a um redeploy da VPS, e o unico que guarda o par por
inteiro em vez de um ponteiro para `historico/<run_id>/grupos.json`.

DOIS BACKENDS, escolhidos automaticamente:

    tabelas   Preferido. auditoria_envios, auditoria_envio_pares,
              auditoria_documentos e auditoria_eventos. Permite consultar por
              INEP, por fornecedor, cruzar com escolas_conectadas. Criado por
              sql/auditoria.sql — DDL nao passa pelo PostgREST, entao o SQL
              precisa ser executado no painel do Supabase.

    storage   Usado enquanto as tabelas nao existirem. Grava os mesmos registros
              como objetos JSON no bucket `auditoria-docs`, que a service_role
              cria e escreve sem DDL nenhum. Guarda tudo e nao perde nada; so
              nao da para consultar por SQL.

    Ao subir, a aplicacao testa as tabelas; se estiverem la, usa tabelas, senao
    cai para storage. Quem chama nao precisa saber qual esta ativo. Rodar o SQL
    depois promove o backend sozinho, e migrar_auditoria.py leva para as tabelas
    o que estiver no storage.

Quando nem um nem outro responde, a operacao NAO e dada como concluida em
silencio: o chamador recebe o erro e o registro entra em `auditoria/_pendentes/`
para ser reenviado. Perder movimentacao e o unico desfecho inaceitavel aqui.
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

TABELAS_BACKEND = "tabelas"
STORAGE_BACKEND = "storage"

_estado = {"checado_em": 0, "ok": False, "motivo": "", "backend": ""}
_VALIDADE_CHECAGEM = 30  # segundos


def _tabelas_existem():
    """
    True quando as quatro tabelas estao no schema.

    Testa com `limit=0`, que nao traz linha nenhuma, e so trata PGRST205 (tabela
    ausente) como "nao existe". Qualquer outro erro e problema de rede ou de
    permissao, e cair para o Storage nesse caso mascararia a causa real.
    """
    for tabela in TABELAS:
        try:
            _chamar(f"{SUPABASE_URL}/{tabela}?select=*&limit=0", timeout=20)
        except BancoIndisponivel as e:
            if "PGRST205" in str(e) or "HTTP 404" in str(e):
                return False
            raise
    return True


def disponivel(forcar=False):
    """
    Diz se da para gravar agora, por qualquer um dos backends. O resultado fica
    em cache por 30 s para a tela nao ir ao Supabase a cada clique.
    """
    agora = time.time()
    if not forcar and agora - _estado["checado_em"] < _VALIDADE_CHECAGEM:
        return _estado["ok"]

    _estado["checado_em"] = agora
    if not SERVICE_KEY:
        _estado.update(ok=False, backend="",
                       motivo="SUPABASE_SERVICE_KEY não está definida.")
        return False

    try:
        if _tabelas_existem():
            _estado.update(ok=True, backend=TABELAS_BACKEND, motivo="")
            return True
    except BancoIndisponivel as e:
        # Tabelas existem mas o Supabase recusou. Nao mascarar com o Storage:
        # a causa precisa aparecer na tela.
        _estado.update(ok=False, backend="", motivo=str(e))
        return False

    # Sem as tabelas, o Storage sustenta tudo sozinho — e a service_role cria o
    # bucket sem precisar de DDL.
    if garantir_bucket():
        _estado.update(ok=True, backend=STORAGE_BACKEND, motivo=(
            "Gravando no Storage do Supabase. Para consultar por SQL, rode "
            "sql/auditoria.sql e depois migrar_auditoria.py."))
        return True

    _estado.update(ok=False, backend="", motivo=(
        "O Supabase não respondeu: nem as tabelas da auditoria nem o Storage "
        "estão acessíveis."))
    return False


def backend():
    """'tabelas', 'storage' ou '' — qual esta ativo agora."""
    disponivel()
    return _estado["backend"]


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
    """Grava no backend ativo. `envio_completo` e a forma neutra do reenvio."""
    if tipo == "envio_completo":
        if backend() == STORAGE_BACKEND:
            _storage_salvar_envio(carga["envio"], carga["pares"])
        else:
            _gravar("envio", [carga["cabecalho"]])
            if carga.get("linhas"):
                _gravar("pares", carga["linhas"])
        return None

    if backend() == STORAGE_BACKEND:
        if tipo == "evento":
            for linha in carga:
                _storage_registrar_evento(linha)
            return None
        # Documento pendente: o arquivo ja subiu ou vai subir junto do metadado;
        # aqui so o indice precisa ser refeito.
        if tipo == "documento":
            for linha in carga:
                indice = _ler_json(_obj_documentos(linha["envio_id"])) or []
                indice = [d for d in indice
                          if d.get("doc_id") != linha["doc_id"]] + [linha]
                _gravar_json(_obj_documentos(linha["envio_id"]), indice)
            return None

    tabela = _TABELA_DO_TIPO[tipo]
    url = f"{SUPABASE_URL}/{tabela}"
    extra = {"Prefer": "return=minimal"}
    if tipo in _CONFLITO:
        url += f"?on_conflict={_CONFLITO[tipo]}"
        extra["Prefer"] = "resolution=merge-duplicates,return=minimal"
    return _chamar(url, data=carga, method="POST", extra=extra)


# --------------------------------------------------------------------------- #
# Backend de Storage
#
# Mesmos registros, gravados como objetos JSON no bucket. Layout:
#
#   registro/envios/<envio_id>.json        cabecalho + copia integral dos pares
#   registro/documentos/<envio_id>.json    indice dos documentos daquele envio
#   registro/eventos/<data>/<ts>_<tipo>.json   trilha, um objeto por evento
#   <envio_id>/<inep>/<arquivo>            os arquivos de resolucao
#
# Cada evento vira um objeto proprio, nunca reescrito: e o que garante que a
# movimentacao nao se perca nem quando o envio e removido depois.
# --------------------------------------------------------------------------- #

_PREFIXO = "registro"


def _obj_envio(envio_id):
    return f"{_PREFIXO}/envios/{envio_id}.json"


def _obj_documentos(envio_id):
    return f"{_PREFIXO}/documentos/{envio_id}.json"


def _ler_json(caminho):
    bruto = baixar_arquivo(caminho)
    if not bruto:
        return None
    try:
        return json.loads(bruto.decode("utf-8"))
    except Exception:
        return None


def _gravar_json(caminho, dado):
    subir_arquivo(caminho, json.dumps(dado, ensure_ascii=False).encode("utf-8"),
                  "application/json")


def _listar_objetos(prefixo):
    """Nomes dos objetos sob um prefixo. O list do Storage pagina de 100."""
    nomes, offset = [], 0
    while True:
        lote = _chamar(f"{_STORAGE}/object/list/{BUCKET}", method="POST",
                       data={"prefix": prefixo, "limit": 100, "offset": offset})
        if not lote:
            break
        nomes += [i["name"] for i in lote if i.get("name")]
        if len(lote) < 100:
            break
        offset += 100
    return nomes


def _storage_salvar_envio(envio, pares):
    registro = {k: v for k, v in envio.items()
                if k not in ("pares", "aviso", "gravado_no_banco")}
    registro["pares"] = pares
    _gravar_json(_obj_envio(envio["envio_id"]), registro)


def _storage_listar_envios():
    nomes = _listar_objetos(f"{_PREFIXO}/envios")
    envios = []
    for nome in nomes:
        if not nome.endswith(".json"):
            continue
        dado = _ler_json(f"{_PREFIXO}/envios/{nome}")
        if isinstance(dado, dict) and dado.get("envio_id"):
            dado.pop("pares", None)      # a lista nao precisa das evidencias
            envios.append(dado)
    envios.sort(key=lambda e: str(e.get("criado_em") or ""), reverse=True)
    return envios


def _storage_carregar_envio(envio_id):
    dado = _ler_json(_obj_envio(envio_id))
    if isinstance(dado, dict):
        dado.pop("pares", None)
        return dado
    return None


def _storage_pares_do_envio(envio_id):
    dado = _ler_json(_obj_envio(envio_id))
    return (dado or {}).get("pares") or []


def _storage_salvar_documento(doc, conteudo):
    # O arquivo em si fica no disco da VPS, por decisao de projeto: encher o
    # Supabase com PDF de parecer nao compensa. Aqui vai so o metadado, que
    # inclui tamanho e sha256 — da para provar o que foi anexado mesmo se o
    # arquivo se perder.
    indice = _ler_json(_obj_documentos(doc["envio_id"])) or []
    limpo = {k: v for k, v in doc.items() if k not in ("aviso", "gravado_no_banco")}
    indice = [d for d in indice if d.get("doc_id") != doc["doc_id"]] + [limpo]
    _gravar_json(_obj_documentos(doc["envio_id"]), indice)


def _storage_listar_documentos(envio_id):
    return _ler_json(_obj_documentos(envio_id)) or []


def _storage_excluir_documento(envio_id, doc_id):
    indice = _ler_json(_obj_documentos(envio_id)) or []
    _gravar_json(_obj_documentos(envio_id),
                 [d for d in indice if d.get("doc_id") != doc_id])


def _storage_excluir_envio(envio_id):
    _chamar(f"{_STORAGE}/object/{BUCKET}", method="DELETE",
            data={"prefixes": [_obj_envio(envio_id)]})


def _storage_registrar_evento(linha):
    carimbo = time.strftime("%Y%m%d_%H%M%S")
    chave = (f"{_PREFIXO}/eventos/{time.strftime('%Y%m%d')}/"
             f"{carimbo}_{linha['tipo']}_{int(time.time() * 1000) % 100000}.json")
    _gravar_json(chave, dict(linha, criado_em=time.strftime("%Y-%m-%d %H:%M:%S")))


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
        if backend() == STORAGE_BACKEND:
            _storage_salvar_envio(envio, pares)
        else:
            _gravar("envio", [cabecalho])
            if linhas:
                _gravar("pares", linhas)
        registrar_evento("envio_criado", envio_id=envio["envio_id"],
                         detalhe={"pares": len(linhas),
                                  "ineps": envio.get("resumo", {}).get("ineps", [])})
        return True
    except BancoIndisponivel:
        # Um item so, com cabecalho e pares juntos: o reenvio precisa dos dois
        # para montar tanto a linha da tabela quanto o objeto do Storage.
        _guardar_pendente("envio_completo",
                          {"envio": envio, "pares": pares, "cabecalho": cabecalho,
                           "linhas": linhas})
        raise


def registrar_evento(tipo, envio_id=None, par_id=None, doc_id=None,
                     inep=None, detalhe=None):
    """Trilha append-only. Nunca levanta: um evento perdido nao pode derrubar
    a operacao que ele descreve — mas fica pendente para reenvio."""
    linha = {"tipo": tipo, "envio_id": envio_id, "par_id": par_id,
             "doc_id": doc_id, "inep": inep, "detalhe": detalhe or {}}
    try:
        if backend() == STORAGE_BACKEND:
            _storage_registrar_evento(linha)
        else:
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
        # Onde o arquivo mora de fato. Fica explicito na linha para quem
        # consultar o banco nao achar que ele esta no Supabase.
        "bucket": "disco-vps",
        "sha256": doc.get("sha256"),
        "tamanho_bytes": doc.get("tamanho_bytes"),
        "tipo_mime": doc.get("tipo_mime"),
        "observacao": doc.get("observacao"),
        "enviado_por": doc.get("enviado_por"),
        "criado_em": doc.get("criado_em"),
    }
    try:
        if backend() == STORAGE_BACKEND:
            _storage_salvar_documento(doc, conteudo)
        else:
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
    if backend() == STORAGE_BACKEND:
        return _storage_listar_envios()
    dados = _chamar(f"{SUPABASE_URL}/auditoria_envios"
                    f"?select=*&order=criado_em.desc&limit=500")
    return dados or []


def carregar_envio(envio_id):
    if backend() == STORAGE_BACKEND:
        return _storage_carregar_envio(envio_id)
    alvo = urllib.parse.quote(envio_id)
    dados = _chamar(f"{SUPABASE_URL}/auditoria_envios?select=*&envio_id=eq.{alvo}&limit=1")
    return (dados or [None])[0]


def pares_do_envio(envio_id):
    """Pares reconstruidos da copia — nao toca no historico em disco."""
    if backend() == STORAGE_BACKEND:
        return _storage_pares_do_envio(envio_id)
    alvo = urllib.parse.quote(envio_id)
    linhas = _chamar(f"{SUPABASE_URL}/auditoria_envio_pares"
                     f"?select=snapshot&envio_id=eq.{alvo}&order=id&limit=5000")
    return [l["snapshot"] for l in (linhas or []) if l.get("snapshot")]


def listar_documentos(envio_id):
    if backend() == STORAGE_BACKEND:
        return _storage_listar_documentos(envio_id)
    alvo = urllib.parse.quote(envio_id)
    linhas = _chamar(f"{SUPABASE_URL}/auditoria_documentos"
                     f"?select=*&envio_id=eq.{alvo}&order=criado_em.desc&limit=1000")
    return linhas or []


def excluir_documento(envio_id, doc_id):
    if backend() == STORAGE_BACKEND:
        _storage_excluir_documento(envio_id, doc_id)
    else:
        alvo = urllib.parse.quote(doc_id)
        _chamar(f"{SUPABASE_URL}/auditoria_documentos?doc_id=eq.{alvo}",
                method="DELETE", extra={"Prefer": "return=minimal"})
    registrar_evento("documento_removido", envio_id=envio_id, doc_id=doc_id)
    return True


def excluir_envio(envio_id):
    """Remove o envio; a trilha de eventos permanece, por ser o registro de que
    ele existiu."""
    registrar_evento("envio_removido", envio_id=envio_id)
    if backend() == STORAGE_BACKEND:
        _storage_excluir_envio(envio_id)
    else:
        alvo = urllib.parse.quote(envio_id)
        _chamar(f"{SUPABASE_URL}/auditoria_envios?envio_id=eq.{alvo}",
                method="DELETE", extra={"Prefer": "return=minimal"})
    return True
