from __future__ import annotations

import json
import threading

from backend.routers.perguntas_pos_venda import create_perguntas_pos_venda_router
from backend.modules.perguntas_pos_venda.endpoints import question_automation as endpoints
from backend.modules.perguntas_pos_venda.endpoints import store_config
from backend.modules.perguntas_pos_venda.endpoints.contracts import _PERGUNTAS_AUTOMACAO_PAGE_LIMIT
from backend.services import perguntas_pos_venda_automacao as automacao
from backend.services import perguntas_pos_venda_endpoints as public_endpoints


def _config(ativa: bool = True, intervalo: int = 5) -> dict:
    return {
        "responder_automaticamente": ativa,
        "intervalo_minutos": intervalo,
    }


def test_status_isola_tenant_e_entrega_payload_seguro(monkeypatch):
    tenant_a = "tenant-a"
    tenant_b = "tenant-b"
    agora = 1_800_000_000.0
    chave_a = automacao._perguntas_automacao_bg_key(tenant_a, "Loja A", "perguntas")
    chave_b = automacao._perguntas_automacao_bg_key(tenant_b, "Loja B", "perguntas")

    monkeypatch.setattr(automacao, "PERGUNTAS_AUTOMACAO_BG_LOCK", threading.Lock(), raising=False)
    monkeypatch.setattr(automacao, "PERGUNTAS_AUTOMACAO_BG_THREAD_STARTED", True, raising=False)
    monkeypatch.setattr(automacao, "PERGUNTAS_AUTOMACAO_BG_RUNNING", set(), raising=False)
    monkeypatch.setattr(automacao, "PERGUNTAS_AUTOMACAO_BG_NEXT_CHECKS", {
        chave_a: agora + 300,
        chave_b: agora + 600,
    }, raising=False)
    monkeypatch.setattr(automacao, "PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS", {
        chave_a: {
            "updated_at": "2026-07-18T14:10:09",
            "success": True,
            "erro": "access_token=segredo https://api.exemplo.test/path?authorization=segredo",
            "enviadas": 1,
            "novas_pendentes": 2,
            "erros": 0,
        },
        chave_b: {
            "updated_at": "2026-07-18T14:11:09",
            "success": False,
            "erro": "nao deve vazar",
            "enviadas": 99,
            "novas_pendentes": 99,
            "erros": 99,
        },
    }, raising=False)

    monkeypatch.setattr(store_config, "carregar_lojas", lambda client_id: (
        [{"nome": "Loja A"}] if client_id == tenant_a else [{"nome": "Loja B"}]
    ), raising=False)
    monkeypatch.setattr(store_config, "_perguntas_loja_configs_carregar", lambda client_id: (
        {"Loja A": _config()} if client_id == tenant_a else {"Loja B": _config()}
    ), raising=False)
    monkeypatch.setattr(store_config, "_perguntas_loja_config_obter", lambda configs, loja: configs.get(loja), raising=False)
    monkeypatch.setattr(store_config, "_perguntas_loja_config_normalizar", lambda config: config or _config(False, 10), raising=False)
    monkeypatch.setattr(store_config, "_integracoes_nome_normalizado", lambda valor: str(valor or "").strip().casefold(), raising=False)
    monkeypatch.setattr(store_config, "_corrigir_texto_mojibake", lambda valor: valor, raising=False)

    payload = store_config.ml_perguntas_automacao_status(client_id=tenant_a)

    assert payload["success"] is True
    assert payload["worker_iniciado"] is True
    assert payload["total"] == 1
    assert payload["lojas"] == [{
        "loja": "Loja A",
        "automacao_ativa": True,
        "intervalo_minutos": 5,
        "executando": False,
        "ultima_checagem": "2026-07-18T14:10:09",
        "proxima_checagem": automacao._perguntas_automacao_bg_timestamp_iso(agora + 300),
        "sucesso": True,
        "erro": "access_token=[redacted] https://api.exemplo.test/path",
            "contagens": {"enviadas": 1, "novas_pendentes": 2, "erros": 0, "deferred": 0},
            "change_token": None,
            "question_ids": [],
            "new_question_ids": [],
        "new_questions_count": 0,
        "question_snapshot_complete": False,
        "queue_saturated": False,
        "queue_backpressure": {},
    }]
    serializado = json.dumps(payload, ensure_ascii=False)
    assert tenant_b not in serializado
    assert "Loja B" not in serializado
    assert "segredo" not in serializado
    assert "nao deve vazar" not in serializado


