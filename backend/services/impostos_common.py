"""Impostos and Siscomex pure helpers."""

from __future__ import annotations

import re
import threading
import unicodedata
from typing import Any


SISCOMEX_AMBIENTES = {
    "val": "val.portalunico.siscomex.gov.br",
    "prod": "portalunico.siscomex.gov.br",
    "producao": "portalunico.siscomex.gov.br",
    "hom": "hom.pucomex.serpro.gov.br",
}


def _normalizar_siscomex_ambiente(valor: Any) -> str:
    base = str(valor or "val").strip().lower()
    return base if base in SISCOMEX_AMBIENTES else "val"


def _normalizar_role_type_siscomex(valor: Any) -> str:
    txt = str(valor or "IMPEXP").strip().upper()
    return txt or "IMPEXP"


def _normalizar_auth_header_type_siscomex(valor: Any) -> str:
    txt = str(valor or "bearer").strip().lower()
    return txt if txt in {"bearer", "token", "raw"} else "bearer"


def _normalizar_tipo_operacao_siscomex(valor: Any) -> str:
    txt = str(valor or "I").strip().upper()
    return txt if txt in {"I", "E", "F"} else "I"


def _normalizar_loja_siscomex(valor: Any) -> str:
    txt = str(valor or "").strip()
    txt = re.sub(r"\s+", " ", txt)
    return txt[:120]


def _normalizar_perfil_siscomex(valor: Any) -> str:
    txt = str(valor or "").strip().lower()
    txt = re.sub(r"\s+", " ", txt)
    return txt[:120]


def _siscomex_scope_key(loja_vinculada: str = "", perfil_usuario: str = "") -> str:
    loja = _normalizar_loja_siscomex(loja_vinculada)
    perfil = _normalizar_perfil_siscomex(perfil_usuario)
    if not loja and not perfil:
        return "__default__"
    return f"{loja}::{perfil}"


def _siscomex_scope_payload(loja_vinculada: str = "", perfil_usuario: str = "") -> dict:
    return {
        "loja_vinculada": _normalizar_loja_siscomex(loja_vinculada),
        "perfil_usuario": _normalizar_perfil_siscomex(perfil_usuario),
    }


def _formatar_ncm_pontos(ncm_digitos: str) -> str:
    """Converte 8 digitos para o formato X4.XX.XX usado pela TEC/TIPI."""
    d = str(ncm_digitos or "").replace(".", "").replace("-", "").strip()
    if len(d) == 8:
        return f"{d[:4]}.{d[4:6]}.{d[6:8]}"
    return d


def _aliquotas_pis_cofins_por_regime(regime: str) -> tuple[float, float]:
    _ = str(regime or "simples").strip().lower()
    return 2.1, 9.65


def _ajustar_pis_cofins_monofasico_revenda(monofasico_info: dict, aliquota_pis: float, aliquota_cofins: float) -> tuple[float, float, bool]:
    status = str((monofasico_info or {}).get("status") or "").strip().lower()
    if status == "sim":
        return 0.0, 0.0, True
    return aliquota_pis, aliquota_cofins, False


def _normalizar_coluna_ncm_excel(nome_coluna: Any) -> str:
    base = unicodedata.normalize("NFKD", str(nome_coluna or ""))
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", base.lower())


def _to_float_percentual(valor, padrao: float = 0.0) -> float:
    txt = str(valor if valor is not None else "").strip()
    if not txt:
        return float(padrao)
    txt = txt.replace("R$", "").replace(" ", "")
    if "," in txt and "." in txt:
        txt = txt.replace(".", "").replace(",", ".")
    elif "," in txt:
        txt = txt.replace(",", ".")
    try:
        return float(txt)
    except Exception:
        return float(padrao)


def _normalizar_aliquota_percentual(valor: Any) -> float:
    txt = str(valor if valor is not None else "").strip()
    tem_percentual = "%" in txt
    if tem_percentual:
        txt = txt.replace("%", "")

    n = _to_float_percentual(txt, 0.0)

    if 0 < n <= 1:
        if not tem_percentual:
            n *= 100.0
    return round(max(0.0, n), 6)


SISCOMEX_CONFIG_DEFAULT = {
    "ambiente": "prod",
    "client_id": "",
    "client_secret": "",
    "role_type": "IMPEXP",
    "authorization_header_type": "raw",
    "codigo_pais_padrao": 741,
    "tipo_operacao_padrao": "I",
    "regime_tributario": "simples",
}


SISCOMEX_CODIGO_PAIS_CHINA = 741


SISCOMEX_TIPO_OPERACAO_IMPORTACAO = "I"


