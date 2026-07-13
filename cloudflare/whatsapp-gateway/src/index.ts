import {
  compactReply,
  compactReplyParts,
  isFreeWindowOpen,
  isPolicyValid,
  mediaPolicy,
  normalizeSeverity,
  outboundImagePolicy,
  outboundRecipient,
  pairingCodeFromText,
  profileImagePolicy,
  safeTemplate,
  sha256Hex,
  timingSafeEqualText,
  verifyMetaSignature,
} from "./core";

interface Env {
  DB: D1Database;
  MEDIA: KVNamespace;
  BRIDGE_TOKEN: string;
  META_VERIFY_TOKEN: string;
  META_APP_SECRET: string;
  META_ACCESS_TOKEN: string;
  META_SYSTEM_USER_TOKEN: string;
  META_WABA_ID: string;
  META_PHONE_NUMBER_ID: string;
  META_APP_ID: string;
  META_GRAPH_API_VERSION: string;
  ZERO_COST_POLICY_VALID_UNTIL: string;
  FREE_WINDOW_SECONDS?: string;
  MEDIA_ACTIVE_BYTES_LIMIT?: string;
  MEDIA_UPLOADS_MONTH_LIMIT?: string;
  MEDIA_UPLOADS_DAY_LIMIT?: string;
  MEDIA_DOWNLOADS_MONTH_LIMIT?: string;
  MEDIA_OUTBOUND_BYTES_MONTH_LIMIT?: string;
  MEDIA_OUTBOUND_UPLOADS_MONTH_LIMIT?: string;
  TYPING_PULSES_DAY_LIMIT?: string;
}

type JsonRecord = Record<string, unknown>;

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const GATEWAY_PROTOCOL_VERSION = 1;
const GATEWAY_BUILD_VERSION = "1.0.95";
const MAX_BINDINGS_PER_USER = 3;
const OUTBOUND_IMAGE_ARTIFACT_TYPES = new Set(["product_photo", "report_chart"]);
const PROACTIVE_IMAGE_FORM_FIELDS = new Set([
  "subject_id",
  "machine_id",
  "fingerprint",
  "event_type",
  "artifact_type",
  "caption",
  "sha256",
  "file",
]);
const TEMPLATE_DEFINITIONS = [
  {
    name: "jk_joao_tarefa_concluida",
    category: "UTILITY",
    language: "pt_BR",
    components: [{ type: "BODY", text: "Black Jhon concluiu sua solicitacao: {{1}}. Abra o JK Sistema para consultar o resultado completo.", example: { body_text: [["consulta concluida"]] } }],
  },
  {
    name: "jk_joao_aprovacao_pendente",
    category: "UTILITY",
    language: "pt_BR",
    components: [{ type: "BODY", text: "Black Jhon recebeu um pedido que exige aprovacao: {{1}}. Abra o JK Sistema para revisar.", example: { body_text: [["ajuste solicitado"]] } }],
  },
  {
    name: "jk_joao_alerta_operacional",
    category: "UTILITY",
    language: "pt_BR",
    components: [{ type: "BODY", text: "Alerta {{1}} do Black Jhon: {{2}}. Consulte o JK Sistema para detalhes.", example: { body_text: [["critico", "risco operacional detectado"]] } }],
  },
];

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

function intEnv(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function monthKey(now = new Date()): string {
  return `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
}

function dayKey(now = new Date()): string {
  return now.toISOString().slice(0, 10);
}

function monthStartSeconds(now = new Date()): number {
  return Math.floor(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1) / 1000);
}

function randomId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "")}`;
}

function bridgeAuthorized(request: Request, env: Env): boolean {
  const value = request.headers.get("authorization") || "";
  return value.startsWith("Bearer ") && timingSafeEqualText(value.slice(7).trim(), env.BRIDGE_TOKEN || "");
}

async function requestJson(request: Request): Promise<JsonRecord> {
  try {
    const parsed = await request.json();
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as JsonRecord) : {};
  } catch {
    return {};
  }
}

async function audit(env: Env, eventType: string, subjectId = "", detail: unknown = {}): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO audit_events(id,event_type,subject_id,detail_json,created_at) VALUES(?,?,?,?,?)",
  ).bind(randomId("audit"), eventType, subjectId || null, JSON.stringify(detail || {}), nowSeconds()).run();
}

async function counterGet(env: Env, key: string): Promise<number> {
  const row = await env.DB.prepare("SELECT counter_value FROM usage_counters WHERE counter_key=?").bind(key).first<{ counter_value: number }>();
  return Number(row?.counter_value || 0);
}

async function counterAdd(env: Env, key: string, delta: number): Promise<void> {
  const now = nowSeconds();
  await env.DB.prepare(
    "INSERT INTO usage_counters(counter_key,counter_value,updated_at) VALUES(?,?,?) ON CONFLICT(counter_key) DO UPDATE SET counter_value=MAX(0,counter_value+excluded.counter_value),updated_at=excluded.updated_at",
  ).bind(key, delta, now).run();
}

async function counterReserveBelow(env: Env, key: string, limit: number): Promise<boolean> {
  const result = await env.DB.prepare(
    "INSERT INTO usage_counters(counter_key,counter_value,updated_at) VALUES(?,1,?) "
    + "ON CONFLICT(counter_key) DO UPDATE SET counter_value=counter_value+1,updated_at=excluded.updated_at "
    + "WHERE counter_value<?",
  ).bind(key, nowSeconds(), Math.max(1, limit)).run();
  return Number(result.meta.changes || 0) > 0;
}

async function graphRequest(env: Env, path: string, init: RequestInit = {}): Promise<Response> {
  const version = String(env.META_GRAPH_API_VERSION || "").trim();
  if (!/^v\d+\.\d+$/.test(version)) throw new Error("META_GRAPH_API_VERSION ausente ou invalida");
  const headers = new Headers(init.headers || {});
  headers.set("authorization", `Bearer ${env.META_SYSTEM_USER_TOKEN}`);
  return fetch(`https://graph.facebook.com/${version}/${path.replace(/^\//, "")}`, { ...init, headers });
}

async function graphUploadRequest(env: Env, path: string, init: RequestInit = {}): Promise<Response> {
  const version = String(env.META_GRAPH_API_VERSION || "").trim();
  if (!/^v\d+\.\d+$/.test(version)) throw new Error("META_GRAPH_API_VERSION ausente ou invalida");
  const headers = new Headers(init.headers || {});
  // Meta's resumable binary-upload endpoint documents the OAuth scheme
  // explicitly; using Bearer can yield a valid-looking but unusable handle.
  headers.set("authorization", `OAuth ${env.META_SYSTEM_USER_TOKEN}`);
  return fetch(`https://graph.facebook.com/${version}/${path.replace(/^\//, "")}`, { ...init, headers });
}

async function responsePayload(response: Response): Promise<JsonRecord> {
  const raw = await response.text();
  try {
    const value = JSON.parse(raw);
    return value && typeof value === "object" ? value as JsonRecord : { value };
  } catch {
    return { message: raw.slice(0, 1000) };
  }
}

function profilePictureUrl(payload: JsonRecord): string {
  const data = Array.isArray(payload.data) ? payload.data : [];
  for (const raw of data) {
    const item = (raw || {}) as JsonRecord;
    const direct = String(item.profile_picture_url || "");
    if (direct) return direct;
    const nested = (item.business_profile || {}) as JsonRecord;
    const nestedUrl = String(nested.profile_picture_url || "");
    if (nestedUrl) return nestedUrl;
  }
  return "";
}

async function businessProfile(env: Env): Promise<Response> {
  const phoneId = String(env.META_PHONE_NUMBER_ID || "").trim();
  if (!/^\d+$/.test(phoneId)) return json({ success: false, error: "META_PHONE_NUMBER_ID_missing" }, 503);
  const response = await graphRequest(
    env,
    `${phoneId}/whatsapp_business_profile?fields=about,address,description,email,profile_picture_url,websites,vertical`,
  );
  const payload = await responsePayload(response);
  if (!response.ok) return json({ success: false, error: "meta_profile_read_failed", meta: payload }, 502);
  return json({ success: true, profile: payload, profile_picture_url: profilePictureUrl(payload) });
}

