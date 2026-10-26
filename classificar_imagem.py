"""
Classifica o que uma imagem do RDO E, para separar evidencia de ruido.

Por que existe:
    O filtro de duplicatas era so por fornecedor: se a imagem repetida ficava
    dentro de um mesmo fornecedor, virava "reaproveitamento". Isso deixou passar
    assinatura digitalizada, logo da operadora, print de planilha e caixa de
    formulario vazia — todos circulam dentro de um fornecedor por natureza.
    Na fase 5 isso respondia por 56 dos 101 pares apontados.

    Separar por proporcao tambem nao resolve: foto de vistoria real aparece em
    4:3, 3:2 e 16:9, a mesma proporcao de um print de tela. A versao anterior so
    aceitava 4:3 e 3:4, e por isso escondia fotos reais em 16:9.

Ordem das evidencias, da mais forte para a mais fraca:

    1. Coordenada carimbada lida pelo OCR. So foto de vistoria tem carimbo, entao
       isto decide sozinho. Quem chama passa `tem_coordenada`.
    2. Tamanho. Abaixo de 400 px no maior lado e logo ou icone.
    3. Textura. Print, documento e assinatura tem regiao identica pixel a pixel;
       foto tem ruido de sensor em toda parte. Medido no conjunto de referencia,
       foto deu uniformidade exatamente 0.000 e nao-foto entre 0.016 e 0.737.

    O teste de textura sozinho acerta 90% das fotos confirmadas por OCR; com a
    regra 1 na frente, o conjunto conferido fica sem erro. As fotos de parede
    branca e de tela de monitor sao as que a textura erra, porque a area lisa
    domina a imagem — e justamente nelas o carimbo costuma estar legivel.
"""

from PIL import Image
import numpy as np

FOTO = "foto"
PRINT_TELA = "print_tela"
DOCUMENTO = "documento"
ASSINATURA = "assinatura"
EM_BRANCO = "em_branco"
LOGO = "logo"
INDEFINIDO = "indefinido"

# So `foto` conta como evidencia de vistoria. Repetir o resto nao diz nada:
# o fornecedor anexa o mesmo material em toda escola por procedimento.
CLASSES_EVIDENCIA = {FOTO}

ROTULOS = {
    FOTO: "Foto de vistoria",
    PRINT_TELA: "Print de tela",
    DOCUMENTO: "Documento / planilha",
    ASSINATURA: "Assinatura",
    EM_BRANCO: "Área em branco",
    LOGO: "Logo / ícone",
    INDEFINIDO: "Indefinido",
}

# Fracao de linhas (ou colunas) vizinhas identicas pixel a pixel. Foto fica em
# zero; qualquer coisa desenhada passa disto.
LIMITE_UNIFORMIDADE = 0.005

# Area sem variacao local. Rede de seguranca para o sintetico que nao tem linha
# repetida; foto, mesmo de parede lisa, nao passou de 0.52 na referencia.
LIMITE_CHAPADO = 0.72

LADO_MINIMO_LOGO = 400


def medir(caminho):
    """Medidas baratas de uma imagem. None quando o arquivo nao abre."""
    try:
        with Image.open(caminho) as im:
            largura, altura = im.size
            # Textura precisa do pixel original: reduzir inventaria gradiente
            # onde nao ha e apagaria justamente a diferenca que o teste procura.
            cinza = np.asarray(im.convert("L"), dtype=np.int16)
            # Cor nao precisa: medir num thumb de 160x160 da o mesmo resultado e
            # evita um np.unique sobre milhoes de pixels, que dominava o tempo.
            pequena = im.convert("RGB").resize((160, 160), Image.NEAREST)
            rgb = np.asarray(pequena, dtype=np.int16)
    except Exception:
        return None

    if cinza.shape[0] < 8 or cinza.shape[1] < 8:
        return None

    linhas_iguais = float((np.abs(np.diff(cinza, axis=0)).max(axis=1) == 0).mean())
    colunas_iguais = float((np.abs(np.diff(cinza, axis=1)).max(axis=0) == 0).mean())

    gx = np.abs(np.diff(cinza, axis=1))[:-1, :]
    gy = np.abs(np.diff(cinza, axis=0))[:, :-1]
    grad = np.maximum(gx, gy)

    maior = rgb.max(axis=2)
    menor = rgb.min(axis=2)
    # Quantiza em 32 niveis por canal: conta cores visivelmente distintas sem
    # inflar a contagem com ruido de compressao.
    q = (rgb // 32).astype(np.uint8)

    return {
        "largura": largura,
        "altura": altura,
        "uniformidade": max(linhas_iguais, colunas_iguais),
        "chapado": float((grad < 2).mean()),
        "gradiente": float(grad.mean()),
        "branco": float((menor > 235).mean()),
        "saturacao": float((maior - menor).mean()),
        "cores": int(len(np.unique(q.reshape(-1, 3), axis=0))),
    }


def classificar_medidas(m, tem_coordenada=False):
    """Aplica as regras sobre as medidas ja calculadas."""
    if m is None:
        return FOTO if tem_coordenada else INDEFINIDO

    # Logo vem antes do carimbo: um logo nunca tem coordenada, mas se o OCR
    # alucinar uma em cima de um icone, o tamanho ainda manda.
    if max(m["largura"] or 0, m["altura"] or 0) < LADO_MINIMO_LOGO:
        return LOGO

    if tem_coordenada:
        return FOTO

    if m["uniformidade"] <= LIMITE_UNIFORMIDADE and m["chapado"] <= LIMITE_CHAPADO:
        return FOTO

    # A partir daqui nao e foto; resta dizer o que e.
    if m["gradiente"] < 1.5 and m["cores"] <= 6:
        return EM_BRANCO
    if m["branco"] >= 0.88 and m["saturacao"] < 12:
        return ASSINATURA
    if m["saturacao"] < 12:
        return DOCUMENTO
    return PRINT_TELA


def classificar(caminho, tem_coordenada=False):
    return classificar_medidas(medir(caminho), tem_coordenada)


def e_evidencia(classe):
    return classe in CLASSES_EVIDENCIA


def rotulo(classe):
    return ROTULOS.get(classe, ROTULOS[INDEFINIDO])
