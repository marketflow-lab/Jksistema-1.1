import { zeroCostEligibility } from "./bindings";
import { compactReply, outboundImagePolicy, outboundRecipient, sha256Hex, timingSafeEqualText } from "./core";
import { audit, counterAdd, Env, graphRequest, intEnv, json, JsonRecord, monthKey, monthStartSeconds, nowSeconds, randomId, responsePayload } from "./shared";

export const OUTBOUND_MEDIA_LEASE_SECONDS = 3 * 60;

export const OUTBOUND_MEDIA_MAX_ATTEMPTS = 5;

export const OUTBOUND_IMAGE_ARTIFACT_TYPES = new Set(["product_photo"]);

export const PROACTIVE_IMAGE_FORM_FIELDS = new Set([
  "subject_id",
  "machine_id",
  "fingerprint",
  "event_type",
  "artifact_type",
  "caption",
  "sha256",
  "file",
]);

export interface OutboundMediaLeaseInput {
  fingerprint: string;
  inboundMessageId: string;
  subjectId: string;
  artifactType: string;
  sha256: string;
  mime: string;
  byteSize: number;
  caption: string;
  perResponseLimit: boolean;
  errorPrefix: string;
}

export interface OutboundMediaLeaseResult {
  acquired: boolean;
  inserted: boolean;
  leaseOwner: string;
  metaMediaId: string;
  response?: Response;
}

export function outboundMediaConfirmed(status: string): boolean {
  return ["sent", "delivered", "read"].includes(status);
}

export function outboundMediaDuplicateResponse(existing: JsonRecord, input: OutboundMediaLeaseInput): Response {
  return json({
    success: true,
    status: String(existing.status || "sent"),
    duplicate: true,
    fingerprint: input.fingerprint,
    meta_message_id: String(existing.meta_message_id || ""),
    bytes: Number(existing.byte_size || input.byteSize),
    mime: String(existing.mime_type || input.mime),
  });
}