async function updateProfilePhoto(request: Request, env: Env): Promise<Response> {
  const contentType = String(request.headers.get("content-type") || "").split(";", 1)[0].trim().toLowerCase();
  const fileNameRaw = String(request.headers.get("x-file-name") || "black-jhon-profile.jpg").trim();
  const fileName = fileNameRaw.replace(/[^A-Za-z0-9._-]+/g, "-").slice(0, 120) || "black-jhon-profile.jpg";
  const file = await request.arrayBuffer();
  const policy = profileImagePolicy(contentType, file.byteLength, new Uint8Array(file.slice(0, 12)));
  if (!policy.allowed) return json({ success: false, error: policy.error }, 400);

  const appId = String(env.META_APP_ID || "").trim();
  const phoneId = String(env.META_PHONE_NUMBER_ID || "").trim();
  if (!/^\d+$/.test(appId) || !/^\d+$/.test(phoneId) || !env.META_SYSTEM_USER_TOKEN) {
    return json({ success: false, error: "meta_profile_configuration_missing" }, 503);
  }

  const sessionResponse = await graphRequest(
    env,
    `${appId}/uploads?file_name=${encodeURIComponent(fileName)}&file_length=${file.byteLength}&file_type=${encodeURIComponent(contentType)}`,
    { method: "POST" },
  );
  const sessionPayload = await responsePayload(sessionResponse);
  const uploadId = String(sessionPayload.id || "");
  if (!sessionResponse.ok || !uploadId.startsWith("upload:")) {
    return json({ success: false, stage: "create_upload_session", error: sessionPayload }, 502);
  }

  const uploadResponse = await graphUploadRequest(env, uploadId, {
    method: "POST",
    headers: { "content-type": contentType, "file_offset": "0" },
    body: file,
  });
  const uploadPayload = await responsePayload(uploadResponse);
  const handle = String(uploadPayload.h || "");
  if (!uploadResponse.ok || !handle) {
    return json({ success: false, stage: "upload_file", error: uploadPayload }, 502);
  }

  const updateResponse = await graphRequest(env, `${phoneId}/whatsapp_business_profile`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messaging_product: "whatsapp", profile_picture_handle: handle }),
  });
  const updatePayload = await responsePayload(updateResponse);
  if (!updateResponse.ok) {
    return json({ success: false, stage: "update_business_profile", error: updatePayload }, 502);
  }

  const verifyResponse = await graphRequest(env, `${phoneId}/whatsapp_business_profile?fields=profile_picture_url`);
  const verifyPayload = await responsePayload(verifyResponse);
  const digest = await crypto.subtle.digest("SHA-256", file);
  const sha256 = [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
  await audit(env, "business_profile_photo_updated", "", { file_name: fileName, mime: contentType, bytes: file.byteLength, sha256 });
  return json({
    success: true,
    updated: true,
    verified: verifyResponse.ok && Boolean(profilePictureUrl(verifyPayload)),
    profile_picture_url: profilePictureUrl(verifyPayload),
    image: { file_name: fileName, mime: contentType, bytes: file.byteLength, sha256 },
  });
}

async function finalizeMetaWebhook(request: Request, env: Env): Promise<Response> {
  const appId = String(env.META_APP_ID || "").trim();
  if (!/^\d+$/.test(appId)) return json({ success: false, stage: "configuration", error: "META_APP_ID_missing" }, 503);
  if (!env.META_APP_SECRET || !env.META_SYSTEM_USER_TOKEN || !env.META_VERIFY_TOKEN || !env.META_WABA_ID) {
    return json({ success: false, stage: "configuration", error: "meta_secrets_missing" }, 503);
  }
  const version = String(env.META_GRAPH_API_VERSION || "").trim();
  const callbackUrl = `${new URL(request.url).origin}/webhooks/whatsapp`;

  const tokenBody = new URLSearchParams({
    client_id: appId,
    client_secret: env.META_APP_SECRET,
    grant_type: "client_credentials",
  });
  const tokenResponse = await fetch(`https://graph.facebook.com/${version}/oauth/access_token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: tokenBody,
  });
  const tokenPayload = await responsePayload(tokenResponse);
  const appAccessToken = String(tokenPayload.access_token || "");
  if (!tokenResponse.ok || !appAccessToken) {
    return json({ success: false, stage: "app_access_token", meta: tokenPayload }, 502);
  }

  const appSubscriptionBody = new URLSearchParams({
    object: "whatsapp_business_account",
    callback_url: callbackUrl,
    verify_token: env.META_VERIFY_TOKEN,
    fields: "messages",
    access_token: appAccessToken,
  });
  const appSubscriptionResponse = await fetch(`https://graph.facebook.com/${version}/${appId}/subscriptions`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: appSubscriptionBody,
  });
  const appSubscription = await responsePayload(appSubscriptionResponse);
  if (!appSubscriptionResponse.ok) {
    return json({ success: false, stage: "app_messages_subscription", meta: appSubscription }, 502);
  }

  const wabaSubscriptionResponse = await graphRequest(env, `${env.META_WABA_ID}/subscribed_apps`, { method: "POST" });
  const wabaSubscription = await responsePayload(wabaSubscriptionResponse);
  if (!wabaSubscriptionResponse.ok) {
    return json({ success: false, stage: "waba_subscription", meta: wabaSubscription }, 502);
  }
  const subscriptionsResponse = await graphRequest(env, `${env.META_WABA_ID}/subscribed_apps`);
  const subscriptions = await responsePayload(subscriptionsResponse);
  if (!subscriptionsResponse.ok) {
    return json({ success: false, stage: "waba_subscription_check", meta: subscriptions }, 502);
  }
  await audit(env, "meta_webhook_finalized", appId, { actor: "bridge", callback_url: callbackUrl });
  return json({
    success: true,
    callback_url: callbackUrl,
    app_subscription: appSubscription,
    waba_subscription: wabaSubscription,
    subscriptions,
  });
}

function extractMessageText(message: JsonRecord): string {
  const type = String(message.type || "");
  if (type === "text") return String((message.text as JsonRecord | undefined)?.body || "").trim();
  if (type === "button") return String((message.button as JsonRecord | undefined)?.text || "").trim();
  if (type === "interactive") {
    const interactive = (message.interactive as JsonRecord | undefined) || {};
    const reply = (interactive.button_reply as JsonRecord | undefined) || (interactive.list_reply as JsonRecord | undefined) || {};
    // O ID carrega o token opaco da decisao. O titulo e apenas visual e nao
    // identifica com seguranca qual aprovacao originou o clique.
    return String(reply.id || reply.title || "").trim();
  }
  return "";
}

