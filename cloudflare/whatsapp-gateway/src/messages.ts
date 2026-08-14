import { compactReply, compactReplyParts, isFreeWindowOpen, isPolicyValid } from "./core";
import { deliveryReceipt, flushOutbox, inboundResultReceipt, queueInboundResultPart, queueOutbound } from "./delivery";
import { inboundMediaAudit, releaseMedia } from "./inbound-media";
import { audit, counterReserveBelow, dayKey, Env, graphRequest, intEnv, json, JsonRecord, nowSeconds, randomId, requestJson, responsePayload } from "./shared";

export const INBOUND_MESSAGE_LEASE_SECONDS = 10 * 60;

export async function claimMessages(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const machineId = String(body.machine_id || "").trim();
  const limit = Math.max(1, Math.min(5, Number(body.limit || 5)));
  if (!machineId) return json({ success: false, error: "machine_id_required" }, 400);
  const now = nowSeconds();
  await env.DB.batch([
    env.DB.prepare("UPDATE inbox SET status='dead_letter',error='retry_limit',completed_at=?,lease_owner=NULL,lease_until=NULL WHERE status='leased' AND lease_until<? AND attempts>=4").bind(now, now),
    env.DB.prepare("UPDATE inbox SET status='queued',lease_owner=NULL,lease_until=NULL WHERE status='leased' AND lease_until<? AND attempts<4").bind(now),
  ]);
  const rows = await env.DB.prepare(
    "SELECT i.*,COALESCE(NULLIF(i.wa_id,''),b.wa_id) AS wa_id,b.client_id,b.username,b.machine_id,b.is_primary AS binding_is_primary FROM inbox i JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 WHERE i.status='queued' AND b.machine_id=? ORDER BY i.received_at LIMIT ?",
  ).bind(machineId, limit).all<JsonRecord>();
  const claimed: JsonRecord[] = [];
  for (const row of rows.results || []) {
    const messageId = String(row.message_id || "");
    const result = await env.DB.prepare("UPDATE inbox SET status='leased',lease_owner=?,lease_until=?,attempts=attempts+1 WHERE message_id=? AND status='queued'")
      .bind(machineId, now + INBOUND_MESSAGE_LEASE_SECONDS, messageId).run();
    if (result.meta.changes) claimed.push({ ...row, status: "leased", lease_until: now + INBOUND_MESSAGE_LEASE_SECONDS });
  }
  return json({ success: true, messages: claimed });
}

export async function renewInboundMessageLease(env: Env, messageId: string, machineId: string, now: number): Promise<boolean> {
  const renewed = await env.DB.prepare(
    "UPDATE inbox SET lease_until=? WHERE message_id=? AND status='leased' AND lease_owner=?",
  ).bind(now + INBOUND_MESSAGE_LEASE_SECONDS, messageId, machineId).run();
  return Number(renewed.meta.changes || 0) > 0;
}

