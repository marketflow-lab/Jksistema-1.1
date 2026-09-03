from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from backend.routers.importacoes import create_importacoes_router
from backend.services.importacoes_tracking import (
    DATALASTIC_VESSEL_URL,
    ErroRastreamentoNavio,
    identificar_navio,
    limpar_cache_rastreamento_navio,
    rastrear_navio_datalastic,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _limpar_cache():
    limpar_cache_rastreamento_navio()
    yield
    limpar_cache_rastreamento_navio()


def test_identificador_exige_imo_ou_mmsi_valido():
    assert identificar_navio(imo="9996678") == ("imo", "9996678")
    assert identificar_navio(mmsi="249369000") == ("mmsi", "249369000")

    with pytest.raises(ErroRastreamentoNavio) as ausente:
        identificar_navio()
    assert ausente.value.codigo == "identificador_ausente"

    with pytest.raises(ErroRastreamentoNavio) as imo_invalido:
        identificar_navio(imo="9996679")
    assert imo_invalido.value.codigo == "imo_invalido"

    with pytest.raises(ErroRastreamentoNavio) as mmsi_invalido:
        identificar_navio(mmsi="123")
    assert mmsi_invalido.value.codigo == "mmsi_invalido"


def test_rastreamento_normaliza_resposta_e_mantem_chave_no_header():
    chamadas = []

    def fake_get(url, *, params, headers, timeout):
        chamadas.append(
            {"url": url, "params": params, "headers": headers, "timeout": timeout}
        )
        return FakeResponse(
            {
                "data": {
                    "name": "CMA CGM IRON",
                    "mmsi": "249369000",
                    "imo": "9996678",
                    "country_iso": "MT",
                    "type_specific": "Container Ship",
                    "lat": -23.95,
                    "lon": -46.31,
                    "speed": 17.2,
                    "course": 221,
                    "heading": 220,
                    "navigation_status": "Under way using engine",
                    "destination": "SANTOS",
                    "last_position_UTC": "2026-08-26T14:00:00Z",
                    "eta_UTC": "2026-10-08T08:00:00Z",
                },
                "meta": {"success": True},
            }
        )

    resposta = rastrear_navio_datalastic(
        imo="9996678",
        environ={
            "JK_DATALASTIC_API_KEY": "segredo-teste",
            "JK_DATALASTIC_TIMEOUT_SECONDS": "5",
        },
        http_get=fake_get,
        now_fn=lambda: 100.0,
    )

    assert resposta["status"] == "ok"
    assert resposta["navio"]["nome"] == "CMA CGM IRON"
    assert resposta["navio"]["imo"] == "9996678"
    assert resposta["posicao"]["latitude"] == -23.95
    assert resposta["posicao"]["longitude"] == -46.31
    assert resposta["viagem"]["destino"] == "SANTOS"
    assert resposta["cached"] is False
    assert chamadas == [
        {
            "url": DATALASTIC_VESSEL_URL,
            "params": {"imo": "9996678"},
            "headers": {"Accept": "application/json", "x-api-key": "segredo-teste"},
            "timeout": 5.0,
        }
    ]
    assert "segredo-teste" not in str(chamadas[0]["params"])

    resposta_cache = rastrear_navio_datalastic(
        imo="9996678",
        environ={"JK_DATALASTIC_API_KEY": "segredo-teste"},
        http_get=fake_get,
        now_fn=lambda: 110.0,
    )
    assert resposta_cache["cached"] is True
    assert len(chamadas) == 1


def test_rastreamento_falha_de_forma_clara_sem_credencial():
    with pytest.raises(ErroRastreamentoNavio) as erro:
        rastrear_navio_datalastic(imo="9996678", environ={})
    assert erro.value.codigo == "api_nao_configurada"
    assert erro.value.status_code == 503


def test_rastreamento_trata_timeout_quota_e_posicao_ausente():
    def timeout_get(*_args, **_kwargs):
        raise requests.Timeout("timeout")

    with pytest.raises(ErroRastreamentoNavio) as timeout:
        rastrear_navio_datalastic(
            imo="9996678",
            environ={"JK_DATALASTIC_API_KEY": "teste"},
            http_get=timeout_get,
        )
    assert timeout.value.codigo == "api_timeout"
    assert timeout.value.status_code == 504

    with pytest.raises(ErroRastreamentoNavio) as creditos:
        rastrear_navio_datalastic(
            imo="9996678",
            environ={"JK_DATALASTIC_API_KEY": "teste"},
            http_get=lambda *_args, **_kwargs: FakeResponse({}, status_code=402),
        )
    assert creditos.value.codigo == "api_creditos_esgotados"

    with pytest.raises(ErroRastreamentoNavio) as quota:
        rastrear_navio_datalastic(
            imo="9996678",
            environ={"JK_DATALASTIC_API_KEY": "teste"},
            http_get=lambda *_args, **_kwargs: FakeResponse({}, status_code=429),
        )
    assert quota.value.codigo == "api_limite_excedido"

    with pytest.raises(ErroRastreamentoNavio) as sem_posicao:
        rastrear_navio_datalastic(
            imo="9996678",
            environ={"JK_DATALASTIC_API_KEY": "teste"},
            http_get=lambda *_args, **_kwargs: FakeResponse(
                {"data": {"name": "CMA CGM IRON"}, "meta": {"success": True}}
            ),
        )
    assert sem_posicao.value.codigo == "posicao_indisponivel"


def test_router_expoe_endpoint_e_protege_com_dependencia_quando_disponivel():
    async def get_tenant_id():
        return "cliente"

    router = create_importacoes_router(SimpleNamespace(get_tenant_id=get_tenant_id))
    rota = next(
        item
        for item in router.routes
        if item.path == "/api/importacoes/rastreamento/navio"
    )
    assert rota.methods == {"GET"}
    assert len(rota.dependencies) == 1
