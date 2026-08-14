import { MAX_BINDINGS_PER_USER } from "./bindings";
import { isPolicyValid } from "./core";
import { inboundMediaStatus } from "./inbound-media";
import { INBOUND_MESSAGE_LEASE_SECONDS } from "./messages";
import { counterGet, Env, GATEWAY_BUILD_VERSION, GATEWAY_PROTOCOL_VERSION, intEnv, json, JsonRecord, monthKey, monthStartSeconds, nowSeconds, requestJson } from "./shared";
import { TEMPLATE_DEFINITIONS } from "./templates";

export async function bridgeHeartbeat(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const machineId = String(body.machine_id || "").trim();
  if (!machineId || machineId.length > 160) return json({ success: false, error: "machine_id_required" }, 400);
  const activeMessageIds = [...new Set(
    (Array.isArray(body.active_message_ids) ? body.active_message_ids : [])
      .map((value) => String(value || "").trim().slice(0, 240))
      .filter(Boolean),
  )].slice(0, 100);
  const now = nowSeconds();
  await env.DB.prepare(
    "INSERT INTO bridge_heartbeats(machine_id,client_id,username,app_version,status,last_seen_at,updated_at) VALUES(?,?,?,?,?,?,?) "
    + "ON CONFLICT(machine_id) DO UPDATE SET client_id=excluded.client_id,username=excluded.username,app_version=excluded.app_version,status='online',last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at",
  ).bind(
    machineId,
    String(body.client_id || "").slice(0, 100) || null,
    String(body.username || "").slice(0, 200) || null,
    String(body.app_version || "").slice(0, 60) || null,
    "online",
    now,
    now,
  ).run();
  let renewedLeases = 0;
  if (activeMessageIds.length) {
    const placeholders = activeMessageIds.map(() => "?").join(",");
    const renewed = await env.DB.prepare(
      `UPDATE inbox SET lease_until=? WHERE status='leased' AND lease_owner=? AND message_id IN (${placeholders})`,
    ).bind(now + INBOUND_MESSAGE_LEASE_SECONDS, machineId, ...activeMessageIds).run();
    renewedLeases = Number(renewed.meta.changes || 0);
  }
  return json({
    success: true,
    status: "online",
    last_seen_at: now,
    offline_after_seconds: 30,
    renewed_leases: renewedLeases,
  });
}

