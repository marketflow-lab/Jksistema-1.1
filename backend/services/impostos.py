"""Impostos and Siscomex pure helpers."""

from __future__ import annotations

import re
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
