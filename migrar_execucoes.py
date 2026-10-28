"""
Leva para o Supabase o historico de execucoes que esta em disco.

Sobe apenas meta.json, grupos.json e o cache de classes — menos de 1 MB por
execucao. analisados.jsonl (233 MB na fase 5) fica onde esta, porque ja esta
espelhado em rdo_analisados, e o ZIP das imagens e gerado sob demanda.

Rode depois de executar sql/execucoes.sql. Pode rodar quantas vezes quiser:
execucao e grupo sao gravados pela chave natural, entao repetir reescreve a
mesma linha.

Uso:
    python migrar_execucoes.py --simular   # mostra o que subiria
    python migrar_execucoes.py             # aplica
"""

import argparse
import io
import json
import os
import sys

import analisar_fase as motor
import execucoes_db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _ler_json(caminho):
    if not os.path.exists(caminho):
        return None
    try:
        with io.open(caminho, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simular", action="store_true",
                    help="Nao grava; so mostra o que seria enviado")
    args = ap.parse_args()

    print("=" * 68)
    print(">>> MIGRACAO DO HISTORICO DE EXECUCOES"
          + ("  [SIMULACAO]" if args.simular else ""))
    print("=" * 68)

    if not execucoes_db.disponivel(forcar=True):
        print("\n[PARADO] " + execucoes_db.motivo_indisponivel())
        return 1

    if not os.path.isdir(motor.HISTORICO_DIR):
        print("\nNao ha pasta historico/ nesta maquina.")
        return 0

    nomes = sorted(os.listdir(motor.HISTORICO_DIR))
    print(f"\n>>> {len(nomes)} pasta(s) em historico/\n")

    enviadas = vazias = falhas = 0
    for run_id in nomes:
        run_dir = os.path.join(motor.HISTORICO_DIR, run_id)
        if not os.path.isdir(run_dir):
            continue

        meta = _ler_json(os.path.join(run_dir, "meta.json"))
        if not meta:
            print(f"  {run_id:34s} sem meta.json, pulando")
            vazias += 1
            continue

        meta.setdefault("run_id", run_id)
        grupos = _ler_json(os.path.join(run_dir, "grupos.json")) or []
        classes = _ler_json(os.path.join(run_dir, "classes_imagens.json")) or {}

        tamanho = sum(os.path.getsize(os.path.join(run_dir, a))
                      for a in ("meta.json", "grupos.json", "classes_imagens.json")
                      if os.path.exists(os.path.join(run_dir, a)))

        print(f"  {run_id:34s} {meta.get('status', '?'):10s} "
              f"{len(grupos):>5} grupos  {len(classes):>5} classes  "
              f"{tamanho/1e6:.2f} MB")

        if args.simular:
            continue

        try:
            execucoes_db.salvar(meta, grupos, classes)
            enviadas += 1
        except Exception as e:
            falhas += 1
            print(f"      FALHOU: {e}")

    if args.simular:
        print("\n[SIMULACAO] Nada foi gravado.")
        return 0

    print("\n" + "=" * 68)
    print(f">>> {enviadas} execucao(oes) no banco"
          + (f" | {vazias} sem meta" if vazias else "")
          + (f" | {falhas} falha(s)" if falhas else ""))
    print("=" * 68)
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
