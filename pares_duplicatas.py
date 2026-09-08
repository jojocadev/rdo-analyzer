"""
Visão por PAR DE ESCOLAS das duplicatas de uma execução.

O motor (analisar_fase.py) produz um grupo por IMAGEM compartilhada. Para auditar,
o que interessa é a relação entre duas escolas: se INEP 1 e INEP 2 têm 6 fotos
iguais, isso é UM caso com 6 evidências, não seis casos. Este módulo reagrupa os
grupos em pares `INEP A × INEP B`, junta todas as imagens e os dois PDFs na mesma
linha, aplica os filtros da tela e guarda a marcação de falso positivo.
"""

import io
import json
import os
import re
import time
from collections import defaultdict
from itertools import combinations

import analisar_fase as motor

# "12009679 - AC - BUJARI - ESC MARIA DO CARMO RAMOS - TIPP GESAC.pdf"
_RE_UF = re.compile(r"^[A-Z]{2}$")

# Proporções de foto de câmera/celular (4:3 e 3:4). Serve para separar foto de
# vistoria de screenshot de painel/planilha.
#
# A tolerância é apertada de propósito: a folga precisa ficar abaixo de 0,043, que é a
# distância entre 3:4 (0,750) e a proporção A4 (0,707). Com folga maior, a página
# "Aviso de desativação de escola" (1240x1754) passaria por foto.
_RAZOES_FOTO = (4 / 3, 3 / 4)
_TOLERANCIA_RAZAO = 0.02


def parse_nome_pdf(pdf_filename):
    """
    Extrai INEP, município e nome da escola do nome do arquivo do RDO.

    O padrão é `INEP - UF - MUNICIPIO - ESCOLA - TIPO.pdf` (97% dos arquivos). O nome
    da escola pode conter " - ", então tudo entre o município e o último campo é
    tratado como nome. Nomes fora do padrão (ex.: "EACE - Aviso de desativação")
    devolvem strings vazias em vez de dado errado.
    """
    vazio = {"inep": None, "uf": "", "municipio": "", "escola": ""}
    if not pdf_filename:
        return vazio

    base = pdf_filename[:-4] if pdf_filename.lower().endswith(".pdf") else pdf_filename
    partes = [p.strip() for p in base.split(" - ")]

    if len(partes) < 4 or not partes[0].isdigit() or not _RE_UF.match(partes[1]):
        return vazio

    return {
        "inep": int(partes[0]),
        "uf": partes[1],
        "municipio": partes[2],
        "escola": " - ".join(partes[3:-1]) if len(partes) > 4 else partes[3],
    }


def e_foto_de_camera(largura, altura):
    """Diz se as dimensões têm proporção de foto de câmera (4:3 ou 3:4)."""
    if not largura or not altura:
        return False
    razao = largura / altura
    return any(abs(razao - r) <= _TOLERANCIA_RAZAO for r in _RAZOES_FOTO)


# --------------------------------------------------------------------------- #
# Marcação de falso positivo
# --------------------------------------------------------------------------- #

def _caminho_marcas(run_id):
    return os.path.join(motor.caminho_run(run_id), "falsos_positivos.json")


