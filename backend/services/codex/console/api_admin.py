"""Administrative, telemetry, evaluation and MCP console endpoints."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import File, Header, HTTPException, Request, UploadFile

from backend.services import codex_evaluations, codex_mcp_rollout
from . import state as console_state
from .contracts import CodexEvaluationRunAPIRequest, CodexMCPRolloutRequest, CodexTaskFeedbackRequest
__codex_dependencies__ = ['_codex_ai_telemetry_instance', '_codex_base_info_dir', '_codex_public_error', '_codex_require_authenticated', '_codex_require_full_admin', '_codex_require_owned_task', '_codex_safe_id']

__codex_exports__: list[str] = []

def _codex_fixture_evaluation_runner(
    case: dict[str, Any],
    model: str,
    repetition: int,
) -> dict[str, Any]:
    fixtures = case.get("fixtures") if isinstance(case.get("fixtures"), dict) else {}
    candidates = fixtures.get("evaluation_candidates") if isinstance(fixtures.get("evaluation_candidates"), dict) else {}
    raw = candidates.get(model)
    if isinstance(raw, list):
        raw = raw[min(max(0, int(repetition)), len(raw) - 1)] if raw else None
    if not isinstance(raw, dict):
        raise codex_evaluations.EvaluationContractError(f"offline_candidate_missing:{case.get('id')}:{model}")
    return dict(raw)



def _codex_mcp_rollout_store(sessao: dict[str, Any]) -> codex_mcp_rollout.MCPRolloutPolicyStore:
    client_id = _codex_safe_id(str(sessao.get("client_id") or "default"))
    path = Path(_codex_base_info_dir()) / client_id / "codex_ai" / "mcp_rollout.sqlite3"
    return codex_mcp_rollout.MCPRolloutPolicyStore(path)



def codex_evaluation_run_get(
    run_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    result = _codex_ai_telemetry_instance().evaluation_run(sessao.get("client_id"), run_id)
    if not result.get("run"):
        raise HTTPException(status_code=404, detail=_codex_public_error("EVALUATION_RUN_NOT_FOUND", "Avaliacao nao encontrada."))
    return {"success": True, **result}



def codex_evaluation_run_post(
    payload: CodexEvaluationRunAPIRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    try:
        cases = codex_evaluations.load_dataset(require_published=True)
        split = str(payload.split or "holdout").strip().lower()
        if split not in {"calibration", "holdout", "all"}:
            raise codex_evaluations.EvaluationContractError("evaluation_split_invalid")
        selected = [case for case in cases if split == "all" or str(case.get("split")) == split]
        requested_ids = {str(item or "") for item in list(payload.case_ids or []) if str(item or "")}
        if requested_ids:
            selected = [case for case in selected if str(case.get("id") or "") in requested_ids]
            if len(selected) != len(requested_ids):
                raise codex_evaluations.EvaluationContractError("evaluation_case_missing")
        run_request = codex_evaluations.build_run_request(
            selected,
            models=payload.models,
            repetitions=payload.repetitions,
            purpose=payload.purpose,
        )
        runner = console_state.CONSOLE_STATE.evaluation_runner or _codex_fixture_evaluation_runner
        result = codex_evaluations.run_with_fixtures(selected, run_request, runner)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=409,
            detail=_codex_public_error(
                "EVALUATION_DATASET_NOT_READY",
                "A base revisada de 120 casos ainda nao foi publicada.",
            ),
        ) from exc
    except codex_evaluations.EvaluationContractError as exc:
        code = "EVALUATION_RUNNER_NOT_CONFIGURED" if "offline_candidate_missing" in str(exc) else "EVALUATION_CONTRACT_INVALID"
        status_code = 503 if code == "EVALUATION_RUNNER_NOT_CONFIGURED" else 409
        raise HTTPException(status_code=status_code, detail=_codex_public_error(code, str(exc)[:300])) from exc

    by_case = {str(case.get("id") or ""): case for case in selected}
    telemetry = _codex_ai_telemetry_instance()
    scored = list(result.get("results") or [])
    for item in scored:
        score = item.get("score") if isinstance(item.get("score"), dict) else {}
        case = by_case.get(str(item.get("case_id") or ""), {})
        telemetry.record_evaluation_case(
            sessao.get("client_id"),
            run_id=result.get("run_id"),
            case_id=item.get("case_id"),
            category=case.get("category"),
            status="passed" if score.get("passed") else "failed",
            model=item.get("model_effective") or item.get("model_requested"),
            score=score.get("score", 0),
            critical_failure=bool(score.get("automatic_failures")),
            duration_ms=item.get("duration_ms", 0),
            total_tokens=item.get("tokens", 0),
        )
    passed = sum(1 for item in scored if (item.get("score") or {}).get("passed") is True)
    critical = sum(1 for item in scored if (item.get("score") or {}).get("automatic_failures"))
    mean_score = sum(float((item.get("score") or {}).get("score") or 0) for item in scored) / max(1, len(scored))
    telemetry.record_evaluation_run(
        sessao.get("client_id"),
        run_id=result.get("run_id"),
        dataset_version=codex_evaluations.DATASET_SCHEMA_VERSION,
        status="completed",
        model="multi" if len(payload.models) > 1 else payload.models[0],
        total_cases=len(scored),
        passed_cases=passed,
        critical_failures=critical,
        mean_score=mean_score,
    )
    return {"success": True, "run": result}



def codex_evaluation_runs(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    limit: int = 100,
):
    sessao = _codex_require_full_admin(request, authorization)
    return {
        "success": True,
        "runs": _codex_ai_telemetry_instance().evaluation_runs(
            sessao.get("client_id"), limit=max(1, min(int(limit or 100), 1000))
        ),
    }



def codex_mcp_rollout_get(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    store = _codex_mcp_rollout_store(sessao)
    return {
        "success": True,
        "rollout": store.get(),
        "metrics": store.current_metrics(),
    }



def codex_mcp_rollout_put(
    payload: CodexMCPRolloutRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    mode = str(payload.mode or "off").strip().lower()
    public_modes = {"off", "shadow", "pilot", "whatsapp_5", "whatsapp_25", "whatsapp_50", "read_only_100"}
    if mode not in public_modes:
        raise HTTPException(status_code=400, detail=_codex_public_error("MCP_ROLLOUT_MODE_INVALID", "Etapa de rollout MCP invalida."))
    try:
        store = _codex_mcp_rollout_store(sessao)
        authoritative_metrics = store.current_metrics()
        rollback = store.evaluate_and_rollback(
            authoritative_metrics,
            actor=str(sessao.get("username") or "admin"),
        )
        if rollback["rolled_back"]:
            return {"success": True, "automatic_rollback": rollback}
        rollout = store.advance(
            {
                "enabled": mode != "off",
                "mode": mode,
                "allowed_tools": list(payload.allowed_tools or []),
                "baseline_p95_ms": payload.baseline_p95_ms,
            },
            actor=str(sessao.get("username") or "admin"),
            observations=authoritative_metrics,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=_codex_public_error("MCP_ROLLOUT_GATE_NOT_MET", "A etapa atual ainda nao cumpriu os criterios minimos."),
        ) from exc
    except Exception as exc:
        # Alterar rollout sem auditoria duravel e proibido.
        raise HTTPException(
            status_code=503,
            detail=_codex_public_error("MCP_ROLLOUT_AUDIT_FAILED", "A alteracao nao foi aplicada porque a auditoria falhou.", retryable=True),
        ) from exc
    return {"success": True, "rollout": rollout}



def codex_task_feedback(
    task_id: str,
    payload: CodexTaskFeedbackRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    task = _codex_require_owned_task(task_id, sessao)
    if int(payload.rating) not in {-1, 0, 1}:
        raise HTTPException(status_code=400, detail=_codex_public_error("FEEDBACK_RATING_INVALID", "Use -1, 0 ou 1."))
    accepted = _codex_ai_telemetry_instance().record_feedback(
        sessao.get("client_id"),
        feedback_id=f"{task_id}:{sessao.get('username')}:{uuid.uuid4().hex}",
        trace_id=task.get("trace_id") or task_id,
        rating=payload.rating,
        label=payload.label,
        source=task.get("origin") or "app",
        user_id=sessao.get("username"),
    )
    return {"success": bool(accepted)}



def codex_telemetry_summary(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    from_at: str = "",
    to_at: str = "",
    surface: str = "",
    model: str = "",
    provider: str = "",
    status: str = "",
):
    sessao = _codex_require_full_admin(request, authorization)
    telemetry = _codex_ai_telemetry_instance()
    return {
        "success": True,
        "summary": telemetry.summary(
            sessao.get("client_id"),
            from_at=from_at,
            to_at=to_at,
            surface=surface,
            model=model,
            provider=provider,
            status=status,
        ),
        "feedback": telemetry.feedback_summary(sessao.get("client_id")),
        "writer": telemetry.diagnostics(),
    }



def codex_telemetry_timeseries(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    from_at: str = "",
    to_at: str = "",
    bucket: str = "hour",
    surface: str = "",
    model: str = "",
    provider: str = "",
    status: str = "",
):
    sessao = _codex_require_full_admin(request, authorization)
    return {
        "success": True,
        "bucket": "day" if str(bucket).lower() == "day" else "hour",
        "series": _codex_ai_telemetry_instance().timeseries(
            sessao.get("client_id"),
            from_at=from_at,
            to_at=to_at,
            bucket=bucket,
            surface=surface,
            model=model,
            provider=provider,
            status=status,
        ),
    }



def codex_transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(default=None),
):
    """Turn a transient local recording into editable composer text."""

    sessao = _codex_require_authenticated(request, authorization)
    from backend.services import local_audio_transcription

    return local_audio_transcription.transcribe_authenticated_upload(
        file,
        client_id=str(sessao.get("client_id") or ""),
        username=str(sessao.get("username") or ""),
    )
