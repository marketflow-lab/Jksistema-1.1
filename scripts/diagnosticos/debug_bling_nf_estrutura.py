"""
Script para debugar a estrutura real da resposta da API do Bling para NF-e de entrada
"""
import json
import sqlite3
import os
import requests

# Configurações
CLIENT_ID = "000002"
LOJA = "Deckas"  # Nome correto da loja

print("\n" + "="*80)
print("DEBUG: ESTRUTURA DA API BLING - DETALHES DE NF-e DE ENTRADA")
print("="*80 + "\n")

# Buscar uma nota de entrada com devolução do banco
db_path = f"info/{CLIENT_ID}/vendas_historico.db"
if not os.path.exists(db_path):
    print(f"[ERRO] Banco não encontrado: {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Pegar uma nota de devolução
nota_teste = cur.execute("""
    SELECT id_bling, numero, data_emissao 
    FROM notas_entrada 
    WHERE devolucao = 1 
    LIMIT 1
""").fetchone()

if not nota_teste:
    print("[ERRO] Nenhuma nota de devolução encontrada no banco")
    conn.close()
    exit(1)

id_bling = nota_teste["id_bling"]
numero = nota_teste["numero"]
print(f"[OK] Nota selecionada: {numero} (ID Bling: {id_bling})")

# Buscar credenciais da API direto do arquivo
config_path = f"info/{CLIENT_ID}/lojas_config.json"
if not os.path.exists(config_path):
    print(f"[ERRO] Arquivo de configuracao nao encontrado: {config_path}")
    conn.close()
    exit(1)

with open(config_path, 'r', encoding='utf-8') as f:
    config_list = json.load(f)

# Procurar a loja na lista
api_data = None
for loja_config in config_list:
    if loja_config.get("nome") == LOJA:
        api_data = loja_config.get("integracoes", {}).get("bling", {})
        break

if not api_data or not api_data.get("access_token"):
    print("[ERRO] Token de acesso nao encontrado. Configure a integracao com o Bling.")
    conn.close()
    exit(1)

access_token = api_data["access_token"]
print("[OK] Token obtido")

# Buscar detalhes da NF
print(f"\n{'='*80}")
print(f"Buscando detalhes da NF {numero} na API do Bling...")
print(f"{'='*80}\n")

url = f"https://www.bling.com.br/Api/v3/nfe/{id_bling}"
headers = {"Authorization": f"Bearer {access_token}"}
resp = requests.get(url, headers=headers, timeout=25)
status = resp.status_code

if status != 200:
    print(f"[ERRO] Status {status} ao buscar detalhes")
    print(f"Resposta: {resp.text}")
    conn.close()
    exit(1)

nf_detalhe = resp.json().get("data", {})

print("[OK] Resposta recebida com sucesso!\n")
print(f"{'='*80}")
print("ESTRUTURA COMPLETA DA RESPOSTA:")
print(f"{'='*80}\n")

# Mostrar JSON formatado
print(json.dumps(nf_detalhe, indent=2, ensure_ascii=False))

print(f"\n{'='*80}")
print("ANÁLISE DOS ITENS:")
print(f"{'='*80}\n")

itens = nf_detalhe.get("itens", [])
print(f"Total de itens encontrados: {len(itens)}\n")

if itens:
    for idx, item in enumerate(itens, 1):
        print(f"--- Item {idx} ---")
        print(f"  Estrutura completa do item:")
        print(json.dumps(item, indent=4, ensure_ascii=False))
        
        print(f"\n  Campo 'produto':")
        produto = item.get("produto")
        print(f"    Tipo: {type(produto)}")
        print(f"    Valor: {produto}")
        
        if isinstance(produto, dict):
            print(f"    Chaves disponíveis: {list(produto.keys())}")
            for key in ["codigo", "id", "sku", "descricao", "nome"]:
                val = produto.get(key)
                if val:
                    print(f"    {key}: {val}")
        
        print()
else:
    print("[AVISO] Nenhum item encontrado na resposta!")

conn.close()

print(f"{'='*80}")
print("[OK] Debug completo")
print(f"{'='*80}\n")
