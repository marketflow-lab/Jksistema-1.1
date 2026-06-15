from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

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

        if self._has_prompt_injection(text):
            return Classification(QuestionCategory.UNKNOWN, "prompt_injection", prompt_injection=True)
        if any(term in text for term in ("comprei", "minha compra", "meu pedido", "devolucao", "devolucao", "troca", "chegou quebrado", "veio errado", "nao funcionou", "defeito")):
            return Classification(QuestionCategory.POST_SALE, "post_sale_keywords")
        if any(term in text or term in listing_text for term in ("medicamento", "remedio", "receita medica", "arma", "municao", "anvisa controlado")):
            return Classification(QuestionCategory.REGULATED_PRODUCT, "regulated_product")
        if any(term in text for term in ("whatsapp", "whats", "zap", "telefone", "email", "instagram", "fora do mercado livre", "contato direto")):
            return Classification(QuestionCategory.PROHIBITED_CONTACT, "prohibited_contact")
        if re.search(r"\b(oi|ola|bom dia|boa tarde|boa noite)\b", text) and len(text) <= 25:
            return Classification(QuestionCategory.GREETING, "greeting")
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
        if any(term in text for term in ("serve", "servi", "compativel", "aplica", "encaixa", "funciona no", "cabe no")):
            return Classification(QuestionCategory.COMPATIBILITY, "compatibility")
        if any(term in text for term in ("voltagem", "volts", "v ", "medida", "tamanho", "cor", "material", "lado", "quantas", "itens inclusos")):
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
