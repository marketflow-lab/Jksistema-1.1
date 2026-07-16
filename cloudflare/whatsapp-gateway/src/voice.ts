import {
  phoneAliases,
  phoneFromSipHeaders,
  sha256Hex,
  verifyOpenAIWebhook,
} from "./core";

type JsonRecord = Record<string, unknown>;

export interface VoiceEnv {
  DB: D1Database;
  OPENAI_REALTIME_API_KEY?: string;
  OPENAI_WEBHOOK_SECRET?: string;
  OPENAI_PROJECT_ID?: string;
  VOICE_SIP_HOST?: string;
  VOICE_REALTIME_MODEL?: string;
  VOICE_TRANSCRIPTION_MODEL?: string;
  VOICE_DEFAULT_NAME?: string;
  VOICE_MAX_CONCURRENT_CALLS?: string;
}

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const ACTIVE_STATUSES = ["accepted", "leased", "connected", "processing"];
const HEARTBEAT_MAX_AGE_SECONDS = 15;

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

function randomId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "")}`;
}

function intSetting(value: unknown, fallback: number): number {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

async function requestJson(request: Request): Promise<JsonRecord> {
  try {
    const parsed = await request.json();
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as JsonRecord : {};
  } catch {
    return {};
  }
}

async function audit(env: VoiceEnv, eventType: string, subjectId = "", detail: JsonRecord = {}): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO audit_events(id,event_type,subject_id,detail_json,created_at) VALUES(?,?,?,?,?)",
  ).bind(randomId("audit"), eventType, subjectId || null, JSON.stringify(detail).slice(0, 2000), nowSeconds()).run();
}

async function apiKeyFingerprint(env: VoiceEnv): Promise<string> {
  const key = String(env.OPENAI_REALTIME_API_KEY || "").trim();
  return key ? (await sha256Hex(key)).slice(0, 16) : "";
}

async function openAICallAction(
  env: VoiceEnv,
  callId: string,
  action: "accept" | "reject" | "hangup",
  body?: JsonRecord,
): Promise<{ ok: boolean; status: number; detail: string }> {
  const apiKey = String(env.OPENAI_REALTIME_API_KEY || "").trim();
  if (!apiKey) return { ok: false, status: 503, detail: "openai_api_key_missing" };
  const response = await fetch(
    `https://api.openai.com/v1/realtime/calls/${encodeURIComponent(callId)}/${action}`,
    {
      method: "POST",
      headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    },
  );
  return { ok: response.ok, status: response.status, detail: response.ok ? "" : (await response.text()).slice(0, 500) };
}

async function callEvent(env: VoiceEnv, callId: string, eventType: string, status: string, detail: JsonRecord = {}): Promise<void> {
  await env.DB.prepare(
    `INSERT OR IGNORE INTO voice_call_events(id,call_id,sequence,event_type,status,detail_json,created_at)
     SELECT ?,?,COALESCE(MAX(sequence),0)+1,?,?,?,? FROM voice_call_events WHERE call_id=?`,
  ).bind(
    randomId("voice_event"), callId, eventType.slice(0, 80), status.slice(0, 40), JSON.stringify(detail).slice(0, 2000), nowSeconds(), callId,
  ).run();
}

function sipHeaderValue(headers: unknown, name: string): string {
  const rows = Array.isArray(headers) ? headers : [];
  const match = rows.find((item) => item && typeof item === "object" && String((item as JsonRecord).name || "").toLowerCase() === name.toLowerCase()) as JsonRecord | undefined;
  return String(match?.value || "").replace(/[\r\n]/g, "").slice(0, 300);
}

async function bindingForPhone(env: VoiceEnv, phone: string): Promise<JsonRecord | null> {
  const aliases = phoneAliases(phone);
  if (!aliases.length) return null;
  const first = aliases[0];
  const second = aliases[1] || first;
  return env.DB.prepare(
    `SELECT b.*,COALESCE(v.allow_voice_calls,0) AS allow_voice_calls
     FROM bindings b LEFT JOIN voice_phone_settings v ON v.subject_id=b.subject_id
     WHERE b.active=1 AND (b.subject_id IN (?,?) OR b.wa_id IN (?,?) OR b.phone_number IN (?,?))
     ORDER BY b.created_at DESC LIMIT 1`,
  ).bind(first, second, first, second, first, second).first<JsonRecord>();
}

