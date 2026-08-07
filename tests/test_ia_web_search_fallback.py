from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from backend.services import ia_web
from backend.modules.perguntas_pos_venda.ai import sources as perguntas_agent_sources


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


def test_broad_search_merges_configured_public_providers_with_domain_diversity(monkeypatch):
    chamadas = []

    def fake_provider(query, max_results=4, timeout_s=18, *, provider=""):
        chamadas.append((query, max_results, timeout_s, provider))
        dados = {
            "tavily": [
                {"title": "Manual oficial", "url": "https://support.fabricante.example/manual.pdf", "snippet": "Interface X"},
                {"title": "Endereco privado", "url": "http://127.0.0.1/segredo", "snippet": "ignorar"},
            ],
            "brave": [
                {"title": "Manual duplicado", "url": "https://support.fabricante.example/manual.pdf?utm_source=x", "snippet": "Interface X"},
                {"title": "Discussao tecnica", "url": "https://forum.example/topico", "snippet": "Relato da comunidade"},
            ],
            "duckduckgo_html": [
                {"title": "Catalogo", "url": "https://distribuidor.example/catalogo", "snippet": "Catalogo tecnico da peca"},
                {"title": "Download", "url": "https://download.example/programa.exe", "snippet": "nao abrir"},
                {"title": "Dark web", "url": "http://produtoabc.onion/manual", "snippet": "nao acessar"},
            ],
        }
        return {"provider": provider, "resultados": dados[provider]}

    monkeypatch.setattr(
        ia_web,
        "_favoritos_busca_externa_provedores_configurados",
        lambda **_kwargs: ["tavily", "brave", "duckduckgo_html"],
        raising=False,
    )
    monkeypatch.setattr(ia_web, "_favoritos_busca_externa_chamar_api", fake_provider, raising=False)
    monkeypatch.setattr(
        ia_web,
        "_ia_web_buscar",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback indevido")),
    )

    resultados = ia_web._ia_web_buscar_amplo("codigo OEM interface", max_results=8, fast=True)

    assert {call[3] for call in chamadas} == {"tavily", "brave", "duckduckgo_html"}
    assert all(call[2] == 4 for call in chamadas)
    assert [item["url"] for item in resultados] == [
        "https://support.fabricante.example/manual.pdf",
        "https://distribuidor.example/catalogo",
        "https://forum.example/topico",
    ]
    assert resultados[0]["authority"] == "public_web_reference"
    assert resultados[1]["authority"] == "technical_catalog"
    assert resultados[2]["authority"] == "community_reference"


def test_public_web_url_policy_blocks_private_darkweb_credentials_and_downloads():
    assert ia_web._ia_web_url_publica_segura("https://docs.fabricante.example/manual.pdf") is True
    assert ia_web._ia_web_url_publica_segura("http://10.0.0.1/manual") is False
    assert ia_web._ia_web_url_publica_segura("http://localhost/admin") is False
    assert ia_web._ia_web_url_publica_segura("http://catalogo.onion/peca") is False
    assert ia_web._ia_web_url_publica_segura("https://usuario:senha@example.com/privado") is False
    assert ia_web._ia_web_url_publica_segura("https://example.com/arquivo.zip") is False
    assert ia_web._ia_web_url_publica_segura("file:///etc/passwd") is False
    assert ia_web._ia_web_autoridade_fonte_publica(
        "https://support.evil.example/official.pdf",
        "Manual oficial OEM",
        "O fabricante confirma",
    ) == "public_web_reference"


def test_broad_cache_is_isolated_by_tenant(monkeypatch):
    caches = {}
    chamadas = []

    def fake_ler(client_id):
        return deepcopy(caches.get(client_id, {"consultas": {}}))

    def fake_salvar(client_id, data):
        caches[client_id] = deepcopy(data)

    def fake_buscar(query, max_results=8, *, fast=True):
        chamadas.append((query, max_results, fast))
        return [{"title": "Manual", "url": "https://docs.example/manual", "snippet": "Dado publico"}]

    monkeypatch.setattr(ia_web, "_ia_web_ler_cache", fake_ler)
    monkeypatch.setattr(ia_web, "_ia_web_salvar_cache", fake_salvar)
    monkeypatch.setattr(ia_web, "_ia_web_buscar_amplo", fake_buscar)

    primeiro_a = ia_web._ia_web_buscar_amplo_cached("peca 123", client_id="cliente-a", max_results=8)
    segundo_a = ia_web._ia_web_buscar_amplo_cached("peca 123", client_id="cliente-a", max_results=8)
    primeiro_b = ia_web._ia_web_buscar_amplo_cached("peca 123", client_id="cliente-b", max_results=8)

    assert primeiro_a == segundo_a == primeiro_b
    assert chamadas == [("peca 123", 8, True), ("peca 123", 8, True)]
    assert caches["cliente-a"] is not caches["cliente-b"]


def test_broad_search_provider_failures_return_no_evidence(monkeypatch):
    monkeypatch.setattr(
        ia_web,
        "_favoritos_busca_externa_provedores_configurados",
        lambda **_kwargs: ["tavily", "brave"],
        raising=False,
    )
    monkeypatch.setattr(
        ia_web,
        "_favoritos_busca_externa_chamar_api",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("timeout")),
        raising=False,
    )
    monkeypatch.setattr(ia_web, "_ia_web_buscar", lambda *_args, **_kwargs: [])

    assert ia_web._ia_web_buscar_amplo("produto sem fonte", max_results=8, fast=True) == []


def test_broad_search_filters_unsafe_results_from_last_fallback(monkeypatch):
    monkeypatch.setattr(
        ia_web,
        "_favoritos_busca_externa_provedores_configurados",
        lambda **_kwargs: ["tavily"],
        raising=False,
    )
    monkeypatch.setattr(
        ia_web,
        "_favoritos_busca_externa_chamar_api",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("timeout")),
        raising=False,
    )
    monkeypatch.setattr(
        ia_web,
        "_ia_web_buscar",
        lambda *_args, **_kwargs: [
            {"title": "Dark", "url": "http://produto.onion/manual", "snippet": "ignorar", "provider": "jina"},
            {"title": "Privado", "url": "http://127.0.0.1/admin", "snippet": "ignorar", "provider": "html"},
            {"title": "Executavel", "url": "https://example.com/setup.exe", "snippet": "ignorar", "provider": "html"},
            {"title": "Publico", "url": "https://docs.example/manual", "snippet": "dado", "provider": "jina"},
        ],
    )

    resultados = ia_web._ia_web_buscar_amplo("produto", max_results=8, fast=True)

    assert [item["url"] for item in resultados] == ["https://docs.example/manual"]
    assert resultados[0]["provider"] == "jina"


def test_question_agent_runtime_uses_broad_tenant_cached_search(monkeypatch):
    import backend_api  # noqa: F401

    chamadas = []

    def fake_broad(query, client_id=None, max_results=8, *, fast=True):
        chamadas.append((query, client_id, max_results, fast))
        return [{"title": "Fonte", "url": "https://docs.example/manual", "snippet": "Dado"}]

    monkeypatch.setattr(perguntas_agent_sources, "_ia_web_buscar_amplo_cached", fake_broad)

    resultados = perguntas_agent_sources._ia_agent_perguntas_buscar_web_publica(
        "produto 123",
        client_id="cliente-a",
        max_results=8,
        fast=True,
    )

    assert resultados[0]["url"] == "https://docs.example/manual"
    assert chamadas == [("produto 123", "cliente-a", 8, True)]
