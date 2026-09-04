"""Stable facade for the provider-client workflow components."""

from __future__ import annotations

from queue import Empty, Queue
from threading import BoundedSemaphore, Thread

from .client_compatibility_workflow import (
    canonical_coverage_reference as _canonical_coverage_reference_impl,
    collect_external as _collect_external_impl,
    collect_internal as _collect_internal_impl,
    compatibility_existing_draft as _compatibility_existing_draft_impl,
    compatibility_external_research_found as _compatibility_external_research_found_impl,
    compatibility_prompt as _compatibility_prompt_impl,
    compatibility_technical_context as _compatibility_technical_context_impl,
    prepare_grounding as _prepare_grounding_impl,
    run_compatibility as _run_compatibility_impl,
)
from .client_general_workflow import (
    collect_context_hub as _collect_context_hub_impl,
    context_hub_response as _context_hub_response_impl,
    existing_general_draft as _existing_general_draft_impl,
    initial_general_response as _initial_general_response_impl,
    initialize_general_context_pipeline as _initialize_general_context_pipeline_impl,
    run_general as _run_general_impl,
    web_fallback as _web_fallback_impl,
)
from .client_workflow_support import (
    CompatibilityBindings,
    CompatibilityWorkflowHooks,
    GeneralBindings,
    GeneralWorkflowHooks,
    STORE_BOUND_PUBLIC_CATEGORIES,
    ToolCallback,
    next_pipeline_step,
    prepare_document_vision,
    untrusted_compact_block,
    with_committed_technical_state_preserved,
)
from .context import _perguntas_ia_context_hub_deve_buscar
from .runtime import AIAnswer, logger
from .sources import (
    _ia_agent_perguntas_tool_error,
    _ia_agent_perguntas_tools_timeout_s,
)


_MANDATORY_WEB_MAX_IN_FLIGHT = 4
_MANDATORY_WEB_THREAD_PREFIX = "ml-question-required-web"
_MANDATORY_WEB_SLOTS = BoundedSemaphore(_MANDATORY_WEB_MAX_IN_FLIGHT)
_STORE_BOUND_PUBLIC_CATEGORIES = STORE_BOUND_PUBLIC_CATEGORIES

_untrusted_compact_block = untrusted_compact_block
_next_pipeline_step = next_pipeline_step
_prepare_document_vision = prepare_document_vision
_with_committed_technical_state_preserved = with_committed_technical_state_preserved


def _classified_tool(
    client,
    allowed: set[str],
    function_name: str,
    callback: ToolCallback,
) -> dict:
    if function_name not in allowed:
        return {
            "function": function_name,
            "arguments": {},
            "result": {
                "found": False,
                "skipped": True,
                "reason": "not_allowed_by_ai_classification_policy",
                "read_only": True,
            },
        }
    return client._tool_segura(function_name, callback)


def _mandatory_web_tool(function_name: str, callback: ToolCallback) -> dict:
    """Run required public research with a bounded daemon-worker deadline."""

    try:
        timeout_s = _ia_agent_perguntas_tools_timeout_s(function_name)
    except TypeError:
        # Compatibility with injected/test adapters that still expose the V6 signature.
        timeout_s = _ia_agent_perguntas_tools_timeout_s()
    if not _MANDATORY_WEB_SLOTS.acquire(blocking=False):
        logger.warning(
            "[PERGUNTAS V2] Capacidade temporaria esgotada para ferramenta web obrigatoria %s.",
            function_name,
        )
        return _ia_agent_perguntas_tool_error(
            function_name,
            "Ferramenta web obrigatoria temporariamente indisponivel; rascunho preservado.",
            timeout=True,
        )

    outcome: Queue = Queue(maxsize=1)

    def run_callback() -> None:
        try:
            outcome.put_nowait(("ok", callback()))
        except Exception as exc:
            outcome.put_nowait(("error", type(exc).__name__))
        finally:
            _MANDATORY_WEB_SLOTS.release()

    try:
        worker = Thread(
            target=run_callback,
            name=f"{_MANDATORY_WEB_THREAD_PREFIX}-{function_name}",
            daemon=True,
        )
        worker.start()
    except Exception as exc:
        _MANDATORY_WEB_SLOTS.release()
        logger.warning(
            "[PERGUNTAS V2] Falha ao iniciar ferramenta web obrigatoria %s: %s",
            function_name,
            type(exc).__name__,
        )
        return _ia_agent_perguntas_tool_error(
            function_name,
            "Ferramenta web obrigatoria temporariamente indisponivel; rascunho preservado.",
        )
    worker.join(timeout=max(0.01, float(timeout_s or 0.0)))
    if worker.is_alive():
        logger.warning(
            "[PERGUNTAS V2] Timeout em ferramenta web obrigatoria %s (%.1fs).",
            function_name,
            timeout_s,
        )
        return _ia_agent_perguntas_tool_error(
            function_name,
            "Ferramenta web obrigatoria excedeu o prazo e foi ignorada nesta resposta.",
            timeout=True,
        )

    try:
        status, payload = outcome.get_nowait()
    except Empty:
        status, payload = "error", "WorkerWithoutResult"
    if status == "error":
        logger.warning(
            "[PERGUNTAS V2] Falha segura em ferramenta web obrigatoria %s: %s",
            function_name,
            payload,
        )
        return _ia_agent_perguntas_tool_error(
            function_name,
            "Falha segura ao consultar ferramenta web obrigatoria.",
        )
    if isinstance(payload, dict):
        return payload
    return {
        "function": function_name,
        "arguments": {},
        "result": {
            "found": False,
            "unavailable": True,
            "read_only": True,
        },
    }


