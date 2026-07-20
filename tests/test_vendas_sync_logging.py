from __future__ import annotations

import io
import sys

from backend.modules.vendas import progress as vendas_progress
from backend.modules.vendas.state import SYNC_LOGS, SYNC_THREAD_CONTEXT


def test_sync_log_survives_cp1252_stdout_and_keeps_unicode_message(monkeypatch) -> None:
    client_id = "tenant-test-cp1252-stdout"
    mensagem = "[SYNC] Etapa concluida: ✅ 3 vendas registradas"
    stdout_cp1252 = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    snapshot = SYNC_LOGS.get(client_id)

    monkeypatch.delattr(SYNC_THREAD_CONTEXT, "job_id", raising=False)
    monkeypatch.delattr(SYNC_THREAD_CONTEXT, "loja", raising=False)
    monkeypatch.setattr(sys, "stdout", stdout_cp1252)
    SYNC_LOGS.pop(client_id, None)

    try:
        vendas_progress._sync_log(client_id, mensagem)

        assert SYNC_LOGS[client_id] == [mensagem]
    finally:
        SYNC_LOGS.pop(client_id, None)
        if snapshot is not None:
            SYNC_LOGS[client_id] = snapshot
