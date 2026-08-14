import { normalizeRegisteredPhone, zeroCostEligibility } from "./bindings";
import { compactReply, outboundRecipient, safeTemplate } from "./core";
import { inboundMediaSchemaMissing } from "./inbound-media";
import { QUESTION_SUGGESTION_OPEN_PAYLOAD, QUESTION_SUGGESTION_TEMPLATE_NAME } from "./question-templates";
import { audit, Env, graphRequest, json, JsonRecord, nowSeconds, randomId, requestJson, retryDelaySeconds } from "./shared";

export async function queueOutbound(env: Env, subjectId: string, recipient: string, textBody: string, reason: string, templateName = "", templateParams: string[] = []): Promise<string> {
  const id = randomId("out");
  const now = nowSeconds();
  await env.DB.prepare(
    "INSERT INTO outbox(id,subject_id,recipient,message_type,text_body,template_name,template_params_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
  ).bind(id, subjectId, recipient, templateName ? "template" : "text", compactReply(textBody), templateName || null, JSON.stringify(templateParams || []), "queued", now, now).run();
  await audit(env, "outbox_queued", subjectId, { outbox_id: id, reason, template_name: templateName || "" });
  return id;
}

export async function queueInboundResultPart(
  env: Env,
  messageId: string,
  subjectId: string,
  recipient: string,
  textBody: string,
  reason: string,
  partIndex: number,
  templateName = "",
  templateParams: string[] = [],
): Promise<string> {
  const idempotencyKey = `inbound_result:${messageId}:${partIndex}`;
  const id = randomId("out");
  const now = nowSeconds();
  try {
    await env.DB.prepare(
      "INSERT OR IGNORE INTO outbox(id,inbound_message_id,subject_id,recipient,message_type,text_body,template_name,template_params_json,status,created_at,updated_at,idempotency_key) "
      + "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
    ).bind(
      id,
      messageId,
      subjectId,
      recipient,
      templateName ? "template" : "text",
      compactReply(textBody),
      templateName || null,
      JSON.stringify(templateParams || []),
      "queued",
      now,
      now,
      idempotencyKey,
    ).run();
    const stored = await env.DB.prepare("SELECT id FROM outbox WHERE idempotency_key=?").bind(idempotencyKey).first<JsonRecord>();
    const storedId = String(stored?.id || id);
    await audit(env, "outbox_queued", subjectId, { outbox_id: storedId, reason, template_name: templateName || "", idempotent: true });
    return storedId;
  } catch (error) {
    if (!inboundMediaSchemaMissing(error)) throw error;
    return queueOutbound(env, subjectId, recipient, textBody, reason, templateName, templateParams);
  }
}

export const DELIVERY_RECEIPT_SCHEMA = "jk.whatsapp.delivery-receipt.v1";

export function deliveryReceipt(rows: JsonRecord[], expectedParts = 0): JsonRecord {
  const statuses = rows.map((item) => String(item.status || "").toLowerCase());
  const total = Math.max(expectedParts, statuses.length);
  const sent = statuses.filter((status) => ["sent", "delivered", "read"].includes(status)).length;
  const failed = statuses.filter((status) => ["failed", "template_not_approved"].includes(status)).length;
  const pending = Math.max(0, total - sent - failed);
  const confirmed = total > 0 && sent === total;
  const terminal = confirmed || (total > 0 && pending === 0);
  return {
    schema_version: DELIVERY_RECEIPT_SCHEMA,
    state: confirmed ? "sent" : failed > 0 && pending === 0 ? "failed" : total > 0 ? "pending" : "none",
    confirmed,
    terminal,
    parts_total: total,
    parts_sent: sent,
    parts_pending: pending,
    parts_failed: failed,
  };
}

export async function inboundResultReceipt(env: Env, messageId: string, expectedParts = 0): Promise<JsonRecord> {
  const rows = await env.DB.prepare(
    "SELECT status FROM outbox WHERE inbound_message_id=? AND idempotency_key LIKE ? ORDER BY created_at",
  ).bind(messageId, `inbound_result:${messageId}:%`).all<JsonRecord>();
  return deliveryReceipt(rows.results || [], expectedParts);
}

