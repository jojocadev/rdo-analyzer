"""
Credenciais e configuração do projeto, lidas do ambiente.

A chave `service_role` do Supabase dá acesso total ao banco e ignora RLS, então ela
não pode viver no código: este repositório é público e a chave anterior ficou
exposta no histórico. Aqui ela vem de variável de ambiente, opcionalmente carregada
de um arquivo `.env` local (que o .gitignore mantém fora do repositório).

Na VPS, defina as variáveis no ambiente do serviço (systemd, docker-compose ou
`docker run -e`) em vez de criar o `.env`.

Uso:
    from config import SUPABASE_URL, SERVICE_KEY
"""

import os


def _carregar_env(caminho=None):
    """
    Lê um `.env` simples (CHAVE=valor por linha) para dentro de os.environ.

    Feito à mão de propósito: evita somar a dependência python-dotenv só para isto.
    Variáveis já presentes no ambiente têm precedência — o que é o comportamento
    correto num servidor, onde o ambiente é a fonte da verdade.
    """
    caminho = caminho or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(caminho):
        return
    with open(caminho, "r", encoding="utf-8") as fh:
        for linha in fh:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


_carregar_env()

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL", "https://jclwfskzstjwmfskbanz.supabase.co/rest/v1")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def exigir_credenciais():
    """
    Interrompe com uma mensagem útil se a chave não estiver configurada.

    Chamado por quem realmente precisa falar com o Supabase, e não no import, para
    que a interface web continue subindo e servindo o histórico já em disco mesmo
    sem credencial.
    """
    if not SERVICE_KEY:
        raise RuntimeError(
            "SUPABASE_SERVICE_KEY não está definida.\n"
            "Crie um arquivo .env na raiz do projeto com:\n"
            "    SUPABASE_URL=https://<projeto>.supabase.co/rest/v1\n"
            "    SUPABASE_SERVICE_KEY=<sua service_role key>\n"
            "Na VPS, defina as duas no ambiente do serviço."
        )
    return SERVICE_KEY
