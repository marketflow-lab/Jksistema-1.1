import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { createWorker, type Env } from "../src/index";
import { createDatabase } from "./database";
import { objectKey } from "../src/assets";
import { sha256, type Value } from "../src/publishing-schema";

const ISSUER = "test-signer@test-project.iam.gserviceaccount.com", NOW = 1792627200;
let key: CryptoKey, jwk: JsonWebKey;
const dbs: ReturnType<typeof createDatabase>[] = [];
beforeAll(async () => {
  const pair = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1,0,1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  key = pair.privateKey; jwk = await crypto.subtle.exportKey("jwk", pair.publicKey);
});
afterEach(() => { for (const db of dbs.splice(0)) db.sqlite.close(); });
async function token(username = "alice", admin = true, company = "company-a") {
  const encode = (v: unknown) => Buffer.from(JSON.stringify(v)).toString("base64url");
  const input = encode({ alg: "RS256", typ: "JWT", kid: "fixture" }) + "." + encode({ iss: ISSUER, aud: "jk-cofre", sub: username,
    client_id: company, machine_hash: "a".repeat(64), scope: admin ? ["cofre:read", "cofre:write"] : ["cofre:read"], iat: NOW, exp: NOW + 28800 });
  return input + "." + Buffer.from(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(input))).toString("base64url");
}
function fixture(withMedia = true) {
  const db = createDatabase(); dbs.push(db);
  let clock = NOW;
  const data = new Map<string, Uint8Array<ArrayBuffer>>();
  const writes: string[] = [], reads: string[] = [], deletes: string[] = [];
  const controls: { failPut?: boolean; invisible?: boolean; waitPut?: Promise<void> } = {};
  const media = { async put(k: string, value: Uint8Array<ArrayBuffer>) {
    writes.push(k); await controls.waitPut;
    if (controls.failPut) throw new Error("fixture storage failure"); data.set(k, value.slice());
  }, async get(k: string) { reads.push(k); return controls.invisible || !data.has(k) ? null : new Response(data.get(k)!).body; },
  async delete(k: string) { deletes.push(k); data.delete(k); } } as unknown as KVNamespace;
  const worker = createWorker({ now: () => clock, fetcher: async () => new Response(JSON.stringify({ keys: [{ ...jwk, kid: "fixture" }] }),
    { headers: { "Cache-Control": "public,max-age=3600" } }) });
  const env: Env = { DB: db.binding, COFRE_ISSUER_EMAIL: ISSUER, ...(withMedia ? { MEDIA: media } : {}) };
  return { db, worker, env, data, writes, reads, deletes, controls, setClock(n: number) { clock = n; } };
}
type Fixture = ReturnType<typeof fixture>;
async function api(f: Fixture, bearer: string, path: string, method = "GET", body?: unknown, extra: Record<string, string> = {}) {
  const response = await f.worker.fetch(new Request("https://cofre.test" + path, { method,
    headers: { Authorization: "Bearer " + bearer, ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...extra },
    body: body === undefined ? undefined : JSON.stringify(body) }), f.env);
  const data = response.headers.get("Content-Type")?.startsWith("application/json") ? await response.json() as Value : null;
  return { status: response.status, data: data!, response };
}
function state() { return { format: "jk-cofre-shared-v1", guilds: [{ guild: { id: "guild1", name: "Synthetic" }, exported_at: "2026-10-22T00:00:00Z",
  channels: [{ id: "channel1", name: "Guides", messages: [{ id: "legacy", author: "alice", content: "Imported", deleted: false }] },
    { id: "channel2", name: "Other", messages: [] }] }] }; }
