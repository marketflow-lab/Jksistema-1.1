import { isFreeWindowOpen, isPolicyValid, sha256Hex } from "./core";
import { audit, Env, intEnv, json, JsonRecord, nowSeconds, requestJson } from "./shared";

export const MAX_BINDINGS_PER_USER = 3;

export async function pairingAttemptsAllowed(env: Env, subjectId: string): Promise<boolean> {
  const since = nowSeconds() - 3600;
  const row = await env.DB.prepare(
    "SELECT COUNT(*) AS total FROM audit_events WHERE event_type='pairing_failed' AND subject_id=? AND created_at>=?",
  ).bind(subjectId, since).first<{ total: number }>();
  return Number(row?.total || 0) < 5;
}

export async function zeroCostEligibility(env: Env, subjectId: string, allowUnregistered = false): Promise<{ allowed: boolean; reason: string; binding?: JsonRecord }> {
  if (!isPolicyValid(env.ZERO_COST_POLICY_VALID_UNTIL)) return { allowed: false, reason: "policy_recheck_required" };
  const binding = allowUnregistered
    ? await env.DB.prepare("SELECT * FROM bindings WHERE active=1 AND (subject_id=? OR wa_id=? OR phone_number=?) LIMIT 1").bind(subjectId, subjectId, subjectId).first<JsonRecord>()
    : await env.DB.prepare("SELECT * FROM bindings WHERE subject_id=? AND active=1").bind(subjectId).first<JsonRecord>();
  if (!binding && !allowUnregistered) return { allowed: false, reason: "binding_missing" };
  let eligibleBinding: JsonRecord | undefined = binding || undefined;
  if (!eligibleBinding && allowUnregistered) {
    const inbound = await env.DB.prepare(
      "SELECT created_at FROM audit_events WHERE event_type='unpaired_phone_message' AND subject_id=? ORDER BY created_at DESC LIMIT 1",
    ).bind(subjectId).first<JsonRecord>();
    eligibleBinding = {
      subject_id: subjectId,
      wa_id: subjectId,
      phone_number: subjectId,
      last_inbound_at: Number(inbound?.created_at || 0),
    };
  }
  if (!isFreeWindowOpen(Number(eligibleBinding?.last_inbound_at || 0), nowSeconds(), intEnv(env.FREE_WINDOW_SECONDS, 84600))) {
    return { allowed: false, reason: "waiting_free_window", binding: eligibleBinding };
  }
  return { allowed: true, reason: "free_service_window", binding: eligibleBinding };
}

export async function createPairingCode(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const code = String(body.code || "").trim().toUpperCase();
  const clientId = String(body.client_id || "").trim();
  const username = String(body.username || "").trim().toLowerCase();
  const machineId = String(body.machine_id || "").trim();
  if (!/^[A-Z2-9]{8}$/.test(code) || !clientId || !username || !machineId) return json({ success: false, error: "invalid_pairing_payload" }, 400);
  const active = await env.DB.prepare("SELECT COUNT(*) AS total FROM bindings WHERE client_id=? AND username=? AND active=1")
    .bind(clientId, username).first<{ total: number }>();
  const activeBindings = Number(active?.total || 0);
  if (activeBindings >= MAX_BINDINGS_PER_USER) {
    return json({ success: false, error: "binding_limit_reached", limit: MAX_BINDINGS_PER_USER, active_bindings: activeBindings }, 409);
  }
  const hash = await sha256Hex(code);
  const now = nowSeconds();
  await env.DB.prepare("INSERT OR REPLACE INTO pairing_codes(code_hash,client_id,username,machine_id,created_at,expires_at,used_at,used_by_subject) VALUES(?,?,?,?,?,?,NULL,NULL)")
    .bind(hash, clientId, username, machineId, now, now + 600).run();
  await audit(env, "pairing_created", "", { client_id: clientId, username, machine_id: machineId });
  return json({ success: true, expires_at: now + 600, limit: MAX_BINDINGS_PER_USER, active_bindings: activeBindings, remaining_slots: MAX_BINDINGS_PER_USER - activeBindings });
}

export function normalizeRegisteredPhone(value: unknown): string {
  let digits = String(value || "").replace(/\D/g, "");
  if (digits.startsWith("00")) digits = digits.slice(2);
  if (digits.length === 10 || digits.length === 11) digits = `55${digits}`;
  if (digits.length < 10 || digits.length > 15 || /^(\d)\1+$/.test(digits)) return "";
  return digits;
}

