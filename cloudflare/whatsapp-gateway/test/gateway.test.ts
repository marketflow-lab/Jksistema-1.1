import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";

import worker from "../src/index";

function context(): ExecutionContext {
  return {
    waitUntil: () => undefined,
    passThroughOnException: () => undefined,
    props: {},
  } as unknown as ExecutionContext;
}

async function signature(secret: string, body: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const result = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  return `sha256=${[...new Uint8Array(result)].map((item) => item.toString(16).padStart(2, "0")).join("")}`;
}

const TEST_PNG = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
  0x00, 0x00, 0x00, 0x0d,
]);
const TEST_JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46]);
const TEST_PDF = new TextEncoder().encode("%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF");
const TEST_XLSX = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x14, 0x00, 0x06, 0x00]);

function copiedArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  const buffer = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(buffer).set(bytes);
  return buffer;
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", copiedArrayBuffer(bytes));
  return [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
}

async function imageForm(fields: Record<string, string>, options: { bytes?: Uint8Array; mime?: string; name?: string } = {}): Promise<FormData> {
  const bytes = options.bytes || TEST_PNG;
  const mime = options.mime || "image/png";
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) form.set(key, value);
  if (!form.has("sha256")) form.set("sha256", await sha256(bytes));
  form.set("file", new File([copiedArrayBuffer(bytes)], options.name || "chart.png", { type: mime }));
  return form;
}

function outboundImageEnvironment(options: {
  machineId?: string;
  lastInboundAt?: number;
  policyUntil?: string;
  reserve?: boolean;
  existing?: Record<string, unknown> | null;
  binding?: boolean;
  metaUploadStatus?: number;
  metaSendStatus?: number;
  messageImageCount?: number;
  interactiveReservation?: boolean;
  interactiveContext?: Record<string, unknown> | null;
} = {}) {
  const now = Math.floor(Date.now() / 1000);
  const sqlCalls: Array<{ sql: string; values: unknown[]; operation: "first" | "run" }> = [];
  const graphRequests: Array<{ url: string; body: BodyInit | null | undefined }> = [];
  const binding = options.binding === false ? null : {
    subject_id: "subject",
    wa_id: "553798379212",
    phone_number: "+55 37 9837-9212",
    client_id: "client-a",
    username: "operator-a",
    machine_id: options.machineId || "machine",
    active: 1,
    last_inbound_at: options.lastInboundAt ?? now,
  };
  const message = binding ? {
    subject_id: "subject",
    wa_id: "553798379212",
    machine_id: binding.machine_id,
  } : null;
  const db = {
    prepare(sql: string) {
      return {
        bind(...values: unknown[]) {
          return {
            async first() {
              sqlCalls.push({ sql, values, operation: "first" });
              if (sql.includes("SELECT * FROM bindings WHERE subject_id=")) return binding;
              if (sql.includes("SELECT machine_id FROM bindings WHERE subject_id=")) return binding;
              if (sql.includes("FROM inbox i JOIN bindings")) return message;
              if (sql.includes("FROM outbound_media WHERE fingerprint=")) return options.existing ?? null;
              if (sql.includes("COUNT(*) AS total FROM outbound_media WHERE inbound_message_id=")) {
                return { total: options.messageImageCount ?? 0 };
              }
              if (sql.includes("SELECT meta_message_id FROM outbound_quote_context WHERE fingerprint=")) {
                return options.interactiveContext ?? null;
              }
              return null;
            },
            async run() {
              sqlCalls.push({ sql, values, operation: "run" });
              if (sql.includes("INSERT OR IGNORE INTO proactive_events")) {
                return { meta: { changes: options.interactiveReservation === false ? 0 : 1 } };
              }
              if (sql.includes("INSERT OR IGNORE INTO outbound_media")) {
                return { meta: { changes: options.reserve === false ? 0 : 1 } };
              }
              return { meta: { changes: 1 } };
            },
            async all() {
              return { results: [] };
            },
          };
        },
      };
    },
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      return Promise.all(statements.map((statement) => statement.run()));
    },
  };
  const env = {
    DB: db,
    BRIDGE_TOKEN: "bridge-secret",
    META_GRAPH_API_VERSION: "v25.0",
    META_SYSTEM_USER_TOKEN: "meta-token",
    META_PHONE_NUMBER_ID: "phone-id",
    ZERO_COST_POLICY_VALID_UNTIL: options.policyUntil || "2026-09-30T23:59:59Z",
    FREE_WINDOW_SECONDS: "84600",
    MEDIA_OUTBOUND_BYTES_MONTH_LIMIT: String(900 * 1024 * 1024),
    MEDIA_OUTBOUND_UPLOADS_MONTH_LIMIT: "10000",
  } as any;
  vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    graphRequests.push({ url, body: init?.body });
    if (url.endsWith("/media")) {
      const status = options.metaUploadStatus || 200;
      return new Response(JSON.stringify(status < 400 ? { id: "media-id" } : { error: { message: "upload failed" } }), { status });
    }
    const status = options.metaSendStatus || 200;
    return new Response(JSON.stringify(status < 400 ? { messages: [{ id: "message-id" }] } : { error: { message: "send failed" } }), { status });
  });
  return { env, sqlCalls, graphRequests };
}

function typingEnvironment(options: {
  status?: string;
  machineId?: string;
  typingLastAt?: number;
  lastInboundAt?: number;
  reserveTyping?: boolean;
  policyUntil?: string;
  graphStatus?: number;
} = {}) {
  const now = Math.floor(Date.now() / 1000);
  const graphRequests: Array<{ url: string; body: any }> = [];
  const sqlCalls: Array<{ sql: string; values: unknown[]; operation: "first" | "run" }> = [];
  const row = {
    message_id: "wamid.typing",
    subject_id: "subject",
    status: options.status || "leased",
    typing_last_at: options.typingLastAt || 0,
    machine_id: options.machineId || "machine",
    last_inbound_at: options.lastInboundAt ?? now,
  };
  const db = {
    prepare(sql: string) {
      return {
        bind(...values: unknown[]) {
          return {
            async first() {
              sqlCalls.push({ sql, values, operation: "first" });
              if (sql.includes("FROM inbox i JOIN bindings")) return row;
              return null;
            },
            async run() {
              sqlCalls.push({ sql, values, operation: "run" });
              if (sql.includes("INSERT INTO usage_counters")) {
                return { meta: { changes: options.reserveTyping === false ? 0 : 1 } };
              }
              return { meta: { changes: 1 } };
            },
          };
        },
      };
    },
  };
  const env = {
    DB: db,
    BRIDGE_TOKEN: "bridge-secret",
    META_GRAPH_API_VERSION: "v25.0",
    META_SYSTEM_USER_TOKEN: "meta-token",
    META_PHONE_NUMBER_ID: "phone-id",
    ZERO_COST_POLICY_VALID_UNTIL: options.policyUntil || "2026-09-30T23:59:59Z",
    FREE_WINDOW_SECONDS: "84600",
    TYPING_PULSES_DAY_LIMIT: "10000",
  } as any;
  vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
    graphRequests.push({
      url: String(input),
      body: JSON.parse(String(init?.body || "{}")),
    });
    const status = options.graphStatus || 200;
    return new Response(JSON.stringify(status < 400 ? { success: true } : { error: { message: "failed" } }), { status });
  });
  return { env, graphRequests, sqlCalls };
}

function registrationEnvironment(existing: Record<string, unknown> | null = null, activeBindings = 0) {
  const sqlCalls: Array<{ sql: string; values: unknown[]; operation: "first" | "run" }> = [];
  const db = {
    prepare(sql: string) {
      return {
        bind(...values: unknown[]) {
          return {
            async first() {
              sqlCalls.push({ sql, values, operation: "first" });
              if (sql.includes("FROM bindings WHERE subject_id=? OR wa_id=? OR phone_number=?")) return existing;
              if (sql.includes("COUNT(*) AS total FROM bindings")) return { total: activeBindings };
              return null;
            },
            async run() {
              sqlCalls.push({ sql, values, operation: "run" });
              return { meta: { changes: 1 } };
            },
          };
        },
      };
    },
  };
  return { env: { DB: db, BRIDGE_TOKEN: "bridge-secret" } as any, sqlCalls };
}

function welcomeEnvironment(lastInboundAt = Math.floor(Date.now() / 1000), bindingAvailable = true, unpairedInboundAt = 0) {
  const sqlCalls: Array<{ sql: string; values: unknown[]; operation: "first" | "run" }> = [];
  const graphRequests: Array<{ url: string; body: any }> = [];
  let outbox: Record<string, unknown> | null = null;
  const binding = {
    subject_id: "5537999993818",
    wa_id: "5537999993818",
    phone_number: "5537999993818",
    machine_id: "machine-1",
    active: 1,
    last_inbound_at: lastInboundAt,
  };
  const db = {
    prepare(sql: string) {
      return {
        bind(...values: unknown[]) {
          return {
            async first() {
              sqlCalls.push({ sql, values, operation: "first" });
              if (sql.includes("SELECT * FROM bindings WHERE")) return bindingAvailable ? binding : null;
              if (sql.includes("event_type='unpaired_phone_message'")) return unpairedInboundAt ? { created_at: unpairedInboundAt } : null;
              if (sql.includes("SELECT * FROM outbox WHERE id=?")) return outbox;
              if (sql.includes("SELECT status,error,meta_message_id FROM outbox")) return outbox;
              return null;
            },
            async run() {
              sqlCalls.push({ sql, values, operation: "run" });
              if (sql.includes("INSERT INTO outbox")) {
                outbox = {
                  id: values[0],
                  subject_id: values[1],
                  recipient: values[2],
                  message_type: values[3],
                  text_body: values[4],
                  template_name: values[5],
                  template_params_json: values[6],
                  status: values[7],
                  attempts: 0,
                };
              } else if (outbox && sql.includes("UPDATE outbox SET message_type='adhoc_text'")) {
                outbox.message_type = "adhoc_text";
              } else if (outbox && sql.includes("UPDATE outbox SET status='sent'")) {
                outbox.status = "sent";
                outbox.meta_message_id = values[2];
                outbox.error = null;
              } else if (outbox && sql.includes("UPDATE outbox SET status=?")) {
                outbox.status = values[0];
                outbox.error = values[2];
              }
              return { meta: { changes: 1 } };
            },
          };
        },
      };
    },
  };
  const env = {
    DB: db,
    BRIDGE_TOKEN: "bridge-secret",
    ZERO_COST_POLICY_VALID_UNTIL: "2026-09-30T23:59:59Z",
    FREE_WINDOW_SECONDS: "84600",
    META_GRAPH_API_VERSION: "v25.0",
    META_SYSTEM_USER_TOKEN: "meta-token",
    META_PHONE_NUMBER_ID: "phone-id",
  } as any;
  vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
    graphRequests.push({ url: String(input), body: JSON.parse(String(init?.body || "{}")) });
    return new Response(JSON.stringify({ messages: [{ id: "wamid.welcome" }] }), { status: 200 });
  });
  return { env, sqlCalls, graphRequests };
}

