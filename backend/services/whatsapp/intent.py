"""Intent, store scope, pagination and contextual period classification."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from backend.services.whatsapp import formatting


WHATSAPP_IMPLICIT_STORE_RECENT_SECONDS = 30 * 60


def query_only_domains(value: Any) -> list[str]:
    """Classify domains that must never be mutated through WhatsApp."""
    text = formatting._whatsapp_text_key(value)
    domains: list[str] = []
    implicit_latest_ml_sale = bool(
        re.search(r"\b(ultima|ultimo|mais recente)\b", text)
        and re.search(r"\bsku\s*[a-z0-9._/-]+\b", text)
        and re.search(r"\b(mercado livre|mercadolivre|ml)\b", text)
        and not re.search(r"\b(devolucao|devolucoes|reembolso|estorno)\b", text)
    )
    if implicit_latest_ml_sale or formatting._whatsapp_daily_sales_report_requested(value) or re.search(
        r"\b(venda|vendas|vendido|vendidos|faturamento|pedidos?|devolucao|devolucoes|sync de vendas|sincronizacao de vendas)\b",
        text,
    ):
        domains.append("vendas")
    if re.search(r"\b(mercado livre|mercadolivre|ml|anuncio|anuncios|mlb[\s_-]*\d+)\b", text):
        domains.append("anuncios_ml")
    if re.search(r"\b(estoque|saldo|quantidade em estoque|disponivel em estoque)\b", text):
        domains.append("estoque")
        if re.search(r"\b(full|fulfillment|mercado envios)\b", text):
            domains.append("mercado_full")
    return domains


def positive_stock_sku_count_requested(value: Any) -> bool:
    """Recognize a catalog-wide count, not the balance of one named SKU."""

    text = formatting._whatsapp_text_key(value)
    if not text:
        return False
    if re.search(r"\b(full|fulfillment|mercado envios)\b", text):
        return False
    count_subject = bool(
        re.search(r"\bquantos?\s+(?:skus?|produtos?|itens?)\b", text)
        or re.search(r"\b(?:numero|quantidade|contagem|total)\s+(?:de\s+)?(?:skus?|produtos?|itens?)\b", text)
    )
    stock_condition = bool(
        re.search(r"\b(?:com|em|tem|tenham|possuem?)\s+(?:o\s+)?(?:estoque|saldo)(?:\s+positivo)?\b", text)
        or re.search(r"\bestao\s+(?:com|no)\s+(?:estoque|saldo)(?:\s+positivo)?\b", text)
        or re.search(r"\b(?:estoque|saldo)\s+positivo\b", text)
    )
    return bool(count_subject and stock_condition)


def mercado_livre_sales_lookup(value: Any) -> dict[str, Any]:
    """Return the strict ML route for one latest or explicitly identified sale."""
    raw = str(value or "")
    text = formatting._whatsapp_text_key(raw)
    latest_sale = bool(
        re.search(r"\b(ultima|ultimo|mais recente)\s+(venda|pedido)\b", text)
        or re.search(r"\b(venda|pedido)\s+mais recente\b", text)
    )
    latest_return = bool(
        re.search(r"\b(ultima|ultimo|mais recente)\s+(devolucao|reembolso|estorno)\b", text)
        or re.search(r"\b(devolucao|reembolso|estorno)\b[^.]{0,80}\b(mais recente|ultima|ultimo)\b", text)
    )
    explicit_both = bool(
        re.search(
            r"\b(ultima|ultimo|mais recente)\s+(venda|pedido)\b[^.]{0,100}\b(e|tambem|junto|ambos)\b"
            r"[^.]{0,100}\b(ultima|ultimo|mais recente)\s+(devolucao|reembolso|estorno)\b",
            text,
        )
        or re.search(
            r"\b(ultima|ultimo|mais recente)\s+(devolucao|reembolso|estorno)\b[^.]{0,100}\b(e|tambem|junto|ambos)\b"
            r"[^.]{0,100}\b(ultima|ultimo|mais recente)\s+(venda|pedido)\b",
            text,
        )
    )
    if latest_return and not explicit_both:
        latest_sale = False
    id_match = re.search(
        r"\b(?:(?:pedido|venda|order|pack)(?:\s+(?:especific[oa]|id|numero|n))?|"
        r"(?:id|numero)\s+(?:do|da)\s+(?:pedido|venda|order|pack))\s*[:#-]?\s*(\d{3,20})\b",
        text,
    )
    if not id_match and re.search(r"\b(venda|pedido|order|pack)\b", text):
        standalone = re.findall(r"\b\d{10,20}\b", text)
        id_match = re.search(re.escape(standalone[0]), text) if len(standalone) == 1 else None
    requested_id = re.sub(r"\D+", "", id_match.group(1) if id_match and id_match.lastindex else id_match.group(0) if id_match else "")
    if mercado_livre_return_reference_only(text):
        requested_id = ""
    mode = "latest" if latest_sale else "exact" if requested_id else ""
    if not mode:
        return {}
    arguments: dict[str, Any] = {"force_refresh": True, "limite": 1, "incluir_detalhes": True}
    if requested_id:
        arguments["id_pedido"] = requested_id
    return {
        "mode": mode,
        "arguments": arguments,
        "forbidden_tools": [
            "bling_sales_orders", "sales_returns_query", "sales_ranking", "sales_summary",
            "sales_timeseries", "avg_ticket", "period_comparison", "sales_anomalies",
        ],
    }


def mercado_livre_return_reference_only(value: Any) -> bool:
    """Tell apart a returned order reference from a request to inspect the sale itself."""
    text = formatting._whatsapp_text_key(value)
    if not re.search(r"\b(devolucao|devolucoes|reembolso|reembolsos|estorno|estornos)\b", text):
        return False
    explicit_latest_both = bool(
        re.search(
            r"\b(ultima|ultimo|mais recente)\s+(venda|pedido)\b[^.]{0,100}\b(e|tambem|junto|ambos)\b"
            r"[^.]{0,100}\b(ultima|ultimo|mais recente)\s+(devolucao|reembolso|estorno)\b",
            text,
        )
        or re.search(
            r"\b(ultima|ultimo|mais recente)\s+(devolucao|reembolso|estorno)\b[^.]{0,100}\b(e|tambem|junto|ambos)\b"
            r"[^.]{0,100}\b(ultima|ultimo|mais recente)\s+(venda|pedido)\b",
            text,
        )
    )
    explicit_exact_both = bool(
        re.search(
            r"\b(venda|pedido|order|pack)\s*[:#-]?\s*\d{10,20}\b[^.]{0,80}\b(e|tambem|junto|ambos)\b"
            r"[^.]{0,80}\b(devolucao|reembolso|estorno)\b",
            text,
        )
        or re.search(
            r"\b(devolucao|reembolso|estorno)\b[^.]{0,80}\b(e|tambem|junto|ambos)\b"
            r"[^.]{0,80}\b(venda|pedido|order|pack)\s*[:#-]?\s*\d{10,20}\b",
            text,
        )
    )
    return not (explicit_latest_both or explicit_exact_both)


def mercado_livre_sales_policy(value: Any, source_policy: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = dict(source_policy) if isinstance(source_policy, dict) else {}
    lookup = mercado_livre_sales_lookup(value)
    if lookup:
        policy["required_tools"] = list(dict.fromkeys([
            "mercado_livre_orders", *list(policy.get("required_tools") or []),
        ]))
        policy["forbidden_tools"] = list(dict.fromkeys([
            *list(policy.get("forbidden_tools") or []), *list(lookup.get("forbidden_tools") or []),
        ]))
        policy["sales_lookup"] = lookup
    return policy, lookup


def mercado_livre_sales_tool_allowed(
    lookup: dict[str, Any], tool_id: str, explicit_returns: bool, explicit_stock: bool,
) -> bool:
    if not lookup or tool_id == "mercado_livre_orders":
        return True
    return bool(
        (explicit_returns and tool_id == "mercado_livre_returns")
        or (explicit_stock and tool_id in {"bling_stock_balances", "mercado_livre_listing", "stock_data"})
    )


def mercado_livre_sales_arguments(lookup: dict[str, Any], arguments: Any) -> dict[str, Any]:
    result = dict(arguments) if isinstance(arguments, dict) else {}
    if lookup.get("mode") != "exact":
        for key in ("id_pedido", "pedido_id", "order_id", "id_order"):
            result.pop(key, None)
    result.update(lookup.get("arguments") or {})
    return result


def readonly_inquiry(value: Any) -> bool:
    text = formatting._whatsapp_text_key(value)
    if not text:
        return False
    explicit_mutation = bool(
        re.search(
            r"\b(responda|envie a resposta|mande a resposta|publique|pause|ative|desative|altere|mude|"
            r"sincronize|cancele|remova|exclua|aprove|rejeite)\b",
            text,
        )
    )
    if explicit_mutation:
        return False
    subject = bool(
        re.search(
            r"\b(saldo|estoque|anuncio|anuncios|informacao|informacoes|detalhe|detalhes|pergunta|perguntas|"
            r"pendencia|pendencias|fila|venda|vendas|pedido|pedidos|preco|status|relatorio|dados)\b",
            text,
        )
    )
    inquiry = bool(
        re.search(r"\b(tem|ha|existe|existem|chegou|chegaram|qual|quais|quanto|quantos|quantas)\b", text)
        or re.search(r"\b(me diga|mostre|consulte|verifique|liste|informe|quero saber|pode ver|consegue ver)\b", text)
    )
    return bool(subject and inquiry)


def mutation_intent(value: Any, *, mutation_detector: Callable[[str], bool]) -> bool:
    text = formatting._whatsapp_text_key(value)
    if readonly_inquiry(value):
        return False
    send_mutation = bool(
        re.search(r"\b(envie|enviar|mande|mandar)\b", text)
        and re.search(r"\b(resposta|mensagem|pergunta|aprovacao|publicacao)\b", text)
    )
    strong_mutation = send_mutation or bool(
        re.search(
            r"\b(pause|pausar|ative|ativar|desative|desativar|publique|publicar|responda|responder|"
            r"aprove|aprovar|cancele|cancelar|mude|mudar|troque|trocar|remova|remover|exclua|excluir|"
            r"delete|deletar|sincronize|sincronizar|altere|alterar|corrija|corrigir)\b",
            text,
        )
    )
    query_delivery = bool(
        re.search(r"\b(envie|enviar|mande|mandar|mostre|mostrar|passe|passar|gere|gerar)\b", text)
        and re.search(r"\b(relatorio|resumo|consulta|dados|informacoes|resultados|lista|listagem)\b", text)
    )
    fresh_api_query = bool(
        re.search(r"\b(atualize|atualizar)\b", text)
        and re.search(r"\b(api|dados|consulta|relatorio|resumo|listagem|resultados)\b", text)
        and not re.search(r"\b(preco|estoque|titulo|status|situacao|quantidade|saldo|resposta|mensagem)\b", text)
    )
    if (query_delivery or fresh_api_query) and not strong_mutation:
        return False
    if strong_mutation:
        return True
    if mutation_detector(str(value or "")):
        return True
    return bool(
        re.search(
            r"\b(pause|pausar|ative|ativar|desative|desativar|publique|publicar|responda|responder|"
            r"aprove|aprovar|cancele|cancelar|mude|mudar|troque|trocar|remova|remover|exclua|excluir|"
            r"delete|deletar|sincronize|sincronizar|atualize|atualizar|altere|alterar|corrija|corrigir)\b",
            text,
        )
    )


def post_sale_action(value: Any) -> bool:
    text = formatting._whatsapp_text_key(value)
    return bool(
        re.search(r"\b(pergunta|perguntas|pos venda|conversa|resposta ao cliente|mensagem ao cliente)\b", text)
        and re.search(r"\b(responda|responder|envie|enviar|mande|mandar|aprove|aprovar)\b", text)
        and not re.search(r"\b(anuncio|anuncios|preco|estoque|titulo|status do anuncio|pausar|publicar)\b", text)
    )


def protected_mutation_domains(value: Any, *, mutation_detector: Callable[[str], bool]) -> list[str]:
    domains = query_only_domains(value)
    if post_sale_action(value):
        domains = [domain for domain in domains if domain != "anuncios_ml"]
    return domains if domains and mutation_intent(value, mutation_detector=mutation_detector) else []


def action_spec_query_only_domains(spec: Any) -> list[str]:
    if spec is None:
        return []
    if isinstance(spec, dict):
        getter = lambda key, default="": spec.get(key, default)
    else:
        getter = lambda key, default="": getattr(spec, key, default)
    text = formatting._whatsapp_text_key(
        " ".join(
            str(item or "")
            for item in (
                getter("id") or getter("action_id"),
                getter("module"),
                getter("label"),
                getter("status_kind"),
                " ".join(getter("side_effects", ()) or ()),
            )
        )
    )
    domains: list[str] = []
    if re.search(r"\b(vendas?|vendas sync|vendas cancel|sincronizar vendas)\b", text):
        domains.append("vendas")
    module_name = str(getter("module") or "").strip().lower()
    action_id = str(getter("id") or getter("action_id") or "").strip().lower()
    if (
        module_name == "anuncios_ml"
        or action_id.startswith("ml.anuncio")
        or re.search(r"\b(anuncio|anuncios|mercado_livre)\b", text)
    ):
        domains.append("anuncios_ml")
    return list(dict.fromkeys(domains))


def exact_store_matches(value: Any, stores: list[str]) -> list[str]:
    text = formatting._whatsapp_text_key(value)
    raw_matches: list[tuple[str, str]] = []
    seen_keys: set[str] = set()
    for store in stores:
        key = formatting._whatsapp_text_key(store)
        if key and key not in seen_keys and re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", text):
            seen_keys.add(key)
            raw_matches.append((store, key))
    return [
        store
        for store, key in raw_matches
        if not any(
            key != other_key and re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", other_key)
            for _, other_key in raw_matches
        )
    ]


def all_stores_requested(value: Any) -> bool:
    text = formatting._whatsapp_text_key(value)
    return bool(
        re.search(
            r"\b(todas as lojas|todas lojas|todas as contas|cada loja|cada conta|por loja|por conta|"
            r"loja a loja|conta a conta|separad[oa]s? por loja|compare as lojas|comparar as lojas|visao geral)\b",
            text,
        )
    )


def store_scoped_request(value: Any) -> bool:
    text = formatting._whatsapp_text_key(value)
    if not text:
        return False
    return bool(
        re.search(
            r"\b(venda|vendas|faturamento|pedido|pedidos|devolucao|devolucoes|estoque|saldo|sku|produto|produtos|"
            r"anuncio|anuncios|mercado livre|mercadolivre|preco|precos|margem|lucro|relatorio|relatorios|"
            r"pergunta|perguntas|pos venda|pos-venda|comprador|compradores|promocao|promocoes)\b",
            text,
        )
    )


def api_query_requires_store(value: Any, domains: list[str]) -> bool:
    del value
    return bool(set(domains) & {"vendas", "anuncios_ml", "estoque", "mercado_full"})


def pagination_request(value: Any) -> bool:
    text = formatting._whatsapp_text_key(value)
    return bool(
        re.fullmatch(
            r"(?:os |as )?(?:proximos|proximas|mais resultados|pagina seguinte|continuar|continue)",
            text,
        )
    )


def contextual_report_request(value: Any) -> bool:
    """Recognize a report round that depends on recent conversation context."""
    text = formatting._whatsapp_text_key(value)
    if not text:
        return False
    relative_period = bool(
        re.search(r"\b(?:deste|desse|este|nesse|no) mes\b", text)
        or re.search(r"\bmes atual\b", text)
    )
    same_store = bool(re.search(r"\b(?:na |da |pela )?mesma (?:loja|conta)\b", text))
    report_language = bool(re.search(r"\b(relatorio|resumo|analise|vendas?|pedidos?|faturamento)\b", text))
    return bool(relative_period or (same_store and report_language) or text in {"mesma loja", "mesma conta"})


def contextual_report_period(value: Any, *, current: datetime) -> tuple[str, str]:
    text = formatting._whatsapp_text_key(value)
    if not (
        re.search(r"\b(?:deste|desse|este|nesse|no) mes\b", text)
        or re.search(r"\bmes atual\b", text)
    ):
        return "", ""
    return current.replace(day=1).date().isoformat(), current.date().isoformat()


def implicit_store_followup(
    value: Any,
    *,
    context_age: float,
    has_direct_policy: bool,
    recent_seconds: int = WHATSAPP_IMPLICIT_STORE_RECENT_SECONDS,
) -> bool:
    text = formatting._whatsapp_text_key(value)
    if not text or all_stores_requested(value):
        return False
    strong_reference = bool(
        re.search(r"^(?:agora|entao|e\s|tambem|continue|continuando)\b", text)
        or re.search(
            r"\b(?:dess[ae]s?|dest[ae]s?|del[ae]s?|sobre isso|sobre eles|sobre elas|"
            r"mais detalhes?|detalhe melhor|motivo de cada|cada (?:um|uma|pedido|venda|devolucao|reclamacao)|"
            r"mesma loja|mesma conta)\b",
            text,
        )
    )
    if strong_reference:
        return True
    word_count = len(re.findall(r"[a-z0-9]+", text))
    return bool(has_direct_policy and context_age <= recent_seconds and word_count <= 16)
