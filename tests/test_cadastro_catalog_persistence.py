import base64
import io
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

from backend.services import (
    cadastro_custos,
    cadastro_fotos,
    cadastro_importacao_catalogos,
    cadastro_lojas_produtos,
    integracoes,
)


@pytest.fixture
def cadastro_runtime(monkeypatch, tmp_path):
    info_root = tmp_path / "info"
    stores = {"cliente-a": [{"store_id": "store-a", "nome": "Loja A"}]}

    def tenant_path(client_id):
        path = info_root / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(cadastro_lojas_produtos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_custos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(info_root), raising=False)
    monkeypatch.setattr(integracoes, "carregar_lojas", lambda client_id: stores.get(client_id, []))
    return info_root, stores


def test_derived_bling_fields_require_privileged_batch_allowlist(cadastro_runtime):
    cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
        "cliente-a",
        "store-a",
        [{"sku": "001", "id_bling": "nao-persistir", "produto_bling": "Ignorado"}],
    )
    first = cadastro_lojas_produtos._obter_produto_loja_sync("cliente-a", "store-a", "001")
    assert first.get("id_bling", "") == ""
    assert first.get("produto_bling", "") == ""

    cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
        "cliente-a",
        "store-a",
        [
            {
                "sku": "001",
                "row_version": first["row_version"],
                "id_bling": "123",
                "produto_bling": "Produto Bling",
            }
        ],
        campos_derivados_permitidos={"id_bling", "produto_bling"},
    )
    persisted = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "001"
    )
    assert persisted["id_bling"] == "123"
    assert persisted["produto_bling"] == "Produto Bling"


def test_catalogo_ml_salva_capa_comprimida_e_descricao_na_pasta_da_loja(
    cadastro_runtime,
    monkeypatch,
):
    info_root, _stores = cadastro_runtime
    source_image = Image.effect_noise((1400, 1000), 100).convert("RGB")
    source_buffer = io.BytesIO()
    source_image.save(source_buffer, format="PNG")
    image_bytes = source_buffer.getvalue()
    data_url = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
    monkeypatch.setattr(
        cadastro_importacao_catalogos.cadastro_ml,
        "_download_photo_data_url",
        lambda *_args: {"data_url": data_url, "filename": "MLB123.png"},
    )

    result = cadastro_importacao_catalogos._salvar_payloads_importacao_catalogo(
        "cliente-a",
        "store-a",
        "mercadolivre",
        [{
            "sku": "CAPA-ML",
            "row_version": 0,
            "__expected_scope": "absent",
            "mlb_principal": "MLB123",
            "descricao": "Descricao completa do anuncio no Mercado Livre.",
            "foto_url_ml": "https://http2.mlstatic.com/capa.png",
            "__catalog_photo_plan": {
                "url": "https://http2.mlstatic.com/capa.png",
                "item_id": "MLB123",
            },
        }],
        campos_derivados_permitidos=set(),
        precommit_validator=lambda _store: None,
    )

    produto = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "CAPA-ML"
    )
    assert result["fotos_salvas"] == 1
    assert produto["mlb_principal"] == "MLB123"
    assert produto["descricao"] == "Descricao completa do anuncio no Mercado Livre."
    assert produto["foto"].startswith("cadastro_fotos/lojas/")
    saved_bytes = (Path(info_root) / "cliente-a" / produto["foto"]).read_bytes()
    assert len(saved_bytes) < len(image_bytes)
    with Image.open(io.BytesIO(saved_bytes)) as saved_image:
        assert saved_image.format == "JPEG"
        assert max(saved_image.size) <= cadastro_importacao_catalogos.CATALOG_IMPORT_PHOTO_MAX_EDGE_PX


def test_persisted_bling_tax_fields_are_read_as_safe_generic_aliases(cadastro_runtime):
    cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
        "cliente-a",
        "store-a",
        [{"sku": "NCM-1", "ncm_bling": "85123000", "cest_bling": "01.001.00"}],
        campos_derivados_permitidos={"ncm_bling", "cest_bling"},
    )

    produto = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "NCM-1"
    )

    assert produto["ncm_bling"] == "85123000"
    assert produto["cest_bling"] == "01.001.00"
    assert produto["ncm"] == "85123000"
    assert produto["cest"] == "01.001.00"


