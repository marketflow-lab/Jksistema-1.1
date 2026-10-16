"""Read snapshots avoid per-product policy I/O without relaxing photo boundaries."""

import builtins
import copy
import json
from collections import Counter
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.services import cadastro_fotos as fotos


@pytest.fixture
def ambiente_fotos(monkeypatch, tmp_path):
    root = tmp_path / "info"
    stores = {
        "cliente-a": [{"store_id": "StoreA"}, {"store_id": "storea"}, {"store_id": "store-b"}],
        "cliente-b": [{"store_id": "store-b"}],
    }
    counts = Counter()

    def policy(client_id, strict=True, groups=None):
        path = root / client_id / fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO
        path.write_text(json.dumps({
            "schema": fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
            "strict_store_scope": strict,
            "shared_groups": groups or [],
        }), encoding="utf-8")

    refs = {}
    for client_id, client_stores in stores.items():
        (root / client_id).mkdir(parents=True)
        policy(client_id)
        for store in client_stores:
            store_id = store["store_id"]
            ref = f"cadastro_fotos/lojas/{fotos._cadastro_store_id_foto_segmento(store_id)}/001.png"
            image = root / client_id / ref
            image.parent.mkdir(parents=True)
            image.write_bytes(b"synthetic-photo")
            refs[client_id, store_id] = ref

    def load_stores(client_id):
        counts["lojas"] += 1
        return copy.deepcopy(stores[client_id])

    original_open = builtins.open

    def counted_open(file, *args, **kwargs):
        if Path(file).name == fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO:
            counts["politica"] += 1
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(fotos, "get_tenant_path", lambda client_id: str(root / client_id))
    monkeypatch.setattr(fotos, "PASTA_INFO", str(root), raising=False)
    monkeypatch.setattr(fotos, "_cadastro_carregar_lojas_foto", load_stores)
    monkeypatch.setattr(builtins, "open", counted_open)
    return {"root": root, "stores": stores, "counts": counts, "policy": policy, "refs": refs}


def _validate_many(state, total=20):
    reference = state["refs"]["cliente-a", "StoreA"]
    return [(
        fotos._cadastro_foto_store_id_referencia("cliente-a", reference),
        fotos._cadastro_foto_referencias_invalidas_loja("cliente-a", "StoreA", {"Foto": reference}),
    ) for _ in range(total)]


def test_fotos_locais_reutilizam_politica_e_ids_por_consulta(ambiente_fotos):
    state = ambiente_fotos
    baseline = _validate_many(state)
    assert state["counts"] == {"lojas": 60, "politica": 20}
    state["counts"].clear()

    snapshot = fotos._cadastro_fotos_capturar_leitura("cliente-a", state["stores"]["cliente-a"])
    with fotos._cadastro_fotos_contexto_leitura(snapshot):
        assert _validate_many(state) == baseline == [("StoreA", [])] * 20
    assert state["counts"] == {"politica": 1}


def test_snapshot_preserva_identidade_exata_e_ambiguidade_casefold(ambiente_fotos):
    state = ambiente_fotos
    snapshot = fotos._cadastro_fotos_capturar_leitura("cliente-a", state["stores"]["cliente-a"])
    state["stores"]["cliente-a"].clear()
    with fotos._cadastro_fotos_contexto_leitura(snapshot):
        assert fotos._cadastro_store_ids_foto_atuais("cliente-a") == ["StoreA", "storea", "store-b"]
        assert not fotos._cadastro_store_id_foto_legado_permitido("cliente-a", "StoreA")
        for store_id in ("StoreA", "storea"):
            reference = state["refs"]["cliente-a", store_id]
            assert fotos._cadastro_foto_referencia_pertence_loja("cliente-a", store_id, reference)
            other = "StoreA" if store_id == "storea" else "storea"
            assert not fotos._cadastro_foto_referencia_pertence_loja("cliente-a", other, reference)


def test_snapshot_nao_vaza_entre_tenants_e_contexto_aninhado_restaura(ambiente_fotos):
    state = ambiente_fotos
    outer = fotos._cadastro_fotos_capturar_leitura("cliente-a", state["stores"]["cliente-a"])
    inner = fotos._cadastro_fotos_capturar_leitura("cliente-b", state["stores"]["cliente-b"])
    reference = state["refs"]["cliente-a", "StoreA"]
    with fotos._cadastro_fotos_contexto_leitura(outer):
        assert fotos._cadastro_foto_store_id_referencia("cliente-b", reference) == ""
        with pytest.raises(RuntimeError, match="synthetic"):
            with fotos._cadastro_fotos_contexto_leitura(inner):
                assert fotos._cadastro_store_ids_foto_atuais("cliente-b") == ["store-b"]
                raise RuntimeError("synthetic")
        assert fotos._cadastro_foto_store_id_referencia("cliente-a", reference) == "StoreA"
        tenant_url = f"/api/cadastro/foto/cliente-b/{reference}"
        assert not fotos._cadastro_foto_referencia_pertence_loja("cliente-a", "StoreA", tenant_url)

    state["stores"]["cliente-a"].clear()
    assert fotos._cadastro_foto_store_id_referencia("cliente-a", reference) == ""


