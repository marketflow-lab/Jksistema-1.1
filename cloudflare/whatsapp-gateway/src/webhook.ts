import { MAX_BINDINGS_PER_USER, normalizeRegisteredPhone, pairingAttemptsAllowed, registeredPhoneAliases } from "./bindings";
import { compactReply, pairingCodeFromText, sha256Hex, timingSafeEqualText, verifyMetaSignature } from "./core";
import { flushAdhocOutbox, flushOutbox, maybeNotifyLocalUnavailable, notifyUnauthorizedAccess, queueOutbound, releaseWaitingSummary } from "./delivery";
import { initializeInboundMedia } from "./inbound-media";
import { QUESTION_SUGGESTION_OPEN_PAYLOAD } from "./question-templates";
import { audit, Env, JsonRecord, nowSeconds } from "./shared";

export function extractMessageText(message: JsonRecord): string {
  const type = String(message.type || "");
  if (type === "text") return String((message.text as JsonRecord | undefined)?.body || "").trim();
  if (type === "button") {
    const button = (message.button as JsonRecord | undefined) || {};
    const payload = String(button.payload || "").trim();
    if (payload === QUESTION_SUGGESTION_OPEN_PAYLOAD) return payload;
    return String(button.text || payload || "").trim();
  }
  if (type === "interactive") {
    const interactive = (message.interactive as JsonRecord | undefined) || {};
    const reply = (interactive.button_reply as JsonRecord | undefined) || (interactive.list_reply as JsonRecord | undefined) || {};
    // O ID carrega o token opaco da decisao. O titulo e apenas visual e nao
    // identifica com seguranca qual aprovacao originou o clique.
    return String(reply.id || reply.title || "").trim();
  }
  return "";
}

export function subjectFromMessage(message: JsonRecord, value?: JsonRecord): string {
  const waId = String(message.from || "").trim();
  const contacts = Array.isArray(value?.contacts) ? value.contacts : [];
  const contact = contacts
    .map((item) => (item && typeof item === "object" ? item as JsonRecord : {}))
    .find((item) => !waId || String(item.wa_id || "") === waId) || {};
  return String(
    message.user_id
    || message.from_user_id
    || contact.user_id
    || contact.bsuid
    || contact.wa_id
    || message.from
    || "",
  ).trim();
}

