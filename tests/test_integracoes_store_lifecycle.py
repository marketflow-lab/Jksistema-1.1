import asyncio
import json
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from backend.schemas import AuthRequest, StoreRequest, TokenRequest
from backend.services import (
    bling_vendas,
    cadastro_fotos,
    integracoes,
    integracoes_api,
    mercadolivre_legacy_api,
)


def _configure(tmp_path):
    info_dir = tmp_path / "info"

    def tenant_path(client_id):
        path = info_dir / str(client_id or "default")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    return info_dir / "cliente-a"


def _homonimas():
    return [
        {"store_id": "StoreA", "nome": "Loja Igual", "integracoes": {}},
        {"store_id": "storea", "nome": "Loja Igual", "integracoes": {}},
    ]


def test_snapshot_antigo_nao_ressuscita_loja_tombstonada(tmp_path):
    tenant = _configure(tmp_path)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-a", "nome": "Loja A", "integracoes": {}}],
    )
    snapshot_antigo = integracoes.carregar_lojas("cliente-a")

    integracoes.excluir_loja("cliente-a", "Loja A", store_id="store-a")
    snapshot_antigo.append(
        {"store_id": "store-b", "nome": "Loja B", "integracoes": {}}
    )
    with pytest.raises(HTTPException) as exc_info:
        integracoes.salvar_lojas("cliente-a", snapshot_antigo)

    assert exc_info.value.detail["code"] == "store_tombstoned"
    assert json.loads((tenant / "lojas_config.json").read_text(encoding="utf-8")) == []
    tombstones = json.loads(
        (tenant / "lojas_sync_tombstones.json").read_text(encoding="utf-8")
    )
    assert {item["store_id"] for item in tombstones} == {"store-a"}