export async function queueProactivePart(
  env: Env,
  subjectId: string,
  textBody: string,
  reason: string,
  fingerprint: string,
  partIndex: number,
  templateName = "",
  templateParams: string[] = [],
): Promise<string> {
  const idempotencyKey = `proactive:${fingerprint}:${partIndex}`;
  const id = randomId("out");
  const now = nowSeconds();
  await env.DB.prepare(
    "INSERT OR IGNORE INTO outbox(id,inbound_message_id,subject_id,recipient,message_type,text_body,template_name,template_params_json,status,created_at,updated_at,idempotency_key) "
    + "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
  ).bind(
    id,
    `proactive:${fingerprint}`,
    subjectId,
    subjectId,
    templateName ? "template" : "text",
    compactReply(textBody),
    templateName || null,
    JSON.stringify(templateParams || []),
    "queued",
    now,
    now,
    idempotencyKey,
  ).run();
  const stored = await env.DB.prepare("SELECT id FROM outbox WHERE idempotency_key=?").bind(idempotencyKey).first<JsonRecord>();
  const storedId = String(stored?.id || id);
  await audit(env, "outbox_queued", subjectId, { outbox_id: storedId, reason, template_name: templateName || "", idempotent: true });
  return storedId;
}

export async function proactiveReceipt(env: Env, fingerprint: string, expectedParts = 0): Promise<JsonRecord> {
  const rows = await env.DB.prepare(
    "SELECT status FROM outbox WHERE inbound_message_id=? ORDER BY created_at",
  ).bind(`proactive:${fingerprint}`).all<JsonRecord>();
  return deliveryReceipt(rows.results || [], expectedParts);
}

export async function maybeNotifyLocalUnavailable(
  env: Env,
  messageId: string,
  subjectId: string,
  machineId: string,
): Promise<void> {
  const heartbeat = machineId
    ? await env.DB.prepare("SELECT last_seen_at FROM bridge_heartbeats WHERE machine_id=?").bind(machineId).first<JsonRecord>()
    : null;
  if (Number(heartbeat?.last_seen_at || 0) > nowSeconds() - 30) return;
  const reserved = await env.DB.prepare(
    "UPDATE inbox SET offline_notified_at=? WHERE message_id=? AND offline_notified_at IS NULL",
  ).bind(nowSeconds(), messageId).run();
  if (!Number(reserved.meta.changes || 0)) return;
  const text = "O Black Jhon esta temporariamente indisponivel porque o aplicativo JK Sistema esta fechado ou sem conexao. Sua solicitacao ficou guardada e sera retomada automaticamente quando o aplicativo voltar.";
  const outboxId = await queueOutbound(env, subjectId, subjectId, text, "local_bridge_offline");
  await env.DB.prepare("UPDATE outbox SET inbound_message_id=? WHERE id=?").bind(messageId, outboxId).run();
  await flushOutbox(env, subjectId, 3);
  await audit(env, "local_bridge_offline_notified", subjectId, { message_id: messageId, machine_id: machineId });
}

