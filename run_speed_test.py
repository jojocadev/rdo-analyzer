import sys
import os
import time

sys.path.append(r"c:\Projetos AI\Sisop\RDO images")
from supabase_analyzer import SupabaseRDOAnalyzer

def run_speed_test_on_test_tables(supplier_filter: str = "MAM INFORMATICA", limit: int = 500):
    print("=========================================================")
    print(">>> INICIANDO TESTE DE VELOCIDADE (TABELAS DE TESTE)")
    print("=========================================================")
    print(f"Fornecedor: '{supplier_filter}' | Limite de escolas: {limit}")
    print(f"Tabela Destino Analisados: rdo_analisados_test")
    print(f"Tabela Destino Duplicatas: duplicatas_rdo_test")
    print("---------------------------------------------------------")

    analyzer = SupabaseRDOAnalyzer(table_analisados="rdo_analisados_test", table_duplicates="duplicatas_rdo_test")

    # 1. Buscar escolas
    print(">>> [1/3] Buscando escolas no Supabase...")
    t_start = time.time()
    schools = analyzer.fetch_schools_from_supabase(fornecedor_filter=supplier_filter, limit=limit)
    print(f"    Carregadas {len(schools)} escolas em {round(time.time() - t_start, 2)}s!")

    if not schools:
        print("Nenhuma escola encontrada.")
        return

    # 2. Processar PDFs com Multi-Threading (20 workers)
    print(f"\n>>> [2/3] Baixando/Extraindo PDFs de {len(schools)} escolas com 20 THREADS PARALELAS...")
    t0 = time.time()
    
    def progress_cb(current, total, forn):
        if current % 50 == 0 or current == total:
            pct = round((current / total) * 100, 1)
            elapsed = round(time.time() - t0, 1)
            print(f"    [{current}/{total}] ({pct}%) escolas em {elapsed}s... (Imagens extraídas: {len(analyzer.extracted_images)})")

    imgs_count = analyzer.process_school_pdfs(schools, progress_callback=progress_cb, max_workers=20)
    t_extract = round(time.time() - t0, 2)

    print(f"\n[OK] Extração concluída em {t_extract}s! Total de {imgs_count} imagens extraídas.")

    # 3. Analisar duplicatas e Salvar na tabela de TESTE
    print("\n>>> [3/3] Analisando duplicatas e gravando na tabela rdo_analisados_test...")
    t_save = time.time()
    res = analyzer.analyze_duplicates()
    t_save_elapsed = round(time.time() - t_save, 2)

    total_time = round(time.time() - t_start, 2)

    print("\n=========================================================")
    print(">>> DESEMPENHO DO TESTE DE VELOCIDADE CONCLUÍDO:")
    print(f"  - Total de escolas testadas: {len(schools)}")
    print(f"  - Total de imagens processadas: {imgs_count}")
    print(f"  - Pares de duplicatas em rdo_analisados_test: {res.get('total_duplicate_pairs')}")
    print(f"  - Tempo de Extração (20 Threads): {t_extract}s")
    print(f"  - Tempo de Gravação no Supabase: {t_save_elapsed}s")
    print(f"  - TEMPO TOTAL DO TESTE: {total_time}s")
    print("=========================================================")

if __name__ == "__main__":
    # Pode alterar o fornecedor e limite aqui para qualquer teste
    run_speed_test_on_test_tables(supplier_filter="", limit=1000)