def _classified_web_tool(
    allowed: set[str],
    function_name: str,
    callback: ToolCallback,
) -> dict:
    if function_name not in allowed:
        return {
            "function": function_name,
            "arguments": {},
            "result": {
                "found": False,
                "skipped": True,
                "reason": "not_allowed_by_ai_classification_policy",
                "read_only": True,
            },
        }
    return _mandatory_web_tool(function_name, callback)


def _compatibility_hooks() -> CompatibilityWorkflowHooks:
    return CompatibilityWorkflowHooks(
        classified_tool=_classified_tool,
        classified_web_tool=_classified_web_tool,
        next_pipeline_step=_next_pipeline_step,
        prepare_document_vision=_prepare_document_vision,
        preserve_technical_state=_with_committed_technical_state_preserved,
    )


def _general_hooks() -> GeneralWorkflowHooks:
    return GeneralWorkflowHooks(
        classified_tool=_classified_tool,
        mandatory_web_tool=_mandatory_web_tool,
        next_pipeline_step=_next_pipeline_step,
        prepare_document_vision=_prepare_document_vision,
        preserve_technical_state=_with_committed_technical_state_preserved,
        context_hub_should_search=_perguntas_ia_context_hub_deve_buscar,
    )


def _collect_internal(client, metadata: dict, bindings: CompatibilityBindings):
    return _collect_internal_impl(client, metadata, bindings, _compatibility_hooks())


def _canonical_coverage_reference(client, metadata: dict, hub: dict):
    return _canonical_coverage_reference_impl(client, metadata, hub)


def _compatibility_technical_context(client, metadata: dict, **kwargs):
    return _compatibility_technical_context_impl(client, metadata, **kwargs)


def _collect_external(client, internal, allowed, bindings: CompatibilityBindings):
    return _collect_external_impl(
        client, internal, allowed, bindings, _compatibility_hooks(),
    )


def _prepare_grounding(client, results, identity, final_web, *additional_research):
    return _prepare_grounding_impl(
        client, results, identity, final_web, *additional_research,
    )


def _compatibility_existing_draft(client):
    return _compatibility_existing_draft_impl(client)


def _compatibility_external_research_found(*results):
    return _compatibility_external_research_found_impl(*results)


def _compatibility_prompt(
    client,
    prompt: str,
    internal: tuple[dict, dict, dict],
    hub: dict,
    memory: dict,
    external: tuple[dict, dict],
    canonical_reference: dict,
) -> str:
    return _compatibility_prompt_impl(
        client, prompt, internal, hub, memory, external, canonical_reference,
    )


def run_compatibility(
    client,
    prompt: str,
    metadata: dict,
    bindings: CompatibilityBindings,
) -> AIAnswer:
    return _run_compatibility_impl(
        client, prompt, metadata, bindings, _compatibility_hooks(),
    )


def _initialize_general_context_pipeline(client, metadata: dict) -> None:
    _initialize_general_context_pipeline_impl(client, metadata)


def _initial_general_response(client, prompt: str, metadata: dict) -> AIAnswer:
    return _initial_general_response_impl(client, prompt, metadata)


def _collect_context_hub(client, binding: GeneralBindings) -> dict:
    return _collect_context_hub_impl(client, binding)


def _context_hub_response(
    client,
    prompt: str,
    metadata: dict,
    post_sale: bool,
    binding: GeneralBindings,
):
    return _context_hub_response_impl(
        client, prompt, metadata, post_sale, binding,
    )


def _existing_general_draft(client, parsed: AIAnswer | None):
    return _existing_general_draft_impl(client, parsed)


def _web_fallback(
    client,
    prompt: str,
    metadata: dict,
    hub: dict,
    parsed: AIAnswer | None,
    binding: GeneralBindings,
    internal_sources: list[dict] | None = None,
) -> AIAnswer:
    return _web_fallback_impl(
        client,
        prompt,
        metadata,
        hub,
        parsed,
        binding,
        _general_hooks(),
        internal_sources,
    )


def run_general(
    client,
    prompt: str,
    metadata: dict,
    bindings: GeneralBindings,
) -> AIAnswer:
    return _run_general_impl(client, prompt, metadata, bindings, _general_hooks())


__all__ = ["CompatibilityBindings", "GeneralBindings", "run_compatibility", "run_general"]