def test_loja_em_grupo_de_fotos_nao_pode_ser_removida(
    tmp_path,
    monkeypatch,
):
    tenant = _configure(tmp_path)

    def tenant_path(client_id):
        path = tenant.parent / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(tenant.parent), raising=False)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", tenant_path, raising=False)
    lojas = [
        {"store_id": store_id, "nome": store_id, "integracoes": {}}
        for store_id in ("store-a", "store-b", "store-c")
    ]
    integracoes.salvar_lojas("cliente-a", lojas)
    config_fotos = tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO
    config_fotos.write_text(
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": True,
                "shared_groups": [
                    {
                        "group_id": "abc",
                        "store_ids": ["store-a", "store-b", "store-c"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    lojas_antes = (tenant / "lojas_config.json").read_bytes()
    fotos_antes = config_fotos.read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        integracoes.excluir_loja(
            "cliente-a",
            "store-a",
            store_id="store-a",
        )

    assert exc_info.value.detail["code"] == "store_in_shared_photo_group"
    assert (tenant / "lojas_config.json").read_bytes() == lojas_antes
    assert config_fotos.read_bytes() == fotos_antes
    assert not (tenant / "lojas_sync_tombstones.json").exists()


def test_nome_homonimo_falha_fechado_e_store_id_e_case_sensitive(tmp_path):
    _configure(tmp_path)
    integracoes.salvar_lojas("cliente-a", _homonimas())

    with pytest.raises(HTTPException) as exc_info:
        integracoes.buscar_loja("cliente-a", "Loja Igual")
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_name_ambiguous"

    assert integracoes.buscar_loja(
        "cliente-a", "nome obsoleto", store_id="StoreA"
    )["store_id"] == "StoreA"
    assert integracoes.buscar_loja(
        "cliente-a", "Loja Igual", store_id="STOREA"
    ) is None

    with pytest.raises(HTTPException) as exc_update:
        integracoes.atualizar_api_loja(
            "cliente-a", "Loja Igual", "mercadoturbo", {"token": "nao-gravar"}
        )
    assert exc_update.value.status_code == 409

    integracoes.atualizar_api_loja(
        "cliente-a",
        "Loja Igual",
        "mercadoturbo",
        {"token": "somente-b", "connected": True},
        store_id="storea",
    )
    por_id = {loja["store_id"]: loja for loja in integracoes.carregar_lojas("cliente-a")}
    assert "mercadoturbo" not in por_id["StoreA"]["integracoes"]
    assert por_id["storea"]["integracoes"]["mercadoturbo"]["token"] == "somente-b"


def test_writer_integracao_nao_cria_loja_sem_store_id(tmp_path):
    _configure(tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        integracoes.atualizar_api_loja(
            "cliente-a",
            "Loja inexistente",
            "mercadolivre",
            {"access_token": "nao-gravar"},
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert integracoes.carregar_lojas("cliente-a") == []


def test_resultado_async_antigo_nao_contamina_nova_loja_homonima(tmp_path):
    _configure(tmp_path)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-antiga", "nome": "Loja Igual", "integracoes": {}}],
    )
    integracoes.excluir_loja(
        "cliente-a",
        "Loja Igual",
        store_id="store-antiga",
    )
    nova = integracoes.criar_loja("cliente-a", "Loja Igual")

    with pytest.raises(HTTPException) as exc_info:
        integracoes.atualizar_api_loja(
            "cliente-a",
            "Loja Igual",
            "mercadolivre",
            {"access_token": "resposta-obsoleta"},
            store_id="store-antiga",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_config_changed"
    persistida = integracoes.buscar_loja(
        "cliente-a",
        "Loja Igual",
        store_id=nova["store_id"],
    )
    assert "mercadolivre" not in persistida.get("integracoes", {})


def test_ml_materializa_store_id_e_nao_persiste_contexto_interno(monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        mercadolivre_legacy_api,
        "atualizar_api_loja",
        lambda *args, **kwargs: chamadas.append((args, kwargs)),
        raising=False,
    )
    cfg = {
        "_store_id_context": "StoreA",
        "access_token": "token",
    }

    mercadolivre_legacy_api._ml_atualizar_api_loja_exata(
        "cliente-a",
        "Loja Igual",
        cfg,
    )

    args, kwargs = chamadas[0]
    assert args[:3] == ("cliente-a", "Loja Igual", "mercadolivre")
    assert kwargs["store_id"] == "StoreA"
    assert "_store_id_context" not in args[3]


def test_mutacoes_stale_nao_excluem_nem_desconectam_nova_homonima(tmp_path):
    _configure(tmp_path)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-antiga", "nome": "Loja Igual", "integracoes": {}}],
    )
    integracoes.excluir_loja(
        "cliente-a",
        "Loja Igual",
        store_id="store-antiga",
    )
    nova = integracoes.criar_loja("cliente-a", "Loja Igual")
    integracoes.atualizar_api_loja(
        "cliente-a",
        "Loja Igual",
        "mercadoturbo",
        {"token": "token-novo", "connected": True},
        store_id=nova["store_id"],
    )

    for mutacao in (
        lambda: integracoes.excluir_loja(
            "cliente-a",
            "Loja Igual",
            store_id="store-antiga",
        ),
        lambda: integracoes.desconectar_api_loja(
            "cliente-a",
            "Loja Igual",
            "mercadoturbo",
            store_id="store-antiga",
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            mutacao()
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "store_config_changed"

    persistida = integracoes.buscar_loja(
        "cliente-a",
        "Loja Igual",
        store_id=nova["store_id"],
    )
    assert persistida["integracoes"]["mercadoturbo"]["token"] == "token-novo"


def test_endpoints_mutadores_rejeitam_nome_sem_store_id(tmp_path):
    _configure(tmp_path)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "StoreA", "nome": "Loja A", "integracoes": {}}],
    )

    chamadas = (
        lambda: integracoes_api.save_turbo_token(
            "Loja A",
            TokenRequest(token="nao-gravar"),
            client_id="cliente-a",
        ),
        lambda: integracoes_api.delete_loja(
            "Loja A",
            client_id="cliente-a",
        ),
        lambda: integracoes_api.disconnect_integracao(
            "Loja A",
            "turbo",
            client_id="cliente-a",
        ),
        lambda: integracoes_api.start_bling_auth(
            AuthRequest(
                loja="Loja A",
                client_id="app",
                client_secret="secret",
            ),
            object(),
            "cliente-a",
        ),
        lambda: integracoes_api.start_mercadolivre_auth(
            AuthRequest(
                loja="Loja A",
                client_id="app",
                client_secret="secret",
            ),
            object(),
            "cliente-a",
        ),
    )
    for chamada in chamadas:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(chamada())
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "store_id_required"

    persistida = integracoes.buscar_loja(
        "cliente-a",
        "Loja A",
        store_id="StoreA",
    )
    assert persistida["integracoes"] == {}


def test_migracao_legada_bootstrap_virgem_e_replay_pos_exclusao(tmp_path):
    tenant_cliente = _configure(tmp_path)
    info_dir = tenant_cliente.parent
    info_dir.mkdir(parents=True, exist_ok=True)
    legado = info_dir / "integracoes.json"
    legado.write_text(
        json.dumps(
            {
                "Loja Legada": {
                    "mercadoturbo": {
                        "token": "token-legado",
                        "connected": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    bootstrap = integracoes.carregar_lojas("default")
    assert len(bootstrap) == 1
    assert bootstrap[0]["integracoes"]["mercadoturbo"]["token"] == "token-legado"
    store_id_antigo = bootstrap[0]["store_id"]
    integracoes.excluir_loja(
        "default",
        "Loja Legada",
        store_id=store_id_antigo,
    )

    assert integracoes.carregar_lojas("default") == []
    nova = integracoes.criar_loja("default", "Loja Legada")
    recarregada = integracoes.buscar_loja(
        "default",
        "Loja Legada",
        store_id=nova["store_id"],
    )
    assert recarregada["store_id"] != store_id_antigo
    assert "mercadoturbo" not in recarregada["integracoes"]
    assert "bling" not in recarregada["integracoes"]
    assert (info_dir / "default" / "integracoes_legacy_migration.json").exists()


def test_config_vazia_existente_bloqueia_bootstrap_legado(tmp_path):
    tenant_cliente = _configure(tmp_path)
    info_dir = tenant_cliente.parent
    tenant_default = info_dir / "default"
    tenant_default.mkdir(parents=True, exist_ok=True)
    (tenant_default / "lojas_config.json").write_text("[]", encoding="utf-8")
    (info_dir / "integracoes.json").write_text(
        json.dumps(
            {"Loja Legada": {"mercadoturbo": {"token": "nao-importar"}}}
        ),
        encoding="utf-8",
    )

    assert integracoes.carregar_lojas("default") == []
    assert json.loads(
        (tenant_default / "lojas_config.json").read_text(encoding="utf-8")
    ) == []


@pytest.mark.parametrize("client_id", ["tenant-b", ""])
def test_tenant_nao_default_vazio_nao_consumir_fontes_legadas_globais(
    tmp_path,
    client_id,
):
    tenant_cliente = _configure(tmp_path)
    info_dir = tenant_cliente.parent
    info_dir.mkdir(parents=True, exist_ok=True)
    root_config = info_dir / "lojas_config.json"
    root_integracoes = info_dir / "integracoes.json"
    root_bling = info_dir / "bling_conf.json"
    root_config.write_text(
        json.dumps(
            [{"store_id": "root-store", "nome": "Loja Global", "integracoes": {}}]
        ),
        encoding="utf-8",
    )
    root_integracoes.write_text(
        json.dumps(
            {"Loja Global": {"mercadoturbo": {"token": "segredo-global"}}}
        ),
        encoding="utf-8",
    )
    root_bling.write_text(
        json.dumps([{"loja": "Loja Global", "token": "bling-global"}]),
        encoding="utf-8",
    )

    assert integracoes.carregar_lojas(client_id) == []
    assert root_config.exists()
    assert root_integracoes.exists()
    assert root_bling.exists()
    tenant_path = info_dir / str(client_id or "default")
    assert not (tenant_path / "integracoes_legacy_migration.json").exists()
    if client_id:
        assert not (tenant_path / "lojas_config.json").exists()


def test_tenant_nao_default_com_config_nao_mescla_credencial_global(tmp_path):
    tenant_cliente = _configure(tmp_path)
    info_dir = tenant_cliente.parent
    tenant_b = info_dir / "tenant-b"
    tenant_b.mkdir(parents=True, exist_ok=True)
    tenant_config = tenant_b / "lojas_config.json"
    tenant_config.write_text(
        json.dumps(
            [
                {
                    "store_id": "tenant-store",
                    "nome": "Loja Global",
                    "integracoes": {},
                }
            ]
        ),
        encoding="utf-8",
    )
    (info_dir / "integracoes.json").write_text(
        json.dumps(
            {"Loja Global": {"mercadoturbo": {"token": "segredo-global"}}}
        ),
        encoding="utf-8",
    )
    (info_dir / "bling_conf.json").write_text(
        json.dumps([{"loja": "Loja Global", "token": "bling-global"}]),
        encoding="utf-8",
    )

    lojas = integracoes.carregar_lojas("tenant-b")

    assert lojas[0]["store_id"] == "tenant-store"
    assert lojas[0]["integracoes"] == {}
    assert not (tenant_b / "integracoes_legacy_migration.json").exists()


def test_migracao_legada_em_config_nao_vazia_e_one_shot(tmp_path):
    tenant_cliente = _configure(tmp_path)
    info_dir = tenant_cliente.parent
    tenant_default = info_dir / "default"
    tenant_default.mkdir(parents=True, exist_ok=True)
    config_path = tenant_default / "lojas_config.json"
    config_path.write_text(
        json.dumps(
            [
                {
                    "store_id": "StoreA",
                    "nome": "Nome Atual",
                    "nomes_anteriores": ["Nome Legado"],
                    "integracoes": {},
                }
            ]
        ),
        encoding="utf-8",
    )
    legado = info_dir / "integracoes.json"
    legado.write_text(
        json.dumps(
            {"Nome Legado": {"bling": {"access_token": "token-primeiro"}}}
        ),
        encoding="utf-8",
    )

    primeira = integracoes.carregar_lojas("default")
    assert primeira[0]["integracoes"]["bling"]["access_token"] == "token-primeiro"
    legado.write_text(
        json.dumps(
            {"Nome Legado": {"mercadoturbo": {"token": "nao-repetir"}}}
        ),
        encoding="utf-8",
    )

    segunda = integracoes.carregar_lojas("default")
    assert "mercadoturbo" not in segunda[0]["integracoes"]


def test_refresh_bling_stale_falha_antes_do_io_e_preserva_homonima(
    monkeypatch,
    tmp_path,
):
    _configure(tmp_path)
    cfg_antiga = {
        "id": "app-antigo",
        "secret": "secret-antigo",
        "access_token": "access-antigo",
        "refresh_token": "refresh-antigo",
    }
    integracoes.salvar_lojas(
        "cliente-a",
        [
            {
                "store_id": "store-antiga",
                "nome": "Loja Igual",
                "integracoes": {"bling": cfg_antiga},
            }
        ],
    )
    integracoes.excluir_loja(
        "cliente-a",
        "Loja Igual",
        store_id="store-antiga",
    )
    nova = integracoes.criar_loja("cliente-a", "Loja Igual")
    integracoes.atualizar_api_loja(
        "cliente-a",
        "Loja Igual",
        "bling",
        {
            "id": "app-novo",
            "secret": "secret-novo",
            "access_token": "access-novo",
            "refresh_token": "refresh-novo",
        },
        store_id=nova["store_id"],
    )
    exchanges = []
    monkeypatch.setattr(
        integracoes,
        "exchange_bling_refresh_token",
        lambda *_args: exchanges.append(True) or {},
    )

    with pytest.raises(HTTPException) as exc_info:
        integracoes.renovar_token_bling_loja(
            "cliente-a",
            "Loja Igual",
            cfg_antiga,
            store_id="store-antiga",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_config_changed"
    assert exchanges == []
    persistida = integracoes.buscar_loja(
        "cliente-a",
        "Loja Igual",
        store_id=nova["store_id"],
    )
    assert persistida["integracoes"]["bling"]["refresh_token"] == "refresh-novo"


def test_fachada_bling_repassa_store_id_exato(monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        bling_vendas,
        "_atualizar_api_loja_fn",
        lambda *args, **kwargs: chamadas.append((args, kwargs)),
    )

    bling_vendas.atualizar_api_loja(
        "cliente-a",
        "Loja Igual",
        "bling",
        {"access_token": "token"},
        store_id="StoreA",
    )

    assert chamadas[0][1]["store_id"] == "StoreA"


def test_endpoint_de_mutacao_usa_store_id_exato(tmp_path):
    _configure(tmp_path)
    integracoes.salvar_lojas("cliente-a", _homonimas())

    resposta = asyncio.run(
        integracoes_api.save_turbo_token(
            "Loja Igual",
            TokenRequest(token="token-b"),
            store_id="storea",
            client_id="cliente-a",
        )
    )

    assert resposta == {"success": True}
    por_id = {loja["store_id"]: loja for loja in integracoes.carregar_lojas("cliente-a")}
    assert "mercadoturbo" not in por_id["StoreA"]["integracoes"]
    assert por_id["storea"]["integracoes"]["mercadoturbo"]["token"] == "token-b"


def test_create_loja_anexa_identidade_nova_mesmo_com_nome_homonimo(tmp_path):
    _configure(tmp_path)

    primeira = asyncio.run(
        integracoes_api.create_loja(StoreRequest(nome="Loja Igual"), "cliente-a")
    )
    segunda = asyncio.run(
        integracoes_api.create_loja(StoreRequest(nome="Loja Igual"), "cliente-a")
    )

    assert primeira["success"] is True
    assert segunda["success"] is True
    assert primeira["store_id"] == primeira["loja"]["store_id"]
    assert segunda["store_id"] == segunda["loja"]["store_id"]
    assert primeira["store_id"] != segunda["store_id"]
    persistidas = integracoes.carregar_lojas("cliente-a")
    assert [loja["nome"] for loja in persistidas] == ["Loja Igual", "Loja Igual"]
    assert {loja["store_id"] for loja in persistidas} == {
        primeira["store_id"],
        segunda["store_id"],
    }


def test_temp_auth_endpoint_ignora_state_e_tenant_do_payload(monkeypatch, tmp_path):
    _configure(tmp_path)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "StoreA", "nome": "Loja A", "integracoes": {}}],
    )
    state_servidor = "state-servidor-123456789012345678901234"
    monkeypatch.setattr(
        integracoes.secrets,
        "token_urlsafe",
        lambda _n: state_servidor,
    )

    resposta = asyncio.run(
        integracoes_api.save_temp_auth_endpoint(
            {
                "store_id": "StoreA",
                "loja": "Nome Forjado",
                "state": "state-escolhido-pelo-cliente-1234567890",
                "client_id": "cliente-b",
                "tenant": "cliente-b",
                "tenant_id": "cliente-b",
                "servico": "bling",
                "id": "app-a",
            },
            "cliente-a",
        )
    )

    assert resposta == {
        "success": True,
        "state": state_servidor,
        "store_id": "StoreA",
    }
    assert integracoes.ler_temp_auth(
        "state-escolhido-pelo-cliente-1234567890"
    ) is None
    fluxo = integracoes.ler_temp_auth(state_servidor)
    assert fluxo["client_id"] == "cliente-a"
    assert fluxo["store_id"] == "StoreA"
    assert fluxo["loja"] == "Loja A"
    assert "tenant" not in fluxo
    assert "tenant_id" not in fluxo


def test_temp_auth_endpoint_nao_aceita_store_de_outro_tenant(tmp_path):
    _configure(tmp_path)
    integracoes.salvar_lojas(
        "cliente-b",
        [{"store_id": "SomenteB", "nome": "Loja B", "integracoes": {}}],
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            integracoes_api.save_temp_auth_endpoint(
                {
                    "store_id": "SomenteB",
                    "state": "state-forjado-12345678901234567890",
                    "client_id": "cliente-b",
                },
                "cliente-a",
            )
        )

    assert exc_info.value.status_code == 404
    assert integracoes.ler_temp_auth("state-forjado-12345678901234567890") is None


def test_oauth_mantem_fluxos_por_state_e_store_id_sem_sobrescrever(
    monkeypatch,
    tmp_path,
):
    _configure(tmp_path)
    integracoes.salvar_lojas("cliente-a", _homonimas())
    states = iter([
        "state-a-12345678901234567890",
        "state-b-12345678901234567890",
        "connection-a-123456789012345",
        "connection-b-123456789012345",
    ])
    monkeypatch.setattr(integracoes_api.secrets, "token_urlsafe", lambda _n: next(states))
    monkeypatch.setattr(
        integracoes_api,
        "_resolver_redirect_uri_bling",
        lambda **_kwargs: "https://jk.local/callback",
    )
    monkeypatch.setattr(
        integracoes_api,
        "auth_bling_exchange",
        lambda app_id, _secret, _code, redirect_uri=None: (
            True,
            {
                "access_token": f"access-{app_id}",
                "refresh_token": f"refresh-{app_id}",
            },
        ),
    )

    asyncio.run(
        integracoes_api.start_bling_auth(
            AuthRequest(
                loja="Loja Igual",
                store_id="StoreA",
                client_id="app-a",
                client_secret="secret-a",
            ),
            object(),
            "cliente-a",
        )
    )
    asyncio.run(
        integracoes_api.start_bling_auth(
            AuthRequest(
                loja="Loja Igual",
                store_id="storea",
                client_id="app-b",
                client_secret="secret-b",
            ),
            object(),
            "cliente-a",
        )
    )

    assert integracoes.ler_temp_auth("state-a-12345678901234567890")["store_id"] == "StoreA"
    assert integracoes.ler_temp_auth("state-b-12345678901234567890")["store_id"] == "storea"

    asyncio.run(
        integracoes_api.integracoes_auth_callback(
            object(),
            code="code-a",
            state="state-a-12345678901234567890",
        )
    )
    assert integracoes.ler_temp_auth("state-a-12345678901234567890") is None
    assert integracoes.ler_temp_auth("state-b-12345678901234567890") is not None

    asyncio.run(
        integracoes_api.integracoes_auth_callback(
            object(),
            code="code-b",
            state="state-b-12345678901234567890",
        )
    )
    por_id = {loja["store_id"]: loja for loja in integracoes.carregar_lojas("cliente-a")}
    assert por_id["StoreA"]["integracoes"]["bling"]["id"] == "app-a"
    assert por_id["storea"]["integracoes"]["bling"]["id"] == "app-b"
    assert (
        por_id["StoreA"]["integracoes"]["bling"]["oauth_connection_id"]
        == "connection-a-123456789012345"
    )
    assert (
        por_id["storea"]["integracoes"]["bling"]["oauth_connection_id"]
        == "connection-b-123456789012345"
    )


def test_oauth_exige_state_e_revalida_loja_antes_do_exchange(monkeypatch, tmp_path):
    _configure(tmp_path)
    integracoes.salvar_lojas("cliente-a", _homonimas())
    state = "state-seguro-12345678901234567890"
    monkeypatch.setattr(integracoes_api.secrets, "token_urlsafe", lambda _n: state)
    monkeypatch.setattr(
        integracoes_api,
        "_resolver_redirect_uri_bling",
        lambda **_kwargs: "https://jk.local/callback",
    )
    exchanges = []
    monkeypatch.setattr(
        integracoes_api,
        "auth_bling_exchange",
        lambda *_args, **_kwargs: exchanges.append(True) or (True, {}),
    )
    asyncio.run(
        integracoes_api.start_bling_auth(
            AuthRequest(
                loja="Loja Igual",
                store_id="StoreA",
                client_id="app-a",
                client_secret="secret-a",
            ),
            object(),
            "cliente-a",
        )
    )

    resposta_sem_state = asyncio.run(
        integracoes_api.integracoes_auth_callback(object(), code="code-a")
    )
    assert "status=error" in resposta_sem_state.headers["location"]
    assert integracoes.ler_temp_auth(state) is not None

    lojas = integracoes.carregar_lojas("cliente-a")
    lojas[0]["nome"] = "Renomeada durante OAuth"
    integracoes.salvar_lojas("cliente-a", lojas)
    resposta_renome = asyncio.run(
        integracoes_api.integracoes_auth_callback(
            object(),
            code="code-a",
            state=state,
        )
    )
    assert "status=error" in resposta_renome.headers["location"]
    assert exchanges == []
    assert integracoes.ler_temp_auth(state) is None


@pytest.mark.parametrize("deleted_at", ["", "2026-08-28T12:00:00Z"])
def test_delete_bloqueia_registro_ativo_ou_tombstone_do_cadastro(tmp_path, deleted_at):
    tenant = _configure(tmp_path)
    integracoes.salvar_lojas("cliente-a", _homonimas())
    (tenant / "cadastro_produtos_lojas.csv").write_text(
        "store_id,sku,sku_normalizado,deleted_at_utc\n"
        f"StoreA,SKU-1,SKU-1,{deleted_at}\n",
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc_info:
        integracoes.excluir_loja(
            "cliente-a", "Loja Igual", store_id="StoreA"
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_has_catalog_records"
    assert {loja["store_id"] for loja in integracoes.carregar_lojas("cliente-a")} == {
        "StoreA",
        "storea",
    }
    assert not (tenant / "lojas_sync_tombstones.json").exists()


def test_delete_remove_uma_homonima_e_tombstone_vem_depois_da_persistencia(
    monkeypatch, tmp_path
):
    tenant = _configure(tmp_path)
    integracoes.salvar_lojas("cliente-a", _homonimas())
    eventos = []
    salvar_real = integracoes.salvar_lojas
    tombstone_real = integracoes.registrar_tombstone_integracao

    def salvar_observado(client_id, lojas, **kwargs):
        resultado = salvar_real(client_id, lojas, **kwargs)
        eventos.append(("salvou", [loja["store_id"] for loja in lojas]))
        return resultado

    def tombstone_observado(client_id, **kwargs):
        persistidas = integracoes.carregar_lojas(client_id)
        eventos.append(("tombstone", [loja["store_id"] for loja in persistidas]))
        return tombstone_real(client_id, **kwargs)

    monkeypatch.setattr(integracoes, "salvar_lojas", salvar_observado)
    monkeypatch.setattr(integracoes, "registrar_tombstone_integracao", tombstone_observado)

    removida = integracoes.excluir_loja(
        "cliente-a", "Loja Igual", store_id="StoreA"
    )

    assert removida["store_id"] == "StoreA"
    assert eventos == [("salvou", ["storea"]), ("tombstone", ["storea"])]
    tombstones = json.loads(
        (tenant / "lojas_sync_tombstones.json").read_text(encoding="utf-8")
    )
    assert [item["store_id"] for item in tombstones] == ["StoreA"]


def test_delete_nao_publica_tombstone_quando_persistencia_falha(monkeypatch, tmp_path):
    tenant = _configure(tmp_path)
    integracoes.salvar_lojas("cliente-a", _homonimas())
    antes = (tenant / "lojas_config.json").read_bytes()
    publicou = []

    def falhar_salvamento(*_args, **_kwargs):
        raise HTTPException(status_code=500, detail="falha simulada")

    monkeypatch.setattr(integracoes, "salvar_lojas", falhar_salvamento)
    monkeypatch.setattr(
        integracoes,
        "registrar_tombstone_integracao",
        lambda *_args, **_kwargs: publicou.append(True),
    )

    with pytest.raises(HTTPException) as exc_info:
        integracoes.excluir_loja(
            "cliente-a", "Loja Igual", store_id="StoreA"
        )

    assert exc_info.value.status_code == 500
    assert publicou == []
    assert (tenant / "lojas_config.json").read_bytes() == antes


def test_delete_reverte_configuracao_quando_tombstone_falha(monkeypatch, tmp_path):
    tenant = _configure(tmp_path)
    integracoes.salvar_lojas("cliente-a", _homonimas())
    config_path = tenant / "lojas_config.json"
    antes = config_path.read_bytes()

    def falhar_tombstone(*_args, **_kwargs):
        raise HTTPException(status_code=500, detail="falha simulada")

    monkeypatch.setattr(
        integracoes,
        "registrar_tombstone_integracao",
        falhar_tombstone,
    )

    with pytest.raises(HTTPException) as exc_info:
        integracoes.excluir_loja(
            "cliente-a",
            "Loja Igual",
            store_id="StoreA",
        )

    assert exc_info.value.status_code == 500
    assert config_path.read_bytes() == antes
    assert not (tenant / "lojas_sync_tombstones.json").exists()


def test_delete_bloqueia_sombra_legada_mesmo_com_nome_homonimo(tmp_path):
    tenant = _configure(tmp_path)
    integracoes.salvar_lojas("cliente-a", _homonimas())
    (tenant / "cadastro_produtos.csv").write_text(
        "sku,loja_sync,nome\nSKU-1,Loja Igual,Produto legado\n",
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc_info:
        integracoes.excluir_loja(
            "cliente-a",
            "Loja Igual",
            store_id="StoreA",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_has_legacy_catalog_records"
    assert {loja["store_id"] for loja in integracoes.carregar_lojas("cliente-a")} == {
        "StoreA",
        "storea",
    }


def test_delete_sombra_historica_usa_store_id_exato_e_ignora_nome_conflitante(
    tmp_path,
):
    tenant = _configure(tmp_path)
    integracoes.salvar_lojas("cliente-a", _homonimas())
    (tenant / "cadastro_produtos.csv").write_text(
        "store_id,sku,loja_sync,nome\n"
        "storea,SKU-1,Loja Igual,Produto da outra identidade\n",
        encoding="utf-8",
    )

    removida = integracoes.excluir_loja(
        "cliente-a",
        "Loja Igual",
        store_id="StoreA",
    )

    assert removida["store_id"] == "StoreA"
    assert [loja["store_id"] for loja in integracoes.carregar_lojas("cliente-a")] == [
        "storea"
    ]


def test_delete_bloqueia_store_id_exato_em_cadastro_historico(tmp_path):
    tenant = _configure(tmp_path)
    integracoes.salvar_lojas("cliente-a", _homonimas())
    (tenant / "cadastro_produtos.csv").write_text(
        "store_id,sku,loja_sync,nome\n"
        "StoreA,SKU-1,Nome conflitante,Produto da identidade exata\n",
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc_info:
        integracoes.excluir_loja(
            "cliente-a",
            "Loja Igual",
            store_id="StoreA",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_has_legacy_catalog_records"


def test_reducao_confirmada_tambem_bloqueia_loja_com_cadastro(tmp_path):
    tenant = _configure(tmp_path)
    lojas = _homonimas()
    integracoes.salvar_lojas("cliente-a", lojas)
    (tenant / "cadastro_produtos_lojas.csv").write_text(
        "store_id,sku,sku_normalizado,deleted_at_utc\n"
        "StoreA,SKU-1,SKU-1,\n",
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc_info:
        integracoes.salvar_lojas(
            "cliente-a",
            [lojas[1]],
            permitir_reducao_confirmada=True,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_has_catalog_records"


def test_delete_mantem_lock_canonico_do_cadastro_ate_publicar_tombstone(
    monkeypatch,
    tmp_path,
):
    _configure(tmp_path)
    integracoes.salvar_lojas("cliente-a", _homonimas())
    locks_ativos = []
    check_real = integracoes._integracoes_cadastro_tem_registros_store
    salvar_real = integracoes.salvar_lojas

    @contextmanager
    def lock_observado(path):
        locks_ativos.append(str(path))
        try:
            yield
        finally:
            locks_ativos.pop()

    def check_observado(*args, **kwargs):
        assert any(path.endswith("cadastro_produtos_lojas.csv") for path in locks_ativos)
        return check_real(*args, **kwargs)

    def salvar_observado(*args, **kwargs):
        assert any(path.endswith("cadastro_produtos_lojas.csv") for path in locks_ativos)
        assert any(path.endswith("cadastro_custos_lojas.csv") for path in locks_ativos)
        return salvar_real(*args, **kwargs)

    def tombstone_observado(*_args, **_kwargs):
        assert any(path.endswith("cadastro_produtos_lojas.csv") for path in locks_ativos)
        assert any(path.endswith("cadastro_custos_lojas.csv") for path in locks_ativos)

    monkeypatch.setattr(integracoes, "path_lock_for", lock_observado)
    monkeypatch.setattr(
        integracoes,
        "_integracoes_cadastro_tem_registros_store",
        check_observado,
    )
    monkeypatch.setattr(integracoes, "salvar_lojas", salvar_observado)
    monkeypatch.setattr(
        integracoes,
        "registrar_tombstone_integracao",
        tombstone_observado,
    )

    integracoes.excluir_loja(
        "cliente-a",
        "Loja Igual",
        store_id="StoreA",
    )

    assert locks_ativos == []


def test_renome_preserva_lista_versionada_de_nomes_anteriores_sem_duplicatas(tmp_path):
    tenant = _configure(tmp_path)
    lojas = [
        {
            "store_id": "store-a",
            "nome": "Nome Inicial",
            "nomes_anteriores": ["Nome Antigo"],
            "integracoes": {},
        }
    ]
    integracoes.salvar_lojas("cliente-a", lojas)
    versao_inicial = lojas[0]["_sync_version"]

    integracoes.salvar_lojas(
        "cliente-a",
        [
            {
                "store_id": "store-a",
                "nome": "Nome Novo",
                "nomes_anteriores": ["Nome Antigo", "Nome Antigo"],
                "integracoes": {},
            }
        ],
    )
    renomeada = json.loads(
        (tenant / "lojas_config.json").read_text(encoding="utf-8")
    )[0]

    assert renomeada["nomes_anteriores"] == ["Nome Antigo", "Nome Inicial"]
    assert renomeada["_sync_version"] == versao_inicial + 1
    assert integracoes.buscar_loja("cliente-a", "Nome Inicial")["store_id"] == "store-a"

    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-a", "nome": "Nome Final", "integracoes": {}}],
    )
    final = json.loads((tenant / "lojas_config.json").read_text(encoding="utf-8"))[0]
    assert final["nomes_anteriores"] == [
        "Nome Antigo",
        "Nome Inicial",
        "Nome Novo",
    ]
