import os, sqlite3
base = r'.\info\000002\vendas_historico.db.bak_20260402_073047'
for path in [base, r'.\info\000002\vendas_historico_jk_pecas.db']:
    print('DB:', path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print('tables=', tables)
    for t in tables:
        cols = [r[1] for r in cur.execute(f'PRAGMA table_info("{t}")').fetchall()]
        print(' ', t, cols)
    conn.close()
    print()
