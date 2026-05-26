import sqlite3
import os
import json

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

# 1. Verificar estrutura da tabela
print("=" * 60)
print("1. ESTRUTURA DA TABELA 'vendas'")
print("=" * 60)
cols = cur.execute("PRAGMA table_info(vendas)").fetchall()
for col in cols:
    print(f"  - {col[1]} ({col[2]})")

# 2. Total de registros
print("\n" + "=" * 60)
print("2. TOTAL DE REGISTROS")
print("=" * 60)
total = cur.execute("SELECT COUNT(*) FROM vendas").fetchone()[0]
print(f"  Total de vendas: {total}")

# 3. Registros com devolucao = 1
print("\n" + "=" * 60)
print("3. DEVOLUÇÕES (devolucao = 1)")
print("=" * 60)
devolucoes_count = cur.execute("SELECT COUNT(*) FROM vendas WHERE devolucao = 1").fetchone()[0]
print(f"  Total de devoluções: {devolucoes_count}")

if devolucoes_count > 0:
    print("\n  📋 Primeiras 5 devoluções:")
    devolucoes = cur.execute("""
        SELECT data, numero, situacao, sku, produto, valor, devolucao 
        FROM vendas 
        WHERE devolucao = 1 
        LIMIT 5
    """).fetchall()
    for dev in devolucoes:
        print(f"    - Data: {dev[0]}, Nº: {dev[1]}, Situação: {dev[2]}, SKU: {dev[3]}, Valor: R$ {dev[5]:.2f}")

# 4. Situações que contêm "devol"
print("\n" + "=" * 60)
print("4. SITUAÇÕES COM 'DEVOL' NO NOME")
print("=" * 60)
situacoes_devol = cur.execute("""
    SELECT DISTINCT situacao, COUNT(*) as qtd, SUM(valor) as valor_total
    FROM vendas 
    WHERE LOWER(situacao) LIKE '%devol%'
    GROUP BY situacao
""").fetchall()

if situacoes_devol:
    for sit in situacoes_devol:
        print(f"  - '{sit[0]}': {sit[1]} registros, R$ {sit[2]:.2f}")
else:
    print("  ❌ Nenhuma situação contendo 'devol' encontrada")

# 5. Todas as situações únicas
print("\n" + "=" * 60)
print("5. TODAS AS SITUAÇÕES ÚNICAS")
print("=" * 60)
todas_situacoes = cur.execute("""
    SELECT DISTINCT situacao, COUNT(*) as qtd, SUM(valor) as valor_total
    FROM vendas 
    GROUP BY situacao
    ORDER BY qtd DESC
""").fetchall()

for sit in todas_situacoes[:10]:  # Mostrar top 10
    print(f"  - '{sit[0]}': {sit[1]} registros, R$ {sit[2]:.2f}")

# 6. Verificar valores no gráfico
print("\n" + "=" * 60)
print("6. DADOS PARA GRÁFICO (últimos 90 dias)")
print("=" * 60)
from datetime import datetime, timedelta

data_inicio = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
data_fim = datetime.now().strftime('%Y-%m-%d')

print(f"  Período: {data_inicio} até {data_fim}\n")

# Vendas (devolucao = 0)
vendas = cur.execute("""
    SELECT 
        date(data) as dia,
        SUM(valor) as total_valor,
        COUNT(*) as qtd
    FROM vendas
    WHERE date(data) BETWEEN ? AND ?
    AND devolucao = 0
    GROUP BY date(data)
    ORDER BY date(data) DESC
    LIMIT 5
""", [data_inicio, data_fim]).fetchall()

print("  📈 VENDAS (devolucao = 0):")
if vendas:
    for v in vendas:
        print(f"    - {v[0]}: R$ {v[1]:.2f} ({v[2]} registros)")
else:
    print("    ❌ Nenhuma venda encontrada")

# Devoluções (devolucao = 1)
devolucoes = cur.execute("""
    SELECT 
        date(data) as dia,
        SUM(valor) as total_valor,
        COUNT(*) as qtd
    FROM vendas
    WHERE date(data) BETWEEN ? AND ?
    AND devolucao = 1
    GROUP BY date(data)
    ORDER BY date(data) DESC
    LIMIT 5
""", [data_inicio, data_fim]).fetchall()

print("\n  📉 DEVOLUÇÕES (devolucao = 1):")
if devolucoes:
    for d in devolucoes:
        print(f"    - {d[0]}: R$ {d[1]:.2f} ({d[2]} registros)")
else:
    print("    ❌ Nenhuma devolução encontrada no período")

conn.close()

print("\n" + "=" * 60)
print("✅ Diagnóstico concluído!")
print("=" * 60)
