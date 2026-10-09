"""Scoped LIGHTNING proposal selection, refresh and financial reanalysis."""
from __future__ import annotations

import copy
import math
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from .promocoes_lightning import lightning_offer_key, lightning_window_valid
from .promocoes_lightning_acao import lightning_reference, lightning_same_window, lightning_has_schedule_data


def money_cents(value: Any):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            return None
        return int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None


def price_number(value: Any):
    cents = money_cents(value)
    return cents / 100 if cents is not None and cents > 0 else None


def ml_error_code(response):
    try:
        data = response.json()
    except Exception:
        return ""
    parts = []
    def collect(value, depth=0):
        if depth > 5:
            return
        if isinstance(value, dict):
            for key in ("error", "code", "error_code", "message", "error_message", "detail", "errors", "cause"):
                if key in value:
                    collect(value[key], depth + 1)
        elif isinstance(value, list):
            for entry in value[:8]:
                collect(entry, depth + 1)
        elif isinstance(value, str):
            parts.extend(re.findall(r"\b(?:ERROR_[A-Z0-9_]+|CANDIDATE_NOT_FOUND|FINAL_PRICE_[A-Z0-9_]+)\b", value.upper()))
    collect(data)
    return next((code for code in parts if "CREDIBILITY" in code), parts[0] if parts else "")


def participation_error_detail(response, fallback):
    """Keep promotion errors separate from generic listing-price update advice."""
    try:
        data = response.json()
    except Exception:
        return fallback
    if not isinstance(data, dict):
        return fallback
    message = data.get("message") or data.get("error") or data.get("detail")
    return str(message) if isinstance(message, (str, int)) else fallback


def _official_prices(service, client_id, loja, cfg, item_id, currency=None):
    standard, sale = None, None
    if item_id.startswith("MLB"):
        if currency and currency != "BRL":
            raise ValueError("Moeda da analise incompatível com custos em reais.")
        currency = "BRL"
    response, cfg = service._ml_api_request(client_id, loja, cfg, "GET",
        f"https://api.mercadolibre.com/items/{item_id}/prices", timeout=12)
    if response.status_code != 200:
        raise ValueError("Preco standard oficial nao confirmado.")
    body = response.json()
    if not isinstance(body, dict) or (str(body.get("id") or "").upper() != item_id):
        raise ValueError("Precos retornados pertencem a outro anuncio.")
    prices = []
    for entry in body.get("prices", []) if isinstance(body, dict) else []:
        if not isinstance(entry, dict) or str(entry.get("type", "")).lower() != "standard":
            continue
        if currency and str(entry.get("currency_id") or "") != currency:
            continue
        conditions = entry.get("conditions") or {}
        quantity = conditions.get("min_purchase_unit", 1)
        if isinstance(quantity, bool) or quantity != 1 or entry.get("net_amount") is not None or str(entry.get("amount_tax_inclusion_type") or "").lower() == "net":
            continue
        current = datetime.now(timezone.utc)
        valid = True
        for key, is_start in (("start_time", True), ("end_time", False)):
            if conditions.get(key):
                try:
                    boundary = datetime.fromisoformat(str(conditions[key]).replace("Z", "+00:00"))
                    if boundary.tzinfo is None or (current < boundary if is_start else current >= boundary):
                        valid = False
                except (ValueError, TypeError):
                    valid = False
        if not valid:
            continue
        contexts = conditions.get("context_restrictions") or []
        if not isinstance(contexts, list) or set(contexts) - {"channel_marketplace"}:
            continue
        amount = price_number(entry.get("amount"))
        if amount is not None:
            prices.append(amount)
    if not prices or len(set(prices)) != 1:
        raise ValueError("Preco standard marketplace ausente ou conflitante.")
    standard = prices[0]
    response, cfg = service._ml_api_request(client_id, loja, cfg, "GET",
        f"https://api.mercadolibre.com/items/{item_id}/sale_price",
        params={"context": "channel_marketplace"}, timeout=12)
    if response.status_code == 200:
        body = response.json()
        sale = price_number(body.get("amount")) if isinstance(body, dict) and body.get("currency_id") == currency else None
    return {"standard_price": standard, "sale_price": sale}, cfg


