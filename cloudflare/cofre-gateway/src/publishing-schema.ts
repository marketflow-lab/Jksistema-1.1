import { GatewayError, MAX_REVISION } from "./schema";

export const MAX_ASSET_BYTES = 10 * 1024 * 1024;
export const NATIVE_ID = /^jk_msg_[0-9a-f]{32}$/;
export const ASSET_ID = /^[0-9a-f]{64}$/;
export type Value = Record<string, any>;
export interface Ownership { guild_id: string; channel_id: string; message_id: string; owner_username: string }
export interface DeletedChannel { guild_id: string; channel_id: string }
export interface Metadata { version: 1; ownership: Ownership[]; deleted_channels: DeletedChannel[] }
export interface Mutation {
  request_id: string; expected_revision: number;
  action: "message.create" | "message.delete" | "message.restore" | "channel.delete";
  guild_id: string; channel_id: string; message_id?: string;
  content?: string; links?: Value[]; asset_ids?: string[];
}
export interface AssetRequest { request_id: string; guild_id: string; channel_id: string; filename: string; size: number; sha256: string }
export function fail(code = "invalid_request", status = 400): never { throw new GatewayError(status, code); }
export function closed(value: unknown, keys: string[], required = keys): Value {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail();
  const result = value as Value;
  if (Object.keys(result).some(key => !keys.includes(key)) || required.some(key => !Object.hasOwn(result, key))) fail();
  return result;
}
export function id(value: unknown): asserts value is string {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(value)) fail();
}
function uuid(value: unknown): void {
  if (typeof value !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value)) fail();
}
export function validateMutation(value: unknown): Mutation {
  const v = closed(value, ["request_id", "expected_revision", "action", "guild_id", "channel_id", "message_id", "content", "links", "asset_ids"],
    ["request_id", "expected_revision", "action", "guild_id", "channel_id"]);
  uuid(v.request_id); id(v.guild_id); id(v.channel_id);
  if (!Number.isSafeInteger(v.expected_revision) || v.expected_revision < 0 || v.expected_revision > MAX_REVISION) fail();
  if (!["message.create", "message.delete", "message.restore", "channel.delete"].includes(v.action)) fail();
  if (v.action === "message.create") {
    if (Object.hasOwn(v, "message_id")) fail();
    if (v.content !== undefined && (typeof v.content !== "string" || v.content.length > 32000)) fail();
    if (v.links !== undefined) {
      if (!Array.isArray(v.links) || v.links.length > 100) fail();
      for (const raw of v.links) {
        const link = closed(raw, ["url", "text"], ["url"]);
        if (typeof link.url !== "string" || link.url.length > 16384 || /[\x00-\x20\x7f]/.test(link.url)) fail();
        try { const u = new URL(link.url); if (!/^https?:$/.test(u.protocol) || u.username || u.password) fail(); } catch { fail(); }
        if (link.text !== undefined && (typeof link.text !== "string" || link.text.length > 65536)) fail();
      }
    }
    if (v.asset_ids !== undefined && (!Array.isArray(v.asset_ids) || v.asset_ids.length > 5
        || new Set(v.asset_ids).size !== v.asset_ids.length || v.asset_ids.some((a: unknown) => typeof a !== "string" || !ASSET_ID.test(a)))) fail();
    if (!(v.content?.trim() || v.links?.length || v.asset_ids?.length)) fail("empty_message");
  } else {
    if (["content", "links", "asset_ids"].some(key => Object.hasOwn(v, key))) fail();
    if (v.action === "channel.delete") { if (Object.hasOwn(v, "message_id")) fail(); }
    else id(v.message_id);
  }
  return v as Mutation;
}
export function validateAttestation(value: unknown): Metadata {
  const v = closed(value, ["version", "ownership", "deleted_channels"], ["version", "ownership"]);
  if (v.version !== 1 || !Array.isArray(v.ownership) || v.ownership.length > 100000
      || (v.deleted_channels !== undefined && (!Array.isArray(v.deleted_channels) || v.deleted_channels.length > 500000))) fail();
  const seen = new Set<string>();
  const ownership = v.ownership.map((raw: unknown) => {
    const row = closed(raw, ["guild_id", "channel_id", "message_id", "owner_username"]);
    id(row.guild_id); id(row.channel_id); id(row.message_id);
    if (!NATIVE_ID.test(row.message_id) || typeof row.owner_username !== "string" || row.owner_username.length < 1
        || row.owner_username.length > 180 || /[\x00-\x1f\x7f]/.test(row.owner_username)) fail();
    const key = tuple(row.guild_id, row.channel_id, row.message_id);
    if (seen.has(key)) fail(); seen.add(key);
    return row as Ownership;
  });
  const channels = new Set<string>();
  const deleted_channels = (v.deleted_channels ?? []).map((raw: unknown) => {
    const row = closed(raw, ["guild_id", "channel_id"]);
    id(row.guild_id); id(row.channel_id);
    const key = tuple(row.guild_id, row.channel_id);
    if (channels.has(key)) fail(); channels.add(key);
    return row as DeletedChannel;
  });
  return { version: 1, ownership, deleted_channels };
}
export function validateAssetRequest(value: unknown): AssetRequest {
  const v = closed(value, ["request_id", "guild_id", "channel_id", "filename", "size", "sha256"]);
  uuid(v.request_id); id(v.guild_id); id(v.channel_id);
  if (typeof v.filename !== "string" || !v.filename || v.filename.length > 240
      || /[\x00-\x1f\x7f/\\]/.test(v.filename) || v.filename === "." || v.filename === ".." || v.filename !== v.filename.trim()) fail();
  try { encodeURIComponent(v.filename); } catch { fail(); }
  if (!Number.isSafeInteger(v.size) || v.size < 1 || v.size > MAX_ASSET_BYTES) fail("invalid_asset_size");
  if (typeof v.sha256 !== "string" || !ASSET_ID.test(v.sha256)) fail();
  return v as AssetRequest;
}
export function tuple(...ids: string[]): string { return JSON.stringify(ids); }
export function randomHex(bytes = 32): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(bytes)), b => b.toString(16).padStart(2, "0")).join("");
}
export async function sha256(value: string | Uint8Array<ArrayBuffer>): Promise<string> {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), b => b.toString(16).padStart(2, "0")).join("");
}
