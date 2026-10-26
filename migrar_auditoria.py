"""
Leva para o Supabase a auditoria que hoje so existe em disco.

Rode UMA VEZ depois de executar sql/auditoria.sql no SQL Editor. Pode rodar de
novo sem estragar nada: envio e documento sao gravados com upsert pela chave
natural, entao repetir apenas reescreve a mesma linha.

Os envios antigos nao guardavam copia dos pares — eles sao remontados a partir
do historico da execucao no momento da migracao, e e por isso que esta migracao
precisa rodar na maquina que ainda tem a pasta `historico/`.

Uso:
    python migrar_auditoria.py --simular    # mostra o que subiria
    python migrar_auditoria.py              # aplica
"""

import argparse
import io
import json
import os
import sys

import auditoria
import auditoria_db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simular", action="store_true",
                    help="Nao grava; so mostra o que seria enviado")
    args = ap.parse_args()

    print("=" * 68)
    print(">>> MIGRACAO DA AUDITORIA PARA O SUPABASE"
          + ("  [SIMULACAO]" if args.simular else ""))
    print("=" * 68)

    if not auditoria_db.disponivel(forcar=True):
        print("\n[PARADO] " + auditoria_db.motivo_indisponivel())
        print("\nRode primeiro, no SQL Editor do Supabase:")
        print("    sql/auditoria.sql")
        return 1

    print("\n>>> Banco acessivel. Garantindo o bucket dos documentos...")
    print("    bucket:", "ok" if auditoria_db.garantir_bucket() else "FALHOU")

    envios = auditoria._envios_do_disco()
    print(f"\n>>> {len(envios)} envio(s) em disco")

    enviados = docs_enviados = falhas = 0
    for cab in sorted(envios, key=lambda e: e.get("criado_em", "")):
        envio_id = cab["envio_id"]
        envio, pares = auditoria.pares_do_envio(envio_id)
        docs = auditoria._ler_meta(envio_id)

        print(f"\n  {envio_id}  ({cab.get('criado_em')})")
        print(f"      pares remontados : {len(pares)}")
        print(f"      documentos       : {len(docs)}")
        if not pares:
            print("      [AVISO] nenhum par: o historico desta execucao nao esta "
                  "nesta maquina. O cabecalho sobe, as evidencias nao.")

        if args.simular:
            continue

        try:
            auditoria_db.salvar_envio(envio, pares)
            enviados += 1
            print("      envio ............ OK")
        except auditoria_db.BancoIndisponivel as e:
            falhas += 1
            print(f"      envio ............ FALHOU: {e}")
            continue

        for doc in docs:
            caminho = os.path.join(auditoria.DIR_DOCS, doc["arquivo_path"])
            if not os.path.exists(caminho):
                print(f"      doc {doc['arquivo_nome'][:28]:28s} arquivo sumiu do disco")
                continue
            with open(caminho, "rb") as fh:
                conteudo = fh.read()
            try:
                auditoria_db.salvar_documento(doc, conteudo)
                docs_enviados += 1
                print(f"      doc {doc['arquivo_nome'][:28]:28s} OK")
            except auditoria_db.BancoIndisponivel as e:
                falhas += 1
                print(f"      doc {doc['arquivo_nome'][:28]:28s} FALHOU: {e}")

    if args.simular:
        print("\n[SIMULACAO] Nada foi gravado.")
        return 0

    print("\n" + "=" * 68)
    print(f">>> {enviados} envio(s) e {docs_enviados} documento(s) no banco"
          + (f" | {falhas} falha(s)" if falhas else ""))

    pendentes = auditoria_db.listar_pendentes()
    if pendentes:
        print(f"    {len(pendentes)} item(ns) na fila de reenvio")
    print("=" * 68)
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
