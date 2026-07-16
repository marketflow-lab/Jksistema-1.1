export const FREE_WINDOW_DEFAULT_SECONDS = 23 * 60 * 60 + 30 * 60;
export const MAX_REPLY_CHARS = 3500;
export const TEMPLATE_NAMES = new Set([
  "jk_joao_tarefa_concluida",
  "jk_joao_aprovacao_pendente",
  "jk_joao_alerta_operacional",
]);

export function timingSafeEqualText(left: string, right: string): boolean {
  const a = new TextEncoder().encode(String(left || ""));
  const b = new TextEncoder().encode(String(right || ""));
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a[i] ^ b[i];
  return diff === 0;
}

export async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
}

export async function verifyMetaSignature(raw: ArrayBuffer, signature: string, appSecret: string): Promise<boolean> {
  if (!signature.startsWith("sha256=") || !appSecret) return false;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(appSecret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signed = await crypto.subtle.sign("HMAC", key, raw);
  const expected = `sha256=${[...new Uint8Array(signed)].map((item) => item.toString(16).padStart(2, "0")).join("")}`;
  return timingSafeEqualText(expected, signature);
}

function standardWebhookSecretBytes(secret: string): Uint8Array | null {
  const value = String(secret || "").trim().replace(/^whsec_/, "");
  if (!value) return null;
  try {
    const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
    const decoded = atob(padded);
    return Uint8Array.from(decoded, (char) => char.charCodeAt(0));
  } catch {
    return null;
  }
}

export async function verifyOpenAIWebhook(
  rawText: string,
  headers: Headers,
  secret: string,
  now = Math.floor(Date.now() / 1000),
  toleranceSeconds = 300,
): Promise<boolean> {
  const webhookId = String(headers.get("webhook-id") || "").trim();
  const timestampText = String(headers.get("webhook-timestamp") || "").trim();
  const signatureHeader = String(headers.get("webhook-signature") || "").trim();
  const timestamp = Number.parseInt(timestampText, 10);
  const keyBytes = standardWebhookSecretBytes(secret);
  if (!webhookId || !Number.isFinite(timestamp) || !signatureHeader || !keyBytes) return false;
  if (Math.abs(now - timestamp) > Math.max(30, toleranceSeconds)) return false;
  const keyData = new Uint8Array(keyBytes.byteLength);
  keyData.set(keyBytes);
  const key = await crypto.subtle.importKey(
    "raw",
    keyData,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signed = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(`${webhookId}.${timestampText}.${rawText}`),
  );
  const expected = btoa(String.fromCharCode(...new Uint8Array(signed)));
  return signatureHeader
    .split(/\s+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .some((item) => {
      const [version, signature] = item.split(",", 2);
      return version === "v1" && Boolean(signature) && timingSafeEqualText(expected, signature);
    });
}

export function normalizeInternationalPhone(value: unknown): string {
  let digits = String(value || "").replace(/\D/g, "");
  if (digits.startsWith("00")) digits = digits.slice(2);
  if (digits.length === 10 || digits.length === 11) digits = `55${digits}`;
  if (digits.length < 10 || digits.length > 15 || /^(\d)\1+$/.test(digits)) return "";
  return digits;
}

export function phoneAliases(value: unknown): string[] {
  const phone = normalizeInternationalPhone(value);
  if (!phone) return [];
  const aliases = new Set([phone]);
  if (phone.startsWith("55") && phone.length === 13 && phone[4] === "9") {
    aliases.add(`${phone.slice(0, 4)}${phone.slice(5)}`);
  } else if (phone.startsWith("55") && phone.length === 12) {
    aliases.add(`${phone.slice(0, 4)}9${phone.slice(4)}`);
  }
  return [...aliases];
}

export function phoneFromSipHeaders(value: unknown): string {
  const headers = Array.isArray(value) ? value : [];
  const preferred = ["x-meta-wa-id", "p-asserted-identity", "p-preferred-identity", "remote-party-id", "from"];
  for (const name of preferred) {
    const header = headers.find((item) => {
      return item && typeof item === "object" && String((item as Record<string, unknown>).name || "").toLowerCase() === name;
    }) as Record<string, unknown> | undefined;
    const raw = String(header?.value || "");
    const match = raw.match(/(?:sip:|tel:)?(\+?[0-9][0-9().\s-]{7,20})(?:@|;|>|$)/i);
    const phone = normalizeInternationalPhone(match?.[1] || "");
    if (phone) return phone;
  }
  return "";
}

export function isPolicyValid(validUntil: string, nowMs = Date.now()): boolean {
  const limit = Date.parse(String(validUntil || ""));
  return Number.isFinite(limit) && nowMs <= limit;
}

export function isFreeWindowOpen(lastInboundAt: number | null | undefined, nowSeconds: number, windowSeconds = FREE_WINDOW_DEFAULT_SECONDS): boolean {
  const inbound = Number(lastInboundAt || 0);
  return inbound > 0 && nowSeconds >= inbound && nowSeconds - inbound <= windowSeconds;
}

export function compactReply(value: unknown, limit = MAX_REPLY_CHARS): string {
  const text = String(value || "").replace(/\r\n/g, "\n").trim();
  if (text.length <= limit) return text;
  const suffix = "\n\nResposta completa disponivel no Joao Pretinho dentro do JK Sistema.";
  return `${text.slice(0, Math.max(1, limit - suffix.length)).trimEnd()}${suffix}`;
}

export function compactReplyParts(value: unknown, fallback: unknown = "", maxParts = 8): string[] {
  const raw = Array.isArray(value) ? value : [];
  const requestedLimit = Number(maxParts);
  const selected = Number.isFinite(requestedLimit) && requestedLimit <= 0
    ? raw
    : raw.slice(0, Math.max(1, Math.min(500, Number(maxParts || 8))));
  const parts = selected
    .map((item) => compactReply(item))
    .filter(Boolean);
  if (parts.length) return parts;
  const single = compactReply(fallback);
  return single ? [single] : [];
}

export function normalizeSeverity(value: unknown): string {
  const severity = String(value || "")
    .trim()
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");
  if (["critical", "critico", "critica"].includes(severity)) return "critical";
  if (["high", "alto", "alta"].includes(severity)) return "high";
  if (["medium", "medio", "media"].includes(severity)) return "medium";
  return "low";
}

export function pairingCodeFromText(value: unknown): string {
  const normalized = String(value || "").trim().toUpperCase();
  const match = normalized.match(/^(?:VINCULAR\s+)?([A-Z2-9]{8})$/);
  return match ? match[1] : "";
}

export function outboundRecipient(
  queuedRecipient: unknown,
  subjectId: unknown,
  binding: { wa_id?: unknown; phone_number?: unknown } | null | undefined,
): string {
  const preferred = binding?.wa_id || binding?.phone_number || queuedRecipient || subjectId;
  return String(preferred || "").replace(/\D/g, "");
}

export function mediaPolicy(type: string, mime: string): { allowed: boolean; maxBytes: number } {
  const normalizedType = String(type || "").toLowerCase();
  const normalizedMime = String(mime || "").split(";", 1)[0].trim().toLowerCase();
  if (normalizedType === "image" && ["image/jpeg", "image/png"].includes(normalizedMime)) {
    return { allowed: true, maxBytes: 5 * 1024 * 1024 };
  }
  if (
    normalizedType === "audio" &&
    ["audio/aac", "audio/mp4", "audio/mpeg", "audio/amr", "audio/ogg"].includes(normalizedMime)
  ) {
    return { allowed: true, maxBytes: 16 * 1024 * 1024 };
  }
  return { allowed: false, maxBytes: 0 };
}

export function profileImagePolicy(mime: unknown, size: unknown, signature: Uint8Array): { allowed: boolean; error: string } {
  const normalizedMime = String(mime || "").split(";", 1)[0].trim().toLowerCase();
  const bytes = Number(size || 0);
  if (!["image/jpeg", "image/png"].includes(normalizedMime)) return { allowed: false, error: "profile_image_type_not_allowed" };
  if (!Number.isFinite(bytes) || bytes < 1 || bytes > 5 * 1024 * 1024) return { allowed: false, error: "profile_image_size_limit" };
  const jpeg = signature.length >= 3 && signature[0] === 0xff && signature[1] === 0xd8 && signature[2] === 0xff;
  const png = signature.length >= 8
    && signature[0] === 0x89 && signature[1] === 0x50 && signature[2] === 0x4e && signature[3] === 0x47
    && signature[4] === 0x0d && signature[5] === 0x0a && signature[6] === 0x1a && signature[7] === 0x0a;
  if ((normalizedMime === "image/jpeg" && !jpeg) || (normalizedMime === "image/png" && !png)) {
    return { allowed: false, error: "profile_image_signature_invalid" };
  }
  return { allowed: true, error: "" };
}

export function outboundImagePolicy(mime: unknown, size: unknown, signature: Uint8Array): { allowed: boolean; error: string } {
  const normalizedMime = String(mime || "").split(";", 1)[0].trim().toLowerCase();
  const bytes = Number(size || 0);
  if (!["image/jpeg", "image/png"].includes(normalizedMime)) return { allowed: false, error: "outbound_image_type_not_allowed" };
  if (!Number.isFinite(bytes) || bytes < 1 || bytes > 5 * 1024 * 1024) return { allowed: false, error: "outbound_image_size_limit" };
  const jpeg = signature.length >= 3 && signature[0] === 0xff && signature[1] === 0xd8 && signature[2] === 0xff;
  const png = signature.length >= 8
    && signature[0] === 0x89 && signature[1] === 0x50 && signature[2] === 0x4e && signature[3] === 0x47
    && signature[4] === 0x0d && signature[5] === 0x0a && signature[6] === 0x1a && signature[7] === 0x0a;
  if ((normalizedMime === "image/jpeg" && !jpeg) || (normalizedMime === "image/png" && !png)) {
    return { allowed: false, error: "outbound_image_signature_invalid" };
  }
  return { allowed: true, error: "" };
}

export function safeTemplate(name: unknown, category: unknown, status: unknown): boolean {
  return TEMPLATE_NAMES.has(String(name || "")) && String(category || "").toUpperCase() === "UTILITY" && String(status || "").toUpperCase() === "APPROVED";
}
