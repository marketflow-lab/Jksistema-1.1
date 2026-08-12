import backend_api  # noqa: F401 - configura o runtime modular
import pytest

from backend.services import mercadolivre_legacy_promocoes as promocoes


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


def test_consulta_normal_reutiliza_cache(monkeypatch):
    antigo = [{"promotion_id": "P-ANTIGA", "offer_id": "OFFER-ANTIGA"}]
    chamadas = []

    monkeypatch.setattr(promocoes, "_cache_get", lambda *_args: antigo)
    monkeypatch.setattr(
        promocoes,
        "_cache_set",
        lambda *_args: chamadas.append("cache_set"),
    )

    def _request(*_args, **_kwargs):
        chamadas.append("request")
        return _Response(200, []), {}

    resultado, _cfg = promocoes._ml_obter_promocoes_item(
        "cliente",
        "loja",
        {},
        "MLB123",
        request_fn=_request,
    )

    assert resultado == antigo
    assert chamadas == []


def test_force_refresh_ignora_leitura_e_atualiza_mesma_chave(monkeypatch):
    novo = [{"promotion_id": "P-ATUAL", "offer_id": "OFFER-ATUAL"}]
    leituras = []
    gravacoes = []
    requisicoes = []

    monkeypatch.setattr(
        promocoes,
        "_cache_get",
        lambda *_args: leituras.append("cache_get") or [{"promotion_id": "P-ANTIGA"}],
    )
    monkeypatch.setattr(
        promocoes,
        "_cache_set",
        lambda cache, key, value: gravacoes.append((cache, key, value)),
    )

    def _request(*_args, **_kwargs):
        requisicoes.append("request")
        return _Response(200, {"results": novo}), {"token": "renovado"}

    cache = {}
    monkeypatch.setattr(promocoes, "ML_ITEM_PROMOTIONS_CACHE", cache)

    resultado, cfg = promocoes._ml_obter_promocoes_item(
        "cliente",
        "loja",
        {"token": "anterior"},
        "MLB123",
        request_fn=_request,
        force_refresh=True,
    )

    assert resultado == novo
    assert cfg == {"token": "renovado"}
    assert leituras == []
    assert requisicoes == ["request"]
    assert gravacoes == [(cache, "v2:cliente:loja:MLB123", novo)]


def test_force_refresh_preserva_chave_e_validacao_do_modo_strict(monkeypatch):
    novo = [{"promotion_id": "P-ATUAL"}]
    gravacoes = []

    monkeypatch.setattr(
        promocoes,
        "_cache_get",
        lambda *_args: (_ for _ in ()).throw(AssertionError("nao deve ler cache")),
    )
    monkeypatch.setattr(
        promocoes,
        "_cache_set",
        lambda _cache, key, value: gravacoes.append((key, value)),
    )

    resultado, _cfg = promocoes._ml_obter_promocoes_item(
        "cliente",
        "loja",
        {},
        "MLB123",
        request_fn=lambda *_args, **_kwargs: (_Response(200, novo), {}),
        strict=True,
        force_refresh=True,
    )

    assert resultado == novo
    assert gravacoes == [("v3-strict:cliente:loja:MLB123", novo)]


def test_refresh_v2_e_consumido_pelo_resolver_sem_reabrir_cache_antigo(monkeypatch):
    antigo = [{
        "promotion_id": "P-CAMPANHA",
        "status": "started",
        "offer_id": "OFFER-ANTIGA",
        "price": 130.00,
    }]
    atual = [{
        "promotion_id": "P-CAMPANHA",
        "status": "candidate",
        "offer_id": "CANDIDATE-ATUAL",
        "price": 148.46,
    }]
    cache = {"v2:cliente:loja:MLB123": antigo}
    requisicoes = []

    monkeypatch.setattr(promocoes, "ML_ITEM_PROMOTIONS_CACHE", cache)
    monkeypatch.setattr(
        promocoes,
        "_cache_get",
        lambda _cache, key, _ttl: cache.get(key),
    )
    monkeypatch.setattr(
        promocoes,
        "_cache_set",
        lambda _cache, key, value: cache.__setitem__(key, value),
    )

    def _request(*_args, **_kwargs):
        requisicoes.append("request")
        return _Response(200, {"results": atual}), {}

    promocoes._ml_obter_promocoes_item(
        "cliente",
        "loja",
        {},
        "MLB123",
        request_fn=_request,
        force_refresh=True,
    )
    resolvido, _cfg = promocoes._ml_resolver_raw_promocao_equivalente_para_analise(
        "cliente",
        "loja",
        {},
        "MLB123",
        antigo[0],
        "P-CAMPANHA",
        "SMART",
        request_fn=_request,
    )

    assert resolvido["offer_id"] == "CANDIDATE-ATUAL"
    assert resolvido["price"] == 148.46
    assert requisicoes == ["request"]


def test_force_refresh_429_invalida_v2_antigo(monkeypatch):
    cache = {"v2:cliente:loja:MLB123": [{"offer_id": "OFFER-ANTIGA"}]}
    monkeypatch.setattr(promocoes, "ML_ITEM_PROMOTIONS_CACHE", cache)
    monkeypatch.setattr(
        promocoes,
        "_cache_get",
        lambda _cache, key, _ttl: cache.get(key),
    )
    monkeypatch.setattr(
        promocoes,
        "_cache_set",
        lambda _cache, key, value: cache.__setitem__(key, value),
    )

    resultado, _cfg = promocoes._ml_obter_promocoes_item(
        "cliente",
        "loja",
        {},
        "MLB123",
        request_fn=lambda *_args, **_kwargs: (_Response(429, {}), {}),
        force_refresh=True,
    )

    assert resultado == []
    assert cache["v2:cliente:loja:MLB123"] == []


def test_force_refresh_excecao_invalida_v2_antigo(monkeypatch):
    cache = {"v2:cliente:loja:MLB123": [{"offer_id": "OFFER-ANTIGA"}]}
    monkeypatch.setattr(promocoes, "ML_ITEM_PROMOTIONS_CACHE", cache)
    monkeypatch.setattr(
        promocoes,
        "_cache_set",
        lambda _cache, key, value: cache.__setitem__(key, value),
    )

    def _falhar(*_args, **_kwargs):
        raise TimeoutError("API indisponivel")

    with pytest.raises(TimeoutError, match="API indisponivel"):
        promocoes._ml_obter_promocoes_item(
            "cliente",
            "loja",
            {},
            "MLB123",
            request_fn=_falhar,
            force_refresh=True,
        )

    assert cache["v2:cliente:loja:MLB123"] == []