export async function bridgeStatus(request: Request, env: Env): Promise<Response> {
  const machineId = String(new URL(request.url).searchParams.get("machine_id") || "").trim();
  if (!machineId || machineId.length > 160) return json({ success: false, error: "machine_id_required" }, 400);
  const activeBinding = await env.DB.prepare(
    "SELECT subject_id FROM bindings WHERE active=1 AND machine_id=? LIMIT 1",
  ).bind(machineId).first<JsonRecord>();
  if (!activeBinding) return json({ success: false, error: "binding_machine_inactive" }, 403);
  const counts = await env.DB.prepare(
    "SELECT "
    + "(SELECT COUNT(*) FROM inbox i JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 WHERE b.machine_id=? AND i.status IN ('queued','media_fetching','media_retry','retry')) AS inbox_pending,"
    + "(SELECT COUNT(*) FROM outbox o JOIN bindings b ON b.subject_id=o.subject_id AND b.active=1 WHERE b.machine_id=? AND o.status IN ('queued','retry','waiting_free_window','policy_recheck_required','template_not_approved')) AS outbox_pending,"
    + "(SELECT COUNT(*) FROM inbox i JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 WHERE b.machine_id=? AND i.status='dead_letter') AS dead_letters,"
    + "(SELECT COUNT(*) FROM outbox o JOIN bindings b ON b.subject_id=o.subject_id AND b.active=1 WHERE b.machine_id=? AND o.status='template_not_approved') AS template_blocked_outbox,"
    + "(SELECT COUNT(*) FROM bindings WHERE active=1 AND machine_id=?) AS bindings,"
    + "(SELECT COUNT(*) FROM template_registry WHERE status='APPROVED' AND category='UTILITY') AS templates",
  ).bind(machineId, machineId, machineId, machineId, machineId).first<JsonRecord>();
  const templates = await env.DB.prepare(
    "SELECT name,language,category,status,last_verified_at FROM template_registry ORDER BY name",
  ).all<JsonRecord>();
  const bindingRows = await env.DB.prepare(
    "SELECT subject_id,wa_id,phone_number,client_id,username,machine_id,is_primary,last_inbound_at,created_at FROM bindings WHERE active=1 AND machine_id=? ORDER BY created_at DESC LIMIT 100",
  ).bind(machineId).all<JsonRecord>();
  const outboundUsage = await env.DB.prepare(
    "SELECT COUNT(*) AS uploads,COALESCE(SUM(om.byte_size),0) AS bytes FROM outbound_media om "
    + "JOIN bindings b ON b.subject_id=om.subject_id AND b.active=1 WHERE b.machine_id=? AND om.created_at>=?",
  ).bind(machineId, monthStartSeconds()).first<JsonRecord>();
  const inboundUsage = await env.DB.prepare(
    "SELECT COALESCE(SUM(CASE WHEN i.media_object_key IS NOT NULL THEN i.media_size ELSE 0 END),0) AS active_bytes,"
    + "SUM(CASE WHEN i.media_object_key IS NOT NULL AND i.received_at>=? THEN 1 ELSE 0 END) AS uploads "
    + "FROM inbox i JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 WHERE b.machine_id=?",
  ).bind(monthStartSeconds(), machineId).first<JsonRecord>();
  const inboxDetails = await env.DB.prepare(
    "SELECT i.message_id,i.subject_id,i.message_type,i.received_at,i.status,i.attempts,i.task_id,i.error,i.completed_at,i.offline_notified_at "
    + "FROM inbox i JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 WHERE b.machine_id=? ORDER BY i.received_at DESC LIMIT 30",
  ).bind(machineId).all<JsonRecord>();
  const outboxDetails = await env.DB.prepare(
    "SELECT o.id,o.inbound_message_id,o.subject_id,o.message_type,o.status,o.attempts,o.created_at,o.updated_at,o.sent_at,o.meta_message_id,o.error,o.next_attempt_at "
    + "FROM outbox o JOIN bindings b ON b.subject_id=o.subject_id AND b.active=1 WHERE b.machine_id=? ORDER BY o.created_at DESC LIMIT 30",
  ).bind(machineId).all<JsonRecord>();
  const deadLetters = await env.DB.prepare(
    "SELECT i.message_id,i.subject_id,i.message_type,i.received_at,i.attempts,i.error,i.completed_at FROM inbox i "
    + "JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 WHERE b.machine_id=? AND i.status='dead_letter' ORDER BY i.completed_at DESC LIMIT 30",
  ).bind(machineId).all<JsonRecord>();
  const heartbeatRows = await env.DB.prepare(
    "SELECT machine_id,client_id,username,app_version,status,last_seen_at,updated_at FROM bridge_heartbeats WHERE machine_id=? ORDER BY last_seen_at DESC LIMIT 30",
  ).bind(machineId).all<JsonRecord>();
  const deliveryStatuses = await env.DB.prepare(
    "SELECT ms.meta_message_id,ms.status,ms.status_at,ms.recipient_id FROM message_status ms WHERE "
    + "EXISTS(SELECT 1 FROM outbox o JOIN bindings b ON b.subject_id=o.subject_id AND b.active=1 WHERE o.meta_message_id=ms.meta_message_id AND b.machine_id=?) "
    + "OR EXISTS(SELECT 1 FROM outbound_media om JOIN bindings b ON b.subject_id=om.subject_id AND b.active=1 WHERE om.meta_message_id=ms.meta_message_id AND b.machine_id=?) "
    + "ORDER BY ms.status_at DESC LIMIT 50",
  ).bind(machineId, machineId).all<JsonRecord>();
  const progressDetails = await env.DB.prepare(
    "SELECT mp.message_id,mp.task_id,mp.sequence,mp.stage,mp.status,mp.created_at,mp.sent_at,mp.error FROM message_progress mp "
    + "JOIN inbox i ON i.message_id=mp.message_id JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 "
    + "WHERE b.machine_id=? ORDER BY mp.created_at DESC LIMIT 50",
  ).bind(machineId).all<JsonRecord>();
  const now = nowSeconds();
  const heartbeats = (heartbeatRows.results || []).map((item) => ({
    ...item,
    online: Number(item.last_seen_at || 0) > now - 30,
    age_seconds: Math.max(0, now - Number(item.last_seen_at || 0)),
  }));
  const templateRows = templates.results || [];
  const templateByName = new Map(templateRows.map((item) => [String(item.name || ""), item]));
  const templateBlockers = TEMPLATE_DEFINITIONS
    .map((definition) => templateByName.get(definition.name) || { name: definition.name, language: definition.language, category: definition.category, status: "MISSING", last_verified_at: 0 })
    .filter((item) => String(item.status || "").toUpperCase() !== "APPROVED");
  const bindings = (bindingRows.results || []).map((item) => ({
    subject_id: String(item.subject_id || ""),
    phone_number: String(item.phone_number || item.wa_id || "").replace(/\D/g, ""),
    phone_suffix: String(item.phone_number || item.wa_id || "").replace(/\D/g, "").slice(-4),
    client_id: String(item.client_id || ""),
    username: String(item.username || ""),
    machine_id: String(item.machine_id || ""),
    last_inbound_at: Number(item.last_inbound_at || 0),
    created_at: Number(item.created_at || 0),
    is_primary: Number(item.is_primary || 0) === 1,
  }));
  const binding = bindings[0];
  return json({
    success: true,
    worker: true,
    gateway_protocol_version: GATEWAY_PROTOCOL_VERSION,
    gateway_capabilities: ["primary_binding_v1", "delivery_receipt_v1"],
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
    inbound_media: await inboundMediaStatus(env, machineId),
    templates: templateRows,
    template_blockers: templateBlockers,
    inbox: inboxDetails.results || [],
    outbox: outboxDetails.results || [],
    dead_letters: deadLetters.results || [],
    delivery_statuses: deliveryStatuses.results || [],
    message_progress: progressDetails.results || [],
    heartbeats,
    last_heartbeat: heartbeats[0] || null,
    binding_limit_per_user: MAX_BINDINGS_PER_USER,
    bindings,
    binding: binding ? {
      paired: true,
      phone_suffix: binding.phone_suffix,
      client_id: binding.client_id,
      username: binding.username,
      machine_id: binding.machine_id,
      last_inbound_at: binding.last_inbound_at,
      is_primary: binding.is_primary,
    } : { paired: false },
    meta: {
      configured: Boolean(env.META_ACCESS_TOKEN && env.META_APP_SECRET && env.META_PHONE_NUMBER_ID && env.META_WABA_ID && env.META_APP_ID),
      graph_api_version: env.META_GRAPH_API_VERSION,
    },
    usage: {
      scoped_to_machine: true,
      media_active_bytes: Number(inboundUsage?.active_bytes || 0),
      media_uploads_month: Number(inboundUsage?.uploads || 0),
      media_outbound_uploads_month: Number(outboundUsage?.uploads || 0),
      media_outbound_bytes_month: Number(outboundUsage?.bytes || 0),
      media_outbound_uploads_limit: intEnv(env.MEDIA_OUTBOUND_UPLOADS_MONTH_LIMIT, 10000),
      media_outbound_bytes_limit: intEnv(env.MEDIA_OUTBOUND_BYTES_MONTH_LIMIT, 900 * 1024 * 1024),
      media_downloads_month: await counterGet(env, `media_downloads:${monthKey()}:${machineId}`),
      typing_pulses_day: null,
      typing_pulses_day_limit: intEnv(env.TYPING_PULSES_DAY_LIMIT, 10000),
    },
  });
}
