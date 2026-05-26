import sqlite3, os, glob
paths = glob.glob(r'.\info\**\vendas_historico*bak*', recursive=True)
for path in paths:
    print('DB:', path)
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        print('tables=', tables)
        for t in tables:
            cols = [r[1] for r in cur.execute(f'PRAGMA table_info("{t}")').fetchall()]
            print(' ', t, cols)
    except Exception as exc:
        print('ERR', exc)
    finally:
        try: conn.close()
        except: pass
    print()
