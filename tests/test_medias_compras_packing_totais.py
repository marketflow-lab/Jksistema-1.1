"""Packing totals take precedence without rewriting old orders or original files."""

import builtins
import copy
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.medias_compras import create_medias_compras_router
from backend.schemas import ListaPedidoSkuAprovacaoRequest
from backend.services import medias_compras_common as common
from backend.services import medias_compras_listas as listas
from backend.services import medias_compras_packing as packing
from test_medias_compras_packing import _modelo, _modelo_caixas_trays, _salvo, tenants


@pytest.fixture(autouse=True)
def cache_isolado():
    with packing._PACKING_TOTALS_CACHE_LOCK:
        packing._PACKING_TOTALS_CACHE.clear()
    yield
    with packing._PACKING_TOTALS_CACHE_LOCK:
        packing._PACKING_TOTALS_CACHE.clear()


def _resumos(monkeypatch, tenant="cliente-a"):
    monkeypatch.setattr(listas, "_construir_mapa_m3_sku", lambda *_args, **_kwargs: {"001": 99})
    return listas._api_medias_compras_listas_pedidos_sync(client_id=tenant)["listas"]


def _legado(tenants, tenant="cliente-a"):
    path = tenants / tenant / "listas_pedidos.json"
    lista = _salvo(tenants, tenant)
    lista["packing_list"].pop("totais_schema", None)
    lista["packing_list"].pop("totais", None)
    path.write_text(json.dumps([lista]), encoding="utf-8")
    return path, lista, tenants / tenant / "packing_lists" / (lista["packing_list"]["documento_id"] + ".xlsx")


def test_upload_retorna_totais_que_lista_detalhe_e_edicao_preservam(tenants, monkeypatch):
    monkeypatch.setattr(listas, "_construir_mapa_m3_sku", lambda *_args, **_kwargs: {"001": 99})
    app = FastAPI()
    app.include_router(create_medias_compras_router())
    app.dependency_overrides[common.get_tenant_id] = lambda: "cliente-a"
    rota = "/api/medias-compras/listas-pedidos/lista-1"
    original = _modelo_caixas_trays()
    itens = copy.deepcopy(_salvo(tenants)["itens"])
    with TestClient(app) as client:
        antes = client.get("/api/medias-compras/listas-pedidos").json()["listas"][0]
        assert antes["m3_fonte"] == "lista" and antes["packing_list_totais"] is None
        resposta = client.post(rota + "/packing-list/importar-excel", files={"file": ("packing.xlsx", original)})
        assert resposta.status_code == 200, resposta.text
        resumo = resposta.json()["lista"]
        assert resumo["m3_fonte"] == "packing_list"
        assert resumo["total_m3"] == pytest.approx(.403)
        assert resumo["m3_completo"] is True and resumo["m3_pendente_itens"] == 0
        assert resumo["packing_list_totais"] == {
            "total_m3": pytest.approx(.403), "total_caixas": 58, "total_trays": 3,
            "peso_bruto_total_kg": pytest.approx(98.8), "peso_liquido_total_kg": pytest.approx(80),
        }
        listada = client.get("/api/medias-compras/listas-pedidos").json()["listas"][0]
        detalhe = client.get(rota, params={"somente_salvos": "true"}).json()["lista"]
        for resultado in (listada, detalhe):
            assert resultado["total_m3"] == resumo["total_m3"]
            assert resultado["packing_list_totais"] == resumo["packing_list_totais"]
            assert resultado["m3_fonte"] == "packing_list"
    assert _salvo(tenants)["itens"] == itens
    editada = listas._api_medias_compras_lista_pedido_atualizar_aprovacao_sku_sync(
        "lista-1", "001", ListaPedidoSkuAprovacaoRequest(aprovada=False), "cliente-a",
    )["lista"]
    assert editada["packing_list_totais"] == resumo["packing_list_totais"]
    arquivo = next((tenants / "cliente-a" / "packing_lists").glob("*.xlsx"))
    assert arquivo.read_bytes() == original


def test_totais_mesclados_nao_duplicam_volume_ou_pesos(tenants):
    def alterar(sheet):
        sheet.unmerge_cells("F4:F5")
        sheet["F4"] = 1
        sheet["F5"] = "3 Trays"
        sheet["K4"] = .06
        sheet["N4"] = 3.2
        sheet["O4"] = 3.6

    resultado = packing._importar_packing_list_sync("lista-1", _modelo(fotos=False, alterar=alterar), "packing.xlsx", "cliente-a")
    assert resultado["lista"]["packing_list_totais"] == {
        "total_m3": pytest.approx(.073), "total_caixas": 4, "total_trays": 3,
        "peso_liquido_total_kg": pytest.approx(7.6), "peso_bruto_total_kg": pytest.approx(9.1),
    }