SISCOMEX_AUTH_CACHE: dict[str, dict] = {}


SISCOMEX_AUTH_CACHE_LOCK = threading.Lock()


PIS_COFINS_IMPORTACAO_RULE_VERSION = "2026-04-28-cofins-2026-v4"


ALIQUOTAS_IMPORTACAO_OVERRIDES: dict[str, dict] = {
    "84138200": {
        "ii": 20.0,
        "ipi": 0.0,
        "pis": 2.10,
        "cofins": 10.25,
        "descricao": "Elevadores de lÃƒÂ­quidos",
        "fonte_ii": "Simulador oficial Siscomex/RFB Ã¢â‚¬â€ validado em 28/04/2026",
        "fonte_ipi": "TIPI oficial Receita Federal Ã¢â‚¬â€ atualizada ADE RFB nÃ‚Âº 1/2026",
        "fonte_pis": "Simulador oficial Siscomex/RFB Ã¢â‚¬â€ regra geral PIS-ImportaÃƒÂ§ÃƒÂ£o",
        "fonte_cofins": "Simulador oficial Siscomex/RFB Ã¢â‚¬â€ COFINS-ImportaÃƒÂ§ÃƒÂ£o geral com adicional vigente em 2026",
        "observacao": "CorreÃƒÂ§ÃƒÂ£o exata validada pelo simulador oficial em 28/04/2026.",
    },
}


_PIS_COFINS_AUTOPECAS_NCM_EXATOS: frozenset[str] = frozenset({
    # Base explÃ­cita conservadora. SÃƒÂ³ entra aqui quando o NCM ÃƒÂ© claramente
    # autopeÃƒÂ§a/acessÃƒÂ³rio automotivo para a regra majorada de importaÃƒÂ§ÃƒÂ£o.
    "40111000", "40112090", "40118090", "40119090", "40121100", "40121200", "40121300", "40121900",
    "40131010", "40131090", "40139000", "40169990",
    "70071100", "70071900", "70072100", "70072900", "70091000", "70099100", "70099200",
    "73201000", "73202010", "73202090", "73209000",
    "83012000", "83021000", "83023000",
    "84073110", "84073190", "84073200", "84073310", "84073390", "84073410", "84073490",
    "84082010", "84082020", "84082030", "84082090",
    "84099111", "84099112", "84099113", "84099114", "84099115", "84099116", "84099117", "84099118", "84099120", "84099130", "84099140", "84099190",
    "84099912", "84099914", "84099915", "84099917", "84099920", "84099930", "84099941", "84099949", "84099951", "84099959", "84099961", "84099969", "84099991", "84099999",
    "84133010", "84133020", "84133030", "84133090", "84139190",
    "84143011", "84143019", "84143091", "84143099", "84145910", "84145990", "84148021", "84148022", "84148029", "84149039",
    "84152010", "84152090", "84159010", "84159020", "84159090",
    "84212300", "84213100", "84213920", "84213990", "84219910", "84219999",
    "84254200", "84311010", "84311090", "84312011", "84312019", "84312090",
    "84811000", "84812010", "84812090", "84813000", "84814000", "84818092", "84818093", "84818095", "84819090",
    "84821010", "84821090", "84822010", "84822090", "84823000", "84824000", "84825010", "84825090", "84828000", "84829111", "84829119", "84829120", "84829130", "84829190", "84829900",
    "84831010", "84831020", "84831090", "84832000", "84833010", "84833090", "84834010", "84834090", "84835010", "84835090", "84836011", "84836019", "84836090", "84839000",
    "84841000", "84842000", "84849000",
    "85013110", "85013120", "85013190", "85013210", "85013220", "85013290",
    "85071010", "85071090", "85072010", "85072090", "85073011", "85073019", "85073090",
    "85111000", "85112010", "85112090", "85113010", "85113020", "85113090", "85114000", "85115010", "85115090", "85118010", "85118020", "85118030", "85118090", "85119000",
    "85122011", "85122019", "85122021", "85122022", "85122023", "85122029", "85123000", "85124010", "85124020", "85129000",
    "85198190", "85272100", "85272900",
    "85361000", "85362000", "85364100", "85364900", "85365090", "85366100", "85366990", "85369090",
    "85443000", "85444200", "85444900",
    "87060010", "87060020", "87060090", "87071000", "87079090",
    "87081000", "87082100", "87082200", "87082911", "87082912", "87082913", "87082914", "87082919", "87082991", "87082992", "87082993", "87082994", "87082995", "87082999",
    "87083011", "87083019", "87083090", "87084011", "87084019", "87084080", "87084090", "87085011", "87085012", "87085019", "87085080", "87085099",
    "87087010", "87087090", "87088000", "87089100", "87089200", "87089300", "87089411", "87089412", "87089413", "87089481", "87089482", "87089483", "87089490", "87089510", "87089521", "87089522", "87089529", "87089910", "87089990",
    "87141000", "87149100", "87149200", "87149310", "87149320", "87149410", "87149490", "87149500", "87149600", "87149910", "87149990",
    "90262010", "90262090", "90291010", "90292010", "90292020", "90318099", "90328911", "90328919", "90328921", "90328929",
    "94012000",
})