def test_status_filtra_loja_sem_revelar_conta_de_outro_tenant(monkeypatch):
    monkeypatch.setattr(automacao, "PERGUNTAS_AUTOMACAO_BG_LOCK", threading.Lock(), raising=False)
    monkeypatch.setattr(automacao, "PERGUNTAS_AUTOMACAO_BG_THREAD_STARTED", True, raising=False)
    monkeypatch.setattr(store_config, "carregar_lojas", lambda _client_id: [{"nome": "Loja A"}], raising=False)
    monkeypatch.setattr(store_config, "_perguntas_loja_configs_carregar", lambda _client_id: {"Loja A": _config()}, raising=False)
    monkeypatch.setattr(store_config, "_perguntas_loja_config_obter", lambda configs, loja: configs.get(loja), raising=False)
    monkeypatch.setattr(store_config, "_perguntas_loja_config_normalizar", lambda config: config or _config(False, 10), raising=False)
    monkeypatch.setattr(store_config, "_integracoes_nome_normalizado", lambda valor: str(valor or "").strip().casefold(), raising=False)
    monkeypatch.setattr(store_config, "_corrigir_texto_mojibake", lambda valor: valor, raising=False)

    payload = store_config.ml_perguntas_automacao_status(loja="Loja B", client_id="tenant-a")

    assert payload["success"] is True
    assert payload["lojas"] == []
    assert payload["total"] == 0


def test_status_sanitiza_formatos_comuns_de_credenciais():
    erro = (
        '{"access_token":"segredo-json"}; '
        'refresh token: segredo-espaco; '
        'Authorization: Basic c2VncmVkbw==; '
        'Bearer segredo-bearer'
    )

    publico = automacao._perguntas_automacao_bg_erro_publico(erro)

    assert "segredo-json" not in publico
    assert "segredo-espaco" not in publico
    assert "c2VncmVkbw==" not in publico
    assert "segredo-bearer" not in publico
    assert publico.count("[redacted]") >= 4


def test_rota_get_e_composicao_backend_api_reconhecem_endpoint():
    router = create_perguntas_pos_venda_router()
    rota = next(
        route
        for route in router.routes
        if route.path == "/api/mercadolivre/perguntas/automacao/status"
    )

    assert rota.methods == {"GET"}
    assert rota.endpoint is public_endpoints.ml_perguntas_automacao_status

    import backend_api

    assert backend_api.ml_perguntas_automacao_status is public_endpoints.ml_perguntas_automacao_status
    assert any(
        route.path == "/api/mercadolivre/perguntas/automacao/status"
        and route.endpoint is public_endpoints.ml_perguntas_automacao_status
        for route in backend_api.app.routes
    )


def _snapshot(loja: str, question_ids: list[str], completo: bool = True) -> dict:
    return {
        "question_snapshots": [{
            "loja": loja,
            "question_ids": question_ids,
            "question_snapshot_complete": completo,
        }],
        "novas_pendentes": [],
        "enviadas": [],
        "erros": [],
    }


