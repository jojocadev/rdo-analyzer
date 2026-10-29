"""
Autenticacao por token do painel.

Por que existe:
    O servico subiu numa URL publica (easypanel.host) com cerca de 40 rotas e
    nenhuma barreira. Qualquer pessoa com o endereco lia os envios de auditoria,
    disparava uma varredura de 35 minutos, baixava as evidencias ou apagava um
    envio. Dado de fiscalizacao de escola nao pode ficar assim.

Como o token chega:
    1. cabecalho `Authorization: Bearer <token>` — para chamada de API;
    2. cabecalho `X-Painel-Token` — idem, quando o Authorization ja e usado;
    3. `?token=<token>` na URL — e o que funciona dentro de um iframe, onde nao
       da para mandar cabecalho; o token entao vira cookie e some da navegacao
       seguinte;
    4. cookie `painel_token`, gravado quando um dos de cima valida.

O que fica aberto:
    `/api/health`, porque a sonda do EasyPanel precisa alcancar sem credencial,
    e ela nao devolve dado nenhum — so quais espelhos estao de pe.

Sem PAINEL_TOKEN definido o painel continua aberto, para nao derrubar quem ja
usa, mas o /api/health passa a dizer `autenticacao: false` e o log avisa a cada
subida. Em producao, defina.
"""

import hmac
import os
import secrets

from flask import jsonify, make_response, request

COOKIE = "painel_token"

# A sonda de saude e os estaticos da propria pagina (a pagina em si e protegida;
# sem isso o navegador pediria o token para cada arquivo).
ABERTAS = ("/api/health",)
EXTENSOES_ABERTAS = (".css", ".js", ".png", ".jpg", ".svg", ".ico", ".woff", ".woff2")


_ARQUIVO_TOKEN = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "auditoria", ".painel_token")

_gerado = {"token": ""}


def token_configurado():
    """
    O token em vigor. Nunca vazio: se PAINEL_TOKEN nao estiver definido, um e
    gerado e impresso no log da aplicacao.

    Por que nao deixar aberto quando falta a variavel: o servico roda em URL
    publica com dado de fiscalizacao. Ficar aberto "ate alguem lembrar de
    configurar" e justamente como ele passou dias exposto. Falhar fechado
    obriga a buscar o token no log uma vez, o que e um custo pequeno perto de
    deixar a auditoria legivel para qualquer um.
    """
    do_ambiente = (os.environ.get("PAINEL_TOKEN") or "").strip()
    if do_ambiente:
        return do_ambiente
    if _gerado["token"]:
        return _gerado["token"]

    # Reaproveita o token da ultima subida quando a pasta e um volume: assim o
    # cookie de quem ja entrou continua valendo depois de um restart.
    try:
        if os.path.exists(_ARQUIVO_TOKEN):
            with open(_ARQUIVO_TOKEN, encoding="utf-8") as fh:
                guardado = fh.read().strip()
            if guardado:
                _gerado["token"] = guardado
                return guardado
    except Exception:
        pass

    novo = secrets.token_urlsafe(24)
    _gerado["token"] = novo
    try:
        os.makedirs(os.path.dirname(_ARQUIVO_TOKEN), exist_ok=True)
        with open(_ARQUIVO_TOKEN, "w", encoding="utf-8") as fh:
            fh.write(novo)
    except Exception:
        pass
    return novo


def _confere(enviado):
    """Comparacao em tempo constante: evita descobrir o token por cronometragem."""
    esperado = token_configurado()
    if not esperado or not enviado:
        return False
    return hmac.compare_digest(enviado.strip(), esperado)


def _token_da_requisicao():
    cab = request.headers.get("Authorization") or ""
    if cab.lower().startswith("bearer "):
        return cab[7:]
    return (request.headers.get("X-Painel-Token")
            or request.args.get("token")
            or request.cookies.get(COOKIE)
            or "")


def _aberta(caminho):
    if caminho in ABERTAS:
        return True
    return caminho.lower().endswith(EXTENSOES_ABERTAS)


def instalar(app):
    """Liga a verificacao em todas as rotas do app."""

    if (os.environ.get("PAINEL_TOKEN") or "").strip():
        print(">>> Acesso protegido por PAINEL_TOKEN.", flush=True)
    else:
        print("\n" + "=" * 62, flush=True)
        print(">>> PAINEL_TOKEN nao definido. Token gerado para esta instalacao:", flush=True)
        print(f"\n        {token_configurado()}\n", flush=True)
        print(">>> Use ?token=<acima> na primeira vez, ou defina PAINEL_TOKEN", flush=True)
        print(">>> no ambiente do servico para fixar um token seu.", flush=True)
        print("=" * 62 + "\n", flush=True)

    @app.before_request
    def _exigir_token():
        if request.method == "OPTIONS" or _aberta(request.path):
            return None
        if _confere(_token_da_requisicao()):
            return None

        if request.path.startswith("/api/"):
            return jsonify({"error": "Token inválido ou ausente.",
                            "dica": "Mande Authorization: Bearer <token> ou ?token=<token>"}), 401
        return make_response(_PAGINA_LOGIN, 401, {"Content-Type": "text/html; charset=utf-8"})

    @app.after_request
    def _fixar_cookie(resp):
        """
        Token que veio pela URL vira cookie, para a navegacao seguinte nao
        precisar carrega-lo — e para ele sair da barra de endereco.
        """
        if request.args.get("token") and resp.status_code < 400:
            if _confere(request.args.get("token")):
                resp.set_cookie(COOKIE, token_configurado(), httponly=True,
                                samesite="Lax", secure=request.is_secure,
                                max_age=60 * 60 * 24 * 30)
        # Limita quem pode embutir a pagina num iframe. Sem isto qualquer site
        # consegue, e o clique do usuario vale como autenticado pelo cookie.
        origens = (os.environ.get("PAINEL_FRAME_ANCESTORS") or "").strip()
        if origens:
            resp.headers["Content-Security-Policy"] = f"frame-ancestors {origens}"
        else:
            resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        return resp


_PAGINA_LOGIN = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Acesso restrito</title>
<style>
  body { font-family: system-ui, -apple-system, Segoe UI, sans-serif;
         background:#f4f6f9; color:#2b3440; display:flex; min-height:100vh;
         align-items:center; justify-content:center; margin:0; }
  .cx { background:#fff; padding:2rem 2.2rem; border-radius:12px; max-width:380px;
        box-shadow:0 2px 14px rgba(20,40,70,.1); }
  h1 { font-size:1.1rem; margin:0 0 .6rem; }
  p  { font-size:.88rem; line-height:1.5; color:#5b6472; margin:0 0 1rem; }
  input { width:100%; padding:.6rem .7rem; border:1px solid #d7dce4; border-radius:8px;
          font-size:.92rem; box-sizing:border-box; }
  button { margin-top:.8rem; width:100%; padding:.6rem; border:0; border-radius:8px;
           background:#2b6cb0; color:#fff; font-size:.92rem; cursor:pointer; }
</style></head>
<body><form class="cx" method="get">
  <h1>Painel de Duplicatas &middot; acesso restrito</h1>
  <p>Informe o token de acesso para continuar.</p>
  <input type="password" name="token" placeholder="Token" autofocus>
  <button type="submit">Entrar</button>
</form></body></html>
"""
