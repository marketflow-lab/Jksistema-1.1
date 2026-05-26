import fitz  # PyMuPDF
import sys

def analisar_texto_bruto(pdf_path):
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"Erro ao abrir arquivo: {e}")
        return

    print(f"--- ANALISANDO: {pdf_path} ---")
    
    texto_acumulado = ""
    
    for i, page in enumerate(doc):
        # Extrai o texto nativo
        text = page.get_text("text")
        
        print(f"\n\n=== PÁGINA {i+1} ===")
        
        # 1. VISÃO "SEM ESPAÇOS" (O que você pediu)
        # Removemos todos os espaços em branco e quebras de linha
        sem_espaco = text.replace('\n', '').replace(' ', '').replace('\r', '')
        print("\n[VISÃO SEM ESPAÇOS - FLUXO DE DADOS]:")
        print(sem_espaco)
        
        # 2. VISÃO "RAIO-X" (Melhor para identificar padrões)
        # Espaço vira (_) e Quebra de linha vira (|)
        # Isso ajuda a ver se o SKU está numa linha isolada
        raio_x = text.replace('\n', '|').replace(' ', '_')
        print("\n[VISÃO RAIO-X ( _ = espaço, | = enter )]:")
        print(raio_x)

        texto_acumulado += sem_espaco

if __name__ == "__main__":
    # Substitua pelo nome do seu arquivo se rodar direto
    arquivo = "TrendMinas 1 item 03-02-2026.pdf" 
    analisar_texto_bruto(arquivo)