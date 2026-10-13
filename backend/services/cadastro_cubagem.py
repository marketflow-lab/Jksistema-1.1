"""Pure, store-scoped cubic volume rules shared by Cadastro and Importacoes.

Operational reads accept only the canonical individual field. Historical data
is considered exclusively by the explicit dry-run migration planner.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

CAMPO_M3_INDIVIDUAL = "m3 individual"
CAMPOS_META_CUBAGEM = ("m3_origem", "m3_unidade", "m3_atualizado_em", "m3_status")
_ALIASES_INDIVIDUAIS = {"m3individual", "cgm3individual", "cbmindividual"}
_NUMERO = re.compile(r"^[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?$")
_UNIDADES = {
    "peca": "peca", "pecas": "peca", "pc": "peca", "pcs": "peca",
    "piece": "peca", "pieces": "peca",
    "par": "par", "pares": "par", "pair": "par", "pairs": "par",
    "kit": "kit", "kits": "kit",
    "unidade": "unidade_comercial", "unidades": "unidade_comercial",
    "unit": "unidade_comercial", "units": "unidade_comercial",
    "unidadecomercial": "unidade_comercial",
}


def normalizar_sku_cubagem(valor: Any) -> str:
    """Normalize presentation without padding digits or collapsing punctuation."""
    if valor is None or isinstance(valor, bool):
        return ""
    return unicodedata.normalize("NFC", str(valor).strip()).upper()


def _sku_produto_cubagem(produto: Mapping[str, Any]) -> str:
    """Use the protected raw identity bridge without changing public SKU keys."""
    return normalizar_sku_cubagem(produto.get("cubagem_sku") or produto.get("sku"))


def _coluna(valor: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or "").strip().lower())
    return "".join(c for c in texto if c.isalnum() and not unicodedata.combining(c))


def decimal_cubagem(valor: Any) -> Decimal | None:
    """Return a strictly positive finite Decimal, with no precision rounding."""
    if valor is None or isinstance(valor, bool):
        return None
    texto = str(valor).strip()
    if not texto or not _NUMERO.fullmatch(texto):
        return None
    try:
        numero = Decimal(texto.replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if not numero.is_finite() or numero <= 0:
        return None
    # Public consumers use JSON/Excel numeric floats. Reject overflow and
    # underflow rather than exposing infinity or silently turning volume zero.
    try:
        numero_float = float(numero)
    except (OverflowError, ValueError):
        return None
    return numero if math.isfinite(numero_float) and numero_float > 0 else None


def _texto_decimal(numero: Decimal) -> str:
    # Very large exponents retain scientific notation to avoid unbounded output.
    if abs(numero.as_tuple().exponent) > 1000:
        return str(numero)
    texto = format(numero, "f")
    return texto.rstrip("0").rstrip(".") if "." in texto else texto


def normalizar_m3_individual(valor: Any, *, permitir_vazio: bool = True) -> str:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        if permitir_vazio:
            return ""
        raise ValueError("Informe o m3 individual.")
    numero = decimal_cubagem(valor)
    if numero is None:
        raise ValueError("O m3 individual deve ser um numero positivo e finito.")
    return _texto_decimal(numero)


def normalizar_unidade_cubagem(valor: Any) -> str:
    texto = str(valor or "").strip()
    if not texto:
        return ""
    unidade = _UNIDADES.get(_coluna(texto))
    if not unidade:
        raise ValueError("Unidade de cubagem invalida: use peca, par, kit ou unidade comercial.")
    return unidade


def validar_dados_cubagem(
    dados: Mapping[str, Any], *, atualizado_em: str | None = None,
) -> dict[str, Any]:
    """Normalize partial writes, refusing contradictory aliases and totals."""
    resultado = dict(dados)
    individuais: list[str] = []
    for chave, valor in dados.items():
        coluna = _coluna(chave)
        if coluna in _ALIASES_INDIVIDUAIS:
            individuais.append(normalizar_m3_individual(valor))
            resultado.pop(chave, None)
        elif coluna in {"m3", "cbm"}:
            if str(valor if valor is not None else "").strip():
                raise ValueError("M3/CBM total nao pode ser cadastrado como m3 individual.")
            resultado.pop(chave, None)
    if len(set(individuais)) > 1:
        raise ValueError("Campos de m3 individual possuem valores divergentes.")
    if "m3_unidade" in resultado:
        resultado["m3_unidade"] = normalizar_unidade_cubagem(resultado["m3_unidade"])
    if "m3_origem" in resultado:
        resultado["m3_origem"] = str(resultado["m3_origem"] or "").strip()
    # Validity and mutation time are server decisions, never user assertions.
    resultado.pop("m3_status", None)
    resultado.pop("m3_atualizado_em", None)
    if individuais:
        resultado[CAMPO_M3_INDIVIDUAL] = individuais[0]
        resultado["m3_status"] = "confirmado" if individuais[0] else "pendente"
        resultado["m3_atualizado_em"] = atualizado_em or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        resultado["m3_origem"] = resultado.get("m3_origem") or "Cadastro manual"
        resultado["m3_unidade"] = resultado.get("m3_unidade") or "unidade_comercial"
    return resultado


def _excluido(produto: Mapping[str, Any]) -> bool:
    return bool(str(produto.get("deleted_at_utc") or "").strip()) or any(
        str(produto.get(campo) or "").strip().lower() in {"true", "1", "sim"}
        for campo in ("deleted", "excluido")
    )


def resolver_cubagem_produtos(
    produtos: Iterable[Mapping[str, Any]], store_id: str,
) -> dict[str, dict[str, Any]]:
    """Resolve only exact cubic-volume identities owned by the requested store.

    cubagem_sku is the protected read-time bridge for a raw SKU changed by the
    legacy public normalizer. It never changes the public product key. Pending
    or conflicting entries have m3_individual=None and cannot enter a total.
    The caller supplies products from one authenticated tenant.
    """
    store = str(store_id or "").strip()
    if not store:
        return {}
    grupos: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for produto in produtos:
        if str(produto.get("store_id") or "").strip() != store or _excluido(produto):
            continue
        sku = _sku_produto_cubagem(produto)
        if sku:
            grupos[sku].append(produto)
    resultado: dict[str, dict[str, Any]] = {}
    for sku, linhas in grupos.items():
        registro = linhas[0]
        valores = {decimal_cubagem(linha.get(CAMPO_M3_INDIVIDUAL)) for linha in linhas}
        valores.discard(None)
        unidades: set[str] = set()
        unidade_invalida = False
        status_bloqueado = False
        for linha in linhas:
            try:
                unidades.add(normalizar_unidade_cubagem(linha.get("m3_unidade")) or "unidade_comercial")
            except ValueError:
                unidade_invalida = True
            status = str(linha.get("m3_status") or "").strip().lower()
            valor_bruto = linha.get(CAMPO_M3_INDIVIDUAL)
            invalido = bool(str(valor_bruto if valor_bruto is not None else "").strip()) and decimal_cubagem(valor_bruto) is None
            status_bloqueado |= bool(status and status != "confirmado") or invalido
        conflito = len(valores) > 1 or len(unidades) > 1
        confirmado = bool(valores) and not (conflito or unidade_invalida or status_bloqueado)
        resultado[sku] = {
            "sku": sku,
            "sku_cadastro": str(registro.get("sku") or "").strip(),
            "store_id": store,
            "m3_individual": next(iter(valores)) if confirmado else None,
            "origem": str(registro.get("m3_origem") or "cadastro_por_loja").strip(),
            "unidade": next(iter(unidades)) if len(unidades) == 1 else "",
            "data": str(registro.get("m3_atualizado_em") or registro.get("updated_at_utc") or "").strip(),
            "status": "conflito" if conflito else ("confirmado" if confirmado else "pendente"),
        }
    return resultado


def _verdadeiro(valor: Any) -> bool:
    return valor is True or str(valor or "").strip().lower() in {"true", "1", "sim"}


def _historico_individual(registro: Mapping[str, Any]) -> tuple[Decimal | None, str]:
    valores: set[Decimal] = set()
    for chave, valor in registro.items():
        if _coluna(chave) not in _ALIASES_INDIVIDUAIS:
            continue
        if not str(valor if valor is not None else "").strip():
            continue
        numero = decimal_cubagem(valor)
        if numero is None:
            return None, "valor_individual_invalido"
        valores.add(numero)
    if len(valores) > 1:
        return None, "aliases_individuais_divergentes"
    return (next(iter(valores)), "") if valores else (None, "sem_campo_individual")


def planejar_migracao_cubagem(
    historicos: Iterable[Mapping[str, Any]],
    produtos_atuais: Iterable[Mapping[str, Any]],
    *,
    lojas_por_nome: Mapping[str, str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Dry-run only: never infer store/unit, overwrite values or access files.

    The caller may mark _unidade_comercial_documentada=True only after proving
    the source document measures volume per commercial SKU quantity.
    """
    produtos = list(produtos_atuais)
    lojas = dict(lojas_por_nome or {})
    nomes: dict[str, set[str]] = defaultdict(set)
    for nome, store_id in lojas.items():
        nomes[str(nome).strip().casefold()].add(str(store_id).strip())
    conhecidas = {str(p.get("store_id") or "").strip() for p in produtos} | {
        str(s).strip() for s in lojas.values()
    }
    conhecidas.discard("")
    atuais = {
        store: resolver_cubagem_produtos(produtos, store)
        for store in conhecidas
    }
    excluidos = {
        (str(p.get("store_id") or "").strip(), _sku_produto_cubagem(p))
        for p in produtos if _excluido(p)
    }
    resultado: dict[str, list[dict[str, Any]]] = {
        "candidatos": [], "conflitos": [], "pendentes": [], "ja_cadastrados": [],
    }
    grupos: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for registro in historicos:
        sku = normalizar_sku_cubagem(registro.get("sku"))
        origem = str(registro.get("m3_origem") or registro.get("_origem") or "").strip()
        base = {"sku": sku, "origem": origem}
        numero, motivo = _historico_individual(registro)
        if not sku or motivo:
            categoria = "conflitos" if motivo == "aliases_individuais_divergentes" else "pendentes"
            resultado[categoria].append({**base, "motivo": motivo or "sku_ausente"})
            continue
        store = str(registro.get("store_id") or "").strip()
        stores: set[str] = set()
        if store:
            if store in conhecidas:
                stores.add(store)
        else:
            labels = [v.strip().casefold() for v in str(registro.get("loja_sync") or "").split("|") if v.strip()]
            if labels and all(len(nomes.get(label, set())) == 1 for label in labels):
                stores = {next(iter(nomes[label])) for label in labels}
        if not stores:
            resultado["pendentes"].append({**base, "motivo": "loja_nao_comprovada"})
            continue
        unidade_invalida = False
        try:
            unidade = normalizar_unidade_cubagem(registro.get("m3_unidade"))
        except ValueError:
            unidade = ""
            unidade_invalida = True
        if not unidade and not unidade_invalida and _verdadeiro(registro.get("_unidade_comercial_documentada")):
            unidade = "unidade_comercial"
        if not unidade or not origem:
            resultado["pendentes"].append({
                **base, "store_ids": sorted(stores),
                "motivo": "unidade_nao_comprovada" if not unidade else "origem_ausente",
            })
            continue
        for store in sorted(stores):
            item = {
                **base, "store_id": store, "m3_individual": _texto_decimal(numero),
                "m3_unidade": unidade,
                "m3_atualizado_em": str(registro.get("m3_atualizado_em") or "").strip(),
            }
            if (store, sku) in excluidos:
                resultado["pendentes"].append({**item, "motivo": "produto_excluido"})
            elif sku not in atuais[store]:
                resultado["pendentes"].append({**item, "motivo": "sku_exato_ausente"})
            else:
                grupos[(store, sku)].append(item)
    for (store, sku), linhas in sorted(grupos.items()):
        pares = {(Decimal(item["m3_individual"]), item["m3_unidade"]) for item in linhas}
        atual = atuais[store][sku]
        item = {
            **linhas[0],
            "sku_cadastro": atual["sku_cadastro"],
            "origens": sorted({linha["origem"] for linha in linhas}),
            "m3_atual": _texto_decimal(atual["m3_individual"]) if atual["m3_individual"] else "",
        }
        if len(pares) > 1 or atual["status"] == "conflito":
            resultado["conflitos"].append({**item, "motivo": "fontes_divergentes", "fontes": linhas})
        elif atual["m3_individual"] is not None:
            numero, unidade = next(iter(pares))
            if numero == atual["m3_individual"] and unidade == atual["unidade"]:
                resultado["ja_cadastrados"].append(item)
            else:
                resultado["conflitos"].append({**item, "motivo": "valor_atual_preservado"})
        else:
            # A populated but invalid/blocked field is not a blank to overwrite.
            preenchido = any(
                str(p.get(CAMPO_M3_INDIVIDUAL) or "").strip()
                for p in produtos
                if str(p.get("store_id") or "").strip() == store
                and _sku_produto_cubagem(p) == sku and not _excluido(p)
            )
            if preenchido:
                resultado["conflitos"].append({**item, "motivo": "cadastro_atual_pendente"})
            else:
                resultado["candidatos"].append({
                    **item, CAMPO_M3_INDIVIDUAL: item["m3_individual"],
                    "m3_origem": "; ".join(item["origens"]), "m3_status": "confirmado",
                })
    return resultado