import type { Principal } from "./auth";
import { fail, randomHex, sha256, type AssetRequest, type Value } from "./publishing-schema";
import { assetResponse, findChannel, isAdmin, readView, type AssetRow } from "./publishing";

export interface AssetEnv { DB: D1Database; MEDIA?: KVNamespace }
function media(env: AssetEnv): KVNamespace {
  if (!env.MEDIA) fail("attachment_storage_unavailable", 503);
  return env.MEDIA;
}
export async function objectKey(company: string, assetId: string): Promise<string> {
  return "cofre:v1:" + await sha256(company) + ":" + assetId;
}
function quota(error: unknown, code: string, status: number): never {
  if (/CHECK constraint failed/.test(String(error))) fail(code, status);
  throw error;
}
async function cleanup(env: AssetEnv, now: number): Promise<void> {
  // A small, bounded sweep of this service's own D1 records; never list shared KV.
  const rows = await env.DB.prepare("SELECT * FROM cofre_assets WHERE attached=0 AND status IN ('pending','ready','deleting') AND created_at<?1 AND (lease_until IS NULL OR lease_until<?2) ORDER BY created_at LIMIT 2")
    .bind(now - 86400, now).all<AssetRow>();
  for (const row of rows.results) {
    const fence = randomHex();
    const claim = await env.DB.prepare("UPDATE cofre_assets SET status='deleting',upload_fence=?3,lease_until=?4 WHERE company=?1 AND asset_id=?2 AND attached=0 AND status IN ('pending','ready','deleting') AND created_at<?5 AND (lease_until IS NULL OR lease_until<?6) RETURNING asset_id")
      .bind(row.company, row.asset_id, fence, now + 300, now - 86400, now).first();
    if (!claim) continue;
    try {
      await media(env).delete(await objectKey(row.company, row.asset_id));
      await env.DB.prepare("DELETE FROM cofre_assets WHERE company=?1 AND asset_id=?2 AND status='deleting' AND upload_fence=?3 AND attached=0")
        .bind(row.company, row.asset_id, fence).run();
    } catch { /* Retain the reservation until a later successful, exact-key cleanup. */ }
  }
}
export async function reserveAsset(env: AssetEnv, p: Principal, input: AssetRequest, origin: string, now: number): Promise<Value> {
  media(env);
  const old = await env.DB.prepare("SELECT * FROM cofre_assets WHERE company=?1 AND request_id=?2").bind(p.company, input.request_id).first<AssetRow>();
  if (old) {
    if (old.owner_username !== p.username || ["guild_id", "channel_id", "filename", "size", "sha256"].some(k => (old as unknown as Value)[k] !== (input as unknown as Value)[k])) fail("request_id_conflict", 409);
    if (old.status === "deleting") fail("asset_expired", 410);
    return assetResponse(origin, old);
  }
  const view = await readView(env.DB, p.company);
  if (view.metadata.deleted_channels.some(d => d.guild_id === input.guild_id && d.channel_id === input.channel_id)) fail("channel_deleted", 409);
  // Publishers may upload local draft attachments before the first publication.
  if (!isAdmin(p) && !findChannel(view.state, input.guild_id, input.channel_id)) fail("channel_not_found", 404);
  await cleanup(env, now);
  const day = new Date(now * 1000).toISOString().slice(0, 10);
  const used = await env.DB.prepare("SELECT writes_reserved,reservations_reserved FROM cofre_asset_days WHERE day=?1").bind(day).first<{ writes_reserved: number; reservations_reserved: number }>();
  if ((used?.writes_reserved ?? 0) >= 250 || (used?.reservations_reserved ?? 0) >= 250) fail("attachment_daily_quota", 429);
  const assetId = randomHex();
  try {
    await env.DB.prepare("INSERT INTO cofre_assets(company,asset_id,request_id,owner_username,guild_id,channel_id,filename,size,sha256,status,created_at) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,'pending',?10) ON CONFLICT(company,request_id) DO NOTHING")
      .bind(p.company, assetId, input.request_id, p.username, input.guild_id, input.channel_id, input.filename, input.size, input.sha256, now).run();
  } catch (error) {
    if (String(error).includes("reservations_reserved")) quota(error, "attachment_daily_quota", 429);
    quota(error, "attachment_storage_quota", 413);
  }
  const row = await env.DB.prepare("SELECT * FROM cofre_assets WHERE company=?1 AND request_id=?2").bind(p.company, input.request_id).first<AssetRow>();
  if (!row) fail("attachment_storage_unavailable", 503);
  if (row.asset_id !== assetId) return reserveAsset(env, p, input, origin, now);
  return assetResponse(origin, row);
}
async function bytes(request: Request, expected: number): Promise<Uint8Array<ArrayBuffer>> {
  const declared = request.headers.get("content-length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) !== expected)) fail("asset_size_mismatch");
  if (!request.body) fail("asset_size_mismatch");
  const result = new Uint8Array(expected), reader = request.body.getReader();
  let position = 0;
  try {
    for (;;) {
      const chunk = await reader.read(); if (chunk.done) break;
      if (position + chunk.value.length > expected) { await reader.cancel(); fail("asset_size_mismatch"); }
      result.set(chunk.value, position); position += chunk.value.length;
    }
  } finally { reader.releaseLock(); }
  if (position !== expected) fail("asset_size_mismatch");
  return result;
}
function sniff(b: Uint8Array): string {
  const starts = (...value: number[]) => value.every((v, i) => b[i] === v);
  if (starts(137,80,78,71,13,10,26,10)) return "image/png";
  if (starts(255,216,255)) return "image/jpeg";
  const text = new TextDecoder().decode(b.slice(0, 12));
  if (text.startsWith("GIF87a") || text.startsWith("GIF89a")) return "image/gif";
  if (text.startsWith("RIFF") && text.slice(8, 12) === "WEBP") return "image/webp";
  return "application/octet-stream";
}
export async function uploadAsset(env: AssetEnv, p: Principal, assetId: string, request: Request, origin: string, now: number): Promise<Value> {
  const kv = media(env);
  const row = await env.DB.prepare("SELECT * FROM cofre_assets WHERE company=?1 AND asset_id=?2").bind(p.company, assetId).first<AssetRow>();
  if (!row || row.owner_username !== p.username) fail("asset_not_found", 404);
  if (row.status === "ready") return assetResponse(origin, row);
  if (row.status === "deleting") fail("asset_expired", 410);
  const content = await bytes(request, row.size);
  if (await sha256(content) !== row.sha256) fail("asset_hash_mismatch");
  const fence = randomHex();
  let claim: unknown;
  try {
    claim = await env.DB.prepare("UPDATE cofre_assets SET status='uploading',upload_fence=?3,lease_until=?4 WHERE company=?1 AND asset_id=?2 AND (status='pending' OR (status='uploading' AND lease_until<?5)) RETURNING asset_id")
      .bind(p.company, assetId, fence, now + 300, now).first();
  } catch (error) { quota(error, "attachment_daily_quota", 429); }
  if (!claim) fail("upload_in_progress", 409);
  const contentType = sniff(content);
  try {
    await kv.put(await objectKey(p.company, assetId), content, { metadata: { sha256: row.sha256 } });
  } catch {
    await env.DB.prepare("UPDATE cofre_assets SET status='pending',upload_fence=NULL,lease_until=NULL WHERE company=?1 AND asset_id=?2 AND upload_fence=?3")
      .bind(p.company, assetId, fence).run();
    fail("attachment_storage_unavailable", 503);
  }
  const saved = await env.DB.prepare("UPDATE cofre_assets SET status='ready',content_type=?4,upload_fence=NULL,lease_until=NULL WHERE company=?1 AND asset_id=?2 AND status='uploading' AND upload_fence=?3 RETURNING *")
    .bind(p.company, assetId, fence, contentType).first<AssetRow>();
  if (!saved) fail("attachment_storage_unavailable", 503);
  return assetResponse(origin, saved);
}
export async function downloadAsset(env: AssetEnv, p: Principal, assetId: string, filename: string): Promise<{ stream: ReadableStream; row: AssetRow }> {
  const kv = media(env);
  // This company-scoped authorization is evaluated before any KV read, including cached reads.
  const row = await env.DB.prepare("SELECT a.* FROM cofre_assets a WHERE a.company=?1 AND a.asset_id=?2 AND a.filename=?3 AND (EXISTS(SELECT 1 FROM cofre_asset_links l WHERE l.company=a.company AND l.asset_id=a.asset_id) OR (a.attached=0 AND a.owner_username=?4 AND a.status<>'deleting'))")
    .bind(p.company, assetId, filename, p.username).first<AssetRow>();
  if (!row) fail("asset_not_found", 404);
  if (row.status !== "ready") fail("asset_not_ready", 503);
  const stream = await kv.get(await objectKey(p.company, assetId), "stream");
  if (!stream) fail("asset_not_ready", 503);
  return { stream, row };
}