function unauthorizedEnvironment() {
  const sqlCalls: Array<{ sql: string; values: unknown[]; operation: "first" | "run" | "all" }> = [];
  const graphRequests: Array<{ url: string; body: any }> = [];
  const auditTimes = new Map<string, number>();
  let outbox: Record<string, unknown> | null = null;
  const db = {
    prepare(sql: string) {
      return {
        bind(...values: unknown[]) {
          return {
            async first() {
              sqlCalls.push({ sql, values, operation: "first" });
              if (sql.includes("SELECT message_id FROM inbox")) return null;
              if (sql.includes("SELECT subject_id FROM bindings")) return null;
              if (sql.includes("event_type='unauthorized_access_notice'")) return { total: 0 };
              if (sql.includes("SELECT * FROM bindings WHERE")) return null;
              if (sql.includes("event_type='unpaired_phone_message'")) {
                return { created_at: auditTimes.get("unpaired_phone_message") || Math.floor(Date.now() / 1000) };
              }
              if (sql.includes("SELECT * FROM outbox WHERE id=?")) return outbox;
              if (sql.includes("SELECT status,error,meta_message_id FROM outbox")) return outbox;
              return null;
            },
            async run() {
              sqlCalls.push({ sql, values, operation: "run" });
              if (sql.includes("INSERT INTO audit_events")) {
                auditTimes.set(String(values[1] || ""), Number(values[4] || 0));
              } else if (sql.includes("INSERT INTO outbox")) {
                outbox = {
                  id: values[0], subject_id: values[1], recipient: values[2], message_type: values[3],
                  text_body: values[4], template_name: values[5], template_params_json: values[6], status: values[7], attempts: 0,
                };
              } else if (outbox && sql.includes("UPDATE outbox SET message_type='adhoc_text'")) {
                outbox.message_type = "adhoc_text";
              } else if (outbox && sql.includes("UPDATE outbox SET status='sent'")) {
                outbox.status = "sent";
                outbox.meta_message_id = values[2];
                outbox.error = null;
              } else if (outbox && sql.includes("UPDATE outbox SET status=?")) {
                outbox.status = values[0];
                outbox.error = values[2];
              }
              return { meta: { changes: 1 } };
            },
            async all() {
              sqlCalls.push({ sql, values, operation: "all" });
              return { results: [] };
            },
          };
        },
      };
    },
  };
  const env = {
    DB: db,
    BRIDGE_TOKEN: "bridge-secret",
    META_APP_SECRET: "app-secret",
    ZERO_COST_POLICY_VALID_UNTIL: "2026-09-30T23:59:59Z",
    FREE_WINDOW_SECONDS: "84600",
    META_GRAPH_API_VERSION: "v25.0",
    META_SYSTEM_USER_TOKEN: "meta-token",
    META_PHONE_NUMBER_ID: "phone-id",
  } as any;
  vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
    graphRequests.push({ url: String(input), body: JSON.parse(String(init?.body || "{}")) });
    return new Response(JSON.stringify({ messages: [{ id: "wamid.unauthorized" }] }), { status: 200 });
  });
  return { env, sqlCalls, graphRequests };
}

function directRegisteredInboundEnvironment(registeredPhone: string) {
  const sqlCalls: Array<{ sql: string; values: unknown[]; operation: "first" | "run" }> = [];
  const boundStatements: Array<{ run(): Promise<{ meta: { changes: number } }> }> = [];
  const db = {
    prepare(sql: string) {
      return {
        bind(...values: unknown[]) {
          const statement = {
            async first() {
              sqlCalls.push({ sql, values, operation: "first" as const });
              if (sql.includes("SELECT message_id FROM inbox")) return null;
              if (sql.includes("FROM bindings WHERE active=1 AND (subject_id=? OR wa_id=? OR phone_number=?)")) {
                return values.includes(registeredPhone) ? { subject_id: registeredPhone } : null;
              }
              if (sql.includes("COUNT(*) AS total FROM inbox")) return { total: 0 };
              return null;
            },
            async run() {
              sqlCalls.push({ sql, values, operation: "run" as const });
              return { meta: { changes: 1 } };
            },
            async all() {
              return { results: [] };
            },
          };
          boundStatements.push(statement);
          return statement;
        },
      };
    },
    async batch(statements: Array<{ run(): Promise<{ meta: { changes: number } }> }>) {
      return Promise.all(statements.map((statement) => statement.run()));
    },
  };
  return {
    env: {
      DB: db,
      BRIDGE_TOKEN: "bridge-secret",
      META_APP_SECRET: "app-secret",
    } as any,
    sqlCalls,
    boundStatements,
  };
}

function quotedInboundEnvironment() {
  const now = Math.floor(Date.now() / 1000);
  const binding = {
    subject_id: "subject-a",
    wa_id: "5537999990000",
    phone_number: "5537999990000",
    client_id: "client-a",
    username: "operator-a",
    machine_id: "machine-a",
    active: 1,
    last_inbound_at: now,
  };
  const inbox = new Map<string, Record<string, any>>();
  const quotedOutbox = new Map<string, Record<string, any>>([
    ["wamid.quote.same", { subject_id: "subject-a", text_body: "SKU 001 na JK Peças: 5 unidades no Full." }],
    ["wamid.quote.other", { subject_id: "subject-b", text_body: "Dado privado de outro tenant." }],
  ]);
  const quotedInteractive = new Map<string, Record<string, any>>([
    ["wamid.card.same", {
      subject_id: "subject-a",
      client_id: "client-a",
      username: "operator-a",
      text_body: "Black Jhon\nResposta sugerida\nEscolher acao\nAprovar e enviar - Envia esta resposta",
    }],
    ["wamid.card.other-tenant", {
      subject_id: "subject-a",
      client_id: "client-b",
      username: "operator-b",
      text_body: "Cartao privado de outro tenant.",
    }],
  ]);
  const db = {
    prepare(sql: string) {
      return {
        bind(...values: unknown[]) {
          return {
            async first() {
              if (sql.includes("SELECT message_id FROM inbox WHERE message_id=")) {
                const item = inbox.get(String(values[0] || ""));
                return item ? { message_id: item.message_id } : null;
              }
              if (sql.includes("FROM bindings WHERE active=1 AND (subject_id=? OR wa_id=? OR phone_number=?)")) {
                return values.some((value) => String(value || "") === binding.wa_id) ? binding : null;
              }
              if (sql.includes("SELECT text_body FROM inbox WHERE message_id=? AND subject_id=?")) {
                const item = inbox.get(String(values[0] || ""));
                return item?.subject_id === String(values[1] || "") ? { text_body: item.text_body } : null;
              }
              if (sql.includes("SELECT text_body FROM outbound_quote_context WHERE meta_message_id=?")) {
                const item = quotedInteractive.get(String(values[0] || ""));
                return item?.subject_id === String(values[1] || "")
                  && item?.client_id === String(values[2] || "")
                  && item?.username === String(values[3] || "")
                  ? { text_body: item.text_body }
                  : null;
              }
              if (sql.includes("SELECT text_body FROM outbox WHERE meta_message_id=? AND subject_id=?")) {
                const item = quotedOutbox.get(String(values[0] || ""));
                return item?.subject_id === String(values[1] || "") ? { text_body: item.text_body } : null;
              }
              if (sql.includes("COUNT(*) AS total FROM inbox")) return { total: 0 };
              if (sql.includes("SELECT last_seen_at FROM bridge_heartbeats")) return { last_seen_at: now };
              return null;
            },
            async run() {
              if (sql.includes("INSERT INTO inbox(")) {
                inbox.set(String(values[0] || ""), {
                  message_id: values[0],
                  subject_id: values[1],
                  wa_id: values[2],
                  phone_number_id: values[3],
                  message_type: values[4],
                  text_body: values[5],
                  media_id: values[6],
                  media_mime: values[7],
                  received_at: values[8],
                  status: values[9],
                  quoted_message_id: values[10],
                  quoted_text: values[11],
                  attempts: 0,
                });
              } else if (sql.includes("UPDATE inbox SET status='leased'")) {
                const item = inbox.get(String(values[2] || ""));
                if (!item || item.status !== "queued") return { meta: { changes: 0 } };
                Object.assign(item, { status: "leased", lease_owner: values[0], lease_until: values[1], attempts: item.attempts + 1 });
              }
              return { meta: { changes: 1 } };
            },
            async all() {
              if (sql.includes("SELECT i.*,COALESCE(NULLIF(i.wa_id,''),b.wa_id) AS wa_id")) {
                const machineId = String(values[0] || "");
                const limit = Number(values[1] || 5);
                return {
                  results: [...inbox.values()]
                    .filter((item) => item.status === "queued" && machineId === binding.machine_id)
                    .slice(0, limit)
                    .map((item) => ({ ...item, client_id: "client-a", username: "operator-a", machine_id: binding.machine_id })),
                };
              }
              return { results: [] };
            },
          };
        },
      };
    },
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      return Promise.all(statements.map((statement) => statement.run()));
    },
  };
  return {
    env: {
      DB: db,
      BRIDGE_TOKEN: "bridge-secret",
      META_APP_SECRET: "app-secret",
      ZERO_COST_POLICY_VALID_UNTIL: "2026-09-30T23:59:59Z",
      FREE_WINDOW_SECONDS: "84600",
    } as any,
    inbox,
  };
}