export async function tryPair(env: Env, subjectId: string, waId: string, phone: string, textBody: string): Promise<boolean> {
  const code = pairingCodeFromText(textBody);
  if (!code) return false;
  if (!(await pairingAttemptsAllowed(env, subjectId))) {
    await audit(env, "pairing_rate_limited", subjectId);
    await notifyUnauthorizedAccess(env, subjectId, waId, "pairing_rate_limited");
    return true;
  }
  const codeHash = await sha256Hex(code);
  const now = nowSeconds();
  const item = await env.DB.prepare(
    "SELECT code_hash,client_id,username,machine_id,expires_at,used_at FROM pairing_codes WHERE code_hash=?",
  ).bind(codeHash).first<{ code_hash: string; client_id: string; username: string; machine_id: string; expires_at: number; used_at: number | null }>();
  if (!item || item.used_at || Number(item.expires_at || 0) < now) {
    await audit(env, "pairing_failed", subjectId, { reason: "invalid_or_expired" });
    await notifyUnauthorizedAccess(env, subjectId, waId, "pairing_invalid_or_expired");
    return true;
  }
  const results = await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO bindings(subject_id,wa_id,phone_number,client_id,username,machine_id,active,last_inbound_at,created_at)
       SELECT ?,?,?,?,?,?,1,?,?
       WHERE EXISTS(SELECT 1 FROM bindings WHERE subject_id=? AND client_id=? AND username=? AND active=1)
          OR (SELECT COUNT(*) FROM bindings WHERE client_id=? AND username=? AND active=1) < ?
       ON CONFLICT(subject_id) DO UPDATE SET wa_id=excluded.wa_id,phone_number=excluded.phone_number,client_id=excluded.client_id,username=excluded.username,machine_id=excluded.machine_id,active=1,last_inbound_at=excluded.last_inbound_at,revoked_at=NULL`,
    ).bind(
      subjectId, waId || null, phone || null, item.client_id, item.username, item.machine_id, now, now,
      subjectId, item.client_id, item.username,
      item.client_id, item.username, MAX_BINDINGS_PER_USER,
    ),
    env.DB.prepare(
      "UPDATE pairing_codes SET used_at=?,used_by_subject=? WHERE code_hash=? AND EXISTS(SELECT 1 FROM bindings WHERE subject_id=? AND client_id=? AND username=? AND active=1)",
    ).bind(now, subjectId, codeHash, subjectId, item.client_id, item.username),
  ]);
  if (!Number(results[0]?.meta?.changes || 0)) {
    await audit(env, "pairing_limit_reached", subjectId, { client_id: item.client_id, username: item.username, limit: MAX_BINDINGS_PER_USER });
    await notifyUnauthorizedAccess(env, subjectId, waId, "pairing_limit_reached");
    return true;
  }
  const active = await env.DB.prepare("SELECT COUNT(*) AS total FROM bindings WHERE client_id=? AND username=? AND active=1")
    .bind(item.client_id, item.username).first<{ total: number }>();
  const total = Number(active?.total || 0);
  await audit(env, "pairing_completed", subjectId, { client_id: item.client_id, username: item.username, machine_id: item.machine_id, active_bindings: total });
  await queueOutbound(env, subjectId, subjectId, `Vinculo concluido (${total}/${MAX_BINDINGS_PER_USER}). O Joao Pretinho ja pode receber suas mensagens.`, "pairing");
  await flushOutbox(env, subjectId, 1);
  return true;
}

export async function handleIncomingMessage(env: Env, ctx: ExecutionContext, value: JsonRecord, message: JsonRecord): Promise<void> {
  const messageId = String(message.id || "").trim();
  const incomingSubjectId = subjectFromMessage(message, value);
  if (!messageId || !incomingSubjectId) return;
  const metadata = (value.metadata as JsonRecord | undefined) || {};
  const messageType = String(message.type || "unknown").toLowerCase();
  const textBody = extractMessageText(message);
  const waId = String(message.from || "").trim();
  const phoneSubject = normalizeRegisteredPhone(waId);
  const receivedAt = Number.parseInt(String(message.timestamp || ""), 10) || nowSeconds();
  const media = (message[messageType] as JsonRecord | undefined) || {};
  const mediaId = ["image", "audio"].includes(messageType) ? String(media.id || "") : "";
  const mediaMime = String(media.mime_type || "");
  const quotedMessageId = String(((message.context as JsonRecord | undefined) || {}).id || "").trim().slice(0, 240);

  const existing = await env.DB.prepare("SELECT message_id FROM inbox WHERE message_id=?").bind(messageId).first();
  if (existing) return;

  if (textBody && (await tryPair(env, incomingSubjectId, waId, waId, textBody))) return;
  // Cadastros feitos pela tela usam o telefone como subject_id. Em mensagens
  // reais a Meta pode enviar um BSUID diferente; nesse caso o wa_id/telefone
  // continua sendo a identidade confiavel para recuperar o mesmo vinculo.
  let binding = await env.DB.prepare(
    "SELECT subject_id,machine_id,client_id,username FROM bindings WHERE active=1 AND (subject_id=? OR wa_id=? OR phone_number=?) LIMIT 1",
  ).bind(incomingSubjectId, waId, phoneSubject || waId).first<JsonRecord>();
  if (!binding) {
    const aliases = registeredPhoneAliases(phoneSubject || waId);
    const alternatePhone = aliases.find((candidate) => candidate !== phoneSubject && candidate !== waId) || "";
    if (alternatePhone) {
      binding = await env.DB.prepare(
        "SELECT subject_id,machine_id,client_id,username FROM bindings WHERE active=1 AND (subject_id=? OR wa_id=? OR phone_number=?) LIMIT 1",
      ).bind(alternatePhone, alternatePhone, alternatePhone).first<JsonRecord>();
    }
  }
  if (!binding) {
    await audit(env, "unpaired_message", incomingSubjectId, { message_id: messageId, type: messageType });
    const unpairedPhone = phoneSubject || normalizeRegisteredPhone(incomingSubjectId);
    if (unpairedPhone) {
      await notifyUnauthorizedAccess(env, incomingSubjectId, unpairedPhone, "unpaired_message");
      ctx.waitUntil(flushAdhocOutbox(env, unpairedPhone, 10));
    }
    return;
  }
  const subjectId = String(binding.subject_id || incomingSubjectId);
  let quotedText = "";
  if (quotedMessageId) {
    const clientId = String(binding.client_id || "").trim();
    const username = String(binding.username || "").trim().toLowerCase();
    const quotedInbox = await env.DB.prepare(
      "SELECT text_body FROM inbox WHERE message_id=? AND subject_id=? LIMIT 1",
    ).bind(quotedMessageId, subjectId).first<JsonRecord>();
    const quotedInteractive = quotedInbox?.text_body || !clientId || !username
      ? null
      : await env.DB.prepare(
        "SELECT text_body FROM outbound_quote_context WHERE meta_message_id=? AND subject_id=? AND client_id=? AND username=? LIMIT 1",
      ).bind(quotedMessageId, subjectId, clientId, username).first<JsonRecord>();
    const quotedOutbound = quotedInbox?.text_body || quotedInteractive?.text_body
      ? null
      : await env.DB.prepare(
        "SELECT text_body FROM outbox WHERE meta_message_id=? AND subject_id=? LIMIT 1",
      ).bind(quotedMessageId, subjectId).first<JsonRecord>();
    quotedText = compactReply(quotedInbox?.text_body || quotedInteractive?.text_body || quotedOutbound?.text_body || "", 3500);
  }

  const recent = await env.DB.prepare("SELECT COUNT(*) AS total FROM inbox WHERE subject_id=? AND received_at>=?").bind(subjectId, nowSeconds() - 3600).first<{ total: number }>();
  if (Number(recent?.total || 0) >= 30) {
    await audit(env, "inbound_rate_limited", subjectId, { message_id: messageId });
    return;
  }
  const allowedType = ["text", "button", "interactive", "image", "audio"].includes(messageType);
  const initialStatus = !allowedType ? "unsupported" : mediaId ? "media_fetching" : "queued";
  await env.DB.batch([
    env.DB.prepare(
      "INSERT INTO inbox(message_id,subject_id,wa_id,phone_number_id,message_type,text_body,media_id,media_mime,received_at,status,quoted_message_id,quoted_text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
    ).bind(
      messageId,
      subjectId,
      waId || null,
      String(metadata.phone_number_id || ""),
      messageType,
      textBody || null,
      mediaId || null,
      mediaMime || null,
      receivedAt,
      initialStatus,
      quotedMessageId || null,
      quotedText || null,
    ),
    env.DB.prepare("UPDATE bindings SET last_inbound_at=?,wa_id=COALESCE(NULLIF(?,''),wa_id) WHERE subject_id=?").bind(receivedAt, waId, subjectId),
  ]);
  await audit(env, "inbound_received", subjectId, { message_id: messageId, type: messageType });
  if (allowedType) {
    ctx.waitUntil(maybeNotifyLocalUnavailable(env, messageId, subjectId, String(binding.machine_id || "")));
  }
  if (mediaId) ctx.waitUntil(initializeInboundMedia(env, messageId, mediaId, messageType, mediaMime));
  ctx.waitUntil(releaseWaitingSummary(env, subjectId));
  // O cadastro pode usar a variante brasileira com o nono digito enquanto a
  // Meta entrega o wa_id sem ele. Libera as mensagens tanto pela identidade
  // canonica do vinculo quanto pela forma recebida, sem cruzar outros numeros.
  ctx.waitUntil(flushAdhocOutbox(env, subjectId, 10));
  if (phoneSubject && phoneSubject !== subjectId) ctx.waitUntil(flushAdhocOutbox(env, phoneSubject, 10));
}

export async function handleStatuses(env: Env, statuses: unknown): Promise<void> {
  if (!Array.isArray(statuses)) return;
  for (const raw of statuses) {
    const item = raw && typeof raw === "object" ? (raw as JsonRecord) : {};
    const id = String(item.id || "");
    const status = String(item.status || "");
    if (!id || !status) continue;
    const at = Number.parseInt(String(item.timestamp || ""), 10) || nowSeconds();
    await env.DB.batch([
      env.DB.prepare("INSERT OR IGNORE INTO message_status(meta_message_id,status,status_at,recipient_id,raw_json) VALUES(?,?,?,?,?)").bind(id, status, at, String(item.recipient_id || ""), JSON.stringify(item)),
      env.DB.prepare("UPDATE outbox SET status=CASE WHEN ? IN ('failed') THEN 'failed' WHEN ? IN ('sent','delivered','read') THEN ? ELSE status END,updated_at=?,error=CASE WHEN ?='failed' THEN ? ELSE error END WHERE meta_message_id=?")
        .bind(status, status, status, at, status, JSON.stringify(item.errors || []).slice(0, 800), id),
      env.DB.prepare(
        "UPDATE outbound_media SET status=CASE WHEN ?='failed' THEN 'failed' WHEN ? IN ('sent','delivered','read') THEN ? ELSE status END,"
        + "updated_at=?,error=CASE WHEN ?='failed' THEN ? ELSE error END,"
        + "lease_owner=CASE WHEN ? IN ('failed','sent','delivered','read') THEN NULL ELSE lease_owner END,"
        + "lease_until=CASE WHEN ? IN ('failed','sent','delivered','read') THEN NULL ELSE lease_until END WHERE meta_message_id=?",
      ).bind(status, status, status, at, status, JSON.stringify(item.errors || []).slice(0, 800), status, status, id),
    ]);
  }
}

export async function handleWebhook(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  if (request.method === "GET") {
    const url = new URL(request.url);
    const mode = url.searchParams.get("hub.mode") || "";
    const token = url.searchParams.get("hub.verify_token") || "";
    const challenge = url.searchParams.get("hub.challenge") || "";
    return mode === "subscribe" && timingSafeEqualText(token, env.META_VERIFY_TOKEN || "")
      ? new Response(challenge, { status: 200 })
      : new Response("Unauthorized", { status: 401 });
  }
  if (request.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
  const raw = await request.arrayBuffer();
  const signature = request.headers.get("x-hub-signature-256") || "";
  if (!(await verifyMetaSignature(raw, signature, env.META_APP_SECRET || ""))) return new Response("Unauthorized", { status: 401 });
  let payload: JsonRecord;
  try {
    payload = JSON.parse(new TextDecoder().decode(raw)) as JsonRecord;
  } catch {
    return new Response("Bad Request", { status: 400 });
  }
  const work: Promise<void>[] = [];
  for (const entryRaw of Array.isArray(payload.entry) ? payload.entry : []) {
    const entry = (entryRaw || {}) as JsonRecord;
    for (const changeRaw of Array.isArray(entry.changes) ? entry.changes : []) {
      const change = (changeRaw || {}) as JsonRecord;
      const value = ((change.value as JsonRecord | undefined) || {});
      work.push(handleStatuses(env, value.statuses));
      for (const messageRaw of Array.isArray(value.messages) ? value.messages : []) {
        work.push(handleIncomingMessage(env, ctx, value, (messageRaw || {}) as JsonRecord));
      }
    }
  }
  ctx.waitUntil(Promise.all(work).then(() => undefined));
  return new Response("EVENT_RECEIVED", { status: 200 });
}
