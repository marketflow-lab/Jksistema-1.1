import datetime as dt
import sqlite3

from backend.modules.vendas.monthly_coverage import meses_confirmados
from backend.modules.vendas.sync_queue import SYNC_TIMEZONE, SyncQueue


MONTH = ("s1", "2024-02")
START = dt.datetime(2024, 3, 1, 1, tzinfo=SYNC_TIMEZONE).timestamp()


def _fechar_fevereiro(queue, *, loja="s1", database="vendas_historico_loja_a.db", start=START):
    job = queue.enqueue(loja, "Loja A", "2024-02-01", "2024-02-29", now=start - 3)
    claimed = queue.claim("worker", now=start - 2)
    assert claimed["id"] == job["id"]
    for day in range(1, 30):
        queue.complete_day(
            job["id"], "worker", f"2024-02-{day:02d}", {"vendas": 0},
            now=start + day, collection_started_at=start, database_name=database,
        )
    queue.complete_job(job["id"], "worker", now=start + 30)


def test_dias_sem_vendas_fecham_mes_bissexto_reutilizacao_nao_altera_revisao(tmp_path):
    queue = SyncQueue("tenant", tenant_path=tmp_path)
    (tmp_path / "vendas_historico_loja_a.db").touch()
    _fechar_fevereiro(queue)
    result = meses_confirmados(tmp_path, "tenant", now=START + 60)
    assert set(result) == {MONTH}
    assert meses_confirmados(tmp_path, "tenant", "s1", now=START + 60) == result
    assert meses_confirmados(tmp_path, "outro", now=START + 60) == {}
    assert meses_confirmados(tmp_path, "tenant", "", now=START + 60) == {}
    assert meses_confirmados(tmp_path, "tenant", "S1", now=START + 60) == {}
    assert meses_confirmados(tmp_path, "tenant", now=START + 28) == {}
    queue.enqueue("s1", "Novo nome", "2024-02-10", "2024-02-20", now=START + 900)
    assert meses_confirmados(tmp_path, "tenant", now=START + 1200) == result
    forced = queue.enqueue("s1", "Loja A", "2024-02-10", "2024-02-10", forcar_resync=True, now=START + 1300)
    with sqlite3.connect(queue.path) as conn:
        conn.execute("INSERT INTO days VALUES (?,?,?,?,?,?)", (
            forced["id"], "2024-02-10", "{}", START + 1400, START + 1390, "vendas_historico_loja_a.db",
        ))
    revised = meses_confirmados(tmp_path, "tenant", now=START + 1500)
    assert revised[MONTH] != result[MONTH]
    queue.bind_store_source("s1", "a" * 64, now=START + 1500)
    assert meses_confirmados(tmp_path, "tenant", now=START + 1600)[MONTH] != revised[MONTH]
    queue.bind_store_source("s1", "b" * 64, now=START + 1600)
    assert meses_confirmados(tmp_path, "tenant", now=START + 1700) == {}


def test_ultimo_banco_perdido_invalida_mes_e_preserva_outra_loja(tmp_path):
    queue = SyncQueue("tenant", tenant_path=tmp_path)
    path_a = tmp_path / "vendas_historico_loja_a.db"
    path_a.touch()
    (tmp_path / "vendas_historico_loja_b.db").touch()
    _fechar_fevereiro(queue)
    _fechar_fevereiro(queue, loja="s2", database="vendas_historico_loja_b.db", start=START + 100)
    initial = meses_confirmados(tmp_path, "tenant", now=START + 170)
    assert set(initial) == {MONTH, ("s2", "2024-02")}
    # The older receipt still has a valid file; the newer committed source
    # went missing for this one day, so the whole month must be reopened.
    new_job = queue.enqueue("s1", "Loja A", "2024-02-08", "2024-02-08", forcar_resync=True, now=START + 175)
    assert queue.claim("worker", now=START + 176)["id"] == new_job["id"]
    queue.complete_day(new_job["id"], "worker", "2024-02-08", {"vendas": 0}, now=START + 182,
                       collection_started_at=START + 178, database_name="vendas_historico_perdido.db")
    assert meses_confirmados(tmp_path, "tenant", now=START + 200) == {
        ("s2", "2024-02"): initial["s2", "2024-02"],
    }


def test_fila_legada_usa_inicio_do_job_e_fuso_sao_paulo(tmp_path):
    database = tmp_path / "vendas_historico_loja_a.db"
    database.touch()
    path = tmp_path / "vendas_sync_queue.db"
    during_last_day = dt.datetime(2024, 3, 1, 1, tzinfo=dt.timezone.utc).timestamp()
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE tenant (client_id TEXT);
            INSERT INTO tenant VALUES ('tenant');
            CREATE TABLE jobs (id TEXT, store_id TEXT, loja TEXT, created_at REAL);
            CREATE TABLE days (job_id TEXT, day TEXT, completed_at REAL);
        """)
        conn.execute("INSERT INTO jobs VALUES ('a', 's1', 'Loja A', ?)", (during_last_day,))
        conn.executemany("INSERT INTO days VALUES ('a', ?, ?)", [
            (f"2024-02-{d:02d}", START + 30) for d in range(1, 30)
        ])
    assert meses_confirmados(tmp_path, "tenant", now=START + 90) == {}
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE jobs SET created_at=?", (START,))
    assert set(meses_confirmados(tmp_path, "tenant", now=START + 90)) == {MONTH}
    database.unlink()
    (tmp_path / "vendas_historico_loja_b.db").touch()
    assert meses_confirmados(tmp_path, "tenant", now=START + 90) == {}
    with sqlite3.connect(tmp_path / "vendas_historico.db") as conn:
        conn.execute("CREATE TABLE vendas (store_id TEXT)")
        conn.execute("INSERT INTO vendas VALUES ('s2')")
    assert meses_confirmados(tmp_path, "tenant", now=START + 90) == {}
    with sqlite3.connect(tmp_path / "vendas_historico.db") as conn:
        conn.execute("INSERT INTO vendas VALUES ('s1')")
    assert set(meses_confirmados(tmp_path, "tenant", now=START + 90)) == {MONTH}


def test_sem_fila_nao_cria_banco_ou_diretorio(tmp_path):
    root = tmp_path / "ausente"
    assert meses_confirmados(root, "tenant") == {}
    assert not root.exists()