def _bounds(raw):
    result = []
    for names in (("min_discounted_price", "minimum_discounted_price", "min_deal_price"),
                  ("max_discounted_price", "maximum_discounted_price", "max_deal_price")):
        values = [raw[name] for name in names if name in raw and raw[name] is not None]
        parsed = [price_number(value) for value in values]
        if values and (None in parsed or len(set(parsed)) != 1):
            raise ValueError("Limites oficiais de preco invalidos ou conflitantes.")
        result.append(parsed[0] if parsed else None)
    if result[0] is not None and result[1] is not None and money_cents(result[0]) > money_cents(result[1]):
        raise ValueError("Limites oficiais de preco invertidos.")
    return result


def lightning_price_signature(raw):
    """Comparable monetary terms for duplicate records of one invitation."""
    try:
        bounds = tuple(money_cents(value) for value in _bounds(raw))
    except ValueError:
        names = ("min_discounted_price", "minimum_discounted_price", "min_deal_price",
                 "max_discounted_price", "maximum_discounted_price", "max_deal_price")
        bounds = ("invalid", tuple((name, repr(raw[name])) for name in names if name in raw))
    suggested = raw.get("suggested_discounted_price")
    signature = (("absent",) if suggested is None else
                 ("money", money_cents(suggested)) if price_number(suggested) is not None else
                 ("invalid", type(suggested).__name__, repr(suggested)))
    return bounds, signature


def _price_reason(raw, prices, *, adjust_candidate=False, price_source="candidate_price"):
    if price_source not in {"candidate_price", "suggested_discounted_price"}:
        return "Fonte da proposta oficial invalida.", None, None, None
    suggested = price_source == "suggested_discounted_price"
    price = price_number(raw.get("suggested_discounted_price") if suggested else
                         raw.get("price") if "price" in raw else raw.get("deal_price"))
    try:
        minimum, maximum = _bounds(raw)
    except ValueError as exc:
        return str(exc), None, None, price
    standard_cents = money_cents(prices["standard_price"])
    if suggested and (
        not adjust_candidate
        or str(raw.get("status") or raw.get("state") or "").strip().lower() != "candidate"
        or price is None or minimum is None or maximum is None
    ):
        return "Sugestao alternativa oficial ou limites do candidato nao confirmados.", minimum, maximum, price
    if (
        adjust_candidate
        and not suggested
        and str(raw.get("status") or raw.get("state") or "").strip().lower() == "candidate"
        and price is not None
        and standard_cents is not None
        and minimum is not None
        and maximum is not None
    ):
        # LIGHTNING candidate.price is a suggestion. Choose the nearest price
        # in the official interval with a strictly positive marketplace discount.
        # Keep raw intact: receipts/benefits quoted for its suggestion cannot be
        # reused when financial reanalysis quotes a different proposal.
        lower = money_cents(minimum)
        upper = min(money_cents(maximum), standard_cents - 1)
        if lower <= upper:
            price = min(max(money_cents(price), lower), upper) / 100
    if price is None or standard_cents is None or money_cents(price) >= standard_cents:
        return "Preco do convite deve ser menor que o standard marketplace atual.", minimum, maximum, price
    if (minimum is not None and money_cents(price) < money_cents(minimum)) or (maximum is not None and money_cents(price) > money_cents(maximum)):
        return "Preco do convite fora dos limites oficiais desta oferta.", minimum, maximum, price
    return "", minimum, maximum, price


