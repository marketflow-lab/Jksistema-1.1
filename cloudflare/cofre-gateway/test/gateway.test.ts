import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import { readFileSync } from "node:fs";
import { createWorker, type Env } from "../src/index";
import { EMPTY_STATE, MAX_REQUEST_BYTES, MAX_STATE_BYTES } from "../src/schema";

const ISSUER = "test-signer@test-project.iam.gserviceaccount.com";
const NOW = 1792627200;
const databases: DatabaseSync[] = [];
let privateKey: CryptoKey;
let publicJwk: JsonWebKey;

beforeAll(async () => {
  const pair = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  privateKey = pair.privateKey;
  publicJwk = await crypto.subtle.exportKey("jwk", pair.publicKey);
});

afterEach(() => { for (const db of databases.splice(0)) db.close(); });

function database() {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(readFileSync(new URL("../migrations/0001_company_state.sql", import.meta.url), "utf8"));
  databases.push(sqlite);
  const calls: { sql: string; values: SQLInputValue[] }[] = [];
  const binding = {
    prepare(sql: string) {
      return { bind(...values: SQLInputValue[]) {
        calls.push({ sql, values });
        return { async first<T>() { return (sqlite.prepare(sql).get(...values) ?? null) as T | null; } };
      } };
    },
  } as unknown as D1Database;
  return { sqlite, binding, calls };
}

function encode(value: unknown) {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64url");
}

async function token(changes: Record<string, unknown> = {}, headerChanges: Record<string, unknown> = {}) {
  const header: Record<string, unknown> = { alg: "RS256", typ: "JWT", kid: "test-key", ...headerChanges };
  if (header.kid === undefined) delete header.kid;
  const claims = { iss: ISSUER, aud: "jk-cofre", sub: "synthetic-user", client_id: "company-a",
    machine_hash: "a".repeat(64), scope: ["cofre:read", "cofre:write"], iat: NOW, exp: NOW + 3600, ...changes };
  const input = encode(header) + "." + encode(claims);
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", privateKey, new TextEncoder().encode(input));
  return input + "." + Buffer.from(signature).toString("base64url");
}

function fixture(options: { failKeys?: boolean; keyCount?: number; now?: () => number; afterKeyFetch?: () => void } = {}) {
  const db = database();
  const fetches: string[] = [];
  const worker = createWorker({ now: options.now ?? (() => NOW), fetcher: async input => {
    fetches.push(String(input));
    if (options.failKeys) return new Response("unavailable", { status: 503 });
    options.afterKeyFetch?.();
    return new Response(JSON.stringify({ keys: Array.from({ length: options.keyCount ?? 1 }, (_, index) =>
      ({ ...publicJwk, kid: index === 0 ? "test-key" : "test-key-" + index, alg: "RS256", use: "sig" })) }),
      { headers: { "Content-Type": "application/json", "Cache-Control": "public,max-age=120" } });
  } });
  const env: Env = { DB: db.binding, COFRE_ISSUER_EMAIL: ISSUER };
  return { worker, env, db, fetches };
}

function request(method: string, bearer: string | null, body?: unknown, query = "") {
  const headers = new Headers();
  if (bearer) headers.set("Authorization", "Bearer " + bearer);
  if (body !== undefined) headers.set("Content-Type", "application/json");
  return new Request("https://cofre.test/v1/state" + query, { method, headers,
    body: body === undefined ? undefined : JSON.stringify(body) });
}

function state(content = "Conteudo sintetico") {
  return { format: "jk-cofre-shared-v1", guilds: [{ guild: { id: "guild1", name: "Servidor teste" },
    exported_at: "2026-10-22T00:00:00Z", channels: [{ id: "channel1", name: "Guias", category: "Estudos",
      url: "https://discord.com/channels/guild1/channel1", topic: null, kind: "text", history_complete: true,
      messages: [{ id: "message1", author: "Autor teste", timestamp: "2026-10-22T00:00:00Z", content,
        source_url: "https://discord.com/channels/guild1/channel1/message1", links: [], attachments: [], embeds: [], deleted: false }] }] }] };
}