@pytest.mark.parametrize("cenario,m3,nw,gw,caixas", [
    ("compartilhados", .033, 7.6, 9.1, 4),
    ("fatores_distintos", .034, 9.6, 11.6, 4),
    ("cbm_informado", .063, 7.6, 9.1, 4),
    ("ctns_distintos", .093, 17.2, 19.9, 7),
])
def test_fallback_deduplica_apenas_mesmas_origens_de_caixas_e_fatores(cenario, m3, nw, gw, caixas):
    def alterar(sheet):
        sheet.unmerge_cells("A4:A5")
        if cenario == "fatores_distintos":
            for coluna, valor in (("G", 10), ("H", 10), ("I", 10), ("L", 2), ("M", 2.5)):
                sheet.unmerge_cells(f"{coluna}4:{coluna}5")
                sheet[f"{coluna}5"] = valor
        elif cenario == "cbm_informado":
            sheet["J4"] = .05
        elif cenario == "ctns_distintos":
            sheet.unmerge_cells("F4:F5")
            sheet["F5"] = 3

    totais = packing._totais_packing(packing._ler_packing_list_bytes(_modelo(fotos=False, alterar=alterar))["grupos"])
    assert totais == {
        "total_m3": pytest.approx(m3), "total_caixas": caixas, "total_trays": 0,
        "peso_liquido_total_kg": pytest.approx(nw), "peso_bruto_total_kg": pytest.approx(gw),
    }


def test_zero_explicito_e_precisao_do_fornecedor_prevalecem(tenants):
    for valor in (0, .0000123456789):
        def alterar(sheet):
            for linha in (3, 4, 6):
                sheet[f"K{linha}"] = valor
                sheet[f"O{linha}"] = 0

        dados = packing._ler_packing_list_bytes(_modelo(fotos=False, alterar=alterar))
        totais = packing._totais_packing(dados["grupos"])
        assert totais["total_m3"] == pytest.approx(valor * 3, abs=1e-15)
        assert totais["peso_bruto_total_kg"] == 0
    original = _modelo(fotos=False, alterar=lambda s: [setattr(s[f"K{r}"], "value", 0) for r in (3, 4, 6)])
    resumo = packing._importar_packing_list_sync("lista-1", original, "zero.xlsx", "cliente-a")["lista"]
    assert resumo["total_m3"] == 0 and resumo["m3_fonte"] == "packing_list"
    assert resumo["m3_completo"] is True and resumo["m3_pendente_itens"] == 0


def test_legado_valida_hash_e_reutiliza_parse_sem_alterar_json_ou_original(tenants, monkeypatch):
    original = _modelo(fotos=False)
    packing._importar_packing_list_sync("lista-1", original, "antigo.xlsx", "cliente-a")
    path, _, arquivo = _legado(tenants)
    json_antes = path.read_bytes()
    parses = []
    ler = packing._ler_packing_list_bytes

    def contar(conteudo):
        parses.append(conteudo)
        return ler(conteudo)

    monkeypatch.setattr(packing, "_ler_packing_list_bytes", contar)
    primeiro = _resumos(monkeypatch)[0]
    assert primeiro["total_m3"] == pytest.approx(.033)
    assert parses == [original]
    # A warm GET may stat the original, but must not reopen or parse its bytes.
    monkeypatch.setattr(packing, "open", lambda *_args, **_kwargs: pytest.fail("warm Packing cache reopened the XLSX"), raising=False)
    assert _resumos(monkeypatch)[0] == primeiro
    assert parses == [original]
    assert path.read_bytes() == json_antes and arquivo.read_bytes() == original


@pytest.mark.parametrize("falha", ["ausente", "corrompido", "sha_metadata", "documento_id", "schema"])
def test_original_invalido_retorna_cubagem_da_lista_sem_inventar_peso(tenants, monkeypatch, falha):
    packing._importar_packing_list_sync("lista-1", _modelo(fotos=False), "packing.xlsx", "cliente-a")
    path, lista, arquivo = _legado(tenants)
    if falha == "ausente":
        arquivo.unlink()
    elif falha == "corrompido":
        estado = arquivo.stat()
        conteudo = arquivo.read_bytes()
        arquivo.write_bytes(bytes([conteudo[0] ^ 1]) + conteudo[1:])
        os.utime(arquivo, ns=(estado.st_atime_ns, estado.st_mtime_ns + 2_000_000_000))
    elif falha == "sha_metadata":
        lista["packing_list"]["sha256"] = "0" * 64
    elif falha == "documento_id":
        lista["packing_list"]["documento_id"] = "../cliente-b/packing_lists/" + lista["packing_list"]["documento_id"]
    else:
        lista["packing_list"]["schema"] = "invalido"
    if falha in {"sha_metadata", "documento_id", "schema"}:
        path.write_text(json.dumps([lista]), encoding="utf-8")
    json_antes = path.read_bytes()
    resumo = _resumos(monkeypatch)[0]
    assert resumo["m3_fonte"] == "lista" and resumo["packing_list_totais"] is None
    assert resumo["total_m3"] == 594
    assert resumo["m3_completo"] is False and resumo["m3_pendente_itens"] == 1
    assert path.read_bytes() == json_antes


