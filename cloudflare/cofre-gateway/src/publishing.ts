import type { Principal } from "./auth";
import { EMPTY_STATE, MAX_STATE_BYTES, serializeState } from "./schema";
import { NATIVE_ID, fail, randomHex, sha256, tuple, validateAttestation, type DeletedChannel, type Metadata,
  type Mutation, type Ownership, type Value } from "./publishing-schema";

export interface View { revision: number; state: Value; metadata: Metadata }
export interface AssetRow {
  company: string; asset_id: string; request_id: string; owner_username: string; guild_id: string; channel_id: string;
  filename: string; size: number; sha256: string; content_type: string; status: string; attached: number;
  created_at: number; upload_fence: string | null; lease_until: number | null;
}
export function isAdmin(p: Principal): boolean { return p.scopes.includes("cofre:write"); }
export function findChannel(state: Value, guild: string, channel: string): Value | undefined {
  return state.guilds.find((g: Value) => g.guild.id === guild)?.channels.find((c: Value) => c.id === channel);
}
export function messages(state: Value): { guild: string; channel: string; message: Value }[] {
  return state.guilds.flatMap((g: Value) => g.channels.flatMap((c: Value) => c.messages.map((m: Value) =>
    ({ guild: g.guild.id, channel: c.id, message: m }))));
}
export async function readView(db: D1Database, company: string): Promise<View> {
  const result = await db.batch([
    db.prepare("SELECT revision,state FROM cofre_company_state WHERE company=?1").bind(company),
    db.prepare("SELECT guild_id,channel_id,message_id,owner_username FROM cofre_message_ownership WHERE company=?1 ORDER BY guild_id,channel_id,message_id").bind(company),
    db.prepare("SELECT guild_id,channel_id FROM cofre_deleted_channels WHERE company=?1 ORDER BY guild_id,channel_id").bind(company),
  ]);
  const row = result[0].results[0] as { revision: number; state: string } | undefined;
  const state = row ? JSON.parse(row.state) : structuredClone(EMPTY_STATE);
  const present = new Set(messages(state).map(m => tuple(m.guild, m.channel, m.message.id)));
  return { revision: row?.revision ?? 0, state, metadata: { version: 1,
    ownership: (result[1].results as unknown as Ownership[]).filter(o => present.has(tuple(o.guild_id, o.channel_id, o.message_id))),
    deleted_channels: result[2].results as unknown as DeletedChannel[] } };
}
export function assetUrl(origin: string, row: Pick<AssetRow, "asset_id" | "filename">): string {
  return origin + "/v1/assets/" + row.asset_id + "/" + encodeURIComponent(row.filename);
}
export function assetResponse(origin: string, row: AssetRow): Value {
  return { asset_id: row.asset_id, url: assetUrl(origin, row), filename: row.filename, content_type: row.content_type,
    size: row.size, sha256: row.sha256, ready: row.status === "ready" };
}
interface AssetLink { asset_id: string; guild_id: string; channel_id: string; message_id: string }
async function references(db: D1Database, company: string, state: Value, origin: string, deleted: DeletedChannel[]): Promise<AssetLink[]> {
  const links: AssetLink[] = [];
  for (const m of messages(state)) for (const a of m.message.attachments ?? []) {
    const u = new URL(a.url);
    const match = /^\/v1\/assets\/([0-9a-f]{64})\/([^/]+)$/.exec(u.pathname);
    if (!match) continue;
    if (u.origin !== origin || u.search || u.hash) fail("invalid_asset_reference", 409);
    links.push({ asset_id: match[1], guild_id: m.guild, channel_id: m.channel, message_id: m.message.id });
  }
  const ids = [...new Set(links.map(l => l.asset_id))];
  if (!ids.length) return links;
  const rows = await db.prepare("SELECT * FROM cofre_assets WHERE company=?1 AND asset_id IN (SELECT value FROM json_each(?2))")
    .bind(company, JSON.stringify(ids)).all<AssetRow>();
  const byId = new Map(rows.results.map(r => [r.asset_id, r]));
  for (const m of messages(state)) for (const a of m.message.attachments ?? []) {
    const u = new URL(a.url), match = /^\/v1\/assets\/([0-9a-f]{64})\//.exec(u.pathname);
    if (!match) continue;
    const row = byId.get(match[1]);
    if (!row || row.status !== "ready" || row.guild_id !== m.guild || row.channel_id !== m.channel
        || a.url !== assetUrl(origin, row) || a.filename !== row.filename || (a.content_type ?? "") !== row.content_type) fail("invalid_asset_reference", 409);
  }
  // Only live messages grant a download. Deleted messages retain bytes for restore.
  const hidden = new Set(deleted.map(d => tuple(d.guild_id, d.channel_id)));
  const live = new Set(messages(state).filter(m => !m.message.deleted && !hidden.has(tuple(m.guild, m.channel))).map(m => tuple(m.guild, m.channel, m.message.id)));
  return links.filter(l => live.has(tuple(l.guild_id, l.channel_id, l.message_id)));
}
interface Receipt { request_id: string; actor: string; fingerprint: string; message_id: string | null }
async function commit(db: D1Database, p: Principal, expected: number, state: Value, ownership: Ownership[],
  deleted: DeletedChannel[], origin: string, now: number, metadataOwnership: Ownership[], receipt?: Receipt): Promise<boolean> {
  deleted = [...new Map(deleted.map(d => [tuple(d.guild_id, d.channel_id), d])).values()];
  if (new TextEncoder().encode(JSON.stringify({ version: 1, ownership: metadataOwnership, deleted_channels: deleted })).byteLength > MAX_STATE_BYTES) fail("metadata_too_large", 413);
  const serialized = serializeState(state), links = await references(db, p.company, state, origin, deleted), fence = randomHex();
  // All statements are in one D1 transaction; CAS misses are not SQL errors, hence every side effect is fenced.
  const required = JSON.stringify([...new Set(messages(state).flatMap(m => (m.message.attachments ?? []).flatMap((a: Value) => {
    const match = /^\/v1\/assets\/([0-9a-f]{64})\//.exec(new URL(a.url).pathname); return match ? [match[1]] : [];
  })))]);
  const available = "NOT EXISTS(SELECT 1 FROM json_each(?6) wanted LEFT JOIN cofre_assets a ON a.company=?1 AND a.asset_id=wanted.value WHERE a.asset_id IS NULL OR a.status<>'ready')";
  const first = expected === 0
    ? db.prepare("INSERT INTO cofre_company_state(company,revision,state,updated_at,fence) SELECT ?1,1,?2,?3,?4 WHERE ?5=0 AND " + available + " ON CONFLICT(company) DO NOTHING RETURNING revision")
      .bind(p.company, serialized, now, fence, expected, required)
    : db.prepare("UPDATE cofre_company_state SET state=?2,updated_at=?3,fence=?4,revision=revision+1 WHERE company=?1 AND revision=?5 AND revision<9007199254740991 AND " + available + " RETURNING revision")
      .bind(p.company, serialized, now, fence, expected, required);
  const guard = "EXISTS(SELECT 1 FROM cofre_company_state WHERE company=?1 AND fence=?2)";
  const statements = [first,
    db.prepare("INSERT INTO cofre_message_ownership(company,guild_id,channel_id,message_id,owner_username) SELECT ?1,json_extract(value,'$.guild_id'),json_extract(value,'$.channel_id'),json_extract(value,'$.message_id'),json_extract(value,'$.owner_username') FROM json_each(?3) WHERE " + guard + " ON CONFLICT DO NOTHING").bind(p.company, fence, JSON.stringify(ownership)),
    db.prepare("INSERT INTO cofre_deleted_channels(company,guild_id,channel_id) SELECT ?1,json_extract(value,'$.guild_id'),json_extract(value,'$.channel_id') FROM json_each(?3) WHERE " + guard + " ON CONFLICT DO NOTHING").bind(p.company, fence, JSON.stringify(deleted)),
    db.prepare("DELETE FROM cofre_asset_links WHERE company=?1 AND " + guard).bind(p.company, fence),
    db.prepare("INSERT INTO cofre_asset_links(company,asset_id,guild_id,channel_id,message_id) SELECT DISTINCT ?1,json_extract(value,'$.asset_id'),json_extract(value,'$.guild_id'),json_extract(value,'$.channel_id'),json_extract(value,'$.message_id') FROM json_each(?3) WHERE " + guard).bind(p.company, fence, JSON.stringify(links)),
    db.prepare("UPDATE cofre_assets SET attached=1 WHERE company=?1 AND asset_id IN (SELECT value FROM json_each(?3)) AND " + guard).bind(p.company, fence, required),
  ];
  if (receipt) statements.push(db.prepare("INSERT INTO cofre_mutation_receipts(company,request_id,actor,fingerprint,message_id,created_at) SELECT ?1,?3,?4,?5,?6,?7 WHERE " + guard)
    .bind(p.company, fence, receipt.request_id, receipt.actor, receipt.fingerprint, receipt.message_id, now));
  const result = await db.batch(statements);
  return result[0].results.length === 1;
}
export async function putState(db: D1Database, p: Principal, expected: number, state: Value, attestation: unknown,
  origin: string, now: number): Promise<number | null> {
  const current = await readView(db, p.company);
  if (current.revision !== expected) return null;
  // Old readers omit hidden channels. Preserve their canonical history before validating the next snapshot.
  for (const d of current.metadata.deleted_channels) {
    const canonicalGuild = current.state.guilds.find((g: Value) => g.guild.id === d.guild_id);
    const canonical = findChannel(current.state, d.guild_id, d.channel_id);
    if (!canonical || !canonicalGuild) fail("channel_tombstone_conflict", 409);
    let guild = state.guilds.find((g: Value) => g.guild.id === d.guild_id);
    if (!guild) { guild = { ...canonicalGuild, channels: [] }; state.guilds.push(guild); }
    const index = guild.channels.findIndex((c: Value) => c.id === d.channel_id);
    if (index < 0) guild.channels.push(structuredClone(canonical));
    else guild.channels[index] = structuredClone(canonical);
  }
  const rows = await db.prepare("SELECT guild_id,channel_id,message_id,owner_username FROM cofre_message_ownership WHERE company=?1").bind(p.company).all<Ownership>();
  const owners = new Map(rows.results.map(o => [tuple(o.guild_id, o.channel_id, o.message_id), o]));
  const metadata = attestation === undefined ? { version: 1, ownership: [], deleted_channels: [] } : validateAttestation(attestation);
  const supplied = metadata.ownership;
  const nextMessages = new Map(messages(state).map(m => [tuple(m.guild, m.channel, m.message.id), m]));
  for (const o of supplied) {
    const key = tuple(o.guild_id, o.channel_id, o.message_id), old = owners.get(key);
    if (!nextMessages.has(key) || (old && old.owner_username !== o.owner_username)) fail("ownership_conflict", 409);
    if (!old && rows.results.some(r => r.message_id === o.message_id)) fail("ownership_conflict", 409);
    owners.set(key, o);
  }
  for (const m of messages(state)) if (NATIVE_ID.test(m.message.id)) {
    const o = owners.get(tuple(m.guild, m.channel, m.message.id));
    if (!o) fail("ownership_required", 409);
  }
  for (const old of messages(current.state)) {
    const next = nextMessages.get(tuple(old.guild, old.channel, old.message.id));
    if (NATIVE_ID.test(old.message.id) && (!next || next.message.timestamp !== old.message.timestamp)) fail("native_message_conflict", 409);
    if (old.message.deleted && next && !next.message.deleted) fail("message_tombstone_conflict", 409);
  }
  const deleted = [...current.metadata.deleted_channels, ...metadata.deleted_channels];
  for (const d of deleted) if (!findChannel(state, d.guild_id, d.channel_id)) fail("channel_tombstone_conflict", 409);
  const resultingOwners = [...owners.values()].filter(o => nextMessages.has(tuple(o.guild_id, o.channel_id, o.message_id)));
  return await commit(db, p, expected, state, supplied, deleted, origin, now, resultingOwners) ? expected + 1 : null;
}
export async function mutate(db: D1Database, p: Principal, input: Mutation, origin: string, now: number): Promise<Value | null> {
  const semantic = { request_id: input.request_id, action: input.action, guild_id: input.guild_id, channel_id: input.channel_id,
    ...(input.message_id === undefined ? {} : { message_id: input.message_id }), ...(input.action === "message.create" ? {
      content: input.content ?? "", links: (input.links ?? []).map(l => ({ url: l.url, ...(l.text === undefined ? {} : { text: l.text }) })),
      asset_ids: input.asset_ids ?? [] } : {}) };
  const fingerprint = await sha256(JSON.stringify(semantic));
  const prior = async (): Promise<Value | undefined> => {
    const r = await db.prepare("SELECT actor,fingerprint,message_id FROM cofre_mutation_receipts WHERE company=?1 AND request_id=?2")
      .bind(p.company, input.request_id).first<Receipt>();
    if (!r) return;
    if (r.actor !== p.username || r.fingerprint !== fingerprint) fail("request_id_conflict", 409);
    return { ...await readView(db, p.company), ...(r.message_id ? { message_id: r.message_id } : {}) };
  };
  const replay = await prior(); if (replay) return replay;
  const view = await readView(db, p.company);
  if (view.revision !== input.expected_revision) return null;
  const channel = findChannel(view.state, input.guild_id, input.channel_id);
  if (!channel) fail("channel_not_found", 404);
  if (view.metadata.deleted_channels.some(d => d.guild_id === input.guild_id && d.channel_id === input.channel_id)) fail("channel_deleted", 409);
  const ownership: Ownership[] = [], deleted: DeletedChannel[] = [];
  let messageId: string | null = null;
  if (input.action === "channel.delete") {
    if (!isAdmin(p)) fail("forbidden", 403);
    deleted.push({ guild_id: input.guild_id, channel_id: input.channel_id });
  } else if (input.action === "message.create") {
    messageId = "jk_msg_" + crypto.randomUUID().replaceAll("-", "");
    const attachments = [];
    for (const assetId of input.asset_ids ?? []) {
      const asset = await db.prepare("SELECT * FROM cofre_assets WHERE company=?1 AND asset_id=?2").bind(p.company, assetId).first<AssetRow>();
      if (!asset || asset.status !== "ready" || asset.guild_id !== input.guild_id || asset.channel_id !== input.channel_id
          || (!isAdmin(p) && asset.owner_username !== p.username)) fail("invalid_asset_reference", 409);
      attachments.push({ url: assetUrl(origin, asset), filename: asset.filename, content_type: asset.content_type });
    }
    channel.messages.push({ id: messageId, author: p.username, timestamp: new Date(now * 1000).toISOString(), content: input.content ?? "",
      links: input.links ?? [], attachments, embeds: [], source_url: "", deleted: false });
    ownership.push({ guild_id: input.guild_id, channel_id: input.channel_id, message_id: messageId, owner_username: p.username });
  } else {
    const message = channel.messages.find((m: Value) => m.id === input.message_id);
    if (!message) fail("message_not_found", 404);
    const owner = view.metadata.ownership.find(o => o.guild_id === input.guild_id && o.channel_id === input.channel_id && o.message_id === input.message_id);
    if (!isAdmin(p) && owner?.owner_username !== p.username) fail("forbidden", 403);
    message.deleted = input.action === "message.delete";
  }
  const receipt = { request_id: input.request_id, actor: p.username, fingerprint, message_id: messageId };
  if (!await commit(db, p, view.revision, view.state, ownership, [...view.metadata.deleted_channels, ...deleted], origin, now,
    [...view.metadata.ownership, ...ownership], receipt)) return await prior() ?? null;
  return { ...await readView(db, p.company), ...(messageId ? { message_id: messageId } : {}) };
}