export async function sendOutboxItem(env: Env, item: JsonRecord): Promise<void> {
  const id = String(item.id || "");
  const subjectId = String(item.subject_id || "");
  const templateName = String(item.template_name || "");
  let template: JsonRecord | null = null;
  if (templateName) {
    template = await env.DB.prepare("SELECT name,language,category,status FROM template_registry WHERE name=?").bind(templateName).first<JsonRecord>();
    if (!template || !safeTemplate(template.name, template.category, template.status)) {
      const templateStatus = String(template?.status || "MISSING").toUpperCase();
      await env.DB.prepare("UPDATE outbox SET status='template_not_approved',updated_at=?,error=? WHERE id=?")
        .bind(nowSeconds(), `template_${templateStatus.toLowerCase()}`, id).run();
      await audit(env, "outbox_template_blocked", subjectId, { outbox_id: id, template_name: templateName, template_status: templateStatus });
      return;
    }
  }
  let eligibility = await zeroCostEligibility(env, subjectId, String(item.message_type || "") === "adhoc_text");
  if (!eligibility.allowed && eligibility.reason === "waiting_free_window" && template) {
    eligibility = { allowed: true, reason: "approved_utility_template", binding: eligibility.binding };
  }
  if (!eligibility.allowed) {
    await env.DB.prepare("UPDATE outbox SET status=?,updated_at=?,error=? WHERE id=?").bind(eligibility.reason, nowSeconds(), eligibility.reason, id).run();
    return;
  }
  const recipient = outboundRecipient(item.recipient, subjectId, eligibility.binding);
  if (!recipient) {
    await env.DB.prepare("UPDATE outbox SET status='failed',updated_at=?,error='recipient_missing' WHERE id=?").bind(nowSeconds(), id).run();
    await audit(env, "outbox_failed", subjectId, { outbox_id: id, error: "recipient_missing" });
    return;
  }
  let body: JsonRecord;
  if (templateName) {
    let params: unknown[] = [];
    try { params = JSON.parse(String(item.template_params_json || "[]")) as unknown[]; } catch { params = []; }
    const components: JsonRecord[] = [{
      type: "body",
      parameters: params.map((value) => ({ type: "text", text: String(value || "").slice(0, 500) })),
    }];
    if (templateName === QUESTION_SUGGESTION_TEMPLATE_NAME) {
      components.push({
        type: "button",
        sub_type: "quick_reply",
        index: "0",
        parameters: [{ type: "payload", payload: QUESTION_SUGGESTION_OPEN_PAYLOAD }],
      });
    }
    body = {
      messaging_product: "whatsapp",
      recipient_type: "individual",
      to: recipient,
      type: "template",
      template: {
        name: templateName,
        language: { code: String(template?.language || "pt_BR") },
        components,
      },
    };
  } else {
    body = { messaging_product: "whatsapp", recipient_type: "individual", to: recipient, type: "text", text: { preview_url: false, body: compactReply(item.text_body) } };
  }
  const response = await graphRequest(env, `${env.META_PHONE_NUMBER_ID}/messages`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const payload = (await response.json()) as JsonRecord;
  if (!response.ok) {
    const attempts = Number(item.attempts || 0) + 1;
    const status = attempts > 3 ? "failed" : "retry";
    await env.DB.prepare("UPDATE outbox SET status=?,attempts=?,updated_at=?,error=?,next_attempt_at=? WHERE id=?")
      .bind(status, attempts, nowSeconds(), JSON.stringify(payload).slice(0, 800), status === "retry" ? nowSeconds() + retryDelaySeconds(attempts) : null, id).run();
    return;
  }
  const messages = Array.isArray(payload.messages) ? payload.messages : [];
  const metaId = String(((messages[0] || {}) as JsonRecord).id || "");
  await env.DB.prepare("UPDATE outbox SET status='sent',sent_at=?,updated_at=?,meta_message_id=?,error=NULL,next_attempt_at=NULL WHERE id=?").bind(nowSeconds(), nowSeconds(), metaId || null, id).run();
  await audit(env, "outbox_sent", subjectId, { outbox_id: id, meta_message_id: metaId });
}

export async function flushOutbox(env: Env, subjectId = "", limit = 10): Promise<void> {
  const query = subjectId
    ? "SELECT * FROM outbox WHERE subject_id=? AND status IN ('queued','retry','waiting_free_window') AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY created_at LIMIT ?"
    : "SELECT * FROM outbox WHERE status IN ('queued','retry','waiting_free_window') AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY created_at LIMIT ?";
  const statement = subjectId ? env.DB.prepare(query).bind(subjectId, nowSeconds(), limit) : env.DB.prepare(query).bind(nowSeconds(), limit);
  const rows = await statement.all<JsonRecord>();
  for (const item of rows.results || []) await sendOutboxItem(env, item);
}

export async function flushAdhocOutbox(env: Env, subjectId: string, limit = 10): Promise<void> {
  const rows = await env.DB.prepare(
    "SELECT * FROM outbox WHERE subject_id=? AND message_type='adhoc_text' AND status IN ('queued','retry','waiting_free_window') AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY created_at LIMIT ?",
  ).bind(subjectId, nowSeconds(), limit).all<JsonRecord>();
  for (const item of rows.results || []) await sendOutboxItem(env, item);
}

export async function notifyUnauthorizedAccess(env: Env, subjectId: string, waId: string, reason: string): Promise<string> {
  const phoneSubject = normalizeRegisteredPhone(waId || subjectId);
  if (!phoneSubject) return "invalid_phone";
  await audit(env, "unpaired_phone_message", phoneSubject, { source_subject_id: subjectId, reason });
  const since = nowSeconds() - 3600;
  const recent = await env.DB.prepare(
    "SELECT COUNT(*) AS total FROM audit_events WHERE event_type='unauthorized_access_notice' AND subject_id=? AND created_at>=?",
  ).bind(phoneSubject, since).first<{ total: number }>();
  if (Number(recent?.total || 0) >= 1) {
    await audit(env, "unauthorized_access_notice_throttled", phoneSubject, { reason });
    return "rate_limited";
  }
  const text = "Olá! Este número não possui permissão para acessar o Black Jhon. Solicite a um administrador do JK Sistema que cadastre e autorize este número.";
  const outboxId = await queueOutbound(env, phoneSubject, phoneSubject, text, "unauthorized_access_notice");
  await env.DB.prepare("UPDATE outbox SET message_type='adhoc_text' WHERE id=?").bind(outboxId).run();
  const queued = await env.DB.prepare("SELECT * FROM outbox WHERE id=?").bind(outboxId).first<JsonRecord>();
  if (queued) await sendOutboxItem(env, queued);
  const delivery = await env.DB.prepare("SELECT status,error,meta_message_id FROM outbox WHERE id=?")
    .bind(outboxId).first<JsonRecord>();
  const status = String(delivery?.status || "queued");
  await audit(env, "unauthorized_access_notice", phoneSubject, { outbox_id: outboxId, status, reason });
  return status;
}

export async function releaseWaitingSummary(env: Env, subjectId: string): Promise<void> {
  const waiting = await env.DB.prepare(
    "SELECT * FROM outbox WHERE subject_id=? AND status='waiting_free_window' AND message_type!='adhoc_text' ORDER BY created_at LIMIT 30",
  ).bind(subjectId).all<JsonRecord>();
  const rows = waiting.results || [];
  if (!rows.length) return;
  if (rows.length === 1) {
    await env.DB.prepare("UPDATE outbox SET status='queued',updated_at=?,error=NULL WHERE id=?")
      .bind(nowSeconds(), String(rows[0].id || "")).run();
    await sendOutboxItem(env, { ...rows[0], status: "queued" });
    return;
  }
  const lines = rows.slice(0, 12).map((item, index) => `${index + 1}. ${compactReply(item.text_body, 260)}`);
  const summary = compactReply(
    `Joao Pretinho guardou ${rows.length} atualizacoes enquanto a janela gratuita estava fechada:\n${lines.join("\n")}`,
  );
  for (const item of rows) {
    await env.DB.prepare("UPDATE outbox SET status='summarized',updated_at=?,error=NULL WHERE id=?")
      .bind(nowSeconds(), String(item.id || "")).run();
  }
  const summaryId = await queueOutbound(env, subjectId, subjectId, summary, "pending_free_window_summary");
  const summaryItem = await env.DB.prepare("SELECT * FROM outbox WHERE id=?").bind(summaryId).first<JsonRecord>();
  if (summaryItem) await sendOutboxItem(env, summaryItem);
}

export async function welcomeMessage(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const subjectId = String(body.subject_id || "").trim();
  const machineId = String(body.machine_id || "").trim();
  const rawText = String(body.text || "").replace(/\r\n/g, "\n").trim();
  if (!subjectId || !machineId || !rawText || rawText.length > 1000) {
    return json({ success: false, error: "invalid_welcome_payload" }, 400);
  }
  const binding = await env.DB.prepare("SELECT * FROM bindings WHERE subject_id=? AND active=1")
    .bind(subjectId).first<JsonRecord>();
  if (!binding) return json({ success: false, error: "binding_missing" }, 404);
  if (String(binding.machine_id || "") !== machineId) {
    return json({ success: false, error: "binding_machine_mismatch" }, 403);
  }

  const outboxId = await queueOutbound(env, subjectId, subjectId, rawText, "welcome_message");
  const queued = await env.DB.prepare("SELECT * FROM outbox WHERE id=?").bind(outboxId).first<JsonRecord>();
  if (queued) await sendOutboxItem(env, queued);
  const delivery = await env.DB.prepare("SELECT status,error,meta_message_id FROM outbox WHERE id=?")
    .bind(outboxId).first<JsonRecord>();
  const status = String(delivery?.status || "queued");
  await audit(env, "welcome_message_requested", subjectId, { outbox_id: outboxId, status });
  return json({
    success: status !== "failed",
    status,
    outbox_id: outboxId,
    meta_message_id: String(delivery?.meta_message_id || ""),
    error: String(delivery?.error || ""),
  });
}

export async function adhocMessage(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const phoneNumber = normalizeRegisteredPhone(body.phone_number);
  const machineId = String(body.machine_id || "").trim();
  const rawText = String(body.text || "").replace(/\r\n/g, "\n").trim();
  if (!phoneNumber || !machineId || !rawText || rawText.length > 3500) {
    return json({ success: false, error: "invalid_adhoc_message_payload" }, 400);
  }

  const outboxId = await queueOutbound(env, phoneNumber, phoneNumber, rawText, "adhoc_message");
  await env.DB.prepare("UPDATE outbox SET message_type='adhoc_text' WHERE id=?").bind(outboxId).run();
  const queued = await env.DB.prepare("SELECT * FROM outbox WHERE id=?").bind(outboxId).first<JsonRecord>();
  if (queued) await sendOutboxItem(env, queued);
  const delivery = await env.DB.prepare("SELECT status,error,meta_message_id FROM outbox WHERE id=?")
    .bind(outboxId).first<JsonRecord>();
  const status = String(delivery?.status || "queued");
  await audit(env, "adhoc_message_requested", phoneNumber, { outbox_id: outboxId, status, machine_id: machineId });
  return json({
    success: status !== "failed",
    status,
    outbox_id: outboxId,
    meta_message_id: String(delivery?.meta_message_id || ""),
    error: String(delivery?.error || ""),
  });
}