def test_existing_sku_persists_only_bling_title_ncm_and_cest_targets(cadastro_runtime):
    cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
        "cliente-a",
        "store-a",
        [
            {
                "sku": "PRIORIDADE-1",
                "nome": "Nome manual",
                "produto": "Produto manual",
                "id_bling": "10",
                "produto_bling": "Titulo antigo",
                "nome_bling": "Titulo antigo",
                "ncm_bling": "11111111",
                "ncm": "11111111",
                "cest_bling": "1111111",
                "cest": "1111111",
                "preco_bling": "10.00",
                "marca_bling": "Marca manual",
            }
        ],
        campos_derivados_permitidos={
            "id_bling",
            "produto_bling",
            "nome_bling",
            "ncm_bling",
            "cest_bling",
        },
    )
    before = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "PRIORIDADE-1"
    )

    cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
        "cliente-a",
        "store-a",
        [
            {
                "sku": "PRIORIDADE-1",
                "row_version": before["row_version"],
                "__expected_scope": "store_file",
                "produto_bling": "Titulo novo",
                "nome_bling": "Titulo novo",
                "ncm_bling": "22222222",
                "ncm": "22222222",
                "cest_bling": "2222222",
                "cest": "2222222",
            }
        ],
        campos_derivados_permitidos={
            "produto_bling",
            "nome_bling",
            "ncm_bling",
            "cest_bling",
        },
    )
    after = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "PRIORIDADE-1"
    )

    assert {key: after.get(key) for key in (
        "produto_bling",
        "nome_bling",
        "ncm_bling",
        "ncm",
        "cest_bling",
        "cest",
    )} == {
        "produto_bling": "Titulo novo",
        "nome_bling": "Titulo novo",
        "ncm_bling": "22222222",
        "ncm": "22222222",
        "cest_bling": "2222222",
        "cest": "2222222",
    }
    assert after["nome"] == "Nome manual"
    assert after["produto"] == "Produto manual"
    assert after["id_bling"] == "10"
    assert str(after["preco_bling"]) in {"10", "10.0", "10.00"}
    assert after["marca_bling"] == "Marca manual"
    assert int(after["row_version"]) == int(before["row_version"]) + 1


def test_precommit_validator_aborts_before_creating_catalog_row(cadastro_runtime):
    info_root, _stores = cadastro_runtime

    def reject(_store):
        raise HTTPException(status_code=409, detail={"code": "catalog_preview_stale"})

    with pytest.raises(HTTPException) as exc_info:
        cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
            "cliente-a",
            "store-a",
            [{"sku": "NEW", "id_bling": "123"}],
            campos_derivados_permitidos={"id_bling"},
            precommit_validator=reject,
        )

    assert exc_info.value.status_code == 409
    cadastro_file = Path(info_root) / "cliente-a" / "cadastro_produtos_lojas.csv"
    assert not cadastro_file.exists()


def test_legacy_snapshot_hash_detects_manual_change_before_materialization():
    original = {
        "sku": "001",
        "sku_normalizado": "001",
        "marca": "Marca manual",
        "descricao": "Descricao original",
        "scope_source": "legacy_shadow",
    }
    payload = {
        "sku": "001",
        "row_version": 0,
        "__legacy_snapshot_hash": cadastro_lojas_produtos._fingerprint_sombra_legada(original),
        "titulo_ml": "Titulo externo",
    }
    alterado = {**original, "descricao": "Descricao alterada depois da previa"}

    with pytest.raises(HTTPException) as exc_info:
        cadastro_lojas_produtos._validar_row_version(payload, None, sombra=alterado)

    assert exc_info.value.status_code == 409
    assert "legado" in str(exc_info.value.detail).lower()


def test_legacy_snapshot_control_field_is_never_persisted():
    dados, _foto, _nome = cadastro_lojas_produtos._dados_mutacao(
        {
            "sku": "001",
            "__legacy_snapshot_hash": "hash-interno",
            "__expected_scope": "legacy_shadow",
            "titulo_ml": "Titulo",
        }
    )

    assert dados == {"sku": "001", "titulo_ml": "Titulo"}


