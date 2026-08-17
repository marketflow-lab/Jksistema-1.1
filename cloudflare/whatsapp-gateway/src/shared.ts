import { timingSafeEqualText } from "./core";

export interface Env {
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

export type JsonRecord = Record<string, unknown>;

export const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };

export const GATEWAY_PROTOCOL_VERSION = 1;

export const GATEWAY_BUILD_VERSION = "1.0.105";

export function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

export function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

export function intEnv(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function retryDelaySeconds(attempt: number): number {
  return [2, 5, 15][Math.max(0, Math.min(2, Number(attempt || 1) - 1))];
}

export function monthKey(now = new Date()): string {
  return `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
}

export function dayKey(now = new Date()): string {
  return now.toISOString().slice(0, 10);
}

export function monthStartSeconds(now = new Date()): number {
  return Math.floor(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1) / 1000);
}

export function randomId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "")}`;
}

export function bridgeAuthorized(request: Request, env: Env): boolean {
  const value = request.headers.get("authorization") || "";
  return value.startsWith("Bearer ") && timingSafeEqualText(value.slice(7).trim(), env.BRIDGE_TOKEN || "");
}

export async function requestJson(request: Request): Promise<JsonRecord> {
  try {
    const parsed = await request.json();
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as JsonRecord) : {};
  } catch {
    return {};
  }
}

export async function audit(env: Env, eventType: string, subjectId = "", detail: unknown = {}): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO audit_events(id,event_type,subject_id,detail_json,created_at) VALUES(?,?,?,?,?)",
  ).bind(randomId("audit"), eventType, subjectId || null, JSON.stringify(detail || {}), nowSeconds()).run();
}

export async function counterGet(env: Env, key: string): Promise<number> {
  const row = await env.DB.prepare("SELECT counter_value FROM usage_counters WHERE counter_key=?").bind(key).first<{ counter_value: number }>();
  return Number(row?.counter_value || 0);
}

export async function counterAdd(env: Env, key: string, delta: number): Promise<void> {
  const now = nowSeconds();
  await env.DB.prepare(
    "INSERT INTO usage_counters(counter_key,counter_value,updated_at) VALUES(?,?,?) ON CONFLICT(counter_key) DO UPDATE SET counter_value=MAX(0,counter_value+excluded.counter_value),updated_at=excluded.updated_at",
  ).bind(key, delta, now).run();
}

export async function counterReserveBelow(env: Env, key: string, limit: number): Promise<boolean> {
  const result = await env.DB.prepare(
    "INSERT INTO usage_counters(counter_key,counter_value,updated_at) VALUES(?,1,?) "
    + "ON CONFLICT(counter_key) DO UPDATE SET counter_value=counter_value+1,updated_at=excluded.updated_at "
    + "WHERE counter_value<?",
  ).bind(key, nowSeconds(), Math.max(1, limit)).run();
  return Number(result.meta.changes || 0) > 0;
}

export async function graphRequest(env: Env, path: string, init: RequestInit = {}): Promise<Response> {
  const version = String(env.META_GRAPH_API_VERSION || "").trim();
  if (!/^v\d+\.\d+$/.test(version)) throw new Error("META_GRAPH_API_VERSION ausente ou invalida");
  const headers = new Headers(init.headers || {});
  headers.set("authorization", `Bearer ${env.META_SYSTEM_USER_TOKEN}`);
  return fetch(`https://graph.facebook.com/${version}/${path.replace(/^\//, "")}`, { ...init, headers });
}

export async function graphUploadRequest(env: Env, path: string, init: RequestInit = {}): Promise<Response> {
  const version = String(env.META_GRAPH_API_VERSION || "").trim();
  if (!/^v\d+\.\d+$/.test(version)) throw new Error("META_GRAPH_API_VERSION ausente ou invalida");
  const headers = new Headers(init.headers || {});
  // Meta's resumable binary-upload endpoint documents the OAuth scheme
  // explicitly; using Bearer can yield a valid-looking but unusable handle.
  headers.set("authorization", `OAuth ${env.META_SYSTEM_USER_TOKEN}`);
  return fetch(`https://graph.facebook.com/${version}/${path.replace(/^\//, "")}`, { ...init, headers });
}

export async function responsePayload(response: Response): Promise<JsonRecord> {
  const raw = await response.text();
  try {
    const value = JSON.parse(raw);
    return value && typeof value === "object" ? value as JsonRecord : { value };
  } catch {
    return { message: raw.slice(0, 1000) };
  }
}

export function sanitizeMetaPayload(value: unknown, key = ""): unknown {
  const normalizedKey = key.trim().toLowerCase();
  if (/^(?:access_token|authorization|credential|credentials|password|pin|secret|sip_password|srtp_key|token|.*_(?:credential|credentials|password|secret|token))$/.test(normalizedKey)) {
    return "[redacted]";
  }
  if (Array.isArray(value)) return value.map((item) => sanitizeMetaPayload(item));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as JsonRecord).map(([childKey, childValue]) => [
        childKey,
        sanitizeMetaPayload(childValue, childKey),
      ]),
    );
  }
  return value;
}
