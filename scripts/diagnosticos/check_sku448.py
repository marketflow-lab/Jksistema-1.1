import sqlite3

conn = sqlite3.connect('info/000002/vendas_historico_jk_pecas.db')
cur = conn.cursor()

print('=== Dias das NFs faltantes: o que estava no banco antes do ML Full ===')
for dia in ['2025-11-04', '2025-11-05', '2025-11-12', '2025-11-18']:
    cur.execute(
        'SELECT COUNT(*), '
        'COUNT(CASE WHEN canal LIKE "%Full%" THEN 1 END), '
        'GROUP_CONCAT(DISTINCT canal) '
        'FROM vendas WHERE date(data)=?',
        (dia,)
    )
    total, full, canais = cur.fetchone()
    status = "BLOQUEADO pelo skip-data (had records, ML Full=0)" if total > 0 and full == 0 else f"ML Full={full}"
    print(f'  {dia}: total={total}, ML_Full={full} → {status}')
    print(f'    Canais: {canais}')

conn.close()



conn.close()




