"""Request-local reuse keeps parser semantics and never survives the request."""

import asyncio
from pathlib import Path

from backend.services import medias_compras_leitura as leitura
from backend.services import cadastro_fotos as fotos
from backend.services import cadastro_lojas_produtos as produtos
from backend.services import cadastro_compatibilidade as cadastro
from backend.services import medias_compras_listas as listas
from backend.services import medias_compras_fiscal as fiscal
from backend.services import impostos_ncm
from test_medias_compras_carregamento import ambiente


def test_bytes_once_parser_options_preserved_next_request_sees_changes(tmp_path, monkeypatch):
    file = tmp_path / "catalog.csv"
    file.write_text("sku,valor\n001,1000.0\n", encoding="utf-8")
    reads = []
    original = Path.read_bytes
    def read(path):
        reads.append(path)
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    with leitura.leitura_medias() as snapshot:
        text = leitura.ler_csv(file, dtype=str)
        inferred = leitura.ler_csv(file)
        text.iloc[0, 0] = "changed"
        assert leitura.ler_csv(file, dtype=str).iloc[0, 0] == "001"
        assert inferred.iloc[0, 0] == 1
        assert isinstance(inferred.iloc[0, 1], float)
        assert snapshot.metrics["leituras_fisicas"] == 1
        assert snapshot.metrics["csv_parses"] == 2
        assert snapshot.metrics["csv_copias"] == 3
        assert all(snapshot.metrics[key] >= 0 for key in ("csv_leitura", "csv_parse", "csv_copia"))
    assert reads == [file]
    file.write_text("sku,valor\n002,5.0\n", encoding="utf-8")
    with leitura.leitura_medias():
        assert leitura.ler_csv(file, dtype=str).iloc[0, 0] == "002"
    assert reads == [file, file]


def test_tax_and_description_share_one_reference_read(monkeypatch):
    calls = []
    def ensure(client):
        calls.append(("ensure", client))
    class Connection:
        def execute(self, _query):
            calls.append(("select",))
            return self
        def fetchall(self):
            return [{"ncm": "8708.99.90", "cest": "01.001.00", "descricao": "short", "descricao_completa": "complete", "ii": 12, "ipi": 5, "pis": 2, "cofins": 9}]
        def close(self):
            calls.append(("close",))
    def connect(_client):
        return Connection()
    for module in (fiscal, impostos_ncm):
        monkeypatch.setattr(module, "_garantir_base_ncm_populada", ensure)
        monkeypatch.setattr(module, "_conexao_db_ncm_impostos", connect)
    with leitura.leitura_medias():
        assert fiscal._indice_ncm_referencia_aliquotas("a")["87089990"]["ii"] == 12
        assert impostos_ncm._indice_ncm_referencia_por_ncm("a")["87089990"]["descricao_completa"] == "complete"
    assert calls == [("ensure", "a"), ("select",), ("close",)]


def test_detail_photo_policy_captured_once_and_closed_before_write(ambiente, monkeypatch):
    calls = []
    original = fotos._cadastro_fotos_config_ler
    original_save = listas._salvar_listas_pedidos
    def config(client, **kwargs):
        calls.append(client)
        return original(client, **kwargs)
    def save(*args):
        assert fotos._CADASTRO_FOTOS_LEITURA.get() is None
        return original_save(*args)
    monkeypatch.setattr(fotos, "_cadastro_fotos_config_ler", config)
    monkeypatch.setattr(listas, "_salvar_listas_pedidos", save)
    result = listas._api_medias_compras_lista_pedido_detalhe_sync("lista", client_id="cliente-a")
    assert result["lista"]["itens"][0]["Foto"] == ambiente["refs"]["cliente-a", "store-a"]
    assert calls == ["cliente-a"]
    assert leitura.leitura_atual() is None


def test_global_legacy_index_is_built_once_for_multiple_stores(ambiente, monkeypatch):
    calls = []
    original = produtos._indice_legado_de_tenant
    def index(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(produtos, "_indice_legado_de_tenant", index)
    baseline = cadastro.visao_compatibilidade_produtos_lojas("cliente-a")
    assert len(calls) == 2
    calls.clear()
    with leitura.leitura_medias():
        result = cadastro.visao_compatibilidade_produtos_lojas("cliente-a")
    assert len(calls) == 1
    assert result == baseline
