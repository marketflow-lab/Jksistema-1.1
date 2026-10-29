"""Store-scoped sources for Importacoes competitor analysis."""

import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import cadastro_compatibilidade as cadastro
from backend.services import medias_compras_listas as listas


BASE = "sheet-base"
ADICIONAL = "1QTJ0i-Y1nYGdu3yRdRuJ7PpAn0KfuGaE8AYozuI6mEY"
HEADER = ["SKU", "Nome"] + [""] * 20


def _linha(sku, identificador):
    row = [str(sku), "Produto de teste"] + [""] * 20
    for numero in range(5):
        coluna = 2 + numero * 4
        row[coluna:coluna + 4] = [
            f"MLB{identificador}1{numero}",
            f"R$ {identificador + numero},10",
            f"MLB{identificador}2{numero}",
            f"R$ {identificador + numero},20",
        ]
    return row


def _comparar_pares(payload, row):
    for numero in range(1, 6):
        chave = f"concorrente_{numero}"
        coluna = 2 + (numero - 1) * 4
        assert payload["concorrentes_mlb"][chave] == row[coluna]
        assert payload["concorrentes_preco"][chave] == row[coluna + 1]
        assert payload["concorrentes"][chave] == row[coluna + 2]
        assert payload["concorrentes_valores"][chave] == row[coluna + 3]


@pytest.fixture
def fontes(monkeypatch):
    estado = SimpleNamespace(
        lojas={
            "tenant-a": [
                {"store_id": "store-jk", "nome": "JK Peças"},
                {"store_id": "store-outra", "nome": "Outra Loja"},
            ],
            "tenant-b": [{"store_id": "store-b", "nome": "Outra Loja"}],
        },
        listas={
            "tenant-a": [{
                "id": "lista-1",
                "store_id": "store-jk",
                "loja": "JK Peças",
                "itens": [{"SKU": sku} for sku in ("001", "167", "002")],
            }],
            "tenant-b": [{
                "id": "lista-1",
                "store_id": "store-b",
                "loja": "Outra Loja",
                "itens": [{"SKU": "167"}],
            }],
        },
        planilhas={
            BASE: [HEADER, _linha("001", 100)],
            ADICIONAL: [HEADER, _linha("167", 200), _linha("002", 300)],
        },
        leituras=[],
        resolucoes=[],
    )

    def carregar_planilha(spreadsheet_id=None):
        chave = spreadsheet_id or BASE
        estado.leituras.append(chave)
        rows = estado.planilhas[chave]
        if isinstance(rows, Exception):
            raise rows
        return copy.deepcopy(rows)

    def lojas_do_tenant(client_id):
        estado.resolucoes.append(client_id)
        return copy.deepcopy(estado.lojas.get(client_id, []))

    monkeypatch.setattr(listas, "SPREADSHEET_ID_CONCORRENTES", BASE, raising=False)
    monkeypatch.setattr(listas, "_carregar_linhas_planilha_concorrentes", carregar_planilha)
    monkeypatch.setattr(
        listas, "_carregar_listas_pedidos",
        lambda client_id: copy.deepcopy(estado.listas.get(client_id, [])),
    )
    monkeypatch.setattr(cadastro, "_lojas_atuais", lojas_do_tenant)
    monkeypatch.setattr(cadastro._store, "get_tenant_path", lambda client_id: Path("tenant-sintetico") / client_id)
    monkeypatch.setattr(
        cadastro, "read_retired_store_names",
        lambda _path: SimpleNamespace(blocks=lambda *_args: False),
    )
    return estado


def _sku(sku="167", *, lista_id="lista-1", tenant="tenant-a"):
    kwargs = {"sku": sku, "client_id": tenant}
    if lista_id is not None:
        kwargs["lista_id"] = lista_id
    return asyncio.run(listas.api_medias_compras_concorrentes_links(**kwargs))


def _lote(*, lista_id="lista-1", tenant="tenant-a"):
    return asyncio.run(
        listas.api_medias_compras_lista_pedido_concorrentes_links_lote(
            lista_id=lista_id, client_id=tenant,
        )
    )


