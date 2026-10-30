"""
Marcacoes da triagem do Painel de Duplicatas: falso positivo e mae/filho.

Mesmo desenho da auditoria: o Supabase e a fonte da verdade e o disco e cache
de leitura. O julgamento de quem faz a triagem, com o motivo escrito, e trabalho
humano — nao pode sumir num redeploy, e e o que a proxima pessoa le para nao
refazer a analise.

Quando o banco nao responde, a marcacao fica em disco e o chamador recebe o
aviso; a tela mostra que nao subiu. A tabela e criada por sql/marcacoes.sql.
"""

import io
import json
import os
import time
import urllib.parse

import analisar_fase as motor
import auditoria_db

TABELA = "duplicatas_marcacoes"

# Motivos oferecidos na tela. O texto livre continua valendo — isto so evita que
# cada pessoa escreva a mesma coisa de um jeito diferente e impede agrupar depois.
MOTIVOS = [
    ("mesma_escola", "Mesma escola com dois INEPs"),
    ("material_comum", "Imagem é material padrão do fornecedor"),
    ("equipamento_igual", "Equipamento igual, fotos diferentes"),
    ("erro_sistema", "Erro do sistema de comparação"),
    ("conferido_ok", "Conferido: fotos realmente distintas"),
    ("outro", "Outro"),
]
CODIGOS = {c for c, _ in MOTIVOS}


def _caminho_disco(run_id):
    return os.path.join(motor.caminho_run(run_id), "marcacoes.json")