def test_change_token_faz_baseline_e_detecta_id_novo_sem_depender_de_pendentes(monkeypatch):
    key = automacao._perguntas_automacao_bg_key("tenant-a", "Loja A", "perguntas")
    monkeypatch.setattr(automacao, "PERGUNTAS_AUTOMACAO_BG_LOCK", threading.Lock(), raising=False)
    monkeypatch.setattr(automacao, "PERGUNTAS_AUTOMACAO_BG_RUNNING", {key}, raising=False)
    monkeypatch.setattr(automacao, "PERGUNTAS_AUTOMACAO_BG_NEXT_CHECKS", {}, raising=False)
    monkeypatch.setattr(automacao, "PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS", {}, raising=False)

    automacao._perguntas_automacao_bg_finalizar(key, 60, resultado=_snapshot("Loja A", ["2", "1"]))
    baseline = automacao._perguntas_automacao_bg_status("tenant-a", "Loja A")

    assert baseline["change_token"] == automacao._perguntas_automacao_bg_change_token(key, ["1", "2"])
    assert baseline["question_ids"] == ["1", "2"]
    assert baseline["new_question_ids"] == []
    assert baseline["new_questions_count"] == 0
    assert baseline["question_snapshot_complete"] is True

    automacao._perguntas_automacao_bg_finalizar(key, 60, resultado=_snapshot("Loja A", ["1", "2"]))
    sem_mudanca = automacao._perguntas_automacao_bg_status("tenant-a", "Loja A")
    assert sem_mudanca["change_token"] == baseline["change_token"]
    assert sem_mudanca["new_question_ids"] == []

    automacao._perguntas_automacao_bg_finalizar(key, 60, resultado=_snapshot("Loja A", ["3", "2", "1"]))
    com_nova = automacao._perguntas_automacao_bg_status("tenant-a", "Loja A")
    assert com_nova["change_token"] != baseline["change_token"]
    assert com_nova["question_ids"] == ["1", "2", "3"]
    assert com_nova["new_question_ids"] == ["3"]
    assert com_nova["new_questions_count"] == 1
    assert com_nova["contagens"]["novas_pendentes"] == 0


def test_snapshot_falho_preserva_token_e_isolamento_por_tenant_loja(monkeypatch):
    key_a = automacao._perguntas_automacao_bg_key("tenant-a", "Loja A", "perguntas")
    key_b = automacao._perguntas_automacao_bg_key("tenant-b", "Loja A", "perguntas")
    key_c = automacao._perguntas_automacao_bg_key("tenant-a", "Loja B", "perguntas")
    monkeypatch.setattr(automacao, "PERGUNTAS_AUTOMACAO_BG_LOCK", threading.Lock(), raising=False)
    monkeypatch.setattr(automacao, "PERGUNTAS_AUTOMACAO_BG_RUNNING", set(), raising=False)
    monkeypatch.setattr(automacao, "PERGUNTAS_AUTOMACAO_BG_NEXT_CHECKS", {}, raising=False)
    monkeypatch.setattr(automacao, "PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS", {}, raising=False)

    automacao._perguntas_automacao_bg_finalizar(key_a, 60, resultado=_snapshot("Loja A", ["10"]))
    automacao._perguntas_automacao_bg_finalizar(key_b, 60, resultado=_snapshot("Loja A", ["20"]))
    automacao._perguntas_automacao_bg_finalizar(key_c, 60, resultado=_snapshot("Loja B", ["30"]))
    token_a = automacao._perguntas_automacao_bg_status("tenant-a", "Loja A")["change_token"]
    token_b = automacao._perguntas_automacao_bg_status("tenant-b", "Loja A")["change_token"]
    token_c = automacao._perguntas_automacao_bg_status("tenant-a", "Loja B")["change_token"]
    assert token_a != token_b
    assert token_a != token_c

    automacao._perguntas_automacao_bg_finalizar(key_a, 60, erro="timeout access_token=segredo")
    falho = automacao._perguntas_automacao_bg_status("tenant-a", "Loja A")
    intacto = automacao._perguntas_automacao_bg_status("tenant-b", "Loja A")
    intacto_outra_loja = automacao._perguntas_automacao_bg_status("tenant-a", "Loja B")
    assert falho["change_token"] == token_a
    assert falho["question_ids"] == ["10"]
    assert falho["new_question_ids"] == []
    assert falho["question_snapshot_complete"] is False
    assert "segredo" not in json.dumps(falho)
    assert intacto["change_token"] == token_b
    assert intacto["question_snapshot_complete"] is True
    assert intacto_outra_loja["change_token"] == token_c
    assert intacto_outra_loja["question_snapshot_complete"] is True

    automacao._perguntas_automacao_bg_finalizar(key_a, 60, resultado=_snapshot("Loja A", ["10", "11"]))
    recuperado = automacao._perguntas_automacao_bg_status("tenant-a", "Loja A")
    assert recuperado["new_question_ids"] == ["11"]


