"""
Historico das execucoes do painel, espelhado no Supabase.

O que vai e o que fica:
    vai    meta.json, grupos.json e o cache de classes — menos de 1 MB por
           execucao, e e tudo que o painel precisa para abrir uma varredura.
    fica   analisados.jsonl (233 MB na fase 5, ja espelhado em rdo_analisados)
           e o ZIP das imagens (74 MB, gerado sob demanda).

Com isso, perder o volume /app/historico deixa de apagar a execucao: o painel
volta a monta-la do banco. As miniaturas continuam no disco, entao as imagens
em si podem faltar — mas a analise, os grupos e os pares voltam inteiros.

As tabelas sao criadas por sql/execucoes.sql.
"""

import io
import json
import os
import time
import urllib.parse

import auditoria_db

TABELA = "execucoes"
TABELA_GRUPOS = "execucao_grupos"
LOTE = 200

_estado = {"checado_em": 0, "ok": False, "motivo": ""}


def disponivel(forcar=False):
    agora = time.time()
    if not forcar and agora - _estado["checado_em"] < 120:
        return _estado["ok"]
    _estado["checado_em"] = agora
    try:
        for t in (TABELA, TABELA_GRUPOS):
            auditoria_db._chamar(
                f"{auditoria_db.SUPABASE_URL}/{t}?select=*&limit=0", timeout=20)
        _estado.update(ok=True, motivo="")
    except auditoria_db.BancoIndisponivel as e:
        falta = "PGRST205" in str(e) or "HTTP 404" in str(e)
        _estado.update(ok=False, motivo=(
            "As tabelas do histórico ainda não existem. Rode sql/execucoes.sql "
            "no SQL Editor do Supabase." if falta else str(e)))
    return _estado["ok"]


def motivo_indisponivel():
    return _estado["motivo"]


def _linha_execucao(meta, classes=None):
    # `meta` sem os grupos: eles vao para a tabela propria, e repetir aqui
    # dobraria o tamanho de graca.
    enxuto = {k: v for k, v in meta.items() if k != "grupos"}
    return {
        "run_id": meta["run_id"],
        "fase": str(meta.get("fase") or "") or None,
        "fornecedor": meta.get("fornecedor") or None,
        "status": meta.get("status") or None,
        "iniciado_em": meta.get("iniciado_em") or None,
        "concluido_em": meta.get("concluido_em") or None,
        "metricas": meta.get("metricas") or {},
        "meta": enxuto,
        "classes": classes or {},
    }


def _linha_grupo(run_id, g):
    return {
        "run_id": run_id,
        "grupo_id": g.get("grupo_id"),
        "tipo": g.get("tipo"),
        "fornecedor": g.get("fornecedor"),
        "qtd_ineps": g.get("qtd_ineps"),
        "qtd_ocorrencias": g.get("qtd_ocorrencias"),
        "qtd_shas": g.get("qtd_shas"),
        "ineps": g.get("ineps") or [],
        "ufs": g.get("ufs") or [],
        "membros": g.get("membros") or [],
    }


def salvar(meta, grupos=None, classes=None):
    """
    Grava (ou regrava) uma execucao e seus grupos.

    Levanta BancoIndisponivel se o banco recusar. Quem chama decide se isso
    interrompe a varredura — hoje nao interrompe: o disco ja guardou tudo.
    """
    if not meta.get("run_id"):
        raise ValueError("meta sem run_id")

    auditoria_db._chamar(
        f"{auditoria_db.SUPABASE_URL}/{TABELA}?on_conflict=run_id",
        data=[_linha_execucao(meta, classes)], method="POST",
        extra={"Prefer": "resolution=merge-duplicates,return=minimal"},
        timeout=180)

    if grupos is None:
        return True

    linhas = [_linha_grupo(meta["run_id"], g) for g in grupos]
    for i in range(0, len(linhas), LOTE):
        auditoria_db._chamar(
            f"{auditoria_db.SUPABASE_URL}/{TABELA_GRUPOS}?on_conflict=run_id,grupo_id",
            data=linhas[i:i + LOTE], method="POST",
            extra={"Prefer": "resolution=merge-duplicates,return=minimal"},
            timeout=180)
    return True


def listar():
    """Execucoes do banco, da mais recente para a mais antiga."""
    linhas = auditoria_db._chamar(
        f"{auditoria_db.SUPABASE_URL}/{TABELA}"
        f"?select=run_id,meta&order=iniciado_em.desc&limit=500")
    return [l["meta"] or {"run_id": l["run_id"]} for l in (linhas or [])]


def carregar(run_id, com_grupos=True):
    """Reconstroi o meta de uma execucao a partir do banco, ou None."""
    alvo = urllib.parse.quote(run_id)
    linhas = auditoria_db._chamar(
        f"{auditoria_db.SUPABASE_URL}/{TABELA}?select=*&run_id=eq.{alvo}&limit=1")
    if not linhas:
        return None

    linha = linhas[0]
    meta = dict(linha.get("meta") or {})
    meta.setdefault("run_id", run_id)
    if com_grupos:
        meta["grupos"] = grupos_de(run_id)
    return meta


