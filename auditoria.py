"""
Envios para auditoria: lotes de pares encaminhados a partir do Painel de Duplicatas.

Cada envio guarda uma CÓPIA INTEGRAL dos pares encaminhados, não apenas os
par_ids. A versão anterior guardava só a seleção e relia as evidências de
`historico/<run_id>/grupos.json`; como essa pasta não vai para o git, a VPS
subiu sem ela e a auditoria abria vazia. Além disso, refazer a varredura mudava
o que um envio antigo mostrava — o registro de fiscalização não pode se mexer
depois de feito.

Onde fica:
    Supabase  — fonte da verdade (auditoria_envios, auditoria_envio_pares,
                auditoria_documentos, auditoria_eventos + bucket auditoria-docs)
    disco     — cache de leitura e rede de segurança quando o banco está fora

Quando o banco falha, a operação não é dada como concluída em silêncio: o
chamador recebe um aviso e o registro entra na fila de reenvio.
"""

import hashlib
import io
import json
import os
import time
import urllib.parse

import analisar_fase as motor
import auditoria_db
import pares_duplicatas

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIR_AUDITORIA = os.path.join(BASE_DIR, "auditoria")


def _caminho(envio_id):
    return os.path.join(DIR_AUDITORIA, f"{envio_id}.json")


def novo_id():
    return "aud_" + time.strftime("%Y%m%d_%H%M%S")


def criar_envio(run_id, par_ids, observacao="", autor=""):
    """
    Registra um lote de pares encaminhados para auditoria.

    Guarda tambem um resumo (escolas, imagens, fornecedores) para a lista do
    histórico não precisar remontar os pares só para mostrar os números.
    """
    par_ids = [p for p in dict.fromkeys(par_ids) if p]   # remove repetidos, mantém ordem
    if not par_ids:
        raise ValueError("Nenhum par selecionado.")

    meta, todos = pares_duplicatas.montar_pares(run_id)
    if meta is None:
        raise ValueError(f"Execução '{run_id}' não encontrada no histórico.")

    escolhidos = [p for p in todos if p["par_id"] in set(par_ids)]
    if not escolhidos:
        raise ValueError("Nenhum dos pares enviados existe nesta execução.")

    ineps = set()
    for p in escolhidos:
        ineps.add(p["escola_a"]["inep"])
        ineps.add(p["escola_b"]["inep"])

    envio = {
        "envio_id": novo_id(),
        "run_id": run_id,
        "fase": meta.get("fase", ""),
        "criado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        "autor": autor or "",
        "observacao": observacao or "",
        "status": "enviado",
        "par_ids": [p["par_id"] for p in escolhidos],
        # Cópia integral: o envio não pode depender do histórico em disco nem
        # mudar se a execução for refeita.
        "pares": escolhidos,
        "resumo": {
            "pares": len(escolhidos),
            "escolas": len(ineps),
            "ineps": sorted(ineps),
            "imagens": sum(p["qtd_imagens"] for p in escolhidos),
            "fotos_camera": sum(p["qtd_fotos_camera"] for p in escolhidos),
            "mesmo_pdf": sum(1 for p in escolhidos if p.get("mesmo_pdf")),
            "pdf_divergente": sum(1 for p in escolhidos if p.get("pdf_divergente")),
            "fornecedores": sorted({f for p in escolhidos for f in p["fornecedores"]}),
            "ufs": sorted({u for p in escolhidos
                           for u in (p["escola_a"]["uf"], p["escola_b"]["uf"]) if u}),
        },
    }

    os.makedirs(DIR_AUDITORIA, exist_ok=True)
    with io.open(_caminho(envio["envio_id"]), "w", encoding="utf-8") as fh:
        json.dump(envio, fh, ensure_ascii=False, indent=2)

    # O banco é a fonte da verdade; se ele falhar, o envio fica em disco e na
    # fila de reenvio, e o aviso sobe para a tela em vez de sumir.
    envio["aviso"] = ""
    try:
        auditoria_db.salvar_envio(envio, escolhidos)
        envio["gravado_no_banco"] = True
    except auditoria_db.BancoIndisponivel as e:
        envio["gravado_no_banco"] = False
        envio["aviso"] = (f"Salvo localmente, mas não foi para o banco: {e}. "
                          "Está na fila de reenvio.")
    return envio


def _envios_do_disco():
    if not os.path.isdir(DIR_AUDITORIA):
        return []
    envios = []
    for nome in os.listdir(DIR_AUDITORIA):
        if not nome.endswith(".json"):
            continue
        try:
            with io.open(os.path.join(DIR_AUDITORIA, nome), encoding="utf-8") as fh:
                dado = json.load(fh)
        except Exception:
            continue
        # A pasta tambem guarda outros JSON (metadados de documentos, por ex.):
        # so entra o que tem a cara de um envio.
        if isinstance(dado, dict) and dado.get("envio_id"):
            dado.pop("pares", None)          # a lista não precisa das evidências
            envios.append(dado)
    return envios


