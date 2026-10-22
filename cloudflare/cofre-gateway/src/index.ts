import { createAuthenticator, type AuthDependencies } from "./auth";
import { EMPTY_STATE, GatewayError, MAX_REVISION, readBoundedJson, validateWrite } from "./schema";

export interface Env { DB: D1Database; COFRE_ISSUER_EMAIL: string }
const HEADERS = {
  "Cache-Control": "no-store, max-age=0",
  "Pragma": "no-cache",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
};

function json(value: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(value), { status, headers: { ...HEADERS, "Content-Type": "application/json; charset=utf-8", ...headers } });
}

function revision(value: string): number {
  if (!/^(0|[1-9]\d{0,15})$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) > MAX_REVISION) {
    throw new GatewayError(400, "invalid_revision");
  }
  return Number(value);
}

function etag(value: number): string { return '"cofre-' + value + '"'; }

export function createWorker(dependencies: AuthDependencies = {}) {
  const authenticate = createAuthenticator(dependencies);
  const now = dependencies.now ?? (() => Math.floor(Date.now() / 1000));
  return {
    async fetch(request: Request, env: Env): Promise<Response> {
      try {
        const url = new URL(request.url);
        if (url.pathname !== "/v1/state") return json({ error: "not_found" }, 404);
        if (request.method !== "GET" && request.method !== "PUT") return json({ error: "method_not_allowed" }, 405, { Allow: "GET, PUT" });
        const write = request.method === "PUT";
        const principal = await authenticate(request, env.COFRE_ISSUER_EMAIL, write);
        const keys = [...url.searchParams.keys()];
        if (keys.length > 1 || keys.some(key => write || key !== "after")) throw new GatewayError(400, "invalid_query");
        if (!write) {
          const after = url.searchParams.has("after") ? revision(url.searchParams.get("after")!) : null;
          const row = await env.DB.prepare("SELECT revision,state FROM cofre_company_state WHERE company=?1")
            .bind(principal.company).first<{ revision: number; state: string }>();
          const current = row?.revision ?? 0;
          if (after === current) return new Response(null, { status: 304, headers: { ...HEADERS, ETag: etag(current) } });
          return json({ revision: current, state: row ? JSON.parse(row.state) : EMPTY_STATE }, 200, { ETag: etag(current) });
        }
        const body = validateWrite(await readBoundedJson(request));
        // Each branch is one atomic, parameterized SQLite statement. No read/modify/write race.
        const result = body.expectedRevision === 0
          ? await env.DB.prepare("INSERT INTO cofre_company_state(company,revision,state,updated_at) VALUES(?1,1,?2,?3) ON CONFLICT(company) DO NOTHING RETURNING revision")
            .bind(principal.company, body.serializedState, now()).first<{ revision: number }>()
          : await env.DB.prepare("UPDATE cofre_company_state SET revision=revision+1,state=?1,updated_at=?2 WHERE company=?3 AND revision=?4 AND revision<?5 RETURNING revision")
            .bind(body.serializedState, now(), principal.company, body.expectedRevision, Number.MAX_SAFE_INTEGER).first<{ revision: number }>();
        if (!result) {
          const current = await env.DB.prepare("SELECT revision FROM cofre_company_state WHERE company=?1")
            .bind(principal.company).first<{ revision: number }>();
          return json({ error: "revision_conflict", revision: current?.revision ?? 0 }, 409);
        }
        return json({ revision: result.revision }, 200, { ETag: etag(result.revision) });
      } catch (error) {
        // No request, token, account identifier, payload, or exception detail is logged or reflected.
        if (error instanceof GatewayError) return json({ error: error.code }, error.status);
        return json({ error: "service_unavailable" }, 503);
      }
    },
  };
}

export default createWorker();
