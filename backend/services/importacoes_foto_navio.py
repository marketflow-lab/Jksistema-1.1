"""Fotos publicas de navios por IMO, independentes da consulta AIS."""

from __future__ import annotations

from collections import OrderedDict
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import re
import threading
import time
from urllib.parse import unquote, urlsplit, urlunsplit

import requests

from backend.services.importacoes_tracking import ErroRastreamentoNavio, identificar_navio


_WIKIDATA = "https://www.wikidata.org/w/api.php"
_COMMONS = "https://commons.wikimedia.org/w/api.php"
_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "JK-Sistema-NavioFoto/1.0 (https://github.com/marketflow-lab/Jksistema-1.1)",
}
_IMAGENS = {"thumb.wikimedia.org", "upload.wikimedia.org"}
_MIME_FOTOS = ("image/jpeg", "image/png", "image/webp")
_CACHE_MAXIMO = 128
_CACHE_FOTO_SEGUNDOS = 24 * 60 * 60
_CACHE_SEM_FOTO_SEGUNDOS = 5 * 60
_cache: OrderedDict[str, tuple[float, dict[str, str] | None]] = OrderedDict()
_lock = threading.Lock()
_pausas: dict[str, float] = {}


class _TextoHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.partes: list[str] = []

    def handle_data(self, data: str) -> None:
        self.partes.append(data)


def _objeto(valor: object) -> dict:
    return valor if isinstance(valor, dict) else {}


def _lista(valor: object) -> list:
    return valor if isinstance(valor, list) else []


def _segundos_retry_after(valor: str) -> float:
    if re.fullmatch(r"[0-9]{1,8}", valor):
        return int(valor)
    try:
        return max(0, parsedate_to_datetime(valor).timestamp() - time.time())
    except (ValueError, OverflowError, OSError):
        return 60


def _consultar(endpoint: str, **params: str) -> dict:
    with _lock:
        if time.monotonic() < _pausas.get(endpoint, 0):
            return {}
    try:
        resposta = requests.get(
            endpoint,
            params={**params, "format": "json", "formatversion": "2"},
            headers=_HEADERS,
            timeout=(2, 3),
            allow_redirects=False,
        )
        if resposta.status_code in {429, 503}:
            retry_after = resposta.headers.get("Retry-After", "")
            espera = _segundos_retry_after(retry_after)
            with _lock:
                _pausas[endpoint] = max(_pausas.get(endpoint, 0), time.monotonic() + espera)
        if resposta.status_code == 200:
            return _objeto(resposta.json())
    except (requests.RequestException, ValueError):
        pass
    return {}


def _valores_de_claims(claims: dict, propriedade: str) -> list[str]:
    valores: list[tuple[bool, str]] = []
    for item in _lista(claims.get(propriedade)):
        claim = _objeto(item)
        if claim.get("rank") not in ("normal", "preferred"):
            continue
        snak = _objeto(claim.get("mainsnak"))
        valor = _objeto(snak.get("datavalue")).get("value")
        if snak.get("snaktype") == "value" and isinstance(valor, str):
            valores.append((claim["rank"] == "preferred", valor))
    valores.sort(key=lambda item: item[0], reverse=True)
    return list(dict.fromkeys(valor for _, valor in valores))


def _arquivos_por_imo(imo: str) -> list[str]:
    busca = _consultar(
        _WIKIDATA, action="query", list="search", srsearch=f"haswbstatement:P458={imo}",
        srnamespace="0", srlimit="2", srprop="",
    )
    query = _objeto(busca.get("query"))
    resultados = _lista(query.get("search"))
    total = _objeto(query.get("searchinfo")).get("totalhits")
    if len(resultados) != 1 or total != 1 or busca.get("continue"):
        return []
    entidade = _objeto(resultados[0]).get("title")
    if not isinstance(entidade, str) or not re.fullmatch(r"Q[1-9][0-9]*", entidade):
        return []
    dados = _consultar(_WIKIDATA, action="wbgetentities", ids=entidade, props="claims")
    navio = _objeto(_objeto(dados.get("entities")).get(entidade))
    claims = _objeto(navio.get("claims"))
    if _valores_de_claims(claims, "P458") != [imo]:
        return []
    return [
        nome for nome in _valores_de_claims(claims, "P18")
        if re.fullmatch(r"[^\x00-\x1f\x7f|<>]{1,250}\.(?:jpe?g|png|webp)", nome, re.IGNORECASE)
    ][:4]