def listar_envios():
    """
    Envios do mais recente para o mais antigo (sem os pares, só o resumo).

    Banco primeiro; o disco entra como complemento para o que ainda não subiu,
    de modo que um envio feito offline continue aparecendo.
    """
    por_id = {}
    if auditoria_db.disponivel():
        try:
            for e in auditoria_db.listar_envios():
                por_id[e["envio_id"]] = e
        except auditoria_db.BancoIndisponivel:
            pass

    for e in _envios_do_disco():
        por_id.setdefault(e["envio_id"], e)

    envios = list(por_id.values())
    envios.sort(key=lambda e: str(e.get("criado_em") or ""), reverse=True)
    return envios


def carregar_envio(envio_id):
    caminho = _caminho(envio_id)
    if os.path.exists(caminho):
        with io.open(caminho, encoding="utf-8") as fh:
            return json.load(fh)
    # Sem o arquivo local (VPS recém-criada, disco limpo): o banco tem tudo.
    if auditoria_db.disponivel():
        try:
            return auditoria_db.carregar_envio(envio_id)
        except auditoria_db.BancoIndisponivel:
            pass
    return None


def excluir_envio(envio_id):
    """
    Remove o envio. A trilha de eventos permanece: o registro de que o lote
    existiu e foi removido e justamente o que nao pode se perder.
    """
    removido = False
    caminho = _caminho(envio_id)
    if os.path.exists(caminho):
        os.remove(caminho)
        removido = True

    if auditoria_db.disponivel():
        try:
            auditoria_db.excluir_envio(envio_id)
            removido = True
        except auditoria_db.BancoIndisponivel:
            pass
    return removido


def pares_do_envio(envio_id):
    """
    Devolve (envio, pares) com os dados completos, prontos para a tela.

    Ordem de busca, da fonte mais confiavel para a menos:
      1. snapshot no banco — imutavel, independe de arquivo local;
      2. copia gravada junto do envio em disco;
      3. remontagem a partir do historico da execucao (envios antigos, feitos
         antes de passarmos a guardar a copia).
    """
    envio = carregar_envio(envio_id)
    if envio is None:
        return None, []

    if auditoria_db.disponivel():
        try:
            pares = auditoria_db.pares_do_envio(envio_id)
            if pares:
                return envio, pares
        except auditoria_db.BancoIndisponivel:
            pass

    if envio.get("pares"):
        return envio, envio["pares"]

    _meta, todos = pares_duplicatas.montar_pares(envio.get("run_id", ""))
    por_id = {p["par_id"]: p for p in todos}
    pares = [por_id[pid] for pid in envio.get("par_ids", []) if pid in por_id]
    return envio, pares


# --------------------------------------------------------------------------- #
# Documentos da auditoria
#
# Guardados em disco, ao lado do envio: auditoria/documentos/<envio_id>/<inep>/.
# Os metadados ficam em auditoria/<envio_id>_documentos.json.
#
# Nada vai para o Supabase por decisao de projeto: enquanto nao estiver definido
# se o documento mora aqui ou no Bubble, o disco local evita criar um terceiro
# lugar com dado de auditoria para depois ter que migrar.
# --------------------------------------------------------------------------- #

import mimetypes
import re

DIR_DOCS = os.path.join(DIR_AUDITORIA, "documentos")

MAX_BYTES = 25 * 1024 * 1024
EXTENSOES = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".png", ".jpg", ".jpeg", ".zip"}


def _nome_seguro(texto):
    """Nome de arquivo sem acento nem caractere especial."""
    tabela = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ",
                           "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", texto.translate(tabela)).strip("_")


def _caminho_meta(envio_id):
    """Fica junto dos arquivos, e nao na pasta dos envios, para nao se misturar."""
    return os.path.join(DIR_DOCS, envio_id, "_documentos.json")