async function rejectCall(
  env: VoiceEnv,
  values: {
    id: string;
    openaiCallId: string;
    webhookId: string;
    metaCallId: string;
    phone: string;
    binding?: JsonRecord | null;
    reason: string;
  },
  sipStatus: number,
): Promise<Response> {
  const now = nowSeconds();
  const binding = values.binding || {};
  await env.DB.prepare(
    `INSERT OR IGNORE INTO voice_calls(
       id,openai_call_id,webhook_id,meta_call_id,subject_id,wa_id,client_id,username,machine_id,
       direction,status,created_at,ended_at,error,idempotency_key
     ) VALUES(?,?,?,?,?,?,?,?,?,'inbound','rejected',?,?,?,?)`,
  ).bind(
    values.id, values.openaiCallId, values.webhookId, values.metaCallId || null,
    String(binding.subject_id || "") || null, values.phone || null, String(binding.client_id || "") || null,
    String(binding.username || "") || null, String(binding.machine_id || "") || null,
    now, now, values.reason.slice(0, 500), values.webhookId,
  ).run();
  const result = await openAICallAction(env, values.openaiCallId, "reject", { status_code: sipStatus });
  await callEvent(env, values.id, "call_rejected", "rejected", { reason: values.reason, sip_status: sipStatus, openai_status: result.status });
  await audit(env, "voice_call_rejected", String(binding.subject_id || ""), { reason: values.reason, sip_status: sipStatus });
  return json({ success: true, accepted: false, status: "rejected" });
}

