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
    if re.search(r"\b(mercado livre|mercadolivre|ml|anuncio|anuncios|mlb\d+)\b", text):
        domains.append("anuncios_ml")
    if re.search(r"\b(estoque|saldo|quantidade em estoque|disponivel em estoque)\b", text):
        domains.append("estoque")
        if re.search(r"\b(full|fulfillment|mercado envios)\b", text):
            domains.append("mercado_full")
    return domains


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
