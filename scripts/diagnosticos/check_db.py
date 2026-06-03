
import sqlite3
import pandas as pd

db_path = 'info/vendas_historico.db'
conn = sqlite3.connect(db_path)
try:
    df = pd.read_sql_query("SELECT * FROM vendas ORDER BY data DESC LIMIT 5", conn)
    print(df)
except Exception as e:
    print(f"Error reading database: {e}")
finally:
    conn.close()