def test_sku_sem_lista_preserva_fonte_legada(fontes):
    payload = _sku("001", lista_id=None)
    _comparar_pares(payload, fontes.planilhas[BASE][1])
    assert payload["fontes_versao"] == 1
    assert fontes.leituras == [BASE]
    assert fontes.resolucoes == []


def test_sku_sem_lista_nao_consulta_fonte_exclusiva_jk(fontes):
    payload = _sku("167", lista_id=None)
    assert all(value == "" for value in payload["concorrentes"].values())
    assert payload["fontes_versao"] == 1
    assert fontes.leituras == [BASE]


def test_sku_jk_usa_fonte_adicional_com_cinco_pares(fontes):
    payload = _sku()
    _comparar_pares(payload, fontes.planilhas[ADICIONAL][1])
    assert payload["fontes_versao"] == 2
    assert fontes.leituras == [BASE, ADICIONAL]
    assert fontes.resolucoes == ["tenant-a"]


def test_lote_jk_le_cada_fonte_uma_vez_e_mantem_pares(fontes):
    payload = _lote()
    assert payload["total"] == 3
    assert set(payload["resultados"]) == {"001", "167", "002"}
    for sku, row in (
        ("001", fontes.planilhas[BASE][1]),
        ("167", fontes.planilhas[ADICIONAL][1]),
        ("002", fontes.planilhas[ADICIONAL][2]),
    ):
        _comparar_pares(payload["resultados"][sku], row)
        assert payload["resultados"][sku]["fontes_versao"] == 2
    assert fontes.leituras == [BASE, ADICIONAL]


@pytest.mark.parametrize("consulta", ["sku", "lote"])
def test_duplicata_preserva_linha_inteira_da_fonte_base(fontes, consulta):
    base = fontes.planilhas[BASE][1]
    base[5] = ""
    base[10:14] = ["", "", "", ""]
    fontes.planilhas[ADICIONAL].append(_linha("001", 900))
    payload = _sku("001") if consulta == "sku" else _lote()["resultados"]["001"]
    _comparar_pares(payload, base)
    assert payload["concorrentes_valores"]["concorrente_1"] == ""
    assert payload["concorrentes_mlb"]["concorrente_3"] == ""
    assert fontes.leituras == [BASE, ADICIONAL]


def test_zeros_iniciais_distinguem_skus_na_mesma_lista(fontes):
    # O contrato legado converte "1" em "001"; "01" e "001" seguem distintos.
    fontes.listas["tenant-a"][0]["itens"].append({"SKU": "01"})
    linha_dois_digitos = _linha("01", 400)
    fontes.planilhas[ADICIONAL].append(linha_dois_digitos)
    payload = _lote()["resultados"]
    _comparar_pares(payload["001"], fontes.planilhas[BASE][1])
    _comparar_pares(payload["01"], linha_dois_digitos)
    assert payload["001"]["concorrentes_mlb"] != payload["01"]["concorrentes_mlb"]


def test_vinculo_do_sku_aceita_equivalente_legado_de_um_digito(fontes):
    # A lista normaliza 1 para 001; a consulta da planilha conserva o SKU recebido.
    linha_legada = _linha("1", 400)
    fontes.planilhas[ADICIONAL].append(linha_legada)
    assert fontes.listas["tenant-a"][0]["itens"][0]["SKU"] == "001"
    payload = _sku("1")
    assert payload["sku"] == "1"
    assert payload["fontes_versao"] == 2
    _comparar_pares(payload, linha_legada)


def test_normalizacao_da_planilha_encontra_sku_sem_trocar_identidade(fontes):
    fontes.planilhas[ADICIONAL][1][0] = " 167.0 "
    payload = _sku("167")
    _comparar_pares(payload, fontes.planilhas[ADICIONAL][1])


