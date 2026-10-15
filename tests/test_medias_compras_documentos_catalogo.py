"""Document lookup precedence, legacy recovery and store isolation."""

import copy
import csv
from contextlib import nullcontext

import pytest

from backend.services import cadastro_compatibilidade, cadastro_fotos, cadastro_lojas_produtos
from backend.services import medias_compras_common as common
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
    monkeypatch.setattr(common, "_resolver_foto_cadastro_sku", lambda _client, _sku, foto, _store, **_kwargs: foto)
    return pasta


def _contexto(monkeypatch, produtos, **overrides):
    contexto = {"scope": "store", "store_id": "loja-a", "produtos": produtos}
    contexto.update(overrides)
    monkeypatch.setattr(cadastro_compatibilidade, "visao_produtos_cadastro_contexto_loja",
                        lambda *_args, **_kwargs: contexto)
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
                        lambda _client, sku, foto, _store, **_kwargs: chamados.append(sku) or foto)
    contexto = documentos._contexto_cadastro_documentos(
        {"store_id": "loja-a", "itens": [{"SKU": "SKU:001 Sensor"}, {"SKU": "SKU:375-2 Light"}]}, "cliente-a")
    assert documentos._dados_produto_documento(contexto, "001")["descricao"] == "1"
    assert documentos._dados_produto_documento(contexto, "375-2")["descricao"] == "375-2"
    assert documentos._dados_produto_documento(contexto, "375-1")["descricao"] == ""
    assert chamados == ["1", "375-2"]


def _catalogo_real_simulado(monkeypatch, registros, *, sombras_por_loja=None):
    lojas = [{"store_id": "loja-a", "nome": "Loja A"}, {"store_id": "loja-b", "nome": "Loja B"}]
    leituras = []
    enriquecidos = []

    def ler_registros(client_id):
        leituras.append(client_id)
        return registros, list(registros[0]) if registros else []

    def enriquecer(registro, _loja, _legado, *, scope_source="store_file"):
        enriquecidos.append(registro["sku"])
        return {**registro, "scope_source": scope_source}

    monkeypatch.setattr(cadastro_compatibilidade, "_cadastro_produtos_lojas_path", lambda *_: "unused.csv")
    monkeypatch.setattr(cadastro_compatibilidade, "_bloquear_config_e_arquivo_cadastro", lambda *_: nullcontext())
    monkeypatch.setattr(cadastro_compatibilidade, "_ler_registros_persistidos", ler_registros)
    monkeypatch.setattr(cadastro_compatibilidade, "_lojas_atuais", lambda *_: lojas)
    monkeypatch.setattr(cadastro_compatibilidade, "resolver_loja_ativa_para_leitura",
                        lambda _client, referencia, _store: {"loja_resolvida": True, "store_id": referencia})
    monkeypatch.setattr(cadastro_lojas_produtos, "resolver_loja_cadastro",
                        lambda _client, store: next(loja for loja in lojas if loja["store_id"] == store))
    monkeypatch.setattr(cadastro_lojas_produtos, "_cadastro_produtos_lojas_path", lambda *_: "unused.csv")
    monkeypatch.setattr(cadastro_lojas_produtos, "_lock_arquivo", lambda *_: nullcontext())
    monkeypatch.setattr(cadastro_lojas_produtos, "_ler_registros_persistidos", ler_registros)
    monkeypatch.setattr(cadastro_lojas_produtos, "_contexto_legado", lambda _client, loja: {
        "sombras": (sombras_por_loja or {}).get(loja["store_id"], {}),
    })
    monkeypatch.setattr(cadastro_lojas_produtos, "_enriquecer_produto", enriquecer)
    return leituras, enriquecidos


@pytest.mark.parametrize("skus_explicitos", [False, True])
def test_consulta_filtrada_preserva_aliases_sem_enriquecer_catalogo_inteiro(
    catalogo, monkeypatch, skus_explicitos,
):
    registros = [
        {"sku": sku, "sku_normalizado": sku.upper(), "store_id": "loja-a",
         "cg_product description": f"Description {sku}", "foto": f"{sku}.png"}
        for sku in ("1", "375-1", "375-2", "AB-007", "12-6", "380", "999")
    ]
    leituras, enriquecidos = _catalogo_real_simulado(monkeypatch, registros)
    aliases = ("SKU:001 Sensor", "SKU:375-2 Light", "sku #ab-007", "12/JUN")
    lista = {"store_id": "loja-a", "itens": [{"SKU": sku} for sku in aliases]}
    kwargs = {}
    if skus_explicitos:
        lista["itens"] = [{"SKU": "999"}]
        kwargs["skus"] = (sku for sku in aliases)
    antes = copy.deepcopy(registros)
    contexto = documentos._contexto_cadastro_documentos(lista, "cliente-a", **kwargs)

    assert leituras == ["cliente-a"]
    assert enriquecidos == ["1", "375-2", "AB-007", "12-6"]
    assert [documentos._dados_produto_documento(contexto, sku)["descricao"] for sku in aliases] == [
        "Description 1", "Description 375-2", "Description AB-007", "Description 12-6",
    ]
    assert documentos._dados_produto_documento(contexto, "375-1")["descricao"] == ""
    assert documentos._dados_produto_documento(contexto, "999")["descricao"] == ""
    assert registros == antes


