import glob
import os
import sqlite3

base = os.path.join("info", "000002")
files = sorted(glob.glob(os.path.join(base, "vendas_historico*.db")))
print("DB files:", [os.path.basename(f) for f in files])

for f in files:
    conn = sqlite3.connect(f)
    cur = conn.cursor()

    def q(sql):
        try:
            return cur.execute(sql).fetchone()[0]
        except Exception:
            return None

    vendas_total = q("select count(*) from vendas")
    vendas_nao_dev = q("select count(*) from vendas where coalesce(devolucao,0)=0")
    notas_dev = q("select count(*) from notas_entrada_itens where coalesce(devolucao,0)=1")

    try:
        lojas = cur.execute("select coalesce(loja_conta, '[vazio]'), count(*) from vendas group by loja_conta order by count(*) desc").fetchall()
    except Exception:
        lojas = []

    print("\n" + os.path.basename(f))
    print("  vendas_total=", vendas_total, " vendas_nao_devolucao=", vendas_nao_dev, " notas_entrada_devolucao=", notas_dev)
    print("  lojas_vendas=", lojas[:5])
    conn.close()
