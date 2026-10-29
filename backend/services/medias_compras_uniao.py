"""Preview and atomic consolidation of saved purchase lists."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from fastapi import Depends, HTTPException

from backend.schemas.medias_compras import ListasPedidosUniaoPreviaRequest, ListasPedidosUniaoRequest
from backend.services import medias_compras_common as common
from backend.services.cadastro_cubagem import normalizar_sku_cubagem


STATUS_PERMITIDOS = {"Lista gerada", "Em Orçamento", "Analisando orçamento"}
CAMPOS = {
    "Valor unidade": "Preço unitário",
    common.TITULO_PRODUTO_INGLES_KEY: "Descrição",
    "OEM": "OEM / modelo",
    common.COR_LADO_LISTA_PEDIDO_KEY: "Cor / lado",
    "Link": "Link do produto",
    common.EMBALAGEM_LISTA_PEDIDO_KEY: "Embalagem",
    "peso_uniao_unitario": "Peso por unidade",
    "MOQ": "Quantidade mínima",
    "package_multiple": "Múltiplo da embalagem",
}
CAMPOS_NUMERICOS = {"Valor unidade", "peso_uniao_unitario", "MOQ", "package_multiple"}
RECEBIMENTOS = (("received_quantity", "Quantidade recebida", "Qtd recebida"),
               ("defective_quantity", "Quantidade defeituosa", "Qtd defeituosa"))
ALIASES = {
    "SKU": ("sku",), "Quantidade": ("quantity", "Qtd", "Qtde", "Qty"),
    "Valor unidade": ("Valor unitario", "Valor unitário", "Cost", "Custo", "Preco", "Preço"),
    "Valor total": ("Sub-total(USD)", "Subtotal USD", "Sub total", "Subtotal"),
    common.TITULO_PRODUTO_INGLES_KEY: (*common.TITULO_PRODUTO_INGLES_KEYS_LEGADO, "Description", "Descricao", "Descrição", "Titulo", "Título", "Produto", "Nome"),
    "OEM": ("OEM/ Model", "OEM Model", "Modelo", "Numero do modelo", "Número do modelo"),
    common.COR_LADO_LISTA_PEDIDO_KEY: ("Color side", "Color/Side", "Cor/Lado", "Cor lado", "Lado/Cor", "Lado", "Cor", "Color", "Side"),
    "Link": ("Link Aliexpress", "url aliexpress", "URL", "Url", "Link produto"),
    common.PESO_LISTA_PEDIDO_KEY: ("Estimated Weight", "Estimated Weigh", "Weight", "Peso"),
    common.EMBALAGEM_LISTA_PEDIDO_KEY: ("Packaging", "Embalagem individual", "Embalagem"),
    "MOQ": ("moq", "Quantidade minima", "Quantidade mínima"),
    "package_multiple": ("Multiplo embalagem", "Múltiplo embalagem", "Packing multiple"),
    "Foto": ("Picture", "foto", "picture", "imagem", "image"),
    "M3 individual": ("M3 Individual", "M³ individual", "M³ Individual", "m3_individual", "CBM individual"),
    common.CBM_LISTA_PEDIDO_KEY: ("Estimated CBM", "CBM", "M3", "M³", "m3"),
}


def _chave(value):
    plain = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", plain.lower())


def _limpar_aliases(item):
    canonical = {_chave(alias): field for field, aliases in ALIASES.items() for alias in (field, *aliases)}
    for key in list(item):
        normalized = _chave(str(key))
        if (normalized in canonical and key != canonical[normalized]) or "ncm" in normalized or normalized in {"cest", "ii", "ipi", "pis", "cofins", "imposto"}:
            item.pop(key, None)


def _verdadeiro(value):
    return value is True or str(value or "").strip().casefold() in {"1", "true", "sim", "yes"}


def motivo_bloqueio_uniao(lista):
    """Cheap eligibility for list summaries; canonical identities are checked in preview."""
    if lista.get("unida_em_lista_id"):
        return "Esta lista já foi unida a outra lista."
    status = common._normalizar_status_lista_pedido(lista.get("status"))
    if status not in STATUS_PERMITIDOS or (status == "Lista gerada" and str(lista.get("status") or "").strip().casefold() not in {"", "lista gerada"}):
        return "Somente listas em preparação ou orçamento podem ser unidas."
    if not str(lista.get("fornecedor_id") or "").strip():
        return "Selecione um fornecedor cadastrado nesta lista antes de unir."
    if not str(lista.get("store_id") or "").strip() and str(lista.get("loja") or "").strip().casefold() in {"", "__todas", "todas", "todas as lojas"}:
        return "Defina uma loja específica nesta lista antes de unir."
    if any(lista.get(key) for key in ("packing_list", "actual_ship_date", "customs_clearance_date", "received_at", "shipment_id", "embarque_id")):
        return "Listas com documentos de embarque ou entregas registradas não podem ser unidas."
    for item in lista.get("itens") or []:
        if not isinstance(item, dict):
            return "A lista contém um produto inválido."
        if _verdadeiro(item.get("compra_aprovada")) or _verdadeiro(item.get("Compra aprovada")):
            return "A lista contém compras já aprovadas."
        try:
            if any(_numero(common._primeiro_texto_item(item, keys), keys[0], vazio=Decimal(0)) > 0 for keys in RECEBIMENTOS):
                return "A lista contém quantidades recebidas ou defeituosas."
        except HTTPException:
            return "A lista contém quantidades de recebimento inválidas."
    return ""


def _digest(value):
    try:
        source = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError):
        raise HTTPException(409, "A lista contém valores inválidos. Revise os produtos antes de unir.") from None
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _numero(value, campo, *, vazio=None):
    if value is None or str(value).strip() == "":
        return vazio
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = Decimal(str(value).strip().replace(",", "."))
        if not number.is_finite() or number < 0 or not math.isfinite(float(number)) or (number and float(number) == 0):
            raise ValueError()
    except (InvalidOperation, ValueError, OverflowError):
        raise HTTPException(409, f"Valor inválido em {campo}. Corrija a lista antes de unir.") from None
    return number


def _json_numero(value):
    if value is None:
        return ""
    if not value.is_finite() or not math.isfinite(float(value)):
        raise HTTPException(409, "O total dos produtos excede o limite numérico.")
    return int(value) if value == value.to_integral_value() else float(value)


def _origem(lista):
    return {"id": str(lista["id"]), "nome_lista": str(lista.get("nome_lista") or "Sem nome")}


def _selecionar(listas, ids, client_id):
    selected = []
    for lista_id in ids:
        matches = [row for row in listas if isinstance(row, dict) and str(row.get("id")) == lista_id]
        if len(matches) != 1:
            raise HTTPException(404, "Uma das listas selecionadas não está disponível neste cliente.")
        lista = matches[0]
        motivo = motivo_bloqueio_uniao(lista)
        if motivo:
            raise HTTPException(409, motivo)
        selected.append(lista)
    identities = []
    for lista in selected:
        store = common._resolver_escopo_loja_medias(client_id, lista.get("loja"), lista.get("store_id"), exigir_especifica=True)
        supplier_id = str(lista.get("fornecedor_id") or "").strip()
        currency = str(lista.get("currency") or "USD").strip().upper()
        identities.append((store["store_id"], supplier_id, currency))
    if len(set(identities)) != 1:
        raise HTTPException(409, "Selecione listas da mesma loja, fornecedor e moeda.")
    supplier = common._resolver_fornecedor_lista(client_id, identities[0][1])
    identity = {"loja": store["loja"], "store_id": store["store_id"], **supplier, "currency": identities[0][2]}
    return selected, identity


def _normalizar(raw):
    quantidade = _numero(common._primeiro_texto_item(raw, ["Quantidade", "quantity", "Qtd", "Qtde", "Qty"]), "quantidade", vazio=Decimal(0))
    if quantidade != quantidade.to_integral_value():
        raise HTTPException(409, "A quantidade de cada produto deve ser inteira. Revise as listas antes de unir.")
    valor = _numero(common._primeiro_texto_item(raw, ["Valor unidade", "Valor unitario", "Valor unitário", "Cost", "Custo", "Preco", "Preço"]), "preço unitário")
    total = _numero(common._primeiro_texto_item(raw, ["Valor total", "Sub-total(USD)", "Subtotal USD", "Sub total", "Subtotal"]), "valor total", vazio=Decimal(0))
    if valor is None:
        if total and not quantidade:
            raise HTTPException(409, "Um produto tem valor total sem quantidade. Revise a lista antes de unir.")
        valor = total / quantidade if quantidade else Decimal(0)
    defaults = {}
    for key, aliases, default in (("MOQ", ["MOQ", "moq", "Quantidade minima", "Quantidade mínima"], 0),
                                  ("package_multiple", ["package_multiple", "Multiplo embalagem", "Múltiplo embalagem", "Packing multiple"], 1)):
        defaults[key] = _numero(common._primeiro_texto_item(raw, aliases), key, vazio=Decimal(default))
    if defaults["package_multiple"] < 1:
        raise HTTPException(409, "O múltiplo de embalagem deve ser pelo menos 1. Revise as listas antes de unir.")
    item = common._normalizar_item_lista_pedido(raw)
    item["SKU"] = normalizar_sku_cubagem(item.get("SKU"))
    if not item["SKU"]:
        raise HTTPException(409, "A lista contém um produto sem SKU.")
    item["Quantidade"], item["Valor unidade"] = _json_numero(quantidade), _json_numero(valor)
    peso = _numero(item.get(common.PESO_LISTA_PEDIDO_KEY), "peso total")
    item["peso_uniao_unitario"] = _json_numero(peso / quantidade if peso is not None and quantidade else None)
    for campo in ("MOQ", "package_multiple"):
        item[campo] = _json_numero(defaults[campo])
    return item


def _preparar(listas, ids, client_id):
    selected, identity = _selecionar(listas, ids, client_id)
    token = _digest({"schema": "jk.medias.uniao.v1", "tenant": client_id, "listas": selected, "identidade": identity})
    grouped = {}
    for lista in selected:
        if not isinstance(lista.get("itens"), list) or not lista["itens"]:
            raise HTTPException(409, "Todas as listas selecionadas precisam conter produtos.")
        for raw in lista["itens"]:
            item = _normalizar(raw)
            grouped.setdefault(item["SKU"], []).append((item, _origem(lista)))
    conflitos, itens, bases = [], [], {}
    for sku, entries in grouped.items():
        base = copy.deepcopy(entries[0][0])
        quantidade = sum((_numero(item["Quantidade"], "quantidade") for item, _ in entries), Decimal(0))
        base["Quantidade"] = _json_numero(quantidade)
        campos = {}
        for campo, rotulo in CAMPOS.items():
            options = {}
            for item, origin in entries:
                value = item.get(campo, "")
                if campo in CAMPOS_NUMERICOS:
                    value = _json_numero(_numero(value, rotulo))
                elif not isinstance(value, (str, int, float)):
                    raise HTTPException(409, f"Valor inválido em {rotulo}.")
                else:
                    value = str(value).strip()
                key = _digest(value)
                option = options.setdefault(key, {"id": key, "valor": value, "listas": []})
                if origin not in option["listas"]:
                    option["listas"].append(origin)
            if campo not in CAMPOS_NUMERICOS:
                filled = {key: value for key, value in options.items() if value["valor"] != ""}
                options = filled or options
            campos[campo] = next(iter(options.values()))["valor"]
            if len(options) > 1:
                conflitos.append({"id": _digest([sku, campo]), "sku": sku, "campo": campo, "rotulo": rotulo, "opcoes": list(options.values())})
        base.update(campos)
        bases[sku] = base
        itens.append({"sku": sku, "descricao": str(base.get(common.TITULO_PRODUTO_INGLES_KEY) or ""),
                      "quantidade": base["Quantidade"], "valor_unitario": base["Valor unidade"], "campos": campos})
    previa = {"token": token, "listas": [_origem(row) for row in selected], **identity,
              "total_listas": len(selected), "total_skus": len(itens),
              "skus_repetidos": sum(len(entries) > 1 for entries in grouped.values()),
              "total_quantidade": _json_numero(sum((_numero(i["quantidade"], "quantidade") for i in itens), Decimal(0))),
              "itens": itens, "conflitos": conflitos}
    return previa, selected, bases


def _resolver_itens(previa, bases, escolhas):
    expected = {conflict["id"] for conflict in previa["conflitos"]}
    if set(escolhas) != expected:
        raise HTTPException(400, "Revise todas as diferenças antes de criar a lista unificada.")
    for conflict in previa["conflitos"]:
        chosen = next((opt for opt in conflict["opcoes"] if opt["id"] == escolhas[conflict["id"]]), None)
        if chosen is None:
            raise HTTPException(400, "Uma das escolhas não pertence à prévia apresentada.")
        bases[conflict["sku"]][conflict["campo"]] = chosen["valor"]
    for item in bases.values():
        qty, price = (_numero(item[key], key, vazio=Decimal(0)) for key in ("Quantidade", "Valor unidade"))
        with localcontext() as numeric:
            numeric.prec = 640
            item["Valor total"] = _json_numero((qty * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        _limpar_aliases(item)
        for key in ("Foto", "M3 individual", common.CBM_LISTA_PEDIDO_KEY, "M3", "m3", "Cubagem fonte", "Cubagem data", "Cubagem unidade"):
            item[key] = ""
        item.update({"Cubagem status": "pendente", "Cubagem loja": previa["store_id"]})
        weight = _numero(item.pop("peso_uniao_unitario", ""), "peso individual")
        item[common.PESO_LISTA_PEDIDO_KEY] = _json_numero(weight * qty if weight is not None else None)
        item["Frete Internacional"] = ""
        item["received_quantity"] = item["defective_quantity"] = 0
        for key in list(item):
            if key.startswith("compra_aprovada") or key.startswith("Compra aprovada") or key in {
                "analise_concorrentes", "margens", "margem_percentual", "II", "IPI", "PIS", "COFINS", "Imposto", "Descricao do NCM",
            }:
                item.pop(key, None)
    return list(bases.values())


def _resposta(lista):
    return {"success": True, "lista": {**common._resumo_lista_pedido(lista), "itens": copy.deepcopy(lista.get("itens") or [])},
            "origens": [origin["id"] for origin in lista.get("uniao_origens") or []]}


def _previa_sync(req, client_id):
    previa, _, _ = _preparar(common._carregar_listas_pedidos(client_id), req.lista_ids, client_id)
    return {"success": True, "previa": previa}


@common._retry_listas_pedidos
def _unir_sync(req, client_id):
    listas = common._carregar_listas_pedidos(client_id)
    assinatura = _digest(req.model_dump())
    for row in listas:
        if row.get("uniao_operacao_id") == req.operacao_id:
            if row.get("uniao_assinatura") != assinatura:
                raise HTTPException(409, "Esta operação já foi usada com escolhas diferentes. Atualize a prévia.")
            return _resposta(row)
    previa, selected, bases = _preparar(listas, req.lista_ids, client_id)
    if previa["token"] != req.previa_token:
        raise HTTPException(409, "Uma das listas mudou. Atualize a prévia e revise novamente.")
    itens = _resolver_itens(previa, bases, req.escolhas)
    now, new_id = datetime.now().isoformat(timespec="microseconds"), str(uuid.uuid4())
    nova = {key: previa[key] for key in ("loja", "store_id", "fornecedor_id", "supplier", "currency")}
    nova.update({"id": new_id, "nome_lista": req.nome_lista, "status": "Lista gerada", "itens": itens,
                 "created_at": now, "updated_at": now, "uniao_origens": previa["listas"],
                 "uniao_operacao_id": req.operacao_id, "uniao_assinatura": assinatura})
    for source in selected:
        source.update({"unida_em_lista_id": new_id, "unida_em_lista_nome": req.nome_lista,
                       "unida_em": now, "updated_at": now})
    listas.insert(0, nova)
    common._salvar_listas_pedidos(client_id, listas)
    for source in selected:
        common._limpar_cache_lista_pedido(client_id, str(source["id"]))
    return _resposta(nova)


async def api_medias_compras_listas_uniao_previa(req: ListasPedidosUniaoPreviaRequest,
                                               client_id: str = Depends(common.get_tenant_id)):
    from backend.services.blocking_workers import run_heavy
    return await run_heavy(_previa_sync, req=req, client_id=client_id)


async def api_medias_compras_listas_uniao(req: ListasPedidosUniaoRequest,
                                        client_id: str = Depends(common.get_tenant_id)):
    from backend.services.blocking_workers import run_heavy
    return await run_heavy(_unir_sync, req=req, client_id=client_id)


__all__ = ["api_medias_compras_listas_uniao_previa", "api_medias_compras_listas_uniao"]