export function registeredPhoneAliases(value: unknown): string[] {
  const phone = normalizeRegisteredPhone(value);
  if (!phone) return [];
  const aliases = new Set([phone]);
  if (phone.startsWith("55") && phone.length === 13 && phone[4] === "9") {
    aliases.add(`${phone.slice(0, 4)}${phone.slice(5)}`);
  } else if (phone.startsWith("55") && phone.length === 12) {
    aliases.add(`${phone.slice(0, 4)}9${phone.slice(4)}`);
  }
  return [...aliases];
}

export async function registerBinding(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const phoneNumber = normalizeRegisteredPhone(body.phone_number);
  const clientId = String(body.client_id || "").trim();
  const username = String(body.username || "").trim().toLowerCase();
  const machineId = String(body.machine_id || "").trim();
  if (!phoneNumber || !clientId || !username || !machineId) {
    return json({ success: false, error: phoneNumber ? "invalid_registration_payload" : "invalid_phone_number" }, 400);
  }

  const existing = await env.DB.prepare(
    "SELECT subject_id,client_id,username,machine_id,active,is_primary,last_inbound_at,created_at FROM bindings WHERE subject_id=? OR wa_id=? OR phone_number=? ORDER BY active DESC LIMIT 1",
  ).bind(phoneNumber, phoneNumber, phoneNumber).first<JsonRecord>();
  const existingActive = Number(existing?.active || 0) === 1;
  const sameOwner = String(existing?.client_id || "") === clientId && String(existing?.username || "").toLowerCase() === username;
  if (existingActive && !sameOwner) {
    return json({ success: false, error: "binding_already_registered" }, 409);
  }

  const active = await env.DB.prepare("SELECT COUNT(*) AS total FROM bindings WHERE client_id=? AND username=? AND active=1")
    .bind(clientId, username).first<{ total: number }>();
  const activeBindings = Number(active?.total || 0);
  if (!existingActive && activeBindings >= MAX_BINDINGS_PER_USER) {
    return json({ success: false, error: "binding_limit_reached", limit: MAX_BINDINGS_PER_USER, active_bindings: activeBindings }, 409);
  }

  const now = nowSeconds();
  const subjectId = String(existing?.subject_id || phoneNumber);
  const primaryProvided = Object.prototype.hasOwnProperty.call(body, "is_primary");
  const requestedPrimary = body.is_primary === true;
  const effectivePrimary = primaryProvided ? requestedPrimary : (existingActive && sameOwner && Number(existing?.is_primary || 0) === 1);
  const mutations: D1PreparedStatement[] = [];
  if (effectivePrimary) {
    mutations.push(env.DB.prepare(
      "UPDATE bindings SET is_primary=0 WHERE client_id=? AND username=? AND active=1 AND subject_id<>?",
    ).bind(clientId, username, subjectId));
  }
  if (existing) {
    mutations.push(env.DB.prepare(
      "UPDATE bindings SET wa_id=?,phone_number=?,client_id=?,username=?,machine_id=?,active=1,is_primary=?,revoked_at=NULL WHERE subject_id=?",
    ).bind(phoneNumber, phoneNumber, clientId, username, machineId, effectivePrimary ? 1 : 0, subjectId));
  } else {
    mutations.push(env.DB.prepare(
      "INSERT INTO bindings(subject_id,wa_id,phone_number,client_id,username,machine_id,active,is_primary,last_inbound_at,created_at,revoked_at) VALUES(?,?,?,?,?,?,1,?,NULL,?,NULL)",
    ).bind(subjectId, phoneNumber, phoneNumber, clientId, username, machineId, effectivePrimary ? 1 : 0, now));
  }
  if (mutations.length === 1) await mutations[0].run();
  else await env.DB.batch(mutations);

  const total = existingActive ? activeBindings : activeBindings + 1;
  await audit(env, "binding_registered_directly", subjectId, {
    client_id: clientId,
    username,
    machine_id: machineId,
    active_bindings: total,
  });
  return json({
    success: true,
    created: !existingActive,
    subject_id: subjectId,
    limit: MAX_BINDINGS_PER_USER,
    active_bindings: total,
    remaining_slots: Math.max(0, MAX_BINDINGS_PER_USER - total),
    binding: {
      subject_id: subjectId,
      phone_number: phoneNumber,
      client_id: clientId,
      username,
      machine_id: machineId,
      last_inbound_at: Number(existing?.last_inbound_at || 0),
      created_at: Number(existing?.created_at || now),
      is_primary: effectivePrimary,
    },
  });
}

