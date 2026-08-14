import { zeroCostEligibility } from "./bindings";
import { compactReply, compactReplyParts, normalizeSeverity, outboundRecipient } from "./core";
import { deliveryReceipt, flushOutbox, proactiveReceipt, queueProactivePart } from "./delivery";
import { QUESTION_SUGGESTION_OPEN_PAYLOAD, QUESTION_SUGGESTION_TEMPLATE_NAME } from "./question-templates";
import { audit, Env, graphRequest, json, JsonRecord, nowSeconds, requestJson, responsePayload } from "./shared";

export async function proactive(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const subjectId = String(body.subject_id || "").trim();
  const machineId = String(body.machine_id || "").trim();
  const fingerprint = String(body.fingerprint || "").trim();
  const eventType = String(body.event_type || "").trim();
  const rawSeverity = String(body.severity || "").trim().toLowerCase();
  const severity = normalizeSeverity(rawSeverity === "warning" ? "medium" : rawSeverity);
  const textParts = compactReplyParts(
    body.text_parts,
    body.text || "",
    body.allow_full_history === true ? 0 : 8,
  );
  const textBody = textParts[0] || "";
  if (!subjectId || !machineId || !fingerprint || !eventType || !textParts.length) return json({ success: false, error: "invalid_proactive_payload" }, 400);
  const boundMachine = await env.DB.prepare(
    "SELECT machine_id FROM bindings WHERE subject_id=? AND active=1",
  ).bind(subjectId).first<JsonRecord>();
  if (!boundMachine || String(boundMachine.machine_id || "") !== machineId) {
    await audit(env, "proactive_text_blocked", subjectId, { reason: "binding_machine_mismatch" });
    return json({ success: false, error: "binding_machine_mismatch" }, 403);
  }
  const isTask = ["task_completed", "task_failed", "task_partial", "task_awaiting_approval", "task_conversation"].includes(eventType);
  const isScheduledReport = ["weekly_report", "monthly_report"].includes(eventType);
  if (!isTask && !isScheduledReport && !["high", "critical"].includes(severity)) return json({ success: true, status: "ignored_low_severity" });
  const existing = await env.DB.prepare("SELECT fingerprint FROM proactive_events WHERE fingerprint=?").bind(fingerprint).first();
  if (existing) {
    await flushOutbox(env, subjectId, 12);
    const deliveryReceipt = await proactiveReceipt(env, fingerprint);
    if (deliveryReceipt.confirmed === true) {
      await env.DB.prepare("UPDATE proactive_events SET status='sent' WHERE fingerprint=?").bind(fingerprint).run();
    }
    return json({ success: true, status: "duplicate", delivery_receipt: deliveryReceipt });
  }
  if (!isTask && !isScheduledReport) {
    const sinceDay = nowSeconds() - 86400;
    const sinceCooldown = nowSeconds() - 7200;
    const counts = await env.DB.prepare("SELECT SUM(CASE WHEN created_at>=? THEN 1 ELSE 0 END) AS daily,SUM(CASE WHEN created_at>=? THEN 1 ELSE 0 END) AS cooldown FROM proactive_events WHERE subject_id=? AND event_type='operational_alert'")
      .bind(sinceDay, sinceCooldown, subjectId).first<{ daily: number; cooldown: number }>();
    if (Number(counts?.daily || 0) >= 3 || Number(counts?.cooldown || 0) >= 1) return json({ success: true, status: "rate_limited" });
  }
  const eligibility = await zeroCostEligibility(env, subjectId);
  await env.DB.prepare("INSERT INTO proactive_events(fingerprint,subject_id,event_type,severity,text_body,status,created_at) VALUES(?,?,?,?,?,?,?)")
    .bind(fingerprint, subjectId, (isTask || isScheduledReport) ? eventType : "operational_alert", severity, textBody, eligibility.allowed ? "queued" : eligibility.reason, nowSeconds()).run();
  const templateName = String(body.template_name || "");
  const params = Array.isArray(body.template_params) ? body.template_params.map(String) : [];
  for (const [index, part] of textParts.entries()) {
    await queueProactivePart(
      env,
      subjectId,
      part,
      `${eventType}:${index + 1}/${textParts.length}`,
      fingerprint,
      index + 1,
      textParts.length === 1 ? templateName : "",
      textParts.length === 1 ? params : [],
    );
  }
  await flushOutbox(env, subjectId, Math.min(12, Math.max(3, textParts.length + 1)));
  const deliveryReceipt = await proactiveReceipt(env, fingerprint, textParts.length);
  if (deliveryReceipt.confirmed === true) {
    await env.DB.prepare("UPDATE proactive_events SET status='sent' WHERE fingerprint=?").bind(fingerprint).run();
  }
  const deliveryStatus = deliveryReceipt.confirmed === true
    ? "sent"
    : eligibility.allowed ? "queued" : eligibility.reason;
  return json({ success: true, status: deliveryStatus, queued_parts: textParts.length, delivery_receipt: deliveryReceipt });
}