def grupos_de(run_id):
    alvo = urllib.parse.quote(run_id)
    grupos, off = [], 0
    while True:
        lote = auditoria_db._chamar(
            f"{auditoria_db.SUPABASE_URL}/{TABELA_GRUPOS}"
            f"?select=*&run_id=eq.{alvo}&order=grupo_id&limit=1000&offset={off}",
            timeout=180)
        if not lote:
            break
        grupos += [{k: v for k, v in g.items() if k not in ("id", "run_id")}
                   for g in lote]
        if len(lote) < 1000:
            break
        off += 1000
    return grupos


def classes_de(run_id):
    """Cache de classificacao das imagens, para nao reclassificar do zero."""
    alvo = urllib.parse.quote(run_id)
    linhas = auditoria_db._chamar(
        f"{auditoria_db.SUPABASE_URL}/{TABELA}?select=classes&run_id=eq.{alvo}&limit=1")
    return (linhas or [{}])[0].get("classes") or {}


def run_ids_no_banco():
    """So os identificadores, para comparar com o disco sem baixar tudo."""
    linhas = auditoria_db._chamar(
        f"{auditoria_db.SUPABASE_URL}/{TABELA}?select=run_id&limit=2000")
    return {l["run_id"] for l in (linhas or [])}


def sincronizar_do_disco(so_faltantes=True):
    """
    Sobe para o banco as execucoes que estao em disco.

    Serve ao container recem-criado: a varredura rodou antes de o historico ir
    para o banco, e sem isto ela existiria so no volume. Roda sozinha na subida
    da aplicacao e tambem da para disparar pela rota /api/historico/sincronizar.

    Com `so_faltantes`, pula o que ja esta la — e o caso normal, e evita
    reescrever centenas de grupos a cada reinicio.
    """
    import analisar_fase as motor

    if not disponivel(forcar=True):
        return {"ok": False, "motivo": motivo_indisponivel(),
                "enviadas": 0, "ja_estavam": 0, "falhas": 0}

    if not os.path.isdir(motor.HISTORICO_DIR):
        return {"ok": True, "motivo": "", "enviadas": 0, "ja_estavam": 0,
                "falhas": 0, "detalhe": "sem pasta historico/"}

    existentes = run_ids_no_banco() if so_faltantes else set()
    enviadas, ja_estavam, falhas, nomes = 0, 0, 0, []

    for run_id in sorted(os.listdir(motor.HISTORICO_DIR)):
        run_dir = os.path.join(motor.HISTORICO_DIR, run_id)
        if not os.path.isdir(run_dir):
            continue
        if run_id in existentes:
            ja_estavam += 1
            continue

        meta = _json_do_arquivo(os.path.join(run_dir, "meta.json"))
        if not meta:
            continue
        meta.setdefault("run_id", run_id)
        grupos = _json_do_arquivo(os.path.join(run_dir, "grupos.json")) or []
        classes = _json_do_arquivo(os.path.join(run_dir, "classes_imagens.json")) or {}

        try:
            salvar(meta, grupos, classes)
            enviadas += 1
            nomes.append(run_id)
        except Exception:
            falhas += 1

    return {"ok": True, "motivo": "", "enviadas": enviadas,
            "ja_estavam": ja_estavam, "falhas": falhas, "novas": nomes}


def _json_do_arquivo(caminho):
    if not os.path.exists(caminho):
        return None
    try:
        with io.open(caminho, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


PREFIXO_MINIATURAS = "miniaturas"


def subir_miniaturas(grupos, dir_thumbs, progresso=None):
    """
    Sobe para o Storage so as miniaturas que aparecem em algum grupo.

    Por que so essas: extracted_images tem 53 GB, porque guarda TODA imagem
    extraida de TODO PDF. Mas o painel so exibe as que se repetem entre escolas
    — algumas centenas por execucao, uns 50 MB. Sao elas a evidencia; o resto e
    subproduto da extracao e se refaz rodando a varredura de novo.

    Sem isto, um container sem o volume de extracted_images mostra a analise
    inteira com os quadradinhos de imagem quebrada, que foi o que aconteceu.
    """
    nomes = []
    for g in grupos or []:
        for m in g.get("membros") or []:
            if m.get("thumb_url"):
                nomes.append(m["thumb_url"])
    nomes = sorted(set(nomes))

    enviadas = faltando = 0
    for i, nome in enumerate(nomes, 1):
        caminho = os.path.join(dir_thumbs, nome)
        if not os.path.exists(caminho):
            faltando += 1
            continue
        try:
            with open(caminho, "rb") as fh:
                auditoria_db.subir_arquivo(f"{PREFIXO_MINIATURAS}/{nome}",
                                           fh.read(), "image/jpeg")
            enviadas += 1
        except auditoria_db.BancoIndisponivel:
            faltando += 1
        if progresso and i % 25 == 0:
            progresso(i, len(nomes))
    return {"total": len(nomes), "enviadas": enviadas, "faltando": faltando}


def baixar_miniatura(nome):
    """Conteudo de uma miniatura guardada no Storage, ou None."""
    return auditoria_db.baixar_arquivo(f"{PREFIXO_MINIATURAS}/{nome}")


def excluir(run_id):
    alvo = urllib.parse.quote(run_id)
    auditoria_db._chamar(f"{auditoria_db.SUPABASE_URL}/{TABELA}?run_id=eq.{alvo}",
                         method="DELETE", extra={"Prefer": "return=minimal"})
    return True
