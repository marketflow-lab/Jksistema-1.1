"""Identify supplier origin from the registered company address only."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path


_PROVINCIAS_CHINESAS = re.compile(
    r"(?<![a-z0-9])(?:anhui|beijing|chongqing|fujian|gansu|guangdong|guangxi|guizhou|"
    r"hainan|hebei|heilongjiang|henan|hubei|hunan|inner mongolia|jiangsu|jiangxi|jilin|"
    r"liaoning|neimenggu|ningxia|qinghai|shaanxi|shandong|shanghai|shanxi|sichuan|"
    r"tianjin|tibet|xinjiang|xizang|yunnan|zhejiang)(?![a-z0-9])"
)


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or "")).casefold()
    texto = "".join(letra for letra in texto if not unicodedata.combining(letra))
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


@lru_cache(maxsize=1)
def _paises_por_nome() -> tuple[tuple[re.Pattern, str], ...]:
    dados = json.loads(Path(__file__).with_name("fornecedor_paises.json").read_text(encoding="utf-8"))
    aliases = {}
    for pais in dados.values():
        for idioma, nome in zip(("en", "pt"), pais["aliases"]):
            for variante in (nome, nome.replace("&", "and" if idioma == "en" else "e")):
                aliases[_normalizar(variante)] = pais["name"]
    for pais, nomes in {
        "CHINA": ("People's Republic of China", "PR China", "P.R. China", "P.R.C.", "Republica Popular da China"),
        "UNITED STATES": ("United States of America", "USA", "U.S.A.", "Estados Unidos da America"),
        "UNITED KINGDOM": ("UK", "U.K.", "Great Britain", "Gra Bretanha"),
        "UNITED ARAB EMIRATES": ("UAE", "U.A.E."),
        "HONG KONG SAR CHINA": ("Hong Kong", "Hongkong"),
        "MACAO SAR CHINA": ("Macao", "Macau"),
    }.items():
        for nome in nomes:
            aliases[_normalizar(nome)] = pais
    return tuple(
        (re.compile(r"(?<![a-z0-9])" + re.escape(nome) + r"(?![a-z0-9])"), pais)
        for nome, pais in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True)
    )


def identificar_pais_fornecedor(cadastro: dict | None) -> str:
    """Return an English country name when the company address identifies it.

    No supplier identity, banking field, manually added origin or historical list
    label is used. Overlapping country names use the longest match; conflicting
    country names remain unresolved. Known provinces cover Chinese addresses
    that omit the country name, including the existing supplier registrations.
    """
    endereco = _normalizar((cadastro or {}).get("endereco_empresa", ""))
    if not endereco:
        return ""
    provincias = [match.span() for match in _PROVINCIAS_CHINESAS.finditer(endereco)]
    encontrados, intervalos = set(), list(provincias)
    for padrao, pais in _paises_por_nome():
        for match in padrao.finditer(endereco):
            if any(match.start() < fim and inicio < match.end() for inicio, fim in intervalos):
                continue
            intervalos.append(match.span())
            encontrados.add(pais)
    if encontrados:
        return next(iter(encontrados)) if len(encontrados) == 1 else ""
    # Geographic evidence from registered company addresses, independent of ID/name.
    if provincias:
        return "CHINA"
    return ""