describe("state and atomic revisions", () => {
  it("isolates companies and returns an empty state without writing a new row", async () => {
    const f = fixture(), a = await token(), b = await token({ client_id: "company-b" });
    const empty = await f.worker.fetch(request("GET", a), f.env);
    expect(await empty.json()).toEqual({ revision: 0, state: EMPTY_STATE });
    expect(f.db.sqlite.prepare("SELECT COUNT(*) total FROM cofre_company_state").get()?.total).toBe(0);
    expect((await f.worker.fetch(request("PUT", a, { expected_revision: 0, state: state() }), f.env)).status).toBe(200);
    const other = await f.worker.fetch(request("GET", b), f.env);
    expect(await other.json()).toEqual({ revision: 0, state: EMPTY_STATE });
    expect(f.db.calls.every(call => !call.sql.includes("company-a") && !call.sql.includes("company-b"))).toBe(true);
    const injection = await f.worker.fetch(request("GET", a, undefined, "?company=company-b"), f.env);
    expect(injection.status).toBe(400);
    const old = await f.worker.fetch(request("GET", a, undefined, "?after=99"), f.env);
    expect(old.status).toBe(200);
    expect((await old.json() as { revision: number }).revision).toBe(1);
  });

  it("returns 304 only for the current revision, with ETag and no-store", async () => {
    const f = fixture(), bearer = await token();
    const empty = await f.worker.fetch(request("GET", bearer, undefined, "?after=0"), f.env);
    expect(empty.status).toBe(304);
    expect(empty.headers.get("ETag")).toBe('"cofre-0"');
    expect(empty.headers.get("Cache-Control")).toContain("no-store");
    expect(await empty.text()).toBe("");
    await f.worker.fetch(request("PUT", bearer, { expected_revision: 0, state: state() }), f.env);
    expect((await f.worker.fetch(request("GET", bearer, undefined, "?after=0"), f.env)).status).toBe(200);
    expect((await f.worker.fetch(request("GET", bearer, undefined, "?after=1"), f.env)).status).toBe(304);
  });

  it("admits one concurrent CAS winner without losing its state", async () => {
    const f = fixture(), bearer = await token();
    const results = await Promise.all(Array.from({ length: 8 }, (_, index) =>
      f.worker.fetch(request("PUT", bearer, { expected_revision: 0, state: state("winner-" + index) }), f.env)));
    expect(results.filter(result => result.status === 200)).toHaveLength(1);
    expect(results.filter(result => result.status === 409)).toHaveLength(7);
    const first = await (await f.worker.fetch(request("GET", bearer), f.env)).json() as { revision: number; state: ReturnType<typeof state> };
    expect(first.revision).toBe(1);
    expect(first.state.guilds[0].channels[0].messages[0].content).toMatch(/^winner-/);
    const conflicts = await Promise.all(["update-a", "update-b"].map(content =>
      f.worker.fetch(request("PUT", bearer, { expected_revision: 1, state: state(content) }), f.env)));
    expect(conflicts.map(result => result.status).sort()).toEqual([200, 409]);
    const current = await (await f.worker.fetch(request("GET", bearer), f.env)).json() as { revision: number; state: ReturnType<typeof state> };
    expect(current.revision).toBe(2);
    const stale = await f.worker.fetch(request("PUT", bearer, { expected_revision: 1, state: state("stale") }), f.env);
    expect(stale.status).toBe(409);
    expect(await stale.json()).toEqual({ error: "revision_conflict", revision: 2 });
    const unchanged = await (await f.worker.fetch(request("GET", bearer), f.env)).json();
    expect(unchanged).toEqual(current);
  });

  it("does not create a company on an invalid initial revision and preserves tombstones", async () => {
    const f = fixture(), bearer = await token();
    expect((await f.worker.fetch(request("PUT", bearer, { expected_revision: 9, state: state() }), f.env)).status).toBe(409);
    expect(f.db.sqlite.prepare("SELECT COUNT(*) total FROM cofre_company_state").get()?.total).toBe(0);
    const deleted = state('<script>opaque text</script>');
    deleted.guilds[0].channels[0].messages[0].deleted = true;
    await f.worker.fetch(request("PUT", bearer, { expected_revision: 0, state: deleted }), f.env);
    expect((await (await f.worker.fetch(request("GET", bearer), f.env)).json() as { state: unknown }).state).toEqual(deleted);
  });

  it("round-trips effective values and the bounded overlay field lists", async () => {
    const f = fixture(), bearer = await token(), snapshot: any = state("Texto editado");
    const channel = snapshot.guilds[0].channels[0], message = channel.messages[0];
    channel.name = "Guias renomeados";
    channel.topic = null;
    channel.edited_fields = ["name", "category", "topic"];
    message.author = "Autor editado";
    message.links = [{ url: "https://example.test/edited", text: "Link editado" }];
    message.embeds = [{ text: "Resumo editado", links: [] }];
    message.deleted = true;
    message.edited_fields = ["content", "author", "links", "embeds"];
    expect((await f.worker.fetch(request("PUT", bearer, { expected_revision: 0, state: snapshot }), f.env)).status).toBe(200);
    expect(await (await f.worker.fetch(request("GET", bearer), f.env)).json()).toEqual({ revision: 1, state: snapshot });
    channel.edited_fields = [];
    message.edited_fields = [];
    expect((await f.worker.fetch(request("PUT", bearer, { expected_revision: 1, state: snapshot }), f.env)).status).toBe(200);
    expect(await (await f.worker.fetch(request("GET", bearer), f.env)).json()).toEqual({ revision: 2, state: snapshot });
  });
});

