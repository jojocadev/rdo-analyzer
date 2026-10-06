"""
Envios para auditoria: lotes de pares encaminhados a partir do Painel de Duplicatas.

Cada envio congela QUAIS pares foram encaminhados (por par_id) e de qual execução
vieram. Os dados do par continuam vindo do histórico da execução — o envio guarda
a seleção, não uma cópia das evidências, para que a auditoria veja sempre as
mesmas imagens e PDFs que o painel mostra.

Estrutura em disco:
    auditoria/<envio_id>.json
"""

import io
import json
import os
import time
import urllib.parse

import analisar_fase as motor
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
    return envio


def listar_envios():
    """Envios do mais recente para o mais antigo (sem os pares, só o resumo)."""
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
            envios.append(dado)
    envios.sort(key=lambda e: e.get("criado_em", ""), reverse=True)
    return envios


def carregar_envio(envio_id):
    caminho = _caminho(envio_id)
    if not os.path.exists(caminho):
        return None
    with io.open(caminho, encoding="utf-8") as fh:
        return json.load(fh)


def excluir_envio(envio_id):
    caminho = _caminho(envio_id)
    if os.path.exists(caminho):
        os.remove(caminho)
        return True
    return False


def pares_do_envio(envio_id):
    """
    Devolve (envio, pares) com os dados completos, prontos para a tela.

    Os pares vem do historico da execucao de origem e sao filtrados pelos par_ids
    congelados no envio, na mesma ordem em que foram encaminhados.
    """
    envio = carregar_envio(envio_id)
    if envio is None:
        return None, []

    _meta, todos = pares_duplicatas.montar_pares(envio["run_id"])
    por_id = {p["par_id"]: p for p in todos}
    pares = [por_id[pid] for pid in envio["par_ids"] if pid in por_id]
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
        "tamanho_bytes": len(conteudo),
        "tipo_mime": mimetypes.guess_type(nome_arquivo)[0] or "application/octet-stream",
        "observacao": observacao or None,
        "enviado_por": enviado_por or None,
        "criado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    docs = _ler_meta(envio_id)
    docs.append(doc)
    _gravar_meta(envio_id, docs)
    return doc


def listar_documentos(envio_id):
    """Documentos do envio, do mais recente para o mais antigo."""
    return sorted(_ler_meta(envio_id),
                  key=lambda d: d.get("criado_em", ""), reverse=True)


def caminho_documento(arquivo_path):
    """Caminho absoluto de um documento, barrando saida da pasta de documentos."""
    destino = os.path.normpath(os.path.join(DIR_DOCS, arquivo_path))
    if not destino.startswith(os.path.normpath(DIR_DOCS) + os.sep):
        raise ValueError("Caminho inválido.")
    return destino if os.path.exists(destino) else None


def excluir_documento(envio_id, doc_id):
    """Remove o documento do disco e do registro."""
    docs = _ler_meta(envio_id)
    alvo = next((d for d in docs if d.get("doc_id") == doc_id), None)
    if alvo is None:
        return False
    caminho = caminho_documento(alvo["arquivo_path"])
    if caminho:
        try:
            os.remove(caminho)
        except Exception:
            pass
    _gravar_meta(envio_id, [d for d in docs if d.get("doc_id") != doc_id])
    return True