async function seed(f: Fixture, bearer: string) { expect((await api(f, bearer, "/v1/state", "PUT", { expected_revision: 0, state: state() })).status).toBe(200); }
async function mutation(f: Fixture, bearer: string, revision: number, action = "message.create", extra: Value = {}) {
  return api(f, bearer, "/v1/mutations", "POST", { request_id: crypto.randomUUID(), expected_revision: revision, action,
    guild_id: "guild1", channel_id: "channel1", ...(action === "message.create" ? { content: "New post" } : {}), ...extra });
}
const PNG = new Uint8Array([137,80,78,71,13,10,26,10,1,2,3]);
async function reserve(f: Fixture, bearer: string, extra: Value = {}) {
  return api(f, bearer, "/v1/assets", "POST", { request_id: crypto.randomUUID(), guild_id: "guild1", channel_id: "channel1",
    filename: "photo.png", size: PNG.length, sha256: await sha256(PNG), ...extra });
}
async function upload(f: Fixture, bearer: string, assetId: string, value = PNG) {
  const response = await f.worker.fetch(new Request("https://cofre.test/v1/assets/" + assetId + "/content", {
    method: "PUT", headers: { Authorization: "Bearer " + bearer, "Content-Type": "text/html" }, body: value }), f.env);
  return { status: response.status, data: await response.json() as Value };
}
async function ready(f: Fixture, bearer: string, extra: Value = {}) {
  const r = await reserve(f, bearer, extra); expect(r.status).toBe(200);
  const u = await upload(f, bearer, r.data.asset_id); expect(u.status).toBe(200); return u.data;
}
describe("native posts, ownership and compatibility", () => {
  it("derives author/id/time from verified identity, preserving the exact old GET shape", async () => {
    const f = fixture(), admin = await token(), reader = await token("bob", false); await seed(f, admin);
    const created = await mutation(f, reader, 1, "message.create", { content: "Texto", links: [{ url: "https://example.test/a" }] });
    expect(created.status).toBe(200); expect(created.data.message_id).toMatch(/^jk_msg_[0-9a-f]{32}$/);
    const m = created.data.state.guilds[0].channels[0].messages[1];
    expect(m).toMatchObject({ author: "bob", timestamp: new Date(NOW * 1000).toISOString(), deleted: false });
    expect(created.data.metadata.ownership).toEqual([{ guild_id: "guild1", channel_id: "channel1", message_id: m.id, owner_username: "bob" }]);
    const old = await api(f, admin, "/v1/state"); expect(Object.keys(old.data).sort()).toEqual(["revision", "state"]);
    const opt = await api(f, admin, "/v1/state", "GET", undefined, { "X-JK-Cofre-Metadata": "1" });
    expect(opt.data.metadata).toEqual(created.data.metadata);
    expect((await mutation(f, reader, 2, "message.create", { author: "admin" })).status).toBe(400);
  });
  it("allows only the actual owner or admin to delete/restore; legacy visual author confers no ownership", async () => {
    const f = fixture(), admin = await token(), a = await token("alice", false), b = await token("bob", false); await seed(f, admin);
    expect((await mutation(f, a, 1, "message.delete", { message_id: "legacy" })).status).toBe(403);
    const created = await mutation(f, a, 1), id = created.data.message_id;
    expect((await mutation(f, b, 2, "message.delete", { message_id: id })).status).toBe(403);
    expect((await mutation(f, a, 2, "message.delete", { message_id: id })).status).toBe(200);
    expect((await mutation(f, b, 3, "message.restore", { message_id: id })).status).toBe(403);
    expect((await mutation(f, a, 3, "message.restore", { message_id: id })).status).toBe(200);
    expect((await mutation(f, admin, 4, "message.delete", { message_id: "legacy" })).status).toBe(200);
  });
  it("channel deletion is admin-only and retains the full v1 snapshot behind an immutable tombstone", async () => {
    const f = fixture(), admin = await token(), reader = await token("bob", false); await seed(f, admin);
    expect((await mutation(f, reader, 1, "channel.delete")).status).toBe(403);
    const removed = await mutation(f, admin, 1, "channel.delete"); expect(removed.status).toBe(200);
    expect(removed.data.state.guilds[0].channels[0].messages[0].id).toBe("legacy");
    expect(removed.data.metadata.deleted_channels).toEqual([{ guild_id: "guild1", channel_id: "channel1" }]);
    expect((await mutation(f, reader, 2)).status).toBe(409);
    expect((await api(f, admin, "/v1/state", "PUT", { expected_revision: 2, state: state() })).status).toBe(200);
    const missing = state(); missing.guilds[0].channels.shift(); missing.guilds[0].channels[0].name = "Edited remainder";
    const old = await api(f, admin, "/v1/state"); expect(old.data.state.guilds[0].channels.map((c: Value) => c.id)).toEqual(["channel2"]);
    expect((await api(f, admin, "/v1/state", "PUT", { expected_revision: 3, state: missing })).status).toBe(200);
    const canonical = await api(f, admin, "/v1/state", "GET", undefined, { "X-JK-Cofre-Metadata": "1" });
    expect(canonical.data.metadata.deleted_channels).toHaveLength(1);
    expect(canonical.data.state.guilds[0].channels.find((c: Value) => c.id === "channel1").messages[0].id).toBe("legacy");
    expect(canonical.data.state.guilds[0].channels.find((c: Value) => c.id === "channel2").name).toBe("Edited remainder");
  });
  it("bootstraps only admin-attested native owners and local channel tombstones; existing owners cannot change", async () => {
    const f = fixture(), admin = await token(), reader = await token("bob", false), initial: Value = state();
    const id = "jk_msg_" + "a".repeat(32);
    initial.guilds[0].channels[0].messages.push({ id, author: "bob", timestamp: "2026-10-26T01:00:00Z", deleted: false, content: "Local post" });
    const metadata = { version: 1, ownership: [{ guild_id: "guild1", channel_id: "channel1", message_id: id, owner_username: "bob" }],
      deleted_channels: [{ guild_id: "guild1", channel_id: "channel2" }] };
    expect((await api(f, reader, "/v1/state", "PUT", { expected_revision: 0, state: initial, metadata })).status).toBe(403);
    expect((await api(f, admin, "/v1/state", "PUT", { expected_revision: 0, state: initial })).status).toBe(409);
    expect((await api(f, admin, "/v1/state", "PUT", { expected_revision: 0, state: initial, metadata })).status).toBe(200);
    metadata.ownership[0].owner_username = "alice"; initial.guilds[0].channels[0].messages[1].author = "alice";
    expect((await api(f, admin, "/v1/state", "PUT", { expected_revision: 1, state: initial, metadata })).status).toBe(409);
    expect((await mutation(f, reader, 1, "message.delete", { message_id: id })).status).toBe(200);
    expect((await api(f, admin, "/v1/state", "PUT", { expected_revision: 2, state: state() })).status).toBe(409);
  });
  it("keeps message tombstones through legacy PUT and accepts an explicit owner restore mutation", async () => {
    const f = fixture(), admin = await token(); await seed(f, admin);
    const c = await mutation(f, admin, 1); await mutation(f, admin, 2, "message.delete", { message_id: c.data.message_id });
    expect((await api(f, admin, "/v1/state", "PUT", { expected_revision: 3, state: c.data.state })).status).toBe(409);
    expect((await mutation(f, admin, 3, "message.restore", { message_id: c.data.message_id })).status).toBe(200);
  });
  it("keeps ownership immutable when an admin changes the visual author or attests an independent display name", async () => {
    const f = fixture(), admin = await token(), bob = await token("bob", false); await seed(f, admin);
    const c = await mutation(f, admin, 1);
    c.data.state.guilds[0].channels[0].messages[1].author = "bob";
    expect((await api(f, admin, "/v1/state", "PUT", { expected_revision: 2, state: c.data.state })).status).toBe(200);
    expect((await mutation(f, bob, 3, "message.delete", { message_id: c.data.message_id })).status).toBe(403);
    const m = await api(f, admin, "/v1/state", "GET", undefined, { "X-JK-Cofre-Metadata": "1" });
    expect(m.data.metadata.ownership[0].owner_username).toBe("alice");
    const f2 = fixture(), s: Value = state(), id = "jk_msg_" + "c".repeat(32);
    s.guilds[0].channels[0].messages.push({ id, author: "Display name", content: "Local", deleted: false });
    expect((await api(f2, admin, "/v1/state", "PUT", { expected_revision: 0, state: s,
      metadata: { version: 1, ownership: [{ guild_id: "guild1", channel_id: "channel1", message_id: id, owner_username: "bob" }] } })).status).toBe(200);
    expect((await mutation(f2, bob, 1, "message.delete", { message_id: id })).status).toBe(200);
  });
  it("has one CAS winner with no leaked ownership/receipt and safely replays an identical UUID", async () => {
    const f = fixture(), admin = await token(); await seed(f, admin);
    const input = { request_id: crypto.randomUUID(), expected_revision: 1, action: "message.create", guild_id: "guild1", channel_id: "channel1", content: "Exact" };
    const results = await Promise.all([api(f, admin, "/v1/mutations", "POST", input), mutation(f, admin, 1)]);
    expect(results.map(r => r.status).sort()).toEqual([200,409]);
    expect(f.db.sqlite.prepare("SELECT COUNT(*) n FROM cofre_message_ownership").get()?.n).toBe(1);
    expect(f.db.sqlite.prepare("SELECT COUNT(*) n FROM cofre_mutation_receipts").get()?.n).toBe(1);
    if (results[0].status === 200) {
      const repeat = await api(f, admin, "/v1/mutations", "POST", { ...input, expected_revision: 2 });
      expect(repeat.status).toBe(200); expect(repeat.data.message_id).toBe(results[0].data.message_id);
      expect((await api(f, admin, "/v1/mutations", "POST", { ...input, content: "Changed" })).status).toBe(409);
      expect((await api(f, await token("bob", false), "/v1/mutations", "POST", input)).status).toBe(409);
    }
  });
  it("deduplicates concurrent copies of the same request", async () => {
    const f = fixture(), admin = await token(); await seed(f, admin);
    const input = { request_id: crypto.randomUUID(), expected_revision: 1, action: "message.create", guild_id: "guild1", channel_id: "channel1", content: "Exact" };
    const results = await Promise.all(Array.from({ length: 5 }, () => api(f, admin, "/v1/mutations", "POST", input)));
    expect(results.every(r => r.status === 200)).toBe(true);
    expect(new Set(results.map(r => r.data.message_id)).size).toBe(1);
    expect(f.db.sqlite.prepare("SELECT revision FROM cofre_company_state").get()?.revision).toBe(2);
  });
  it("rolls back state, owners and receipt together when a later statement fails", async () => {
    const f = fixture(), admin = await token(); await seed(f, admin);
    f.db.sqlite.exec("CREATE TRIGGER fixture_abort BEFORE INSERT ON cofre_mutation_receipts BEGIN SELECT RAISE(ABORT,'fixture'); END");
    expect((await mutation(f, admin, 1)).status).toBe(503);
    expect(f.db.sqlite.prepare("SELECT revision FROM cofre_company_state").get()?.revision).toBe(1);
    expect(f.db.sqlite.prepare("SELECT COUNT(*) n FROM cofre_message_ownership").get()?.n).toBe(0);
    expect(f.db.sqlite.prepare("SELECT COUNT(*) n FROM cofre_mutation_receipts").get()?.n).toBe(0);
  });
  it("isolates case-distinct companies and rejects body identity, extra fields, malformed actions and UUIDs", async () => {
    const f = fixture(), a = await token("alice", true, "Company-A"), b = await token("alice", true, "company-a"); await seed(f, a);
    expect((await mutation(f, b, 0)).status).toBe(404);
    expect((await api(f, b, "/v1/state", "GET", undefined, { "X-JK-Cofre-Metadata": "1" })).data.metadata.ownership).toEqual([]);
    for (const extra of [{ request_id: "not-a-uuid" }, { client_id: "Company-A" }, { owner_username: "admin" }, { action: "message.edit" }, { asset_ids: ["a".repeat(64), "a".repeat(64)] }])
      expect((await mutation(f, a, 1, "message.create", extra)).status).toBe(400);
  });
  it("bounds merged metadata separately while admitting valid state+metadata beyond the old envelope limit", async () => {
    const build = (count: number) => {
      const s: Value = state(), ownership = [];
      for (let n=0; n<count; n++) {
        const id = "jk_msg_" + n.toString(16).padStart(32,"0");
        s.guilds[0].channels[0].messages.push({ id, author: "Name", deleted: false });
        ownership.push({ guild_id: "guild1", channel_id: "channel1", message_id: id, owner_username: "漢".repeat(180) });
      }
      return { expected_revision: 0, state: s, metadata: { version: 1, ownership, deleted_channels: [] } };
    };
    const admin = await token(), f = fixture(), large = build(1502);
    expect(new TextEncoder().encode(JSON.stringify(large)).length).toBeGreaterThan(1024*1024+65536);
    expect(new TextEncoder().encode(JSON.stringify(large.metadata)).length).toBeLessThanOrEqual(1024*1024);
    expect((await api(f, admin, "/v1/state", "PUT", large)).status).toBe(200);
    const other = fixture(), oversized = await api(other, admin, "/v1/state", "PUT", build(1700));
    expect(oversized.status).toBe(413); expect(oversized.data.error).toBe("metadata_too_large");
    expect(other.db.sqlite.prepare("SELECT COUNT(*) n FROM cofre_company_state").get()?.n).toBe(0);
    expect(other.db.sqlite.prepare("SELECT COUNT(*) n FROM cofre_message_ownership").get()?.n).toBe(0);
  });
  it("applies composer limits without shrinking the legacy snapshot schema", async () => {
    const f = fixture(), a = await token(); await seed(f,a);
    expect((await mutation(f,a,1,"message.create",{content:"x".repeat(32001)})).status).toBe(400);
    expect((await mutation(f,a,1,"message.create",{links:Array.from({length:101},()=>({url:"https://example.test"}))})).status).toBe(400);
    const legacy = state(); legacy.guilds[0].channels[0].messages[0].content = "x".repeat(32001);
    expect((await api(f,a,"/v1/state","PUT",{expected_revision:1,state:legacy})).status).toBe(200);
  });
});