export async function reserveOutboundMedia(env: Env, input: OutboundMediaLeaseInput): Promise<OutboundMediaLeaseResult> {
  const now = nowSeconds();
  const leaseOwner = randomId("outbound_media_lease");
  const monthStart = monthStartSeconds();
  const bytesLimit = intEnv(env.MEDIA_OUTBOUND_BYTES_MONTH_LIMIT, 900 * 1024 * 1024);
  const uploadLimit = intEnv(env.MEDIA_OUTBOUND_UPLOADS_MONTH_LIMIT, 10000);
  const responseLimitClause = input.perResponseLimit
    ? " AND (SELECT COUNT(*) FROM outbound_media WHERE inbound_message_id=?) < 5"
    : "";
  const reservation = await env.DB.prepare(
    `INSERT OR IGNORE INTO outbound_media(
       fingerprint,inbound_message_id,subject_id,artifact_type,sha256,mime_type,byte_size,caption,status,
       attempts,lease_owner,lease_until,last_attempt_at,created_at,updated_at
     )
     SELECT ?,?,?,?,?,?,?,?,'processing',1,?,?,?,?,?
     WHERE (SELECT COALESCE(SUM(byte_size),0) FROM outbound_media WHERE created_at>=?) + ? <= ?
       AND (SELECT COUNT(*) FROM outbound_media WHERE created_at>=?) < ?${responseLimitClause}`,
  ).bind(
    input.fingerprint,
    input.inboundMessageId,
    input.subjectId,
    input.artifactType,
    input.sha256,
    input.mime,
    input.byteSize,
    input.caption || null,
    leaseOwner,
    now + OUTBOUND_MEDIA_LEASE_SECONDS,
    now,
    now,
    now,
    monthStart,
    input.byteSize,
    bytesLimit,
    monthStart,
    uploadLimit,
    ...(input.perResponseLimit ? [input.inboundMessageId] : []),
  ).run();
  if (Number(reservation.meta.changes || 0)) {
    await counterAdd(env, `media_outbound_uploads:${monthKey()}`, 1);
    return { acquired: true, inserted: true, leaseOwner, metaMediaId: "" };
  }

  let existing = await env.DB.prepare(
    "SELECT status,meta_media_id,meta_message_id,byte_size,mime_type,error,subject_id,artifact_type,sha256,attempts,lease_until "
    + "FROM outbound_media WHERE fingerprint=?",
  ).bind(input.fingerprint).first<JsonRecord>();
  if (!existing) {
    if (input.perResponseLimit) {
      const perResponse = await env.DB.prepare(
        "SELECT COUNT(*) AS total FROM outbound_media WHERE inbound_message_id=?",
      ).bind(input.inboundMessageId).first<{ total: number }>();
      if (Number(perResponse?.total || 0) >= 5) {
        await audit(env, `${input.errorPrefix}_blocked`, input.subjectId, { message_id: input.inboundMessageId, reason: "outbound_media_response_limit" });
        return { acquired: false, inserted: false, leaseOwner, metaMediaId: "", response: json({ success: false, error: "outbound_media_response_limit" }, 429) };
      }
    }
    await audit(env, `${input.errorPrefix}_blocked`, input.subjectId, { message_id: input.inboundMessageId, reason: "outbound_media_month_limit" });
    return { acquired: false, inserted: false, leaseOwner, metaMediaId: "", response: json({ success: false, error: "outbound_media_month_limit" }, 429) };
  }

  const existingStatus = String(existing.status || "processing");
  if (outboundMediaConfirmed(existingStatus)) {
    return { acquired: false, inserted: false, leaseOwner, metaMediaId: String(existing.meta_media_id || ""), response: outboundMediaDuplicateResponse(existing, input) };
  }
  const immutableConflict = (
    String(existing.subject_id || input.subjectId) !== input.subjectId
    || String(existing.artifact_type || input.artifactType) !== input.artifactType
    || String(existing.sha256 || input.sha256) !== input.sha256
    || String(existing.mime_type || input.mime) !== input.mime
    || Number(existing.byte_size || input.byteSize) !== input.byteSize
  );
  if (immutableConflict) {
    return { acquired: false, inserted: false, leaseOwner, metaMediaId: "", response: json({ success: false, error: "outbound_media_fingerprint_conflict" }, 409) };
  }
  const leaseUntil = Number(existing.lease_until || 0);
  if (existingStatus === "processing" && leaseUntil > now) {
    return {
      acquired: false,
      inserted: false,
      leaseOwner,
      metaMediaId: String(existing.meta_media_id || ""),
      response: json({
        success: false,
        status: "processing",
        error: "outbound_media_processing",
        retry_after_seconds: Math.max(1, leaseUntil - now),
      }, 409),
    };
  }
  if (Number(existing.attempts || 0) >= OUTBOUND_MEDIA_MAX_ATTEMPTS) {
    return { acquired: false, inserted: false, leaseOwner, metaMediaId: String(existing.meta_media_id || ""), response: json({ success: false, status: "failed", error: "outbound_media_retry_exhausted" }, 409) };
  }
  const claimed = await env.DB.prepare(
    "UPDATE outbound_media SET status='processing',attempts=attempts+1,lease_owner=?,lease_until=?,last_attempt_at=?,updated_at=?,error=NULL "
    + "WHERE fingerprint=? AND status NOT IN ('sent','delivered','read') AND attempts<? AND COALESCE(lease_until,0)<=?",
  ).bind(
    leaseOwner,
    now + OUTBOUND_MEDIA_LEASE_SECONDS,
    now,
    now,
    input.fingerprint,
    OUTBOUND_MEDIA_MAX_ATTEMPTS,
    now,
  ).run();
  if (!Number(claimed.meta.changes || 0)) {
    existing = await env.DB.prepare(
      "SELECT status,meta_media_id,meta_message_id,byte_size,mime_type,lease_until FROM outbound_media WHERE fingerprint=?",
    ).bind(input.fingerprint).first<JsonRecord>();
    if (existing && outboundMediaConfirmed(String(existing.status || ""))) {
      return { acquired: false, inserted: false, leaseOwner, metaMediaId: String(existing.meta_media_id || ""), response: outboundMediaDuplicateResponse(existing, input) };
    }
    return { acquired: false, inserted: false, leaseOwner, metaMediaId: String(existing?.meta_media_id || ""), response: json({ success: false, status: "processing", error: "outbound_media_processing" }, 409) };
  }
  return { acquired: true, inserted: false, leaseOwner, metaMediaId: String(existing.meta_media_id || "") };
}

export async function failOutboundMedia(env: Env, fingerprint: string, leaseOwner: string, errorCode: string): Promise<void> {
  await env.DB.prepare(
    "UPDATE outbound_media SET status='failed',error=?,updated_at=?,lease_owner=NULL,lease_until=NULL WHERE fingerprint=? AND lease_owner=? AND status='processing'",
  ).bind(String(errorCode || "outbound_media_failed").slice(0, 160), nowSeconds(), fingerprint, leaseOwner).run();
}