def test_mapa_fotos_reutilizado_na_geracao_sem_vazar_entre_cliente_loja_ou_atualizacao(monkeypatch):
    produtos = [{"sku": f"{i:03}", "cg_product description": f"Product {i}"} for i in range(1, 81)]
    chamadas = []
    versao = {"valor": "original"}

    def visao(_client_id, store_id, *, skus, **_kwargs):
        assert len(skus) == len(produtos)
        return {"scope": "store", "store_id": store_id, "produtos": produtos}

    def mapa(client_id, store_id):
        chamadas.append((client_id, store_id, versao["valor"]))
        return {f'exact:{produto["sku"]}': f'{client_id}/{store_id}/{versao["valor"]}/{produto["sku"]}.png'
                for produto in produtos}

    monkeypatch.setattr(cadastro_compatibilidade, "visao_produtos_cadastro_contexto_loja", visao)
    monkeypatch.setattr(cadastro_fotos, "_cadastro_mapa_fotos_locais", mapa)
    for client_id, store_id, atualizacao in (
        ("cliente-a", "loja-a", "original"),
        ("cliente-a", "loja-b", "original"),
        ("cliente-b", "loja-a", "original"),
        ("cliente-a", "loja-a", "atualizada"),
    ):
        versao["valor"] = atualizacao
        contexto = documentos._contexto_cadastro_documentos(
            {"store_id": store_id, "itens": [{"SKU": produto["sku"]} for produto in produtos]}, client_id,
        )
        assert len(contexto["por_sku"]) == 80
        assert documentos._dados_produto_documento(contexto, "SKU:001 Sensor")["foto"] == (
            f"{client_id}/{store_id}/{atualizacao}/001.png"
        )
    assert chamadas == [
        ("cliente-a", "loja-a", "original"),
        ("cliente-a", "loja-b", "original"),
        ("cliente-b", "loja-a", "original"),
        ("cliente-a", "loja-a", "atualizada"),
    ]


@pytest.mark.parametrize("loja_referencia", ["loja-a", "__todas"])
@pytest.mark.parametrize("colisao", [False, True])
def test_filtro_documento_preserva_sku_rotulado_colisoes_e_conflitos_globais(
    catalogo, monkeypatch, loja_referencia, colisao,
):
    skus = ["SKU:375-2 Light", "SKU:999 Other"]
    if colisao:
        skus.insert(0, "375-2")
    registros = [
        {"sku": sku, "sku_normalizado": sku.upper(), "store_id": loja,
         "cg_product description": f"{loja} / {sku}", "foto": f"{loja}/{sku}.png", "Unit/Pair": "Pair"}
        for loja in ("loja-a", "loja-b") for sku in skus
    ]
    _leituras, enriquecidos = _catalogo_real_simulado(monkeypatch, registros)
    lista = {"store_id": loja_referencia, "itens": [{"SKU": "375-2"}]}
    original = copy.deepcopy(registros)
    visao_real = cadastro_compatibilidade.visao_produtos_cadastro_contexto_loja
    with monkeypatch.context() as consulta_completa:
        consulta_completa.setattr(cadastro_compatibilidade, "visao_produtos_cadastro_contexto_loja",
                                 lambda client, loja, **_kwargs: visao_real(client, loja))
        antes = documentos._contexto_cadastro_documentos(lista, "cliente-a")
    enriquecidos.clear()
    depois = documentos._contexto_cadastro_documentos(lista, "cliente-a")

    assert depois["por_sku"] == antes["por_sku"]
    produto = documentos._dados_produto_documento(depois, "SKU:375-2 Other label")
    assert produto["sku"] == ("SKU:375-2 Light" if loja_referencia == "loja-a" else "SKU:375-2 LIGHT")
    assert produto["descricao"] == ("loja-a / SKU:375-2 Light" if loja_referencia == "loja-a" else "")
    assert produto["foto"] == ("loja-a/SKU:375-2 Light.png" if loja_referencia == "loja-a" else "")
    esperados = (["375-2"] if colisao else []) + ["SKU:375-2 Light"]
    assert enriquecidos == esperados * (1 if loja_referencia == "loja-a" else 2)
    assert registros == original


def test_filtro_documento_preserva_sombra_legada_com_sku_rotulado(catalogo, monkeypatch):
    sombra = {"sku": "SKU:001 Sensor", "sku_normalizado": "SKU:001 SENSOR",
              "cg_product description": "Legacy technical description", "foto": "001.png"}
    _leituras, enriquecidos = _catalogo_real_simulado(
        monkeypatch, [], sombras_por_loja={"loja-a": {sombra["sku_normalizado"]: sombra}},
    )
    contexto = documentos._contexto_cadastro_documentos(
        {"store_id": "loja-a", "itens": [{"SKU": "001"}]}, "cliente-a",
    )
    assert documentos._dados_produto_documento(contexto, "001") == {
        "sku": "SKU:001 Sensor", "descricao": "Legacy technical description", "foto": "001.png", "unidade": "Unit",
    }
    assert enriquecidos == ["SKU:001 Sensor"]


def test_consulta_cadastro_sem_normalizador_preserva_aliases_existentes(catalogo, monkeypatch):
    registros = [{"sku": sku, "sku_normalizado": sku.upper(), "store_id": "loja-a"}
                 for sku in ("375-2", "SKU:375-2 Light")]
    _catalogo_real_simulado(monkeypatch, registros)
    resultado = cadastro_compatibilidade.visao_produtos_cadastro_contexto_loja(
        "cliente-a", "loja-a", skus=["3752"],
    )
    assert [produto["sku"] for produto in resultado["produtos"]] == ["375-2"]
