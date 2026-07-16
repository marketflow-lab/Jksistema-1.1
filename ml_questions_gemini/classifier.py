from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .compatibility import is_compatibility_question
from .schemas import ListingSnapshot, QuestionCategory, QuestionContext


@dataclass
class Classification:
    category: QuestionCategory
    reason: str = ""
    prompt_injection: bool = False


class QuestionClassifier:
    def classify(self, question: QuestionContext, listing: ListingSnapshot) -> Classification:
        text = normalize(question.text)
        listing_text = normalize(listing.searchable_text())
        agent_intent = question.raw.get("_agent_intent") if isinstance(question.raw, dict) else {}
        if isinstance(agent_intent, dict):
            fluxo = normalize(agent_intent.get("fluxo"))
            intencao = normalize(agent_intent.get("intencao") or agent_intent.get("intent"))
            if fluxo == "pos_venda" or intencao in {"pos_venda_defeito", "troca_garantia", "reclamacao", "cancelamento"}:
                return Classification(QuestionCategory.POST_SALE, "agent_intent")

        if self._has_prompt_injection(text):
            return Classification(QuestionCategory.UNKNOWN, "prompt_injection", prompt_injection=True)
        if any(term in text for term in ("comprei", "minha compra", "meu pedido", "devolucao", "troca", "chegou quebrado", "veio errado", "nao funcionou", "defeito", "deu ruim", "procon", "procom", "nao presta", "nao vale nada", "nao valem nada")):
            return Classification(QuestionCategory.POST_SALE, "post_sale_keywords")
        if any(term in text or term in listing_text for term in ("medicamento", "remedio", "receita medica", "arma", "municao", "anvisa controlado")):
            return Classification(QuestionCategory.REGULATED_PRODUCT, "regulated_product")
        if _asks_external_contact(text):
            return Classification(QuestionCategory.PROHIBITED_CONTACT, "prohibited_contact")
        if re.search(r"\b(oi|ola|bom dia|boa tarde|boa noite)\b", text) and len(text) <= 25:
            return Classification(QuestionCategory.GREETING, "greeting")
        # Perguntas compostas sobre compatibilidade e entrega precisam passar
        # pelo fluxo tecnico completo. As demais intencoes continuam anexadas
        # pelo orquestrador e devem ser respondidas no mesmo rascunho.
        asks_compatibility = bool(
            is_compatibility_question(text)
            or re.search(r"\b(?:e|eh|seria)\s+(?:do|da|para)\s+(?:motor|modelo|versao|cambio)\b", text)
            or re.search(r"\b(?:motor|modelo|versao|cambio)\b.{0,30}\b(?:correto|certa|certo)\b", text)
        )
        asks_shipping = any(term in text for term in ("frete", "entrega", "cep", "prazo", "envio", "data prevista"))
        if asks_compatibility and asks_shipping:
            return Classification(QuestionCategory.COMPATIBILITY, "multi_intent_compatibility_shipping")
        if any(term in text for term in ("preco", "valor", "desconto", "faz por", "quanto custa", "menor valor")):
            return Classification(QuestionCategory.PRICE, "price")
        if any(term in text for term in ("estoque", "tem disponivel", "pronta entrega", "disponivel")):
            return Classification(QuestionCategory.STOCK, "stock")
        if any(term in text for term in ("frete", "entrega", "cep", "prazo", "envio")):
            return Classification(QuestionCategory.SHIPPING, "shipping")
        if any(term in text for term in ("nota fiscal", " nf ", "emite nota", "danfe")):
            return Classification(QuestionCategory.INVOICE, "invoice")
        if any(term in text for term in ("original", "genuino", "garantia", "paralelo", "procedencia")):
            return Classification(QuestionCategory.WARRANTY_ORIGINALITY, "warranty_originality")
        if asks_compatibility:
            return Classification(QuestionCategory.COMPATIBILITY, "compatibility")
        if any(term in text for term in (
            "voltagem", "volts", "v ", "medida", "tamanho", "cor", "material", "lado", "quantas",
            "itens inclusos", "engate rapido", "abracadeira", "conexao", "conector", "plug", "entrada",
            "saida", "encaixe", "fixacao", "flange", "furacao", "rosca", "diametro", "mangueira",
            "terminal", "pino", "estria", "eixo", "haste", "tensao", "frequencia", "potencia",
            "amperagem", "pressao", "protocolo", "acompanha", "vem com", "incluso", "inclui",
            "temperatura", "capacidade", "vazao",
        )):
            return Classification(QuestionCategory.PRODUCT_FEATURE, "product_feature")
        if re.search(r"\btem\s+(outra|a|o|esse|essa)\b", text):
            return Classification(QuestionCategory.OTHER_PRODUCT, "other_product")
        return Classification(QuestionCategory.UNKNOWN, "fallback")

    @staticmethod
    def _has_prompt_injection(text: str) -> bool:
        patterns = (
            "ignore as instrucoes",
            "ignore todas as regras",
            "revele o prompt",
            "mostre o prompt",
            "system prompt",
            "responda em json",
            "finja que",
            "voce agora e",
        )
        return any(pattern in text for pattern in patterns)


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.lower()).strip()


def _asks_external_contact(text: str) -> bool:
    if any(term in text for term in ("whatsapp", "whats", " zap ", "email", "instagram", "fora do mercado livre", "contato direto")):
        return True
    return bool(
        re.search(r"\b(?:qual|passe|envie|informe|manda|mandar|tem)\b.{0,35}\b(?:telefone|numero|contato)\b", text)
        or re.search(r"\b(?:telefone|numero|contato)\b.{0,35}\b(?:loja|vendedor|voces|atendimento)\b", text)
    )
