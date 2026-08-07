"""Extracted legacy AI implementation with static dependencies."""

from __future__ import annotations

import re
import unicodedata

from .runtime import (
    PerguntasPosVendaDomainError,
    IAChatRequest,
    ML_POS_VENDA_DEFAULT_MAX_CHARS,
    ML_POS_VENDA_LIMITE_SEGURO,
    Optional,
    PerguntasIARespostaIndisponivel,
    _ia_modelo_perguntas_configurado,
    _normalizar_ia_modelo_padrao,
    _perguntas_ia_assinatura_loja,
    _perguntas_ia_fluxo_pos_venda,
    _perguntas_ia_intencao_agent,
    _perguntas_ia_limpar_resposta,
    _perguntas_ia_resposta_fallback_invalida,
    resolve_runtime_adapter,
    time,
)


def _ml_pos_venda_texto_norm(value) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text)
from .context import (
    _ia_agent_perguntas_log_perf,
)
from .inputs import (
    _perguntas_codex_compact_json,
)
from .tools import (
    _ia_agent_perguntas_chamar_modelo,
    _ia_agent_perguntas_montar_prompt,
    _ia_agent_perguntas_preparar_tools,
)
from .validation import (
    _ia_agent_perguntas_violacoes_resposta,
)

def _ia_agent_perguntas_gerar_resposta_legado_desativado(
    client_id: str, agent_input: dict,
) -> tuple[str, str, list[dict]]:
    del client_id, agent_input
    raise PerguntasIARespostaIndisponivel(
        "Fluxo local legado de perguntas removido. Use a nova IA Vertex Gemini V2."
    )

def _ml_pos_venda_contexto_prompt(contexto: Optional[dict]) -> str:
    if not isinstance(contexto, dict):
        return ""
    prompt_context = {
        "pipeline": contexto.get("etapas_pipeline") or [],
        "loja": contexto.get("loja") or "",
        "pedido": contexto.get("pedido") or {},
        "mensagem": contexto.get("mensagem") or {},
        "anuncios": contexto.get("anuncios") or [],
        "envio": contexto.get("envio") or {},
        "pagamento": contexto.get("pagamento") or {},
        "nota_fiscal": contexto.get("nota_fiscal") or {},
        "reclamacao_mediacao": contexto.get("reclamacao_mediacao") or {},
        "classificacao": contexto.get("classificacao") or {},
        "decisao_automacao": contexto.get("decisao_automacao") or {},
        "regras_oficiais": contexto.get("regras_oficiais") or {},
        "perguntas_anteriores_anuncio": contexto.get("perguntas_anteriores_anuncio") or [],
        "evidence_envelope": contexto.get("evidence_envelope") or {},
    }
    return _perguntas_codex_compact_json(prompt_context, 6500)

def _ml_pos_venda_validar_resposta(resposta: str, contexto: dict, limite: int | None = None) -> dict:
    limite_num = int(limite or contexto.get("max_chars") or ML_POS_VENDA_DEFAULT_MAX_CHARS)
    limite_num = max(1, min(limite_num, ML_POS_VENDA_DEFAULT_MAX_CHARS))
    limite_seguro = min(limite_num, ML_POS_VENDA_LIMITE_SEGURO)
    texto = str(resposta or "").strip()
    norm = _ml_pos_venda_texto_norm(texto)
    issues = []
    if not texto:
        issues.append("resposta_vazia")
    if len(texto) > limite_seguro:
        issues.append("resposta_acima_do_limite")
    if any(term in norm for term in ("jk sistema", "sou assistente", "sou uma ia", "gemini", "vertex")):
        issues.append("identidade_incorreta")
    if any(term in norm for term in ("whatsapp", "telefone", "email", "e-mail", "fora do mercado livre")):
        issues.append("contato_externo")
    assinatura_fn = resolve_runtime_adapter("state", "store_signature", _perguntas_ia_assinatura_loja)
    assinatura = _ml_pos_venda_texto_norm(assinatura_fn(str(contexto.get("loja") or "")))
    assinatura_curta = _ml_pos_venda_texto_norm(assinatura_fn(""))
    if assinatura and assinatura not in norm and assinatura_curta not in norm:
        issues.append("sem_assinatura_loja")
    regras = contexto.get("regras_oficiais") if isinstance(contexto.get("regras_oficiais"), dict) else {}
    decisao = contexto.get("decisao_automacao") if isinstance(contexto.get("decisao_automacao"), dict) else {}
    requires_human = bool(regras.get("precisa_consultar") or not decisao.get("pode_responder_automaticamente"))
    if requires_human:
        issues.append("requer_revisao_humana")
    return {
        "ok": not [issue for issue in issues if issue not in {"requer_revisao_humana"}],
        "requires_human_review": requires_human or bool([issue for issue in issues if issue != "requer_revisao_humana"]),
        "issues": list(dict.fromkeys(issues)),
    }