describe("signed authorization", () => {
  it.each([
    ["expired", { exp: NOW }], ["too-long", { exp: NOW + 28801 }], ["future", { iat: NOW + 61 }],
    ["issuer", { iss: "untrusted@example.test" }], ["audience", { aud: "other" }],
    ["tenant", { client_id: "../company-b" }], ["trailing-tenant-dot", { client_id: "company." }],
    ["tenant-too-long", { client_id: "c".repeat(65) }], ["machine", { machine_hash: "raw-machine" }],
    ["extra-claim", { admin: true }], ["empty-subject", { sub: "" }], ["string-expiry", { exp: String(NOW + 100) }],
  ])("rejects %s claims before querying D1", async (_label, claims) => {
    const f = fixture(), bearer = await token(claims);
    const response = await f.worker.fetch(request("GET", bearer), f.env);
    expect(response.status).toBe(401);
    expect(f.db.calls).toHaveLength(0);
    expect(f.fetches).toHaveLength(0);
  });

  it("rejects tampering, unknown keys, wrong algorithms, and untrusted key URLs", async () => {
    const f = fixture(), valid = await token();
    const parts = valid.split(".");
    const changedClaims = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
    changedClaims.client_id = "company-b";
    parts[1] = encode(changedClaims);
    expect((await f.worker.fetch(request("GET", parts.join(".")), f.env)).status).toBe(401);
    expect((await f.worker.fetch(request("GET", await token({}, { kid: "unknown" })), f.env)).status).toBe(401);
    expect((await f.worker.fetch(request("GET", await token({}, { alg: "HS256" })), f.env)).status).toBe(401);
    expect((await f.worker.fetch(request("GET", await token({}, { jku: "https://attacker.test/keys" })), f.env)).status).toBe(401);
    expect(f.db.calls).toHaveLength(0);
    expect(f.fetches).toHaveLength(1);
    expect(f.fetches[0]).toBe("https://www.googleapis.com/service_accounts/v1/jwk/" + encodeURIComponent(ISSUER));
  });

  it("allows bounded trusted keys without kid and caches simultaneous Google lookups", async () => {
    const f = fixture(), bearer = await token({}, { kid: undefined });
    const results = await Promise.all(Array.from({ length: 8 }, () => f.worker.fetch(request("GET", bearer), f.env)));
    expect(results.every(result => result.status === 200)).toBe(true);
    expect(f.fetches).toHaveLength(1);
    const excessive = fixture({ keyCount: 11 });
    expect((await excessive.worker.fetch(request("GET", bearer), excessive.env)).status).toBe(503);
    expect(excessive.db.calls).toHaveLength(0);
  });

  it("refreshes cached public keys after their bounded TTL", async () => {
    let clock = NOW;
    const f = fixture({ now: () => clock }), bearer = await token();
    await f.worker.fetch(request("GET", bearer), f.env);
    clock += 119;
    await f.worker.fetch(request("GET", bearer), f.env);
    expect(f.fetches).toHaveLength(1);
    clock += 2;
    await f.worker.fetch(request("GET", bearer), f.env);
    expect(f.fetches).toHaveLength(2);
  });

  it("rejects a token that expires while public keys are loading", async () => {
    let clock = NOW;
    const f = fixture({ now: () => clock, afterKeyFetch: () => { clock += 2; } });
    const response = await f.worker.fetch(request("GET", await token({ exp: NOW + 1 })), f.env);
    expect(response.status).toBe(401);
    expect(f.db.calls).toHaveLength(0);
  });

  it("enforces read/write scopes and fails closed when key service/configuration fails", async () => {
    const f = fixture(), readOnly = await token({ scope: ["cofre:read"] });
    expect((await f.worker.fetch(request("GET", readOnly), f.env)).status).toBe(200);
    expect((await f.worker.fetch(request("PUT", readOnly, { expected_revision: 0, state: state() }), f.env)).status).toBe(403);
    expect((await f.worker.fetch(request("GET", await token({ scope: ["cofre:write"] })), f.env)).status).toBe(403);
    expect((await f.worker.fetch(request("GET", null), f.env)).status).toBe(401);
    const unavailable = fixture({ failKeys: true });
    const response = await unavailable.worker.fetch(request("GET", await token()), unavailable.env);
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "verification_unavailable" });
    expect((await f.worker.fetch(request("GET", await token()), { ...f.env, COFRE_ISSUER_EMAIL: "" })).status).toBe(503);
  });
});

