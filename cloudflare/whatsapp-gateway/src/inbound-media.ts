import { mediaPolicy } from "./core";
import { audit, counterAdd, counterGet, dayKey, Env, graphRequest, intEnv, json, JsonRecord, monthKey, nowSeconds } from "./shared";

export const INBOUND_MEDIA_MAX_ATTEMPTS = 5;

export const INBOUND_MEDIA_RETRY_DELAYS_SECONDS = [5 * 60, 15 * 60, 30 * 60, 60 * 60] as const;

export const INBOUND_MEDIA_RETENTION_SECONDS = 48 * 60 * 60;

export const INBOUND_MEDIA_FETCH_LEASE_SECONDS = 3 * 60;

export const INBOUND_MEDIA_FETCH_TIMEOUT_MS = 20_000;

export const INBOUND_MEDIA_RETRY_BATCH = 10;

export const INBOUND_MEDIA_MAX_REDIRECTS = 3;

export const META_MEDIA_EXACT_HOSTS = new Set([
  "lookaside.fbsbx.com",
  "lookaside.facebook.com",
  "scontent.whatsapp.net",
]);

export function inboundMediaRetryDelaySeconds(attempt: number): number {
  return INBOUND_MEDIA_RETRY_DELAYS_SECONDS[
    Math.max(0, Math.min(INBOUND_MEDIA_RETRY_DELAYS_SECONDS.length - 1, Number(attempt || 1) - 1))
  ];
}

export class InboundMediaError extends Error {
  constructor(
    readonly errorClass: string,
    readonly retryable: boolean,
  ) {
    super(errorClass);
    this.name = "InboundMediaError";
  }
}

export function inboundMediaHttpError(stage: "metadata" | "download", status: number): InboundMediaError {
  if (status === 404) return new InboundMediaError(`meta_${stage}_not_found`, true);
  if (status === 429) return new InboundMediaError(`meta_${stage}_rate_limited`, true);
  if (status >= 500) return new InboundMediaError(`meta_${stage}_server_error`, true);
  return new InboundMediaError(`meta_${stage}_client_error`, false);
}

export function classifyInboundMediaError(error: unknown): InboundMediaError {
  if (error instanceof InboundMediaError) return error;
  const name = String((error as { name?: unknown } | null)?.name || "").toLowerCase();
  const detail = error instanceof Error ? error.message : String(error || "");
  if (name === "aborterror" || name === "timeouterror" || /tim(?:e|ed)[ -]?out|timeout/i.test(detail)) {
    return new InboundMediaError("media_fetch_timeout", true);
  }
  if (/META_GRAPH_API_VERSION|META_SYSTEM_USER_TOKEN/i.test(detail)) {
    return new InboundMediaError("media_configuration_error", false);
  }
  if (error instanceof TypeError || /network|connection|socket|fetch failed/i.test(detail)) {
    return new InboundMediaError("media_network_error", true);
  }
  return new InboundMediaError("media_dependency_error", true);
}

export function validatedMetaMediaUrl(value: unknown): URL {
  let parsed: URL;
  try {
    parsed = new URL(String(value || ""));
  } catch {
    throw new InboundMediaError("meta_media_url_not_allowed", false);
  }
  const hostname = parsed.hostname.toLowerCase();
  const officialHost = META_MEDIA_EXACT_HOSTS.has(hostname) || hostname.endsWith(".fbcdn.net");
  if (
    parsed.protocol !== "https:"
    || !officialHost
    || Boolean(parsed.username || parsed.password)
    || (parsed.port && parsed.port !== "443")
  ) {
    throw new InboundMediaError("meta_media_url_not_allowed", false);
  }
  return parsed;
}