export async function storeOutboundMetaMediaId(env: Env, fingerprint: string, leaseOwner: string, mediaId: string): Promise<boolean> {
  const result = await env.DB.prepare(
    "UPDATE outbound_media SET meta_media_id=?,updated_at=? WHERE fingerprint=? AND lease_owner=? AND status='processing'",
  ).bind(mediaId, nowSeconds(), fingerprint, leaseOwner).run();
  return Number(result.meta.changes || 0) > 0;
}

export async function confirmOutboundMediaSent(env: Env, fingerprint: string, leaseOwner: string, metaMessageId: string): Promise<boolean> {
  const now = nowSeconds();
  const result = await env.DB.prepare(
    "UPDATE outbound_media SET status='sent',meta_message_id=?,sent_at=?,updated_at=?,error=NULL,lease_owner=NULL,lease_until=NULL "
    + "WHERE fingerprint=? AND lease_owner=? AND status='processing'",
  ).bind(metaMessageId || null, now, now, fingerprint, leaseOwner).run();
  return Number(result.meta.changes || 0) > 0;
}

export async function messageImage(request: Request, env: Env, messageId: string): Promise<Response> {
  const sizeLimit = 5 * 1024 * 1024;
  const errorPrefix = "outbound_image";
  const contentLength = Number.parseInt(String(request.headers.get("content-length") || "0"), 10);
  if (Number.isFinite(contentLength) && contentLength > sizeLimit + 256 * 1024) {
    return json({ success: false, error: `${errorPrefix}_size_limit` }, 413);
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
  if (!OUTBOUND_IMAGE_ARTIFACT_TYPES.has(artifactType)) {
    return json({ success: false, error: `${errorPrefix}_artifact_not_allowed` }, 400);
  }
  if (!/^[a-f0-9]{64}$/.test(expectedSha256)) return json({ success: false, error: `${errorPrefix}_sha256_required` }, 400);
  if (rawFile.size < 1 || rawFile.size > sizeLimit) return json({ success: false, error: `${errorPrefix}_size_limit` }, 413);

  const row = await env.DB.prepare(
    "SELECT i.subject_id,i.wa_id,b.machine_id FROM inbox i JOIN bindings b ON b.subject_id=i.subject_id AND b.active=1 WHERE i.message_id=?",
  ).bind(messageId).first<JsonRecord>();
  if (!row || String(row.machine_id || "") !== machineId) return json({ success: false, error: "message_not_owned" }, 404);

  const subjectId = String(row.subject_id || "");
  const eligibility = await zeroCostEligibility(env, subjectId);
  if (!eligibility.allowed) {
    await audit(env, `${errorPrefix}_blocked`, subjectId, { message_id: messageId, reason: eligibility.reason });
    return json({ success: false, status: eligibility.reason, error: eligibility.reason }, 409);
  }

  const mime = String(rawFile.type || "").split(";", 1)[0].trim().toLowerCase();
  const fileBuffer = await rawFile.arrayBuffer();
  const policy = outboundImagePolicy(mime, fileBuffer.byteLength, new Uint8Array(fileBuffer.slice(0, 12)));
  if (!policy.allowed) return json({ success: false, error: policy.error }, 400);
  if (!["image/jpeg", "image/png"].includes(mime)) return json({ success: false, error: "product_photo_mime_not_allowed" }, 400);
  const digest = await crypto.subtle.digest("SHA-256", fileBuffer);
  const sha256 = [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
  if (!timingSafeEqualText(expectedSha256, sha256)) return json({ success: false, error: `${errorPrefix}_sha256_mismatch` }, 400);
  const fingerprint = await sha256Hex(`${messageId}\n${subjectId}\n${artifactType}\n${sha256}`);
  const defaultFileName = "black-jhon-image.jpg";
  const fileName = String(rawFile.name || defaultFileName).replace(/[^A-Za-z0-9._-]+/g, "-").slice(0, 120) || defaultFileName;
  const lease = await reserveOutboundMedia(env, {
    fingerprint,
    inboundMessageId: messageId,
    subjectId,
    artifactType,
    sha256,
    mime,
    byteSize: fileBuffer.byteLength,
    caption,
    perResponseLimit: true,
    errorPrefix,
  });
  if (!lease.acquired) return lease.response || json({ success: false, error: "outbound_media_processing" }, 409);

  let mediaId = lease.metaMediaId;
  if (!mediaId) {
    const uploadForm = new FormData();
    uploadForm.set("messaging_product", "whatsapp");
    uploadForm.set("file", new File([fileBuffer], fileName, { type: mime }));
    const uploadResponse = await graphRequest(env, `${env.META_PHONE_NUMBER_ID}/media`, { method: "POST", body: uploadForm });
    const uploadPayload = await responsePayload(uploadResponse);
    mediaId = String(uploadPayload.id || "");
    if (!uploadResponse.ok || !mediaId) {
      await failOutboundMedia(env, fingerprint, lease.leaseOwner, `meta_media_upload_http_${uploadResponse.status}`);
      await audit(env, `${errorPrefix}_failed`, subjectId, { message_id: messageId, stage: "meta_media_upload", status: uploadResponse.status });
      return json({ success: false, stage: "meta_media_upload", error: uploadPayload }, 502);
    }
    if (!(await storeOutboundMetaMediaId(env, fingerprint, lease.leaseOwner, mediaId))) {
      return json({ success: false, error: "outbound_media_lease_lost" }, 409);
    }
  }

  const recipient = outboundRecipient(row.wa_id, subjectId, eligibility.binding);
  if (!recipient) {
    await failOutboundMedia(env, fingerprint, lease.leaseOwner, "recipient_missing");
    return json({ success: false, error: "recipient_missing" }, 400);
  }
  const mediaPayload: JsonRecord = { id: mediaId };
  if (caption) mediaPayload.caption = caption;
  const sendResponse = await graphRequest(env, `${env.META_PHONE_NUMBER_ID}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messaging_product: "whatsapp", recipient_type: "individual", to: recipient, type: "image", image: mediaPayload }),
  });
  const sendPayload = await responsePayload(sendResponse);
  if (!sendResponse.ok) {
    await failOutboundMedia(env, fingerprint, lease.leaseOwner, `meta_message_send_http_${sendResponse.status}`);
    await audit(env, `${errorPrefix}_failed`, subjectId, { message_id: messageId, stage: "meta_message_send", status: sendResponse.status, media_id: mediaId });
    return json({ success: false, stage: "meta_message_send", error: sendPayload }, 502);
  }
  const messages = Array.isArray(sendPayload.messages) ? sendPayload.messages : [];
  const metaMessageId = String(((messages[0] || {}) as JsonRecord).id || "");
  if (!(await confirmOutboundMediaSent(env, fingerprint, lease.leaseOwner, metaMessageId))) {
    return json({ success: false, error: "outbound_media_lease_lost" }, 409);
  }
  await audit(env, `${errorPrefix}_sent`, subjectId, {
    message_id: messageId,
    meta_message_id: metaMessageId,
    bytes: fileBuffer.byteLength,
    mime,
    sha256,
  });
  return json({ success: true, status: "sent", fingerprint, meta_message_id: metaMessageId, bytes: fileBuffer.byteLength, mime });
}

export async function proactiveImage(request: Request, env: Env): Promise<Response> {
  const sizeLimit = 5 * 1024 * 1024;
  const errorPrefix = "proactive_image";
  const contentLength = Number.parseInt(String(request.headers.get("content-length") || "0"), 10);
  if (Number.isFinite(contentLength) && contentLength > sizeLimit + 256 * 1024) {
    return json({ success: false, error: `${errorPrefix}_size_limit` }, 413);
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
    return json({ success: false, error: `invalid_${errorPrefix}_payload` }, 400);
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
    || eventType !== "task_completed"
    || !OUTBOUND_IMAGE_ARTIFACT_TYPES.has(artifactType)
    || !(rawFile instanceof File)
  ) {
    return json({ success: false, error: `invalid_${errorPrefix}_payload` }, 400);
  }
  if (!/^[a-f0-9]{64}$/.test(expectedSha256)) return json({ success: false, error: `${errorPrefix}_sha256_required` }, 400);
  if (rawFile.size < 1 || rawFile.size > sizeLimit) return json({ success: false, error: `${errorPrefix}_size_limit` }, 413);

  const eligibility = await zeroCostEligibility(env, subjectId);
  if (!eligibility.allowed) {
    await audit(env, `${errorPrefix}_blocked`, subjectId, { event_type: eventType, reason: eligibility.reason });
    return json({ success: false, status: eligibility.reason, error: eligibility.reason }, 409);
  }
  if (String(eligibility.binding?.machine_id || "") !== machineId) {
    await audit(env, `${errorPrefix}_blocked`, subjectId, { event_type: eventType, reason: "binding_machine_mismatch" });
    return json({ success: false, error: "binding_machine_mismatch" }, 403);
  }
  const recipient = outboundRecipient(subjectId, subjectId, eligibility.binding);
  if (!recipient) return json({ success: false, error: "recipient_missing" }, 400);

  const mime = String(rawFile.type || "").split(";", 1)[0].trim().toLowerCase();
  const fileBuffer = await rawFile.arrayBuffer();
  const policy = outboundImagePolicy(mime, fileBuffer.byteLength, new Uint8Array(fileBuffer.slice(0, 12)));
  if (!policy.allowed) return json({ success: false, error: policy.error }, 400);
  if (!["image/jpeg", "image/png"].includes(mime)) return json({ success: false, error: "product_photo_mime_not_allowed" }, 400);
  const digest = await crypto.subtle.digest("SHA-256", fileBuffer);
  const sha256 = [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
  if (!timingSafeEqualText(expectedSha256, sha256)) return json({ success: false, error: `${errorPrefix}_sha256_mismatch` }, 400);

  // Keep deduplication stable even if the same product photo is re-encoded.
  const fingerprint = await sha256Hex(`${errorPrefix}\n${subjectId}\n${eventType}\n${callerFingerprint}`);
  const inboundMessageId = `proactive:${eventType}:${callerFingerprint}`;
  const defaultFileName = "black-jhon-product-photo.jpg";
  const fileName = String(rawFile.name || defaultFileName).replace(/[^A-Za-z0-9._-]+/g, "-").slice(0, 120) || defaultFileName;
  const lease = await reserveOutboundMedia(env, {
    fingerprint,
    inboundMessageId,
    subjectId,
    artifactType,
    sha256,
    mime,
    byteSize: fileBuffer.byteLength,
    caption,
    perResponseLimit: false,
    errorPrefix,
  });
  if (!lease.acquired) return lease.response || json({ success: false, error: "outbound_media_processing" }, 409);

  let mediaId = lease.metaMediaId;
  if (!mediaId) {
    const uploadForm = new FormData();
    uploadForm.set("messaging_product", "whatsapp");
    uploadForm.set("file", new File([fileBuffer], fileName, { type: mime }));
    const uploadResponse = await graphRequest(env, `${env.META_PHONE_NUMBER_ID}/media`, { method: "POST", body: uploadForm });
    const uploadPayload = await responsePayload(uploadResponse);
    mediaId = String(uploadPayload.id || "");
    if (!uploadResponse.ok || !mediaId) {
      await failOutboundMedia(env, fingerprint, lease.leaseOwner, `meta_media_upload_http_${uploadResponse.status}`);
      await audit(env, `${errorPrefix}_failed`, subjectId, { event_type: eventType, stage: "meta_media_upload", status: uploadResponse.status });
      return json({ success: false, stage: "meta_media_upload", error: uploadPayload }, 502);
    }
    if (!(await storeOutboundMetaMediaId(env, fingerprint, lease.leaseOwner, mediaId))) {
      return json({ success: false, error: "outbound_media_lease_lost" }, 409);
    }
  }

  const mediaPayload: JsonRecord = { id: mediaId };
  if (caption) mediaPayload.caption = caption;
  const sendResponse = await graphRequest(env, `${env.META_PHONE_NUMBER_ID}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messaging_product: "whatsapp", recipient_type: "individual", to: recipient, type: "image", image: mediaPayload }),
  });
  const sendPayload = await responsePayload(sendResponse);
  if (!sendResponse.ok) {
    await failOutboundMedia(env, fingerprint, lease.leaseOwner, `meta_message_send_http_${sendResponse.status}`);
    await audit(env, `${errorPrefix}_failed`, subjectId, { event_type: eventType, stage: "meta_message_send", status: sendResponse.status, media_id: mediaId });
    return json({ success: false, stage: "meta_message_send", error: sendPayload }, 502);
  }
  const messages = Array.isArray(sendPayload.messages) ? sendPayload.messages : [];
  const metaMessageId = String(((messages[0] || {}) as JsonRecord).id || "");
  if (!(await confirmOutboundMediaSent(env, fingerprint, lease.leaseOwner, metaMessageId))) {
    return json({ success: false, error: "outbound_media_lease_lost" }, 409);
  }
  await audit(env, `${errorPrefix}_sent`, subjectId, {
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