def test_poll_publica_snapshot_antes_de_falha_da_ia(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "questions": [
                    {"id": 101, "status": "UNANSWERED", "item_id": "MLB1"},
                    {"id": 102, "status": "UNANSWERED", "hold": True},
                    {"id": 103, "status": "ANSWERED"},
                ]
            }

    monkeypatch.setattr(endpoints, "_perguntas_ia_state_carregar", lambda _client_id: {}, raising=False)
    monkeypatch.setattr(endpoints, "_perguntas_ia_aprovacoes_carregar", lambda _client_id: [], raising=False)
    monkeypatch.setattr(endpoints, "_perguntas_loja_configs_carregar", lambda _client_id: {"Loja A": _config()}, raising=False)
    monkeypatch.setattr(endpoints, "carregar_lojas", lambda _client_id: [{
        "nome": "Loja A",
        "integracoes": {"mercadolivre": {}},
    }], raising=False)
    monkeypatch.setattr(endpoints, "_perguntas_loja_config_normalizar", lambda config: config, raising=False)
    monkeypatch.setattr(endpoints, "_ml_oauth_status", lambda _cfg: {"conectado": True}, raising=False)
    monkeypatch.setattr(endpoints, "_obter_cfg_ml", lambda _client_id, _loja: {"user_id": "seller"}, raising=False)
    monkeypatch.setattr(endpoints, "_ml_api_request", lambda *_args, **_kwargs: (Response(), {}), raising=False)
    monkeypatch.setattr(
        endpoints,
        "_ml_buscar_itens_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("falha depois da busca de perguntas")),
        raising=False,
    )
    monkeypatch.setattr(endpoints, "_corrigir_texto_mojibake", lambda valor: valor, raising=False)

    payload = endpoints.ml_perguntas_automacao_poll(loja="Loja A", client_id="tenant-a")

    assert payload["novas_pendentes"] == []
    assert payload["question_snapshots"] == [{
        "loja": "Loja A",
        "question_ids": ["101"],
        "question_snapshot_complete": True,
    }]
    assert payload["erros"]


def _preparar_poll_paginado(monkeypatch, request_fn) -> None:
    monkeypatch.setattr(endpoints, "_perguntas_ia_state_carregar", lambda _client_id: {}, raising=False)
    monkeypatch.setattr(endpoints, "_perguntas_ia_aprovacoes_carregar", lambda _client_id: [], raising=False)
    monkeypatch.setattr(endpoints, "_perguntas_loja_configs_carregar", lambda _client_id: {"Loja A": _config()}, raising=False)
    monkeypatch.setattr(endpoints, "carregar_lojas", lambda _client_id: [{
        "nome": "Loja A",
        "integracoes": {"mercadolivre": {}},
    }], raising=False)
    monkeypatch.setattr(endpoints, "_perguntas_loja_config_normalizar", lambda config: config, raising=False)
    monkeypatch.setattr(endpoints, "_ml_oauth_status", lambda _cfg: {"conectado": True}, raising=False)
    monkeypatch.setattr(endpoints, "_obter_cfg_ml", lambda _client_id, _loja: {"user_id": "seller"}, raising=False)
    monkeypatch.setattr(endpoints, "_ml_api_request", request_fn, raising=False)
    monkeypatch.setattr(endpoints, "_ml_buscar_itens_batch", lambda *_args, **_kwargs: ([], {}), raising=False)
    monkeypatch.setattr(endpoints, "_ml_perguntas_completar_skus_itens", lambda *_args, **_kwargs: [], raising=False)
    monkeypatch.setattr(endpoints, "_ml_perguntas_buscar_usuarios", lambda *_args, **_kwargs: ({}, {}), raising=False)
    monkeypatch.setattr(endpoints, "_ml_perguntas_normalizar", lambda pergunta, *_args: pergunta, raising=False)
    monkeypatch.setattr(
        endpoints,
        "_ml_perguntas_anexar_historico_comprador",
        lambda _client_id, _loja, cfg, _seller, perguntas: (perguntas, cfg),
        raising=False,
    )
    monkeypatch.setattr(endpoints, "_perguntas_ia_ja_processada", lambda *_args: True, raising=False)
    monkeypatch.setattr(endpoints, "_corrigir_texto_mojibake", lambda valor: valor, raising=False)


