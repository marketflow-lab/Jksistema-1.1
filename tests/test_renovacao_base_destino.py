"""Financial writes must use the freshly confirmed destination's price base."""

from types import SimpleNamespace

import pytest
import requests

from backend.services import renovacao_promocoes as service


SOURCE, DESTINATION, ITEM = "C-MLB99001", "C-MLB99002", "MLB99003"


@pytest.mark.parametrize("destination_base", [125, None])
def test_first_update_uses_confirmed_destination_base_or_stops(monkeypatch, destination_base):
    writes = []
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_a, **_k: pytest.fail("Real network forbidden"))
    monkeypatch.setattr(service.ctx, "_parse_float_flex", lambda value: float(value) if value is not None else None)
    monkeypatch.setattr(service.ctx, "_ml_extrair_preco_promocao_raw", lambda raw, **_: (raw.get("price"), raw.get("discount_percentage")))

    def lookup(client, store, cfg, item, campaigns):
        assert (client, store, item) == ("tenant-a", "Loja", ITEM)
        def participant(campaign, price, base):
            return {"situacao": "participante", "raw": {"id": campaign, "item_id": ITEM,
                "status": "pending", "type": "SELLER_CAMPAIGN", "price": price,
                "original_price": base, "discount_percentage": 20}}
        return {SOURCE: participant(SOURCE, 80, 100),
                DESTINATION: participant(DESTINATION, 100 if writes else 110, destination_base)}, cfg

    def write(client, store, cfg, method, url, **kwargs):
        writes.append((method, kwargs["json"]["deal_price"]))
        return SimpleNamespace(status_code=200), cfg

    monkeypatch.setattr(service, "_renovacao_consultar_participacoes", lookup)
    monkeypatch.setattr(service.ctx, "_ml_api_request", write)
    ok, error, _ = service._renovacao_adicionar_item_campanha("tenant-a", "Loja", {}, ITEM,
        DESTINATION, 20, 100, 80, campanha_origem_id=SOURCE)
    if destination_base:
        assert ok is True, error
        assert writes == [("PUT", 100)]
    else:
        assert ok is False
        assert "base" in error.lower()
        assert writes == []
