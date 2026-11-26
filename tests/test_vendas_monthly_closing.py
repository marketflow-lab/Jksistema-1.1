"""Exercise the background close from an actual Vendas queue and sales database."""

from datetime import datetime, timezone
from threading import Event
from unittest.mock import Mock

from backend.modules.vendas import monthly_closing
from backend.modules.vendas.monthly_coverage import meses_confirmados
from backend.modules.vendas.sync_queue import SyncQueue
from backend.services import medias_compras_fechamento, store_listing_service
from test_medias_historico_projecao import _banco


def test_ultimo_dia_confirmado_prepara_mes_sem_abrir_medias(tmp_path, monkeypatch):
    tenant, store, loja = "tenant-a", "loja-a", "Loja A"
    root = tmp_path / tenant
    queue = SyncQueue(tenant, tenant_path=root)
    banco = root / "vendas_historico_loja_a.db"
    _banco(banco, [("pedido", "SKU", "2024-02-03", "Produto", 5, loja)])
    monkeypatch.setattr(monthly_closing, "get_tenant_path", lambda _: root)
    monkeypatch.setattr(medias_compras_fechamento, "raiz_tenant", lambda _: root)
    monkeypatch.setattr(monthly_closing, "_listar_bancos_vendas_tenant", lambda *_: [str(banco)])
    monkeypatch.setattr(store_listing_service, "read_store_cards",
                        lambda _: {"lojas": [{"store_id": store, "nome": loja}]})

    now = datetime(2024, 3, 2, tzinfo=timezone.utc).timestamp()
    job = queue.enqueue(store, loja, "2024-02-01", "2024-02-29", now=now - 2)
    queue.claim("coleta", now=now - 1)
    for day in range(1, 29):
        queue.complete_day(job["id"], "coleta", f"2024-02-{day:02d}", {"vendas": int(day == 3)},
                           now=now, collection_started_at=now - 1, database_name=banco.name)
    monthly_closing.preparar_cliente(tenant, Event())
    assert not meses_confirmados(root, tenant)

    queue.complete_day(job["id"], "coleta", "2024-02-29", {"vendas": 0}, now=now,
                       collection_started_at=now - 1, database_name=banco.name)
    monthly_closing.preparar_cliente(tenant, Event())
    prova = meses_confirmados(root, tenant)[store, "2024-02"]
    assert medias_compras_fechamento.fechamentos_disponiveis(
        client_id=tenant, loja=loja, store_id=store, meses={"2024-02": prova}, db_paths=[str(banco)],
    ) == {"2024-02"}

    gravar = Mock()
    monkeypatch.setattr(medias_compras_fechamento, "preparar_fechamentos", gravar)
    monthly_closing.preparar_cliente(tenant, Event())
    gravar.assert_not_called()