export async function handleOpenAIRealtimeWebhook(request: Request, env: VoiceEnv): Promise<Response> {
  if (request.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
  const rawText = await request.text();
  if (!(await verifyOpenAIWebhook(rawText, request.headers, env.OPENAI_WEBHOOK_SECRET || ""))) {
    return new Response("Invalid signature", { status: 400 });
  }
  let event: JsonRecord;
  try {
    const parsed = JSON.parse(rawText);
    event = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as JsonRecord : {};
  } catch {
    return json({ success: false, error: "invalid_json" }, 400);
  }
  if (String(event.type || "") !== "realtime.call.incoming") return json({ success: true, ignored: true });
  const data = event.data && typeof event.data === "object" && !Array.isArray(event.data) ? event.data as JsonRecord : {};
  const openaiCallId = String(data.call_id || "").trim();
  const webhookId = String(request.headers.get("webhook-id") || event.id || "").trim();
  if (!openaiCallId || !webhookId) return json({ success: false, error: "call_identity_missing" }, 400);
  const duplicate = await env.DB.prepare("SELECT id,status FROM voice_calls WHERE webhook_id=? OR openai_call_id=? LIMIT 1")
    .bind(webhookId, openaiCallId).first<JsonRecord>();
  if (duplicate) return json({ success: true, duplicate: true, status: String(duplicate.status || "") });

  const phone = phoneFromSipHeaders(data.sip_headers);
  const common = {
    id: randomId("voice_call"),
    openaiCallId,
    webhookId,
    metaCallId: sipHeaderValue(data.sip_headers, "call-id"),
    phone,
  };
  if (!phone) return rejectCall(env, { ...common, reason: "caller_phone_missing" }, 603);
  const binding = await bindingForPhone(env, phone);
  if (!binding) return rejectCall(env, { ...common, reason: "binding_missing" }, 403);
  if (Number(binding.allow_voice_calls || 0) !== 1) return rejectCall(env, { ...common, binding, reason: "voice_not_allowed" }, 403);
  if (!env.OPENAI_REALTIME_API_KEY || !env.OPENAI_PROJECT_ID || !env.VOICE_SIP_HOST) {
    return rejectCall(env, { ...common, binding, reason: "voice_gateway_not_configured" }, 480);
  }

  const now = nowSeconds();
  const machineId = String(binding.machine_id || "");
  const heartbeat = await env.DB.prepare("SELECT * FROM machine_voice_heartbeats WHERE machine_id=?").bind(machineId).first<JsonRecord>();
  const fingerprint = await apiKeyFingerprint(env);
  if (!heartbeat || now - Number(heartbeat.updated_at || 0) > HEARTBEAT_MAX_AGE_SECONDS || Number(heartbeat.capacity || 0) < 1) {
    return rejectCall(env, { ...common, binding, reason: "voice_backend_offline" }, 480);
  }
  if (!fingerprint || String(heartbeat.key_fingerprint || "") !== fingerprint) {
    return rejectCall(env, { ...common, binding, reason: "openai_key_mismatch" }, 480);
  }

  const placeholders = ACTIVE_STATUSES.map(() => "?").join(",");
  const activeGlobal = await env.DB.prepare(`SELECT COUNT(*) AS total FROM voice_calls WHERE status IN (${placeholders})`)
    .bind(...ACTIVE_STATUSES).first<{ total: number }>();
  const activePhone = await env.DB.prepare(`SELECT COUNT(*) AS total FROM voice_calls WHERE wa_id=? AND status IN (${placeholders})`)
    .bind(phone, ...ACTIVE_STATUSES).first<{ total: number }>();
  const configuredLimit = Math.max(1, Math.min(10, intSetting(env.VOICE_MAX_CONCURRENT_CALLS, 3)));
  const capacity = Math.max(0, Math.min(configuredLimit, Number(heartbeat.capacity || 0)));
  if (Number(activeGlobal?.total || 0) >= capacity || Number(activePhone?.total || 0) > 0) {
    return rejectCall(env, { ...common, binding, reason: "voice_capacity_reached" }, 486);
  }

  await env.DB.prepare(
    `INSERT INTO voice_calls(
       id,openai_call_id,webhook_id,meta_call_id,subject_id,wa_id,client_id,username,machine_id,
       direction,status,created_at,idempotency_key
     ) VALUES(?,?,?,?,?,?,?,?,?,'inbound','incoming',?,?)`,
  ).bind(
    common.id, openaiCallId, webhookId, common.metaCallId || null, String(binding.subject_id || ""), phone,
    String(binding.client_id || ""), String(binding.username || ""), machineId, now, webhookId,
  ).run();
  await callEvent(env, common.id, "incoming_webhook", "incoming", { webhook_id: webhookId });

  const accepted = await openAICallAction(env, openaiCallId, "accept", {
    type: "realtime",
    model: String(env.VOICE_REALTIME_MODEL || "gpt-realtime-2.1"),
    instructions: (
      "Voce e a interface de voz do Black Jhon. Fale em portugues brasileiro, de forma natural e breve. "
      + "Nao responda perguntas de negocio sozinho e nao use ferramentas. Aguarde o servidor seguro fornecer cada resposta."
    ),
    audio: {
      input: {
        transcription: { model: String(env.VOICE_TRANSCRIPTION_MODEL || "gpt-4o-transcribe"), language: "pt" },
        turn_detection: { type: "server_vad", create_response: false, interrupt_response: true, silence_duration_ms: 650, prefix_padding_ms: 300 },
      },
      output: { voice: String(env.VOICE_DEFAULT_NAME || "cedar") },
    },
    tool_choice: "none",
    tools: [],
    max_output_tokens: 1200,
  });
  if (!accepted.ok) {
    await env.DB.prepare("UPDATE voice_calls SET status='failed',ended_at=?,error=? WHERE id=?")
      .bind(nowSeconds(), `openai_accept_${accepted.status}:${accepted.detail}`.slice(0, 500), common.id).run();
    await callEvent(env, common.id, "openai_accept", "failed", { status: accepted.status });
    return json({ success: false, error: "openai_accept_failed" }, 502);
  }
  await env.DB.prepare("UPDATE voice_calls SET status='accepted',accepted_at=?,error=NULL WHERE id=?")
    .bind(nowSeconds(), common.id).run();
  await callEvent(env, common.id, "openai_accept", "accepted", { model: String(env.VOICE_REALTIME_MODEL || "gpt-realtime-2.1") });
  await audit(env, "voice_call_accepted", String(binding.subject_id || ""), { call_id: common.id, machine_id: machineId });
  return json({ success: true, accepted: true, call_id: common.id });
}

export async function voiceHeartbeat(request: Request, env: VoiceEnv): Promise<Response> {
  const body = await requestJson(request);
  const machineId = String(body.machine_id || "").trim();
  const capacity = Math.max(0, Math.min(10, Number(body.capacity || 0)));
  const active = Math.max(0, Math.min(capacity, Number(body.active_call_count || 0)));
  const fingerprint = String(body.key_fingerprint || "").trim().toLowerCase();
  if (!machineId || (fingerprint && !/^[a-f0-9]{16}$/.test(fingerprint))) return json({ success: false, error: "invalid_voice_heartbeat" }, 400);
  await env.DB.prepare(
    `INSERT INTO machine_voice_heartbeats(machine_id,updated_at,active_call_count,capacity,key_fingerprint)
     VALUES(?,?,?,?,?) ON CONFLICT(machine_id) DO UPDATE SET
       updated_at=excluded.updated_at,active_call_count=excluded.active_call_count,
       capacity=excluded.capacity,key_fingerprint=excluded.key_fingerprint`,
  ).bind(machineId, nowSeconds(), active, capacity, fingerprint || null).run();
  const gatewayFingerprint = await apiKeyFingerprint(env);
  return json({
    success: true,
    configured: Boolean(env.OPENAI_REALTIME_API_KEY && env.OPENAI_WEBHOOK_SECRET && env.OPENAI_PROJECT_ID && env.VOICE_SIP_HOST),
    key_match: Boolean(gatewayFingerprint && fingerprint && gatewayFingerprint === fingerprint),
  });
}

export async function voicePhoneSettings(request: Request, env: VoiceEnv): Promise<Response> {
  const body = await requestJson(request);
  const subjectId = String(body.subject_id || "").trim();
  const machineId = String(body.machine_id || "").trim();
  const binding = subjectId
    ? await env.DB.prepare("SELECT subject_id,machine_id FROM bindings WHERE subject_id=? AND active=1").bind(subjectId).first<JsonRecord>()
    : null;
  if (!binding) return json({ success: false, error: "binding_missing" }, 404);
  if (!machineId || String(binding.machine_id || "") !== machineId) return json({ success: false, error: "binding_machine_mismatch" }, 403);
  const allowed = body.allow_voice_calls === true ? 1 : 0;
  await env.DB.prepare(
    `INSERT INTO voice_phone_settings(subject_id,allow_voice_calls,updated_at) VALUES(?,?,?)
     ON CONFLICT(subject_id) DO UPDATE SET allow_voice_calls=excluded.allow_voice_calls,updated_at=excluded.updated_at`,
  ).bind(subjectId, allowed, nowSeconds()).run();
  await audit(env, "voice_phone_settings", subjectId, { allow_voice_calls: allowed === 1, machine_id: machineId });
  return json({ success: true, subject_id: subjectId, allow_voice_calls: allowed === 1 });
}

export async function claimVoiceCalls(request: Request, env: VoiceEnv): Promise<Response> {
  const body = await requestJson(request);
  const machineId = String(body.machine_id || "").trim();
  const limit = Math.max(1, Math.min(3, Number(body.limit || 3)));
  if (!machineId) return json({ success: false, error: "machine_id_required" }, 400);
  const now = nowSeconds();
  await env.DB.prepare("UPDATE voice_calls SET status='accepted',lease_owner=NULL,lease_until=NULL WHERE status='leased' AND lease_until<?")
    .bind(now).run();
  const rows = await env.DB.prepare("SELECT * FROM voice_calls WHERE machine_id=? AND status='accepted' ORDER BY created_at LIMIT ?")
    .bind(machineId, limit).all<JsonRecord>();
  const calls: JsonRecord[] = [];
  for (const row of rows.results || []) {
    const changed = await env.DB.prepare("UPDATE voice_calls SET status='leased',lease_owner=?,lease_until=? WHERE id=? AND status='accepted'")
      .bind(machineId, now + 30, String(row.id || "")).run();
    if (!Number(changed.meta.changes || 0)) continue;
    await callEvent(env, String(row.id || ""), "bridge_claim", "leased", { machine_id: machineId });
    calls.push({
      id: String(row.id || ""), openai_call_id: String(row.openai_call_id || ""),
      subject_id: String(row.subject_id || ""), wa_id: String(row.wa_id || ""),
      client_id: String(row.client_id || ""), username: String(row.username || ""),
      machine_id: String(row.machine_id || ""), created_at: Number(row.created_at || 0),
    });
  }
  return json({ success: true, calls });
}

function safeUsage(value: unknown): JsonRecord {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {};
  const result: JsonRecord = {};
  for (const key of ["input_tokens", "output_tokens", "audio_input_tokens", "audio_output_tokens", "total_tokens"]) {
    const number = Number(source[key] || 0);
    if (Number.isFinite(number) && number >= 0) result[key] = Math.floor(number);
  }
  return result;
}

export async function updateVoiceCallState(request: Request, env: VoiceEnv, callId: string): Promise<Response> {
  const body = await requestJson(request);
  const machineId = String(body.machine_id || "").trim();
  const status = String(body.status || "").trim().toLowerCase();
  if (!machineId || !["leased", "connected", "processing", "ended", "failed"].includes(status)) return json({ success: false, error: "invalid_voice_state" }, 400);
  const row = await env.DB.prepare("SELECT * FROM voice_calls WHERE id=? AND machine_id=?").bind(callId, machineId).first<JsonRecord>();
  if (!row) return json({ success: false, error: "voice_call_not_owned" }, 404);
  const now = nowSeconds();
  const terminal = status === "ended" || status === "failed";
  const connectedAt = Number(row.connected_at || 0) || (status === "connected" ? now : 0);
  const duration = terminal ? Math.max(0, now - Number(connectedAt || row.accepted_at || row.created_at || now)) : Number(row.duration_seconds || 0);
  await env.DB.prepare(
    `UPDATE voice_calls SET status=?,connected_at=COALESCE(connected_at,?),ended_at=?,duration_seconds=?,
       usage_json=?,error=?,lease_owner=?,lease_until=? WHERE id=? AND machine_id=?`,
  ).bind(
    status, connectedAt || null, terminal ? now : null, duration, JSON.stringify(safeUsage(body.usage)),
    String(body.error || "").slice(0, 500) || null, terminal ? null : machineId, terminal ? null : now + 30, callId, machineId,
  ).run();
  await callEvent(env, callId, "bridge_state", status, { duration_seconds: duration });
  return json({ success: true, call_id: callId, status, duration_seconds: duration });
}

export async function voiceBridgeStatus(env: VoiceEnv): Promise<Response> {
  const now = nowSeconds();
  const counts = await env.DB.prepare(
    `SELECT
       (SELECT COUNT(*) FROM voice_calls WHERE status IN ('accepted','leased','connected','processing')) AS active_calls,
       (SELECT COUNT(*) FROM voice_calls WHERE created_at>=?) AS calls_today,
       (SELECT COUNT(*) FROM voice_phone_settings WHERE allow_voice_calls=1) AS authorized_phones`,
  ).bind(now - 86400).first<JsonRecord>();
  const calls = await env.DB.prepare(
    `SELECT id,openai_call_id,subject_id,wa_id,client_id,username,machine_id,status,created_at,accepted_at,connected_at,ended_at,duration_seconds,usage_json,error
     FROM voice_calls ORDER BY created_at DESC LIMIT 100`,
  ).all<JsonRecord>();
  const heartbeats = await env.DB.prepare("SELECT machine_id,updated_at,active_call_count,capacity,key_fingerprint FROM machine_voice_heartbeats ORDER BY updated_at DESC LIMIT 20")
    .all<JsonRecord>();
  const fingerprint = await apiKeyFingerprint(env);
  return json({
    success: true,
    configured: Boolean(env.OPENAI_REALTIME_API_KEY && env.OPENAI_WEBHOOK_SECRET && env.OPENAI_PROJECT_ID && env.VOICE_SIP_HOST),
    openai: {
      api_key_configured: Boolean(env.OPENAI_REALTIME_API_KEY), webhook_secret_configured: Boolean(env.OPENAI_WEBHOOK_SECRET),
      project_configured: Boolean(env.OPENAI_PROJECT_ID), key_fingerprint: fingerprint,
      model: String(env.VOICE_REALTIME_MODEL || "gpt-realtime-2.1"),
      transcription_model: String(env.VOICE_TRANSCRIPTION_MODEL || "gpt-4o-transcribe"), voice: String(env.VOICE_DEFAULT_NAME || "cedar"),
    },
    sip: { configured: Boolean(env.VOICE_SIP_HOST), host: String(env.VOICE_SIP_HOST || "") },
    counts: counts || {},
    calls: (calls.results || []).map((item) => {
      let usage: JsonRecord = {};
      try { usage = safeUsage(JSON.parse(String(item.usage_json || "{}"))); } catch { usage = {}; }
      return { ...item, usage_json: "", usage, wa_id: "", phone_suffix: String(item.wa_id || "").replace(/\D/g, "").slice(-4) };
    }),
    heartbeats: (heartbeats.results || []).map((item) => ({
      ...item, key_fingerprint: "", key_match: Boolean(fingerprint && String(item.key_fingerprint || "") === fingerprint),
      fresh: now - Number(item.updated_at || 0) <= HEARTBEAT_MAX_AGE_SECONDS,
    })),
  });
}
