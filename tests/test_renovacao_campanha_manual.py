import datetime as dt

import pytest
from fastapi import HTTPException

from backend.routers.renovacao import RenovacaoRouterConfig, create_renovacao_router
from backend.schemas.renovacao import RenovacaoCampanhaManualCriarRequest
from backend.services import renovacao_promocoes


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _datas_validas() -> tuple[str, str]:
    inicio = dt.date.today() + dt.timedelta(days=1)
    fim = inicio + dt.timedelta(days=14)
    return inicio.isoformat(), fim.isoformat()


def test_criar_campanha_manual_envia_payload_oficial_sem_copiar_itens(monkeypatch):
    inicio, fim = _datas_validas()
    chamadas = []
    invalidacoes = []
    cfg = {"store": "JK Pecas"}

    monkeypatch.setattr(renovacao_promocoes.ctx, "_obter_cfg_ml", lambda client_id, loja: cfg)

    def fake_request(client_id, loja, request_cfg, method, url, **kwargs):
        chamadas.append((client_id, loja, request_cfg, method, url, kwargs))
        return _FakeResponse(201, {"id": "C-MLB123"}), request_cfg

    monkeypatch.setattr(renovacao_promocoes.ctx, "_ml_api_request", fake_request)
    monkeypatch.setattr(
        renovacao_promocoes.ctx,
        "_cache_invalidar_loja",
        lambda client_id, loja: invalidacoes.append((client_id, loja)),
    )

    resultado = renovacao_promocoes._renovacao_criar_campanha_manual_ml(
        "tenant-1",
        "  JK Pecas  ",
        "  Promocao Agosto  ",
        inicio,
        fim,
    )

    assert len(chamadas) == 1
    client_id, loja, request_cfg, method, url, kwargs = chamadas[0]
    assert (client_id, loja, request_cfg, method) == ("tenant-1", "JK Pecas", cfg, "POST")
    assert url == "https://api.mercadolibre.com/seller-promotions/promotions"
    assert "/items/" not in url
    assert kwargs["params"] == {"app_version": "v2"}
    assert kwargs["json"] == {
        "promotion_type": "SELLER_CAMPAIGN",
        "sub_type": "FLEXIBLE_PERCENTAGE",
        "name": "Promocao Agosto",
        "start_date": f"{inicio}T00:00:00",
        "finish_date": f"{fim}T23:59:59",
    }
    assert invalidacoes == [("tenant-1", "JK Pecas")]
    assert resultado == {
        "success": True,
        "loja": "JK Pecas",
        "nova_campanha": {
            "id": "C-MLB123",
            "nome": "Promocao Agosto",
            "start_date": f"{inicio}T00:00:00",
            "finish_date": f"{fim}T23:59:59",
            "promotion_type": "SELLER_CAMPAIGN",
            "sub_type": "FLEXIBLE_PERCENTAGE",
        },
    }


@pytest.mark.parametrize(
    ("loja", "nome", "inicio", "fim", "mensagem"),
    [
        ("", "Campanha", "2099-01-01", "2099-01-02", "Informe a loja"),
        ("Loja", "  ", "2099-01-01", "2099-01-02", "Informe o nome"),
        ("Loja", "Campanha", "01/01/2099", "2099-01-02", "YYYY-MM-DD"),
        ("Loja", "Campanha", "2099-02-30", "2099-03-01", "invalida"),
    ],
)
def test_criar_campanha_manual_rejeita_campos_invalidos_sem_chamar_ml(
    monkeypatch,
    loja,
    nome,
    inicio,
    fim,
    mensagem,
):
    monkeypatch.setattr(
        renovacao_promocoes.ctx,
        "_obter_cfg_ml",
        lambda *_args, **_kwargs: pytest.fail("Nao deveria consultar configuracao do ML"),
    )

    with pytest.raises(HTTPException) as exc_info:
        renovacao_promocoes._renovacao_criar_campanha_manual_ml(
            "tenant-1",
            loja,
            nome,
            inicio,
            fim,
        )

    assert exc_info.value.status_code == 400
    assert mensagem in str(exc_info.value.detail)


