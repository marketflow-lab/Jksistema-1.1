from __future__ import annotations

import base64

from backend.schemas.ia import IAChatRequest
from backend.services.marketplace_tools import images


class _Response:
    def __init__(self, payload, *, ok=True, status_code=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code

    def json(self):
        return self._payload


def _payload() -> IAChatRequest:
    return IAChatRequest(
        message="Gere uma imagem comercial do SKU 123",
        page="produtos",
        context={},
        history=[],
        attachments=[],
    )


def _configure_generation(monkeypatch) -> None:
    monkeypatch.setattr(images._runtime, "chat_requests_image_generation", lambda *_args: True)
    monkeypatch.setattr(images._runtime, "active_provider", lambda *_args: True)
    monkeypatch.setattr(images._runtime, "openai_api_key", lambda: "test-key")
    monkeypatch.setattr(images, "build_prompt", lambda *_args: ("prompt", {"sku": "123", "produto": "Peca"}))


def test_image_generation_reports_disabled_provider(monkeypatch) -> None:
    monkeypatch.setattr(images._runtime, "chat_requests_image_generation", lambda *_args: True)
    monkeypatch.setattr(images._runtime, "active_provider", lambda *_args: False)

    answer = images.generate_response(_payload(), "000002")

    assert answer == "A geracao de imagem esta desativada pelo administrador nas configuracoes de IA."


def test_image_generation_reports_http_error(monkeypatch) -> None:
    _configure_generation(monkeypatch)
    monkeypatch.setattr(images.requests, "post", lambda *_args, **_kwargs: _Response(
        {"error": {"message": "modelo indisponivel"}}, ok=False, status_code=503,
    ))

    answer = images.generate_response(_payload(), "000002")

    assert answer == "Nao consegui gerar a imagem agora. Retorno da OpenAI: modelo indisponivel"


def test_image_generation_rejects_response_without_base64(monkeypatch) -> None:
    _configure_generation(monkeypatch)
    monkeypatch.setattr(images.requests, "post", lambda *_args, **_kwargs: _Response({"data": [{}]}))
    monkeypatch.setattr(images._runtime, "extract_openai_image_b64", lambda *_args: None)

    answer = images.generate_response(_payload(), "000002")

    assert answer == "A OpenAI respondeu, mas nao retornou uma imagem utilizavel."


def test_image_generation_reports_persistence_failure(monkeypatch) -> None:
    _configure_generation(monkeypatch)
    encoded = base64.b64encode(b"png").decode("ascii")
    monkeypatch.setattr(images.requests, "post", lambda *_args, **_kwargs: _Response({"data": [{"b64_json": encoded}]}))
    monkeypatch.setattr(images._runtime, "extract_openai_image_b64", lambda *_args: encoded)
    monkeypatch.setattr(images, "save_generated_image", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk")))

    answer = images.generate_response(_payload(), "000002")

    assert answer == "A imagem foi gerada, mas nao consegui salvar o arquivo no sistema."


def test_image_generation_uses_named_persistence_api(monkeypatch) -> None:
    _configure_generation(monkeypatch)
    encoded = base64.b64encode(b"png").decode("ascii")
    monkeypatch.setattr(images.requests, "post", lambda *_args, **_kwargs: _Response({"data": [{"b64_json": encoded}]}))
    monkeypatch.setattr(images._runtime, "extract_openai_image_b64", lambda *_args: encoded)
    monkeypatch.setattr(images, "save_generated_image", lambda *_args, **_kwargs: ("ignored.png", "/api/ia/imagens/result.png"))

    answer = images.generate_response(_payload(), "000002")

    assert "/api/ia/imagens/result.png" in answer
    assert "SKU 123" in answer