_PIS_COFINS_AUTOPECAS_PREFIXOS_SEGUROS: frozenset[str] = frozenset({
    "8708",
})


_MONOFASICO_PREFIXOS_LEGIS: dict[str, tuple[str, str]] = {
    # FarmacÃƒÂªuticos Ã¢â‚¬â€ Lei 10.147/2000
    "3003": ("Lei 10.147/2000", "FarmacÃƒÂªuticos"),
    "3004": ("Lei 10.147/2000", "FarmacÃƒÂªuticos"),
    # CosmÃƒÂ©ticos, perfumaria, higiene pessoal Ã¢â‚¬â€ Lei 10.147/2000
    "3301": ("Lei 10.147/2000", "CosmÃƒÂ©ticos/Perfumaria"),
    "3302": ("Lei 10.147/2000", "CosmÃƒÂ©ticos/Perfumaria"),
    "3303": ("Lei 10.147/2000", "CosmÃƒÂ©ticos/Perfumaria"),
    "3304": ("Lei 10.147/2000", "CosmÃƒÂ©ticos/Perfumaria"),
    "3305": ("Lei 10.147/2000", "CosmÃƒÂ©ticos/Perfumaria"),
    "3306": ("Lei 10.147/2000", "CosmÃƒÂ©ticos/Perfumaria"),
    "3307": ("Lei 10.147/2000", "CosmÃƒÂ©ticos/Perfumaria"),
    "3401": ("Lei 10.147/2000", "CosmÃƒÂ©ticos/Perfumaria"),
    "9603": ("Lei 10.147/2000", "CosmÃƒÂ©ticos/Perfumaria"),
    # CombustÃƒÂ­veis e derivados de petrÃƒÂ³leo Ã¢â‚¬â€ Lei 9.718/1998 + 10.336/2001
    "2207": ("Lei 9.718/1998", "CombustÃƒÂ­veis"),
    "2710": ("Lei 10.336/2001", "CombustÃƒÂ­veis"),
    "2711": ("Lei 10.336/2001", "CombustÃƒÂ­veis"),
    "3826": ("Lei 12.649/2012", "Biodiesel"),
    # ÃƒÂguas, refrigerantes, cerveja Ã¢â‚¬â€ Lei 10.833/2003 art. 58-A
    "2201": ("Lei 10.833/2003", "Bebidas"),
    "2202": ("Lei 10.833/2003", "Bebidas"),
    "2203": ("Lei 10.833/2003", "Bebidas"),
    # VeÃƒÂ­culos Ã¢â‚¬â€ Lei 10.485/2002
    "8701": ("Lei 10.485/2002", "VeÃƒÂ­culos"),
    "8702": ("Lei 10.485/2002", "VeÃƒÂ­culos"),
    "8703": ("Lei 10.485/2002", "VeÃƒÂ­culos"),
    "8704": ("Lei 10.485/2002", "VeÃƒÂ­culos"),
    "8705": ("Lei 10.485/2002", "VeÃƒÂ­culos"),
    "8706": ("Lei 10.485/2002", "VeÃƒÂ­culos"),
    "8711": ("Lei 10.485/2002", "Motocicletas"),
    # AutopeÃƒÂ§as Ã¢â‚¬â€ Lei 10.485/2002 Anexo II
    "4011": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "4012": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "4013": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "4016": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "7007": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "7009": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "7320": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8301": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8407": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8408": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8409": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8413": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8414": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8421": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8425": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8431": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8481": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8482": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8483": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8484": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8511": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8512": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8519": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8527": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8536": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8544": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8708": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "8714": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "9026": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "9029": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
    "9032": ("Lei 10.485/2002", "AutopeÃƒÂ§as"),
}


_MONOFASICO_EXCLUIR_EXATOS: frozenset[str] = frozenset()


__all__ = [
    name
    for name in globals()
    if (
        (name.startswith("_") and not name.startswith("__"))
        or name.startswith("SISCOMEX")
        or name.startswith("PIS_COFINS")
        or name.startswith("ALIQUOTAS")
    )
]

