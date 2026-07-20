import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from backend.services import integracoes


def _configure(tmp_path):
    info_dir = tmp_path / "info"

    def tenant_path(client_id):
        path = info_dir / str(client_id or "default")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda servico, dados: dados,
    )
    return info_dir / "000002" / "lojas_config.json"


def _lojas(qtd):
    return [
        {"nome": f"Loja {idx}", "integracoes": {"mercadolivre": {"connected": True, "access_token": f"tok-{idx}"}}}
        for idx in range(1, qtd + 1)
    ]


def _bling_lojas():
    return [
        {
            "nome": "Loja A",
            "integracoes": {
                "bling": {
                    "id": "client-a",
                    "secret": "secret-a",
                    "access_token": "access-a-old",
                    "refresh_token": "refresh-a-old",
                    "connected": True,
                }
            },
        },
        {
            "nome": "Loja B",
            "integracoes": {
                "bling": {
                    "id": "client-b",
                    "secret": "secret-b",
                    "access_token": "access-b-old",
                    "refresh_token": "refresh-b-old",
                    "connected": True,
                }
            },
        },
    ]


def test_salvar_lojas_bloqueia_snapshot_regressivo(tmp_path):
    arquivo = _configure(tmp_path)
    boas = _lojas(4)
    integracoes.salvar_lojas("000002", boas)

    with pytest.raises(HTTPException) as exc:
        integracoes.salvar_lojas("000002", [boas[0]])

    assert exc.value.status_code == 409
    assert [loja["nome"] for loja in json.loads(arquivo.read_text(encoding="utf-8"))] == [
        "Loja 1",
        "Loja 2",
        "Loja 3",
        "Loja 4",
    ]


def test_carregar_lojas_restaurar_backup_imediato_quando_arquivo_fica_vazio(tmp_path):
    arquivo = _configure(tmp_path)
    boas = _lojas(4)
    integracoes.salvar_lojas("000002", boas)
    integracoes.salvar_lojas("000002", boas)
    arquivo.write_text("", encoding="utf-8")

    restauradas = integracoes.carregar_lojas("000002")

    assert [loja["nome"] for loja in restauradas] == ["Loja 1", "Loja 2", "Loja 3", "Loja 4"]
    assert [loja["nome"] for loja in json.loads(arquivo.read_text(encoding="utf-8"))] == [
        "Loja 1",
        "Loja 2",
        "Loja 3",
        "Loja 4",
    ]


def test_refresh_single_flight_mesma_loja_faz_um_post(tmp_path, monkeypatch):
    _configure(tmp_path)
    lojas = _bling_lojas()
    integracoes.salvar_lojas("000002", lojas)
    snapshot = dict(lojas[0]["integracoes"]["bling"])
    chamadas = 0
    chamadas_lock = threading.Lock()

    def exchange(client_id, client_secret, refresh_token):
        nonlocal chamadas
        with chamadas_lock:
            chamadas += 1
        return {"access_token": "access-a-new", "refresh_token": "refresh-a-new"}

    monkeypatch.setattr(integracoes, "exchange_bling_refresh_token", exchange)
    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(
            executor.map(
                lambda _: integracoes.renovar_token_bling_loja("000002", "Loja A", snapshot),
                range(2),
            )
        )

    assert chamadas == 1
    assert {item["access_token"] for item in resultados} == {"access-a-new"}
    assert integracoes.buscar_loja("000002", "Loja A")["integracoes"]["bling"]["refresh_token"] == "refresh-a-new"


def test_refresh_single_flight_sem_rotacao_do_refresh_token(tmp_path, monkeypatch):
    _configure(tmp_path)
    lojas = _bling_lojas()
    integracoes.salvar_lojas("000002", lojas)
    snapshot = dict(lojas[0]["integracoes"]["bling"])
    chamadas = 0

    def exchange(client_id, client_secret, refresh_token):
        nonlocal chamadas
        chamadas += 1
        return {"access_token": "access-a-new"}

    monkeypatch.setattr(integracoes, "exchange_bling_refresh_token", exchange)
    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(
            executor.map(
                lambda _: integracoes.renovar_token_bling_loja("000002", "Loja A", snapshot),
                range(2),
            )
        )

    assert chamadas == 1
    assert {item["access_token"] for item in resultados} == {"access-a-new"}
    assert {item["refresh_token"] for item in resultados} == {"refresh-a-old"}


