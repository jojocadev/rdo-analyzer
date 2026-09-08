import sys
import os
import time
import json
import urllib.request

sys.path.append(r"c:\Projetos AI\Sisop\RDO images")
from supabase_analyzer import SupabaseRDOAnalyzer

def run_fast_recovery():
    print("=========================================================")
    print(">>> INICIANDO RECUPERAÇÃO RÁPIDA DOS REGISTROS (CACHE LOCAL) ")
    print("=========================================================")
    
    analyzer = SupabaseRDOAnalyzer()
    
    # 1. Buscar todas as escolas salvas no Supabase
    print(">>> Buscando lista completa de escolas no Supabase (escolas_conectadas)...")
    schools = analyzer.fetch_schools_from_supabase(limit=30000)
    print(f"Total de escolas carregadas do banco: {len(schools)}")
    
    if not schools:
        print("Nenhuma escola encontrada em escolas_conectadas.")
        return

    # 2. Processamento Multi-Threaded paralelo com 20 workers
    print("\n>>> Processando PDFs com 20 Threads em paralelo...")
    t0 = time.time()
    
    def progress_cb(current, total, forn):
        if current % 500 == 0 or current == total:
            pct = round((current / total) * 100, 1)
            elapsed = round(time.time() - t0, 1)
            print(f"    [{current}/{total}] ({pct}%) escolas processadas em {elapsed}s... (Imagens extraídas até agora: {len(analyzer.extracted_images)})")

    total_images = analyzer.process_school_pdfs(schools, progress_callback=progress_cb, max_workers=20)
    elapsed_total = round(time.time() - t0, 1)
    
    print(f"\n[OK] Processamento concluído em {elapsed_total}s!")
    print(f"Total de imagens válidas extraídas: {total_images}")

    # 3. Análise de duplicatas e Salvamento no Supabase
    print("\n>>> Executando comparação de duplicatas e salvando nas tabelas rdo_analisados e duplicatas_rdo...")
    results = analyzer.analyze_duplicates()
    
    print("\n=========================================================")
    print(">>> RECUPERAÇÃO CONCLUÍDA COM SUCESSO!")
    print(f"  - Total de escolas analisadas: {results.get('total_schools_analyzed')}")
    print(f"  - Total de registros em rdo_analisados: {results.get('total_images')}")
    print(f"  - Total de pares em duplicatas_rdo: {results.get('total_duplicate_pairs')}")
    print("=========================================================")

if __name__ == "__main__":
    run_fast_recovery()