def ler_marcas(run_id):
    """Lê as marcações de falso positivo de uma execução."""
    caminho = _caminho_marcas(run_id)
    if not os.path.exists(caminho):
        return {}
    try:
        with io.open(caminho, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def marcar(run_id, par_id, falso_positivo):
    """
    Liga/desliga a marcação de falso positivo de um par.

    Fica em arquivo dentro da execução, então a triagem sobrevive a recarregar a
    página e a reiniciar o servidor.
    """
    marcas = ler_marcas(run_id)
    if falso_positivo:
        marcas[par_id] = {"falso_positivo": True,
                          "marcado_em": time.strftime("%Y-%m-%d %H:%M:%S")}
    else:
        marcas.pop(par_id, None)

    os.makedirs(motor.caminho_run(run_id), exist_ok=True)
    with io.open(_caminho_marcas(run_id), "w", encoding="utf-8") as fh:
        json.dump(marcas, fh, ensure_ascii=False, indent=2)
    return marcas


# --------------------------------------------------------------------------- #
# Montagem dos pares
# --------------------------------------------------------------------------- #

def _lado(membro):
    """Dados de exibição de uma escola dentro do par."""
    info = parse_nome_pdf(membro.get("pdf_filename"))
    return {
        "inep": membro.get("inep"),
        "uf": membro.get("uf") or "",
        "municipio": info["municipio"],
        "escola": info["escola"],
        "fornecedor": membro.get("fornecedor") or "",
        "fase": membro.get("fase") or "",
    }


def montar_pares(run_id):
    """
    Reagrupa os grupos da execução em pares `INEP A × INEP B`.

    Um par junta TODAS as imagens que as duas escolas compartilham, com as páginas
    de cada lado e os PDFs envolvidos. Grupos com mais de duas escolas geram uma
    linha por combinação de duas — na prática são poucos (máximo 5 escolas).
    """
    meta = motor.carregar_run(run_id)
    if not meta:
        return None, []

    grupos = meta.pop("grupos", []) or []
    pares = {}

    for g in grupos:
        # Um representante por escola dentro do grupo
        por_inep = {}
        for m in g["membros"]:
            if m.get("inep"):
                por_inep.setdefault(m["inep"], m)

        for inep_a, inep_b in combinations(sorted(por_inep), 2):
            par_id = f"{inep_a}x{inep_b}"
            ma, mb = por_inep[inep_a], por_inep[inep_b]

            par = pares.get(par_id)
            if par is None:
                par = pares[par_id] = {
                    "par_id": par_id,
                    "escola_a": _lado(ma),
                    "escola_b": _lado(mb),
                    "fornecedores": set(),
                    "grupos": [],
                    "imagens": [],
                    "pdfs_a": {},
                    "pdfs_b": {},
                }

            if g.get("fornecedor"):
                par["fornecedores"].update(x.strip() for x in g["fornecedor"].split("/"))
            par["grupos"].append(g["grupo_id"])

            exata = ma["sha256"] == mb["sha256"]
            par["imagens"].append({
                "grupo_id": g["grupo_id"],
                "sha256": ma["sha256"],
                "exata": exata,
                "tipo": "Exata (100%)" if exata else "Visual",
                "largura": ma.get("width"),
                "altura": ma.get("height"),
                "foto_camera": e_foto_de_camera(ma.get("width"), ma.get("height")),
                "thumb_a": ma.get("thumb_url"),
                "thumb_b": mb.get("thumb_url"),
                "pagina_a": ma.get("pagina"),
                "pagina_b": mb.get("pagina"),
            })

            for lado, m in (("pdfs_a", ma), ("pdfs_b", mb)):
                if not m.get("pdf_url"):
                    continue
                info_pdf = parse_nome_pdf(m.get("pdf_filename"))
                par[lado].setdefault(m["pdf_url"], {
                    "pdf_filename": m.get("pdf_filename"),
                    "pdf_url": m.get("pdf_url"),
                    "inep_no_nome": info_pdf["inep"],
                    # O nome do arquivo carrega o INEP da escola a que ele pertence.
                    # Divergir do INEP do registro significa que o RDO anexado é de
                    # OUTRA escola — é achado por si só, não só imagem repetida.
                    "inep_divergente": bool(info_pdf["inep"]
                                            and info_pdf["inep"] != m.get("inep")),
                    "paginas": set(),
                })["paginas"].add(m.get("pagina"))

    marcas = ler_marcas(run_id)

    lista = []
    for par in pares.values():
        imagens = par["imagens"]
        for lado in ("pdfs_a", "pdfs_b"):
            par[lado] = [{**p, "paginas": sorted(x for x in p["paginas"] if x)}
                         for p in par[lado].values()]

        par["fornecedores"] = sorted(f for f in par["fornecedores"] if f)
        par["grupos"] = sorted(set(par["grupos"]))
        par["qtd_imagens"] = len(imagens)
        par["qtd_exatas"] = sum(1 for i in imagens if i["exata"])
        par["qtd_fotos_camera"] = sum(1 for i in imagens if i["foto_camera"])
        par["todas_exatas"] = par["qtd_exatas"] == len(imagens)
        par["mesmo_municipio"] = bool(par["escola_a"]["municipio"]) and \
            par["escola_a"]["municipio"] == par["escola_b"]["municipio"]
        par["mesma_uf"] = par["escola_a"]["uf"] == par["escola_b"]["uf"]
        par["pdf_divergente"] = any(p["inep_divergente"]
                                    for p in par["pdfs_a"] + par["pdfs_b"])
        # Os dois lados apontando para o MESMO arquivo é o caso mais grave: não é só
        # foto repetida, é o RDO de uma escola servindo de relatório para a outra.
        par["mesmo_pdf"] = bool(
            {p["pdf_url"] for p in par["pdfs_a"]} & {p["pdf_url"] for p in par["pdfs_b"]})

        marca = marcas.get(par["par_id"]) or {}
        par["falso_positivo"] = bool(marca.get("falso_positivo"))
        par["marcado_em"] = marca.get("marcado_em", "")

        lista.append(par)

    # Mais imagens compartilhadas primeiro: é o caso mais forte.
    lista.sort(key=lambda p: (p["qtd_fotos_camera"], p["qtd_imagens"]), reverse=True)
    return meta, lista


# --------------------------------------------------------------------------- #
# Filtros
# --------------------------------------------------------------------------- #

def _texto_do_par(par):
    """Todo o texto pesquisável de um par, em minúsculas."""
    partes = [str(par["escola_a"]["inep"]), str(par["escola_b"]["inep"])]
    for lado in ("escola_a", "escola_b"):
        partes += [par[lado]["escola"], par[lado]["municipio"],
                   par[lado]["uf"], par[lado]["fornecedor"]]
    for lado in ("pdfs_a", "pdfs_b"):
        partes += [p["pdf_filename"] or "" for p in par[lado]]
    return " ".join(partes).lower()


def _nomes_pdf(par):
    return " ".join([p["pdf_filename"] or "" for p in par["pdfs_a"] + par["pdfs_b"]]).lower()


def aplicar_filtros(pares, f):
    """
    Filtra a lista de pares conforme os controles da tela.

    :param f: dict com as chaves busca, contem, nao_contem, uf, municipio,
        fornecedor, tipo, falso_positivo, min_imagens, somente_fotos, mesmo_municipio.
    """
    out = pares

    busca = (f.get("busca") or "").strip().lower()
    if busca:
        out = [p for p in out if busca in _texto_do_par(p)]

    contem = (f.get("contem") or "").strip().lower()
    if contem:
        out = [p for p in out if contem in _nomes_pdf(p)]

    nao_contem = (f.get("nao_contem") or "").strip().lower()
    if nao_contem:
        out = [p for p in out if nao_contem not in _nomes_pdf(p)]

    uf = (f.get("uf") or "").strip().upper()
    if uf:
        out = [p for p in out if uf in (p["escola_a"]["uf"], p["escola_b"]["uf"])]

    municipio = (f.get("municipio") or "").strip().lower()
    if municipio:
        out = [p for p in out
               if municipio in (p["escola_a"]["municipio"].lower(),
                                p["escola_b"]["municipio"].lower())]

    fornecedor = (f.get("fornecedor") or "").strip().lower()
    if fornecedor:
        out = [p for p in out
               if any(fornecedor in x.lower() for x in p["fornecedores"])]

    tipo = (f.get("tipo") or "").strip()
    if tipo == "exatas":
        out = [p for p in out if p["todas_exatas"]]
    elif tipo == "visuais":
        out = [p for p in out if not p["todas_exatas"]]

    fp = (f.get("falso_positivo") or "").strip()
    if fp == "ocultar":
        out = [p for p in out if not p["falso_positivo"]]
    elif fp == "somente":
        out = [p for p in out if p["falso_positivo"]]

    try:
        min_imagens = int(f.get("min_imagens") or 0)
    except (TypeError, ValueError):
        min_imagens = 0
    if min_imagens > 1:
        out = [p for p in out if p["qtd_imagens"] >= min_imagens]

    if str(f.get("somente_fotos") or "").lower() in ("1", "true", "sim"):
        out = [p for p in out if p["qtd_fotos_camera"] > 0]

    mm = (f.get("mesmo_municipio") or "").strip()
    if mm == "sim":
        out = [p for p in out if p["mesmo_municipio"]]
    elif mm == "nao":
        out = [p for p in out if not p["mesmo_municipio"]]

    gravidade = (f.get("gravidade") or "").strip()
    if gravidade == "mesmo_pdf":
        out = [p for p in out if p["mesmo_pdf"]]
    elif gravidade == "pdf_divergente":
        out = [p for p in out if p["pdf_divergente"]]

    return out


def resumo(pares):
    """Métricas e distribuições para os cartões e gráficos da tela."""
    escolas = set()
    por_fornecedor = defaultdict(int)
    por_uf = defaultdict(int)
    imagens = fotos = exatas = fp = mesmo_pdf = pdf_div = 0

    for p in pares:
        escolas.add(p["escola_a"]["inep"])
        escolas.add(p["escola_b"]["inep"])
        imagens += p["qtd_imagens"]
        fotos += p["qtd_fotos_camera"]
        exatas += p["qtd_exatas"]
        if p["falso_positivo"]:
            fp += 1
        if p.get("mesmo_pdf"):
            mesmo_pdf += 1
        if p.get("pdf_divergente"):
            pdf_div += 1
        for nome in p["fornecedores"]:
            por_fornecedor[nome] += 1
        for lado in ("escola_a", "escola_b"):
            if p[lado]["uf"]:
                por_uf[p[lado]["uf"]] += 1

    ordenar = lambda d: sorted(({"nome": k, "valor": v} for k, v in d.items()),
                               key=lambda x: x["valor"], reverse=True)

    return {
        "pares": len(pares),
        "escolas": len(escolas),
        "imagens": imagens,
        "fotos_camera": fotos,
        "exatas": exatas,
        "falsos_positivos": fp,
        "pendentes": len(pares) - fp,
        "mesmo_municipio": sum(1 for p in pares if p["mesmo_municipio"]),
        "mesmo_pdf": mesmo_pdf,
        "pdf_divergente": pdf_div,
        "por_fornecedor": ordenar(por_fornecedor),
        "por_uf": ordenar(por_uf),
    }


def opcoes_filtro(pares):
    """Valores distintos para popular os seletores da tela."""
    ufs, municipios, fornecedores = set(), set(), set()
    for p in pares:
        for lado in ("escola_a", "escola_b"):
            if p[lado]["uf"]:
                ufs.add(p[lado]["uf"])
            if p[lado]["municipio"]:
                municipios.add(p[lado]["municipio"])
        fornecedores.update(p["fornecedores"])
    return {
        "ufs": sorted(ufs),
        "municipios": sorted(municipios),
        "fornecedores": sorted(fornecedores),
    }
