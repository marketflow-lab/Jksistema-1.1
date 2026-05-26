import sqlite3
import os
import pandas as pd

# 1. Verificar CSV
csv_path = r'info/000002/produtos_compilado.csv'
print("=" * 60)
print("CSV COMPILADO")
print("=" * 60)
df = pd.read_csv(csv_path)
resultado = df[df['sku'].astype(str).str.strip().str.upper() == '254-1']
if len(resultado) > 0:
    print("SKU 254-1 no CSV:")
    print(f"  saldo_loja: {resultado.iloc[0]['saldo_loja']}")
    print(f"  saldo_full: {resultado.iloc[0]['saldo_full']}")

# 2. Verificar banco de produtos
db_path = r'info/000002/produtos.db'
if os.path.exists(db_path):
    print("\n" + "=" * 60)
    print("BANCO DE PRODUTOS (produtos.db)")
    print("=" * 60)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Ver tabelas
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tabelas = [t[0] for t in cur.fetchall()]
    print(f"Tabelas: {tabelas}")
    
    # Ver info do SKU 254-1
    if 'produtos' in tabelas:
        cur.execute("PRAGMA table_info(produtos)")
        colunas = [c[1] for c in cur.fetchall()]
        print(f"Colunas em produtos: {colunas}")
        
        cur.execute("SELECT sku, saldo_loja FROM produtos WHERE sku='254-1'")
        dados = cur.fetchone()
        if dados:
            print(f"SKU 254-1 no BD: {dados}")
    
    conn.close()

# 3. Verificar estoque.py para entender a sincronização
print("\n" + "=" * 60)
print("ANALISANDO estoque.py")
print("=" * 60)
with open('estoque.py', 'r', encoding='utf-8') as f:
    content = f.read()
    if 'saldo_loja' in content:
        print("✓ estoque.py usa 'saldo_loja'")
    if 'to_csv' in content:
        print("✓ estoque.py exporta para CSV")