def test_criar_campanha_manual_valida_periodo_local_sem_chamar_ml(monkeypatch):
    hoje = dt.date.today()
    casos = [
        (hoje - dt.timedelta(days=1), hoje, "anterior a hoje"),
        (hoje + dt.timedelta(days=2), hoje + dt.timedelta(days=1), "anterior a data inicial"),
    ]
    monkeypatch.setattr(
        renovacao_promocoes.ctx,
        "_obter_cfg_ml",
        lambda *_args, **_kwargs: pytest.fail("Nao deveria consultar configuracao do ML"),
    )

    for inicio, fim, mensagem in casos:
        with pytest.raises(HTTPException) as exc_info:
            renovacao_promocoes._renovacao_criar_campanha_manual_ml(
                "tenant-1",
                "Loja",
                "Campanha",
                inicio.isoformat(),
                fim.isoformat(),
            )
        assert exc_info.value.status_code == 400
        assert mensagem in str(exc_info.value.detail)


def test_criar_campanha_manual_preserva_erro_do_mercado_livre(monkeypatch):
    inicio_data = dt.date.today() + dt.timedelta(days=1)
    fim_data = inicio_data + dt.timedelta(days=45)
    inicio, fim = inicio_data.isoformat(), fim_data.isoformat()
    chamadas = []
    invalidacoes = []
    monkeypatch.setattr(renovacao_promocoes.ctx, "_obter_cfg_ml", lambda *_args: {})

    def fake_request(*args, **kwargs):
        chamadas.append((args, kwargs))
        return _FakeResponse(400, {"message": "Maximum period cannot exceed the allowed limit."}), {}

    monkeypatch.setattr(
        renovacao_promocoes.ctx,
        "_ml_api_request",
        fake_request,
    )
    monkeypatch.setattr(
        renovacao_promocoes.ctx,
        "_ml_parse_error_detail",
        lambda _resp, _fallback: "Maximum period cannot exceed the allowed limit.",
    )
    monkeypatch.setattr(
        renovacao_promocoes.ctx,
        "_cache_invalidar_loja",
        lambda *args: invalidacoes.append(args),
    )

    with pytest.raises(HTTPException) as exc_info:
        renovacao_promocoes._renovacao_criar_campanha_manual_ml(
            "tenant-1", "Loja", "Campanha", inicio, fim
        )

    assert len(chamadas) == 1
    assert chamadas[0][1]["json"]["start_date"] == f"{inicio}T00:00:00"
    assert chamadas[0][1]["json"]["finish_date"] == f"{fim}T23:59:59"
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Maximum period cannot exceed the allowed limit."
    assert invalidacoes == []


def test_criar_campanha_manual_rejeita_sucesso_sem_id(monkeypatch):
    inicio, fim = _datas_validas()
    monkeypatch.setattr(renovacao_promocoes.ctx, "_obter_cfg_ml", lambda *_args: {})
    monkeypatch.setattr(
        renovacao_promocoes.ctx,
        "_ml_api_request",
        lambda *_args, **_kwargs: (_FakeResponse(201, {}), {}),
    )

    with pytest.raises(HTTPException) as exc_info:
        renovacao_promocoes._renovacao_criar_campanha_manual_ml(
            "tenant-1", "Loja", "Campanha", inicio, fim
        )

    assert exc_info.value.status_code == 500
    assert "nao retornou o ID" in str(exc_info.value.detail)


def test_rota_criar_campanha_manual_encaminha_tenant_e_body():
    chamadas = []

    def criar_campanha_manual(*args):
        chamadas.append(args)
        return {"success": True}

    noop = lambda *_args, **_kwargs: {}
    router = create_renovacao_router(
        RenovacaoRouterConfig(
            get_tenant_id=noop,
            listar_campanhas_usuario=noop,
            criar_ou_completar_proximo_mes=noop,
            criar_campanha_manual=criar_campanha_manual,
            atualizar_periodo_campanha=noop,
            deletar_campanha=noop,
            sincronizar_promocao_existente=noop,
            iniciar_sincronizacao_promocao=noop,
            sync_job_get=noop,
            agendamento_atual=noop,
            agendamento_put=noop,
        )
    )
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/renovacao/campanha" and "POST" in route.methods
    )
    req = RenovacaoCampanhaManualCriarRequest(
        loja="JK Pecas",
        nome="Promocao Agosto",
        start_date="2099-08-01",
        finish_date="2099-08-14",
    )

    assert endpoint(req, client_id="tenant-1") == {"success": True}
    assert chamadas == [
        ("tenant-1", "JK Pecas", "Promocao Agosto", "2099-08-01", "2099-08-14")
    ]