def test_refresh_concorrente_de_duas_lojas_preserva_ambos_tokens(tmp_path, monkeypatch):
    _configure(tmp_path)
    lojas = _bling_lojas()
    integracoes.salvar_lojas("000002", lojas)
    snapshots = {loja["nome"]: dict(loja["integracoes"]["bling"]) for loja in lojas}
    barrier = threading.Barrier(2)

    def exchange(client_id, client_secret, refresh_token):
        barrier.wait(timeout=3)
        sufixo = "a" if client_id == "client-a" else "b"
        return {
            "access_token": f"access-{sufixo}-new",
            "refresh_token": f"refresh-{sufixo}-new",
        }

    monkeypatch.setattr(integracoes, "exchange_bling_refresh_token", exchange)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(integracoes.renovar_token_bling_loja, "000002", nome, snapshots[nome])
            for nome in ("Loja A", "Loja B")
        ]
        [future.result(timeout=5) for future in futures]

    assert integracoes.buscar_loja("000002", "Loja A")["integracoes"]["bling"]["refresh_token"] == "refresh-a-new"
    assert integracoes.buscar_loja("000002", "Loja B")["integracoes"]["bling"]["refresh_token"] == "refresh-b-new"


def test_resultado_antigo_nao_sobrescreve_reconexao(tmp_path, monkeypatch):
    _configure(tmp_path)
    lojas = _bling_lojas()
    integracoes.salvar_lojas("000002", lojas)
    snapshot = dict(lojas[0]["integracoes"]["bling"])

    def exchange(client_id, client_secret, refresh_token):
        integracoes.atualizar_api_loja(
            "000002",
            "Loja A",
            "bling",
            {
                "access_token": "access-reconnected",
                "refresh_token": "refresh-reconnected",
                "connected": True,
            },
        )
        return {"access_token": "access-stale", "refresh_token": "refresh-stale"}

    monkeypatch.setattr(integracoes, "exchange_bling_refresh_token", exchange)
    resultado = integracoes.renovar_token_bling_loja("000002", "Loja A", snapshot)

    assert resultado["access_token"] == "access-reconnected"
    persistido = integracoes.buscar_loja("000002", "Loja A")["integracoes"]["bling"]
    assert persistido["refresh_token"] == "refresh-reconnected"
    assert persistido["connected"] is True


def test_resultado_antigo_nao_sobrescreve_reconexao_com_mesmo_refresh(tmp_path, monkeypatch):
    _configure(tmp_path)
    lojas = _bling_lojas()
    integracoes.salvar_lojas("000002", lojas)
    snapshot = dict(lojas[0]["integracoes"]["bling"])

    def exchange(client_id, client_secret, refresh_token):
        integracoes.atualizar_api_loja(
            "000002",
            "Loja A",
            "bling",
            {
                "access_token": "access-reconnected",
                "refresh_token": refresh_token,
                "connected": True,
                "updated_at": "reconnected-now",
            },
        )
        return {"access_token": "access-stale", "refresh_token": refresh_token}

    monkeypatch.setattr(integracoes, "exchange_bling_refresh_token", exchange)
    resultado = integracoes.renovar_token_bling_loja("000002", "Loja A", snapshot)

    assert resultado["access_token"] == "access-reconnected"
    assert integracoes.buscar_loja("000002", "Loja A")["integracoes"]["bling"]["access_token"] == "access-reconnected"


def test_invalid_grant_obsoleto_nao_invalida_token_reconectado(tmp_path, monkeypatch):
    _configure(tmp_path)
    lojas = _bling_lojas()
    integracoes.salvar_lojas("000002", lojas)
    snapshot = dict(lojas[0]["integracoes"]["bling"])

    def exchange(client_id, client_secret, refresh_token):
        integracoes.atualizar_api_loja(
            "000002",
            "Loja A",
            "bling",
            {
                "access_token": "access-reconnected",
                "refresh_token": "refresh-reconnected",
                "connected": True,
                "oauth_invalid": False,
            },
        )
        raise HTTPException(status_code=401, detail="invalid_grant")

    monkeypatch.setattr(integracoes, "exchange_bling_refresh_token", exchange)
    resultado = integracoes.renovar_token_bling_loja("000002", "Loja A", snapshot)

    assert resultado["refresh_token"] == "refresh-reconnected"
    assert resultado["connected"] is True
    assert resultado.get("oauth_invalid") is False
