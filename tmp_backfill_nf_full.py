import json
import sqlite3
import time
from pathlib import Path
import requests

base = Path(r"c:/Users/Trend/Desktop/app-de-gest-o---jksistema-1.0-ver/V7 - Copia")
db = base / "info/000002/vendas_historico.db"
cfg_path = base / "info/000002/lojas_config.json"

cfg_raw = json.loads(cfg_path.read_text(encoding="utf-8"))
lojas = cfg_raw if isinstance(cfg_raw, list) else []

token_por_loja = {}
for l in lojas:
    nome = str(l.get("nome") or "").strip()
    bling = (l.get("integracoes") or {}).get("bling") or {}
    tok = str(bling.get("access_token") or "").strip()
    if nome and tok:
        token_por_loja[nome] = tok

conn = sqlite3.connect(db)
cur = conn.cursor()
sess = requests.Session()

def pendentes_total():
    return cur.execute("SELECT COUNT(*) FROM vendas WHERE COALESCE(TRIM(nota_fiscal_id),'')!='' AND COALESCE(TRIM(numero_nf),'')='' ").fetchone()[0]

antes = pendentes_total()
print("PENDENTES_INICIAIS", antes)

atualizados_geral = 0
falhas_geral = 0
sem_progresso = 0
max_rodadas = 20
lote_ids = 1200

for rodada in range(1, max_rodadas + 1):
    pendentes = cur.execute(f'''
    SELECT loja_conta, nota_fiscal_id, MAX(date(data)) as dt
    FROM vendas
    WHERE COALESCE(TRIM(nota_fiscal_id),'')!='' AND COALESCE(TRIM(numero_nf),'')=''
    GROUP BY loja_conta, nota_fiscal_id
    ORDER BY dt DESC
    LIMIT {lote_ids}
    ''').fetchall()

    if not pendentes:
        print("SEM_PENDENTES")
        break

    atualizados_rodada = 0
    falhas_rodada = 0

    for i, (loja, nf_id, _dt) in enumerate(pendentes, 1):
        token = token_por_loja.get(str(loja or "").strip())
        if not token:
            falhas_rodada += 1
            continue

        headers = {"Authorization": f"Bearer {token}"}
        numero = ""
        for tentativa in range(5):
            try:
                r = sess.get(f"https://api.bling.com.br/Api/v3/nfe/{nf_id}", headers=headers, timeout=20)
                if r.status_code == 429 and tentativa < 4:
                    time.sleep(0.8 * (tentativa + 1))
                    continue
                if r.status_code == 200:
                    data = (r.json() or {}).get("data") or {}
                    numero = str(data.get("numero") or "").strip()
                break
            except Exception:
                if tentativa < 4:
                    time.sleep(0.6 * (tentativa + 1))

        if numero:
            cur.execute(
                "UPDATE vendas SET numero_nf = ? WHERE loja_conta = ? AND nota_fiscal_id = ? AND COALESCE(TRIM(numero_nf),'') = ''",
                (numero, loja, str(nf_id))
            )
            atualizados_rodada += cur.rowcount
        else:
            falhas_rodada += 1

        if i % 150 == 0:
            conn.commit()
            time.sleep(0.15)

    conn.commit()
    atualizados_geral += atualizados_rodada
    falhas_geral += falhas_rodada
    restantes = pendentes_total()

    print(
        "RODADA", rodada,
        "ATUALIZADOS", atualizados_rodada,
        "FALHAS", falhas_rodada,
        "RESTANTES", restantes
    )

    if atualizados_rodada == 0:
        sem_progresso += 1
    else:
        sem_progresso = 0

    if restantes == 0:
        break
    if sem_progresso >= 2:
        print("PARANDO_SEM_PROGRESSO")
        break

    time.sleep(0.8)

print("ATUALIZADOS_GERAL", atualizados_geral)
print("FALHAS_GERAL", falhas_geral)
print("PENDENTES_FINAIS", pendentes_total())
conn.close()
