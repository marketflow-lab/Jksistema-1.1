"""Compatibility facade for modular Codex assistant SQLite persistence."""

from __future__ import annotations

from backend.services.codex.storage.common import (
    SCHEMA_VERSION,
    STATE_ROW_SCHEDULER,
    _LOCKS_LOCK,
    _LOCKS,
    _safe_id,
    _now_iso,
    _json_dumps,
    _json_loads,
    _sha_text,
    _read_json_file,
    _file_sha256,
    _info_base,
    codex_assistant_client_dir,
    codex_assistant_cache_db_path,
    codex_assistant_state_db_path,
    _lock_for,
    _connect,
    _connection,
    _meta_get,
    _meta_set,
    _ensure_meta_schema,
)

from backend.services.codex.storage.customer_reply_state import (
    _CUSTOMER_REPLY_TRANSIENT_LOCK,
    _CUSTOMER_REPLY_TRANSIENT,
    _CUSTOMER_REPLY_ACTIVE_TRANSIENT_TTL_SECONDS,
    _CUSTOMER_REPLY_COMPLETED_TRANSIENT_TTL_SECONDS,
    _CUSTOMER_REPLY_TERMINAL_ROW_TTL_DAYS,
    _CUSTOMER_REPLY_DURABLE_FIELDS,
    _CUSTOMER_REPLY_SCOPE_ID_FIELDS,
    _customer_reply_cache_key,
    _customer_reply_durable_payload,
    _customer_reply_plan_durable_payload,
    _customer_reply_transient_put,
    _customer_reply_transient_merge,
    _customer_reply_cleanup_rows,
)

from backend.services.codex.storage.schema import (
    _ensure_cache_schema,
    _ensure_state_schema,
)

from backend.services.codex.storage.cache import (
    _cache_insert_conn,
    _legacy_cache_file,
    _cache_import_legacy_conn,
    _cache_try_import_single_legacy,
    codex_assistant_cache_get,
    codex_assistant_cache_set,
)

from backend.services.codex.storage.reports import (
    _report_row_to_payload,
    _report_upsert_conn,
    _legacy_report_metadata_path,
    _report_import_legacy_file_conn,
    codex_assistant_reports_import_legacy,
    codex_assistant_report_save,
    codex_assistant_report_get,
    codex_assistant_reports_list,
)

from backend.services.codex.storage.scheduler import (
    _scheduler_legacy_path,
    _scheduler_light_payload_conn,
    _scheduler_upsert_conn,
    _scheduler_import_legacy_conn,
    _scheduler_rehydrate,
    codex_assistant_scheduler_get,
    codex_assistant_scheduler_save,
)

from backend.services.codex.storage.management import (
    codex_assistant_report_settings_get,
    codex_assistant_report_settings_save,
    codex_assistant_financial_adjustment_save,
    codex_assistant_financial_adjustments_list,
    codex_assistant_financial_adjustment_delete,
    codex_assistant_action_queue_save,
    codex_assistant_action_queue_list,
    codex_assistant_action_queue_get,
)

from backend.services.codex.storage.plans import (
    codex_assistant_agent_plan_save,
    codex_assistant_agent_plan_get,
    codex_assistant_agent_guidance_save,
    codex_assistant_agent_guidance_list,
)

from backend.services.codex.storage.customer_replies import (
    codex_assistant_customer_reply_job_has_transient,
    codex_assistant_customer_reply_jobs_cleanup,
    codex_assistant_customer_reply_job_save,
    codex_assistant_customer_reply_job_get,
    codex_assistant_customer_reply_job_latest,
    codex_assistant_customer_reply_jobs_list,
    codex_assistant_customer_reply_queue_metrics,
    codex_assistant_customer_reply_job_claim,
    codex_assistant_customer_reply_job_heartbeat,
    codex_assistant_customer_reply_job_request_cancel,
)

from backend.services.codex.storage.solicitacoes import (
    codex_assistant_customer_reply_solicitacoes_list,
)

from backend.services.codex.storage.actions import (
    codex_assistant_action_proposal_save,
    codex_assistant_action_proposal_get,
    codex_assistant_action_proposal_list,
    codex_assistant_action_run_save,
    codex_assistant_action_run_get,
    codex_assistant_action_approval_save,
    codex_assistant_agent_audit_add,
    codex_assistant_agent_audit_list,
)

__all__ = [
    "codex_assistant_customer_reply_job_has_transient",
    "codex_assistant_customer_reply_jobs_cleanup",
    "codex_assistant_client_dir",
    "codex_assistant_cache_db_path",
    "codex_assistant_state_db_path",
    "codex_assistant_cache_get",
    "codex_assistant_cache_set",
    "codex_assistant_reports_import_legacy",
    "codex_assistant_report_save",
    "codex_assistant_report_get",
    "codex_assistant_reports_list",
    "codex_assistant_scheduler_get",
    "codex_assistant_scheduler_save",
    "codex_assistant_report_settings_get",
    "codex_assistant_report_settings_save",
    "codex_assistant_financial_adjustment_save",
    "codex_assistant_financial_adjustments_list",
    "codex_assistant_financial_adjustment_delete",
    "codex_assistant_action_queue_save",
    "codex_assistant_action_queue_list",
    "codex_assistant_action_queue_get",
    "codex_assistant_agent_plan_save",
    "codex_assistant_agent_plan_get",
    "codex_assistant_customer_reply_job_save",
    "codex_assistant_customer_reply_job_get",
    "codex_assistant_customer_reply_job_latest",
    "codex_assistant_customer_reply_jobs_list",
    "codex_assistant_customer_reply_solicitacoes_list",
    "codex_assistant_customer_reply_queue_metrics",
    "codex_assistant_customer_reply_job_claim",
    "codex_assistant_customer_reply_job_heartbeat",
    "codex_assistant_customer_reply_job_request_cancel",
    "codex_assistant_agent_guidance_save",
    "codex_assistant_agent_guidance_list",
    "codex_assistant_action_proposal_save",
    "codex_assistant_action_proposal_get",
    "codex_assistant_action_proposal_list",
    "codex_assistant_action_run_save",
    "codex_assistant_action_run_get",
    "codex_assistant_action_approval_save",
    "codex_assistant_agent_audit_add",
    "codex_assistant_agent_audit_list",
]
