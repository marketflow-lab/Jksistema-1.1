import sqlite3
import os
import json
from datetime import datetime, timedelta

# Carregar config para pegar client_id
config_path = "info/config_sheet.json"
if os.path.exists(config_path):
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
        client_id = config.get('client_id', '000002')
else:
    client_id = '000002'

# Conectar ao banco
db_path = f"info/{client_id}/vendas_historico.db"
if not os.path.exists(db_path):
    print(f"❌ Banco de dados não encontrado: {db_path}")
    exit(1)

print(f"📂 Conectando ao banco: {db_path}\n")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Verificar se a tabela existe
tabelas = cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("📋 Tabelas no banco:")
for t in tabelas:
    print(f"  - {t[0]}")

# Verificar notas_entrada_itens
print("\n" + "=" * 60)
print("NOTAS FISCAIS DE ENTRADA (DEVOLUÇÕES)")
print("=" * 60)

if 'notas_entrada_itens' in [t[0] for t in tabelas]:
    total_notas = cur.execute("SELECT COUNT(*) FROM notas_entrada_itens").fetchone()[0]
    print(f"\n📦 Total de itens: {total_notas}")
    
    devolucoes = cur.execute("SELECT COUNT(*) FROM notas_entrada_itens WHERE devolucao = 1").fetchone()[0]
    print(f"📉 Total de devoluções (devolucao=1): {devolucoes}")
    
    if devolucoes > 0:
        print("\n🔍 Primeiras 5 devoluções:")
        items = cur.execute("""
            SELECT data_emissao, numero_nota, sku, descricao, quantidade, valor_total, natureza_operacao
            FROM notas_entrada_itens
            WHERE devolucao = 1
            ORDER BY data_emissao DESC
            LIMIT 5
        """).fetchall()
        for item in items:
            print(f"  📅 {item[0]} | NF: {item[1]} | SKU: {item[2]} | {item[3]}")
            print(f"     Qtd: {item[4]} | Valor: R$ {item[5]:.2f} | Natureza: {item[6]}")
        
        # Dados para o gráfico
        data_inicio = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
        data_fim = datetime.now().strftime('%Y-%m-%d')
        
        print(f"\n📊 DADOS PARA GRÁFICO ({data_inicio} até {data_fim}):")
        grafico_dev = cur.execute("""
            SELECT 
                date(data_emissao) as dia,
                SUM(valor_total) as total_valor,
                SUM(quantidade) as total_qtd,
                COUNT(*) as num_itens
            FROM notas_entrada_itens
            WHERE date(data_emissao) BETWEEN ? AND ?
            AND devolucao = 1
            GROUP BY date(data_emissao)
            ORDER BY date(data_emissao) DESC
            LIMIT 10
        """, [data_inicio, data_fim]).fetchall()
        
        if grafico_dev:
            for row in grafico_dev:
                print(f"  📅 {row[0]}: R$ {row[1]:.2f} ({row[2]:.0f} unid., {row[3]} itens)")
        else:
            print("  ❌ Nenhuma devolução no período dos últimos 90 dias")
    else:
        print("\n⚠️  Nenhuma devolução encontrada! Naturezas presentes:")
        naturezas = cur.execute("""
            SELECT DISTINCT natureza_operacao, COUNT(*) as qtd
            FROM notas_entrada_itens
            GROUP BY natureza_operacao
        """).fetchall()
        for nat in naturezas:
            print(f"  - {nat[0]}: {nat[1]} itens")
else:
    print("❌ Tabela 'notas_entrada_itens' não existe!")

conn.close()
print("\n" + "=" * 60)
print("✅ Verificação concluída!")
print("=" * 60)