@pytest.mark.parametrize("consulta", ["sku", "lote"])
def test_mesmo_id_lista_em_outro_tenant_nao_ativa_fonte_jk(fontes, consulta):
    payload = (
        _sku(tenant="tenant-b") if consulta == "sku"
        else _lote(tenant="tenant-b")["resultados"]["167"]
    )
    assert payload["fontes_versao"] == 1
    assert all(value == "" for value in payload["concorrentes_mlb"].values())
    assert fontes.leituras == [BASE]
    assert fontes.resolucoes == ["tenant-b"]


@pytest.mark.parametrize("consulta", ["sku", "lote"])
@pytest.mark.parametrize("tenant,lista_id", [
    ("tenant-inexistente", "lista-1"),
    ("tenant-a", "lista-inexistente"),
])
def test_lista_fora_do_tenant_falha_antes_de_consultar_fontes(fontes, consulta, tenant, lista_id):
    with pytest.raises(HTTPException) as erro:
        if consulta == "sku":
            _sku(tenant=tenant, lista_id=lista_id)
        else:
            _lote(tenant=tenant, lista_id=lista_id)
    assert erro.value.status_code == 404
    assert fontes.leituras == []


def test_sku_ausente_da_lista_falha_antes_de_consultar_fontes(fontes):
    with pytest.raises(HTTPException) as erro:
        _sku("999")
    assert erro.value.status_code == 404
    assert fontes.leituras == []


@pytest.mark.parametrize("store_id,nome_salvo,versao", [
    ("store-outra", "JK Peças", 1),
    ("store-jk", "Outra Loja", 2),
])
def test_store_id_prevalece_sobre_nome_salvo(fontes, store_id, nome_salvo, versao):
    fontes.listas["tenant-a"][0].update(store_id=store_id, loja=nome_salvo)
    payload = _sku()
    assert payload["fontes_versao"] == versao
    assert fontes.leituras == ([BASE, ADICIONAL] if versao == 2 else [BASE])
    if versao == 2:
        _comparar_pares(payload, fontes.planilhas[ADICIONAL][1])
    else:
        assert all(value == "" for value in payload["concorrentes"].values())


def test_nome_legado_unico_resolve_identidade_canonica_jk(fontes):
    fontes.listas["tenant-a"][0].update(store_id="", loja="jk pecas")
    payload = _sku()
    assert payload["fontes_versao"] == 2
    _comparar_pares(payload, fontes.planilhas[ADICIONAL][1])


def test_nome_parecido_nao_ativa_fonte_jk(fontes):
    fontes.lojas["tenant-a"][0]["nome"] = "JK Peças Outlet"
    fontes.listas["tenant-a"][0]["loja"] = "JK Peças Outlet"
    payload = _sku()
    assert payload["fontes_versao"] == 1
    assert fontes.leituras == [BASE]


@pytest.mark.parametrize("consulta", ["sku", "lote"])
def test_nome_ambiguo_nao_escolhe_loja_nem_fonte(fontes, consulta):
    fontes.listas["tenant-a"][0]["store_id"] = ""
    fontes.lojas["tenant-a"].append({"store_id": "store-jk-duplicada", "nome": "JK Peças"})
    with pytest.raises(HTTPException) as erro:
        _sku() if consulta == "sku" else _lote()
    assert erro.value.status_code == 409
    assert fontes.leituras == []


def test_store_id_de_outro_tenant_nao_usa_nome_como_fallback(fontes):
    fontes.listas["tenant-a"][0]["store_id"] = "store-b"
    with pytest.raises(HTTPException) as erro:
        _sku()
    assert erro.value.status_code == 409
    assert fontes.leituras == []