def test_poll_pagina_mais_de_500_perguntas_antes_de_declarar_snapshot_completo(monkeypatch):
    total = 505
    offsets = []

    class Response:
        status_code = 200

        def __init__(self, offset: int, limit: int):
            self.offset = offset
            self.limit = limit

        def json(self):
            fim = min(total, self.offset + self.limit)
            return {
                "total": total,
                "limit": self.limit,
                "offset": self.offset,
                "questions": [
                    {"id": index + 1, "status": "UNANSWERED", "item_id": f"MLB{index + 1}"}
                    for index in range(self.offset, fim)
                ],
            }

    def request_fn(*_args, **kwargs):
        params = kwargs["params"]
        offsets.append(params["offset"])
        return Response(params["offset"], params["limit"]), {"user_id": "seller"}

    _preparar_poll_paginado(monkeypatch, request_fn)

    payload = endpoints.ml_perguntas_automacao_poll(loja="Loja A", client_id="tenant-a")

    assert offsets == list(range(0, total, _PERGUNTAS_AUTOMACAO_PAGE_LIMIT))
    assert payload["erros"] == []
    assert payload["question_snapshots"][0]["question_snapshot_complete"] is True
    assert len(payload["question_snapshots"][0]["question_ids"]) == total
    assert set(payload["question_snapshots"][0]["question_ids"]) == {str(index) for index in range(1, total + 1)}


def test_poll_falha_fechado_quando_pagina_intermediaria_nao_e_entregue(monkeypatch):
    offsets = []

    class Response:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.text = ""

        def json(self):
            return self._payload

    def request_fn(*_args, **kwargs):
        offset = kwargs["params"]["offset"]
        offsets.append(offset)
        if offset:
            return Response(503, {"message": "temporariamente indisponivel"}), {"user_id": "seller"}
        return Response(200, {
            "total": 100,
            "limit": 50,
            "offset": 0,
            "questions": [
                {"id": index + 1, "status": "UNANSWERED"}
                for index in range(50)
            ],
        }), {"user_id": "seller"}

    _preparar_poll_paginado(monkeypatch, request_fn)

    payload = endpoints.ml_perguntas_automacao_poll(loja="Loja A", client_id="tenant-a")

    assert offsets == [0, 50]
    assert payload["erros"]
    assert payload["question_snapshots"] == [{
        "loja": "Loja A",
        "question_ids": [],
        "question_snapshot_complete": False,
    }]


def test_poll_falha_fechado_se_total_mudar_durante_paginacao(monkeypatch):
    class Response:
        status_code = 200

        def __init__(self, offset: int):
            self.offset = offset

        def json(self):
            return {
                "total": 100 if self.offset == 0 else 101,
                "limit": 50,
                "offset": self.offset,
                "questions": [
                    {"id": self.offset + index + 1, "status": "UNANSWERED"}
                    for index in range(50)
                ],
            }

    def request_fn(*_args, **kwargs):
        return Response(kwargs["params"]["offset"]), {"user_id": "seller"}

    _preparar_poll_paginado(monkeypatch, request_fn)

    payload = endpoints.ml_perguntas_automacao_poll(loja="Loja A", client_id="tenant-a")

    assert payload["erros"]
    assert payload["question_snapshots"][0]["question_snapshot_complete"] is False