function inboundMediaRetryEnvironment(
  outcomes: Array<number | "timeout" | "success">,
  options: {
    schemaMissing?: boolean;
    metadataUrl?: string;
    metadataSize?: number;
    redirectLocation?: string;
    downloadBody?: BodyInit;
  } = {},
) {
  let inbox: Record<string, any> | null = null;
  let metadataAttempts = 0;
  let mediaDownloads = 0;
  const mediaPuts: Array<{ key: string; options: any }> = [];
  const mediaDeletes: string[] = [];
  const boundStatements: Array<{ run(): Promise<{ meta: { changes: number } }> }> = [];

  function changes(value: boolean) {
    return { meta: { changes: value ? 1 : 0 } };
  }

  const db = {
    prepare(sql: string) {
      const makeStatement = (values: any[]) => {
        const statement = {
            async first() {
              if (sql.includes("SELECT message_id FROM inbox WHERE message_id=")) return inbox ? { message_id: inbox.message_id } : null;
              if (sql.includes("SELECT subject_id FROM bindings WHERE active=1 AND machine_id=")) {
                return values[0] === "machine" ? { subject_id: "subject" } : null;
              }
              if (sql.includes("FROM bindings WHERE active=1 AND (subject_id=? OR wa_id=? OR phone_number=?)")) {
                return { subject_id: "subject", machine_id: "machine" };
              }
              if (sql.includes("COUNT(*) AS total FROM inbox")) return { total: 0 };
              if (sql.includes("SELECT counter_value FROM usage_counters")) return { counter_value: 0 };
              if (sql.includes("SELECT message_id,message_type,media_id,media_mime,media_attempts,media_expires_at")) {
                return inbox?.media_state === "fetching" ? { ...inbox } : null;
              }
              if (sql.includes("SUM(CASE WHEN i.media_state='retry_wait'")) {
                return {
                  waiting_retry: inbox?.media_state === "retry_wait" ? 1 : 0,
                  fetching: inbox?.media_state === "fetching" ? 1 : 0,
                  stored: inbox?.media_state === "stored" ? 1 : 0,
                  failed: inbox?.media_state === "failed" ? 1 : 0,
                  expired: inbox?.media_state === "expired" ? 1 : 0,
                };
              }
              if (sql.includes("(SELECT COUNT(*) FROM inbox i JOIN bindings")) return { inbox_pending: inbox && !inbox.completed_at ? 1 : 0 };
              if (sql.includes("COUNT(*) AS uploads,COALESCE(SUM(om.byte_size),0)")) return { uploads: 0, bytes: 0 };
              if (sql.includes("COALESCE(SUM(CASE WHEN i.media_object_key")) return { active_bytes: 0, uploads: 0 };
              return null;
            },
            async run() {
              const now = Math.floor(Date.now() / 1000);
              if (sql.includes("INSERT INTO inbox(")) {
                inbox = {
                  message_id: values[0], subject_id: values[1], wa_id: values[2], phone_number_id: values[3],
                  message_type: values[4], text_body: values[5], media_id: values[6], media_mime: values[7],
                  received_at: values[8], status: values[9], media_size: 0, media_object_key: null,
                  media_state: "none", media_attempts: 0, media_next_attempt_at: null,
                  media_last_attempt_at: null, media_lease_until: null, media_error_class: null,
                  media_expires_at: null, completed_at: null,
                };
                return changes(true);
              }
              if (!inbox) return changes(true);
              if (options.schemaMissing && sql.includes("media_state")) throw new Error("no such column: media_state");
              if (sql.includes("SET media_state='pending',media_attempts=0")) {
                if (inbox.status !== "media_fetching" || inbox.media_state !== "none") return changes(false);
                Object.assign(inbox, {
                  media_state: "pending", media_attempts: 0, media_next_attempt_at: values[0], media_expires_at: values[1],
                  media_last_attempt_at: null, media_lease_until: null, media_error_class: null, error: null,
                });
                return changes(true);
              }
              if (sql.includes("status='queued',error=NULL WHERE message_id=? AND status='media_fetching'")) {
                if (inbox.status !== "media_fetching" || inbox.media_object_key) return changes(false);
                Object.assign(inbox, {
                  media_mime: values[0], media_size: values[1], media_object_key: values[2], media_filename: values[3],
                  status: "queued", error: null,
                });
                return changes(true);
              }
              if (sql.includes("SET media_state='fetching',media_attempts=media_attempts+1")) {
                const eligible = ["pending", "retry_wait"].includes(inbox.media_state)
                  && Number(inbox.media_next_attempt_at || 0) <= Number(values[3])
                  && Number(inbox.media_expires_at || 0) > Number(values[4])
                  && Number(inbox.media_attempts || 0) < Number(values[5]);
                if (!eligible) return changes(false);
                Object.assign(inbox, {
                  media_state: "fetching", media_attempts: Number(inbox.media_attempts || 0) + 1,
                  media_last_attempt_at: values[0], media_lease_until: values[1], media_next_attempt_at: null,
                });
                return changes(true);
              }
              if (sql.includes("status='queued',media_state='stored'")) {
                if (inbox.media_state !== "fetching" || inbox.media_attempts !== values[5] || inbox.media_object_key) return changes(false);
                Object.assign(inbox, {
                  media_mime: values[0], media_size: values[1], media_object_key: values[2], media_filename: values[3],
                  status: "queued", media_state: "stored", media_next_attempt_at: null, media_lease_until: null,
                  media_error_class: null, error: null, completed_at: null,
                });
                return changes(true);
              }
              if (sql.includes("status='media_retry',media_state='retry_wait',media_next_attempt_at=?")) {
                if (inbox.media_state !== "fetching" || inbox.media_attempts !== values[4]) return changes(false);
                Object.assign(inbox, {
                  status: "media_retry", media_state: "retry_wait", media_next_attempt_at: values[0],
                  media_lease_until: null, media_error_class: values[1], error: values[2],
                });
                return changes(true);
              }
              if (sql.includes("media_error_class='media_retry_exhausted'")) return changes(false);
              if (sql.includes("media_error_class='media_fetch_lease_expired'")) return changes(false);
              if (sql.includes("SET status='failed',media_state='expired',media_error_class='media_retention_expired'")) {
                if (inbox.media_state !== "stored" || inbox.media_object_key !== values[2]) return changes(false);
                Object.assign(inbox, {
                  status: "failed", media_state: "expired", media_error_class: "media_retention_expired",
                  error: "media_retention_expired", completed_at: values[0], lease_owner: null, lease_until: null,
                });
                return changes(true);
              }
              if (sql.includes("status='failed',media_state='failed',media_next_attempt_at=NULL")) {
                Object.assign(inbox, {
                  status: "failed", media_state: "failed", media_next_attempt_at: null, media_lease_until: null,
                  media_error_class: values[0], error: values[1], completed_at: values[2],
                });
                return changes(true);
              }
              if (sql.includes("status='failed',media_state='expired'")) {
                const expired = !inbox.media_object_key && Number(inbox.media_expires_at || inbox.received_at + values[1]) <= Number(values[2]);
                if (!expired) return changes(false);
                Object.assign(inbox, {
                  status: "failed", media_state: "expired", media_next_attempt_at: null, media_lease_until: null,
                  media_error_class: "media_retention_expired", error: "media_retention_expired", completed_at: values[0],
                });
                return changes(true);
              }
              if (sql.includes("SET media_state='pending',media_next_attempt_at=COALESCE")) return changes(false);
              if (sql.includes("SET media_object_key=NULL,media_state=?")) {
                if (inbox.media_object_key !== values[2]) return changes(false);
                Object.assign(inbox, { media_object_key: null, media_state: values[0], media_expires_at: null });
                return changes(true);
              }
              if (sql.includes("UPDATE bindings SET last_inbound_at")) return changes(true);
              if (sql.includes("INSERT INTO usage_counters")) return changes(true);
              if (sql.includes("INSERT INTO audit_events")) return changes(true);
              return changes(true);
            },
            async all() {
              if (sql.includes("SELECT message_id FROM inbox WHERE media_state IN ('pending','retry_wait')")) {
                const due = inbox && ["pending", "retry_wait"].includes(inbox.media_state)
                  && Number(inbox.media_next_attempt_at || 0) <= Number(values[0])
                  && Number(inbox.media_expires_at || 0) > Number(values[1])
                  && Number(inbox.media_attempts || 0) < Number(values[2]);
                return { results: due ? [{ message_id: inbox!.message_id }] : [] };
              }
              if (sql.includes("SELECT i.media_error_class AS error_class")) {
                return { results: inbox?.media_error_class ? [{ error_class: inbox.media_error_class, total: 1 }] : [] };
              }
              if (sql.includes("media_object_key IS NOT NULL") && sql.includes("status IN ('completed','failed'")) {
                return { results: inbox?.media_object_key && ["completed", "failed", "awaiting_approval", "dead_letter", "unsupported"].includes(inbox.status) ? [{ ...inbox }] : [] };
              }
              if (sql.includes("media_state='stored'") && sql.includes("COALESCE(media_expires_at")) {
                return { results: inbox?.media_object_key && inbox.media_state === "stored" && Number(inbox.media_expires_at) <= Number(values[1]) ? [{ ...inbox }] : [] };
              }
              return { results: [] };
            },
        };
        boundStatements.push(statement);
        return statement;
      };
      return {
        bind(...values: any[]) {
          return makeStatement(values);
        },
        first() { return makeStatement([]).first(); },
        run() { return makeStatement([]).run(); },
        all() { return makeStatement([]).all(); },
      };
    },
    async batch(statements: Array<{ run(): Promise<{ meta: { changes: number } }> }>) {
      return Promise.all(statements.map((statement) => statement.run()));
    },
  };

  const env = {
    DB: db,
    MEDIA: {
      async put(key: string, _body: ArrayBuffer, options: any) { mediaPuts.push({ key, options }); },
      async delete(key: string) { mediaDeletes.push(key); },
    },
    BRIDGE_TOKEN: "bridge-secret",
    META_APP_SECRET: "app-secret",
    META_GRAPH_API_VERSION: "v25.0",
    META_SYSTEM_USER_TOKEN: "meta-token",
  } as any;

  vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("graph.facebook.com") && url.endsWith("/media-1")) {
      const outcome = outcomes[metadataAttempts++] ?? "success";
      if (outcome === "timeout") throw new DOMException("timed out", "TimeoutError");
      if (typeof outcome === "number") return new Response(JSON.stringify({ error: { code: outcome } }), { status: outcome });
      return new Response(JSON.stringify({
        url: options.metadataUrl || "https://lookaside.fbsbx.com/whatsapp_business/attachments/audio",
        mime_type: "audio/ogg; codecs=opus",
        file_size: options.metadataSize ?? 4,
      }), { status: 200 });
    }
    if (url === "https://lookaside.fbsbx.com/whatsapp_business/attachments/audio") {
      mediaDownloads += 1;
      if (options.redirectLocation) {
        return new Response(null, { status: 302, headers: { location: options.redirectLocation } });
      }
      return new Response(options.downloadBody || new Uint8Array([0x4f, 0x67, 0x67, 0x53]), {
        status: 200,
        headers: { "content-type": "audio/ogg" },
      });
    }
    throw new Error(`unexpected_fetch:${url}`);
  });

  return {
    env,
    get inbox() { return inbox; },
    get metadataAttempts() { return metadataAttempts; },
    get mediaDownloads() { return mediaDownloads; },
    mediaPuts,
    mediaDeletes,
  };
}

function pendingContext() {
  const pending: Promise<unknown>[] = [];
  return {
    context: {
      waitUntil(promise: Promise<unknown>) { pending.push(Promise.resolve(promise)); },
      passThroughOnException: () => undefined,
      props: {},
    } as unknown as ExecutionContext,
    async drain() {
      for (let index = 0; index < pending.length; index += 1) await pending[index];
    },
  };
}

async function inboundAudioRequest(messageId: string): Promise<Request> {
  const body = JSON.stringify({
    entry: [{ changes: [{ value: {
      metadata: { phone_number_id: "phone-id" },
      messages: [{
        id: messageId,
        from: "5537999990000",
        timestamp: String(Math.floor(Date.now() / 1000)),
        type: "audio",
        audio: { id: "media-1", mime_type: "audio/ogg" },
      }],
    } }] }],
  });
  return new Request("https://example.test/webhooks/whatsapp", {
    method: "POST",
    body,
    headers: { "x-hub-signature-256": await signature("app-secret", body) },
  });
}

