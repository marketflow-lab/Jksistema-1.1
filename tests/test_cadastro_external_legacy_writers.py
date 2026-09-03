import asyncio
from contextlib import contextmanager

import pandas as pd
import pytest
from fastapi import HTTPException

from backend.services import (
    cadastro_lojas_produtos,
    favoritos_storage,
    impostos_regras,
)


def _configurar(monkeypatch, tmp_path):
    tenant = tmp_path / "cliente-a"
    tenant.mkdir()

    def tenant_path(_client_id):
        return str(tenant)

    monkeypatch.setattr(cadastro_lojas_produtos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(favoritos_storage, "get_tenant_path", tenant_path, raising=False)
    monkeypatch.setattr(impostos_regras, "get_tenant_path", tenant_path)
    monkeypatch.setattr(
        favoritos_storage,
        "_migrar_arquivo_legado_para_tenant",
        lambda _client_id, filename, _legacy: str(tenant / filename),
        raising=False,
    )
    monkeypatch.setattr(
        impostos_regras,
        "_migrar_arquivo_legado_para_tenant",
        lambda _client_id, filename, _legacy: str(tenant / filename),
        raising=False,
    )
    monkeypatch.setattr(
        favoritos_storage,
        "_normalizar_sku_mes",
        lambda valor: str(valor or "").strip(),
        raising=False,
    )
    for module in (favoritos_storage, impostos_regras):
        monkeypatch.setattr(
            module,
            "ARQUIVO_DB_CADASTRO_PRODUTOS",
            "legacy-cadastro",
            raising=False,
        )
    monkeypatch.setattr(
        favoritos_storage,
        "ARQUIVO_DB_PRODUTOS",
        "legacy-estoque",
        raising=False,
    )
    return tenant


def _criar_sku_controlado(tenant, sku="001"):
    pd.DataFrame(
        [{"store_id": "store-a", "sku": sku, "sku_normalizado": sku}]
    ).to_csv(tenant / "cadastro_produtos_lojas.csv", index=False)


@pytest.mark.parametrize("writer", ["descricao", "pesquisas"])
def test_favoritos_nao_grava_sku_controlado(monkeypatch, tmp_path, writer):
    tenant = _configurar(monkeypatch, tmp_path)
    cadastro = tenant / "cadastro_produtos.csv"
    pd.DataFrame(
        [{"sku": "001", "nome": "Legado", "descricao": "antes", "pesquisa_1": "antes"}]
    ).to_csv(cadastro, index=False)
    _criar_sku_controlado(tenant)
    antes = cadastro.read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        if writer == "descricao":
            favoritos_storage._favoritos_salvar_descricao_cadastro(
                "cliente-a", "001", "depois"
            )
        else:
            favoritos_storage._favoritos_salvar_pesquisas_batch(
                "cliente-a",
                [{"sku": "001", "pesquisa_1": "depois"}],
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert cadastro.read_bytes() == antes


def test_favoritos_revalida_sku_criado_antes_do_read_modify_write(
    monkeypatch, tmp_path
):
    tenant = _configurar(monkeypatch, tmp_path)
    cadastro = tenant / "cadastro_produtos.csv"
    pd.DataFrame([{"sku": "001", "descricao": "antes"}]).to_csv(
        cadastro, index=False
    )
    antes = cadastro.read_bytes()

    @contextmanager
    def path_lock_com_race(_path):
        _criar_sku_controlado(tenant)
        yield

    monkeypatch.setattr(favoritos_storage, "path_lock_for", path_lock_com_race)

    with pytest.raises(HTTPException) as exc_info:
        favoritos_storage._favoritos_salvar_descricao_cadastro(
            "cliente-a", "001", "nao-gravar"
        )

    assert exc_info.value.status_code == 409
    assert cadastro.read_bytes() == antes


def test_impostos_nao_grava_sku_controlado(monkeypatch, tmp_path):
    tenant = _configurar(monkeypatch, tmp_path)
    cadastro = tenant / "cadastro_produtos.csv"
    pd.DataFrame(
        [{"sku": "001", "nome": "Legado", "ncm": "1234", "cest": "", "imposto": ""}]
    ).to_csv(cadastro, index=False)
    _criar_sku_controlado(tenant)
    antes = cadastro.read_bytes()
    monkeypatch.setattr(
        impostos_regras,
        "_carregar_regras_impostos",
        lambda _client_id: [
            {
                "id": "regra-1",
                "ncm": "1234",
                "cest": "",
                "aliquota_federal": 10,
                "aliquota_estadual": 0,
                "aliquota_municipal": 0,
            }
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(impostos_regras.aplicar_impostos_no_cadastro("cliente-a"))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert cadastro.read_bytes() == antes


def test_impostos_revalida_sku_criado_entre_preview_e_commit(monkeypatch, tmp_path):
    tenant = _configurar(monkeypatch, tmp_path)
    cadastro = tenant / "cadastro_produtos.csv"
    pd.DataFrame(
        [{"sku": "001", "nome": "Legado", "ncm": "1234", "cest": "", "imposto": ""}]
    ).to_csv(cadastro, index=False)
    antes = cadastro.read_bytes()
    monkeypatch.setattr(
        impostos_regras,
        "_carregar_regras_impostos",
        lambda _client_id: [],
    )

    @contextmanager
    def path_lock_com_race(_path):
        _criar_sku_controlado(tenant)
        yield

    monkeypatch.setattr(impostos_regras, "path_lock_for", path_lock_com_race)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(impostos_regras.aplicar_impostos_no_cadastro("cliente-a"))

    assert exc_info.value.status_code == 409
    assert cadastro.read_bytes() == antes
