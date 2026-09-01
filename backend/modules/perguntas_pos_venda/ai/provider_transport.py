"""Low-level network transports without dependencies on the PPV state graph."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Mapping

import httpx
import requests

from backend.schemas.ia import IAChatRequest
from backend.services.ia_providers import (
    _chamar_codex_chat,
    _chamar_codex_chat_com_thread,
    _chamar_deepseek_chat,
    _chamar_gemini_chat,
    _chamar_openai_responses,
    _chamar_vertex_ai_chat,
    _codex_modelo_nome_curto,
    _gemini_nome_curto,
    _modelo_eh_codex,
    _modelo_eh_gemini_api,
    _modelo_eh_vertex_ai,
    _vertex_ai_modelo_padrao,
    _vertex_modelo_nome_curto,
)
from .deep_research_contracts import PUBLIC_RESEARCH_MAX_DECOMPRESSED_BYTES


@dataclass
class _BufferedResearchResponse:
    status_code: int
    content: bytes
    encoding: str = "utf-8"
    closed: bool = False

    def iter_content(self, *, chunk_size: int, decode_unicode: bool = False):
        for start in range(0, len(self.content), max(1, int(chunk_size))):
            chunk = self.content[start : start + chunk_size]
            yield chunk.decode(self.encoding, errors="replace") if decode_unicode else chunk

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def close(self) -> None:
        self.closed = True


async def _fetch_buffered_research_response(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: tuple[float, float],
    verify: object,
    allow_redirects: bool,
    maximum_bytes: int,
    transport: httpx.AsyncBaseTransport | None,
) -> _BufferedResearchResponse:
    request_timeout = httpx.Timeout(
        connect=float(timeout[0]),
        read=float(timeout[1]),
        write=float(timeout[0]),
        pool=float(timeout[0]),
    )
    async with httpx.AsyncClient(
        verify=verify,
        timeout=request_timeout,
        follow_redirects=allow_redirects,
        transport=transport,
    ) as client:
        async with client.stream("GET", url, headers=dict(headers)) as response:
            if int(response.status_code) >= 300:
                return _BufferedResearchResponse(
                    status_code=int(response.status_code),
                    content=b"",
                    encoding=str(response.encoding or "utf-8"),
                )
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                remaining = maximum_bytes - len(payload)
                if remaining <= 0:
                    break
                payload.extend(chunk[:remaining])
                if len(chunk) >= remaining:
                    break
            return _BufferedResearchResponse(
                status_code=int(response.status_code),
                content=bytes(payload),
                encoding=str(response.encoding or "utf-8"),
            )


def fetch_research_response(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: tuple[float, float],
    verify: object,
    allow_redirects: bool,
    stream: bool,
    deadline_monotonic: float | None = None,
    maximum_bytes: int = PUBLIC_RESEARCH_MAX_DECOMPRESSED_BYTES,
    transport: httpx.AsyncBaseTransport | None = None,
) -> object:
    """Fetch a reader response with a hard deadline over headers and body."""

    if deadline_monotonic is None:
        return requests.get(
            url,
            headers=dict(headers),
            timeout=timeout,
            verify=verify,
            allow_redirects=allow_redirects,
            stream=stream,
        )
    loop_timeout = float(deadline_monotonic) - time.monotonic()
    if loop_timeout <= 0:
        raise requests.exceptions.ReadTimeout("research deadline exceeded")

    async def run_with_deadline() -> _BufferedResearchResponse:
        return await asyncio.wait_for(
            _fetch_buffered_research_response(
                url,
                headers=headers,
                timeout=timeout,
                verify=verify,
                allow_redirects=allow_redirects,
                maximum_bytes=max(1, int(maximum_bytes)),
                transport=transport,
            ),
            timeout=loop_timeout,
        )

    try:
        return asyncio.run(run_with_deadline())
    except TimeoutError as exc:
        raise requests.exceptions.ReadTimeout("research deadline exceeded") from exc
    except httpx.ConnectTimeout as exc:
        raise requests.exceptions.ConnectTimeout(str(exc)) from exc
    except httpx.TimeoutException as exc:
        raise requests.exceptions.ReadTimeout(str(exc)) from exc
    except httpx.TransportError as exc:
        raise requests.exceptions.ConnectionError(str(exc)) from exc


def _model_adapter(name: str, default):
    from .runtime import resolve_runtime_adapter

    return resolve_runtime_adapter("models", name, default)


def invoke_model(client_id: str, payload: IAChatRequest, model_req: str) -> tuple[str, str]:
    if _modelo_eh_codex(model_req):
        context = payload.context if isinstance(payload.context, dict) else {}
        on_thread_ready = context.pop("_codex_on_thread_ready", None)
        thread_id = str(context.get("_codex_thread_id") or "").strip()
        if context.get("_codex_persist_thread") or thread_id:
            response, resulting_thread_id = _model_adapter("call_codex_thread", _chamar_codex_chat_com_thread)(
                payload,
                client_id,
                thread_id=thread_id,
                persist_thread=True,
                conversation_key=str(context.get("_codex_conversation_key") or context.get("_codex_job_id") or ""),
                active_turn_key=str(context.get("_codex_active_turn_key") or context.get("_codex_job_id") or ""),
                on_thread_ready=on_thread_ready if callable(on_thread_ready) else None,
            )
            context["_codex_thread_id_result"] = resulting_thread_id
            payload.context = context
        else:
            response = _model_adapter("call_codex", _chamar_codex_chat)(payload, client_id)
        return response, f"codex:{_codex_modelo_nome_curto(model_req)}"
    if _modelo_eh_vertex_ai(model_req):
        response = _model_adapter("call_vertex", _chamar_vertex_ai_chat)(payload, client_id)
        return response, f"vertex:{_vertex_modelo_nome_curto(model_req) or _vertex_ai_modelo_padrao()}"
    if _modelo_eh_gemini_api(model_req):
        response = _model_adapter("call_gemini", _chamar_gemini_chat)(payload, client_id)
        return response, f"gemini:{_gemini_nome_curto(model_req) or 'gemini-2.5-flash'}"
    if model_req.startswith("deepseek-"):
        return _model_adapter("call_deepseek", _chamar_deepseek_chat)(payload, client_id), model_req
    response = _model_adapter("call_openai", _chamar_openai_responses)(payload, client_id)
    return response, model_req or (os.getenv("OPENAI_MODEL") or "gpt-5.4-nano").strip()


__all__ = ["fetch_research_response", "invoke_model"]
