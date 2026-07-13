from __future__ import annotations

import asyncio
from pathlib import Path

from backend.services import ia as _ia_facade  # noqa: F401
from backend.services import ia_endpoints, ia_providers


ROOT = Path(__file__).resolve().parents[1]


def test_codex_model_is_preserved_by_configuration_normalizer():
    assert ia_providers._normalizar_ia_modelo_padrao("codex:gpt-5.5") == "codex:gpt-5.5"
    assert ia_providers._normalizar_ia_modelo_padrao("codex:gpt-5.6-sol") == "codex:gpt-5.6-sol"
    assert ia_providers._ia_provedor_por_modelo("codex:gpt-5.5") == "codex"
    assert ia_providers._codex_modelo_nome_curto("codex:gpt-5.5") == "gpt-5.5"


def test_model_catalog_exposes_codex_to_admin(monkeypatch):
    monkeypatch.setattr(ia_endpoints, "_usuario_pode_escolher_modelo_chat", lambda request, client_id: True)
    monkeypatch.setattr(ia_endpoints, "_listar_modelos_gemini_api", lambda: [])
    monkeypatch.setattr(ia_endpoints, "_listar_modelos_vertex_ai", lambda: [])
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
    assert [item["name"] for item in result["codex"]] == [
        "codex:gpt-5.6-sol",
        "codex:gpt-5.6-terra",
        "codex:gpt-5.6-luna",
        "codex:gpt-5.5",
        "codex:gpt-5.4",
        "codex:gpt-5.4-mini",
        "codex:gpt-5.3-codex-spark",
    ]
    assert result["defaults"]["codex"] == "codex:gpt-5.5"


def test_configuration_page_lists_and_accepts_codex_in_both_copies():
    root_html = (ROOT / "configuracoes.html").read_text(encoding="utf-8")
    static_html = (ROOT / "static" / "configuracoes.html").read_text(encoding="utf-8")

    assert root_html == static_html
    assert "{ label: 'Codex', itens: data.codex || [] }" in root_html
    assert "{ name: 'codex:gpt-5.5', display_name: 'Codex GPT-5.5' }" in root_html
    assert "{ name: 'codex:gpt-5.6-sol', display_name: 'Codex GPT-5.6 Sol' }" in root_html
    assert "{ name: 'codex:gpt-5.3-codex-spark', display_name: 'Codex GPT-5.3 Codex Spark' }" in root_html
    assert "!modeloLower.startsWith('codex:')" in root_html


def test_questions_v2_keeps_codex_as_an_allowed_configured_provider():
    source = (ROOT / "backend" / "services" / "perguntas_pos_venda_agent.py").read_text(encoding="utf-8")
    assert "_modelo_eh_vertex_ai(model_req) or _modelo_eh_codex(model_req)" in source
    assert '"codex": _modelo_eh_codex(model_req)' in source