def test_poll_encontra_candidatas_de_pagina_antiga_sem_aguardar_job(monkeypatch):
    total = 75
    offsets = []
    item_batches = []
    jobs = []

    class Response:
        status_code = 200

        def __init__(self, offset: int, limit: int):
            self.offset = offset
            self.limit = limit

        def json(self):
            fim = min(total, self.offset + self.limit)
            return {
                "total": total,
                "limit": self.limit,
                "offset": self.offset,
                "questions": [
                    {"id": index + 1, "status": "UNANSWERED", "item_id": f"MLB{index + 1}"}
                    for index in range(self.offset, fim)
                ],
            }

    def request_fn(*_args, **kwargs):
        params = kwargs["params"]
        offsets.append(params["offset"])
        return Response(params["offset"], params["limit"]), {"user_id": "seller"}

    def buscar_itens(_client_id, _loja, cfg, item_ids):
        item_batches.append(list(item_ids))
        return [{"id": item_id} for item_id in item_ids], cfg

    def create_job(**kwargs):
        jobs.append(kwargs["subject_key"])
        return {"job_id": f"job-{kwargs['subject_key']}", "status": "queued"}

    _preparar_poll_paginado(monkeypatch, request_fn)
    monkeypatch.setattr(
        endpoints,
        "_perguntas_ia_ja_processada",
        lambda _state, _loja, question_id: int(question_id) <= 50,
        raising=False,
    )
    monkeypatch.setattr(endpoints, "_ml_buscar_itens_batch", buscar_itens, raising=False)
    monkeypatch.setattr(endpoints, "_ml_perguntas_completar_skus_itens", lambda _client, _loja, _cfg, itens: itens, raising=False)
    monkeypatch.setattr(endpoints.perguntas_pos_venda_codex, "enabled", lambda: True, raising=False)
    monkeypatch.setattr(
        endpoints,
        "_customer_reply_late_reconciliation_candidate",
        lambda **_kwargs: (None, False),
        raising=False,
    )
    monkeypatch.setattr(
        endpoints,
        "_customer_reply_automation_terminal_blocker",
        lambda **_kwargs: "",
        raising=False,
    )
    monkeypatch.setattr(
        endpoints.perguntas_pos_venda_codex,
        "automation_queue_admission",
        lambda *_args: {"allowed": True, "queue_saturated": False},
        raising=False,
    )
    monkeypatch.setattr(endpoints.perguntas_pos_venda_codex, "create_job", create_job, raising=False)
    monkeypatch.setattr(
        endpoints.perguntas_pos_venda_codex,
        "wait_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("poll automatico nao pode aguardar job")),
        raising=False,
    )

    payload = endpoints.ml_perguntas_automacao_poll(loja="Loja A", max_per_store=3, client_id="tenant-a")

    assert offsets == [0, 50]
    assert jobs == ["51", "52", "53"]
    assert item_batches == [[f"MLB{index}" for index in range(51, 76)]]
    assert payload["question_snapshots"][0]["question_snapshot_complete"] is True
    assert payload["erros"] == []


def test_poll_does_not_recreate_current_terminal_review_job(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "total": 1,
                "limit": 50,
                "offset": 0,
                "questions": [{"id": 101, "status": "UNANSWERED", "item_id": "MLB1"}],
            }

    def request_fn(*_args, **_kwargs):
        return Response(), {"user_id": "seller"}

    _preparar_poll_paginado(monkeypatch, request_fn)
    monkeypatch.setattr(endpoints, "_perguntas_ia_ja_processada", lambda *_args: False, raising=False)
    monkeypatch.setattr(
        endpoints,
        "_ml_buscar_itens_batch",
        lambda _client, _loja, cfg, _ids: ([{"id": "MLB1"}], cfg),
        raising=False,
    )
    monkeypatch.setattr(endpoints.perguntas_pos_venda_codex, "enabled", lambda: True, raising=False)
    monkeypatch.setattr(
        endpoints,
        "_customer_reply_late_reconciliation_candidate",
        lambda **_kwargs: (None, False),
        raising=False,
    )
    monkeypatch.setattr(
        endpoints,
        "_customer_reply_automation_terminal_blocker",
        lambda **_kwargs: "terminal_review_required",
        raising=False,
    )
    monkeypatch.setattr(
        endpoints.perguntas_pos_venda_codex,
        "create_job",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("job terminal nao pode ser recriado")),
        raising=False,
    )
    monkeypatch.setattr(
        endpoints.perguntas_pos_venda_codex,
        "automation_queue_admission",
        lambda *_args: (_ for _ in ()).throw(AssertionError("nao deve consultar backpressure")),
        raising=False,
    )

    payload = endpoints.ml_perguntas_automacao_poll(loja="Loja A", client_id="tenant-a")

    assert payload["novas_pendentes"] == []
    assert payload["erros"] == []
    assert payload["deferred"] == [{
        "loja": "Loja A",
        "question_id": "101",
        "reason": "terminal_review_required",
    }]
    assert payload["queue_saturated"] is False
    assert payload["queue_backpressure"]["queue_saturated"] is False