def _url_https(valor: object, hosts: set[str], caminho: str) -> str:
    if not isinstance(valor, str) or len(valor) > 2048 or re.search(r"[\x00-\x20\x7f\\]", valor):
        return ""
    try:
        url = urlsplit(valor)
    except ValueError:
        return ""
    path = unquote(url.path)
    if url.scheme != "https" or url.netloc not in hosts or not path.startswith(caminho):
        return ""
    if re.search(r"[\x00-\x1f\x7f\\]", path) or any(p in {".", ".."} for p in path.split("/")):
        return ""
    return urlunsplit(("https", url.netloc, url.path, "", ""))


def _valor_metadado(metadados: dict, chave: str) -> str:
    valor = _objeto(metadados.get(chave)).get("value")
    return valor.strip() if isinstance(valor, str) else ""


def _autor(metadados: dict) -> str:
    valor = _valor_metadado(metadados, "Attribution") or _valor_metadado(metadados, "Artist")
    if not valor or len(valor) > 2048:
        return ""
    parser = _TextoHtml()
    try:
        parser.feed(valor)
        parser.close()
    except (AssertionError, ValueError):
        return ""
    nome = " ".join("".join(parser.partes).split())
    return nome if len(nome) <= 240 and not re.search(r"[<>\x00-\x1f\x7f]", nome) else ""


def _licenca(metadados: dict) -> tuple[str, str]:
    valor = _valor_metadado(metadados, "LicenseUrl")
    if valor.startswith("http://creativecommons.org/"):
        valor = "https://" + valor.removeprefix("http://")
    url = _url_https(valor, {"creativecommons.org"}, "/")
    caminho = urlsplit(url).path
    if caminho.rstrip("/") == "/publicdomain/zero/1.0":
        return "CC0 1.0", url
    cc = re.fullmatch(r"/licenses/(by|by-sa)/(1\.0|2\.0|2\.5|3\.0|4\.0)/?", caminho)
    if cc:
        return f"CC {cc[1].upper()} {cc[2]}", url
    return "", ""


def _foto_do_arquivo(pagina: dict, imo: str) -> dict[str, str] | None:
    infos = _lista(pagina.get("imageinfo"))
    info = _objeto(infos[0]) if infos else {}
    if info.get("mime") not in _MIME_FOTOS or info.get("thumbmime") not in _MIME_FOTOS:
        return None
    imagem_url = _url_https(info.get("thumburl"), _IMAGENS, "/wikipedia/commons/thumb/")
    arquivo_url = _url_https(info.get("descriptionurl"), {"commons.wikimedia.org"}, "/wiki/File:")
    metadados = _objeto(info.get("extmetadata"))
    if _valor_metadado(metadados, "Restrictions"):
        return None
    autor = _autor(metadados)
    licenca, licenca_url = _licenca(metadados)
    if not all((imagem_url, arquivo_url, autor, licenca, licenca_url)):
        return None
    return {
        "imo": imo, "imagem_url": imagem_url, "arquivo_url": arquivo_url,
        "autor": autor, "licenca": licenca, "licenca_url": licenca_url,
    }


def _buscar(imo: str) -> dict[str, str] | None:
    arquivos = _arquivos_por_imo(imo)
    if not arquivos:
        return None
    dados = _consultar(
        _COMMONS, action="query", prop="imageinfo",
        titles="|".join("File:" + nome for nome in arquivos),
        iiprop="url|mime|thumbmime|extmetadata", iiurlwidth="640",
        iiextmetadatafilter="Artist|Attribution|LicenseUrl|Restrictions",
    )
    paginas = {
        str(item.get("title", "")): item for item in _lista(_objeto(dados.get("query")).get("pages"))
        if isinstance(item, dict)
    }
    for nome in arquivos:
        titulo = "File:" + nome.replace("_", " ")
        foto = _foto_do_arquivo(paginas.get(titulo, {}), imo)
        if foto is not None:
            return foto
    return None


def buscar_foto_navio(imo: str) -> dict[str, str] | None:
    imo = imo.strip()
    if not re.fullmatch(r"[1-9][0-9]{6}", imo):
        return None
    try:
        _, imo = identificar_navio(imo=imo)
    except ErroRastreamentoNavio:
        return None

    with _lock:
        cache = _cache.get(imo)
        if cache and cache[0] > time.monotonic():
            _cache.move_to_end(imo)
            return cache[1].copy() if cache[1] is not None else None

    foto = _buscar(imo)
    with _lock:
        agora = time.monotonic()
        cache = _cache.get(imo)
        if foto is None and cache and cache[0] > agora and cache[1] is not None:
            return cache[1].copy()
        ttl = _CACHE_FOTO_SEGUNDOS if foto is not None else _CACHE_SEM_FOTO_SEGUNDOS
        _cache[imo] = (agora + ttl, foto)
        _cache.move_to_end(imo)
        if len(_cache) > _CACHE_MAXIMO:
            _cache.popitem(last=False)
    return foto.copy() if foto is not None else None