describe("closed state and request limits", () => {
  it.each([
    ["tenant field", (s: any) => { s.company = "company-b"; }],
    ["UI metadata", (s: any) => { s.guilds[0].channels[0].edited = true; }],
    ["message UI metadata", (s: any) => { s.guilds[0].channels[0].messages[0].edited = true; }],
    ["invalid message overlay field", (s: any) => { s.guilds[0].channels[0].messages[0].edited_fields = ["attachments"]; }],
    ["invalid channel overlay field", (s: any) => { s.guilds[0].channels[0].edited_fields = ["content"]; }],
    ["duplicate message overlay field", (s: any) => { s.guilds[0].channels[0].messages[0].edited_fields = ["content", "content"]; }],
    ["duplicate channel overlay field", (s: any) => { s.guilds[0].channels[0].edited_fields = ["name", "name"]; }],
    ["non-list message overlay", (s: any) => { s.guilds[0].channels[0].messages[0].edited_fields = "content"; }],
    ["null channel overlay", (s: any) => { s.guilds[0].channels[0].edited_fields = null; }],
    ["non-string overlay field", (s: any) => { s.guilds[0].channels[0].edited_fields = [1]; }],
    ["too many message overlay fields", (s: any) => { s.guilds[0].channels[0].messages[0].edited_fields = ["content", "author", "links", "embeds", "content"]; }],
    ["too many channel overlay fields", (s: any) => { s.guilds[0].channels[0].edited_fields = ["name", "category", "topic", "name"]; }],
    ["local attachment", (s: any) => { s.guilds[0].channels[0].messages[0].attachments = [{ url: "", filename: "local.txt", local_url: "/api/local" }]; }],
    ["archive path", (s: any) => { s.guilds[0].channels[0].messages[0].attachments = [{ url: "https://example.test/file", filename: "file", archive_path: "assets/file" }]; }],
    ["script URL", (s: any) => { s.guilds[0].channels[0].messages[0].links = [{ url: "javascript:alert(1)" }]; }],
    ["invalid ID", (s: any) => { s.guilds[0].channels[0].id = "../other"; }],
    ["duplicate message", (s: any) => { s.guilds[0].channels[0].messages.push(s.guilds[0].channels[0].messages[0]); }],
    ["duplicate channel", (s: any) => { s.guilds[0].channels.push(s.guilds[0].channels[0]); }],
    ["duplicate guild", (s: any) => { s.guilds.push(s.guilds[0]); }],
    ["missing tombstone", (s: any) => { delete s.guilds[0].channels[0].messages[0].deleted; }],
  ])("rejects %s without replacing stored state", async (_label, mutate) => {
    const f = fixture(), bearer = await token();
    await f.worker.fetch(request("PUT", bearer, { expected_revision: 0, state: state("original") }), f.env);
    const changed = state();
    mutate(changed);
    expect((await f.worker.fetch(request("PUT", bearer, { expected_revision: 1, state: changed }), f.env)).status).toBe(400);
    const current = await (await f.worker.fetch(request("GET", bearer), f.env)).json() as { revision: number; state: ReturnType<typeof state> };
    expect(current.revision).toBe(1);
    expect(current.state.guilds[0].channels[0].messages[0].content).toBe("original");
  });

  it("checks canonical state bytes, declared body size, and streamed bodies without Content-Length", async () => {
    const f = fixture(), bearer = await token();
    const oversized = state("x".repeat(MAX_STATE_BYTES));
    expect((await f.worker.fetch(request("PUT", bearer, { expected_revision: 0, state: oversized }), f.env)).status).toBe(413);
    const declared = request("PUT", bearer, { expected_revision: 0, state: EMPTY_STATE });
    declared.headers.set("Content-Length", String(MAX_REQUEST_BYTES + 1));
    expect((await f.worker.fetch(declared, f.env)).status).toBe(413);
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new Uint8Array(MAX_REQUEST_BYTES + 1));
    }, cancel() { cancelled = true; } });
    const streamed = new Request("https://cofre.test/v1/state", { method: "PUT", body,
      headers: { Authorization: "Bearer " + bearer, "Content-Type": "application/json" }, duplex: "half" } as RequestInit & { duplex: "half" });
    expect((await f.worker.fetch(streamed, f.env)).status).toBe(413);
    expect(cancelled).toBe(true);
    expect(f.db.sqlite.prepare("SELECT COUNT(*) total FROM cofre_company_state").get()?.total).toBe(0);
  });

  it("rejects extra write fields, invalid revisions, content type, and broad browser CORS", async () => {
    const f = fixture(), bearer = await token();
    expect((await f.worker.fetch(request("PUT", bearer, { expected_revision: 0, state: EMPTY_STATE, client_id: "company-b" }), f.env)).status).toBe(400);
    expect((await f.worker.fetch(request("PUT", bearer, { expected_revision: -1, state: EMPTY_STATE }), f.env)).status).toBe(400);
    expect((await f.worker.fetch(request("GET", bearer, undefined, "?after=01"), f.env)).status).toBe(400);
    expect((await f.worker.fetch(request("GET", bearer, undefined, "?after=0&after=0"), f.env)).status).toBe(400);
    const nonJson = request("PUT", bearer, {}); nonJson.headers.set("Content-Type", "text/plain");
    expect((await f.worker.fetch(nonJson, f.env)).status).toBe(415);
    const options = await f.worker.fetch(request("OPTIONS", null), f.env);
    expect(options.status).toBe(405);
    expect(options.headers.has("Access-Control-Allow-Origin")).toBe(false);
    expect(options.headers.get("Cache-Control")).toContain("no-store");
  });
});