function subjectFromMessage(message: JsonRecord, value?: JsonRecord): string {
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

async function pairingAttemptsAllowed(env: Env, subjectId: string): Promise<boolean> {
  const since = nowSeconds() - 3600;
  const row = await env.DB.prepare(
    "SELECT COUNT(*) AS total FROM audit_events WHERE event_type='pairing_failed' AND subject_id=? AND created_at>=?",
  ).bind(subjectId, since).first<{ total: number }>();
  return Number(row?.total || 0) < 5;
}

async function tryPair(env: Env, subjectId: string, waId: string, phone: string, textBody: string): Promise<boolean> {
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
  await queueOutbound(env, subjectId, subjectId, `Vinculo concluido (${total}/${MAX_BINDINGS_PER_USER}). O Black Jhon ja pode receber suas mensagens.`, "pairing");
  await flushOutbox(env, subjectId, 1);
  return true;
}

async function mediaQuotaAllowed(env: Env, declaredSize: number): Promise<boolean> {
  const month = monthKey();
  const day = new Date().toISOString().slice(0, 10);
  const [activeBytes, uploads, dailyUploads] = await Promise.all([
    counterGet(env, "media_active_bytes"),
    counterGet(env, `media_uploads:${month}`),
    counterGet(env, `media_uploads_day:${day}`),
  ]);
  const bytesLimit = intEnv(env.MEDIA_ACTIVE_BYTES_LIMIT, 900 * 1024 * 1024);
  const uploadLimit = intEnv(env.MEDIA_UPLOADS_MONTH_LIMIT, 10000);
  const dailyUploadLimit = intEnv(env.MEDIA_UPLOADS_DAY_LIMIT, 900);
  return activeBytes + Math.max(0, declaredSize) <= bytesLimit && uploads < uploadLimit && dailyUploads < dailyUploadLimit;
}

async function downloadMedia(env: Env, messageId: string, subjectId: string, mediaId: string, messageType: string, declaredMime: string): Promise<void> {
  try {
    const metadataResp = await graphRequest(env, mediaId);
    const metadata = (await metadataResp.json()) as JsonRecord;
    if (!metadataResp.ok) throw new Error(String((metadata.error as JsonRecord | undefined)?.message || `Meta media metadata ${metadataResp.status}`));
    const url = String(metadata.url || "");
    const mime = String(metadata.mime_type || declaredMime || "").split(";", 1)[0].trim().toLowerCase();
    const declaredSize = Number(metadata.file_size || 0);
    const policy = mediaPolicy(messageType, mime);
    if (!policy.allowed) throw new Error(`Tipo de midia nao permitido: ${mime || messageType}`);
    if (declaredSize > policy.maxBytes) throw new Error("Midia acima do limite gratuito configurado");
    if (!(await mediaQuotaAllowed(env, declaredSize))) throw new Error("Limite gratuito preventivo do KV atingido");
    const mediaResp = await fetch(url, { headers: { authorization: `Bearer ${env.META_SYSTEM_USER_TOKEN}` } });
    if (!mediaResp.ok) throw new Error(`Falha ao baixar midia Meta: ${mediaResp.status}`);
    const body = await mediaResp.arrayBuffer();
    if (body.byteLength > policy.maxBytes) throw new Error("Midia acima do limite apos download");
    if (!(await mediaQuotaAllowed(env, body.byteLength))) throw new Error("Limite gratuito preventivo do KV atingido");
    const extension = mime === "image/jpeg" ? "jpg" : mime === "image/png" ? "png" : mime.split("/", 2)[1]?.replace(/[^a-z0-9]/g, "") || "bin";
    const objectKey = `incoming/${new Date().toISOString().slice(0, 10)}/${crypto.randomUUID()}.${extension}`;
    await env.MEDIA.put(objectKey, body, {
      expirationTtl: 86400,
      metadata: { contentType: mime, messageId, subjectId, expiresAt: String(nowSeconds() + 86400) },
    });
    await env.DB.prepare(
      "UPDATE inbox SET media_mime=?,media_size=?,media_object_key=?,media_filename=?,status='queued',error=NULL WHERE message_id=?",
    ).bind(mime, body.byteLength, objectKey, `whatsapp_${messageId}.${extension}`, messageId).run();
    await Promise.all([
      counterAdd(env, "media_active_bytes", body.byteLength),
      counterAdd(env, `media_uploads:${monthKey()}`, 1),
      counterAdd(env, `media_uploads_day:${new Date().toISOString().slice(0, 10)}`, 1),
      audit(env, "media_stored", subjectId, { message_id: messageId, bytes: body.byteLength, mime }),
    ]);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    await env.DB.prepare("UPDATE inbox SET status='failed',error=?,completed_at=? WHERE message_id=?").bind(detail.slice(0, 800), nowSeconds(), messageId).run();
    await audit(env, "media_failed", subjectId, { message_id: messageId, error: detail.slice(0, 500) });
  }
}

async function handleIncomingMessage(env: Env, ctx: ExecutionContext, value: JsonRecord, message: JsonRecord): Promise<void> {
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

  const existing = await env.DB.prepare("SELECT message_id FROM inbox WHERE message_id=?").bind(messageId).first();
  if (existing) return;

  if (textBody && (await tryPair(env, incomingSubjectId, waId, waId, textBody))) return;
  // Cadastros feitos pela tela usam o telefone como subject_id. Em mensagens
  // reais a Meta pode enviar um BSUID diferente; nesse caso o wa_id/telefone
  // continua sendo a identidade confiavel para recuperar o mesmo vinculo.
  let binding = await env.DB.prepare(
    "SELECT subject_id FROM bindings WHERE active=1 AND (subject_id=? OR wa_id=? OR phone_number=?) LIMIT 1",
  ).bind(incomingSubjectId, waId, phoneSubject || waId).first<JsonRecord>();
  if (!binding) {
    const aliases = registeredPhoneAliases(phoneSubject || waId);
    const alternatePhone = aliases.find((candidate) => candidate !== phoneSubject && candidate !== waId) || "";
    if (alternatePhone) {
      binding = await env.DB.prepare(
        "SELECT subject_id FROM bindings WHERE active=1 AND (subject_id=? OR wa_id=? OR phone_number=?) LIMIT 1",
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

  const recent = await env.DB.prepare("SELECT COUNT(*) AS total FROM inbox WHERE subject_id=? AND received_at>=?").bind(subjectId, nowSeconds() - 3600).first<{ total: number }>();
  if (Number(recent?.total || 0) >= 30) {
    await audit(env, "inbound_rate_limited", subjectId, { message_id: messageId });
    return;
  }
  const allowedType = ["text", "button", "interactive", "image", "audio"].includes(messageType);
  const initialStatus = !allowedType ? "unsupported" : mediaId ? "media_fetching" : "queued";
  await env.DB.batch([
    env.DB.prepare(
      "INSERT INTO inbox(message_id,subject_id,wa_id,phone_number_id,message_type,text_body,media_id,media_mime,received_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
    ).bind(messageId, subjectId, waId || null, String(metadata.phone_number_id || ""), messageType, textBody || null, mediaId || null, mediaMime || null, receivedAt, initialStatus),
    env.DB.prepare("UPDATE bindings SET last_inbound_at=?,wa_id=COALESCE(NULLIF(?,''),wa_id) WHERE subject_id=?").bind(receivedAt, waId, subjectId),
  ]);
  await audit(env, "inbound_received", subjectId, { message_id: messageId, type: messageType });
  if (mediaId) ctx.waitUntil(downloadMedia(env, messageId, subjectId, mediaId, messageType, mediaMime));
  ctx.waitUntil(releaseWaitingSummary(env, subjectId));
  // O cadastro pode usar a variante brasileira com o nono digito enquanto a
  // Meta entrega o wa_id sem ele. Libera as mensagens tanto pela identidade
  // canonica do vinculo quanto pela forma recebida, sem cruzar outros numeros.
  ctx.waitUntil(flushAdhocOutbox(env, subjectId, 10));
  if (phoneSubject && phoneSubject !== subjectId) ctx.waitUntil(flushAdhocOutbox(env, phoneSubject, 10));
}

async function handleStatuses(env: Env, statuses: unknown): Promise<void> {
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
      env.DB.prepare("UPDATE outbound_media SET status=CASE WHEN ?='failed' THEN 'failed' WHEN ? IN ('sent','delivered','read') THEN ? ELSE status END,updated_at=?,error=CASE WHEN ?='failed' THEN ? ELSE error END WHERE meta_message_id=?")
        .bind(status, status, status, at, status, JSON.stringify(item.errors || []).slice(0, 800), id),
    ]);
  }
}

async function handleWebhook(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
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

async function queueOutbound(env: Env, subjectId: string, recipient: string, textBody: string, reason: string, templateName = "", templateParams: string[] = []): Promise<string> {
  const id = randomId("out");
  const now = nowSeconds();
  await env.DB.prepare(
    "INSERT INTO outbox(id,subject_id,recipient,message_type,text_body,template_name,template_params_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
  ).bind(id, subjectId, recipient, templateName ? "template" : "text", compactReply(textBody), templateName || null, JSON.stringify(templateParams || []), "queued", now, now).run();
  await audit(env, "outbox_queued", subjectId, { outbox_id: id, reason, template_name: templateName || "" });
  return id;
}

async function zeroCostEligibility(env: Env, subjectId: string, allowUnregistered = false): Promise<{ allowed: boolean; reason: string; binding?: JsonRecord }> {
  if (!isPolicyValid(env.ZERO_COST_POLICY_VALID_UNTIL)) return { allowed: false, reason: "policy_recheck_required" };
  const binding = allowUnregistered
    ? await env.DB.prepare("SELECT * FROM bindings WHERE active=1 AND (subject_id=? OR wa_id=? OR phone_number=?) LIMIT 1").bind(subjectId, subjectId, subjectId).first<JsonRecord>()
    : await env.DB.prepare("SELECT * FROM bindings WHERE subject_id=? AND active=1").bind(subjectId).first<JsonRecord>();
  if (!binding && !allowUnregistered) return { allowed: false, reason: "binding_missing" };
  let eligibleBinding: JsonRecord | undefined = binding || undefined;
  if (!eligibleBinding && allowUnregistered) {
    const inbound = await env.DB.prepare(
      "SELECT created_at FROM audit_events WHERE event_type='unpaired_phone_message' AND subject_id=? ORDER BY created_at DESC LIMIT 1",
    ).bind(subjectId).first<JsonRecord>();
    eligibleBinding = {
      subject_id: subjectId,
      wa_id: subjectId,
      phone_number: subjectId,
      last_inbound_at: Number(inbound?.created_at || 0),
    };
  }
  if (!isFreeWindowOpen(Number(eligibleBinding?.last_inbound_at || 0), nowSeconds(), intEnv(env.FREE_WINDOW_SECONDS, 84600))) {
    return { allowed: false, reason: "waiting_free_window", binding: eligibleBinding };
  }
  return { allowed: true, reason: "free_service_window", binding: eligibleBinding };
}

async function sendOutboxItem(env: Env, item: JsonRecord): Promise<void> {
  const id = String(item.id || "");
  const subjectId = String(item.subject_id || "");
  const eligibility = await zeroCostEligibility(env, subjectId, String(item.message_type || "") === "adhoc_text");
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
  const templateName = String(item.template_name || "");
  let body: JsonRecord;
  if (templateName) {
    const template = await env.DB.prepare("SELECT name,language,category,status FROM template_registry WHERE name=?").bind(templateName).first<JsonRecord>();
    if (!template || !safeTemplate(template.name, template.category, template.status)) {
      body = { messaging_product: "whatsapp", recipient_type: "individual", to: recipient, type: "text", text: { preview_url: false, body: compactReply(item.text_body) } };
    } else {
      let params: unknown[] = [];
      try { params = JSON.parse(String(item.template_params_json || "[]")) as unknown[]; } catch { params = []; }
      body = {
        messaging_product: "whatsapp",
        recipient_type: "individual",
        to: recipient,
        type: "template",
        template: {
          name: templateName,
          language: { code: String(template.language || "pt_BR") },
          components: [{ type: "body", parameters: params.map((value) => ({ type: "text", text: String(value || "").slice(0, 500) })) }],
        },
      };
    }
  } else {
    body = { messaging_product: "whatsapp", recipient_type: "individual", to: recipient, type: "text", text: { preview_url: false, body: compactReply(item.text_body) } };
  }
  const response = await graphRequest(env, `${env.META_PHONE_NUMBER_ID}/messages`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const payload = (await response.json()) as JsonRecord;
  if (!response.ok) {
    const attempts = Number(item.attempts || 0) + 1;
    await env.DB.prepare("UPDATE outbox SET status=?,attempts=?,updated_at=?,error=? WHERE id=?").bind(attempts >= 5 ? "failed" : "retry", attempts, nowSeconds(), JSON.stringify(payload).slice(0, 800), id).run();
    return;
  }
  const messages = Array.isArray(payload.messages) ? payload.messages : [];
  const metaId = String(((messages[0] || {}) as JsonRecord).id || "");
  await env.DB.prepare("UPDATE outbox SET status='sent',sent_at=?,updated_at=?,meta_message_id=?,error=NULL WHERE id=?").bind(nowSeconds(), nowSeconds(), metaId || null, id).run();
  await audit(env, "outbox_sent", subjectId, { outbox_id: id, meta_message_id: metaId });
}

async function flushOutbox(env: Env, subjectId = "", limit = 10): Promise<void> {
  const query = subjectId
    ? "SELECT * FROM outbox WHERE subject_id=? AND status IN ('queued','retry','waiting_free_window') ORDER BY created_at LIMIT ?"
    : "SELECT * FROM outbox WHERE status IN ('queued','retry','waiting_free_window') ORDER BY created_at LIMIT ?";
  const statement = subjectId ? env.DB.prepare(query).bind(subjectId, limit) : env.DB.prepare(query).bind(limit);
  const rows = await statement.all<JsonRecord>();
  for (const item of rows.results || []) await sendOutboxItem(env, item);
}

async function flushAdhocOutbox(env: Env, subjectId: string, limit = 10): Promise<void> {
  const rows = await env.DB.prepare(
    "SELECT * FROM outbox WHERE subject_id=? AND message_type='adhoc_text' AND status IN ('queued','retry','waiting_free_window') ORDER BY created_at LIMIT ?",
  ).bind(subjectId, limit).all<JsonRecord>();
  for (const item of rows.results || []) await sendOutboxItem(env, item);
}

async function notifyUnauthorizedAccess(env: Env, subjectId: string, waId: string, reason: string): Promise<string> {
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

async function releaseWaitingSummary(env: Env, subjectId: string): Promise<void> {
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
    `Black Jhon guardou ${rows.length} atualizacoes enquanto a janela gratuita estava fechada:\n${lines.join("\n")}`,
  );
  for (const item of rows) {
    await env.DB.prepare("UPDATE outbox SET status='summarized',updated_at=?,error=NULL WHERE id=?")
      .bind(nowSeconds(), String(item.id || "")).run();
  }
  const summaryId = await queueOutbound(env, subjectId, subjectId, summary, "pending_free_window_summary");
  const summaryItem = await env.DB.prepare("SELECT * FROM outbox WHERE id=?").bind(summaryId).first<JsonRecord>();
  if (summaryItem) await sendOutboxItem(env, summaryItem);
}

async function bridgeStatus(env: Env): Promise<Response> {
  const counts = await env.DB.prepare(
    "SELECT (SELECT COUNT(*) FROM inbox WHERE status IN ('queued','media_fetching','retry')) AS inbox_pending,(SELECT COUNT(*) FROM outbox WHERE status IN ('queued','retry','waiting_free_window','policy_recheck_required')) AS outbox_pending,(SELECT COUNT(*) FROM bindings WHERE active=1) AS bindings,(SELECT COUNT(*) FROM template_registry WHERE status='APPROVED' AND category='UTILITY') AS templates",
  ).first<JsonRecord>();
  const templates = await env.DB.prepare(
    "SELECT name,language,category,status,last_verified_at FROM template_registry ORDER BY name",
  ).all<JsonRecord>();
  const bindingRows = await env.DB.prepare(
    "SELECT subject_id,wa_id,phone_number,client_id,username,machine_id,last_inbound_at,created_at FROM bindings WHERE active=1 ORDER BY created_at DESC LIMIT 100",
  ).all<JsonRecord>();
  const outboundUsage = await env.DB.prepare(
    "SELECT COUNT(*) AS uploads,COALESCE(SUM(byte_size),0) AS bytes FROM outbound_media WHERE created_at>=?",
  ).bind(monthStartSeconds()).first<JsonRecord>();
  const bindings = (bindingRows.results || []).map((item) => ({
    subject_id: String(item.subject_id || ""),
    phone_number: String(item.phone_number || item.wa_id || "").replace(/\D/g, ""),
    phone_suffix: String(item.phone_number || item.wa_id || "").replace(/\D/g, "").slice(-4),
    client_id: String(item.client_id || ""),
    username: String(item.username || ""),
    machine_id: String(item.machine_id || ""),
    last_inbound_at: Number(item.last_inbound_at || 0),
    created_at: Number(item.created_at || 0),
  }));
  const binding = bindings[0];
  return json({
    success: true,
    worker: true,
    gateway_protocol_version: GATEWAY_PROTOCOL_VERSION,
    build_version: GATEWAY_BUILD_VERSION,
    d1: true,
    r2: false,
    kv: true,
    media_storage: "workers_kv_free",
    zero_cost: {
      policy_valid: isPolicyValid(env.ZERO_COST_POLICY_VALID_UNTIL),
      valid_until: env.ZERO_COST_POLICY_VALID_UNTIL,
      free_window_seconds: intEnv(env.FREE_WINDOW_SECONDS, 84600),
    },
    counts: counts || {},
    templates: templates.results || [],
    binding_limit_per_user: MAX_BINDINGS_PER_USER,
    bindings,
    binding: binding ? {
      paired: true,
      phone_suffix: binding.phone_suffix,
      client_id: binding.client_id,
      username: binding.username,
      machine_id: binding.machine_id,
      last_inbound_at: binding.last_inbound_at,
    } : { paired: false },
    meta: {
      configured: Boolean(env.META_ACCESS_TOKEN && env.META_APP_SECRET && env.META_PHONE_NUMBER_ID && env.META_WABA_ID && env.META_APP_ID),
      graph_api_version: env.META_GRAPH_API_VERSION,
    },
    usage: {
      media_active_bytes: await counterGet(env, "media_active_bytes"),
      media_uploads_month: await counterGet(env, `media_uploads:${monthKey()}`),
      media_outbound_uploads_month: Number(outboundUsage?.uploads || 0),
      media_outbound_bytes_month: Number(outboundUsage?.bytes || 0),
      media_outbound_uploads_limit: intEnv(env.MEDIA_OUTBOUND_UPLOADS_MONTH_LIMIT, 10000),
      media_outbound_bytes_limit: intEnv(env.MEDIA_OUTBOUND_BYTES_MONTH_LIMIT, 900 * 1024 * 1024),
      media_downloads_month: await counterGet(env, `media_downloads:${monthKey()}`),
      typing_pulses_day: await counterGet(env, `typing_pulses:${dayKey()}`),
      typing_pulses_day_limit: intEnv(env.TYPING_PULSES_DAY_LIMIT, 10000),
    },
  });
}

async function createPairingCode(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const code = String(body.code || "").trim().toUpperCase();
  const clientId = String(body.client_id || "").trim();
  const username = String(body.username || "").trim().toLowerCase();
  const machineId = String(body.machine_id || "").trim();
  if (!/^[A-Z2-9]{8}$/.test(code) || !clientId || !username || !machineId) return json({ success: false, error: "invalid_pairing_payload" }, 400);
  const active = await env.DB.prepare("SELECT COUNT(*) AS total FROM bindings WHERE client_id=? AND username=? AND active=1")
    .bind(clientId, username).first<{ total: number }>();
  const activeBindings = Number(active?.total || 0);
  if (activeBindings >= MAX_BINDINGS_PER_USER) {
    return json({ success: false, error: "binding_limit_reached", limit: MAX_BINDINGS_PER_USER, active_bindings: activeBindings }, 409);
  }
  const hash = await sha256Hex(code);
  const now = nowSeconds();
  await env.DB.prepare("INSERT OR REPLACE INTO pairing_codes(code_hash,client_id,username,machine_id,created_at,expires_at,used_at,used_by_subject) VALUES(?,?,?,?,?,?,NULL,NULL)")
    .bind(hash, clientId, username, machineId, now, now + 600).run();
  await audit(env, "pairing_created", "", { client_id: clientId, username, machine_id: machineId });
  return json({ success: true, expires_at: now + 600, limit: MAX_BINDINGS_PER_USER, active_bindings: activeBindings, remaining_slots: MAX_BINDINGS_PER_USER - activeBindings });
}

function normalizeRegisteredPhone(value: unknown): string {
  let digits = String(value || "").replace(/\D/g, "");
  if (digits.startsWith("00")) digits = digits.slice(2);
  if (digits.length === 10 || digits.length === 11) digits = `55${digits}`;
  if (digits.length < 10 || digits.length > 15 || /^(\d)\1+$/.test(digits)) return "";
  return digits;
}

function registeredPhoneAliases(value: unknown): string[] {
  const phone = normalizeRegisteredPhone(value);
  if (!phone) return [];
  const aliases = new Set([phone]);
  if (phone.startsWith("55") && phone.length === 13 && phone[4] === "9") {
    aliases.add(`${phone.slice(0, 4)}${phone.slice(5)}`);
  } else if (phone.startsWith("55") && phone.length === 12) {
    aliases.add(`${phone.slice(0, 4)}9${phone.slice(4)}`);
  }
  return [...aliases];
}

async function registerBinding(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const phoneNumber = normalizeRegisteredPhone(body.phone_number);
  const clientId = String(body.client_id || "").trim();
  const username = String(body.username || "").trim().toLowerCase();
  const machineId = String(body.machine_id || "").trim();
  if (!phoneNumber || !clientId || !username || !machineId) {
    return json({ success: false, error: phoneNumber ? "invalid_registration_payload" : "invalid_phone_number" }, 400);
  }

  const existing = await env.DB.prepare(
    "SELECT subject_id,client_id,username,machine_id,active,last_inbound_at,created_at FROM bindings WHERE subject_id=? OR wa_id=? OR phone_number=? ORDER BY active DESC LIMIT 1",
  ).bind(phoneNumber, phoneNumber, phoneNumber).first<JsonRecord>();
  const existingActive = Number(existing?.active || 0) === 1;
  const sameOwner = String(existing?.client_id || "") === clientId && String(existing?.username || "").toLowerCase() === username;
  if (existingActive && !sameOwner) {
    return json({ success: false, error: "binding_already_registered" }, 409);
  }

  const active = await env.DB.prepare("SELECT COUNT(*) AS total FROM bindings WHERE client_id=? AND username=? AND active=1")
    .bind(clientId, username).first<{ total: number }>();
  const activeBindings = Number(active?.total || 0);
  if (!existingActive && activeBindings >= MAX_BINDINGS_PER_USER) {
    return json({ success: false, error: "binding_limit_reached", limit: MAX_BINDINGS_PER_USER, active_bindings: activeBindings }, 409);
  }

  const now = nowSeconds();
  const subjectId = String(existing?.subject_id || phoneNumber);
  if (existing) {
    await env.DB.prepare(
      "UPDATE bindings SET wa_id=?,phone_number=?,client_id=?,username=?,machine_id=?,active=1,revoked_at=NULL WHERE subject_id=?",
    ).bind(phoneNumber, phoneNumber, clientId, username, machineId, subjectId).run();
  } else {
    await env.DB.prepare(
      "INSERT INTO bindings(subject_id,wa_id,phone_number,client_id,username,machine_id,active,last_inbound_at,created_at,revoked_at) VALUES(?,?,?,?,?,?,1,NULL,?,NULL)",
    ).bind(subjectId, phoneNumber, phoneNumber, clientId, username, machineId, now).run();
  }

  const total = existingActive ? activeBindings : activeBindings + 1;
  await audit(env, "binding_registered_directly", subjectId, {
    client_id: clientId,
    username,
    machine_id: machineId,
    active_bindings: total,
  });
  return json({
    success: true,
    created: !existingActive,
    subject_id: subjectId,
    limit: MAX_BINDINGS_PER_USER,
    active_bindings: total,
    remaining_slots: Math.max(0, MAX_BINDINGS_PER_USER - total),
    binding: {
      subject_id: subjectId,
      phone_number: phoneNumber,
      client_id: clientId,
      username,
      machine_id: machineId,
      last_inbound_at: Number(existing?.last_inbound_at || 0),
      created_at: Number(existing?.created_at || now),
    },
  });
}

async function welcomeMessage(request: Request, env: Env): Promise<Response> {
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

async function adhocMessage(request: Request, env: Env): Promise<Response> {
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

async function claimMessages(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const machineId = String(body.machine_id || "").trim();
  const limit = Math.max(1, Math.min(5, Number(body.limit || 5)));
  if (!machineId) return json({ success: false, error: "machine_id_required" }, 400);
  const now = nowSeconds();
  await env.DB.batch([
    env.DB.prepare("UPDATE inbox SET status='dead_letter',error='retry_limit',completed_at=?,lease_owner=NULL,lease_until=NULL WHERE status='leased' AND lease_until<? AND attempts>=5").bind(now, now),
    env.DB.prepare("UPDATE inbox SET status='queued',lease_owner=NULL,lease_until=NULL WHERE status='leased' AND lease_until<? AND attempts<5").bind(now),
  ]);
  const rows = await env.DB.prepare(
    "SELECT i.*,COALESCE(NULLIF(i.wa_id,''),b.wa_id) AS wa_id,b.client_id,b.username,b.machine_id FROM inbox i JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 WHERE i.status='queued' AND b.machine_id=? ORDER BY i.received_at LIMIT ?",
  ).bind(machineId, limit).all<JsonRecord>();
  const claimed: JsonRecord[] = [];
  for (const row of rows.results || []) {
    const messageId = String(row.message_id || "");
    const result = await env.DB.prepare("UPDATE inbox SET status='leased',lease_owner=?,lease_until=?,attempts=attempts+1 WHERE message_id=? AND status='queued'")
      .bind(machineId, now + 600, messageId).run();
    if (result.meta.changes) claimed.push({ ...row, status: "leased", lease_until: now + 600 });
  }
  return json({ success: true, messages: claimed });
}

async function bridgeMedia(request: Request, env: Env, mediaId: string): Promise<Response> {
  const row = await env.DB.prepare("SELECT media_object_key,media_mime,media_size,media_filename FROM inbox WHERE message_id=?").bind(mediaId).first<JsonRecord>();
  const key = String(row?.media_object_key || "");
  if (!key) return json({ success: false, error: "media_not_found" }, 404);
  const reads = await counterGet(env, `media_downloads:${monthKey()}`);
  if (reads >= intEnv(env.MEDIA_DOWNLOADS_MONTH_LIMIT, 50000)) return json({ success: false, error: "free_media_download_limit" }, 429);
  const object = await env.MEDIA.get(key, "stream");
  if (!object) return json({ success: false, error: "media_expired" }, 404);
  await counterAdd(env, `media_downloads:${monthKey()}`, 1);
  const headers = new Headers({ "content-type": String(row?.media_mime || "application/octet-stream"), "cache-control": "no-store", "x-jk-filename": encodeURIComponent(String(row?.media_filename || "whatsapp-media")) });
  return new Response(object, { status: 200, headers });
}

async function releaseMedia(env: Env, messageId: string): Promise<void> {
  const row = await env.DB.prepare("SELECT media_object_key,media_size FROM inbox WHERE message_id=?").bind(messageId).first<JsonRecord>();
  const key = String(row?.media_object_key || "");
  if (!key) return;
  await env.MEDIA.delete(key);
  await env.DB.prepare("UPDATE inbox SET media_object_key=NULL WHERE message_id=?").bind(messageId).run();
  await counterAdd(env, "media_active_bytes", -Number(row?.media_size || 0));
}

async function messageTyping(request: Request, env: Env, messageId: string): Promise<Response> {
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
  if (!isPolicyValid(env.ZERO_COST_POLICY_VALID_UNTIL)) {
    return json({ success: false, status: "policy_recheck_required", error: "policy_recheck_required" }, 409);
  }
  if (!isFreeWindowOpen(Number(row.last_inbound_at || 0), nowSeconds(), intEnv(env.FREE_WINDOW_SECONDS, 84600))) {
    return json({ success: false, status: "waiting_free_window", error: "waiting_free_window" }, 409);
  }
  const now = nowSeconds();
  if (Number(row.typing_last_at || 0) > now - 15) return json({ success: true, status: "too_soon" });
  const throttle = await env.DB.prepare(
    "UPDATE inbox SET typing_last_at=? WHERE message_id=? AND status='leased' AND (typing_last_at IS NULL OR typing_last_at<=?)",
  ).bind(now, messageId, now - 15).run();
  if (!Number(throttle.meta.changes || 0)) return json({ success: true, status: "too_soon" });
  const dailyLimit = intEnv(env.TYPING_PULSES_DAY_LIMIT, 10000);
  if (!(await counterReserveBelow(env, `typing_pulses:${dayKey()}`, dailyLimit))) {
    return json({ success: true, status: "daily_limit", limit: dailyLimit });
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
    return json({ success: false, error: "meta_typing_indicator_failed", meta: payload }, 502);
  }
  return json({ success: true, status: "sent" });
}

async function messageResult(request: Request, env: Env, messageId: string): Promise<Response> {
  const body = await requestJson(request);
  const machineId = String(body.machine_id || "").trim();
  const row = await env.DB.prepare(
    "SELECT i.*,b.machine_id FROM inbox i JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 WHERE i.message_id=?",
  ).bind(messageId).first<JsonRecord>();
  if (!row || String(row.machine_id || "") !== machineId) return json({ success: false, error: "message_not_owned" }, 404);
  const status = ["completed", "failed", "awaiting_approval"].includes(String(body.status || "")) ? String(body.status) : "failed";
  const diagnostic = compactReply(body.error || body.response || "falha local", 800);
  const responseFallback = status === "failed"
    ? body.response || "Nao consegui concluir esta solicitacao. O erro tecnico ficou registrado no JK Sistema."
    : body.response || "";
  const responseParts = compactReplyParts(
    body.response_parts,
    responseFallback,
    body.allow_full_history === true ? 0 : 30,
  );
  await env.DB.prepare("UPDATE inbox SET status=?,task_id=?,error=?,completed_at=?,lease_owner=NULL,lease_until=NULL WHERE message_id=?")
    .bind(status, String(body.task_id || "") || null, status === "failed" ? diagnostic : null, nowSeconds(), messageId).run();
  if (responseParts.length) {
    const templateName = String(body.template_name || "");
    const params = Array.isArray(body.template_params) ? body.template_params.map(String) : [];
    for (const [index, responseText] of responseParts.entries()) {
      await queueOutbound(
        env,
        String(row.subject_id || ""),
        String(row.wa_id || row.subject_id || ""),
        responseText,
        `${status}:${index + 1}/${responseParts.length}`,
        responseParts.length === 1 ? templateName : "",
        responseParts.length === 1 ? params : [],
      );
    }
    await flushOutbox(env, String(row.subject_id || ""), Math.min(12, Math.max(3, responseParts.length + 1)));
  }
  await releaseMedia(env, messageId);
  return json({ success: true, status, queued_parts: responseParts.length });
}

async function messageImage(request: Request, env: Env, messageId: string): Promise<Response> {
  const contentLength = Number.parseInt(String(request.headers.get("content-length") || "0"), 10);
  if (Number.isFinite(contentLength) && contentLength > 5 * 1024 * 1024 + 256 * 1024) {
    return json({ success: false, error: "outbound_image_size_limit" }, 413);
  }
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return json({ success: false, error: "invalid_multipart_payload" }, 400);
  }
  const machineId = String(form.get("machine_id") || "").trim();
  const caption = compactReply(form.get("caption") || "", 1024);
  const artifactType = String(form.get("artifact_type") || "").trim().toLowerCase();
  const expectedSha256 = String(form.get("sha256") || "").trim().toLowerCase();
  const rawFile = form.get("file");
  if (!machineId || !(rawFile instanceof File)) return json({ success: false, error: "image_payload_required" }, 400);
  if (!OUTBOUND_IMAGE_ARTIFACT_TYPES.has(artifactType)) return json({ success: false, error: "outbound_image_artifact_not_allowed" }, 400);
  if (!/^[a-f0-9]{64}$/.test(expectedSha256)) return json({ success: false, error: "outbound_image_sha256_required" }, 400);
  if (rawFile.size < 1 || rawFile.size > 5 * 1024 * 1024) return json({ success: false, error: "outbound_image_size_limit" }, 413);

  const row = await env.DB.prepare(
    "SELECT i.subject_id,i.wa_id,b.machine_id FROM inbox i JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 WHERE i.message_id=?",
  ).bind(messageId).first<JsonRecord>();
  if (!row || String(row.machine_id || "") !== machineId) return json({ success: false, error: "message_not_owned" }, 404);

  const subjectId = String(row.subject_id || "");
  const eligibility = await zeroCostEligibility(env, subjectId);
  if (!eligibility.allowed) {
    await audit(env, "outbound_image_blocked", subjectId, { message_id: messageId, reason: eligibility.reason });
    return json({ success: false, status: eligibility.reason, error: eligibility.reason }, 409);
  }

  const mime = String(rawFile.type || "").split(";", 1)[0].trim().toLowerCase();
  const fileBuffer = await rawFile.arrayBuffer();
  const policy = outboundImagePolicy(mime, fileBuffer.byteLength, new Uint8Array(fileBuffer.slice(0, 12)));
  if (!policy.allowed) return json({ success: false, error: policy.error }, 400);
  if (artifactType === "report_chart" && mime !== "image/png") {
    return json({ success: false, error: "report_chart_png_required" }, 400);
  }
  const digest = await crypto.subtle.digest("SHA-256", fileBuffer);
  const sha256 = [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
  if (!timingSafeEqualText(expectedSha256, sha256)) return json({ success: false, error: "outbound_image_sha256_mismatch" }, 400);
  const fingerprint = await sha256Hex(`${messageId}\n${subjectId}\n${artifactType}\n${sha256}`);
  const fileName = String(rawFile.name || "black-jhon-image.jpg").replace(/[^A-Za-z0-9._-]+/g, "-").slice(0, 120) || "black-jhon-image.jpg";
  const now = nowSeconds();
  const monthStart = monthStartSeconds();
  const bytesLimit = intEnv(env.MEDIA_OUTBOUND_BYTES_MONTH_LIMIT, 900 * 1024 * 1024);
  const uploadLimit = intEnv(env.MEDIA_OUTBOUND_UPLOADS_MONTH_LIMIT, 10000);
  const reservation = await env.DB.prepare(
    `INSERT OR IGNORE INTO outbound_media(
       fingerprint,inbound_message_id,subject_id,artifact_type,sha256,mime_type,byte_size,caption,status,created_at,updated_at
     )
     SELECT ?,?,?,?,?,?,?,?,'processing',?,?
     WHERE (SELECT COALESCE(SUM(byte_size),0) FROM outbound_media WHERE created_at>=?) + ? <= ?
       AND (SELECT COUNT(*) FROM outbound_media WHERE created_at>=?) < ?
       AND (SELECT COUNT(*) FROM outbound_media WHERE inbound_message_id=?) < 3`,
  ).bind(
    fingerprint, messageId, subjectId, artifactType, sha256, mime, fileBuffer.byteLength, caption || null, now, now,
    monthStart, fileBuffer.byteLength, bytesLimit, monthStart, uploadLimit, messageId,
  ).run();
  if (!Number(reservation.meta.changes || 0)) {
    const existing = await env.DB.prepare(
      "SELECT status,meta_media_id,meta_message_id,byte_size,mime_type,error FROM outbound_media WHERE fingerprint=?",
    ).bind(fingerprint).first<JsonRecord>();
    if (existing) {
      const existingStatus = String(existing.status || "processing");
      if (["processing", "sent", "delivered", "read"].includes(existingStatus)) {
        return json({
          success: true,
          status: existingStatus,
          duplicate: true,
          meta_message_id: String(existing.meta_message_id || ""),
          bytes: Number(existing.byte_size || fileBuffer.byteLength),
          mime: String(existing.mime_type || mime),
        });
      }
      return json({ success: false, status: existingStatus, error: String(existing.error || "outbound_image_previous_failure") }, 409);
    }
    const perResponse = await env.DB.prepare(
      "SELECT COUNT(*) AS total FROM outbound_media WHERE inbound_message_id=?",
    ).bind(messageId).first<{ total: number }>();
    if (Number(perResponse?.total || 0) >= 3) {
      await audit(env, "outbound_image_blocked", subjectId, { message_id: messageId, reason: "outbound_image_response_limit" });
      return json({ success: false, error: "outbound_image_response_limit" }, 429);
    }
    await audit(env, "outbound_image_blocked", subjectId, { message_id: messageId, reason: "outbound_media_month_limit" });
    return json({ success: false, error: "outbound_media_month_limit" }, 429);
  }
  await counterAdd(env, `media_outbound_uploads:${monthKey()}`, 1);

  const uploadForm = new FormData();
  uploadForm.set("messaging_product", "whatsapp");
  uploadForm.set("file", new File([fileBuffer], fileName, { type: mime }));
  const uploadResponse = await graphRequest(env, `${env.META_PHONE_NUMBER_ID}/media`, { method: "POST", body: uploadForm });
  const uploadPayload = await responsePayload(uploadResponse);
  const mediaId = String(uploadPayload.id || "");
  if (!uploadResponse.ok || !mediaId) {
    await env.DB.prepare("UPDATE outbound_media SET status='failed',error=?,updated_at=? WHERE fingerprint=?")
      .bind(JSON.stringify(uploadPayload).slice(0, 800), nowSeconds(), fingerprint).run();
    await audit(env, "outbound_image_failed", subjectId, { message_id: messageId, stage: "meta_media_upload", status: uploadResponse.status });
    return json({ success: false, stage: "meta_media_upload", error: uploadPayload }, 502);
  }
  await env.DB.prepare("UPDATE outbound_media SET meta_media_id=?,updated_at=? WHERE fingerprint=?")
    .bind(mediaId, nowSeconds(), fingerprint).run();

  const recipient = outboundRecipient(row.wa_id, subjectId, eligibility.binding);
  if (!recipient) {
    await env.DB.prepare("UPDATE outbound_media SET status='failed',error='recipient_missing',updated_at=? WHERE fingerprint=?")
      .bind(nowSeconds(), fingerprint).run();
    return json({ success: false, error: "recipient_missing" }, 400);
  }
  const image: JsonRecord = { id: mediaId };
  if (caption) image.caption = caption;
  const sendResponse = await graphRequest(env, `${env.META_PHONE_NUMBER_ID}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messaging_product: "whatsapp", recipient_type: "individual", to: recipient, type: "image", image }),
  });
  const sendPayload = await responsePayload(sendResponse);
  if (!sendResponse.ok) {
    await env.DB.prepare("UPDATE outbound_media SET status='failed',error=?,updated_at=? WHERE fingerprint=?")
      .bind(JSON.stringify(sendPayload).slice(0, 800), nowSeconds(), fingerprint).run();
    await audit(env, "outbound_image_failed", subjectId, { message_id: messageId, stage: "meta_message_send", status: sendResponse.status, media_id: mediaId });
    return json({ success: false, stage: "meta_message_send", error: sendPayload }, 502);
  }
  const messages = Array.isArray(sendPayload.messages) ? sendPayload.messages : [];
  const metaMessageId = String(((messages[0] || {}) as JsonRecord).id || "");
  await env.DB.prepare("UPDATE outbound_media SET status='sent',meta_message_id=?,sent_at=?,updated_at=?,error=NULL WHERE fingerprint=?")
    .bind(metaMessageId || null, nowSeconds(), nowSeconds(), fingerprint).run();
  await audit(env, "outbound_image_sent", subjectId, {
    message_id: messageId,
    meta_message_id: metaMessageId,
    bytes: fileBuffer.byteLength,
    mime,
    sha256,
  });
  return json({ success: true, status: "sent", fingerprint, meta_message_id: metaMessageId, bytes: fileBuffer.byteLength, mime });
}

async function proactiveImage(request: Request, env: Env): Promise<Response> {
  const contentLength = Number.parseInt(String(request.headers.get("content-length") || "0"), 10);
  if (Number.isFinite(contentLength) && contentLength > 5 * 1024 * 1024 + 256 * 1024) {
    return json({ success: false, error: "outbound_image_size_limit" }, 413);
  }
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return json({ success: false, error: "invalid_multipart_payload" }, 400);
  }
  let hasUnexpectedField = false;
  form.forEach((_value, key) => {
    if (!PROACTIVE_IMAGE_FORM_FIELDS.has(key)) hasUnexpectedField = true;
  });
  if (hasUnexpectedField) {
    return json({ success: false, error: "invalid_proactive_image_payload" }, 400);
  }
  const subjectId = String(form.get("subject_id") || "").trim();
  const machineId = String(form.get("machine_id") || "").trim();
  const callerFingerprint = String(form.get("fingerprint") || "").trim();
  const eventType = String(form.get("event_type") || "").trim().toLowerCase();
  const artifactType = String(form.get("artifact_type") || "").trim().toLowerCase();
  const caption = compactReply(form.get("caption") || "", 1024);
  const expectedSha256 = String(form.get("sha256") || "").trim().toLowerCase();
  const rawFile = form.get("file");
  if (
    !subjectId
    || !machineId
    || !/^[A-Za-z0-9:_-]{16,160}$/.test(callerFingerprint)
    || eventType !== "weekly_report"
    || artifactType !== "report_chart"
    || !(rawFile instanceof File)
  ) {
    return json({ success: false, error: "invalid_proactive_image_payload" }, 400);
  }
  if (!/^[a-f0-9]{64}$/.test(expectedSha256)) return json({ success: false, error: "outbound_image_sha256_required" }, 400);
  if (rawFile.size < 1 || rawFile.size > 5 * 1024 * 1024) return json({ success: false, error: "outbound_image_size_limit" }, 413);

  const eligibility = await zeroCostEligibility(env, subjectId);
  if (!eligibility.allowed) {
    await audit(env, "proactive_image_blocked", subjectId, { event_type: eventType, reason: eligibility.reason });
    return json({ success: false, status: eligibility.reason, error: eligibility.reason }, 409);
  }
  if (String(eligibility.binding?.machine_id || "") !== machineId) {
    await audit(env, "proactive_image_blocked", subjectId, { event_type: eventType, reason: "binding_machine_mismatch" });
    return json({ success: false, error: "binding_machine_mismatch" }, 403);
  }
  const recipient = outboundRecipient(subjectId, subjectId, eligibility.binding);
  if (!recipient) return json({ success: false, error: "recipient_missing" }, 400);

  const mime = String(rawFile.type || "").split(";", 1)[0].trim().toLowerCase();
  const fileBuffer = await rawFile.arrayBuffer();
  const policy = outboundImagePolicy(mime, fileBuffer.byteLength, new Uint8Array(fileBuffer.slice(0, 12)));
  if (!policy.allowed) return json({ success: false, error: policy.error }, 400);
  if (mime !== "image/png") return json({ success: false, error: "report_chart_png_required" }, 400);
  const digest = await crypto.subtle.digest("SHA-256", fileBuffer);
  const sha256 = [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
  if (!timingSafeEqualText(expectedSha256, sha256)) return json({ success: false, error: "outbound_image_sha256_mismatch" }, 400);

  // The caller fingerprint represents the weekly event. Keep deduplication
  // stable even if a later render produces different PNG metadata.
  const fingerprint = await sha256Hex(`proactive_image\n${subjectId}\n${eventType}\n${callerFingerprint}`);
  const inboundMessageId = `proactive:${eventType}:${callerFingerprint}`;
  const fileName = String(rawFile.name || "black-jhon-weekly-report.png").replace(/[^A-Za-z0-9._-]+/g, "-").slice(0, 120) || "black-jhon-weekly-report.png";
  const now = nowSeconds();
  const monthStart = monthStartSeconds();
  const bytesLimit = intEnv(env.MEDIA_OUTBOUND_BYTES_MONTH_LIMIT, 900 * 1024 * 1024);
  const uploadLimit = intEnv(env.MEDIA_OUTBOUND_UPLOADS_MONTH_LIMIT, 10000);
  const reservation = await env.DB.prepare(
    `INSERT OR IGNORE INTO outbound_media(
       fingerprint,inbound_message_id,subject_id,artifact_type,sha256,mime_type,byte_size,caption,status,created_at,updated_at
     )
     SELECT ?,?,?,?,?,?,?,?,'processing',?,?
     WHERE (SELECT COALESCE(SUM(byte_size),0) FROM outbound_media WHERE created_at>=?) + ? <= ?
       AND (SELECT COUNT(*) FROM outbound_media WHERE created_at>=?) < ?`,
  ).bind(
    fingerprint, inboundMessageId, subjectId, artifactType, sha256, mime, fileBuffer.byteLength, caption || null, now, now,
    monthStart, fileBuffer.byteLength, bytesLimit, monthStart, uploadLimit,
  ).run();
  if (!Number(reservation.meta.changes || 0)) {
    const existing = await env.DB.prepare(
      "SELECT status,meta_media_id,meta_message_id,byte_size,mime_type,error FROM outbound_media WHERE fingerprint=?",
    ).bind(fingerprint).first<JsonRecord>();
    if (existing) {
      const existingStatus = String(existing.status || "processing");
      if (["processing", "sent", "delivered", "read"].includes(existingStatus)) {
        return json({
          success: true,
          status: existingStatus,
          duplicate: true,
          fingerprint,
          meta_message_id: String(existing.meta_message_id || ""),
          bytes: Number(existing.byte_size || fileBuffer.byteLength),
          mime: String(existing.mime_type || mime),
        });
      }
      return json({ success: false, status: existingStatus, error: String(existing.error || "outbound_image_previous_failure") }, 409);
    }
    await audit(env, "proactive_image_blocked", subjectId, { event_type: eventType, reason: "outbound_media_month_limit" });
    return json({ success: false, error: "outbound_media_month_limit" }, 429);
  }
  await counterAdd(env, `media_outbound_uploads:${monthKey()}`, 1);

  const uploadForm = new FormData();
  uploadForm.set("messaging_product", "whatsapp");
  uploadForm.set("file", new File([fileBuffer], fileName, { type: mime }));
  const uploadResponse = await graphRequest(env, `${env.META_PHONE_NUMBER_ID}/media`, { method: "POST", body: uploadForm });
  const uploadPayload = await responsePayload(uploadResponse);
  const mediaId = String(uploadPayload.id || "");
  if (!uploadResponse.ok || !mediaId) {
    await env.DB.prepare("UPDATE outbound_media SET status='failed',error=?,updated_at=? WHERE fingerprint=?")
      .bind(JSON.stringify(uploadPayload).slice(0, 800), nowSeconds(), fingerprint).run();
    await audit(env, "proactive_image_failed", subjectId, { event_type: eventType, stage: "meta_media_upload", status: uploadResponse.status });
    return json({ success: false, stage: "meta_media_upload", error: uploadPayload }, 502);
  }
  await env.DB.prepare("UPDATE outbound_media SET meta_media_id=?,updated_at=? WHERE fingerprint=?")
    .bind(mediaId, nowSeconds(), fingerprint).run();

  const image: JsonRecord = { id: mediaId };
  if (caption) image.caption = caption;
  const sendResponse = await graphRequest(env, `${env.META_PHONE_NUMBER_ID}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messaging_product: "whatsapp", recipient_type: "individual", to: recipient, type: "image", image }),
  });
  const sendPayload = await responsePayload(sendResponse);
  if (!sendResponse.ok) {
    await env.DB.prepare("UPDATE outbound_media SET status='failed',error=?,updated_at=? WHERE fingerprint=?")
      .bind(JSON.stringify(sendPayload).slice(0, 800), nowSeconds(), fingerprint).run();
    await audit(env, "proactive_image_failed", subjectId, { event_type: eventType, stage: "meta_message_send", status: sendResponse.status, media_id: mediaId });
    return json({ success: false, stage: "meta_message_send", error: sendPayload }, 502);
  }
  const messages = Array.isArray(sendPayload.messages) ? sendPayload.messages : [];
  const metaMessageId = String(((messages[0] || {}) as JsonRecord).id || "");
  await env.DB.prepare("UPDATE outbound_media SET status='sent',meta_message_id=?,sent_at=?,updated_at=?,error=NULL WHERE fingerprint=?")
    .bind(metaMessageId || null, nowSeconds(), nowSeconds(), fingerprint).run();
  await audit(env, "proactive_image_sent", subjectId, {
    event_type: eventType,
    caller_fingerprint: callerFingerprint,
    fingerprint,
    meta_message_id: metaMessageId,
    bytes: fileBuffer.byteLength,
    mime,
    sha256,
  });
  return json({ success: true, status: "sent", fingerprint, meta_message_id: metaMessageId, bytes: fileBuffer.byteLength, mime });
}

async function proactive(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const subjectId = String(body.subject_id || "").trim();
  const fingerprint = String(body.fingerprint || "").trim();
  const eventType = String(body.event_type || "").trim();
  const severity = normalizeSeverity(body.severity);
  const textParts = compactReplyParts(
    body.text_parts,
    body.text || "",
    body.allow_full_history === true ? 0 : 8,
  );
  const textBody = textParts[0] || "";
  if (!subjectId || !fingerprint || !eventType || !textParts.length) return json({ success: false, error: "invalid_proactive_payload" }, 400);
  const isTask = ["task_completed", "task_failed", "task_awaiting_approval"].includes(eventType);
  const isScheduledReport = ["weekly_report", "monthly_report"].includes(eventType);
  if (!isTask && !isScheduledReport && !["high", "critical"].includes(severity)) return json({ success: true, status: "ignored_low_severity" });
  const existing = await env.DB.prepare("SELECT fingerprint FROM proactive_events WHERE fingerprint=?").bind(fingerprint).first();
  if (existing) return json({ success: true, status: "duplicate" });
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
    await queueOutbound(
      env,
      subjectId,
      subjectId,
      part,
      `${eventType}:${index + 1}/${textParts.length}`,
      textParts.length === 1 ? templateName : "",
      textParts.length === 1 ? params : [],
    );
  }
  await flushOutbox(env, subjectId, Math.min(12, Math.max(3, textParts.length + 1)));
  return json({ success: true, status: eligibility.allowed ? "queued" : eligibility.reason, queued_parts: textParts.length });
}

async function interactiveApproval(request: Request, env: Env): Promise<Response> {
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
  const validApprovalButtons = buttons.length === 3 && buttons.every((item) =>
    /^ppv_(?:approve|reject|regenerate):[A-Z2-9]{8}$/.test(item.id)
    && ["Aprovar", "Negar", "Gerar nova resposta"].includes(item.title),
  );
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
  const validEventPayload = (
    eventType === "question_approval" && validApprovalButtons
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
  const now = nowSeconds();
  const reservation = await env.DB.prepare(
    "INSERT OR IGNORE INTO proactive_events(fingerprint,subject_id,event_type,severity,text_body,status,created_at) VALUES(?,?,?,?,?,'processing',?)",
  ).bind(fingerprint, subjectId, eventType, "info", textBody, now).run();
  if (!Number(reservation.meta.changes || 0)) return json({ success: true, status: "duplicate" });
  const useList = eventType === "store_selection" && options.length > 3;
  const interactive: JsonRecord = useList
    ? {
      type: "list",
      body: { text: textBody },
      action: {
        button: compactReply(body.button_label || "Ver lojas", 20),
        sections: [{
          title: "Lojas disponiveis",
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
  await env.DB.prepare("UPDATE proactive_events SET status='sent' WHERE fingerprint=?").bind(fingerprint).run();
  await audit(env, `interactive_${eventType}_sent`, subjectId, { fingerprint, meta_message_id: metaMessageId });
  return json({ success: true, status: "sent", meta_message_id: metaMessageId });
}

async function syncTemplates(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const createMissing = body.create_missing === true;
  if (createMissing) {
    for (const definition of TEMPLATE_DEFINITIONS) {
      await graphRequest(env, `${env.META_WABA_ID}/message_templates`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(definition) });
    }
  }
  const response = await graphRequest(env, `${env.META_WABA_ID}/message_templates?fields=name,status,category,language,components&limit=100`);
  const payload = (await response.json()) as JsonRecord;
  if (!response.ok) return json({ success: false, error: payload }, 502);
  const now = nowSeconds();
  let synced = 0;
  for (const raw of Array.isArray(payload.data) ? payload.data : []) {
    const item = (raw || {}) as JsonRecord;
    const name = String(item.name || "");
    if (!TEMPLATE_DEFINITIONS.some((definition) => definition.name === name)) continue;
    await env.DB.prepare("INSERT INTO template_registry(name,language,category,status,components_json,last_verified_at) VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET language=excluded.language,category=excluded.category,status=excluded.status,components_json=excluded.components_json,last_verified_at=excluded.last_verified_at")
      .bind(name, String(item.language || "pt_BR"), String(item.category || ""), String(item.status || ""), JSON.stringify(item.components || []), now).run();
    synced += 1;
  }
  return json({ success: true, synced });
}

async function revokeBinding(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const clientId = String(body.client_id || "").trim();
  const username = String(body.username || "").trim().toLowerCase();
  const subjectId = String(body.subject_id || "").trim();
  const revokeAll = body.revoke_all === true;
  if (!clientId || !username || (!subjectId && !revokeAll)) return json({ success: false, error: "invalid_revoke_payload" }, 400);
  const now = nowSeconds();
  const result = subjectId
    ? await env.DB.prepare("UPDATE bindings SET active=0,revoked_at=? WHERE subject_id=? AND client_id=? AND username=? AND active=1").bind(now, subjectId, clientId, username).run()
    : await env.DB.prepare("UPDATE bindings SET active=0,revoked_at=? WHERE client_id=? AND username=? AND active=1").bind(now, clientId, username).run();
  const remaining = await env.DB.prepare("SELECT COUNT(*) AS total FROM bindings WHERE client_id=? AND username=? AND active=1")
    .bind(clientId, username).first<{ total: number }>();
  await audit(env, "binding_revoked", subjectId, { client_id: clientId, username, revoke_all: revokeAll, changed: result.meta.changes, remaining: Number(remaining?.total || 0) });
  return json({ success: true, revoked: result.meta.changes, remaining: Number(remaining?.total || 0) });
}

async function cleanup(env: Env): Promise<void> {
  const now = nowSeconds();
  const expired = await env.DB.prepare("SELECT message_id,media_object_key,media_size FROM inbox WHERE media_object_key IS NOT NULL AND received_at<? LIMIT 100").bind(now - 86400).all<JsonRecord>();
  for (const item of expired.results || []) {
    await env.MEDIA.delete(String(item.media_object_key || ""));
    await env.DB.prepare("UPDATE inbox SET media_object_key=NULL WHERE message_id=?").bind(String(item.message_id || "")).run();
    await counterAdd(env, "media_active_bytes", -Number(item.media_size || 0));
  }
  await env.DB.batch([
    env.DB.prepare("DELETE FROM pairing_codes WHERE expires_at<?").bind(now - 86400),
    env.DB.prepare("DELETE FROM audit_events WHERE created_at<?").bind(now - 30 * 86400),
    env.DB.prepare("DELETE FROM message_status WHERE status_at<?").bind(now - 30 * 86400),
    env.DB.prepare("DELETE FROM outbound_media WHERE created_at<?").bind(now - 90 * 86400),
    env.DB.prepare("DELETE FROM usage_counters WHERE counter_key LIKE 'typing_pulses:%' AND updated_at<?").bind(now - 7 * 86400),
  ]);
}

async function bridgeRoute(request: Request, env: Env): Promise<Response> {
  if (!bridgeAuthorized(request, env)) return json({ success: false, error: "unauthorized" }, 401);
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/bridge/status") return bridgeStatus(env);
  if (request.method === "GET" && url.pathname === "/bridge/meta/profile") return businessProfile(env);
  if (request.method === "POST" && url.pathname === "/bridge/pairing-codes") return createPairingCode(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/bindings/register") return registerBinding(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/welcome") return welcomeMessage(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/messages/send") return adhocMessage(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/claim") return claimMessages(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/proactive") return proactive(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/proactive/image") return proactiveImage(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/interactive") return interactiveApproval(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/templates/sync") return syncTemplates(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/meta/finalize") return finalizeMetaWebhook(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/meta/profile/photo") return updateProfilePhoto(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/bindings/revoke") return revokeBinding(request, env);
  const mediaMatch = url.pathname.match(/^\/bridge\/media\/([^/]+)$/);
  if (request.method === "GET" && mediaMatch) return bridgeMedia(request, env, decodeURIComponent(mediaMatch[1]));
  const imageMatch = url.pathname.match(/^\/bridge\/messages\/([^/]+)\/image$/);
  if (request.method === "POST" && imageMatch) return messageImage(request, env, decodeURIComponent(imageMatch[1]));
  const typingMatch = url.pathname.match(/^\/bridge\/messages\/([^/]+)\/typing$/);
  if (request.method === "POST" && typingMatch) return messageTyping(request, env, decodeURIComponent(typingMatch[1]));
  const resultMatch = url.pathname.match(/^\/bridge\/messages\/([^/]+)\/result$/);
  if (request.method === "POST" && resultMatch) return messageResult(request, env, decodeURIComponent(resultMatch[1]));
  return json({ success: false, error: "not_found" }, 404);
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const path = new URL(request.url).pathname;
    if (path === "/webhooks/whatsapp") return handleWebhook(request, env, ctx);
    if (path.startsWith("/bridge/")) return bridgeRoute(request, env);
    return json({
      success: true,
      service: "jk-whatsapp-gateway",
      zero_cost: true,
      gateway_protocol_version: GATEWAY_PROTOCOL_VERSION,
      build_version: GATEWAY_BUILD_VERSION,
    });
  },
  async scheduled(_controller: ScheduledController, env: Env, _ctx: ExecutionContext): Promise<void> {
    await flushOutbox(env, "", 10);
    await cleanup(env);
  },
};
