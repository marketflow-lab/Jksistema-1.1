"""Background auto-renew worker for Renovacao."""

from __future__ import annotations

import datetime as dt
import os
import threading
import time

from backend.services import renovacao_context as ctx
from backend.services.renovacao_agendamento_store import (
    _renovacao_agendamento_sanitizar,
    _renovacao_agendamento_upsert_lista,
    _renovacao_agendamentos_carregar,
    _renovacao_agendamentos_salvar,
)
from backend.services.renovacao_promocoes import _renovacao_criar_ou_completar_proximo_mes
from backend.services.renovacao_promocoes_helpers import _renovacao_dias_mes, _renovacao_nome_proximo_mes

def _renovacao_data_eh_fim_de_mes(data: dt.date | None = None) -> bool:
    data = data or dt.date.today()
    ultimo_dia = _renovacao_dias_mes(data.year, data.month)
    return data.day >= max(1, ultimo_dia - 1)


def _renovacao_tenants_com_agendamento() -> list[str]:
    tenants = []
    try:
        for nome in os.listdir(ctx.PASTA_INFO):
            pasta = os.path.join(ctx.PASTA_INFO, nome)
            if not os.path.isdir(pasta):
                continue
            if os.path.exists(os.path.join(pasta, "renovacao_agendamentos.json")):
                tenants.append(nome)
    except Exception as exc:
        ctx.logger.warning("[RENOVACAO AGENDAMENTO] Falha ao listar tenants: %s", exc)
    return tenants


def _renovacao_processar_agendamentos_fim_mes(data: dt.date | None = None) -> None:
    data = data or dt.date.today()
    if not _renovacao_data_eh_fim_de_mes(data):
        return
    run_key = f"{data.year:04d}-{data.month:02d}"
    for client_id in _renovacao_tenants_com_agendamento():
        with ctx.RENOVACAO_AGENDAMENTO_LOCK:
            payload = _renovacao_agendamentos_carregar(client_id)
            agendamentos = [
                _renovacao_agendamento_sanitizar(entry)
                for entry in (payload.get("agendamentos") or [])
                if isinstance(entry, dict)
            ]
        mudou = False
        novos_agendamentos = []
        for idx, entry in enumerate(agendamentos):
            if not entry.get("enabled"):
                continue
            if entry.get("last_run_key") == run_key:
                continue
            loja = str(entry.get("loja") or "").strip()
            campanha_id = str(entry.get("campanha_id") or "").strip()
            if not loja or not campanha_id:
                continue
            try:
                ctx.logger.info("[RENOVACAO AGENDAMENTO] Executando tenant=%s loja=%s campanha=%s", client_id, loja, campanha_id)
                resultado = _renovacao_criar_ou_completar_proximo_mes(
                    client_id,
                    loja,
                    campanha_id,
                    entry.get("nome") or "",
                    entry.get("promotion_type") or "SELLER_CAMPAIGN",
                )
                agendamentos[idx]["last_run_key"] = run_key
                agendamentos[idx]["last_result"] = {
                    "executed_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "nova_campanha": resultado.get("nova_campanha") or {},
                    "campanha_existente": bool(resultado.get("campanha_existente")),
                    "total_origem": resultado.get("total_origem"),
                    "mlbs_ja_presentes": resultado.get("mlbs_ja_presentes"),
                    "faltantes": resultado.get("faltantes"),
                    "incluidos": resultado.get("incluidos"),
                    "falhas_total": resultado.get("falhas_total"),
                }
                agendamentos[idx]["last_error"] = ""
                nova_campanha = resultado.get("nova_campanha") or {}
                nova_campanha_id = str(nova_campanha.get("id") or "").strip()
                if nova_campanha_id:
                    agendamentos[idx]["enabled"] = False
                    novos_agendamentos.append({
                        "loja": loja,
                        "campanha_id": nova_campanha_id,
                        "nome": _renovacao_nome_proximo_mes(nova_campanha),
                        "promotion_type": str(nova_campanha.get("promotion_type") or "SELLER_CAMPAIGN").strip() or "SELLER_CAMPAIGN",
                        "enabled": True,
                        "last_run_key": run_key,
                    })
                mudou = True
            except Exception as exc:
                ctx.logger.exception("[RENOVACAO AGENDAMENTO] Falha tenant=%s loja=%s campanha=%s", client_id, loja, campanha_id)
                agendamentos[idx]["last_error"] = str(getattr(exc, "detail", None) or exc)
                mudou = True
        for novo in novos_agendamentos:
            agendamentos = _renovacao_agendamento_upsert_lista(
                agendamentos,
                loja=novo.get("loja"),
                campanha_id=novo.get("campanha_id"),
                enabled=True,
                nome=novo.get("nome") or "",
                promotion_type=novo.get("promotion_type") or "SELLER_CAMPAIGN",
                last_run_key=run_key,
                last_error="",
            )
            mudou = True
        if mudou:
            with ctx.RENOVACAO_AGENDAMENTO_LOCK:
                payload["agendamentos"] = agendamentos
                _renovacao_agendamentos_salvar(client_id, payload)


def _renovacao_agendamento_worker() -> None:
    time.sleep(8)
    while True:
        try:
            _renovacao_processar_agendamentos_fim_mes()
        except Exception:
            ctx.logger.exception("[RENOVACAO AGENDAMENTO] Falha inesperada no verificador")
        time.sleep(6 * 60 * 60)


def _renovacao_iniciar_agendamento_background():
    if ctx._agent_service_only():
        ctx.logger.info("[AGENT SERVICE] Agendamento de renovacao desativado neste servico.")
        return
    with ctx.RENOVACAO_AGENDAMENTO_LOCK:
        if ctx.RENOVACAO_AGENDAMENTO_THREAD_STARTED:
            return
        ctx.RENOVACAO_AGENDAMENTO_THREAD_STARTED = True
    threading.Thread(
        target=_renovacao_agendamento_worker,
        name="renovacao-agendamento-fim-mes",
        daemon=True,
    ).start()
