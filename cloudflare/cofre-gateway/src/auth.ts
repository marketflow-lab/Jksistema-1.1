import { GatewayError } from "./schema";

const MAX_TOKEN_BYTES = 8192;
const MAX_TOKEN_SECONDS = 8 * 60 * 60;
const MAX_PUBLIC_KEYS = 10;
const CLOCK_SKEW_SECONDS = 60;
const encoder = new TextEncoder();

export interface Principal {
  company: string;
  username: string;
  machineHash: string;
  scopes: string[];
  expiresAt: number;
}

interface PublicKey { kid: string; key: CryptoKey }
interface KeySet { expiresAt: number; keys: PublicKey[] }
type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
export interface AuthDependencies { fetcher?: Fetcher; now?: () => number }

function invalid(): never { throw new GatewayError(401, "invalid_token"); }

function closed(value: unknown, allowed: string[], required: string[]): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) invalid();
  const item = value as Record<string, unknown>;
  if (Object.keys(item).some(key => !allowed.includes(key)) || required.some(key => !Object.hasOwn(item, key))) invalid();
  return item;
}

function decodeSegment(value: string): Uint8Array<ArrayBuffer> {
  if (!/^[A-Za-z0-9_-]+$/.test(value) || value.length % 4 === 1) invalid();
  try {
    const decoded = atob(value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4));
    return Uint8Array.from(decoded, char => char.charCodeAt(0));
  } catch { return invalid(); }
}

function decodeObject(value: string): unknown {
  try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(decodeSegment(value))); }
  catch { return invalid(); }
}

function issuerValid(issuer: string): boolean {
  return /^[a-zA-Z0-9._-]+@[a-zA-Z0-9.-]+\.gserviceaccount\.com$/.test(issuer) && issuer.length <= 254;
}

function claims(value: unknown, issuer: string, now: number): Principal {
  const fields = ["iss", "aud", "sub", "client_id", "machine_hash", "scope", "iat", "exp"];
  const item = closed(value, fields, fields);
  if (item.iss !== issuer || item.aud !== "jk-cofre") invalid();
  if (typeof item.sub !== "string" || !item.sub || item.sub !== item.sub.trim()
      || item.sub.length > 180 || /[\u0000-\u001f\u007f]/.test(item.sub)) invalid();
  if (typeof item.client_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(item.client_id)
      || item.client_id.endsWith(".")) invalid();
  if (typeof item.machine_hash !== "string" || !/^[0-9a-f]{64}$/.test(item.machine_hash)) invalid();
  if (!Array.isArray(item.scope) || item.scope.length > 2 || item.scope.length < 1
      || item.scope.some(scope => scope !== "cofre:read" && scope !== "cofre:write")
      || new Set(item.scope).size !== item.scope.length) invalid();
  if (typeof item.iat !== "number" || typeof item.exp !== "number"
      || !Number.isSafeInteger(item.iat) || !Number.isSafeInteger(item.exp)
      || item.iat < 0 || item.exp <= now || item.iat > now + CLOCK_SKEW_SECONDS
      || item.exp <= item.iat || item.exp - item.iat > MAX_TOKEN_SECONDS) invalid();
  return { company: item.client_id, username: item.sub, machineHash: item.machine_hash, scopes: item.scope, expiresAt: item.exp };
}

export function createAuthenticator(dependencies: AuthDependencies = {}) {
  const now = dependencies.now ?? (() => Math.floor(Date.now() / 1000));
  const fetcher = dependencies.fetcher ?? ((input, init) => fetch(input, init));
  const cache = new Map<string, KeySet>();
  const pending = new Map<string, Promise<KeySet>>();

  async function loadKeys(issuer: string): Promise<KeySet> {
    const cached = cache.get(issuer);
    if (cached && cached.expiresAt > now()) return cached;
    const existing = pending.get(issuer);
    if (existing) return existing;
    const loading = (async () => {
      try {
        // Only the configured service account can determine this fixed Google URL.
        const endpoint = "https://www.googleapis.com/service_accounts/v1/jwk/" + encodeURIComponent(issuer);
        const response = await fetcher(endpoint, { method: "GET", redirect: "error", signal: AbortSignal.timeout(5000) });
        if (!response.ok) throw new Error("unavailable");
        const raw = await response.text();
        if (raw.length > 65536) throw new Error("invalid_keyset");
        const parsed = JSON.parse(raw) as { keys?: (JsonWebKey & { kid?: string })[] };
        if (!Array.isArray(parsed.keys) || !parsed.keys.length || parsed.keys.length > MAX_PUBLIC_KEYS) throw new Error("invalid_keyset");
        const keys: PublicKey[] = [];
        for (const jwk of parsed.keys) {
          if (jwk.kty !== "RSA" || (jwk.alg && jwk.alg !== "RS256") || (jwk.use && jwk.use !== "sig")
              || typeof jwk.n !== "string" || typeof jwk.e !== "string" || decodeSegment(jwk.n).byteLength < 256
              || typeof jwk.kid !== "string" || !/^[A-Za-z0-9_-]{1,200}$/.test(jwk.kid)
              || Object.hasOwn(jwk, "d")) throw new Error("invalid_keyset");
          const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
          keys.push({ kid: jwk.kid, key });
        }
        const declared = response.headers.get("cache-control")?.match(/(?:^|,)\s*max-age=(\d+)/i)?.[1];
        const ttl = Math.max(60, Math.min(3600, Number(declared ?? 600)));
        const value = { expiresAt: now() + ttl, keys };
        cache.set(issuer, value);
        return value;
      } catch {
        throw new GatewayError(503, "verification_unavailable");
      }
    })();
    pending.set(issuer, loading);
    try { return await loading; }
    finally { pending.delete(issuer); }
  }

  return async function authenticate(request: Request, issuer: string | undefined, write: boolean): Promise<Principal> {
    if (!issuer || !issuerValid(issuer)) throw new GatewayError(503, "service_unavailable");
    const authorization = request.headers.get("authorization");
    if (!authorization?.startsWith("Bearer ") || authorization.length > MAX_TOKEN_BYTES) invalid();
    const token = authorization.slice(7);
    const segments = token.split(".");
    if (segments.length !== 3) invalid();
    const header = closed(decodeObject(segments[0]), ["alg", "typ", "kid"], ["alg", "typ"]);
    if (header.alg !== "RS256" || header.typ !== "JWT"
        || (header.kid !== undefined && (typeof header.kid !== "string" || !/^[A-Za-z0-9_-]{1,200}$/.test(header.kid)))) invalid();
    const principal = claims(decodeObject(segments[1]), issuer, now());
    const signature = decodeSegment(segments[2]);
    if (signature.byteLength < 256 || signature.byteLength > 512) invalid();
    const publicKeys = await loadKeys(issuer);
    const candidates = header.kid === undefined ? publicKeys.keys : publicKeys.keys.filter(key => key.kid === header.kid);
    const signed = encoder.encode(segments[0] + "." + segments[1]);
    let verified = false;
    for (const candidate of candidates) {
      if (await crypto.subtle.verify("RSASSA-PKCS1-v1_5", candidate.key, signature, signed)) { verified = true; break; }
    }
    if (!verified || principal.expiresAt <= now()) invalid();
    if (!principal.scopes.includes("cofre:read") || (write && !principal.scopes.includes("cofre:write"))) {
      throw new GatewayError(403, "insufficient_scope");
    }
    return principal;
  };
}