describe("private attachments and bounded quotas", () => {
  it("keeps text available without MEDIA and fails attachment reservation clearly", async () => {
    const f = fixture(false), admin = await token(); await seed(f, admin);
    expect((await mutation(f, admin, 1)).status).toBe(200);
    const r = await reserve(f, admin); expect(r.status).toBe(503); expect(r.data.error).toBe("attachment_storage_unavailable");
    expect(f.db.sqlite.prepare("SELECT bytes_reserved FROM cofre_asset_budget").get()?.bytes_reserved).toBe(0);
  });
  it("keeps PDF as octet-stream so a local snapshot publishes with the same canonical MIME", async () => {
    const f = fixture(), a = await token(), content = new TextEncoder().encode("%PDF-1.7\nSynthetic fixture");
    const reserved = await reserve(f, a, { filename: "document.pdf", size: content.length, sha256: await sha256(content) });
    const uploaded = await upload(f, a, reserved.data.asset_id, content);
    expect(uploaded.status).toBe(200); expect(uploaded.data.content_type).toBe("application/octet-stream");
    const initial: Value = state(), id = "jk_msg_" + "d".repeat(32);
    initial.guilds[0].channels[0].messages.push({ id, author: "alice", content: "Local PDF", deleted: false,
      attachments: [{ url: uploaded.data.url, filename: "document.pdf", content_type: "application/octet-stream" }] });
    const published = await api(f,a,"/v1/state","PUT",{expected_revision:0,state:initial,
      metadata:{version:1,ownership:[{guild_id:"guild1",channel_id:"channel1",message_id:id,owner_username:"alice"}],deleted_channels:[]}});
    expect(published.status).toBe(200);
    expect((await api(f,a,new URL(uploaded.data.url).pathname)).response.headers.get("Content-Type")).toBe("application/octet-stream");
  });
  it("reserves once, validates bytes before KV, sniffs MIME and uploads once on retry", async () => {
    const f = fixture(), admin = await token(); await seed(f, admin);
    const request = { request_id: crypto.randomUUID(), guild_id: "guild1", channel_id: "channel1", filename: "x.html", size: PNG.length, sha256: await sha256(PNG) };
    const a = await api(f, admin, "/v1/assets", "POST", request), repeated = await api(f, admin, "/v1/assets", "POST", request);
    expect(a.data.ready).toBe(false); expect(repeated.data.asset_id).toBe(a.data.asset_id);
    expect(f.db.sqlite.prepare("SELECT bytes_reserved FROM cofre_asset_budget").get()?.bytes_reserved).toBe(PNG.length);
    expect((await upload(f, admin, a.data.asset_id, PNG.slice(0, -1))).data.error).toBe("asset_size_mismatch");
    expect((await upload(f, admin, a.data.asset_id, new Uint8Array(PNG.length))).data.error).toBe("asset_hash_mismatch");
    expect(f.writes).toEqual([]);
    const u = await upload(f, admin, a.data.asset_id); expect(u.data).toMatchObject({ ready: true, content_type: "image/png" });
    expect((await upload(f, admin, a.data.asset_id)).status).toBe(200); expect(f.writes).toHaveLength(1);
    expect(f.writes[0]).toBe(await objectKey("company-a", a.data.asset_id));
    const download = await api(f, admin, new URL(u.data.url).pathname);
    expect(download.status).toBe(200); expect(download.response.headers.get("X-Cofre-SHA256")).toBe(request.sha256);
    expect(download.response.headers.get("Content-Disposition")).toContain("attachment;");
    expect(new Uint8Array(await download.response.arrayBuffer())).toEqual(PNG);
  });
  it("checks signed company/uploader and active links before KV reads", async () => {
    const f = fixture(), admin = await token(), a = await token("alice", false), b = await token("bob", false), other = await token("alice", true, "other-company"); await seed(f, admin);
    const asset = await ready(f, a), path = new URL(asset.url).pathname;
    expect((await api(f, b, path)).status).toBe(404); expect((await api(f, other, path)).status).toBe(404); expect(f.reads).toHaveLength(0);
    expect((await upload(f, b, asset.asset_id)).status).toBe(404);
    const c = await mutation(f, a, 1, "message.create", { content: "", asset_ids: [asset.asset_id] }); expect(c.status).toBe(200);
    expect((await api(f, b, path)).status).toBe(200);
    await mutation(f, a, 2, "message.delete", { message_id: c.data.message_id });
    const readCount = f.reads.length;
    expect((await api(f, a, path)).status).toBe(404); expect((await api(f, b, path)).status).toBe(404); expect(f.reads.length).toBe(readCount);
    await mutation(f, a, 3, "message.restore", { message_id: c.data.message_id });
    expect((await api(f, b, path)).status).toBe(200);
    await mutation(f, admin, 4, "channel.delete");
    expect((await api(f, b, path)).status).toBe(404);
  });
  it("forbids attaching another user's pending asset or another company's ready asset", async () => {
    const f = fixture(), admin = await token(), a = await token("alice", false), b = await token("bob", false); await seed(f, admin);
    const pending = await reserve(f, a);
    expect((await mutation(f, a, 1, "message.create", { asset_ids: [pending.data.asset_id] })).status).toBe(409);
    const asset = await ready(f, a);
    expect((await mutation(f, b, 1, "message.create", { asset_ids: [asset.asset_id] })).status).toBe(409);
    const other = await token("alice", true, "other-company"); await seed(f, other);
    expect((await mutation(f, other, 1, "message.create", { asset_ids: [asset.asset_id] })).status).toBe(409);
    expect((await mutation(f, a, 1, "message.create", { asset_ids: Array.from({ length: 6 }, () => "a".repeat(64)) })).status).toBe(400);
  });
  it("returns retryable eventual consistency failures and never exposes unready bytes", async () => {
    const f = fixture(), admin = await token(); await seed(f, admin);
    const pending = await reserve(f, admin);
    const p = await api(f, admin, new URL(pending.data.url).pathname);
    expect(p.status).toBe(503); expect(p.response.headers.get("Retry-After")).toBe("60"); expect(f.reads).toHaveLength(0);
    const asset = await upload(f, admin, pending.data.asset_id); f.controls.invisible = true;
    const missing = await api(f, admin, new URL(asset.data.url).pathname); expect(missing.status).toBe(503);
    expect(missing.response.headers.get("Retry-After")).toBe("60"); f.controls.invisible = false;
    expect((await api(f, admin, new URL(asset.data.url).pathname)).status).toBe(200);
  });
  it("retains byte reservations after KV failure and counts each actual write attempt", async () => {
    const f = fixture(), admin = await token(); await seed(f, admin); const asset = await reserve(f, admin);
    f.controls.failPut = true; expect((await upload(f, admin, asset.data.asset_id)).status).toBe(503);
    expect(f.db.sqlite.prepare("SELECT bytes_reserved FROM cofre_asset_budget").get()?.bytes_reserved).toBe(PNG.length);
    expect(f.db.sqlite.prepare("SELECT status FROM cofre_assets").get()?.status).toBe("pending");
    f.controls.failPut = false; expect((await upload(f, admin, asset.data.asset_id)).status).toBe(200);
    expect(f.db.sqlite.prepare("SELECT writes_reserved FROM cofre_asset_days").get()?.writes_reserved).toBe(2);
  });
  it("serializes concurrent content uploads to the same key", async () => {
    const f = fixture(), admin = await token(); await seed(f, admin); const asset = await reserve(f, admin);
    let release!: () => void; f.controls.waitPut = new Promise<void>(resolve => { release = resolve; });
    const first = upload(f, admin, asset.data.asset_id);
    while (!f.writes.length) await new Promise(resolve => setTimeout(resolve, 1));
    expect((await upload(f, admin, asset.data.asset_id)).status).toBe(409); release(); expect((await first).status).toBe(200);
    expect(f.writes).toHaveLength(1);
  });
  it("enforces global bytes and daily write caps atomically under competing requests", async () => {
    const f = fixture(), admin = await token(); await seed(f, admin);
    f.db.sqlite.prepare("UPDATE cofre_asset_budget SET bytes_reserved=367001600-?1").run(PNG.length);
    const r = await Promise.all([reserve(f, admin), reserve(f, admin)]); expect(r.map(x => x.status).sort()).toEqual([200,413]);
    const asset = r.find(x => x.status === 200)!.data;
    f.db.sqlite.prepare("UPDATE cofre_asset_days SET writes_reserved=250 WHERE day=?1").run(new Date(NOW * 1000).toISOString().slice(0,10));
    expect((await upload(f, admin, asset.asset_id)).status).toBe(429); expect(f.writes).toHaveLength(0);
    expect(f.db.sqlite.prepare("SELECT status FROM cofre_assets").get()?.status).toBe("pending");
    expect((await reserve(f, admin)).status).toBe(429);
  });
  it("bounds cleanup to exact Cofre keys and retains attached or fresh assets", async () => {
    const f = fixture(), admin = await token(); await seed(f, admin);
    const attached = await ready(f, admin); await mutation(f, admin, 1, "message.create", { asset_ids: [attached.asset_id] });
    const orphans = [await reserve(f, admin), await reserve(f, admin), await reserve(f, admin)];
    f.data.set("whatsapp:synthetic", new Uint8Array([9]));
    f.setClock(NOW + 3600); f.db.sqlite.prepare("UPDATE cofre_assets SET created_at=?1 WHERE attached=0").run(NOW - 86401);
    await reserve(f, admin);
    expect(f.deletes).toHaveLength(2); expect(f.deletes.every(k => k.startsWith("cofre:v1:"))).toBe(true);
    expect(f.data.has("whatsapp:synthetic")).toBe(true);
    expect(f.db.sqlite.prepare("SELECT COUNT(*) n FROM cofre_assets WHERE attached=1").get()?.n).toBe(1);
    expect(orphans.filter(o => f.db.sqlite.prepare("SELECT 1 FROM cofre_assets WHERE asset_id=?1").get(o.data.asset_id))).toHaveLength(1);
  });
  it("requires a valid signed capability on every new endpoint before any D1 or KV access", async () => {
    const f = fixture();
    for (const [path, method] of [["/v1/mutations", "POST"], ["/v1/assets", "POST"], ["/v1/assets/" + "a".repeat(64) + "/content", "PUT"], ["/v1/assets/" + "a".repeat(64) + "/x", "GET"]]) {
      const r = await f.worker.fetch(new Request("https://cofre.test" + path, { method, headers: { Authorization: "Bearer forged" } }), f.env);
      expect(r.status).toBe(401);
    }
    expect(f.db.calls).toHaveLength(0); expect(f.reads).toHaveLength(0); expect(f.writes).toHaveLength(0);
  });
  it("shares the byte quota across companies rather than allocating it per tenant", async () => {
    const f = fixture(), a = await token(), b = await token("alice", true, "company-b");
    f.db.sqlite.prepare("UPDATE cofre_asset_budget SET bytes_reserved=367001600-?1").run(PNG.length);
    const result = await Promise.all([reserve(f, a), reserve(f, b)]);
    expect(result.map(r => r.status).sort()).toEqual([200,413]);
    expect(f.db.sqlite.prepare("SELECT COUNT(*) n FROM cofre_assets").get()?.n).toBe(1);
  });
  it("admits one final daily KV write atomically and leaves the losing upload pending", async () => {
    const f = fixture(), a = await token(); const one = await reserve(f, a), two = await reserve(f, a);
    f.db.sqlite.prepare("UPDATE cofre_asset_days SET writes_reserved=249 WHERE day=?1").run(new Date(NOW * 1000).toISOString().slice(0,10));
    const result = await Promise.all([upload(f, a, one.data.asset_id), upload(f, a, two.data.asset_id)]);
    expect(result.map(r => r.status).sort()).toEqual([200,429]); expect(f.writes).toHaveLength(1);
    expect(f.db.sqlite.prepare("SELECT writes_reserved FROM cofre_asset_days").get()?.writes_reserved).toBe(250);
    expect(f.db.sqlite.prepare("SELECT COUNT(*) n FROM cofre_assets WHERE status='pending'").get()?.n).toBe(1);
  });
  it("preserves links and native data when an old publisher submits a stale state", async () => {
    const f = fixture(), a = await token(); await seed(f, a); const asset = await ready(f, a);
    const c = await mutation(f, a, 1, "message.create", { asset_ids: [asset.asset_id] });
    expect((await api(f, a, "/v1/state", "PUT", { expected_revision: 1, state: state() })).status).toBe(409);
    expect((await api(f, a, "/v1/state", "PUT", { expected_revision: 2, state: state() })).status).toBe(409);
    expect((await api(f, a, new URL(asset.url).pathname)).status).toBe(200);
    expect(f.db.sqlite.prepare("SELECT COUNT(*) n FROM cofre_asset_links").get()?.n).toBe(1);
    expect((await api(f, a, "/v1/state")).data.state).toEqual(c.data.state);
  });
  it("cannot introduce a foreign company's asset through an admin snapshot", async () => {
    const f = fixture(), a = await token(), b = await token("alice", true, "company-b"); const asset = await ready(f, a);
    const initial: Value = state(); initial.guilds[0].channels[0].messages[0].attachments = [{ url: asset.url, filename: asset.filename, content_type: asset.content_type }];
    expect((await api(f, b, "/v1/state", "PUT", { expected_revision: 0, state: initial })).status).toBe(409);
    expect((await api(f, b, "/v1/state")).data.revision).toBe(0); expect(f.reads).toHaveLength(0);
  });
  it("retains a deleted bootstrap message's bytes for restore without granting downloads", async () => {
    const f = fixture(), a = await token(), b = await token("bob", false), asset = await ready(f, a);
    const initial: Value = state(), id = "jk_msg_" + "b".repeat(32);
    initial.guilds[0].channels[0].messages.push({ id, author: "alice", timestamp: "2026-10-26T00:00:00Z", deleted: true,
      attachments: [{ url: asset.url, filename: asset.filename, content_type: asset.content_type }] });
    expect((await api(f, a, "/v1/state", "PUT", { expected_revision: 0, state: initial,
      metadata: { version: 1, ownership: [{ guild_id: "guild1", channel_id: "channel1", message_id: id, owner_username: "alice" }], deleted_channels: [] } })).status).toBe(200);
    expect(f.db.sqlite.prepare("SELECT attached FROM cofre_assets").get()?.attached).toBe(1);
    expect((await api(f, a, new URL(asset.url).pathname)).status).toBe(404);
    f.db.sqlite.prepare("UPDATE cofre_assets SET created_at=?1").run(NOW-90000); await reserve(f, a); expect(f.deletes).toHaveLength(0);
    await mutation(f, a, 1, "message.restore", { message_id: id }); expect((await api(f, b, new URL(asset.url).pathname)).status).toBe(200);
  });
  it("does not reclaim an indeterminate in-flight upload or release its quota", async () => {
    const f = fixture(), a = await token(); const first = await reserve(f, a);
    f.db.sqlite.prepare("UPDATE cofre_assets SET status='uploading',created_at=?1,upload_fence='fixture',lease_until=?2 WHERE asset_id=?3").run(NOW-90000,NOW-1,first.data.asset_id);
    await reserve(f, a); expect(f.deletes).toHaveLength(0);
    expect(f.db.sqlite.prepare("SELECT bytes_reserved FROM cofre_asset_budget").get()?.bytes_reserved).toBe(PNG.length*2);
  });
  it("caps tiny pending reservations independently from actual KV writes and preserves idempotent retries", async () => {
    const f = fixture(), a = await token();
    const request = { request_id: crypto.randomUUID(), guild_id: "guild1", channel_id: "channel1", filename: "x.bin", size: 1, sha256: "a".repeat(64) };
    const first = await api(f, a, "/v1/assets", "POST", request); expect(first.status).toBe(200);
    for (let n=1; n<249; n++) expect((await reserve(f, a, { size: 1 })).status).toBe(200);
    const last = await Promise.all([reserve(f, a, { size: 1 }), reserve(f, a, { size: 1 })]);
    expect(last.map(r => r.status).sort()).toEqual([200,429]);
    expect(f.db.sqlite.prepare("SELECT COUNT(*) n FROM cofre_assets").get()?.n).toBe(250);
    expect(f.db.sqlite.prepare("SELECT bytes_reserved FROM cofre_asset_budget").get()?.bytes_reserved).toBe(250);
    expect(f.db.sqlite.prepare("SELECT reservations_reserved,writes_reserved FROM cofre_asset_days").get()).toMatchObject({ reservations_reserved: 250, writes_reserved: 0 });
    const again = await api(f, a, "/v1/assets", "POST", request); expect(again.status).toBe(200); expect(again.data.asset_id).toBe(first.data.asset_id);
  });
  it("restores nothing when a failing metadata statement aborts a channel deletion", async () => {
    const f = fixture(), a = await token(); await seed(f, a); const asset = await ready(f, a); await mutation(f, a, 1, "message.create", { asset_ids: [asset.asset_id] });
    f.db.sqlite.exec("CREATE TRIGGER fixture_abort BEFORE INSERT ON cofre_mutation_receipts BEGIN SELECT RAISE(ABORT,'fixture'); END");
    expect((await mutation(f, a, 2, "channel.delete")).status).toBe(503);
    expect(f.db.sqlite.prepare("SELECT COUNT(*) n FROM cofre_deleted_channels").get()?.n).toBe(0);
    expect((await api(f, a, new URL(asset.url).pathname)).status).toBe(200);
    expect((await api(f, a, "/v1/state")).data.revision).toBe(2);
  });
  it.each([{ filename: "../x" }, { filename: "x\r\nContent-Type:text/html" }, { size: 10485761 }, { size: 0 }, { sha256: "not-a-hash" }, { owner_username: "admin" }])("rejects unsafe reservation metadata %j before writes", async extra => {
    const f = fixture(), admin = await token(); await seed(f, admin);
    expect((await reserve(f, admin, extra)).status).toBe(400); expect(f.writes).toHaveLength(0);
    expect(f.db.sqlite.prepare("SELECT bytes_reserved FROM cofre_asset_budget").get()?.bytes_reserved).toBe(0);
  });
});
