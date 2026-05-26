import sqlite3

db = 'info/000002/vendas_historico.db'
conn = sqlite3.connect(db)
cur = conn.cursor()

cols = [r[1] for r in cur.execute('PRAGMA table_info(notas_entrada_itens)').fetchall()]
print('Colunas notas_entrada_itens:', cols)
print()

total = cur.execute('SELECT COUNT(*) FROM notas_entrada_itens WHERE devolucao=1').fetchone()[0]
print(f'Total devolicoes: {total}')
print()

rows = cur.execute(
    "SELECT loja_conta, COUNT(*) FROM notas_entrada_itens WHERE devolucao=1 GROUP BY loja_conta ORDER BY COUNT(*) DESC"
).fetchall()
print('Por loja_conta (raw):')
for r in rows:
    print(f'  loja_conta={repr(r[0])}  count={r[1]}')

print()
rows2 = cur.execute(
    "SELECT unidade_negocio, COUNT(*) FROM notas_entrada_itens WHERE devolucao=1 GROUP BY unidade_negocio ORDER BY COUNT(*) DESC LIMIT 20"
).fetchall()
print('Por unidade_negocio:')
for r in rows2:
    print(f'  unidade={repr(r[0])}  count={r[1]}')

print()
rows3 = cur.execute(
    "SELECT unidade_negocio_virtual, COUNT(*) FROM notas_entrada_itens WHERE devolucao=1 GROUP BY unidade_negocio_virtual ORDER BY COUNT(*) DESC LIMIT 20"
).fetchall()
print('Por unidade_negocio_virtual:')
for r in rows3:
    print(f'  virtual={repr(r[0])}  count={r[1]}')

print()
# Verificar se o loja_conta e campo na tabela notas_entrada (pai)
rows4 = cur.execute(
    "SELECT loja_conta, COUNT(*) FROM notas_entrada WHERE devolucao=1 GROUP BY loja_conta ORDER BY COUNT(*) DESC"
).fetchall()
print('notas_entrada (pai) por loja_conta:')
for r in rows4:
    print(f'  loja_conta={repr(r[0])}  count={r[1]}')

conn.close()