function inboundResultIdempotencyEnvironment() {
  const inbox: Record<string, any> = {
    message_id: "wamid.result.idempotent",
    subject_id: "subject",
    wa_id: "5537999990000",
    machine_id: "machine",
    status: "leased",
    media_object_key: null,
    media_size: 0,
  };
  const outboxByKey = new Map<string, Record<string, any>>();
  const db = {
    prepare(sql: string) {
      const make = (values: any[]) => ({
        async first() {
          if (sql.includes("SELECT i.*,b.machine_id FROM inbox")) return { ...inbox };
          if (sql.includes("SELECT media_object_key,media_size FROM inbox")) return { media_object_key: null, media_size: 0 };
          if (sql.includes("SELECT id FROM outbox WHERE idempotency_key=")) return outboxByKey.get(String(values[0])) || null;
          return null;
        },
        async run() {
          if (sql.includes("UPDATE inbox SET status=?,task_id=?")) {
            inbox.status = values[0];
            inbox.completed_at = values[3];
          } else if (sql.includes("INSERT OR IGNORE INTO outbox")) {
            const key = String(values[11]);
            if (!outboxByKey.has(key)) {
              outboxByKey.set(key, {
                id: values[0], inbound_message_id: values[1], subject_id: values[2], recipient: values[3],
                message_type: values[4], text_body: values[5], template_name: values[6], template_params_json: values[7],
                status: values[8], created_at: values[9], updated_at: values[10], idempotency_key: key, attempts: 0,
              });
            }
          } else if (sql.includes("UPDATE outbox SET status=?")) {
            const item = [...outboxByKey.values()].find((candidate) => candidate.id === values[3]);
            if (item) item.status = values[0];
          }
          return { meta: { changes: 1 } };
        },
        async all() {
          if (sql.includes("SELECT * FROM outbox WHERE subject_id=")) {
            return { results: [...outboxByKey.values()].filter((item) => ["queued", "retry", "waiting_free_window"].includes(item.status)) };
          }
          return { results: [] };
        },
      });
      return {
        bind(...values: any[]) { return make(values); },
        first() { return make([]).first(); },
        run() { return make([]).run(); },
        all() { return make([]).all(); },
      };
    },
  };
  return {
    env: {
      DB: db,
      MEDIA: { async delete() { return undefined; } },
      BRIDGE_TOKEN: "bridge-secret",
      ZERO_COST_POLICY_VALID_UNTIL: "2026-01-01T00:00:00Z",
    } as any,
    inbox,
    outboxByKey,
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("public gateway routes", () => {
  const env = {
    META_VERIFY_TOKEN: "verify-me",
    META_APP_SECRET: "app-secret",
    BRIDGE_TOKEN: "bridge-secret",
  } as any;

  it("accepts only the configured webhook challenge", async () => {
    const accepted = await worker.fetch(
      new Request("https://example.test/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=12345"),
      env,
      context(),
    );
    expect(accepted.status).toBe(200);
    expect(await accepted.text()).toBe("12345");

    const denied = await worker.fetch(
      new Request("https://example.test/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=12345"),
      env,
      context(),
    );
    expect(denied.status).toBe(401);
  });

  it("rejects absent or tampered Meta signatures", async () => {
    const body = JSON.stringify({ entry: [] });
    const absent = await worker.fetch(
      new Request("https://example.test/webhooks/whatsapp", { method: "POST", body }),
      env,
      context(),
    );
    expect(absent.status).toBe(401);

    const tampered = await worker.fetch(
      new Request("https://example.test/webhooks/whatsapp", {
        method: "POST",
        body,
        headers: { "x-hub-signature-256": await signature("wrong-secret", body) },
      }),
      env,
      context(),
    );
    expect(tampered.status).toBe(401);
  });

  it("acknowledges a correctly signed webhook immediately", async () => {
    const body = JSON.stringify({ entry: [] });
    const response = await worker.fetch(
      new Request("https://example.test/webhooks/whatsapp", {
        method: "POST",
        body,
        headers: { "x-hub-signature-256": await signature("app-secret", body) },
      }),
      env,
      context(),
    );
    expect(response.status).toBe(200);
    expect(await response.text()).toBe("EVENT_RECEIVED");
  });

  it("preserves quoted context in claims without crossing the bound subject or tenant", async () => {
    const target = quotedInboundEnvironment();
    const body = JSON.stringify({
      entry: [{
        changes: [{
          value: {
            metadata: { phone_number_id: "phone-id" },
            messages: [
              {
                id: "wamid.inbound.same",
                from: "5537999990000",
                timestamp: String(Math.floor(Date.now() / 1000)),
                type: "text",
                text: { body: "E o mesmo SKU na Uai Mineirinho?" },
                context: { id: "wamid.quote.same" },
              },
              {
                id: "wamid.inbound.other",
                from: "5537999990000",
                timestamp: String(Math.floor(Date.now() / 1000) + 1),
                type: "text",
                text: { body: "Repita o dado citado." },
                context: { id: "wamid.quote.other" },
              },
              {
                id: "wamid.inbound.card",
                from: "5537999990000",
                timestamp: String(Math.floor(Date.now() / 1000) + 2),
                type: "text",
                text: { body: "Pode corrigir esse cartao?" },
                context: { id: "wamid.card.same" },
              },
              {
                id: "wamid.inbound.card.other-tenant",
                from: "5537999990000",
                timestamp: String(Math.floor(Date.now() / 1000) + 3),
                type: "text",
                text: { body: "Qual era o cartao?" },
                context: { id: "wamid.card.other-tenant" },
              },
            ],
          },
        }],
      }],
    });
    const pending = pendingContext();
    const webhook = await worker.fetch(
      new Request("https://example.test/webhooks/whatsapp", {
        method: "POST",
        body,
        headers: { "x-hub-signature-256": await signature("app-secret", body) },
      }),
      target.env,
      pending.context,
    );
    expect(webhook.status).toBe(200);
    await pending.drain();

    const claim = await worker.fetch(
      new Request("https://example.test/bridge/claim", {
        method: "POST",
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
        body: JSON.stringify({ machine_id: "machine-a", limit: 5 }),
      }),
      target.env,
      context(),
    );
    const claimed = await claim.json() as any;

    expect(claimed.messages).toHaveLength(4);
    const claimedByQuote = new Map(claimed.messages.map((item: any) => [item.quoted_message_id, item]));
    expect(claimedByQuote.get("wamid.quote.same")).toMatchObject({
      text_body: "E o mesmo SKU na Uai Mineirinho?",
      quoted_message_id: "wamid.quote.same",
      quoted_text: "SKU 001 na JK Peças: 5 unidades no Full.",
    });
    expect(claimedByQuote.get("wamid.quote.other")).toMatchObject({
      quoted_message_id: "wamid.quote.other",
      quoted_text: null,
    });
    expect(claimedByQuote.get("wamid.card.same")).toMatchObject({
      quoted_message_id: "wamid.card.same",
      quoted_text: "Black Jhon\nResposta sugerida\nEscolher acao\nAprovar e enviar - Envia esta resposta",
    });
    expect(claimedByQuote.get("wamid.card.other-tenant")).toMatchObject({
      quoted_message_id: "wamid.card.other-tenant",
      quoted_text: null,
    });
  });

  it("durably retries inbound audio after timeout, 404, 429 and 5xx before storing it once", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-18T12:00:00Z"));
    const media = inboundMediaRetryEnvironment(["timeout", 404, 429, 503, "success"]);
    const body = JSON.stringify({
      entry: [{
        changes: [{
          value: {
            metadata: { phone_number_id: "phone-id" },
            messages: [{
              id: "wamid.audio.retry",
              from: "5537999990000",
              timestamp: String(Math.floor(Date.now() / 1000)),
              type: "audio",
              audio: { id: "media-1", mime_type: "audio/ogg; codecs=opus" },
            }],
          },
        }],
      }],
    });
    const pending = pendingContext();
    const response = await worker.fetch(
      new Request("https://example.test/webhooks/whatsapp", {
        method: "POST",
        body,
        headers: { "x-hub-signature-256": await signature("app-secret", body) },
      }),
      media.env,
      pending.context,
    );
    await pending.drain();

    expect(response.status).toBe(200);
    expect(media.inbox).toMatchObject({
      status: "media_retry",
      media_state: "retry_wait",
      media_attempts: 1,
      media_error_class: "media_fetch_timeout",
    });
    expect(Number(media.inbox?.media_next_attempt_at) - Math.floor(Date.now() / 1000)).toBe(5 * 60);

    const statusResponse = await worker.fetch(
      new Request("https://example.test/bridge/status?machine_id=machine", { headers: { authorization: "Bearer bridge-secret" } }),
      media.env,
      context(),
    );
    const statusPayload = await statusResponse.json() as any;
    expect(statusPayload).toMatchObject({ gateway_protocol_version: 1, build_version: "1.0.103" });
    expect(statusPayload.inbound_media).toMatchObject({
      durable_retry: true,
      max_attempts: 5,
      retry_delays_seconds: [300, 900, 1800, 3600],
      retention_seconds: 172800,
      counts: { waiting_retry: 1 },
    });
    expect(statusPayload.inbound_media.failures_by_code).toContainEqual({ error_class: "media_fetch_timeout", total: 1 });

    const expectedFailures = [
      { attempts: 2, error: "meta_metadata_not_found", delay: 15 * 60 },
      { attempts: 3, error: "meta_metadata_rate_limited", delay: 30 * 60 },
      { attempts: 4, error: "meta_metadata_server_error", delay: 60 * 60 },
    ];
    for (const expected of expectedFailures) {
      vi.setSystemTime(Number(media.inbox?.media_next_attempt_at) * 1000);
      await worker.scheduled({} as ScheduledController, media.env, context());
      expect(media.inbox).toMatchObject({
        status: "media_retry",
        media_state: "retry_wait",
        media_attempts: expected.attempts,
        media_error_class: expected.error,
      });
      expect(Number(media.inbox?.media_next_attempt_at) - Math.floor(Date.now() / 1000)).toBe(expected.delay);
    }

    vi.setSystemTime(Number(media.inbox?.media_next_attempt_at) * 1000);
    await worker.scheduled({} as ScheduledController, media.env, context());
    expect(media.inbox).toMatchObject({ status: "queued", media_state: "stored", media_attempts: 5, media_error_class: null });
    expect(media.metadataAttempts).toBe(5);
    expect(media.mediaDownloads).toBe(1);
    expect(media.mediaPuts).toHaveLength(1);
    expect(media.mediaPuts[0]?.options.expiration).toBe(media.inbox?.media_expires_at);

    const duplicate = pendingContext();
    await worker.fetch(
      new Request("https://example.test/webhooks/whatsapp", {
        method: "POST",
        body,
        headers: { "x-hub-signature-256": await signature("app-secret", body) },
      }),
      media.env,
      duplicate.context,
    );
    await duplicate.drain();
    expect(media.metadataAttempts).toBe(5);
    expect(media.mediaPuts).toHaveLength(1);

    vi.setSystemTime((Number(media.inbox?.media_expires_at) - 1) * 1000);
    await worker.scheduled({} as ScheduledController, media.env, context());
    expect(media.inbox).toMatchObject({ status: "queued", media_state: "stored" });
    expect(media.mediaDeletes).toHaveLength(0);

    vi.setSystemTime((Number(media.inbox?.media_expires_at) + 1) * 1000);
    await worker.scheduled({} as ScheduledController, media.env, context());
    expect(media.inbox).toMatchObject({
      status: "failed",
      media_state: "expired",
      media_object_key: null,
      media_error_class: "media_retention_expired",
    });
    expect(media.mediaDeletes).toHaveLength(1);
  });

  it("stops inbound media retry after five failed attempts without producing a queued message", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-18T14:00:00Z"));
    const media = inboundMediaRetryEnvironment([503, 503, 503, 503, 503]);
    const body = JSON.stringify({
      entry: [{ changes: [{ value: {
        metadata: { phone_number_id: "phone-id" },
        messages: [{
          id: "wamid.audio.exhausted", from: "5537999990000", timestamp: String(Math.floor(Date.now() / 1000)),
          type: "audio", audio: { id: "media-1", mime_type: "audio/ogg" },
        }],
      } }] }],
    });
    const pending = pendingContext();
    await worker.fetch(
      new Request("https://example.test/webhooks/whatsapp", {
        method: "POST", body, headers: { "x-hub-signature-256": await signature("app-secret", body) },
      }),
      media.env,
      pending.context,
    );
    await pending.drain();
    for (let attempt = 2; attempt <= 5; attempt += 1) {
      vi.setSystemTime(Number(media.inbox?.media_next_attempt_at) * 1000);
      await worker.scheduled({} as ScheduledController, media.env, context());
    }
    expect(media.inbox).toMatchObject({
      status: "failed",
      media_state: "failed",
      media_attempts: 5,
      media_error_class: "meta_metadata_server_error",
    });
    expect(media.metadataAttempts).toBe(5);
    expect(media.mediaDownloads).toBe(0);
    expect(media.mediaPuts).toHaveLength(0);
  });

  it("keeps the legacy one-shot media path working while migration 0008 is not applied", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-18T15:00:00Z"));
    const media = inboundMediaRetryEnvironment(["success"], { schemaMissing: true });
    const body = JSON.stringify({
      entry: [{ changes: [{ value: {
        metadata: { phone_number_id: "phone-id" },
        messages: [{
          id: "wamid.audio.legacy", from: "5537999990000", timestamp: String(Math.floor(Date.now() / 1000)),
          type: "audio", audio: { id: "media-1", mime_type: "audio/ogg" },
        }],
      } }] }],
    });
    const pending = pendingContext();
    await worker.fetch(
      new Request("https://example.test/webhooks/whatsapp", {
        method: "POST", body, headers: { "x-hub-signature-256": await signature("app-secret", body) },
      }),
      media.env,
      pending.context,
    );
    await pending.drain();

    expect(media.inbox).toMatchObject({ status: "queued", media_object_key: expect.any(String) });
    expect(media.metadataAttempts).toBe(1);
    expect(media.mediaDownloads).toBe(1);
    expect(media.mediaPuts).toHaveLength(1);
    expect(media.mediaPuts[0]?.options.expirationTtl).toBe(172800);
  });

  it("fails closed on a non-retryable Meta media 4xx", async () => {
    const media = inboundMediaRetryEnvironment([400]);
    const body = JSON.stringify({
      entry: [{ changes: [{ value: {
        metadata: { phone_number_id: "phone-id" },
        messages: [{
          id: "wamid.audio.bad-request", from: "5537999990000", timestamp: String(Math.floor(Date.now() / 1000)),
          type: "audio", audio: { id: "media-1", mime_type: "audio/ogg" },
        }],
      } }] }],
    });
    const pending = pendingContext();
    await worker.fetch(
      new Request("https://example.test/webhooks/whatsapp", {
        method: "POST", body, headers: { "x-hub-signature-256": await signature("app-secret", body) },
      }),
      media.env,
      pending.context,
    );
    await pending.drain();

    expect(media.inbox).toMatchObject({
      status: "failed",
      media_state: "failed",
      media_attempts: 1,
      media_error_class: "meta_metadata_client_error",
      media_next_attempt_at: null,
    });
    expect(media.mediaPuts).toHaveLength(0);
  });

  it("rejects non-HTTPS or non-official Meta metadata URLs before sending credentials", async () => {
    const media = inboundMediaRetryEnvironment(["success"], {
      metadataUrl: "https://attacker.example/private-audio",
    });
    const pending = pendingContext();
    await worker.fetch(await inboundAudioRequest("wamid.audio.untrusted-url"), media.env, pending.context);
    await pending.drain();

    expect(media.inbox).toMatchObject({
      status: "failed",
      media_state: "failed",
      media_error_class: "meta_media_url_not_allowed",
    });
    expect(media.mediaDownloads).toBe(0);
    expect(media.mediaPuts).toHaveLength(0);
  });

  it("does not follow an official Meta media redirect to an untrusted host", async () => {
    const media = inboundMediaRetryEnvironment(["success"], {
      redirectLocation: "https://attacker.example/redirected-audio",
    });
    const pending = pendingContext();
    await worker.fetch(await inboundAudioRequest("wamid.audio.redirect-outside"), media.env, pending.context);
    await pending.drain();

    expect(media.inbox).toMatchObject({
      status: "failed",
      media_state: "failed",
      media_error_class: "meta_media_url_not_allowed",
    });
    expect(media.mediaDownloads).toBe(1);
    expect(media.mediaPuts).toHaveLength(0);
  });

  it("stops a streaming Meta media download as soon as the byte limit is crossed", async () => {
    const largeChunk = new Uint8Array(9 * 1024 * 1024);
    let pulls = 0;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        pulls += 1;
        controller.enqueue(largeChunk);
        if (pulls >= 2) controller.close();
      },
    });
    const media = inboundMediaRetryEnvironment(["success"], {
      metadataSize: 0,
      downloadBody: body,
    });
    const pending = pendingContext();
    await worker.fetch(await inboundAudioRequest("wamid.audio.streaming-limit"), media.env, pending.context);
    await pending.drain();

    expect(media.inbox).toMatchObject({
      status: "failed",
      media_state: "failed",
      media_error_class: "media_size_limit",
    });
    expect(pulls).toBe(2);
    expect(media.mediaPuts).toHaveLength(0);
  });

  it("does not queue a duplicate WhatsApp response when a terminal bridge result is replayed", async () => {
    const target = inboundResultIdempotencyEnvironment();
    const request = () => new Request("https://example.test/bridge/messages/wamid.result.idempotent/result", {
      method: "POST",
      headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      body: JSON.stringify({ machine_id: "machine", status: "completed", response: "Resposta unica" }),
    });

    const first = await worker.fetch(request(), target.env, context());
    const replay = await worker.fetch(request(), target.env, context());

    expect(await first.json()).toMatchObject({ success: true, status: "completed", queued_parts: 1 });
    expect(await replay.json()).toMatchObject({
      success: true,
      status: "completed",
      queued_parts: 0,
      idempotent_replay: true,
    });
    expect(target.outboxByKey.size).toBe(1);
    expect([...target.outboxByKey.keys()]).toEqual(["inbound_result:wamid.result.idempotent:1"]);
  });

  it("informs an unregistered WhatsApp number that it has no Black Jhon permission", async () => {
    const unauthorized = unauthorizedEnvironment();
    const phone = "5537999993818";
    const body = JSON.stringify({
      entry: [{
        changes: [{
          value: {
            metadata: { phone_number_id: "phone-id" },
            contacts: [{ wa_id: phone }],
            messages: [{ id: "wamid.in.unauthorized", from: phone, timestamp: String(Math.floor(Date.now() / 1000)), type: "text", text: { body: "Olá" } }],
          },
        }],
      }],
    });
    const pending: Promise<unknown>[] = [];
    const ctx = {
      waitUntil(promise: Promise<unknown>) { pending.push(Promise.resolve(promise)); },
      passThroughOnException: () => undefined,
      props: {},
    } as unknown as ExecutionContext;
    const response = await worker.fetch(
      new Request("https://example.test/webhooks/whatsapp", {
        method: "POST",
        body,
        headers: { "x-hub-signature-256": await signature("app-secret", body) },
      }),
      unauthorized.env,
      ctx,
    );
    for (let index = 0; index < pending.length; index += 1) await pending[index];

    expect(response.status).toBe(200);
    expect(unauthorized.graphRequests).toHaveLength(1);
    expect(unauthorized.graphRequests[0].body).toMatchObject({
      to: phone,
      type: "text",
      text: {
        body: "Olá! Este número não possui permissão para acessar o Black Jhon. Solicite a um administrador do JK Sistema que cadastre e autorize este número.",
      },
    });
    expect(unauthorized.sqlCalls.some((item) => item.values.includes("unauthorized_access_notice"))).toBe(true);
  });

  it("protects every bridge route with BRIDGE_TOKEN", async () => {
    const response = await worker.fetch(
      new Request("https://example.test/bridge/status"),
      env,
      context(),
    );
    expect(response.status).toBe(401);
  });

  it("requires an active machine binding for status and inbound media reads", async () => {
    const db = {
      prepare(sql: string) {
        return {
          bind(..._values: unknown[]) {
            return {
              async first() {
                if (sql.includes("FROM bindings WHERE active=1 AND machine_id=")) return null;
                return null;
              },
            };
          },
        };
      },
    };
    const scopedEnv = { DB: db, BRIDGE_TOKEN: "bridge-secret" } as any;
    const missing = await worker.fetch(
      new Request("https://example.test/bridge/status", { headers: { authorization: "Bearer bridge-secret" } }),
      scopedEnv,
      context(),
    );
    expect(missing.status).toBe(400);
    expect(await missing.json()).toMatchObject({ success: false, error: "machine_id_required" });

    const inactive = await worker.fetch(
      new Request("https://example.test/bridge/status?machine_id=other-machine", { headers: { authorization: "Bearer bridge-secret" } }),
      scopedEnv,
      context(),
    );
    expect(inactive.status).toBe(403);
    expect(await inactive.json()).toMatchObject({ success: false, error: "binding_machine_inactive" });

    const media = await worker.fetch(
      new Request("https://example.test/bridge/media/wamid.private?machine_id=other-machine", { headers: { authorization: "Bearer bridge-secret" } }),
      scopedEnv,
      context(),
    );
    expect(media.status).toBe(404);
    expect(await media.json()).toMatchObject({ success: false, error: "media_not_found" });
  });

  it("blocks proactive text when machine_id does not own the active subject binding", async () => {
    const target = outboundImageEnvironment({ machineId: "machine-owner" });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/proactive", {
        method: "POST",
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
        body: JSON.stringify({
          subject_id: "subject",
          machine_id: "machine-attacker",
          fingerprint: "task:machine-scope:123456",
          event_type: "task_completed",
          text: "conteudo privado",
        }),
      }),
      target.env,
      context(),
    );
    expect(response.status).toBe(403);
    expect(await response.json()).toMatchObject({ success: false, error: "binding_machine_mismatch" });
    expect(target.graphRequests).toHaveLength(0);
  });

  it("queues task_partial as a task and normalizes warning to medium", async () => {
    for (const requestedSeverity of ["medium", "warning"]) {
      const target = outboundImageEnvironment();
      const response = await worker.fetch(
        new Request("https://example.test/bridge/proactive", {
          method: "POST",
          headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
          body: JSON.stringify({
            subject_id: "subject",
            machine_id: "machine",
            fingerprint: `job:partial-stock:${requestedSeverity}`,
            event_type: "task_partial",
            severity: requestedSeverity,
            text: "Não encontrei saldo confirmado para esta consulta.",
          }),
        }),
        target.env,
        context(),
      );

      expect(response.status).toBe(200);
      expect(await response.json()).toMatchObject({ success: true, status: "queued", queued_parts: 1 });
      const event = target.sqlCalls.find((item) => item.operation === "run" && item.sql.includes("INSERT INTO proactive_events"));
      expect(event?.values).toContain("task_partial");
      expect(event?.values).toContain("medium");
    }
  });

  it("registers name-targeted phone bindings directly without sending a confirmation message", async () => {
    const direct = registrationEnvironment();
    const response = await worker.fetch(
      new Request("https://example.test/bridge/bindings/register", {
        method: "POST",
        body: JSON.stringify({
          phone_number: "(37) 99999-3818",
          client_id: "cliente",
          username: "operador",
          machine_id: "machine-1",
        }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      direct.env,
      context(),
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      success: true,
      created: true,
      subject_id: "5537999993818",
      binding: {
        phone_number: "5537999993818",
        client_id: "cliente",
        username: "operador",
        machine_id: "machine-1",
        last_inbound_at: 0,
      },
    });
    const insert = direct.sqlCalls.find((item) => item.operation === "run" && item.sql.includes("INSERT INTO bindings"));
    expect(insert?.values.slice(0, 6)).toEqual([
      "5537999993818",
      "5537999993818",
      "5537999993818",
      "cliente",
      "operador",
      "machine-1",
    ]);
    expect(direct.sqlCalls.some((item) => item.sql.includes("outbox"))).toBe(false);
  });

  it("routes a BSUID webhook to a phone binding registered directly by wa_id", async () => {
    const registeredPhone = "5537999995515";
    const metaPhoneWithoutNinthDigit = "553799995515";
    const direct = directRegisteredInboundEnvironment(registeredPhone);
    const body = JSON.stringify({
      entry: [{
        changes: [{
          value: {
            metadata: { phone_number_id: "phone-id" },
            contacts: [{ wa_id: metaPhoneWithoutNinthDigit, bsuid: "opaque-bsuid-for-this-business" }],
            messages: [{
              id: "wamid.in.direct-binding",
              from: metaPhoneWithoutNinthDigit,
              timestamp: String(Math.floor(Date.now() / 1000)),
              type: "text",
              text: { body: "O Black Jhon está disponível?" },
            }],
          },
        }],
      }],
    });
    const pending: Promise<unknown>[] = [];
    const ctx = {
      waitUntil(promise: Promise<unknown>) { pending.push(Promise.resolve(promise)); },
      passThroughOnException: () => undefined,
      props: {},
    } as unknown as ExecutionContext;

    const response = await worker.fetch(
      new Request("https://example.test/webhooks/whatsapp", {
        method: "POST",
        body,
        headers: { "x-hub-signature-256": await signature("app-secret", body) },
      }),
      direct.env,
      ctx,
    );
    for (let index = 0; index < pending.length; index += 1) await pending[index];

    expect(response.status).toBe(200);
    const lookups = direct.sqlCalls.filter((item) => item.sql.includes("subject_id=? OR wa_id=? OR phone_number=?"));
    expect(lookups).toHaveLength(2);
    expect(lookups[0]?.values).toEqual([
      "opaque-bsuid-for-this-business",
      metaPhoneWithoutNinthDigit,
      metaPhoneWithoutNinthDigit,
    ]);
    expect(lookups[1]?.values).toEqual([registeredPhone, registeredPhone, registeredPhone]);
    const insert = direct.sqlCalls.find((item) => item.sql.includes("INSERT INTO inbox"));
    expect(insert?.values[1]).toBe(registeredPhone);
    expect(insert?.values[2]).toBe(metaPhoneWithoutNinthDigit);
    expect(direct.sqlCalls.some((item) => item.sql.includes("UPDATE bindings SET last_inbound_at"))).toBe(true);
  });

  it("sends a written welcome message when the WhatsApp service window is open", async () => {
    const welcome = welcomeEnvironment();
    const response = await worker.fetch(
      new Request("https://example.test/bridge/welcome", {
        method: "POST",
        body: JSON.stringify({
          subject_id: "5537999993818",
          machine_id: "machine-1",
          text: "Olá! Seja bem-vindo ao JK Sistema.",
        }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      welcome.env,
      context(),
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ success: true, status: "sent", meta_message_id: "wamid.welcome" });
    expect(welcome.graphRequests).toHaveLength(1);
    expect(welcome.graphRequests[0]).toMatchObject({
      url: "https://graph.facebook.com/v25.0/phone-id/messages",
      body: {
        messaging_product: "whatsapp",
        recipient_type: "individual",
        to: "5537999993818",
        type: "text",
        text: { preview_url: false, body: "Olá! Seja bem-vindo ao JK Sistema." },
      },
    });
  });

  it("keeps the welcome message waiting when the WhatsApp service window is closed", async () => {
    const welcome = welcomeEnvironment(0);
    const response = await worker.fetch(
      new Request("https://example.test/bridge/welcome", {
        method: "POST",
        body: JSON.stringify({
          subject_id: "5537999993818",
          machine_id: "machine-1",
          text: "Mensagem aguardando janela.",
        }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      welcome.env,
      context(),
    );

    expect(await response.json()).toMatchObject({ success: true, status: "waiting_free_window" });
    expect(welcome.graphRequests).toHaveLength(0);
  });

  it("sends an ad hoc message to any normalized number with an open service window", async () => {
    const adhoc = welcomeEnvironment();
    const response = await worker.fetch(
      new Request("https://example.test/bridge/messages/send", {
        method: "POST",
        body: JSON.stringify({
          phone_number: "(37) 99999-3818",
          machine_id: "machine-1",
          text: "Mensagem avulsa para o cliente.",
        }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      adhoc.env,
      context(),
    );

    expect(await response.json()).toMatchObject({ success: true, status: "sent", meta_message_id: "wamid.welcome" });
    expect(adhoc.graphRequests[0].body).toMatchObject({
      to: "5537999993818",
      type: "text",
      text: { body: "Mensagem avulsa para o cliente." },
    });
    expect(adhoc.sqlCalls.some((item) => item.sql.includes("message_type='adhoc_text'"))).toBe(true);
  });

  it("keeps an ad hoc message waiting for a number that has not opened the service window", async () => {
    const adhoc = welcomeEnvironment(0, false, 0);
    const response = await worker.fetch(
      new Request("https://example.test/bridge/messages/send", {
        method: "POST",
        body: JSON.stringify({
          phone_number: "5537999993818",
          machine_id: "machine-1",
          text: "Mensagem aguardando a primeira interação.",
        }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      adhoc.env,
      context(),
    );

    expect(await response.json()).toMatchObject({ success: true, status: "waiting_free_window" });
    expect(adhoc.graphRequests).toHaveLength(0);
    expect(adhoc.sqlCalls.some((item) => item.sql.includes("unpaired_phone_message"))).toBe(true);
  });

  it("rejects an oversized outbound image before parsing multipart or touching D1", async () => {
    const response = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.oversized/image", {
        method: "POST",
        body: "x",
        headers: {
          authorization: "Bearer bridge-secret",
          "content-type": "multipart/form-data; boundary=test",
          "content-length": String(6 * 1024 * 1024),
        },
      }),
      env,
      context(),
    );
    expect(response.status).toBe(413);
    expect(await response.json()).toMatchObject({ success: false, error: "outbound_image_size_limit" });
  });

  it("rejects an invalid outbound image payload before touching D1", async () => {
    const form = new FormData();
    form.set("machine_id", "machine");
    const response = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.invalid/image", {
        method: "POST",
        body: form,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      env,
      context(),
    );
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ success: false, error: "image_payload_required" });
  });

  it("accepts a report chart on the inbound-message image route", async () => {
    const { env: imageEnv, sqlCalls, graphRequests } = outboundImageEnvironment();
    const form = await imageForm({
      machine_id: "machine",
      artifact_type: "report_chart",
      caption: "Evolucao das vendas da semana",
    });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.report/image", {
        method: "POST",
        body: form,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      imageEnv,
      context(),
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ success: true, status: "sent", mime: "image/png" });
    expect(graphRequests.map((item) => item.url)).toEqual([
      "https://graph.facebook.com/v25.0/phone-id/media",
      "https://graph.facebook.com/v25.0/phone-id/messages",
    ]);
    const reservation = sqlCalls.find((item) => item.operation === "run" && item.sql.includes("INSERT OR IGNORE INTO outbound_media"));
    expect(reservation?.values).toContain("report_chart");
  });

  it("keeps the inbound-message image artifact allowlist closed", async () => {
    const { env: imageEnv, graphRequests } = outboundImageEnvironment();
    const form = await imageForm({ machine_id: "machine", artifact_type: "marketing_banner" });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.report/image", {
        method: "POST",
        body: form,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      imageEnv,
      context(),
    );
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ success: false, error: "outbound_image_artifact_not_allowed" });
    expect(graphRequests).toHaveLength(0);
  });

  it("requires PNG specifically for report-chart artifacts", async () => {
    const { env: imageEnv, sqlCalls, graphRequests } = outboundImageEnvironment();
    const form = await imageForm(
      { machine_id: "machine", artifact_type: "report_chart" },
      { bytes: TEST_JPEG, mime: "image/jpeg", name: "chart.jpg" },
    );
    const response = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.jpeg-chart/image", {
        method: "POST",
        body: form,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      imageEnv,
      context(),
    );
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ success: false, error: "report_chart_png_required" });
    expect(sqlCalls.some((item) => item.sql.includes("INSERT OR IGNORE INTO outbound_media"))).toBe(false);
    expect(graphRequests).toHaveLength(0);
  });

  it("enforces at most five artifacts for one inbound response", async () => {
    const limited = outboundImageEnvironment({ reserve: false, existing: null, messageImageCount: 5 });
    const form = await imageForm({ machine_id: "machine", artifact_type: "report_chart" });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.three-images/image", {
        method: "POST",
        body: form,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      limited.env,
      context(),
    );
    expect(response.status).toBe(429);
    expect(await response.json()).toMatchObject({ success: false, error: "outbound_media_response_limit" });
    expect(limited.graphRequests).toHaveLength(0);
  });

  it("sends a weekly report chart through the proactive image route", async () => {
    const { env: imageEnv, sqlCalls, graphRequests } = outboundImageEnvironment();
    const form = await imageForm({
      subject_id: "subject",
      machine_id: "machine",
      fingerprint: "weekly:2026-07-13",
      event_type: "weekly_report",
      artifact_type: "report_chart",
      caption: "Resumo semanal da JK Pecas",
    });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/proactive/image", {
        method: "POST",
        body: form,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      imageEnv,
      context(),
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ success: true, status: "sent", mime: "image/png" });
    expect(graphRequests.map((item) => item.url)).toEqual([
      "https://graph.facebook.com/v25.0/phone-id/media",
      "https://graph.facebook.com/v25.0/phone-id/messages",
    ]);
    const send = graphRequests[1];
    expect(JSON.parse(String(send.body))).toMatchObject({
      messaging_product: "whatsapp",
      recipient_type: "individual",
      to: "553798379212",
      type: "image",
      image: { id: "media-id", caption: "Resumo semanal da JK Pecas" },
    });
    const reservation = sqlCalls.find((item) => item.operation === "run" && item.sql.includes("INSERT OR IGNORE INTO outbound_media"));
    expect(reservation?.values).toContain("proactive:weekly_report:weekly:2026-07-13");
    expect(reservation?.values).toContain("report_chart");
    expect(reservation?.sql).toContain("SUM(byte_size)");
    expect(reservation?.sql).toContain("COUNT(*)");
  });

  it("sends an idempotent product photo for a completed voice task", async () => {
    const { env: imageEnv, sqlCalls, graphRequests } = outboundImageEnvironment();
    const form = await imageForm({
      subject_id: "subject",
      machine_id: "machine",
      fingerprint: "voice-product:0123456789abcdef",
      event_type: "task_completed",
      artifact_type: "product_photo",
      caption: "Loja Principal - MLB123456 - Foto 1/1",
    }, { bytes: TEST_JPEG, mime: "image/jpeg", name: "MLB123456-1.jpg" });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/proactive/image", {
        method: "POST",
        body: form,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      imageEnv,
      context(),
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ success: true, status: "sent", mime: "image/jpeg" });
    const send = graphRequests[1];
    expect(JSON.parse(String(send.body))).toMatchObject({
      to: "553798379212",
      type: "image",
      image: { id: "media-id", caption: "Loja Principal - MLB123456 - Foto 1/1" },
    });
    const reservation = sqlCalls.find((item) => item.operation === "run" && item.sql.includes("INSERT OR IGNORE INTO outbound_media"));
    expect(reservation?.values).toContain("proactive:task_completed:voice-product:0123456789abcdef");
    expect(reservation?.values).toContain("product_photo");
  });

  it("rejects unsupported proactive image events and artifacts before D1", async () => {
    const { env: imageEnv, sqlCalls, graphRequests } = outboundImageEnvironment();
    const form = await imageForm({
      subject_id: "subject",
      machine_id: "machine",
      fingerprint: "daily:2026-07-13",
      event_type: "daily_report",
      artifact_type: "product_photo",
    });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/proactive/image", {
        method: "POST",
        body: form,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      imageEnv,
      context(),
    );
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ success: false, error: "invalid_proactive_image_payload" });
    expect(sqlCalls).toHaveLength(0);
    expect(graphRequests).toHaveLength(0);
  });

  it("rejects paths and URLs even when a valid binary chart is attached", async () => {
    const { env: imageEnv, sqlCalls, graphRequests } = outboundImageEnvironment();
    const form = await imageForm({
      subject_id: "subject",
      machine_id: "machine",
      fingerprint: "weekly:2026-07-13",
      event_type: "weekly_report",
      artifact_type: "report_chart",
      url: "https://example.test/chart.png",
      file_path: "C:\\private\\chart.png",
    });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/proactive/image", {
        method: "POST",
        body: form,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      imageEnv,
      context(),
    );
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ success: false, error: "invalid_proactive_image_payload" });
    expect(sqlCalls).toHaveLength(0);
    expect(graphRequests).toHaveLength(0);
  });

  it("validates proactive image MIME and PNG magic before reserving quota", async () => {
    const { env: imageEnv, sqlCalls, graphRequests } = outboundImageEnvironment();
    const fakePng = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x00, 0x00, 0x00]);
    const form = await imageForm({
      subject_id: "subject",
      machine_id: "machine",
      fingerprint: "weekly:2026-07-13",
      event_type: "weekly_report",
      artifact_type: "report_chart",
    }, { bytes: fakePng, mime: "image/png" });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/proactive/image", {
        method: "POST",
        body: form,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      imageEnv,
      context(),
    );
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ success: false, error: "outbound_image_signature_invalid" });
    expect(sqlCalls.some((item) => item.sql.includes("INSERT OR IGNORE INTO outbound_media"))).toBe(false);
    expect(graphRequests).toHaveLength(0);
  });

  it("blocks proactive charts for the wrong machine or a closed zero-cost window", async () => {
    const wrongMachine = outboundImageEnvironment({ machineId: "other-machine" });
    const wrongMachineForm = await imageForm({
      subject_id: "subject",
      machine_id: "machine",
      fingerprint: "weekly:2026-07-13",
      event_type: "weekly_report",
      artifact_type: "report_chart",
    });
    const denied = await worker.fetch(
      new Request("https://example.test/bridge/proactive/image", {
        method: "POST",
        body: wrongMachineForm,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      wrongMachine.env,
      context(),
    );
    expect(denied.status).toBe(403);
    expect(await denied.json()).toMatchObject({ success: false, error: "binding_machine_mismatch" });
    expect(wrongMachine.graphRequests).toHaveLength(0);

    const closedWindow = outboundImageEnvironment({ lastInboundAt: 1 });
    const closedWindowForm = await imageForm({
      subject_id: "subject",
      machine_id: "machine",
      fingerprint: "weekly:2026-07-20",
      event_type: "weekly_report",
      artifact_type: "report_chart",
    });
    const blocked = await worker.fetch(
      new Request("https://example.test/bridge/proactive/image", {
        method: "POST",
        body: closedWindowForm,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      closedWindow.env,
      context(),
    );
    expect(blocked.status).toBe(409);
    expect(await blocked.json()).toMatchObject({ success: false, status: "waiting_free_window", error: "waiting_free_window" });
    expect(closedWindow.graphRequests).toHaveLength(0);
  });

  it("blocks proactive charts when the zero-cost policy has expired", async () => {
    const expired = outboundImageEnvironment({ policyUntil: "2026-01-01T00:00:00Z" });
    const form = await imageForm({
      subject_id: "subject",
      machine_id: "machine",
      fingerprint: "weekly:2026-07-13",
      event_type: "weekly_report",
      artifact_type: "report_chart",
    });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/proactive/image", {
        method: "POST",
        body: form,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      expired.env,
      context(),
    );
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ success: false, status: "policy_recheck_required", error: "policy_recheck_required" });
    expect(expired.graphRequests).toHaveLength(0);
  });

  it("deduplicates proactive charts and shares the outbound monthly quota", async () => {
    const duplicate = outboundImageEnvironment({
      reserve: false,
      existing: { status: "sent", meta_message_id: "existing-message", byte_size: TEST_PNG.byteLength, mime_type: "image/png" },
    });
    const duplicateForm = await imageForm({
      subject_id: "subject",
      machine_id: "machine",
      fingerprint: "weekly:2026-07-13",
      event_type: "weekly_report",
      artifact_type: "report_chart",
    });
    const duplicateResponse = await worker.fetch(
      new Request("https://example.test/bridge/proactive/image", {
        method: "POST",
        body: duplicateForm,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      duplicate.env,
      context(),
    );
    expect(await duplicateResponse.json()).toMatchObject({ success: true, status: "sent", duplicate: true, meta_message_id: "existing-message" });
    expect(duplicate.graphRequests).toHaveLength(0);

    const exhausted = outboundImageEnvironment({ reserve: false, existing: null });
    const exhaustedForm = await imageForm({
      subject_id: "subject",
      machine_id: "machine",
      fingerprint: "weekly:2026-07-20",
      event_type: "weekly_report",
      artifact_type: "report_chart",
    });
    const exhaustedResponse = await worker.fetch(
      new Request("https://example.test/bridge/proactive/image", {
        method: "POST",
        body: exhaustedForm,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      exhausted.env,
      context(),
    );
    expect(exhaustedResponse.status).toBe(429);
    expect(await exhaustedResponse.json()).toMatchObject({ success: false, error: "outbound_media_month_limit" });
    expect(exhausted.graphRequests).toHaveLength(0);
  });

  it("does not report a live outbound-media processing lease as success", async () => {
    const now = Math.floor(Date.now() / 1000);
    const processing = outboundImageEnvironment({
      reserve: false,
      existing: {
        status: "processing",
        attempts: 1,
        lease_until: now + 120,
        byte_size: TEST_PNG.byteLength,
        mime_type: "image/png",
      },
    });
    const form = await imageForm({
      subject_id: "subject",
      machine_id: "machine",
      fingerprint: "weekly:lease-active:2026-07-18",
      event_type: "weekly_report",
      artifact_type: "report_chart",
    });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/proactive/image", {
        method: "POST",
        body: form,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      processing.env,
      context(),
    );
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({
      success: false,
      status: "processing",
      error: "outbound_media_processing",
    });
    expect(processing.graphRequests).toHaveLength(0);
  });

  it("resumes an expired outbound-media lease with the same uploaded media id", async () => {
    const now = Math.floor(Date.now() / 1000);
    const resumed = outboundImageEnvironment({
      reserve: false,
      existing: {
        status: "processing",
        attempts: 1,
        lease_until: now - 1,
        meta_media_id: "existing-media-id",
        byte_size: TEST_PNG.byteLength,
        mime_type: "image/png",
      },
    });
    const form = await imageForm({
      subject_id: "subject",
      machine_id: "machine",
      fingerprint: "weekly:lease-expired:2026-07-18",
      event_type: "weekly_report",
      artifact_type: "report_chart",
    });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/proactive/image", {
        method: "POST",
        body: form,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      resumed.env,
      context(),
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ success: true, status: "sent" });
    expect(resumed.graphRequests.map((item) => item.url)).toEqual([
      "https://graph.facebook.com/v25.0/phone-id/messages",
    ]);
    expect(JSON.parse(String(resumed.graphRequests[0].body))).toMatchObject({
      image: { id: "existing-media-id" },
    });
    expect(resumed.sqlCalls.some((item) => item.sql.includes("attempts=attempts+1"))).toBe(true);
  });

  it("ships the additive outbound-media lease migration", () => {
    const migration = String.raw`${readFileSync(
      new URL("../migrations/0009_outbound_media_lease.sql", import.meta.url),
      "utf8",
    )}`.toLowerCase();
    expect(migration).toContain("attempts integer not null default 0");
    expect(migration).toContain("lease_owner text");
    expect(migration).toContain("lease_until integer");
    expect(migration).toContain("last_attempt_at integer");
  });

  it("ships tenant-scoped quoted context storage for direct Meta cards", () => {
    const migration = String.raw`${readFileSync(
      new URL("../migrations/0010_quoted_message_context.sql", import.meta.url),
      "utf8",
    )}`.toLowerCase();
    expect(migration).toContain("create table if not exists outbound_quote_context");
    expect(migration).toContain("meta_message_id text primary key");
    expect(migration).toContain("unique(fingerprint, subject_id, client_id, username)");
    expect(migration).toContain("on outbound_quote_context(meta_message_id, subject_id, client_id, username)");
  });

  it("rejects an altered proactive chart before reserving outbound quota", async () => {
    const { env: imageEnv, sqlCalls, graphRequests } = outboundImageEnvironment();
    const form = await imageForm({
      subject_id: "subject",
      machine_id: "machine",
      fingerprint: "weekly:2026-07-13",
      event_type: "weekly_report",
      artifact_type: "report_chart",
      sha256: "0".repeat(64),
    });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/proactive/image", {
        method: "POST",
        body: form,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      imageEnv,
      context(),
    );
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ success: false, error: "proactive_image_sha256_mismatch" });
    expect(sqlCalls.some((item) => item.sql.includes("INSERT OR IGNORE INTO outbound_media"))).toBe(false);
    expect(graphRequests).toHaveLength(0);
  });

  it("rejects malformed interactive approvals before touching D1", async () => {
    const response = await worker.fetch(
      new Request("https://example.test/bridge/interactive", {
        method: "POST",
        body: JSON.stringify({ subject_id: "subject", event_type: "question_approval", body: "teste" }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      env,
      context(),
    );
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ success: false, error: "invalid_interactive_payload" });
  });

  it("stores the authenticated local heartbeat with the thirty second offline contract", async () => {
    const calls: Array<{ sql: string; values: unknown[] }> = [];
    const heartbeatEnv = {
      BRIDGE_TOKEN: "bridge-secret",
      DB: {
        prepare(sql: string) {
          return {
            bind(...values: unknown[]) {
              return { async run() { calls.push({ sql, values }); return { meta: { changes: 1 } }; } };
            },
          };
        },
      },
    } as any;
    const response = await worker.fetch(
      new Request("https://example.test/bridge/heartbeat", {
        method: "POST",
        body: JSON.stringify({
          machine_id: "machine",
          client_id: "cliente",
          username: "admin",
          app_version: "bridge-v9",
          active_message_ids: ["wamid.long-running", "wamid.long-running", ""],
        }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      heartbeatEnv,
      context(),
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      success: true,
      status: "online",
      offline_after_seconds: 30,
      renewed_leases: 1,
    });
    expect(calls[0].sql).toContain("INSERT INTO bridge_heartbeats");
    expect(calls[0].values.slice(0, 5)).toEqual(["machine", "cliente", "admin", "bridge-v9", "online"]);
    expect(calls[1].sql).toContain("UPDATE inbox SET lease_until=?");
    expect(calls[1].sql).toContain("status='leased' AND lease_owner=?");
    expect(calls[1].values.slice(1)).toEqual(["machine", "wamid.long-running"]);
    expect(Number(calls[1].values[0] || 0)).toBeGreaterThan(Math.floor(Date.now() / 1000) + 590);
  });

  it("sends authenticated PDF and XLSX documents with hash binding and filenames", async () => {
    const pdfEnv = outboundImageEnvironment();
    const pdfForm = await imageForm(
      { machine_id: "machine", artifact_type: "report_pdf", caption: "Relatorio confirmado" },
      { bytes: TEST_PDF, mime: "application/pdf", name: "vendas.pdf" },
    );
    const pdfResponse = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.document/document", {
        method: "POST",
        body: pdfForm,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      pdfEnv.env,
      context(),
    );
    expect(pdfResponse.status).toBe(200);
    expect(await pdfResponse.json()).toMatchObject({ success: true, status: "sent", mime: "application/pdf" });
    expect(JSON.parse(String(pdfEnv.graphRequests[1].body))).toMatchObject({
      type: "document",
      document: { id: "media-id", caption: "Relatorio confirmado", filename: "vendas.pdf" },
    });

    const xlsxEnv = outboundImageEnvironment();
    const xlsxForm = await imageForm(
      {
        subject_id: "subject",
        machine_id: "machine",
        fingerprint: "monthly:2026-07-xlsx",
        event_type: "monthly_report",
        artifact_type: "report_xlsx",
      },
      { bytes: TEST_XLSX, mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", name: "vendas.xlsx" },
    );
    const xlsxResponse = await worker.fetch(
      new Request("https://example.test/bridge/proactive/document", {
        method: "POST",
        body: xlsxForm,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      xlsxEnv.env,
      context(),
    );
    expect(xlsxResponse.status).toBe(200);
    expect(await xlsxResponse.json()).toMatchObject({ success: true, status: "sent" });
    expect(JSON.parse(String(xlsxEnv.graphRequests[1].body))).toMatchObject({
      type: "document",
      document: { id: "media-id", filename: "vendas.xlsx" },
    });
  });

  it("rejects document MIME or magic mismatches before quota reservation", async () => {
    const invalid = outboundImageEnvironment();
    const form = await imageForm(
      { machine_id: "machine", artifact_type: "report_pdf" },
      { bytes: TEST_XLSX, mime: "application/pdf", name: "tampered.pdf" },
    );
    const response = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.document/document", {
        method: "POST",
        body: form,
        headers: { authorization: "Bearer bridge-secret" },
      }),
      invalid.env,
      context(),
    );
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ success: false, error: "outbound_document_pdf_signature_invalid" });
    expect(invalid.sqlCalls.some((item) => item.sql.includes("INSERT OR IGNORE INTO outbound_media"))).toBe(false);
    expect(invalid.graphRequests).toHaveLength(0);
  });

  it("renders Mercado Livre approval as the exact four-option tokenized list", async () => {
    const approval = outboundImageEnvironment();
    const token = "ABCDEFGH";
    const response = await worker.fetch(
      new Request("https://example.test/bridge/interactive", {
        method: "POST",
        body: JSON.stringify({
          subject_id: "subject",
          machine_id: "machine",
          fingerprint: "approval:1234567890",
          event_type: "question_approval",
          body: "Resposta sugerida",
          button_label: "Escolher acao",
          options: [
            { id: `ppv_approve:${token}`, title: "Aprovar e enviar", description: "Envia esta resposta" },
            { id: `ppv_correct:${token}`, title: "Corrigir", description: "Oriente a correcao" },
            { id: `ppv_regenerate:${token}`, title: "Gerar outra resposta", description: "Cria outra versao" },
            { id: `ppv_reject:${token}`, title: "Negar", description: "Nao enviar" },
          ],
        }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      approval.env,
      context(),
    );

    expect(response.status).toBe(200);
    expect(await response.clone().json()).toMatchObject({
      success: true,
      status: "sent",
      meta_message_id: "message-id",
      outbound_message_id: "message-id",
    });
    const sent = JSON.parse(String(approval.graphRequests[0].body));
    expect(sent.interactive.type).toBe("list");
    expect(sent.interactive.action.sections[0].rows.map((item: any) => item.title)).toEqual([
      "Aprovar e enviar", "Corrigir", "Gerar outra resposta", "Negar",
    ]);
    expect(sent.interactive.action.sections[0].rows.every((item: any) => item.id.endsWith(`:${token}`))).toBe(true);
    const storedContext = approval.sqlCalls.find((item) => item.sql.includes("INSERT INTO outbound_quote_context"));
    expect(storedContext?.values.slice(0, 7)).toEqual([
      "message-id",
      "approval:1234567890",
      "subject",
      "client-a",
      "operator-a",
      "question_approval",
      expect.stringContaining("Resposta sugerida"),
    ]);
    expect(String(storedContext?.values[7] || "")).toContain("Aprovar e enviar");
    expect(JSON.stringify(storedContext?.values || [])).not.toContain(token);
    expect(JSON.stringify(storedContext?.values || [])).not.toContain("ppv_approve");
  });

  it("returns the original outbound message id when an interactive card is duplicated", async () => {
    const duplicate = outboundImageEnvironment({
      interactiveReservation: false,
      interactiveContext: { meta_message_id: "wamid.existing-card" },
    });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/interactive", {
        method: "POST",
        body: JSON.stringify({
          subject_id: "subject",
          machine_id: "machine",
          fingerprint: "approval:duplicate:1234",
          event_type: "question_approval",
          body: "Resposta sugerida",
          options: [
            { id: "ppv_approve:ABCDEFGH", title: "Aprovar e enviar" },
            { id: "ppv_correct:ABCDEFGH", title: "Corrigir" },
            { id: "ppv_regenerate:ABCDEFGH", title: "Gerar outra resposta" },
            { id: "ppv_reject:ABCDEFGH", title: "Negar" },
          ],
        }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      duplicate.env,
      context(),
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      success: true,
      status: "duplicate",
      meta_message_id: "wamid.existing-card",
      outbound_message_id: "wamid.existing-card",
    });
    expect(duplicate.graphRequests).toHaveLength(0);
  });

  it("sends the official read and typing payload for the leased message", async () => {
    const { env: typingEnv, graphRequests, sqlCalls } = typingEnvironment();
    const response = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.typing/typing", {
        method: "POST",
        body: JSON.stringify({ machine_id: "machine" }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      typingEnv,
      context(),
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ success: true, status: "sent" });
    expect(graphRequests).toHaveLength(1);
    expect(graphRequests[0]).toMatchObject({
      url: "https://graph.facebook.com/v25.0/phone-id/messages",
      body: {
        messaging_product: "whatsapp",
        status: "read",
        message_id: "wamid.typing",
        typing_indicator: { type: "text" },
      },
    });
    const renewal = sqlCalls.find((item) => item.sql.includes("UPDATE inbox SET lease_until=?"));
    expect(renewal).toBeTruthy();
    expect(Number(renewal?.values[0] || 0)).toBeGreaterThan(Math.floor(Date.now() / 1000) + 590);
    expect(renewal?.values.slice(1)).toEqual(["wamid.typing", "machine"]);
  });

  it("blocks typing for another machine or an inactive message", async () => {
    const wrongMachine = typingEnvironment({ machineId: "other-machine" });
    const denied = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.typing/typing", {
        method: "POST",
        body: JSON.stringify({ machine_id: "machine" }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      wrongMachine.env,
      context(),
    );
    expect(denied.status).toBe(403);
    expect(wrongMachine.graphRequests).toHaveLength(0);

    const inactive = typingEnvironment({ status: "completed" });
    const stopped = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.typing/typing", {
        method: "POST",
        body: JSON.stringify({ machine_id: "machine" }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      inactive.env,
      context(),
    );
    expect(await stopped.json()).toMatchObject({ success: true, status: "not_active" });
    expect(inactive.graphRequests).toHaveLength(0);
  });

  it("does not deliver task progress after the inbound message is terminal", async () => {
    const inactive = typingEnvironment({ status: "completed" });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.typing/progress", {
        method: "POST",
        body: JSON.stringify({
          machine_id: "machine",
          task_id: "task-1",
          sequence: 1,
          stage: "consultando",
          text: "Estou consultando os dados autorizados.",
          fingerprint: "progress-task-1-1",
        }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      inactive.env,
      context(),
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ success: true, status: "not_active" });
    expect(inactive.graphRequests).toHaveLength(0);
  });

  it("throttles typing pulses closer than fifteen seconds", async () => {
    const { env: typingEnv, graphRequests } = typingEnvironment({ typingLastAt: Math.floor(Date.now() / 1000) });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.typing/typing", {
        method: "POST",
        body: JSON.stringify({ machine_id: "machine" }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      typingEnv,
      context(),
    );
    expect(await response.json()).toMatchObject({ success: true, status: "too_soon" });
    expect(graphRequests).toHaveLength(0);
  });

  it("fails closed when the zero-cost policy is expired", async () => {
    const { env: typingEnv, graphRequests } = typingEnvironment({ policyUntil: "2026-01-01T00:00:00Z" });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.typing/typing", {
        method: "POST",
        body: JSON.stringify({ machine_id: "machine" }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      typingEnv,
      context(),
    );
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ success: false, status: "policy_recheck_required" });
    expect(graphRequests).toHaveLength(0);
  });

  it("stops only the indicator when the daily pulse budget is exhausted", async () => {
    const { env: typingEnv, graphRequests } = typingEnvironment({ reserveTyping: false });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.typing/typing", {
        method: "POST",
        body: JSON.stringify({ machine_id: "machine" }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      typingEnv,
      context(),
    );
    expect(await response.json()).toMatchObject({ success: true, status: "daily_limit", limit: 10000 });
    expect(graphRequests).toHaveLength(0);
  });

  it("reports a Meta typing failure without changing the bridge contract", async () => {
    const { env: typingEnv, graphRequests } = typingEnvironment({ graphStatus: 500 });
    const response = await worker.fetch(
      new Request("https://example.test/bridge/messages/wamid.typing/typing", {
        method: "POST",
        body: JSON.stringify({ machine_id: "machine" }),
        headers: { authorization: "Bearer bridge-secret", "content-type": "application/json" },
      }),
      typingEnv,
      context(),
    );
    expect(response.status).toBe(502);
    expect(await response.json()).toMatchObject({ success: false, error: "meta_typing_indicator_failed" });
    expect(graphRequests).toHaveLength(1);
  });
});
