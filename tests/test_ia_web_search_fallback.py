from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from backend.services import ia_web


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_empty_html_and_lite_results_reach_jina_fallback(monkeypatch):
    markdown = """\
## [Rider's Manual R1300GS](https://duckduckgo.com/l/?uddg=https%3A%2F%2Fmanuals.bmw-motorrad.com%2Fmanual.pdf&rut=abc)

Official BMW rider manual for the R1300GS.

## [Duplicate result](https://duckduckgo.com/l/?uddg=https%3A%2F%2Fmanuals.bmw-motorrad.com%2Fmanual.pdf&rut=def)

Repeated result.
"""
    chamadas = []

    def fake_get(url, **kwargs):
        chamadas.append(str(url))
        if "html.duckduckgo.com" in str(url):
            return _FakeResponse("<html><body>no result cards</body></html>")
        if "lite.duckduckgo.com" in str(url):
            return _FakeResponse("<html><body>no result links</body></html>")
        if "r.jina.ai" in str(url):
            return _FakeResponse(markdown)
        raise AssertionError(f"URL inesperada: {url}")

    monkeypatch.setattr(
        ia_web,
        "_favoritos_busca_externa_chamar_api",
        lambda *_args, **_kwargs: {},
        raising=False,
    )
    monkeypatch.setattr(ia_web.requests, "get", fake_get)

    resultados = ia_web._ia_web_buscar("BMW R1300GS Navigator IV", max_results=4)

    assert len(chamadas) == 3
    assert len(resultados) == 1
    assert resultados[0]["title"] == "Rider's Manual R1300GS"
    assert resultados[0]["url"] == "https://manuals.bmw-motorrad.com/manual.pdf"
    assert resultados[0]["provider"] == "jina_duckduckgo"
    assert "Official BMW rider manual" in resultados[0]["snippet"]


def test_empty_cached_search_is_retried_and_not_persisted(monkeypatch):
    query = "compatibilidade tecnica produto veiculo"
    chave = ia_web._ia_web_cache_key(query, tipo="web")
    cache = {
        "consultas": {
            chave: {
                "query": query,
                "tipo": "web",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "resultados": [],
            }
        }
    }
    respostas = [[], [{"title": "Manual oficial", "url": "https://fabricante.example/manual", "snippet": "interface X"}]]
    chamadas = []

    def fake_buscar(_query, max_results=5):
        chamadas.append(max_results)
        return respostas.pop(0)

    def fake_ler(_client_id):
        return deepcopy(cache)

    def fake_salvar(_client_id, data):
        cache.clear()
        cache.update(deepcopy(data))

    monkeypatch.setattr(ia_web, "_ia_chat_pede_noticias", lambda _query: False)
    # Simula inclusive um cache legado considerado valido por engano: a
    # leitura nunca pode devolver [] como cache hit.
    monkeypatch.setattr(ia_web, "_ia_web_cache_valido", lambda _item: True)
    monkeypatch.setattr(ia_web, "_ia_web_buscar", fake_buscar)
    monkeypatch.setattr(ia_web, "_ia_web_ler_cache", fake_ler)
    monkeypatch.setattr(ia_web, "_ia_web_salvar_cache", fake_salvar)

    primeiro = ia_web._ia_web_buscar_cached(query, client_id="000002", max_results=4)
    assert primeiro == []
    assert chave not in cache["consultas"]

    segundo = ia_web._ia_web_buscar_cached(query, client_id="000002", max_results=4)
    assert segundo and segundo[0]["title"] == "Manual oficial"
    assert chamadas == [4, 4]
    assert cache["consultas"][chave]["resultados"] == segundo


def test_cache_validator_rejects_fresh_empty_result():
    assert ia_web._ia_web_cache_valido({
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "resultados": [],
    }) is False


def test_fast_search_uses_short_timeouts_and_skips_lite(monkeypatch):
    markdown = """## [Manual tecnico](https://fabricante.example/manual)\n\nInterface oficial."""
    chamadas = []
    provider_timeouts = []

    def fake_provider(*_args, **kwargs):
        provider_timeouts.append(kwargs.get("timeout_s"))
        return {}

    def fake_get(url, **kwargs):
        chamadas.append((str(url), kwargs.get("timeout")))
        if "html.duckduckgo.com" in str(url):
            return _FakeResponse("<html><body>sem resultados</body></html>")
        if "r.jina.ai" in str(url):
            return _FakeResponse(markdown)
        raise AssertionError(f"Fallback inesperado no modo rapido: {url}")

    monkeypatch.setattr(ia_web, "_favoritos_busca_externa_chamar_api", fake_provider, raising=False)
    monkeypatch.setattr(ia_web.requests, "get", fake_get)

    resultados = ia_web._ia_web_buscar("manual interface", max_results=4, fast=True)

    assert provider_timeouts == [4]
    assert [timeout for _, timeout in chamadas] == [4, 5]
    assert all("lite.duckduckgo.com" not in url for url, _ in chamadas)
    assert resultados[0]["url"] == "https://fabricante.example/manual"