def test_cache_invalida_revisao_e_guarda_falha_de_hash_sem_reabrir(tenants, monkeypatch):
    original = _modelo(fotos=False)
    packing._importar_packing_list_sync("lista-1", original, "packing.xlsx", "cliente-a")
    path, _, arquivo = _legado(tenants)
    assert _resumos(monkeypatch)[0]["m3_fonte"] == "packing_list"
    estado = arquivo.stat()
    arquivo.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    os.utime(arquivo, ns=(estado.st_atime_ns, estado.st_mtime_ns + 2_000_000_000))
    assert _resumos(monkeypatch)[0]["m3_fonte"] == "lista"
    abertura = builtins.open
    monkeypatch.setattr(packing, "open", lambda *_args, **_kwargs: pytest.fail("invalid unchanged original was reopened"), raising=False)
    assert _resumos(monkeypatch)[0]["m3_fonte"] == "lista"
    monkeypatch.setattr(packing, "open", abertura)
    arquivo.write_bytes(original)
    os.utime(arquivo, ns=(estado.st_atime_ns, estado.st_mtime_ns + 4_000_000_000))
    assert _resumos(monkeypatch)[0]["m3_fonte"] == "packing_list"
    assert "totais_schema" not in json.loads(path.read_text(encoding="utf-8"))[0]["packing_list"]


def test_cache_isola_tenants_com_mesmo_documento_id(tenants, monkeypatch):
    monkeypatch.setattr(packing.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    packing._importar_packing_list_sync("lista-1", _modelo(fotos=False), "a.xlsx", "cliente-a")
    packing._importar_packing_list_sync("lista-1", _modelo_caixas_trays(), "b.xlsx", "cliente-b")
    _legado(tenants, "cliente-a")
    _legado(tenants, "cliente-b")
    a = _resumos(monkeypatch, "cliente-a")[0]
    b = _resumos(monkeypatch, "cliente-b")[0]
    assert a["packing_list_totais"]["total_caixas"] == 4
    assert b["packing_list_totais"]["total_caixas"] == 58
    assert a["total_m3"] == pytest.approx(.033)
    assert b["total_m3"] == pytest.approx(.403)
    assert _resumos(monkeypatch, "cliente-a")[0] == a


def test_metadados_de_outro_tenant_nao_habilitam_fonte_packing(tenants, monkeypatch):
    packing._importar_packing_list_sync("lista-1", _modelo(fotos=False), "a.xlsx", "cliente-a")
    estrangeiro = _salvo(tenants, "cliente-a")["packing_list"]
    lista_b = _salvo(tenants, "cliente-b")
    lista_b["packing_list"] = estrangeiro
    path = tenants / "cliente-b" / "listas_pedidos.json"
    path.write_text(json.dumps([lista_b]), encoding="utf-8")
    assert _resumos(monkeypatch, "cliente-b")[0]["packing_list_totais"] is None


def test_totais_persistidos_invalidos_recuperam_original_sem_regravar(tenants, monkeypatch):
    packing._importar_packing_list_sync("lista-1", _modelo(fotos=False), "packing.xlsx", "cliente-a")
    lista = _salvo(tenants)
    lista["packing_list"]["totais"]["total_m3"] = "invalid"
    path = tenants / "cliente-a" / "listas_pedidos.json"
    path.write_text(json.dumps([lista]), encoding="utf-8")
    antes = path.read_bytes()
    assert _resumos(monkeypatch)[0]["total_m3"] == pytest.approx(.033)
    assert path.read_bytes() == antes


def test_cache_limita_entradas_por_tenant_e_globais(monkeypatch):
    monkeypatch.setattr(packing, "_PACKING_TOTALS_CACHE_MAX", 3)
    monkeypatch.setattr(packing, "_PACKING_TOTALS_TENANT_MAX", 2)
    for tenant in ("a", "b"):
        for documento in range(3):
            packing._guardar_cache_totais_packing((tenant, documento), {"total_m3": documento})
    assert len(packing._PACKING_TOTALS_CACHE) == 3
    assert sum(key[0] == "b" for key in packing._PACKING_TOTALS_CACHE) == 2
    assert ("b", 0) not in packing._PACKING_TOTALS_CACHE