def test_politica_e_ids_retornados_nao_modificam_snapshot(ambiente_fotos):
    state = ambiente_fotos
    state["policy"]("cliente-a", groups=[{"group_id": "grupo", "store_ids": ["StoreA", "store-b"]}])
    snapshot = fotos._cadastro_fotos_capturar_leitura("cliente-a", state["stores"]["cliente-a"])
    with fotos._cadastro_fotos_contexto_leitura(snapshot):
        first = fotos._cadastro_fotos_config_carregar("cliente-a")
        first["strict_store_scope"] = False
        first["shared_groups"][0]["store_ids"].clear()
        fotos._cadastro_store_ids_foto_atuais("cliente-a").clear()
        assert fotos._cadastro_fotos_config_carregar("cliente-a") == {
            "exists": True, "strict_store_scope": True,
            "shared_groups": [{"group_id": "grupo", "store_ids": ["StoreA", "store-b"]}],
        }
        assert fotos._cadastro_store_ids_foto_atuais("cliente-a") == ["StoreA", "storea", "store-b"]


def test_nova_consulta_rele_politica_e_identidades(ambiente_fotos):
    state = ambiente_fotos
    old = fotos._cadastro_fotos_capturar_leitura("cliente-a", state["stores"]["cliente-a"])
    state["policy"]("cliente-a", strict=False)
    state["stores"]["cliente-a"] = [{"store_id": "store-nova"}]
    with fotos._cadastro_fotos_contexto_leitura(old):
        assert fotos._cadastro_fotos_escopo_estrito("cliente-a")
        assert "StoreA" in fotos._cadastro_store_ids_foto_atuais("cliente-a")
    fresh = fotos._cadastro_fotos_capturar_leitura("cliente-a", state["stores"]["cliente-a"])
    with fotos._cadastro_fotos_contexto_leitura(fresh):
        assert not fotos._cadastro_fotos_escopo_estrito("cliente-a")
        assert fotos._cadastro_store_ids_foto_atuais("cliente-a") == ["store-nova"]
    assert state["counts"] == {"politica": 2}


@pytest.mark.parametrize("invalid", ["json", "grupo", "link"])
def test_politica_invalida_ou_link_falha_fechado_no_snapshot(ambiente_fotos, monkeypatch, invalid):
    state = ambiente_fotos
    path = state["root"] / "cliente-a" / fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO
    if invalid == "json":
        path.write_text("{", encoding="utf-8")
    elif invalid == "grupo":
        state["policy"]("cliente-a", strict=False, groups=[{"group_id": "grupo", "store_ids": ["StoreA", "estranha"]}])
    else:
        original = fotos._cadastro_caminho_e_link
        monkeypatch.setattr(fotos, "_cadastro_caminho_e_link", lambda p: Path(p) == path or original(p))
    snapshot = fotos._cadastro_fotos_capturar_leitura("cliente-a", state["stores"]["cliente-a"])
    with fotos._cadastro_fotos_contexto_leitura(snapshot):
        assert fotos._cadastro_fotos_escopo_estrito("cliente-a")
        with pytest.raises(fotos.CadastroFotosConfigInvalida):
            fotos._cadastro_fotos_config_carregar("cliente-a")
    assert state["counts"]["politica"] == (0 if invalid == "link" else 1)


def test_snapshot_nao_cacheia_validacao_de_arquivo_ou_diretorio_link(ambiente_fotos, monkeypatch):
    state = ambiente_fotos
    reference = state["refs"]["cliente-a", "StoreA"]
    path = state["root"] / "cliente-a" / reference
    snapshot = fotos._cadastro_fotos_capturar_leitura("cliente-a", state["stores"]["cliente-a"])
    original = fotos._cadastro_caminho_e_link
    with fotos._cadastro_fotos_contexto_leitura(snapshot):
        assert fotos._cadastro_resolver_arquivo_foto("cliente-a", reference, "StoreA") == str(path)
        assert reference in fotos._cadastro_mapa_fotos_locais("cliente-a", "StoreA").values()
        for link in (path, path.parent):
            monkeypatch.setattr(fotos, "_cadastro_caminho_e_link", lambda p: Path(p) == link or original(p))
            assert reference not in fotos._cadastro_mapa_fotos_locais("cliente-a", "StoreA").values()
            with pytest.raises(HTTPException) as error:
                fotos._cadastro_resolver_arquivo_foto("cliente-a", reference, "StoreA")
            assert error.value.status_code == 404
