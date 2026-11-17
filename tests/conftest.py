"""Keep model metadata and account discovery isolated between unit tests."""

import pytest

from backend.services import gpt_model_catalog


@pytest.fixture(autouse=True)
def isolated_gpt_catalog(monkeypatch):
    monkeypatch.setenv("JK_GPT_CATALOG_DISCOVERY_ENABLED", "0")
    monkeypatch.setattr(gpt_model_catalog, "_CACHE", [])
    monkeypatch.setattr(gpt_model_catalog, "_CACHE_AT", 0.0)
    monkeypatch.setattr(gpt_model_catalog, "_ATTEMPT_AT", 0.0)
    monkeypatch.setattr(gpt_model_catalog, "_INFLIGHT", None)
