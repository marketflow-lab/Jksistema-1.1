import logging
from contextlib import nullcontext

from backend.services import favoritos_ml, mercadolivre_legacy_api, integracoes


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


def test_favoritos_refresh_ml_carrega_store_id_e_nao_persiste_contexto(
    monkeypatch, tmp_path,
):
    loja = {
        "store_id": "StoreA",
        "nome": "Loja Igual",
        "integracoes": {
            "mercadolivre": {
                "id": "app-a",
                "secret": "secret-a",
                "access_token": "access-old",
                "refresh_token": "refresh-old",
            }
        },
    }
    gravacoes = []
    monkeypatch.setattr(integracoes, "_get_tenant_path", lambda _client: str(tmp_path))
    monkeypatch.setattr(integracoes, "buscar_loja_snapshot", lambda *_args: loja)
    monkeypatch.setattr(integracoes, "carregar_lojas", lambda _client: [loja])
    monkeypatch.setattr(integracoes, "_integracoes_bloquear_rmw_lojas", lambda _client: nullcontext())
    monkeypatch.setattr(integracoes, "salvar_lojas", lambda client, rows: gravacoes.append(rows))

    def request(_client_id, _loja, token, _method, url, **_kwargs):
        if url.endswith("/oauth/token"):
            return _Response(
                200,
                {
                    "access_token": "access-new",
                    "refresh_token": "refresh-new",
                },
            )
        if token == "access-old":
            return _Response(401, {})
        return _Response(200, {"id": "MLB123"})

    monkeypatch.setattr(favoritos_ml, "carregar_lojas", lambda _client_id: [loja], raising=False)
    monkeypatch.setattr(
        favoritos_ml,
        "_extrair_item_id",
        lambda item_id: str(item_id or "").strip().upper(),
        raising=False,
    )
    monkeypatch.setattr(
        favoritos_ml,
        "_ml_cfg_com_store_id_context",
        mercadolivre_legacy_api._ml_cfg_com_store_id_context,
        raising=False,
    )
    monkeypatch.setattr(
        favoritos_ml,
        "_ml_api_request",
        mercadolivre_legacy_api._ml_api_request,
        raising=False,
    )
    monkeypatch.setattr(
        favoritos_ml,
        "logger",
        logging.getLogger("test.favoritos_ml"),
        raising=False,
    )
    monkeypatch.setattr(
        mercadolivre_legacy_api,
        "logger",
        logging.getLogger("test.mercadolivre_legacy_api"),
        raising=False,
    )
    monkeypatch.setattr(
        mercadolivre_legacy_api,
        "_ml_http_request",
        request,
        raising=False,
    )
    monkeypatch.setattr(
        mercadolivre_legacy_api,
        "_ml_http_invalidar_session",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        mercadolivre_legacy_api,
        "_env_bool",
        lambda *_args, **_kwargs: True,
        raising=False,
    )
    monkeypatch.setattr(
        mercadolivre_legacy_api,
        "atualizar_api_loja",
        lambda *args, **kwargs: gravacoes.append((args, kwargs)),
        raising=False,
    )

    resultado = favoritos_ml._ml_api_item_com_oauth_tenant(
        "cliente-a",
        "MLB123",
    )

    assert resultado["id"] == "MLB123"
    assert len(gravacoes) == 1
    assert gravacoes[0][0]["store_id"] == "StoreA"
    persisted = gravacoes[0][0]["integracoes"]["mercadolivre"]
    assert "_store_id_context" not in persisted
    assert persisted["access_token"] == "access-new"