def test_expected_absent_scope_detects_legacy_shadow_appearing_before_commit():
    payload = {
        "sku": "001",
        "__expected_scope": "absent",
        "titulo_ml": "Titulo externo",
    }
    sombra = {
        "sku": "001",
        "sku_normalizado": "001",
        "scope_source": "legacy_shadow",
    }

    with pytest.raises(HTTPException) as exc_info:
        cadastro_lojas_produtos._validar_row_version(payload, None, sombra=sombra)

    assert exc_info.value.status_code == 409
    assert "alterado" in str(exc_info.value.detail).lower()


def test_expected_legacy_shadow_scope_detects_shadow_disappearing_before_commit():
    payload = {
        "sku": "001",
        "__expected_scope": "legacy_shadow",
        "titulo_ml": "Titulo externo",
    }

    with pytest.raises(HTTPException) as exc_info:
        cadastro_lojas_produtos._validar_row_version(payload, None, sombra=None)

    assert exc_info.value.status_code == 409
    assert "alterado" in str(exc_info.value.detail).lower()


def test_batch_commit_uses_prebuilt_store_sku_index(cadastro_runtime, monkeypatch):
    cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
        "cliente-a", "store-a", [{"sku": "001", "titulo_ml": "Antes"}]
    )

    def fail_linear_lookup(*_args, **_kwargs):
        raise AssertionError("o commit em lote nao deve fazer busca linear por SKU")

    monkeypatch.setattr(cadastro_lojas_produtos, "_registro_por_chave", fail_linear_lookup)
    result = cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
        "cliente-a",
        "store-a",
        [
            {"sku": "001", "row_version": 1, "titulo_ml": "Depois"},
            *[
                {"sku": f"NOVO-{index:05d}", "titulo_ml": f"Produto {index}"}
                for index in range(500)
            ],
        ],
    )

    assert result["atualizados"] == 1
    assert result["incluidos"] == 500
    assert result["total"] == 501


def test_batch_preserva_grafia_do_sku_store_file_existente(cadastro_runtime):
    cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
        "cliente-a",
        "store-a",
        [{"sku": "AbC-1", "titulo_ml": ""}],
    )
    atual = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "ABC-1"
    )

    result = cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
        "cliente-a",
        "store-a",
        [
            {
                "sku": "abc-1",
                "row_version": atual["row_version"],
                "__expected_scope": "store_file",
                "titulo_ml": "Titulo importado",
            }
        ],
    )

    assert result["produtos"][0]["sku"] == "AbC-1"
    assert result["produtos"][0]["titulo_ml"] == "Titulo importado"


def test_batch_preserva_grafia_do_sku_legacy_shadow(cadastro_runtime):
    info_root, _stores = cadastro_runtime
    tenant = Path(info_root) / "cliente-a"
    tenant.mkdir(parents=True, exist_ok=True)
    (tenant / "cadastro_produtos.csv").write_text(
        "sku;loja_sync;marca\nAbC-1;Loja A;Marca manual\n",
        encoding="utf-8",
    )
    sombra = cadastro_lojas_produtos._listar_produtos_loja_sync(
        "cliente-a",
        "store-a",
        include_legacy_snapshot_hash=True,
    )[0]

    result = cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
        "cliente-a",
        "store-a",
        [
            {
                "sku": "abc-1",
                "row_version": 0,
                "__expected_scope": "legacy_shadow",
                "__legacy_snapshot_hash": sombra["__legacy_snapshot_hash"],
                "titulo_ml": "Titulo importado",
            }
        ],
    )

    assert result["produtos"][0]["sku"] == "AbC-1"
    assert result["produtos"][0]["marca"] == "Marca manual"


def test_batch_usa_sku_bruto_do_provedor_quando_absent(cadastro_runtime):
    result = cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
        "cliente-a",
        "store-a",
        [
            {
                "sku": "abc-novo",
                "row_version": 0,
                "__expected_scope": "absent",
                "titulo_ml": "Titulo importado",
            }
        ],
    )

    assert result["produtos"][0]["sku"] == "abc-novo"