export async function fetchAllowedMetaMedia(env: Env, initialUrl: unknown): Promise<Response> {
  let url = validatedMetaMediaUrl(initialUrl);
  for (let redirects = 0; redirects <= INBOUND_MEDIA_MAX_REDIRECTS; redirects += 1) {
    const response = await fetch(url.toString(), {
      headers: { authorization: `Bearer ${env.META_SYSTEM_USER_TOKEN}` },
      redirect: "manual",
      signal: AbortSignal.timeout(INBOUND_MEDIA_FETCH_TIMEOUT_MS),
    });
    if (![301, 302, 303, 307, 308].includes(response.status)) return response;
    if (redirects >= INBOUND_MEDIA_MAX_REDIRECTS) {
      throw new InboundMediaError("meta_media_redirect_limit", false);
    }
    const location = response.headers.get("location");
    if (!location) throw new InboundMediaError("meta_media_redirect_invalid", false);
    url = validatedMetaMediaUrl(new URL(location, url).toString());
  }
  throw new InboundMediaError("meta_media_redirect_limit", false);
}

export async function readResponseBodyLimited(response: Response, maxBytes: number): Promise<ArrayBuffer> {
  const declaredLength = Number.parseInt(String(response.headers.get("content-length") || "0"), 10);
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
    throw new InboundMediaError("media_size_limit", false);
  }
  if (!response.body) throw new InboundMediaError("meta_download_empty", true);
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value?.byteLength) continue;
      total += value.byteLength;
      if (total > maxBytes) {
        try {
          await reader.cancel("media_size_limit");
        } catch {
          // Preserve the stable size-limit failure even if stream cancellation
          // itself reports a transport error.
        }
        throw new InboundMediaError("media_size_limit", false);
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const joined = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    joined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return joined.buffer;
}

export function inboundMediaSchemaMissing(error: unknown): boolean {
  return /no such column:\s*(?:media_|idempotency_key)|has no column named\s+(?:media_|idempotency_key)/i.test(
    error instanceof Error ? error.message : String(error || ""),
  );
}

