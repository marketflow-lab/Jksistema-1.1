from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
import requests

from backend.routers.importacoes import create_importacoes_router
from backend.services import importacoes_foto_navio as fotos


IMO = "9811000"
TITULO = "File:Navio.jpg"
MINIATURA = "https://thumb.wikimedia.org/wikipedia/commons/thumb/b/b1/Navio.jpg/960px-Navio.jpg"
ARQUIVO = "https://commons.wikimedia.org/wiki/File:Navio.jpg"
LICENCA = "https://creativecommons.org/licenses/by-sa/4.0/"


def _claim(valor):
    return {
        "rank": "normal",
        "mainsnak": {"snaktype": "value", "datavalue": {"value": valor}},
    }


@pytest.fixture
def provedor(monkeypatch):
    monkeypatch.setattr(fotos, "_cache", fotos.OrderedDict())
    monkeypatch.setattr(fotos, "_pausas", {})
    busca = {"query": {"searchinfo": {"totalhits": 1}, "search": [{"title": "Q123"}]}}
    claims = {"P458": [_claim(IMO)], "P18": [_claim("Navio.jpg")]}
    info = {
        "mime": "image/jpeg",
        "thumbmime": "image/jpeg",
        "thumburl": MINIATURA + "?utm_source=fixture",
        "descriptionurl": ARQUIVO,
        "extmetadata": {
            "Artist": {"value": '<a href="https://example.org">Fotógrafa &amp; coautora</a>'},
            "LicenseUrl": {"value": LICENCA},
        },
    }
    respostas = {
        ("www.wikidata.org", "query"): busca,
        ("www.wikidata.org", "wbgetentities"): {"entities": {"Q123": {"claims": claims}}},
        ("commons.wikimedia.org", "query"): {
            "query": {"pages": [{"title": TITULO, "imageinfo": [info]}]},
        },
    }
    chamadas = []

    def get(url, *, params, headers, timeout, allow_redirects):
        host = url.removeprefix("https://").removesuffix("/w/api.php")
        chamadas.append((host, params))
        assert headers["Accept"] == "application/json"
        assert "JK-Sistema-NavioFoto/" in headers["User-Agent"]
        assert timeout[0] > 0 and timeout[1] > 0
        assert allow_redirects is False
        payload = respostas[(host, params["action"])]
        if isinstance(payload, Exception):
            raise payload
        return SimpleNamespace(status_code=200, json=lambda: payload)

    monkeypatch.setattr(fotos.requests, "get", get)
    return SimpleNamespace(busca=busca, claims=claims, info=info, respostas=respostas, chamadas=chamadas)


def test_foto_exige_imo_exato_e_retorna_credito_sem_html(provedor):
    esperado = {
        "imo": IMO, "imagem_url": MINIATURA, "arquivo_url": ARQUIVO,
        "autor": "Fotógrafa & coautora", "licenca": "CC BY-SA 4.0", "licenca_url": LICENCA,
    }
    foto = fotos.buscar_foto_navio(IMO)
    assert foto == esperado
    foto["autor"] = "Alteração local"
    assert fotos.buscar_foto_navio(IMO) == esperado
    assert len(provedor.chamadas) == 3
    assert provedor.chamadas[0][1]["srsearch"] == "haswbstatement:P458=" + IMO
    assert provedor.chamadas[-1][1]["titles"] == TITULO


@pytest.mark.parametrize("imo", ["", "9811001", "0000000", "981100²", "9811000/"])
def test_imo_invalido_nao_chega_ao_provedor(provedor, imo):
    assert fotos.buscar_foto_navio(imo) is None
    assert not provedor.chamadas


def test_foto_ignora_varios_navios_e_claim_sem_correspondencia(provedor):
    provedor.busca["query"]["searchinfo"]["totalhits"] = 2
    assert fotos.buscar_foto_navio(IMO) is None
    assert len(provedor.chamadas) == 1

    fotos._cache.clear()
    provedor.busca["query"]["searchinfo"]["totalhits"] = 1
    provedor.claims["P458"] = [_claim("9811001")]
    assert fotos.buscar_foto_navio(IMO) is None
    assert len(provedor.chamadas) == 3


@pytest.mark.parametrize(
    ("chave", "valor"),
    [
        ("mime", "image/svg+xml"),
        ("thumburl", "https://thumb.wikimedia.org/fora/Navio.jpg"),
        ("thumburl", "https://thumb.wikimedia.org/wikipedia/commons/thumb/%2e%2e/Navio.jpg"),
        ("descriptionurl", "https://commons.wikimedia.org.exemplo.org/wiki/File:Navio.jpg"),
        ("extmetadata", {"Artist": {"value": "Fotógrafa"}}),
        ("extmetadata", {"LicenseUrl": {"value": LICENCA}}),
        ("extmetadata", {
            "Artist": {"value": "Fotógrafa"},
            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-nc/4.0/"},
        }),
    ],
)
def test_foto_recusa_arquivo_sem_procedencia_credito_ou_licenca(provedor, chave, valor):
    provedor.info[chave] = valor
    assert fotos.buscar_foto_navio(IMO) is None


def test_provedor_indisponivel_cai_no_fallback_e_evita_repeticao(provedor):
    provedor.respostas[("www.wikidata.org", "query")] = requests.Timeout()
    assert fotos.buscar_foto_navio(IMO) is None
    assert fotos.buscar_foto_navio(IMO) is None
    assert len(provedor.chamadas) == 1


def test_rota_foto_exige_a_mesma_autenticacao_e_devolve_contrato(provedor):
    autorizado = False

    def get_tenant_id():
        if not autorizado:
            raise HTTPException(status_code=401, detail="Sessão obrigatória")
        return "tenant-sintetico"

    app = FastAPI()
    app.include_router(create_importacoes_router(SimpleNamespace(get_tenant_id=get_tenant_id)))
    with TestClient(app) as client:
        path = "/api/importacoes/rastreamento/navio/foto"
        assert client.get(path, params={"imo": IMO}).status_code == 401
        assert not provedor.chamadas

        autorizado = True
        assert client.get(path, params={"imo": "9811001"}).json() == {"foto": None}
        result = client.get(path, params={"imo": IMO})
        assert result.status_code == 200
        assert result.json()["foto"]["imagem_url"] == MINIATURA