export async function messageTyping(request: Request, env: Env, messageId: string): Promise<Response> {
  const body = await requestJson(request);
  const machineId = String(body.machine_id || "").trim();
  if (!machineId) return json({ success: false, error: "machine_id_required" }, 400);
  const row = await env.DB.prepare(
    "SELECT i.message_id,i.subject_id,i.status,i.typing_last_at,b.machine_id,b.last_inbound_at "
    + "FROM inbox i JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 WHERE i.message_id=?",
  ).bind(messageId).first<JsonRecord>();
  if (!row) return json({ success: false, error: "message_not_owned" }, 404);
  if (String(row.machine_id || "") !== machineId) return json({ success: false, error: "binding_machine_mismatch" }, 403);
  if (String(row.status || "") !== "leased") return json({ success: true, status: "not_active" });
  const now = nowSeconds();
  if (!(await renewInboundMessageLease(env, messageId, machineId, now))) {
    return json({ success: true, status: "not_active" });
  }
  if (!isPolicyValid(env.ZERO_COST_POLICY_VALID_UNTIL)) {
    return json({ success: false, status: "policy_recheck_required", error: "policy_recheck_required", lease_renewed: true }, 409);
  }
  if (!isFreeWindowOpen(Number(row.last_inbound_at || 0), now, intEnv(env.FREE_WINDOW_SECONDS, 84600))) {
    return json({ success: false, status: "waiting_free_window", error: "waiting_free_window", lease_renewed: true }, 409);
  }
  if (Number(row.typing_last_at || 0) > now - 15) return json({ success: true, status: "too_soon", lease_renewed: true });
  const throttle = await env.DB.prepare(
    "UPDATE inbox SET typing_last_at=? WHERE message_id=? AND status='leased' AND (typing_last_at IS NULL OR typing_last_at<=?)",
  ).bind(now, messageId, now - 15).run();
  if (!Number(throttle.meta.changes || 0)) return json({ success: true, status: "too_soon", lease_renewed: true });
  const dailyLimit = intEnv(env.TYPING_PULSES_DAY_LIMIT, 10000);
  if (!(await counterReserveBelow(env, `typing_pulses:${dayKey()}`, dailyLimit))) {
    return json({ success: true, status: "daily_limit", limit: dailyLimit, lease_renewed: true });
  }
  const response = await graphRequest(env, `${env.META_PHONE_NUMBER_ID}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      messaging_product: "whatsapp",
      status: "read",
      message_id: messageId,
      typing_indicator: { type: "text" },
    }),
  });
  const payload = await responsePayload(response);
  if (!response.ok) {
    await audit(env, "typing_indicator_failed", String(row.subject_id || ""), {
      message_id: messageId,
      status: response.status,
    });
    return json({ success: false, error: "meta_typing_indicator_failed", meta: payload, lease_renewed: true }, 502);
  }
  return json({ success: true, status: "sent", lease_renewed: true });
}

export async function messageProgress(request: Request, env: Env, messageId: string): Promise<Response> {
  const body = await requestJson(request);
  const machineId = String(body.machine_id || "").trim();
  const taskId = String(body.task_id || "").trim().slice(0, 160);
  const stage = String(body.stage || "processando").trim().slice(0, 80);
  const textBody = compactReply(body.text || "", 1200);
  const fingerprint = String(body.fingerprint || "").trim().slice(0, 160);
  const sequence = Math.max(1, Number.parseInt(String(body.sequence || "1"), 10) || 1);
  if (!machineId || !taskId || !textBody || !fingerprint) {
    return json({ success: false, error: "progress_payload_required" }, 400);
  }
  const row = await env.DB.prepare(
    "SELECT i.message_id,i.subject_id,i.wa_id,i.status,b.machine_id,b.last_inbound_at "
    + "FROM inbox i JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 WHERE i.message_id=?",
  ).bind(messageId).first<JsonRecord>();
  if (!row) return json({ success: false, error: "message_not_owned" }, 404);
  if (String(row.machine_id || "") !== machineId) return json({ success: false, error: "binding_machine_mismatch" }, 403);
  if (String(row.status || "") !== "leased") return json({ success: true, status: "not_active" });
  const leaseNow = nowSeconds();
  if (!(await renewInboundMessageLease(env, messageId, machineId, leaseNow))) {
    return json({ success: true, status: "not_active" });
  }
  if (!isPolicyValid(env.ZERO_COST_POLICY_VALID_UNTIL)) {
    return json({ success: false, status: "policy_recheck_required", error: "policy_recheck_required" }, 409);
  }
  if (!isFreeWindowOpen(Number(row.last_inbound_at || 0), nowSeconds(), intEnv(env.FREE_WINDOW_SECONDS, 84600))) {
    return json({ success: false, status: "waiting_free_window", error: "waiting_free_window" }, 409);
  }
  const existing = await env.DB.prepare(
    "SELECT status,outbox_id FROM message_progress WHERE fingerprint=?",
  ).bind(fingerprint).first<JsonRecord>();
  if (existing && String(existing.status || "") !== "failed") {
    return json({ success: true, status: "duplicate", outbox_id: String(existing.outbox_id || "") });
  }
  const progressId = existing ? "" : randomId("progress");
  if (!existing) {
    await env.DB.prepare(
      "INSERT INTO message_progress(id,message_id,task_id,sequence,fingerprint,stage,text_body,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
    ).bind(progressId, messageId, taskId, sequence, fingerprint, stage, textBody, "preparing", nowSeconds()).run();
  }
  try {
    const active = await env.DB.prepare("SELECT status FROM inbox WHERE message_id=?").bind(messageId).first<JsonRecord>();
    if (String(active?.status || "") !== "leased") {
      await env.DB.prepare("UPDATE message_progress SET status='superseded',error=NULL WHERE fingerprint=?").bind(fingerprint).run();
      return json({ success: true, status: "not_active" });
    }
    const outboxId = await queueOutbound(
      env,
      String(row.subject_id || ""),
      String(row.wa_id || row.subject_id || ""),
      textBody,
      `task_progress:${taskId}:${sequence}`,
    );
    await env.DB.batch([
      env.DB.prepare("UPDATE outbox SET inbound_message_id=?,message_type='progress' WHERE id=?").bind(messageId, outboxId),
      env.DB.prepare("UPDATE message_progress SET status='queued',outbox_id=?,error=NULL WHERE fingerprint=?").bind(outboxId, fingerprint),
    ]);
    await flushOutbox(env, String(row.subject_id || ""), 3);
    const delivery = await env.DB.prepare("SELECT status,error,sent_at FROM outbox WHERE id=?").bind(outboxId).first<JsonRecord>();
    const deliveryStatus = String(delivery?.status || "queued");
    await env.DB.prepare("UPDATE message_progress SET status=?,error=?,sent_at=? WHERE fingerprint=?")
      .bind(deliveryStatus, String(delivery?.error || "") || null, Number(delivery?.sent_at || 0) || null, fingerprint).run();
    await audit(env, "task_progress", String(row.subject_id || ""), { message_id: messageId, task_id: taskId, sequence, stage, status: deliveryStatus });
    return json({ success: true, status: deliveryStatus, outbox_id: outboxId });
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    await env.DB.prepare("UPDATE message_progress SET status='failed',error=? WHERE fingerprint=?").bind(detail.slice(0, 800), fingerprint).run();
    return json({ success: false, error: "progress_delivery_failed", detail: detail.slice(0, 500) }, 502);
  }
}

export async function messageResult(request: Request, env: Env, messageId: string): Promise<Response> {
  const body = await requestJson(request);
  const machineId = String(body.machine_id || "").trim();
  const row = await env.DB.prepare(
    "SELECT i.*,b.machine_id FROM inbox i JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 WHERE i.message_id=?",
  ).bind(messageId).first<JsonRecord>();
  if (!row || String(row.machine_id || "") !== machineId) return json({ success: false, error: "message_not_owned" }, 404);
  const requestedStatus = String(body.status || "");
  const currentStatus = String(row.status || "");
  if (["completed", "failed", "awaiting_approval"].includes(currentStatus)) {
    await flushOutbox(env, String(row.subject_id || ""), 12);
    const deliveryReceipt = await inboundResultReceipt(env, messageId);
    return json({ success: true, status: currentStatus, queued_parts: deliveryReceipt.parts_total, idempotent_replay: true, delivery_receipt: deliveryReceipt });
  }
  if (requestedStatus === "retry") {
    const diagnostic = compactReply(body.error || "codex_temporarily_unavailable", 800);
    await env.DB.prepare("UPDATE inbox SET status='queued',error=?,lease_owner=NULL,lease_until=NULL WHERE message_id=? AND status='leased'")
      .bind(diagnostic, messageId).run();
    return json({ success: true, status: "queued_for_retry", queued_parts: 0 });
  }
  const status = ["completed", "failed", "awaiting_approval"].includes(requestedStatus) ? requestedStatus : "failed";
  const diagnostic = compactReply(body.error || body.response || "falha local", 800);
  const responseFallback = status === "failed"
    ? body.response || "Nao consegui concluir esta solicitacao. O erro tecnico ficou registrado no JK Sistema."
    : body.response || "";
  const responseParts = compactReplyParts(
    body.response_parts,
    responseFallback,
    body.allow_full_history === true ? 0 : 30,
  );
  await env.DB.prepare(
    "UPDATE outbox SET status='superseded',updated_at=?,error=NULL WHERE inbound_message_id=? AND message_type='progress' AND status IN ('queued','retry','waiting_free_window')",
  ).bind(nowSeconds(), messageId).run();
  await env.DB.prepare(
    "UPDATE message_progress SET status='superseded',error=NULL WHERE message_id=? AND status IN ('preparing','queued','retry','waiting_free_window')",
  ).bind(messageId).run();
  await env.DB.prepare("UPDATE inbox SET status=?,task_id=?,error=?,completed_at=?,lease_owner=NULL,lease_until=NULL WHERE message_id=?")
    .bind(status, String(body.task_id || "") || null, status === "failed" ? diagnostic : null, nowSeconds(), messageId).run();
  if (responseParts.length) {
    const templateName = String(body.template_name || "");
    const params = Array.isArray(body.template_params) ? body.template_params.map(String) : [];
    for (const [index, responseText] of responseParts.entries()) {
      await queueInboundResultPart(
        env,
        messageId,
        String(row.subject_id || ""),
        String(row.wa_id || row.subject_id || ""),
        responseText,
        `${status}:${index + 1}/${responseParts.length}`,
        index + 1,
        responseParts.length === 1 ? templateName : "",
        responseParts.length === 1 ? params : [],
      );
    }
    await flushOutbox(env, String(row.subject_id || ""), Math.min(12, Math.max(3, responseParts.length + 1)));
  }
  try {
    await releaseMedia(env, messageId);
  } catch {
    await inboundMediaAudit(env, "media_release_deferred", { terminal_status: status });
  }
  const deliveryReceipt = await inboundResultReceipt(env, messageId, responseParts.length);
  return json({ success: true, status, queued_parts: responseParts.length, delivery_receipt: deliveryReceipt });
}