@pytest.mark.parametrize("store_id,nome_salvo,versao", [
    ("store-jk", "Nome antigo", 2),
    ("store-outra", "JK Peças", 1),
    ("store-inexistente", "JK Peças", 0),
])
@pytest.mark.parametrize("somente_salvos", [False, True])
def test_detalhe_informa_fonte_canonica_sem_ler_planilha(
    fontes, monkeypatch, store_id, nome_salvo, versao, somente_salvos,
):
    fontes.listas["tenant-a"][0].update(store_id=store_id, loja=nome_salvo)
    contexto_consultado = []

    def contexto_cadastro(client_id, referencia, **_kwargs):
        contexto_consultado.append((client_id, referencia))
        return {"scope": "store", "store_id": referencia, "produtos": []}

    def persistencia_inesperada(*_args, **_kwargs):
        pytest.fail("A consulta de metadados nao deve alterar a lista")

    monkeypatch.setattr(cadastro, "visao_produtos_cadastro_contexto_loja", contexto_cadastro)
    monkeypatch.setattr(
        listas, "_recalcular_frete_internacional_itens_lista",
        lambda _client, itens, **_kwargs: copy.deepcopy(itens),
    )
    monkeypatch.setattr(listas, "_resumo_lista_pedido", lambda lista, **_kwargs: dict(lista))
    monkeypatch.setattr(
        listas, "_itens_salvos_lista_pedido_no_escopo",
        lambda _client, lista: copy.deepcopy(lista["itens"]),
    )
    monkeypatch.setattr(listas, "_salvar_listas_pedidos", persistencia_inesperada)
    detalhe = listas._api_medias_compras_lista_pedido_detalhe_sync
    while hasattr(detalhe, "__wrapped__"):
        detalhe = detalhe.__wrapped__
    payload = detalhe("lista-1", client_id="tenant-a", somente_salvos=somente_salvos)

    assert fontes.leituras == []
    if somente_salvos:
        assert "analise_concorrentes_fontes_versao" not in payload["lista"]
        assert fontes.resolucoes == []
        assert contexto_consultado == []
    else:
        assert payload["lista"]["analise_concorrentes_fontes_versao"] == versao
        assert fontes.resolucoes == ["tenant-a"]
        assert contexto_consultado == [("tenant-a", store_id)]


@pytest.mark.parametrize("consulta", ["sku", "lote"])
@pytest.mark.parametrize("fonte", [BASE, ADICIONAL])
@pytest.mark.parametrize("falha,codigo", [
    (HTTPException(status_code=503, detail="Fonte temporariamente indisponível"), 503),
    (RuntimeError("Falha simulada de leitura"), 500),
])
def test_falha_da_fonte_nao_vira_resultado_vazio(fontes, consulta, fonte, falha, codigo):
    fontes.planilhas[fonte] = falha
    with pytest.raises(HTTPException) as erro:
        _sku() if consulta == "sku" else _lote()
    assert erro.value.status_code == codigo
    assert fonte in fontes.leituras


def test_leitor_aceita_fonte_explicita_e_preserva_padrao(monkeypatch):
    abertas = []
    row = _linha("167", 200)

    class Worksheet:
        def get_all_values(self):
            return [HEADER, row]

    class Spreadsheet:
        def get_worksheet(self, indice):
            assert indice == 0
            return Worksheet()

    class Google:
        def open_by_key(self, spreadsheet_id):
            abertas.append(spreadsheet_id)
            return Spreadsheet()

    monkeypatch.setattr(listas, "SPREADSHEET_ID_CONCORRENTES", BASE, raising=False)
    monkeypatch.setattr(listas, "autenticar_google_sheets", lambda: Google(), raising=False)
    assert listas._carregar_linhas_planilha_concorrentes() == [HEADER, row]
    assert listas._carregar_linhas_planilha_concorrentes(ADICIONAL) == [HEADER, row]
    assert abertas == [BASE, ADICIONAL]


def test_leitor_sem_aba_falha_em_vez_de_retornar_vazio(monkeypatch):
    google = SimpleNamespace(open_by_key=lambda _key: SimpleNamespace(get_worksheet=lambda _index: None))
    monkeypatch.setattr(listas, "autenticar_google_sheets", lambda: google, raising=False)
    with pytest.raises(HTTPException) as erro:
        listas._carregar_linhas_planilha_concorrentes(ADICIONAL)
    assert erro.value.status_code == 404