def _ler_disco(run_id):
    caminho = _caminho_disco(run_id)
    if not os.path.exists(caminho):
        return {}
    try:
        with io.open(caminho, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _gravar_disco(run_id, marcas):
    os.makedirs(motor.caminho_run(run_id), exist_ok=True)
    with io.open(_caminho_disco(run_id), "w", encoding="utf-8") as fh:
        json.dump(marcas, fh, ensure_ascii=False, indent=2)


def _tabela_existe():
    try:
        auditoria_db._chamar(
            f"{auditoria_db.SUPABASE_URL}/{TABELA}?select=*&limit=0", timeout=20)
        return True
    except auditoria_db.BancoIndisponivel as e:
        if "PGRST205" in str(e) or "HTTP 404" in str(e):
            return False
        raise


_estado = {"checado_em": 0, "ok": False, "motivo": ""}


def disponivel(forcar=False):
    agora = time.time()
    if not forcar and agora - _estado["checado_em"] < 120:
        return _estado["ok"]
    _estado["checado_em"] = agora
    try:
        if _tabela_existe():
            _estado.update(ok=True, motivo="")
        else:
            _estado.update(ok=False, motivo=(
                "A tabela duplicatas_marcacoes ainda não existe. Rode "
                "sql/marcacoes.sql no SQL Editor do Supabase."))
    except auditoria_db.BancoIndisponivel as e:
        _estado.update(ok=False, motivo=str(e))
    return _estado["ok"]


def motivo_indisponivel():
    return _estado["motivo"]


def ler(run_id):
    """
    Marcacoes da execucao, por par_id. Banco primeiro; disco completa o que
    ainda nao subiu.
    """
    marcas = {}
    if disponivel():
        try:
            alvo = urllib.parse.quote(run_id)
            linhas = auditoria_db._chamar(
                f"{auditoria_db.SUPABASE_URL}/{TABELA}"
                f"?select=*&run_id=eq.{alvo}&limit=20000")
            for linha in linhas or []:
                marcas[linha["par_id"]] = linha
        except auditoria_db.BancoIndisponivel:
            pass

    for par_id, marca in _ler_disco(run_id).items():
        marcas.setdefault(par_id, marca)
    return marcas


def marcar(run_id, par_id, falso_positivo=None, motivo_codigo=None, motivo=None,
           mae_filho=None, inep_a=None, inep_b=None, autor=""):
    """
    Cria ou atualiza a marcacao de um par.

    Os campos vem como None quando nao se quer mexer naquele: alternar mae/filho
    nao pode apagar o motivo do falso positivo que ja estava escrito.

    Levanta BancoIndisponivel se o banco recusar — mas so depois de gravar em
    disco, para que a marcacao nao se perca.
    """
    atual = dict(_ler_disco(run_id).get(par_id) or {})
    remoto = ler(run_id).get(par_id) or {}
    base = {**remoto, **atual} if atual else dict(remoto)

    if falso_positivo is not None:
        base["falso_positivo"] = bool(falso_positivo)
        if not falso_positivo:
            # Desmarcou: o motivo deixa de valer, mas fica na trilha.
            base["motivo_codigo"] = None
            base["motivo"] = None
    if motivo_codigo is not None:
        base["motivo_codigo"] = motivo_codigo or None
    if motivo is not None:
        base["motivo"] = motivo or None
    if mae_filho is not None:
        base["mae_filho"] = bool(mae_filho)

    base.update({
        "run_id": run_id,
        "par_id": par_id,
        "falso_positivo": bool(base.get("falso_positivo")),
        "mae_filho": bool(base.get("mae_filho")),
        "inep_a": inep_a if inep_a is not None else base.get("inep_a"),
        "inep_b": inep_b if inep_b is not None else base.get("inep_b"),
        "atualizado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        "atualizado_por": autor or base.get("atualizado_por") or None,
    })

    # Disco primeiro: se o banco falhar, a marcacao ainda existe em algum lugar.
    disco = _ler_disco(run_id)
    disco[par_id] = base
    _gravar_disco(run_id, disco)

    aviso = ""
    if disponivel():
        try:
            auditoria_db._chamar(
                f"{auditoria_db.SUPABASE_URL}/{TABELA}?on_conflict=run_id,par_id",
                data=[base], method="POST",
                extra={"Prefer": "resolution=merge-duplicates,return=minimal"})
        except auditoria_db.BancoIndisponivel as e:
            aviso = f"Salvo localmente, mas não foi para o banco: {e}"
    else:
        aviso = motivo_indisponivel()

    # Trilha: o estado atual fica na tabela, mas cada mudanca fica registrada.
    auditoria_db.registrar_evento(
        "marcacao_alterada", par_id=par_id,
        detalhe={"run_id": run_id,
                 "falso_positivo": base["falso_positivo"],
                 "motivo_codigo": base.get("motivo_codigo"),
                 "motivo": base.get("motivo"),
                 "mae_filho": base["mae_filho"]})

    base["aviso"] = aviso
    return base


# --------------------------------------------------------------------------- #
# INEPs ja encaminhados para auditoria
# --------------------------------------------------------------------------- #

_cache_enviados = {"em": 0, "ineps": set(), "pares": set()}
_VALIDADE = 30


def ineps_encaminhados(forcar=False):
    """
    INEPs e par_ids que ja foram para alguma auditoria.

    Serve ao filtro da tela: quem esta triando nao precisa rever uma escola que
    ja esta sendo fiscalizada. Vale para TODOS os envios, nao so os desta
    execucao — a escola ja encaminhada continua encaminhada.
    """
    agora = time.time()
    if not forcar and agora - _cache_enviados["em"] < _VALIDADE:
        return _cache_enviados["ineps"], _cache_enviados["pares"]

    ineps, pares = set(), set()
    if auditoria_db.disponivel():
        try:
            if auditoria_db.backend() == auditoria_db.STORAGE_BACKEND:
                for envio in auditoria_db.listar_envios():
                    for i in (envio.get("resumo") or {}).get("ineps", []):
                        ineps.add(int(i))
            else:
                linhas = auditoria_db._chamar(
                    f"{auditoria_db.SUPABASE_URL}/auditoria_envio_pares"
                    f"?select=par_id,inep_a,inep_b&limit=50000")
                for linha in linhas or []:
                    pares.add(linha["par_id"])
                    for chave in ("inep_a", "inep_b"):
                        if linha.get(chave):
                            ineps.add(int(linha[chave]))
        except auditoria_db.BancoIndisponivel:
            pass

    _cache_enviados.update(em=agora, ineps=ineps, pares=pares)
    return ineps, pares


def invalidar_encaminhados():
    """Chamado logo apos criar um envio, para o filtro refletir na hora."""
    _cache_enviados["em"] = 0
