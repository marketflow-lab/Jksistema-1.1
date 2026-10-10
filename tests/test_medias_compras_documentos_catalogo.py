"""Document lookup precedence, legacy recovery and store isolation."""

import copy
import csv

import pytest

from backend.services import cadastro_compatibilidade, medias_compras_common as common
from backend.services import medias_compras_documentos_cadastro as documentos


def _csv(path, rows):
    campos = list(dict.fromkeys(chave for row in rows for chave in row))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=campos)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def catalogo(tmp_path, monkeypatch):
    pasta = tmp_path / "cliente-a"
    pasta.mkdir()
    monkeypatch.setattr(common, "get_tenant_path", lambda client: str(tmp_path / client))
    monkeypatch.setattr(common, "_resolver_foto_cadastro_sku", lambda _client, _sku, foto, _store: foto)
    return pasta


def _contexto(monkeypatch, produtos, **overrides):
    contexto = {"scope": "store", "store_id": "loja-a", "produtos": produtos}
    contexto.update(overrides)
    monkeypatch.setattr(cadastro_compatibilidade, "visao_produtos_cadastro_contexto_loja",
                        lambda *_args: contexto)
    return contexto


def _consultar(sku="SKU:380 Switch Panel"):
    contexto = documentos._contexto_cadastro_documentos(
        {"store_id": "loja-a", "itens": [{"SKU": sku}]}, "cliente-a",
    )
    return documentos._dados_produto_documento(contexto, sku)


def test_metadados_atuais_da_loja_prevalecem_sobre_lista_e_historico(catalogo, monkeypatch):
    produtos = [{"sku": "380", "store_id": "loja-a", "cg_product description": "Current full technical description",
                 "Título do produto em inglês": "Short name", "foto": "lojas/a/380.png", "Unit/Pair": "Pair"},
                {"sku": "380", "store_id": "loja-b", "cg_product description": "Other store description"}]
    antes = copy.deepcopy(produtos)
    _contexto(monkeypatch, produtos)
    _csv(catalogo / "cadastro_produtos.backup.csv", [{"sku": "380", "cg_product description": "Old technical description"}])
    assert _consultar() == {"sku": "380", "descricao": "Current full technical description",
                           "foto": "lojas/a/380.png", "unidade": "Pair"}
    assert produtos == antes
    produtos[0]["cg_product description"] = "Updated registered description"
    assert _consultar()["descricao"] == "Updated registered description"


def test_sombra_legada_recupera_descricao_tecnica_completa_do_proprio_cliente(catalogo, monkeypatch):
    _contexto(monkeypatch, [{"sku": "380", "scope_source": "legacy_shadow", "loja_sync": "Loja A",
                           "cg_product name": "Current short name", "foto": "380.png"}], skus_controlados={"380"})
    _csv(catalogo / "cadastro_produtos.csv", [{"sku": "380", "produto_bling": "Generic name"}])
    _csv(catalogo / "cadastro_produtos.backup_classificacao_unicode.csv", [
        {"sku": "380", "cg_product description": "Supplier technical specification, material and size"}])
    antes = {path.name: path.read_bytes() for path in catalogo.iterdir()}
    assert _consultar()["descricao"] == "Supplier technical specification, material and size"
    assert _consultar()["foto"] == "380.png"
    assert {path.name: path.read_bytes() for path in catalogo.iterdir()} == antes


def test_campo_apagado_no_cadastro_atual_nao_e_restaurado_do_historico(catalogo, monkeypatch):
    _contexto(monkeypatch, [{"sku": "380", "scope_source": "store_file", "cg_product description": "", "foto": ""}])
    _csv(catalogo / "cadastro_produtos.backup.csv", [{"sku": "380", "cg_product description": "Deleted description", "foto": "old.png"}])
    assert _consultar()["descricao"] == ""
    assert _consultar()["foto"] == ""


def test_id_exato_da_loja_prevalece_sobre_nome_historico(catalogo, monkeypatch):
    _contexto(monkeypatch, [{"sku": "380", "scope_source": "legacy_shadow", "loja_sync": "Current name"}])
    _csv(catalogo / "cadastro_produtos.backup.csv", [
        {"sku": "380", "store_id": "loja-a", "loja_sync": "Old name", "cg_product description": "Correct exact store"}])
    assert _consultar()["descricao"] == "Correct exact store"


def test_historico_de_outra_loja_e_default_nao_vazam(catalogo, monkeypatch):
    _contexto(monkeypatch, [{"sku": "380", "scope_source": "legacy_shadow", "loja_sync": "Loja A"}])
    _csv(catalogo / "cadastro_produtos.backup.csv", [
        {"sku": "380", "store_id": "loja-b", "cg_product description": "Wrong store"}])
    outra = catalogo.parent / "default"
    outra.mkdir()
    _csv(outra / "cadastro_produtos.csv", [{"sku": "380", "cg_product description": "Wrong tenant"}])
    assert _consultar()["descricao"] == ""


@pytest.mark.parametrize("scope", ["unresolved", "unavailable"])
def test_contexto_ausente_nao_reutiliza_lista_ou_historico(catalogo, monkeypatch, scope):
    _contexto(monkeypatch, [{"sku": "380", "cg_product description": "Unscoped content"}], scope=scope)
    _csv(catalogo / "cadastro_produtos.csv", [{"sku": "380", "cg_product description": "Unscoped history"}])
    assert _consultar() == {"sku": "", "descricao": "", "foto": "", "unidade": ""}


def test_projecao_global_nao_restabelece_descricao_conflitante(catalogo, monkeypatch):
    _contexto(monkeypatch, [{"sku": "380", "cg_product description": ""}],
              scope="global_safe", store_id="", skus_controlados={"380"})
    _csv(catalogo / "cadastro_produtos.backup.csv", [{"sku": "380", "cg_product description": "Unsafe global fallback"}])
    assert _consultar()["descricao"] == ""


def test_alias_sku_nao_confunde_sufixos_e_resolve_somente_itens_solicitados(catalogo, monkeypatch):
    produtos = [{"sku": sku, "cg_product description": sku, "foto": sku + ".png"}
                for sku in ("1", "375-1", "375-2", "380")]
    _contexto(monkeypatch, produtos)
    chamados = []
    monkeypatch.setattr(common, "_resolver_foto_cadastro_sku",
                        lambda _client, sku, foto, _store: chamados.append(sku) or foto)
    contexto = documentos._contexto_cadastro_documentos(
        {"store_id": "loja-a", "itens": [{"SKU": "SKU:001 Sensor"}, {"SKU": "SKU:375-2 Light"}]}, "cliente-a")
    assert documentos._dados_produto_documento(contexto, "001")["descricao"] == "1"
    assert documentos._dados_produto_documento(contexto, "375-2")["descricao"] == "375-2"
    assert documentos._dados_produto_documento(contexto, "375-1")["descricao"] == ""
    assert chamados == ["1", "375-2"]