def canonical_update_keys(updates):
    """Normalize only these additive response keys, leaving legacy rows intact."""
    result = {}
    for key, value in updates.items():
        for _ in range(2):
            if not any(marker in key for marker in ("\u00c3", "\u00c2")):
                break
            try:
                key = key.encode("latin1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                break
        result[key] = value
    return result


def _fresh_financial(service, analysis, client_id, loja, cfg, item_id, raw, row_context, price, standard):
    canonical_currency = (row_context.get("item") or {}).get("currency_id")
    if item_id.startswith("MLB") and canonical_currency not in (None, "", "BRL"):
        raise ValueError("Moeda da analise incompatível com custos em reais.")
    response, cfg = service._ml_api_request(client_id, loja, cfg, "GET",
        f"https://api.mercadolibre.com/items/{item_id}", params={"include_attributes": "all"}, timeout=12)
    item = response.json() if response.status_code == 200 else {}
    if not isinstance(item, dict) or str(item.get("id") or "").upper() != item_id:
        raise ValueError("Dados atuais do anuncio nao confirmados para recalcular a margem.")
    seller = str(cfg.get("user_id") or cfg.get("seller_id") or "")
    if not seller or str(item.get("seller_id") or "") != seller:
        raise ValueError("Vendedor atual do anuncio diverge da analise.")
    if str(item.get("site_id") or "") != str(row_context.get("site_id") or "MLB"):
        raise ValueError("Site atual do anuncio diverge da analise.")
    if item_id.startswith("MLB") and item.get("currency_id") != "BRL":
        raise ValueError("Moeda atual do anuncio incompatível com custos em reais.")
    costs, taxes = analysis._carregar_custos_impostos_cadastro_por_sku_loja(client_id, loja)
    sku = analysis._ml_extrair_sku(item)
    variations = analysis._ml_extrair_variacoes_resumo(item, client_id=client_id, loja=loja)
    skus = [variation.get("sku") for variation in variations if isinstance(variation, dict) and variation.get("sku")]
    cost = analysis._resolver_maior_custo_por_skus(costs, skus or [sku, row_context.get("sku")])
    tax = analysis._resolver_imposto_rate_por_sku(taxes, sku)
    if tax is None:
        tax = analysis._resolver_imposto_rate_por_sku(taxes, row_context.get("sku") or "")
    # Explicit file inputs belong to the canonical analysis. Current cadastro
    # values take precedence, while an unavailable canonical source is blocked.
    sources = row_context.get("fontes") or {}
    if cost is None and sources.get("custo") == "arquivo":
        cost = row_context.get("custo")
    if tax is None and sources.get("imposto") == "arquivo":
        tax = row_context.get("imposto_rate")
    if cost is None or tax is None:
        raise ValueError("Custo ou imposto atual nao confirmado para recalcular a margem.")
    percentage = (1 - price / standard) * 100
    financial, cfg = analysis._promo_obter_contexto_financeiro_acao(
        client_id, loja, cfg, item_id, item, raw, row_context["promotion_id"],
        standard, price, percentage, cost, tax, promotion_type_esperado="LIGHTNING")
    if not financial.get("tecnico_apto") or financial.get("margem") is None:
        raise ValueError(financial.get("motivo") or "Margem atual nao confirmada.")
    reference_id = row_context.get("promocao_a_id")
    reference_type = row_context.get("promocao_a_type")
    if not reference_id or not reference_type:
        raise ValueError("Referencia da Promocao 1 nao confirmada.")
    # Refresh the same reference campaign, never use the current winning price
    # as a replacement for the campaign selected in the server-side analysis.
    records, cfg = service._ml_obter_promocoes_item(client_id, loja, cfg, item_id, force_refresh=True)
    reference_raw, cfg = analysis._ml_obter_item_promocao_raw(
        client_id, loja, cfg, reference_id, reference_type, item_id)
    identity = analysis._promo_identidade_financeira_raw(reference_raw)
    if not (identity[3] and str(identity[0]).upper() == str(reference_id).upper()
            and str(identity[1]).upper() == str(reference_type).upper()
            and str(identity[2]).upper() == item_id):
        raise ValueError("Contexto atual da Promocao 1 nao confirmado.")
    reference_price, reference_discount = analysis._ml_extrair_preco_promocao_raw(reference_raw)
    if price_number(reference_price) is None:
        raise ValueError("Preco atual da Promocao 1 nao confirmado.")
    reference_financial, cfg = analysis._promo_obter_contexto_financeiro_acao(
        client_id, loja, cfg, item_id, item, reference_raw, reference_id,
        standard, reference_price, reference_discount, cost, tax, promotion_type_esperado=reference_type)
    reference_margin = reference_financial.get("margem")
    if reference_margin is None or not math.isfinite(float(reference_margin)):
        raise ValueError("Margem atual da Promocao 1 nao confirmada.")
    reference_status = analysis._ml_classificar_status_promocao_entry(reference_raw)
    for value in (row_context.get("margem_minima"), row_context.get("margem_tolerancia")):
        if isinstance(value, bool) or value is None or not math.isfinite(float(value)):
            raise ValueError("Regras de margem da analise invalidas.")
    approved = analysis._promo_margens_aprovadas_por_status(
        reference_margin, financial["margem"], reference_status,
        row_context.get("margem_minima"), row_context.get("margem_tolerancia"))
    updates = analysis._promo_campos_financeiros_acao(financial, raw, percentage, reference_margin)
    updates.update({"action_deal_price": price, "action_margem_ml": financial["margem"],
        "action_tarifa_ml": financial.get("tarifa"), "action_valor_liquido_ml": financial.get("valor_liquido"),
        "action_financeiro_exato": financial.get("exato", False), "Custo": analysis.formatar_moeda_br(cost),
        "action_discount_percentage": percentage, "Margem": analysis._format_pct_br(reference_margin),
        "Pre\u00e7o Final": analysis.formatar_moeda_br(reference_price),
        "reference_margin": reference_margin, "reference_status": reference_status,
        "Frete": analysis.formatar_moeda_br(reference_financial.get("frete")) if reference_financial.get("frete") is not None else "A calcular",
        "Tarifa": analysis.formatar_moeda_br(reference_financial.get("tarifa")) if reference_financial.get("tarifa") is not None else "A calcular",
        "Imposto": analysis.formatar_moeda_br(reference_financial.get("imposto")) if reference_financial.get("imposto") is not None else "A calcular",
        "Valor L\u00edquido": analysis.formatar_moeda_br(reference_financial.get("valor_liquido")) if reference_financial.get("valor_liquido") is not None else "A calcular"})
    return financial, canonical_update_keys(updates), approved, cfg


def _reconcile(service, client_id, loja, cfg, campaign, item, raw, price):
    from .promocoes_lightning_confirmacao import select_confirmed_lightning
    try:
        records, cfg = service._ml_obter_promocoes_item(client_id, loja, cfg, item, force_refresh=True)
    except Exception:
        return None, cfg
    return select_confirmed_lightning(service, records, cfg, campaign, item, raw, price), cfg


def validate_exact_lightning(service, client_id, loja, cfg, campaign, item_id, raw, selected_price, stock, diagnostics, prices=None):
    """Validate the legacy exact price or the server-reanalysed candidate proposal."""
    from .promocoes_lightning_acao import lightning_row_fields
    current = raw
    for refresh in range(2):
        try:
            official, cfg = _official_prices(service, client_id, loja, cfg, item_id) if prices is None else (prices, cfg)
            reason, minimum, maximum, price = _price_reason(
                current, official, adjust_candidate=prices is not None,
                price_source=(prices.get("_jk_lightning_price_source", "candidate_price")
                              if prices is not None else "candidate_price"))
        except Exception:
            diagnostics["decision_code"] = "OFFICIAL_PRICE_UNCONFIRMED"
            return None, "Preco standard oficial nao confirmado. Execute uma nova analise.", cfg
        diagnostics.update(official, official_price=price, min_price=minimum, max_price=maximum,
            invitation_price=price_number(current.get("price") if "price" in current else current.get("deal_price")))
        if not reason:
            if money_cents(price) != money_cents(selected_price):
                diagnostics["decision_code"] = "OFFICIAL_PRICE_CHANGED_REANALYSIS_REQUIRED"
                return None, "O preco oficial mudou. Execute uma nova analise antes de participar.", cfg
            return current, "", cfg
        if refresh or prices is not None:
            diagnostics["decision_code"] = "OFFICIAL_PRICE_INCONSISTENT"
            return None, reason + " Execute uma nova analise.", cfg
        selected = {"item_id": item_id, "deal_price": selected_price, "offer_id": lightning_reference(raw),
            **lightning_row_fields(raw, "LIGHTNING", campaign, item_id), "lightning_stock": stock}
        confirmed, cfg = service._promo_confirmar_oferta_lightning(client_id, loja, cfg, campaign, item_id, selected)
        if confirmed.get("_lightning_blocked") or not confirmed.get("_lightning_raw_confirmed"):
            diagnostics["decision_code"] = "OFFICIAL_INVITATION_UNCONFIRMED"
            return None, confirmed.get("_lightning_blocked") or "Convite atual nao confirmado.", cfg
        current = confirmed["_lightning_raw_confirmed"]
    return None, "Preco oficial nao confirmado.", cfg


def apply_reanalysed_lightning(service, analysis, client_id, loja, cfg, *, campaign, item_id,
                              selected, row_context, result_details):
    """Send at most twice, only after an official refresh and approved finances."""
    diagnostic = {"initial_price": price_number(row_context["selected"].get("action_deal_price")
                   or row_context["selected"].get("deal_price")), "official_price": None,
        "sent_price": None, "standard_price": None, "sale_price": None, "min_price": None,
        "max_price": None, "recalculated_margin": None, "attempt_count": 0,
        "decision_code": "CONTEXT_VALIDATED", "ml_error_code": "", "http_status": None}
    result_details.update(outcome="blocked", price_diagnostics=diagnostic)
    rejected_prices = set()
    rejected_invitation_key = None
    coherent_refresh_used = False
    while diagnostic["attempt_count"] < 2:
        # The canonical selection preserves the exact candidate/reference/window.
        canonical = copy.deepcopy(row_context["selected"])
        canonical["lightning_stock"] = selected.get("lightning_stock")
        confirmed, cfg = service._promo_confirmar_oferta_lightning(client_id, loja, cfg, campaign, item_id, canonical)
        if confirmed.get("_lightning_blocked"):
            diagnostic["decision_code"] = "OFFICIAL_INVITATION_UNCONFIRMED"
            return False, confirmed["_lightning_blocked"], cfg
        if confirmed.get("already_participating"):
            result_details.update(outcome="already_participating")
            result_details.update({key: confirmed[key] for key in ("status", "lightning_start_date", "lightning_finish_date", "lightning_schedule_pending") if key in confirmed})
            diagnostic["decision_code"] = "ALREADY_PARTICIPATING"
            return True, "Participacao atual confirmada pelo Mercado Livre.", cfg
        raw = confirmed.get("_lightning_raw_confirmed") or {}
        try:
            prices, cfg = _official_prices(service, client_id, loja, cfg, item_id, (row_context.get("item") or {}).get("currency_id"))
            reason, minimum, maximum, price = _price_reason(raw, prices, adjust_candidate=True)
        except Exception:
            diagnostic["decision_code"] = "OFFICIAL_PRICE_UNCONFIRMED"
            return False, "Nao foi possivel confirmar os precos oficiais atuais deste anuncio.", cfg
        price_source = "candidate_price"
        diagnostic.update(prices, min_price=minimum, max_price=maximum, official_price=price,
            invitation_price=price_number(raw.get("price") if "price" in raw else raw.get("deal_price")),
            suggested_price=price_number(raw.get("suggested_discounted_price")), price_source=price_source)
        if reason:
            if not coherent_refresh_used and diagnostic["attempt_count"] == 0:
                coherent_refresh_used = True
                continue
            diagnostic["decision_code"] = "OFFICIAL_PRICE_INCONSISTENT"
            return False, reason + " Os dados oficiais continuaram incoerentes apos nova consulta.", cfg
        if money_cents(price) in rejected_prices:
            # Only the same freshly confirmed candidate may provide an explicit
            # alternative. Never derive another discount or use another offer.
            current_key = lightning_offer_key(raw, campaign_id=campaign, item_id=item_id)
            if current_key != rejected_invitation_key:
                diagnostic["decision_code"] = "OFFICIAL_INVITATION_CHANGED_AFTER_REJECTION"
                result_details["outcome"] = "rejected"
                return False, "O convite mudou apos a recusa. Execute uma nova analise.", cfg
            alternate_reason, _, _, alternate = _price_reason(
                raw, prices, adjust_candidate=True, price_source="suggested_discounted_price")
            if alternate_reason or money_cents(alternate) in rejected_prices:
                diagnostic["decision_code"] = "OFFICIAL_PRICE_UNCHANGED_AFTER_REJECTION"
                result_details["outcome"] = "rejected"
                return False, "Mercado Livre recusou o preco por credibilidade. O mesmo convite nao trouxe outra sugestao oficial valida; nenhum preco recusado foi reenviado.", cfg
            price, price_source = alternate, "suggested_discounted_price"
            diagnostic.update(official_price=price, price_source=price_source)
        try:
            financial, updates, approved, cfg = _fresh_financial(service, analysis, client_id, loja, cfg,
                item_id, raw, row_context, price, prices["standard_price"])
        except Exception as exc:
            diagnostic["decision_code"] = "FINANCIAL_CONTEXT_UNCONFIRMED"
            return False, str(exc) if isinstance(exc, ValueError) else "Dados atuais indisponiveis para recalcular a margem.", cfg
        diagnostic["recalculated_margin"] = financial["margem"]
        result_details["analysis_row_updates"] = updates
        if not approved:
            diagnostic["decision_code"] = "RECALCULATED_MARGIN_REJECTED"
            return False, "O preco oficial atualizado nao passou pela margem minima e tolerancia da analise.", cfg
        attempt = {}
        ok, message, cfg = service._promo_aplicar_item_participacao_ml(client_id, loja, cfg,
            item_id=item_id, promotion_id=campaign, promotion_type="LIGHTNING", deal_price=price,
            allow_alternative_deal_price=False, result_details=attempt,
            stock=selected.get("lightning_stock"), lightning_context=raw,
            official_price_context={**prices, "_jk_lightning_price_source": price_source})
        if attempt.get("submission_attempted"):
            diagnostic["attempt_count"] += 1
            diagnostic["sent_price"] = price
            result_details["submission_attempted"] = True
        diagnostic.update(http_status=attempt.get("http_status"), ml_error_code=attempt.get("ml_error_code", ""))
        result_details.update({key: value for key, value in attempt.items()
                              if key not in {"price_diagnostics", "http_status", "ml_error_code"}})
        if ok:
            diagnostic["decision_code"] = "PARTICIPATION_CONFIRMED"
            return True, message, cfg
        if attempt.get("outcome") == "unknown":
            matched, cfg = _reconcile(service, client_id, loja, cfg, campaign, item_id, raw, price)
            if matched:
                result_details.update(outcome="applied", **service._promo_lightning_confirmation_fields(matched))
                diagnostic["decision_code"] = "PARTICIPATION_CONFIRMED_BY_REFRESH"
                return True, "Participacao confirmada em consulta apos resposta inconclusiva.", cfg
            diagnostic["decision_code"] = "PARTICIPATION_UNCONFIRMED"
            return False, "Resultado inconclusivo; consulta atual nao confirmou a participacao. Nenhum reenvio foi realizado.", cfg
        if diagnostic["http_status"] == 401:
            diagnostic["decision_code"] = "ML_AUTHENTICATION_REQUIRED"
            return False, message, cfg
        if diagnostic["http_status"] != 400 or diagnostic["ml_error_code"] != "ERROR_CREDIBILITY_DISCOUNTED_PRICE":
            diagnostic["decision_code"] = "ML_PARTICIPATION_REJECTED"
            return False, message, cfg
        rejected_prices.add(money_cents(price))
        rejected_invitation_key = lightning_offer_key(raw, campaign_id=campaign, item_id=item_id)
        if diagnostic["attempt_count"] >= 2:
            diagnostic["decision_code"] = "ML_PRICE_CREDIBILITY_REJECTED"
            return False, message, cfg
    return False, "Nenhuma participacao confirmada.", cfg