export async function updatePrimaryBinding(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const subjectId = String(body.subject_id || "").trim();
  const clientId = String(body.client_id || "").trim();
  const username = String(body.username || "").trim().toLowerCase();
  const machineId = String(body.machine_id || "").trim();
  if (!subjectId || !clientId || !username || !machineId || typeof body.is_primary !== "boolean") {
    return json({ success: false, error: "invalid_primary_binding_payload" }, 400);
  }
  const target = await env.DB.prepare(
    "SELECT subject_id,machine_id,is_primary FROM bindings WHERE subject_id=? AND client_id=? AND username=? AND active=1",
  ).bind(subjectId, clientId, username).first<JsonRecord>();
  if (!target) return json({ success: false, error: "binding_missing" }, 404);
  if (String(target.machine_id || "") !== machineId) {
    return json({ success: false, error: "binding_machine_mismatch" }, 403);
  }
  const enabled = body.is_primary === true;
  const previous = Number(target.is_primary || 0) === 1;
  if (enabled) {
    await env.DB.batch([
      env.DB.prepare(
        "UPDATE bindings SET is_primary=0 WHERE client_id=? AND username=? AND active=1 AND subject_id<>?",
      ).bind(clientId, username, subjectId),
      env.DB.prepare(
        "UPDATE bindings SET is_primary=1 WHERE subject_id=? AND client_id=? AND username=? AND machine_id=? AND active=1",
      ).bind(subjectId, clientId, username, machineId),
    ]);
  } else {
    await env.DB.prepare(
      "UPDATE bindings SET is_primary=0 WHERE subject_id=? AND client_id=? AND username=? AND machine_id=? AND active=1",
    ).bind(subjectId, clientId, username, machineId).run();
  }
  const confirmed = await env.DB.prepare(
    "SELECT is_primary FROM bindings WHERE subject_id=? AND client_id=? AND username=? AND machine_id=? AND active=1",
  ).bind(subjectId, clientId, username, machineId).first<JsonRecord>();
  const actual = Number(confirmed?.is_primary || 0) === 1;
  if (actual !== enabled) return json({ success: false, error: "primary_binding_conflict" }, 409);
  await audit(env, "binding_primary_updated", subjectId, {
    client_id: clientId,
    username,
    machine_id: machineId,
    enabled,
    changed: previous !== actual,
  });
  return json({
    success: true,
    applied: true,
    changed: previous !== actual,
    is_primary: actual,
    owner_has_primary: enabled || Boolean(await env.DB.prepare(
      "SELECT 1 AS present FROM bindings WHERE client_id=? AND username=? AND active=1 AND is_primary=1 LIMIT 1",
    ).bind(clientId, username).first()),
  });
}

export async function revokeBinding(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const clientId = String(body.client_id || "").trim();
  const username = String(body.username || "").trim().toLowerCase();
  const subjectId = String(body.subject_id || "").trim();
  const revokeAll = body.revoke_all === true;
  if (!clientId || !username || (!subjectId && !revokeAll)) return json({ success: false, error: "invalid_revoke_payload" }, 400);
  const now = nowSeconds();
  const result = subjectId
    ? await env.DB.prepare("UPDATE bindings SET active=0,is_primary=0,revoked_at=? WHERE subject_id=? AND client_id=? AND username=? AND active=1").bind(now, subjectId, clientId, username).run()
    : await env.DB.prepare("UPDATE bindings SET active=0,is_primary=0,revoked_at=? WHERE client_id=? AND username=? AND active=1").bind(now, clientId, username).run();
  const remaining = await env.DB.prepare("SELECT COUNT(*) AS total FROM bindings WHERE client_id=? AND username=? AND active=1")
    .bind(clientId, username).first<{ total: number }>();
  await audit(env, "binding_revoked", subjectId, { client_id: clientId, username, revoke_all: revokeAll, changed: result.meta.changes, remaining: Number(remaining?.total || 0) });
  return json({ success: true, revoked: result.meta.changes, remaining: Number(remaining?.total || 0) });
}