def _ler_meta(envio_id):
    caminho = _caminho_meta(envio_id)
    if not os.path.exists(caminho):
        return []
    try:
        with io.open(caminho, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


def _gravar_meta(envio_id, docs):
    os.makedirs(os.path.join(DIR_DOCS, envio_id), exist_ok=True)
    with io.open(_caminho_meta(envio_id), "w", encoding="utf-8") as fh:
        json.dump(docs, fh, ensure_ascii=False, indent=2)


def salvar_documento(envio_id, inep, nome_arquivo, conteudo,
                     par_id="", observacao="", enviado_por=""):
    """Grava o arquivo em disco e registra o metadado do documento."""
    if not conteudo:
        raise ValueError("Arquivo vazio.")
    if len(conteudo) > MAX_BYTES:
        raise ValueError(f"Arquivo maior que {MAX_BYTES // 1024 // 1024} MB.")

    ext = os.path.splitext(nome_arquivo)[1].lower()
    if ext not in EXTENSOES:
        raise ValueError(f"Extensão {ext or '(sem)'} não permitida. "
                         f"Aceitas: {', '.join(sorted(EXTENSOES))}")

    carimbo = time.strftime("%Y%m%d_%H%M%S")
    seguro = f"{carimbo}_{_nome_seguro(os.path.basename(nome_arquivo))}"
    pasta = os.path.join(DIR_DOCS, envio_id, str(inep))
    os.makedirs(pasta, exist_ok=True)
    with open(os.path.join(pasta, seguro), "wb") as fh:
        fh.write(conteudo)

    doc = {
        "doc_id": f"{envio_id}_{inep}_{carimbo}",
        "envio_id": envio_id,
        "inep": int(inep),
        "par_id": par_id or None,
        "arquivo_nome": os.path.basename(nome_arquivo),
        "arquivo_path": f"{envio_id}/{inep}/{seguro}",
        # Impressao digital do que foi anexado: permite provar depois que o
        # arquivo em disco e o mesmo que a auditoria registrou.
        "sha256": hashlib.sha256(conteudo).hexdigest(),
        "tamanho_bytes": len(conteudo),
        "tipo_mime": mimetypes.guess_type(nome_arquivo)[0] or "application/octet-stream",
        "observacao": observacao or None,
        "enviado_por": enviado_por or None,
        "criado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    docs = _ler_meta(envio_id)
    docs.append(doc)
    _gravar_meta(envio_id, docs)

    # Arquivo no Storage e metadado no banco. Falhou, vai para a fila e o aviso
    # sobe para a tela — documento de resolucao nao pode sumir calado.
    doc["aviso"] = ""
    try:
        auditoria_db.salvar_documento(doc, conteudo)
        doc["gravado_no_banco"] = True
    except auditoria_db.BancoIndisponivel as e:
        doc["gravado_no_banco"] = False
        doc["aviso"] = (f"Salvo localmente, mas não foi para o banco: {e}. "
                        "Está na fila de reenvio.")
    return doc


def listar_documentos(envio_id):
    """
    Documentos do envio, do mais recente para o mais antigo.

    Banco primeiro; o disco completa com o que ainda nao subiu.
    """
    por_id = {}
    if auditoria_db.disponivel():
        try:
            for d in auditoria_db.listar_documentos(envio_id):
                por_id[d["doc_id"]] = d
        except auditoria_db.BancoIndisponivel:
            pass

    for d in _ler_meta(envio_id):
        por_id.setdefault(d.get("doc_id"), d)

    return sorted(por_id.values(),
                  key=lambda d: str(d.get("criado_em") or ""), reverse=True)


def caminho_documento(arquivo_path):
    """
    Caminho absoluto de um documento, barrando saida da pasta de documentos.

    O arquivo fica no disco da VPS E no Storage do Supabase. Se o disco nao
    tiver (container recem-criado, volume nao montado), baixa do Storage para o
    mesmo lugar e segue — e por isso que o parecer nao depende mais de o volume
    estar configurado certo.
    """
    destino = os.path.normpath(os.path.join(DIR_DOCS, arquivo_path))
    if not destino.startswith(os.path.normpath(DIR_DOCS) + os.sep):
        raise ValueError("Caminho inválido.")
    if os.path.exists(destino):
        return destino

    conteudo = auditoria_db.baixar_arquivo(arquivo_path)
    if conteudo:
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with open(destino, "wb") as fh:
            fh.write(conteudo)
        return destino
    return None


def excluir_documento(envio_id, doc_id):
    """Remove o documento do disco e do registro. O evento fica na trilha."""
    docs = _ler_meta(envio_id)
    alvo = next((d for d in docs if d.get("doc_id") == doc_id), None)
    if alvo is not None:
        caminho = caminho_documento(alvo["arquivo_path"])
        if caminho:
            try:
                os.remove(caminho)
            except Exception:
                pass
        _gravar_meta(envio_id, [d for d in docs if d.get("doc_id") != doc_id])

    if auditoria_db.disponivel():
        try:
            auditoria_db.excluir_documento(envio_id, doc_id)
            return True
        except auditoria_db.BancoIndisponivel:
            pass
    return alvo is not None
