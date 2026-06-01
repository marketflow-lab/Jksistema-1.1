import json
import requests
import base64
import os

# --- CONFIGURAÇÕES ---
PASTA_INFO = "info"
ARQUIVO_CONFIG_BLING = os.path.join(PASTA_INFO, "bling_contas.json")

def carregar_contas():
    if os.path.exists(ARQUIVO_CONFIG_BLING):
        try:
            with open(ARQUIVO_CONFIG_BLING, "r") as f: return json.load(f)
        except: return []
    return []

def buscar_ultimas_operacoes(conta):
    print(f"🔄 Consultando operações da conta: {conta.get('loja')}...")
    url = "https://www.bling.com.br/Api/v3/estoques/operacoes"
    headers = {"Authorization": f"Bearer {conta.get('access_token')}"}
    
    # Busca saídas recentes
    params = {"limite": 50, "tipo": "S"} 
    
    try:
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            print("\n" + "="*80)
            print("📋 RELATÓRIO DE MOVIMENTAÇÕES (SAÍDA)")
            print("="*80)
            print(f"{'DATA':<12} | {'QTD':<8} | {'OBSERVAÇÃO (Onde o segredo está escondido)'}")
            print("-" * 80)
            
            for item in data:
                obs = item.get("observacoes", "").replace("\n", " ")
                qtd = item.get("quantidade", 0)
                data_op = item.get("data", "")[:10]
                print(f"{data_op:<12} | {qtd:<8} | {obs}")
                
            print("\n" + "="*80)
            print("👉 ANALISE ACIMA: Procure uma linha que você sabe que é 'Devolução N'.")
            print("Veja qual palavra aparece na Observação: 'Nota Fiscal'? 'Manual'? 'Acerto'?")
        else:
            print(f"Erro ao buscar: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"Erro de conexão: {e}")

# EXECUÇÃO
contas = carregar_contas()
if contas:
    buscar_ultimas_operacoes(contas[0])
else:
    print("Nenhuma conta configurada.")