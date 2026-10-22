export const MAX_STATE_BYTES = 1024 * 1024;
export const MAX_REQUEST_BYTES = MAX_STATE_BYTES + 64 * 1024;
export const MAX_REVISION = Number.MAX_SAFE_INTEGER - 1;
export const EMPTY_STATE = Object.freeze({ format: "jk-cofre-shared-v1", guilds: [] });

export class GatewayError extends Error {
  constructor(public readonly status: number, public readonly code: string) {
    super(code);
  }
}

type ObjectValue = Record<string, unknown>;
const encoder = new TextEncoder();

function invalid(): never { throw new GatewayError(400, "invalid_state"); }

function object(value: unknown, allowed: readonly string[], required: readonly string[]): ObjectValue {
  if (value === null || typeof value !== "object" || Array.isArray(value)) invalid();
  const item = value as ObjectValue;
  if (Object.keys(item).some(key => !allowed.includes(key)) || required.some(key => !Object.hasOwn(item, key))) invalid();
  return item;
}

function text(value: unknown, limit: number, minimum = 0): asserts value is string {
  if (typeof value !== "string" || value.length < minimum || value.length > limit) invalid();
}

function identifier(value: unknown): asserts value is string {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(value)) invalid();
}

function boolean(value: unknown): void { if (typeof value !== "boolean") invalid(); }

function url(value: unknown, required = false): void {
  text(value, 16384, required ? 1 : 0);
  if (!value) return;
  try {
    const parsed = new URL(value);
    if (!/^https?:$/.test(parsed.protocol) || !parsed.hostname || parsed.username || parsed.password
        || /[\u0000-\u0020\u007f]/.test(value)) invalid();
  } catch { invalid(); }
}

function array(value: unknown, limit: number): unknown[] {
  if (!Array.isArray(value) || value.length > limit) invalid();
  return value;
}

function link(value: unknown): void {
  const item = object(value, ["url", "text"], ["url"]);
  url(item.url, true);
  if (Object.hasOwn(item, "text")) text(item.text, 65536);
}

function attachment(value: unknown): void {
  const item = object(value, ["url", "filename", "content_type"], ["url", "filename"]);
  url(item.url, true);
  text(item.filename, 512, 1);
  if (item.content_type !== undefined && item.content_type !== null) text(item.content_type, 255);
}

function embed(value: unknown): void {
  const item = object(value, ["text", "links"], []);
  if (item.text !== undefined) text(item.text, MAX_STATE_BYTES);
  if (item.links !== undefined) array(item.links, 1000).forEach(link);
}

function editedFields(value: unknown, allowed: readonly string[]): void {
  const fields = array(value, allowed.length);
  if (fields.some(field => typeof field !== "string" || !allowed.includes(field))
      || new Set(fields).size !== fields.length) invalid();
}

function message(value: unknown): string {
  const item = object(value, ["id", "author", "timestamp", "content", "links", "attachments", "embeds", "source_url", "deleted", "edited_fields"], ["id", "deleted"]);
  identifier(item.id);
  boolean(item.deleted);
  if (item.author !== undefined) text(item.author, 512);
  if (item.timestamp !== undefined) text(item.timestamp, 128);
  if (item.content !== undefined) text(item.content, MAX_STATE_BYTES);
  if (item.source_url !== undefined) url(item.source_url);
  if (item.links !== undefined) array(item.links, 1000).forEach(link);
  if (item.attachments !== undefined) array(item.attachments, 1000).forEach(attachment);
  if (item.embeds !== undefined) array(item.embeds, 1000).forEach(embed);
  if (item.edited_fields !== undefined) editedFields(item.edited_fields, ["content", "author", "links", "embeds"]);
  return item.id;
}

function channel(value: unknown): string {
  const item = object(value, ["id", "name", "category", "url", "topic", "history_complete", "reached_start", "reached_end", "last_capture_at", "kind", "messages", "edited_fields"], ["id", "name", "messages"]);
  identifier(item.id);
  text(item.name, 512, 1);
  if (item.category !== undefined) text(item.category, 512);
  if (item.url !== undefined) url(item.url);
  if (item.topic !== undefined && item.topic !== null) text(item.topic, 65536);
  for (const key of ["history_complete", "reached_start", "reached_end"]) {
    if (item[key] !== undefined) boolean(item[key]);
  }
  if (item.last_capture_at !== undefined && item.last_capture_at !== null) text(item.last_capture_at, 128);
  if (item.kind !== undefined && item.kind !== "text" && item.kind !== "voice") invalid();
  if (item.edited_fields !== undefined) editedFields(item.edited_fields, ["name", "category", "topic"]);
  unique(array(item.messages, 100000), message);
  return item.id;
}

function unique(items: unknown[], validate: (value: unknown) => string): void {
  const ids = new Set<string>();
  for (const item of items) {
    const id = validate(item);
    if (ids.has(id)) invalid();
    ids.add(id);
  }
}

function guildEntry(value: unknown): string {
  const entry = object(value, ["guild", "exported_at", "channels"], ["guild", "exported_at", "channels"]);
  const guild = object(entry.guild, ["id", "name"], ["id", "name"]);
  identifier(guild.id);
  text(guild.name, 512, 1);
  text(entry.exported_at, 128);
  unique(array(entry.channels, 5000), channel);
  return guild.id;
}

export function serializeState(value: unknown): string {
  const state = object(value, ["format", "guilds"], ["format", "guilds"]);
  if (state.format !== "jk-cofre-shared-v1") invalid();
  unique(array(state.guilds, 100), guildEntry);
  const serialized = JSON.stringify(state);
  if (encoder.encode(serialized).byteLength > MAX_STATE_BYTES) throw new GatewayError(413, "state_too_large");
  return serialized;
}

export function validateWrite(value: unknown): { expectedRevision: number; serializedState: string } {
  const body = object(value, ["expected_revision", "state"], ["expected_revision", "state"]);
  if (typeof body.expected_revision !== "number" || !Number.isSafeInteger(body.expected_revision)
      || body.expected_revision < 0 || body.expected_revision > MAX_REVISION) invalid();
  return { expectedRevision: body.expected_revision, serializedState: serializeState(body.state) };
}

export async function readBoundedJson(request: Request): Promise<unknown> {
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
    throw new GatewayError(415, "json_required");
  }
  const declared = request.headers.get("content-length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_REQUEST_BYTES)) {
    throw new GatewayError(413, "request_too_large");
  }
  if (!request.body) invalid();
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      length += next.value.byteLength;
      if (length > MAX_REQUEST_BYTES) {
        await reader.cancel();
        throw new GatewayError(413, "request_too_large");
      }
      chunks.push(next.value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(length);
  let position = 0;
  for (const chunk of chunks) { bytes.set(chunk, position); position += chunk.byteLength; }
  try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); }
  catch { return invalid(); }
}