export async function interactiveApproval(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const subjectId = String(body.subject_id || "").trim();
  const machineId = String(body.machine_id || "").trim();
  const fingerprint = String(body.fingerprint || "").trim();
  const eventType = String(body.event_type || "").trim();
  const header = compactReply(body.header || "Black Jhon", 60);
  const textBody = compactReply(body.body || "", 1024);
  const footer = compactReply(body.footer || "", 60);
  const rawButtons = Array.isArray(body.buttons) ? body.buttons : [];
  const buttons = rawButtons.slice(0, 3).map((raw) => {
    const item = raw && typeof raw === "object" ? raw as JsonRecord : {};
    return { id: String(item.id || "").trim(), title: compactReply(item.title || "", 20) };
  });
  const rawOptions = Array.isArray(body.options) ? body.options : [];
  const options = rawOptions.slice(0, 10).map((raw) => {
    const item = raw && typeof raw === "object" ? raw as JsonRecord : {};
    return {
      id: String(item.id || "").trim(),
      title: compactReply(item.title || "", 24),
      description: compactReply(item.description || "", 72),
    };
  });
  const validStoreOptions = options.length >= 1 && options.length <= 10 && options.every((item) =>
    /^store_select:[A-Z2-9]{8}$/.test(item.id) && Boolean(item.title),
  );
  const approvalTitles: Record<string, string> = {
    approve: "Aprovar e enviar",
    correct: "Corrigir",
    regenerate: "Gerar outra resposta",
    reject: "Negar",
  };
  const validApprovalOptions = options.length === 4 && options.every((item) => {
    const match = item.id.match(/^ppv_(approve|correct|regenerate|reject):[A-Z2-9]{8}$/);
    return Boolean(match && approvalTitles[String(match[1] || "")] === item.title);
  });
  const validEventPayload = (
    eventType === "question_approval" && validApprovalOptions
  ) || (
    eventType === "store_selection" && validStoreOptions
  );
  if (
    !subjectId
    || !machineId
    || !/^[a-zA-Z0-9:_-]{16,160}$/.test(fingerprint)
    || !textBody
    || !validEventPayload
  ) {
    return json({ success: false, error: "invalid_interactive_payload" }, 400);
  }
  const eligibility = await zeroCostEligibility(env, subjectId);
  if (!eligibility.allowed) {
    await audit(env, `interactive_${eventType}_blocked`, subjectId, { reason: eligibility.reason });
    return json({ success: false, status: eligibility.reason, error: eligibility.reason }, 409);
  }
  if (String(eligibility.binding?.machine_id || "") !== machineId) {
    return json({ success: false, error: "binding_machine_mismatch" }, 403);
  }
  const recipient = outboundRecipient(subjectId, subjectId, eligibility.binding);
  if (!recipient) return json({ success: false, error: "recipient_missing" }, 400);
  const clientId = String(eligibility.binding?.client_id || "").trim();
  const username = String(eligibility.binding?.username || "").trim().toLowerCase();
  if (!clientId || !username) return json({ success: false, error: "binding_tenant_scope_missing" }, 409);
  const now = nowSeconds();
  const reservation = await env.DB.prepare(
    "INSERT OR IGNORE INTO proactive_events(fingerprint,subject_id,event_type,severity,text_body,status,created_at) VALUES(?,?,?,?,?,'processing',?)",
  ).bind(fingerprint, subjectId, eventType, "info", textBody, now).run();
  if (!Number(reservation.meta.changes || 0)) {
    const existingContext = await env.DB.prepare(
      "SELECT meta_message_id FROM outbound_quote_context WHERE fingerprint=? AND subject_id=? AND client_id=? AND username=? LIMIT 1",
    ).bind(fingerprint, subjectId, clientId, username).first<JsonRecord>();
    const existingMessageId = String(existingContext?.meta_message_id || "");
    if (existingMessageId) {
      return json({
        success: true,
        status: "duplicate",
        meta_message_id: existingMessageId,
        outbound_message_id: existingMessageId,
      });
    }
    return json({ success: true, status: "processing", meta_message_id: null, outbound_message_id: null }, 202);
  }
  const useList = eventType === "question_approval" || (eventType === "store_selection" && options.length > 3);
  const interactive: JsonRecord = useList
    ? {
      type: "list",
      body: { text: textBody },
      action: {
        button: compactReply(body.button_label || "Ver lojas", 20),
        sections: [{
          title: eventType === "question_approval" ? "Decisao" : "Lojas disponiveis",
          rows: options.map((item) => ({ id: item.id, title: item.title, description: item.description })),
        }],
      },
    }
    : {
      type: "button",
      body: { text: textBody },
      action: {
        buttons: (eventType === "store_selection" ? options : buttons).map((item) => ({
          type: "reply",
          reply: { id: item.id, title: compactReply(item.title, 20) },
        })),
      },
    };
  if (header) interactive.header = { type: "text", text: header };
  if (footer) interactive.footer = { text: footer };
  const response = await graphRequest(env, `${env.META_PHONE_NUMBER_ID}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      messaging_product: "whatsapp",
      recipient_type: "individual",
      to: recipient,
      type: "interactive",
      interactive,
    }),
  });
  const payload = await responsePayload(response);
  if (!response.ok) {
    await env.DB.prepare("DELETE FROM proactive_events WHERE fingerprint=? AND status='processing'").bind(fingerprint).run();
    await audit(env, `interactive_${eventType}_failed`, subjectId, { status: response.status });
    return json({ success: false, error: "meta_interactive_send_failed", meta: payload }, 502);
  }
  const messages = Array.isArray(payload.messages) ? payload.messages : [];
  const metaMessageId = String(((messages[0] || {}) as JsonRecord).id || "");
  if (!metaMessageId) {
    await env.DB.prepare("UPDATE proactive_events SET status='sent_untracked' WHERE fingerprint=?").bind(fingerprint).run();
    await audit(env, `interactive_${eventType}_untracked`, subjectId, { fingerprint });
    return json({ success: false, error: "meta_interactive_message_id_missing" }, 502);
  }
  const visibleOptions = options.map((item) => ({ title: item.title, description: item.description }));
  const actionLabel = compactReply(body.button_label || (useList ? "Ver lojas" : ""), 20);
  const quoteText = compactReply([
    header,
    textBody,
    actionLabel,
    ...visibleOptions.map((item) => item.description ? `${item.title} - ${item.description}` : item.title),
    footer,
  ].filter(Boolean).join("\n"), 3500);
  const quoteContext = JSON.stringify({
    version: 1,
    kind: "interactive",
    event_type: eventType,
    header,
    body: textBody,
    footer,
    action_label: actionLabel,
    options: visibleOptions,
  });
  await env.DB.batch([
    env.DB.prepare("UPDATE proactive_events SET status='sent' WHERE fingerprint=?").bind(fingerprint),
    env.DB.prepare(
      "INSERT INTO outbound_quote_context(meta_message_id,fingerprint,subject_id,client_id,username,event_type,text_body,context_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
    ).bind(metaMessageId, fingerprint, subjectId, clientId, username, eventType, quoteText, quoteContext, now),
  ]);
  await audit(env, `interactive_${eventType}_sent`, subjectId, { fingerprint, meta_message_id: metaMessageId });
  return json({ success: true, status: "sent", meta_message_id: metaMessageId, outbound_message_id: metaMessageId });
}
