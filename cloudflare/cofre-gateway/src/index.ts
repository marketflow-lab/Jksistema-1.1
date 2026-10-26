import { createAuthenticator, type AuthDependencies } from "./auth";
import { EMPTY_STATE, GatewayError, MAX_REVISION, readBoundedJson, validateWrite } from "./schema";
import { tuple, validateAssetRequest, validateMutation, type DeletedChannel, type Value } from "./publishing-schema";
import { mutate, putState, readView } from "./publishing";
import { downloadAsset, reserveAsset, uploadAsset } from "./assets";

export interface Env { DB: D1Database; COFRE_ISSUER_EMAIL: string; MEDIA?: KVNamespace }
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
        const assetContent = /^\/v1\/assets\/([0-9a-f]{64})\/content$/.exec(url.pathname);
        const assetDownload = /^\/v1\/assets\/([0-9a-f]{64})\/([^/]+)$/.exec(url.pathname);
        const allowed = url.pathname === "/v1/state" ? ["GET", "PUT"]
          : url.pathname === "/v1/mutations" || url.pathname === "/v1/assets" ? ["POST"]
          : assetContent ? ["PUT", "GET"] : assetDownload ? ["GET"] : null;
        if (!allowed) return json({ error: "not_found" }, 404);
        if (!allowed.includes(request.method)) return json({ error: "method_not_allowed" }, 405, { Allow: allowed.join(", ") });
        const write = request.method === "PUT" && url.pathname === "/v1/state";
        const principal = await authenticate(request, env.COFRE_ISSUER_EMAIL, write);
        const keys = [...url.searchParams.keys()];
        if (keys.length > 1 || keys.some(key => url.pathname !== "/v1/state" || write || key !== "after")) throw new GatewayError(400, "invalid_query");
        const conflict = async () => {
          const row = await env.DB.prepare("SELECT revision FROM cofre_company_state WHERE company=?1").bind(principal.company).first<{ revision: number }>();
          return json({ error: "revision_conflict", revision: row?.revision ?? 0 }, 409);
        };
        if (url.pathname === "/v1/mutations") {
          const result = await mutate(env.DB, principal, validateMutation(await readBoundedJson(request)), url.origin, now());
          return result ? json(result, 200, { ETag: etag(result.revision) }) : await conflict();
        }
        if (url.pathname === "/v1/assets") return json(await reserveAsset(env, principal, validateAssetRequest(await readBoundedJson(request)), url.origin, now()));
        if (assetContent && request.method === "PUT") return json(await uploadAsset(env, principal, assetContent[1], request, url.origin, now()));
        if (assetDownload) {
          let filename: string;
          try { filename = decodeURIComponent(assetDownload[2]); } catch { throw new GatewayError(400, "invalid_filename"); }
          const { stream, row } = await downloadAsset(env, principal, assetDownload[1], filename);
          return new Response(stream, { headers: { ...HEADERS, "Content-Type": row.content_type, "Content-Length": String(row.size),
            "X-Cofre-SHA256": row.sha256, "Content-Disposition": "attachment; filename*=UTF-8''" + encodeURIComponent(row.filename).replace(/['()*]/g, c => "%" + c.charCodeAt(0).toString(16).toUpperCase()) } });
        }
        if (!write) {
          const after = url.searchParams.has("after") ? revision(url.searchParams.get("after")!) : null;
          const metadata = request.headers.get("X-JK-Cofre-Metadata");
          if (metadata !== null && metadata !== "1") throw new GatewayError(400, "invalid_metadata_version");
          if (metadata === "1") {
            const view = await readView(env.DB, principal.company);
            if (after === view.revision) return new Response(null, { status: 304, headers: { ...HEADERS, ETag: etag(view.revision) } });
            return json(view, 200, { ETag: etag(view.revision) });
          }
          const row = await env.DB.prepare("SELECT revision,state FROM cofre_company_state WHERE company=?1")
            .bind(principal.company).first<{ revision: number; state: string }>();
          const current = row?.revision ?? 0;
          if (after === current) return new Response(null, { status: 304, headers: { ...HEADERS, ETag: etag(current) } });
          const state = row ? JSON.parse(row.state) : structuredClone(EMPTY_STATE);
          if (row) {
            const tombstones = await env.DB.prepare("SELECT guild_id,channel_id FROM cofre_deleted_channels WHERE company=?1").bind(principal.company).all<DeletedChannel>();
            const hidden = new Set(tombstones.results.map(d => tuple(d.guild_id, d.channel_id)));
            for (const g of state.guilds) g.channels = g.channels.filter((c: Value) => !hidden.has(tuple(g.guild.id, c.id)));
          }
          return json({ revision: current, state }, 200, { ETag: etag(current) });
        }
        const body = validateWrite(await readBoundedJson(request));
        const result = await putState(env.DB, principal, body.expectedRevision, JSON.parse(body.serializedState), body.metadata, url.origin, now());
        return result === null ? await conflict() : json({ revision: result }, 200, { ETag: etag(result) });
      } catch (error) {
        // No request, token, account identifier, payload, or exception detail is logged or reflected.
        if (error instanceof GatewayError) return json({ error: error.code }, error.status,
          error.status === 503 && error.code.startsWith("asset_") ? { "Retry-After": "60" } : {});
        return json({ error: "service_unavailable" }, 503);
      }
    },
  };
}

export default createWorker();
