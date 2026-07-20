"""OpenAI Realtime SIP sideband for inbound Black Jhon WhatsApp calls.

Audio stays between Meta/SIP and OpenAI.  This process receives only Realtime
events and completed transcripts, and delegates every semantic answer to the
existing WhatsApp Codex orchestrator in strict read-only mode.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import re
import threading
import time
import uuid
from collections import deque
from typing import Any, Callable, Optional

import requests

from backend.services import codex_console, codex_whatsapp_agents, ia_providers


VOICE_MODEL_DEFAULT = "gpt-realtime-2.1"
VOICE_TRANSCRIPTION_MODEL_DEFAULT = "gpt-4o-transcribe"
VOICE_NAME_DEFAULT = "cedar"
VOICE_LANGUAGE_DEFAULT = "pt-BR"
VOICE_PROGRESS_SECONDS_DEFAULT = 8
VOICE_LONG_TASK_OFFER_SECONDS_DEFAULT = 90
VOICE_MAX_CALL_MINUTES_DEFAULT = 30
VOICE_SILENCE_SECONDS_DEFAULT = 90
VOICE_MAX_CONCURRENT_DEFAULT = 3
VOICE_TERMINAL_TASK_STATES = {"completed", "failed", "cancelled", "canceled"}


def openai_key_fingerprint() -> str:
    key = str(ia_providers._obter_openai_api_key() or "").strip()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16] if key else ""


def _websockets_available() -> bool:
    return importlib.util.find_spec("websockets") is not None


def _setting_int(config: dict[str, Any], key: str, fallback: int, minimum: int, maximum: int) -> int:
    try:
        value = int(config.get(key) or fallback)
    except (TypeError, ValueError):
        value = fallback
    return max(minimum, min(maximum, value))


def _safe_spoken_text(value: Any, limit: int = 3500) -> str:
    text = str(value or "").replace("\x00", "").replace("\r\n", "\n").strip()
    text = re.sub(r"https?://\S+", "link disponível na mensagem", text)
    text = re.sub(r"[*_`#>|]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:limit].strip()


def _choice(value: Any) -> str:
    text = unicodedata_key(value)
    if re.search(r"\b(mensagem|whatsapp|manda|envia|pode desligar)\b", text):
        return "message"
    if re.search(r"\b(continuar|aguardar|esperar|na ligacao|fico aqui)\b", text):
        return "continue"
    return ""


def unicodedata_key(value: Any) -> str:
    import unicodedata

    text = unicodedata.normalize("NFD", str(value or "").lower())
    return re.sub(r"[^a-z0-9 ]+", " ", "".join(char for char in text if unicodedata.category(char) != "Mn"))


class VoiceRuntime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._calls: dict[str, dict[str, Any]] = {}
        self._last_heartbeat = 0.0
        self._last_claim = 0.0
        self._last_error = ""
        self._last_ok_at = ""

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            active = [
                {
                    "call_id": call_id,
                    "phone_suffix": str(state.get("phone") or "")[-4:],
                    "status": str(state.get("status") or ""),
                    "started_at": str(state.get("started_at") or ""),
                    "active_task_id": str(state.get("active_task_id") or ""),
                    "queued_turns": len(state.get("queue") or []),
                    "deliver_by_message": state.get("deliver_by_message") is True,
                }
                for call_id, state in self._calls.items()
            ]
            return {
                "ready": _websockets_available() and bool(openai_key_fingerprint()),
                "dependency_installed": _websockets_available(),
                "api_key_configured": bool(openai_key_fingerprint()),
                "api_key_fingerprint": openai_key_fingerprint(),
                "active_calls": active,
                "active_call_count": len(active),
                "last_error": self._last_error,
                "last_ok_at": self._last_ok_at,
            }

    def preflight(self, config: dict[str, Any], bridge: Any, *, check_openai: bool = True) -> dict[str, Any]:
        local = self.diagnostics()
        try:
            gateway = bridge._gateway_json(config, "GET", "/bridge/voice/status", timeout=15)
        except Exception as exc:
            gateway = {"success": False, "configured": False, "error": str(exc)[:500]}
        model = str(config.get("voice_model") or VOICE_MODEL_DEFAULT)
        model_access = {"checked": False, "ready": False, "status": 0, "error": ""}
        key = str(ia_providers._obter_openai_api_key() or "").strip()
        if check_openai and key:
            try:
                response = requests.get(
                    f"https://api.openai.com/v1/models/{model}",
                    headers={"authorization": f"Bearer {key}"},
                    timeout=12,
                )
                model_access = {
                    "checked": True,
                    "ready": response.ok,
                    "status": response.status_code,
                    "error": "" if response.ok else "Modelo Realtime indisponivel para esta chave.",
                }
            except Exception as exc:
                model_access = {"checked": True, "ready": False, "status": 0, "error": str(exc)[:300]}
        gateway_fingerprint = str(((gateway.get("openai") or {}) if isinstance(gateway.get("openai"), dict) else {}).get("key_fingerprint") or "")
        key_match = bool(gateway_fingerprint and gateway_fingerprint == openai_key_fingerprint())
        local_public = {key: value for key, value in local.items() if key != "api_key_fingerprint"}
        gateway_public = {
            key: value
            for key, value in gateway.items()
            if key not in {"calls", "heartbeats"}
        }
        if isinstance(gateway_public.get("openai"), dict):
            gateway_public["openai"] = {
                key: value
                for key, value in gateway_public["openai"].items()
                if key != "key_fingerprint"
            }
        return {
            "success": bool(local.get("ready") and gateway.get("configured") and key_match and (model_access.get("ready") or not check_openai)),
            "local": local_public,
            "gateway": gateway_public,
            "openai_model": model_access,
            "key_match": key_match,
            "sip": gateway.get("sip") if isinstance(gateway.get("sip"), dict) else {},
        }

    def tick(self, config: dict[str, Any], bridge: Any) -> None:
        now = time.time()
        enabled = config.get("voice_enabled") is True
        capacity = _setting_int(config, "voice_max_concurrent_calls", VOICE_MAX_CONCURRENT_DEFAULT, 1, 10) if enabled else 0
        with self._lock:
            active = len(self._calls)
        if now - self._last_heartbeat >= 5:
            self._last_heartbeat = now
            try:
                bridge._gateway_json(
                    config,
                    "POST",
                    "/bridge/voice/heartbeat",
                    {
                        "machine_id": str(config.get("machine_id") or ""),
                        "active_call_count": active,
                        "capacity": capacity,
                        "key_fingerprint": openai_key_fingerprint(),
                    },
                    timeout=10,
                )
                self._last_ok_at = bridge._now()
            except Exception as exc:
                self._last_error = str(exc)[:800]
        if not enabled or not _websockets_available() or not openai_key_fingerprint() or active >= capacity:
            return
        if now - self._last_claim < 1:
            return
        self._last_claim = now
        try:
            result = bridge._gateway_json(
                config,
                "POST",
                "/bridge/voice/calls/claim",
                {"machine_id": str(config.get("machine_id") or ""), "limit": max(1, capacity - active)},
                timeout=10,
            )
            self._last_error = ""
        except Exception as exc:
            self._last_error = str(exc)[:800]
            return
        for call in list(result.get("calls") or []):
            if isinstance(call, dict):
                self._start_call(dict(config), bridge, call)

    def _start_call(self, config: dict[str, Any], bridge: Any, call: dict[str, Any]) -> None:
        call_id = str(call.get("id") or "").strip()
        if not call_id:
            return
        with self._lock:
            if call_id in self._threads:
                return
            state = {
                "status": "starting",
                "started_at": bridge._now(),
                "started_epoch": time.time(),
                "phone": str(call.get("wa_id") or ""),
                "queue": deque(),
                "active_task_id": "",
                "task_ids": [],
                "choice_requested": False,
                "deliver_by_message": False,
                "usage": {},
                "last_user_speech_at": time.time(),
            }
            self._calls[call_id] = state
            thread = threading.Thread(
                target=self._run_call_thread,
                args=(config, bridge, call, state),
                name=f"jk-whatsapp-voice-{call_id[-10:]}",
                daemon=True,
            )
            self._threads[call_id] = thread
            thread.start()

    def _call_state(self, bridge: Any, config: dict[str, Any], call_id: str, status: str, **extra: Any) -> None:
        payload = {"machine_id": str(config.get("machine_id") or ""), "status": status, **extra}
        bridge._gateway_json(config, "POST", f"/bridge/voice/calls/{call_id}/state", payload, timeout=10)

    def _run_call_thread(self, config: dict[str, Any], bridge: Any, call: dict[str, Any], state: dict[str, Any]) -> None:
        call_id = str(call.get("id") or "")
        error = ""
        terminal = "ended"
        try:
            asyncio.run(self._run_call(config, bridge, call, state))
        except Exception as exc:
            error = str(exc)[:800]
            terminal = "failed"
            self._last_error = error
        finally:
            duration = max(0, int(time.time() - float(state.get("started_epoch") or time.time())))
            for task_id in list(state.get("task_ids") or []):
                try:
                    task = codex_console._codex_load_task(str(task_id))
                    if not isinstance(task, dict):
                        continue
                    metadata = dict(task.get("channel_metadata") or {})
                    metadata["duration_seconds"] = duration
                    codex_console._codex_update_task(str(task_id), channel_metadata=metadata)
                except Exception:
                    pass
            try:
                self._call_state(bridge, config, call_id, terminal, usage=state.get("usage") or {}, error=error)
            except Exception:
                pass
            with self._lock:
                self._calls.pop(call_id, None)
                self._threads.pop(call_id, None)

    async def _run_call(self, config: dict[str, Any], bridge: Any, call: dict[str, Any], state: dict[str, Any]) -> None:
        try:
            from websockets.asyncio.client import connect
        except ImportError:
            from websockets import connect  # type: ignore

        key = str(ia_providers._obter_openai_api_key() or "").strip()
        if not key:
            raise RuntimeError("openai_api_key_missing")
        call_id = str(call.get("id") or "")
        openai_call_id = str(call.get("openai_call_id") or "")
        if not openai_call_id:
            raise RuntimeError("openai_call_id_missing")
        url = f"wss://api.openai.com/v1/realtime?call_id={openai_call_id}"
        max_seconds = _setting_int(config, "voice_max_call_minutes", VOICE_MAX_CALL_MINUTES_DEFAULT, 5, 60) * 60
        silence_seconds = _setting_int(config, "voice_silence_timeout_seconds", VOICE_SILENCE_SECONDS_DEFAULT, 30, 300)
        async with connect(url, additional_headers={"Authorization": f"Bearer {key}"}, open_timeout=15, close_timeout=5) as websocket:
            state["status"] = "connected"
            self._call_state(bridge, config, call_id, "connected")
            await websocket.send(json.dumps({
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "instructions": (
                        "Voce apenas fala mensagens fornecidas pelo servidor seguro do Black Jhon. "
                        "Nao responda por conta propria, nao use ferramentas e nao altere fatos. Fale em portugues brasileiro."
                    ),
                    "audio": {
                        "input": {
                            "transcription": {"model": str(config.get("voice_transcription_model") or VOICE_TRANSCRIPTION_MODEL_DEFAULT), "language": "pt"},
                            "turn_detection": {"type": "server_vad", "create_response": False, "interrupt_response": True, "silence_duration_ms": 650, "prefix_padding_ms": 300},
                        },
                        "output": {"voice": str(config.get("voice_name") or VOICE_NAME_DEFAULT)},
                    },
                    "tool_choice": "none",
                    "tools": [],
                },
            }, ensure_ascii=False))
            await self._speak(
                websocket,
                "Olá! Você está falando com o Black Jhon. Esta ligação será transcrita e aparecerá no seu histórico. Como posso ajudar?",
            )
            current: Optional[asyncio.Task[str]] = None
            current_prompt = ""
            started = time.time()
            silence_warned = False
            while True:
                if time.time() - started >= max_seconds:
                    await self._speak(websocket, "Chegamos ao limite desta ligação. Se houver uma consulta em andamento, enviarei o resultado neste mesmo WhatsApp.")
                    state["deliver_by_message"] = True
                    await asyncio.sleep(1.5)
                    await asyncio.to_thread(self._hangup, openai_call_id, key)
                    if current is not None:
                        answer = await current
                        if not state.pop("result_message_sent", False):
                            await asyncio.to_thread(self._send_result_message, config, bridge, call, answer)
                    return
                idle = time.time() - float(state.get("last_user_speech_at") or time.time())
                if not current and not state["queue"] and idle >= silence_seconds and not silence_warned:
                    silence_warned = True
                    await self._speak(websocket, "Ainda está aí? Se não ouvir você, encerrarei a ligação em trinta segundos.")
                if not current and not state["queue"] and idle >= silence_seconds + 30:
                    await asyncio.to_thread(self._hangup, openai_call_id, key)
                    return
                if current is None and state["queue"]:
                    current_prompt = str(state["queue"].popleft())
                    current = asyncio.create_task(self._answer_turn_async(config, bridge, call, state, current_prompt, websocket))
                if current and current.done():
                    try:
                        answer = current.result()
                    except Exception as exc:
                        answer = f"Não consegui concluir esta consulta: {str(exc)[:240]}."
                    result_message_sent = bool(state.pop("result_message_sent", False))
                    if state.get("deliver_by_message") is True:
                        if not result_message_sent:
                            await asyncio.to_thread(self._send_result_message, config, bridge, call, answer)
                    else:
                        await self._speak(websocket, answer)
                    current = None
                    current_prompt = ""
                    state["active_task_id"] = ""
                    state["choice_requested"] = False
                    continue
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    if current is not None:
                        state["deliver_by_message"] = True
                        answer = await current
                        if not state.pop("result_message_sent", False):
                            await asyncio.to_thread(self._send_result_message, config, bridge, call, answer)
                    return
                event = json.loads(raw)
                event_type = str(event.get("type") or "")
                if event_type == "input_audio_buffer.speech_started":
                    state["last_user_speech_at"] = time.time()
                    silence_warned = False
                    try:
                        await websocket.send(json.dumps({"type": "response.cancel"}))
                    except Exception:
                        pass
                    continue
                if event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = str(event.get("transcript") or "").strip()
                    if not transcript:
                        continue
                    state["last_user_speech_at"] = time.time()
                    if state.get("choice_requested") is True:
                        selected = _choice(transcript)
                        if selected == "message":
                            state["deliver_by_message"] = True
                            state["choice_requested"] = False
                            await self._speak(websocket, "Tudo bem. Vou concluir e enviar o resultado neste mesmo WhatsApp.")
                            await asyncio.sleep(1.2)
                            await asyncio.to_thread(self._hangup, openai_call_id, key)
                            if current is not None:
                                answer = await current
                                if not state.pop("result_message_sent", False):
                                    await asyncio.to_thread(self._send_result_message, config, bridge, call, answer)
                            return
                        if selected == "continue":
                            state["choice_requested"] = False
                            await self._speak(websocket, "Certo, continuo com você na ligação.")
                            continue
                    if current is not None:
                        state["queue"].append(transcript)
                        await self._speak(websocket, "Anotei. Vou tratar isso assim que concluir a consulta atual.")
                    else:
                        state["queue"].append(transcript)
                    continue
                if event_type == "response.done":
                    usage = ((event.get("response") or {}) if isinstance(event.get("response"), dict) else {}).get("usage")
                    self._merge_usage(state, usage)
                if event_type in {"session.closed", "call.ended"}:
                    return
                if event_type == "error":
                    error = event.get("error") if isinstance(event.get("error"), dict) else {}
                    raise RuntimeError(str(error.get("message") or "realtime_error")[:500])

    async def _answer_turn_async(
        self,
        config: dict[str, Any],
        bridge: Any,
        call: dict[str, Any],
        state: dict[str, Any],
        transcript: str,
        websocket: Any,
    ) -> str:
        loop = asyncio.get_running_loop()

        def progress(text: str, *, choice: bool = False) -> None:
            if choice:
                state["choice_requested"] = True
            asyncio.run_coroutine_threadsafe(self._speak(websocket, text), loop)

        return await asyncio.to_thread(self._answer_turn, config, bridge, call, state, transcript, progress)

    def _answer_turn(
        self,
        config: dict[str, Any],
        bridge: Any,
        call: dict[str, Any],
        state: dict[str, Any],
        transcript: str,
        progress: Callable[..., None],
    ) -> str:
        phone = str(call.get("wa_id") or "")
        message_id = f"voice:{str(call.get('id') or '')}:{uuid.uuid4().hex}"
        message = {
            "message_id": message_id,
            "subject_id": str(call.get("subject_id") or ""),
            "wa_id": phone,
            "phone_number": phone,
            "client_id": str(call.get("client_id") or ""),
            "username": str(call.get("username") or ""),
            "machine_id": str(call.get("machine_id") or config.get("machine_id") or ""),
            "message_type": "voice_call",
            "text_body": transcript,
            "received_at": int(time.time()),
        }
        session = bridge._reload_bound_session(config, message)
        conversation_id = bridge._conversation_id(config, message)
        phone_settings = bridge._phone_notification_settings(
            config,
            message["subject_id"],
            client_id=session.get("client_id"),
            username=session.get("username"),
        )
        ai_behavior = bridge._normalize_phone_ai_behavior(phone_settings.get("ai_behavior"))
        local_state = bridge._load_state()
        decision = bridge._run_conversation_agent(
            config,
            local_state,
            conversation_id,
            event_type="user_message",
            user_message=transcript,
            ai_behavior=ai_behavior,
        )
        action = str(decision.get("action") or "")
        if action in {"reply", "request_information"}:
            answer = _safe_spoken_text(decision.get("reply_text"))
            task = codex_console.codex_registrar_interacao_whatsapp_externa(
                client_id=str(session.get("client_id") or "default"),
                username=str(session.get("username") or ""),
                phone=phone,
                prompt=transcript,
                response=answer,
                model=str(decision.get("effective_model") or "black-jhon-voice"),
                call_id=str(call.get("id") or ""),
            )
            state["task_ids"].append(str(task.get("task_id") or ""))
            return answer
        if action not in {"delegate", "queue", "steer"}:
            return "Não consegui interpretar esse pedido com segurança. Pode reformular em uma frase?"
        job_prompt = str(decision.get("job_prompt") or transcript).strip()
        query_policy = bridge._dual_delegate_query_policy(job_prompt, session, local_state, conversation_id)
        if query_policy.get("store_required") and query_policy.get("store_mode") != "all" and len(query_policy.get("store_matches") or []) != 1:
            stores = [str(item) for item in list(query_policy.get("authorized_stores") or []) if str(item).strip()]
            suffix = f" As opções são: {', '.join(stores)}." if stores else ""
            answer = f"De qual loja você está falando?{suffix}"
            task = codex_console.codex_registrar_interacao_whatsapp_externa(
                client_id=str(session.get("client_id") or "default"), username=str(session.get("username") or ""),
                phone=phone, prompt=transcript, response=answer, call_id=str(call.get("id") or ""),
            )
            state["task_ids"].append(str(task.get("task_id") or ""))
            return answer
        task = bridge._create_dual_worker_task(
            config,
            message=message,
            message_id=message_id,
            subject=str(message["subject_id"]),
            phone=phone,
            session=session,
            conversation_id=conversation_id,
            request_text=transcript,
            job_prompt=job_prompt,
            job_title=str(decision.get("job_title") or transcript)[:180],
            media=None,
            transcription=None,
            phone_ai_behavior=ai_behavior,
            query_policy=query_policy,
        )
        task_id = str(task.get("task_id") or "")
        if not task_id:
            raise RuntimeError("voice_task_creation_failed")
        state["active_task_id"] = task_id
        state["task_ids"].append(task_id)
        started = time.time()
        progress_interval = _setting_int(config, "voice_progress_interval_seconds", VOICE_PROGRESS_SECONDS_DEFAULT, 8, 30)
        offer_after = _setting_int(config, "voice_long_task_offer_seconds", VOICE_LONG_TASK_OFFER_SECONDS_DEFAULT, 30, 300)
        deadline = 600 if codex_console._codex_agent_is_report_request(job_prompt) else 180
        next_progress = started + progress_interval
        offered = False
        latest: dict[str, Any] = task
        while time.time() - started < deadline:
            loaded = codex_console._codex_load_task(task_id)
            if isinstance(loaded, dict):
                latest = loaded
            if str(latest.get("status") or "") in VOICE_TERMINAL_TASK_STATES:
                break
            now = time.time()
            if now >= next_progress:
                progress(self._progress_text(latest))
                next_progress = now + progress_interval
            if not offered and now - started >= offer_after:
                offered = True
                progress("Esta consulta está demorando mais que o normal. Quer continuar aguardando na ligação ou receber o resultado por mensagem?", choice=True)
            time.sleep(0.5)
        else:
            try:
                codex_console.codex_cancelar_tarefa_para_sessao(task_id, session, cancel_source="whatsapp_voice_deadline")
            except Exception:
                pass
            loaded = codex_console._codex_load_task(task_id)
            if isinstance(loaded, dict):
                latest = loaded
        worker_result = codex_whatsapp_agents.normalize_worker_result(latest)
        final_decision = bridge._run_conversation_agent(
            config,
            local_state,
            conversation_id,
            event_type="worker_result",
            user_message=transcript,
            worker_result=worker_result,
            ai_behavior=ai_behavior,
        )
        answer = _safe_spoken_text(final_decision.get("reply_text") or worker_result.get("summary") or "Não consegui concluir a consulta.")
        existing = codex_console._codex_load_task(task_id) or latest
        delivery = self._deliver_requested_listing(config, bridge, call, existing, transcript, answer)
        if delivery.get("text_sent") is True:
            state["result_message_sent"] = True
            sent_images = int(delivery.get("images_sent") or 0)
            if delivery.get("pictures_requested") is True and sent_images > 0:
                answer = _safe_spoken_text(f"{answer} Enviei o link, os detalhes e {sent_images} foto(s) no seu WhatsApp.")
            elif delivery.get("pictures_requested") is True:
                answer = _safe_spoken_text(f"{answer} Enviei o link e os detalhes no seu WhatsApp, mas as fotos ficaram indisponiveis.")
            else:
                answer = _safe_spoken_text(f"{answer} Enviei os detalhes no seu WhatsApp.")
        elif delivery.get("attempted") is True:
            answer = _safe_spoken_text(f"{answer} Nao consegui enviar os detalhes no WhatsApp nesta tentativa.")
        metadata = dict(existing.get("channel_metadata") or {})
        metadata.update({
            "wa_id": phone,
            "phone": phone,
            "message_type": "voice_call",
            "interaction_type": "call",
            "source": "whatsapp_call",
            "call_id": str(call.get("id") or "")[:200],
            "safe_read_only": True,
        })
        if str(existing.get("status") or "") == "completed":
            codex_console._codex_update_task(
                task_id,
                prompt=transcript,
                final_response=answer,
                completed_at=str(existing.get("completed_at") or codex_console._codex_now()),
                channel_metadata=metadata,
                access_mode="query_only",
                whatsapp_query_only=True,
                external_safe_mode=True,
                approval_required=False,
                approved=True,
            )
            try:
                codex_console._codex_update_conversation_memory(task_id)
            except Exception:
                pass
        else:
            # A explicação entregue ao usuário é um par concluído, mas a tarefa
            # técnica conserva seu estado falho/cancelado para auditoria e não
            # pode ser reclassificada como sucesso.
            delivered = codex_console.codex_registrar_interacao_whatsapp_externa(
                client_id=str(session.get("client_id") or "default"),
                username=str(session.get("username") or ""),
                phone=phone,
                prompt=transcript,
                response=answer,
                model=str(existing.get("model") or "black-jhon-voice"),
                call_id=str(call.get("id") or ""),
            )
            state["task_ids"].append(str(delivered.get("task_id") or ""))
        return answer

    @staticmethod
    def _deliver_requested_listing(
        config: dict[str, Any],
        bridge: Any,
        call: dict[str, Any],
        task: dict[str, Any],
        transcript: str,
        answer: str,
    ) -> dict[str, Any]:
        from backend.services.whatsapp import artifacts as whatsapp_artifacts
        from backend.services.whatsapp import marketplace_listing_delivery

        if not marketplace_listing_delivery.delivery_requested(transcript):
            return {"attempted": False, "text_sent": False, "images_sent": 0}
        subject_id = str(call.get("subject_id") or "").strip()
        task_id = str(task.get("task_id") or "").strip()
        call_id = str(call.get("id") or "").strip()
        if not subject_id or not task_id or not call_id:
            return {"attempted": True, "text_sent": False, "images_sent": 0}
        bundle = task.get("whatsapp_listing_bundle") if isinstance(task.get("whatsapp_listing_bundle"), dict) else {}
        delivery_text = marketplace_listing_delivery.format_listing_bundle(bundle, transcript) if bundle else ""
        delivery_text = str(delivery_text or task.get("final_response") or answer or "").strip()[:3500]
        fingerprint_seed = hashlib.sha256(f"{call_id}\n{task_id}".encode("utf-8")).hexdigest()[:48]
        try:
            text_result = bridge._post_proactive(
                config,
                {
                    "subject_id": subject_id,
                    "fingerprint": f"voice-task:{fingerprint_seed}",
                    "event_type": "task_completed",
                    "severity": "info",
                    "text": delivery_text,
                },
            )
        except Exception:
            text_result = {"success": False, "status": "delivery_failed"}
        text_sent = bool(isinstance(text_result, dict) and text_result.get("success") is True)
        pictures_requested = marketplace_listing_delivery.pictures_requested(transcript)
        image_results: list[dict[str, Any]] = []
        if text_sent and pictures_requested and bundle:
            image_results = whatsapp_artifacts._whatsapp_deliver_marketplace_listing_images_proactive(
                config,
                subject_id=subject_id,
                listing_bundle=bundle,
                request_text=transcript,
                fingerprint_seed=fingerprint_seed,
                max_images=3,
            )
        return {
            "attempted": True,
            "text_sent": text_sent,
            "pictures_requested": pictures_requested,
            "images_sent": sum(1 for item in image_results if item.get("success")),
            "images_attempted": len(image_results),
        }

    @staticmethod
    def _progress_text(task: dict[str, Any]) -> str:
        text = unicodedata_key(f"{task.get('live_status') or ''} {task.get('wait_reason') or ''}")
        if "fila" in text or "queue" in text:
            return "Sua consulta está na fila e será processada na ordem correta."
        if "pagina" in text:
            return "Estou percorrendo as páginas necessárias da consulta."
        if "valid" in text or "compar" in text:
            return "Já encontrei dados e estou conferindo a consistência antes de responder."
        if "api" in text or "consult" in text or "tool" in text:
            return "Estou consultando as fontes autorizadas para confirmar a resposta."
        return "Ainda estou analisando o pedido e validando as informações disponíveis."

    @staticmethod
    async def _speak(websocket: Any, text: Any) -> None:
        message = _safe_spoken_text(text)
        if not message:
            return
        await websocket.send(json.dumps({
            "type": "response.create",
            "response": {
                "output_modalities": ["audio"],
                "instructions": (
                    "Fale em portugues brasileiro e transmita fielmente a mensagem abaixo. "
                    "Nao acrescente fatos, titulos, assinatura ou explicacoes.\n\nMENSAGEM:\n" + message
                ),
            },
        }, ensure_ascii=False))

    @staticmethod
    def _hangup(openai_call_id: str, key: str) -> None:
        try:
            requests.post(
                f"https://api.openai.com/v1/realtime/calls/{openai_call_id}/hangup",
                headers={"authorization": f"Bearer {key}"},
                timeout=10,
            )
        except Exception:
            pass

    @staticmethod
    def _send_result_message(config: dict[str, Any], bridge: Any, call: dict[str, Any], answer: str) -> dict[str, Any]:
        return bridge._gateway_json(
            config,
            "POST",
            "/bridge/messages/send",
            {
                "phone_number": str(call.get("wa_id") or ""),
                "machine_id": str(config.get("machine_id") or ""),
                "text": _safe_spoken_text(answer, 3500),
            },
            timeout=20,
        )

    @staticmethod
    def _merge_usage(state: dict[str, Any], usage: Any) -> None:
        source = usage if isinstance(usage, dict) else {}
        totals = state.get("usage") if isinstance(state.get("usage"), dict) else {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            try:
                totals[key] = int(totals.get(key) or 0) + max(0, int(source.get(key) or 0))
            except (TypeError, ValueError):
                continue
        state["usage"] = totals


VOICE_RUNTIME = VoiceRuntime()


__all__ = [
    "VOICE_RUNTIME",
    "VOICE_MODEL_DEFAULT",
    "VOICE_TRANSCRIPTION_MODEL_DEFAULT",
    "VOICE_NAME_DEFAULT",
    "openai_key_fingerprint",
]