export async function mediaQuotaAllowed(env: Env, declaredSize: number): Promise<boolean> {
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

export type InboundMediaPayload = {
  body: ArrayBuffer;
  mime: string;
  extension: string;
};

export async function inboundMediaAudit(env: Env, eventType: string, detail: JsonRecord): Promise<void> {
  try {
    await audit(env, eventType, "", detail);
  } catch {
    // Telemetry must never change delivery state. Details contain only stable
    // codes, attempt numbers, sizes and MIME types -- no content or identity.
  }
}

export async function fetchInboundMedia(
  env: Env,
  mediaId: string,
  messageType: string,
  declaredMime: string,
): Promise<InboundMediaPayload> {
  const metadataResp = await graphRequest(env, mediaId, { signal: AbortSignal.timeout(INBOUND_MEDIA_FETCH_TIMEOUT_MS) });
  if (!metadataResp.ok) throw inboundMediaHttpError("metadata", metadataResp.status);
  const metadata = (await metadataResp.json()) as JsonRecord;
  const url = String(metadata.url || "");
  if (!url) throw new InboundMediaError("meta_metadata_missing_url", true);
  const mime = String(metadata.mime_type || declaredMime || "").split(";", 1)[0].trim().toLowerCase();
  const declaredSize = Number(metadata.file_size || 0);
  const policy = mediaPolicy(messageType, mime);
  if (!policy.allowed) throw new InboundMediaError("media_type_not_allowed", false);
  if (declaredSize > policy.maxBytes) throw new InboundMediaError("media_size_limit", false);
  if (!(await mediaQuotaAllowed(env, declaredSize))) throw new InboundMediaError("media_quota_exceeded", false);
  const mediaResp = await fetchAllowedMetaMedia(env, url);
  if (!mediaResp.ok) throw inboundMediaHttpError("download", mediaResp.status);
  const body = await readResponseBodyLimited(mediaResp, policy.maxBytes);
  if (!(await mediaQuotaAllowed(env, body.byteLength))) throw new InboundMediaError("media_quota_exceeded", false);
  const extension = mime === "image/jpeg" ? "jpg" : mime === "image/png" ? "png" : mime.split("/", 2)[1]?.replace(/[^a-z0-9]/g, "") || "bin";
  return { body, mime, extension };
}

export async function storeLegacyInboundMedia(env: Env, messageId: string, payload: InboundMediaPayload): Promise<void> {
  const objectKey = `incoming/${new Date().toISOString().slice(0, 10)}/${crypto.randomUUID()}.${payload.extension}`;
  await env.MEDIA.put(objectKey, payload.body, {
    expirationTtl: INBOUND_MEDIA_RETENTION_SECONDS,
    metadata: { contentType: payload.mime, expiresAt: String(nowSeconds() + INBOUND_MEDIA_RETENTION_SECONDS) },
  });
  let stored = false;
  try {
    const result = await env.DB.prepare(
      "UPDATE inbox SET media_mime=?,media_size=?,media_object_key=?,media_filename=?,status='queued',error=NULL WHERE message_id=? AND status='media_fetching' AND media_object_key IS NULL",
    ).bind(payload.mime, payload.body.byteLength, objectKey, `whatsapp_${messageId}.${payload.extension}`, messageId).run();
    stored = Number(result.meta.changes || 0) > 0;
  } finally {
    if (!stored) await env.MEDIA.delete(objectKey);
  }
  if (!stored) return;
  await Promise.allSettled([
    counterAdd(env, "media_active_bytes", payload.body.byteLength),
    counterAdd(env, `media_uploads:${monthKey()}`, 1),
    counterAdd(env, `media_uploads_day:${dayKey()}`, 1),
    inboundMediaAudit(env, "media_stored_legacy", { bytes: payload.body.byteLength, mime: payload.mime }),
  ]);
}

export async function downloadMediaLegacy(
  env: Env,
  messageId: string,
  mediaId: string,
  messageType: string,
  declaredMime: string,
): Promise<void> {
  try {
    await storeLegacyInboundMedia(env, messageId, await fetchInboundMedia(env, mediaId, messageType, declaredMime));
  } catch (error) {
    const classified = classifyInboundMediaError(error);
    await env.DB.prepare("UPDATE inbox SET status='failed',error=?,completed_at=? WHERE message_id=? AND status='media_fetching'")
      .bind(classified.errorClass, nowSeconds(), messageId).run();
    await inboundMediaAudit(env, "media_failed_legacy", { error_class: classified.errorClass });
  }
}

export async function claimInboundMediaAttempt(env: Env, messageId: string): Promise<JsonRecord | null> {
  const now = nowSeconds();
  const claim = await env.DB.prepare(
    "UPDATE inbox SET media_state='fetching',media_attempts=media_attempts+1,media_last_attempt_at=?,media_lease_until=?,media_next_attempt_at=NULL "
    + "WHERE message_id=? AND media_id IS NOT NULL AND media_object_key IS NULL AND media_state IN ('pending','retry_wait') "
    + "AND COALESCE(media_next_attempt_at,0)<=? AND COALESCE(media_expires_at,0)>? AND media_attempts<?",
  ).bind(now, now + INBOUND_MEDIA_FETCH_LEASE_SECONDS, messageId, now, now, INBOUND_MEDIA_MAX_ATTEMPTS).run();
  if (!Number(claim.meta.changes || 0)) return null;
  return env.DB.prepare(
    "SELECT message_id,message_type,media_id,media_mime,media_attempts,media_expires_at FROM inbox WHERE message_id=? AND media_state='fetching'",
  ).bind(messageId).first<JsonRecord>();
}

export async function storeInboundMedia(env: Env, row: JsonRecord, payload: InboundMediaPayload): Promise<boolean> {
  const messageId = String(row.message_id || "");
  const attempt = Number(row.media_attempts || 0);
  const expiresAt = Number(row.media_expires_at || 0);
  if (!expiresAt || expiresAt <= nowSeconds() + 60) throw new InboundMediaError("media_retention_expired", false);
  const objectKey = `incoming/${new Date().toISOString().slice(0, 10)}/${crypto.randomUUID()}.${payload.extension}`;
  await env.MEDIA.put(objectKey, payload.body, {
    expiration: expiresAt,
    metadata: { contentType: payload.mime, expiresAt: String(expiresAt) },
  });
  let stored = false;
  try {
    const result = await env.DB.prepare(
      "UPDATE inbox SET media_mime=?,media_size=?,media_object_key=?,media_filename=?,status='queued',media_state='stored',"
      + "media_next_attempt_at=NULL,media_lease_until=NULL,media_error_class=NULL,error=NULL,completed_at=NULL "
      + "WHERE message_id=? AND media_state='fetching' AND media_attempts=? AND media_object_key IS NULL",
    ).bind(
      payload.mime,
      payload.body.byteLength,
      objectKey,
      `whatsapp_${messageId}.${payload.extension}`,
      messageId,
      attempt,
    ).run();
    stored = Number(result.meta.changes || 0) > 0;
  } finally {
    if (!stored) await env.MEDIA.delete(objectKey);
  }
  if (!stored) return false;
  await Promise.allSettled([
    counterAdd(env, "media_active_bytes", payload.body.byteLength),
    counterAdd(env, `media_uploads:${monthKey()}`, 1),
    counterAdd(env, `media_uploads_day:${dayKey()}`, 1),
    inboundMediaAudit(env, "media_stored", { attempt, bytes: payload.body.byteLength, mime: payload.mime }),
  ]);
  return true;
}

export async function recordInboundMediaFailure(env: Env, row: JsonRecord, error: unknown): Promise<void> {
  const classified = classifyInboundMediaError(error);
  const now = nowSeconds();
  const messageId = String(row.message_id || "");
  const attempt = Number(row.media_attempts || 0);
  const expiresAt = Number(row.media_expires_at || 0);
  const delay = inboundMediaRetryDelaySeconds(attempt);
  const retryable = classified.retryable
    && attempt < INBOUND_MEDIA_MAX_ATTEMPTS
    && expiresAt > now + delay;
  if (retryable) {
    const nextAttemptAt = now + delay;
    const scheduled = await env.DB.prepare(
      "UPDATE inbox SET status='media_retry',media_state='retry_wait',media_next_attempt_at=?,media_lease_until=NULL,media_error_class=?,error=? "
      + "WHERE message_id=? AND media_state='fetching' AND media_attempts=?",
    ).bind(nextAttemptAt, classified.errorClass, classified.errorClass, messageId, attempt).run();
    if (Number(scheduled.meta.changes || 0)) {
      await inboundMediaAudit(env, "media_retry_scheduled", {
        attempt,
        error_class: classified.errorClass,
        delay_seconds: delay,
      });
    }
    return;
  }
  const failed = await env.DB.prepare(
    "UPDATE inbox SET status='failed',media_state='failed',media_next_attempt_at=NULL,media_lease_until=NULL,media_error_class=?,error=?,completed_at=? "
    + "WHERE message_id=? AND media_state='fetching' AND media_attempts=?",
  ).bind(classified.errorClass, classified.errorClass, now, messageId, attempt).run();
  if (Number(failed.meta.changes || 0)) {
    await inboundMediaAudit(env, "media_failed", { attempt, error_class: classified.errorClass });
  }
}

export async function processInboundMediaAttempt(env: Env, messageId: string): Promise<void> {
  const row = await claimInboundMediaAttempt(env, messageId);
  if (!row) return;
  try {
    const payload = await fetchInboundMedia(
      env,
      String(row.media_id || ""),
      String(row.message_type || ""),
      String(row.media_mime || ""),
    );
    await storeInboundMedia(env, row, payload);
  } catch (error) {
    await recordInboundMediaFailure(env, row, error);
  }
}

export async function initializeInboundMedia(
  env: Env,
  messageId: string,
  mediaId: string,
  messageType: string,
  declaredMime: string,
): Promise<void> {
  const now = nowSeconds();
  try {
    await env.DB.prepare(
      "UPDATE inbox SET media_state='pending',media_attempts=0,media_next_attempt_at=?,media_last_attempt_at=NULL,media_lease_until=NULL,"
      + "media_error_class=NULL,media_expires_at=?,error=NULL WHERE message_id=? AND status='media_fetching' AND media_state='none'",
    ).bind(now, now + INBOUND_MEDIA_RETENTION_SECONDS, messageId).run();
  } catch (error) {
    if (!inboundMediaSchemaMissing(error)) throw error;
    await downloadMediaLegacy(env, messageId, mediaId, messageType, declaredMime);
    return;
  }
  await processInboundMediaAttempt(env, messageId);
}

export async function retryInboundMedia(env: Env): Promise<void> {
  const now = nowSeconds();
  try {
    await env.DB.batch([
      env.DB.prepare(
        "UPDATE inbox SET status='failed',media_state='expired',media_next_attempt_at=NULL,media_lease_until=NULL,"
        + "media_error_class='media_retention_expired',error='media_retention_expired',completed_at=? "
        + "WHERE media_id IS NOT NULL AND media_object_key IS NULL AND media_state IN ('none','pending','retry_wait','fetching') "
        + "AND COALESCE(media_expires_at,received_at+?)<=?",
      ).bind(now, INBOUND_MEDIA_RETENTION_SECONDS, now),
      env.DB.prepare(
        "UPDATE inbox SET status='failed',media_state='failed',media_next_attempt_at=NULL,media_lease_until=NULL,"
        + "media_error_class='media_retry_exhausted',error='media_retry_exhausted',completed_at=? "
        + "WHERE media_state='fetching' AND media_lease_until<? AND media_attempts>=?",
      ).bind(now, now, INBOUND_MEDIA_MAX_ATTEMPTS),
      env.DB.prepare(
        "UPDATE inbox SET status='media_retry',media_state='retry_wait',media_next_attempt_at=CASE media_attempts "
        + "WHEN 1 THEN ? WHEN 2 THEN ? WHEN 3 THEN ? ELSE ? END,media_lease_until=NULL,"
        + "media_error_class='media_fetch_lease_expired',error='media_fetch_lease_expired' "
        + "WHERE media_state='fetching' AND media_lease_until<? AND media_attempts<? AND media_expires_at>?",
      ).bind(
        now + INBOUND_MEDIA_RETRY_DELAYS_SECONDS[0],
        now + INBOUND_MEDIA_RETRY_DELAYS_SECONDS[1],
        now + INBOUND_MEDIA_RETRY_DELAYS_SECONDS[2],
        now + INBOUND_MEDIA_RETRY_DELAYS_SECONDS[3],
        now,
        INBOUND_MEDIA_MAX_ATTEMPTS,
        now,
      ),
      env.DB.prepare(
        "UPDATE inbox SET media_state='pending',media_next_attempt_at=COALESCE(media_next_attempt_at,?),"
        + "media_expires_at=COALESCE(media_expires_at,received_at+?) "
        + "WHERE media_id IS NOT NULL AND media_object_key IS NULL AND status='media_fetching' AND media_state='none'",
      ).bind(now, INBOUND_MEDIA_RETENTION_SECONDS),
    ]);
    const due = await env.DB.prepare(
      "SELECT message_id FROM inbox WHERE media_state IN ('pending','retry_wait') AND COALESCE(media_next_attempt_at,0)<=? "
      + "AND media_expires_at>? AND media_attempts<? ORDER BY COALESCE(media_next_attempt_at,received_at),received_at LIMIT ?",
    ).bind(now, now, INBOUND_MEDIA_MAX_ATTEMPTS, INBOUND_MEDIA_RETRY_BATCH).all<JsonRecord>();
    await Promise.allSettled(
      (due.results || []).map((item) => processInboundMediaAttempt(env, String(item.message_id || ""))),
    );
  } catch (error) {
    if (!inboundMediaSchemaMissing(error)) throw error;
  }
}

export async function inboundMediaStatus(env: Env, machineId: string): Promise<JsonRecord> {
  try {
    const counts = await env.DB.prepare(
      "SELECT "
      + "SUM(CASE WHEN i.media_state='retry_wait' THEN 1 ELSE 0 END) AS waiting_retry,"
      + "SUM(CASE WHEN i.media_state='fetching' THEN 1 ELSE 0 END) AS fetching,"
      + "SUM(CASE WHEN i.media_state='stored' THEN 1 ELSE 0 END) AS stored,"
      + "SUM(CASE WHEN i.media_state='failed' THEN 1 ELSE 0 END) AS failed,"
      + "SUM(CASE WHEN i.media_state='expired' THEN 1 ELSE 0 END) AS expired "
      + "FROM inbox i JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 "
      + "WHERE i.media_id IS NOT NULL AND b.machine_id=?",
    ).bind(machineId).first<JsonRecord>();
    const failures = await env.DB.prepare(
      "SELECT i.media_error_class AS error_class,COUNT(*) AS total FROM inbox i "
      + "JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 "
      + "WHERE i.media_id IS NOT NULL AND i.media_error_class IS NOT NULL AND b.machine_id=? "
      + "GROUP BY i.media_error_class ORDER BY total DESC,error_class LIMIT 30",
    ).bind(machineId).all<JsonRecord>();
    return {
      durable_retry: true,
      max_attempts: INBOUND_MEDIA_MAX_ATTEMPTS,
      retry_delays_seconds: [...INBOUND_MEDIA_RETRY_DELAYS_SECONDS],
      retention_seconds: INBOUND_MEDIA_RETENTION_SECONDS,
      counts: counts || {},
      failures_by_code: (failures.results || []).map((item) => ({
        error_class: String(item.error_class || "unknown").slice(0, 100),
        total: Number(item.total || 0),
      })),
    };
  } catch (error) {
    if (!inboundMediaSchemaMissing(error)) throw error;
    return {
      durable_retry: false,
      migration_required: "0008_inbound_media_retry.sql",
      max_attempts: 1,
      retry_delays_seconds: [],
      retention_seconds: INBOUND_MEDIA_RETENTION_SECONDS,
      counts: {},
      failures_by_code: [],
    };
  }
}

export async function bridgeMedia(request: Request, env: Env, mediaId: string): Promise<Response> {
  const machineId = String(new URL(request.url).searchParams.get("machine_id") || "").trim();
  if (!machineId || machineId.length > 160) return json({ success: false, error: "machine_id_required" }, 400);
  const row = await env.DB.prepare(
    "SELECT i.media_object_key,i.media_mime,i.media_size,i.media_filename FROM inbox i "
    + "JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 WHERE i.message_id=? AND b.machine_id=?",
  ).bind(mediaId, machineId).first<JsonRecord>();
  const key = String(row?.media_object_key || "");
  if (!key) return json({ success: false, error: "media_not_found" }, 404);
  const reads = await counterGet(env, `media_downloads:${monthKey()}`);
  if (reads >= intEnv(env.MEDIA_DOWNLOADS_MONTH_LIMIT, 50000)) return json({ success: false, error: "free_media_download_limit" }, 429);
  const object = await env.MEDIA.get(key, "stream");
  if (!object) return json({ success: false, error: "media_expired" }, 404);
  await Promise.all([
    counterAdd(env, `media_downloads:${monthKey()}`, 1),
    counterAdd(env, `media_downloads:${monthKey()}:${machineId}`, 1),
  ]);
  const headers = new Headers({ "content-type": String(row?.media_mime || "application/octet-stream"), "cache-control": "no-store", "x-jk-filename": encodeURIComponent(String(row?.media_filename || "whatsapp-media")) });
  return new Response(object, { status: 200, headers });
}

export async function releaseMedia(env: Env, messageId: string): Promise<void> {
  const row = await env.DB.prepare("SELECT media_object_key,media_size FROM inbox WHERE message_id=?").bind(messageId).first<JsonRecord>();
  const key = String(row?.media_object_key || "");
  if (!key) return;
  await env.MEDIA.delete(key);
  try {
    await env.DB.prepare(
      "UPDATE inbox SET media_object_key=NULL,media_state='released',media_expires_at=NULL WHERE message_id=? AND media_object_key=?",
    ).bind(messageId, key).run();
  } catch (error) {
    if (!inboundMediaSchemaMissing(error)) throw error;
    await env.DB.prepare("UPDATE inbox SET media_object_key=NULL WHERE message_id=? AND media_object_key=?").bind(messageId, key).run();
  }
  try {
    await counterAdd(env, "media_active_bytes", -Number(row?.media_size || 0));
  } catch {
    await inboundMediaAudit(env, "media_counter_update_failed", { operation: "terminal_release" });
  }
}

export async function deleteInboundMediaObject(env: Env, item: JsonRecord, mediaState: "released" | "expired"): Promise<void> {
  const messageId = String(item.message_id || "");
  const objectKey = String(item.media_object_key || "");
  if (!messageId || !objectKey) return;
  await env.MEDIA.delete(objectKey);
  const cleared = await env.DB.prepare(
    "UPDATE inbox SET media_object_key=NULL,media_state=?,media_expires_at=NULL WHERE message_id=? AND media_object_key=?",
  ).bind(mediaState, messageId, objectKey).run();
  if (Number(cleared.meta.changes || 0)) {
    try {
      await counterAdd(env, "media_active_bytes", -Number(item.media_size || 0));
    } catch {
      await inboundMediaAudit(env, "media_counter_update_failed", { operation: "release" });
    }
  }
}

export async function cleanupInboundMedia(env: Env, now: number): Promise<void> {
  try {
    const terminal = await env.DB.prepare(
      "SELECT message_id,media_object_key,media_size,status FROM inbox WHERE media_object_key IS NOT NULL "
      + "AND status IN ('completed','failed','awaiting_approval','dead_letter','unsupported') LIMIT 100",
    ).all<JsonRecord>();
    for (const item of terminal.results || []) {
      await deleteInboundMediaObject(env, item, "released");
    }

    const expired = await env.DB.prepare(
      "SELECT message_id,media_object_key,media_size,status FROM inbox WHERE media_object_key IS NOT NULL "
      + "AND media_state='stored' AND COALESCE(media_expires_at,received_at+?)<=? LIMIT 100",
    ).bind(INBOUND_MEDIA_RETENTION_SECONDS, now).all<JsonRecord>();
    for (const item of expired.results || []) {
      const reserved = await env.DB.prepare(
        "UPDATE inbox SET status='failed',media_state='expired',media_error_class='media_retention_expired',"
        + "error='media_retention_expired',completed_at=?,lease_owner=NULL,lease_until=NULL "
        + "WHERE message_id=? AND media_object_key=? AND media_state='stored'",
      ).bind(now, String(item.message_id || ""), String(item.media_object_key || "")).run();
      if (Number(reserved.meta.changes || 0)) await deleteInboundMediaObject(env, item, "expired");
    }
  } catch (error) {
    if (!inboundMediaSchemaMissing(error)) throw error;
    const legacyExpired = await env.DB.prepare(
      "SELECT message_id,media_object_key,media_size FROM inbox WHERE media_object_key IS NOT NULL AND received_at<? LIMIT 100",
    ).bind(now - INBOUND_MEDIA_RETENTION_SECONDS).all<JsonRecord>();
    for (const item of legacyExpired.results || []) {
      const objectKey = String(item.media_object_key || "");
      await env.MEDIA.delete(objectKey);
      const cleared = await env.DB.prepare(
        "UPDATE inbox SET media_object_key=NULL,status='failed',error='media_retention_expired',completed_at=? WHERE message_id=? AND media_object_key=?",
      ).bind(now, String(item.message_id || ""), objectKey).run();
      if (Number(cleared.meta.changes || 0)) {
        try {
          await counterAdd(env, "media_active_bytes", -Number(item.media_size || 0));
        } catch {
          await inboundMediaAudit(env, "media_counter_update_failed", { operation: "legacy_release" });
        }
      }
    }
  }
}
