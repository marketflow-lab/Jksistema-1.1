from __future__ import annotations

import asyncio
from pathlib import Path

from backend.services import ia as _ia_facade  # noqa: F401
from backend.services import configuracoes, gpt_model_catalog, ia_endpoints, ia_providers


ROOT = Path(__file__).resolve().parents[1]


def test_codex_model_normalizer_migrates_retired_default_and_preserves_selection():
    assert ia_providers._normalizar_ia_modelo_padrao("codex:gpt-5.5") == "codex:gpt-6.1-sol"
    assert ia_providers._normalizar_ia_modelo_padrao("codex:gpt-5.6-sol") == "codex:gpt-5.6-sol"
    assert ia_providers._ia_provedor_por_modelo("codex:gpt-5.5") == "codex"
    assert ia_providers._codex_modelo_nome_curto("codex:gpt-5.5") == "gpt-6.1-sol"
    assert ia_providers._normalizar_ia_modelo_padrao("codex:gpt-6-astra") == "codex:gpt-6-astra"
    assert ia_providers._normalizar_ia_modelo_padrao("gpt-5.5") == "gpt-5.5"


def test_global_ai_defaults_match_installed_codex_routing():
    defaults = configuracoes.CONFIG_GLOBAIS_DEFAULT

    for purpose in ("padrao", "perguntas", "pos_venda", "chat", "favoritos"):
        assert defaults[f"ia_modelo_{purpose}"] == "codex:gpt-6.1-sol"
        assert defaults[f"ia_raciocinio_{purpose}"] == "medium"
    assert defaults["ia_vertex_ativa"] is False


def test_model_catalog_exposes_codex_to_admin(monkeypatch):
    monkeypatch.setenv("JK_GPT_CATALOG_DISCOVERY_ENABLED", "0")
    monkeypatch.setattr(ia_endpoints, "_usuario_pode_escolher_modelo_chat", lambda request, client_id: True)
    monkeypatch.setattr(ia_endpoints, "_listar_modelos_gemini_api", lambda: [])
    monkeypatch.setattr(ia_endpoints, "_listar_modelos_vertex_ai", lambda: [])
    monkeypatch.setattr(ia_endpoints, "_listar_modelos_openai_api", lambda: [])
    monkeypatch.setattr(ia_endpoints, "_ia_modelo_padrao_configurado", lambda: "codex:gpt-5.5")
    monkeypatch.setattr(ia_endpoints, "_ia_modelo_perguntas_configurado", lambda: "codex:gpt-5.5")
    monkeypatch.setattr(ia_endpoints, "_ia_modelo_pos_venda_configurado", lambda: "codex:gpt-5.5")
    monkeypatch.setattr(ia_endpoints, "_ia_modelo_chat_configurado", lambda: "codex:gpt-5.5")
    monkeypatch.setattr(ia_endpoints, "_ia_modelo_favoritos_configurado", lambda: "codex:gpt-5.5")
    monkeypatch.setattr(ia_endpoints, "_ia_favoritos_usar_imagem_configurado", lambda: False)
    monkeypatch.setattr(ia_endpoints, "_ia_provedor_ativo", lambda provider: True)
    monkeypatch.setattr(ia_endpoints, "_vertex_ai_modelo_padrao", lambda: "gemini-2.5-flash")
    monkeypatch.setattr(ia_endpoints, "_vertex_ai_project_id_configurado", lambda: "")
    monkeypatch.setattr(ia_endpoints, "_vertex_ai_location", lambda: "global")
    monkeypatch.setattr(ia_endpoints, "_vertex_ai_service_account_email", lambda: "")
    monkeypatch.setattr(ia_endpoints, "_vertex_ai_agent_api_key", lambda: "")

    result = asyncio.run(ia_endpoints.ia_listar_modelos(object(), "tenant-test"))

    assert result["codex"] == ia_providers._listar_modelos_codex_configuraveis()
    assert {"codex:gpt-6.1-sol", "codex:gpt-6-astra", "codex:gpt-5.6-luna"} <= {item["name"] for item in result["codex"]}
    assert all("reasoning_efforts" in item and "default_reasoning_effort" in item for item in result["codex"])
    assert result["reasoning_efforts"] == list(gpt_model_catalog.REASONING_EFFORTS)
    assert result["defaults"]["codex"] == "codex:gpt-6.1-sol"


def test_configuration_page_lists_and_accepts_codex_in_both_copies():
    root_html = (ROOT / "configuracoes.html").read_text(encoding="utf-8")
    static_html = (ROOT / "static" / "configuracoes.html").read_text(encoding="utf-8")

    assert root_html == static_html
    assert "{ label: 'Codex', itens: data.codex || [] }" in root_html
    assert "ia-model-catalog.js" in root_html
    assert "gpt-6.1-sol" in (ROOT / "static" / "ia-model-catalog.js").read_text(encoding="utf-8")
    assert "!modeloLower.startsWith('codex:')" in root_html


def test_questions_v2_uses_codex_and_preserves_configured_provider_as_optional_fallback():
    execution_source = (
        ROOT / "backend" / "modules" / "perguntas_pos_venda" / "ai" / "execution.py"
    ).read_text(encoding="utf-8")
    clients_source = (
        ROOT / "backend" / "modules" / "perguntas_pos_venda" / "ai" / "clients.py"
    ).read_text(encoding="utf-8")

    for model in (
        "codex:gpt-6.1-sol",
        "vertex:gemini-2.5-flash",
        "gemini:gemini-2.5-flash",
        "deepseek-chat",
        "gpt-5.4-nano",
    ):
        assert ia_providers._normalizar_ia_modelo_padrao(model) == model
    assert "selection = _perguntas_codex_provider_selection(" in execution_source
    assert 'gpt_model_catalog.DEFAULT_CODEX_MODEL' in execution_source
    assert '"response_provider_policy": selection.get("policy")' in execution_source
    assert '"configured_fallback": selection.get("configured_fallback")' in execution_source
    assert '"fallback_used": bool(selection.get("fallback_used"))' in execution_source
    assert '"codex": _modelo_eh_codex(model_req)' in execution_source
    assert '"reasoning_effort": reasoning' in execution_source
    assert '"_codex_reasoning_effort": stage_reasoning_effort' in clients_source
    assert '_PUBLIC_TECHNICAL_RESEARCH_MODEL = f"codex:{gpt_model_catalog.DEFAULT_CODEX_MODEL}"' in clients_source
    assert '_PUBLIC_TECHNICAL_RESEARCH_REASONING_EFFORT = "high"' in clients_source
