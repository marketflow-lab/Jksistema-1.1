const GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token";
const GOOGLE_SCOPE = "https://www.googleapis.com/auth/cloud-platform";
const SECRET_MANAGER_API = "https://secretmanager.googleapis.com/v1";

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function bearerToken(request) {
  const auth = request.headers.get("authorization") || "";
  if (auth.toLowerCase().startsWith("bearer ")) return auth.slice(7).trim();
  return request.headers.get("x-jk-provisioning-token") || "";
}

function base64Url(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function base64Std(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function decodeBase64Std(value) {
  const binary = atob(String(value || ""));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

function pemToArrayBuffer(pem) {
  const clean = String(pem || "")
    .replace(/-----BEGIN PRIVATE KEY-----/g, "")
    .replace(/-----END PRIVATE KEY-----/g, "")
    .replace(/\s+/g, "");
  const binary = atob(clean);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

async function signJwt(env) {
  const now = Math.floor(Date.now() / 1000);
  const header = { alg: "RS256", typ: "JWT" };
  const claims = {
    iss: env.GCP_CLIENT_EMAIL,
    scope: GOOGLE_SCOPE,
    aud: GOOGLE_TOKEN_URL,
    iat: now,
    exp: now + 3600,
  };
  const encoder = new TextEncoder();
  const unsigned = `${base64Url(encoder.encode(JSON.stringify(header)))}.${base64Url(encoder.encode(JSON.stringify(claims)))}`;
  const key = await crypto.subtle.importKey(
    "pkcs8",
    pemToArrayBuffer(env.GCP_PRIVATE_KEY),
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, encoder.encode(unsigned));
  return `${unsigned}.${base64Url(new Uint8Array(signature))}`;
}

async function googleAccessToken(env) {
  const assertion = await signJwt(env);
  const body = new URLSearchParams({
    grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
    assertion,
  });
  const resp = await fetch(GOOGLE_TOKEN_URL, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
  });
  const data = await resp.json();
  if (!resp.ok || !data.access_token) {
    throw new Error(`Google OAuth recusou a credencial: HTTP ${resp.status}`);
  }
  return data.access_token;
}

function secretResource(env) {
  return `projects/${env.GCP_PROJECT_ID}/secrets/${env.GCP_SECRET_ID}`;
}

async function googleFetch(env, path, init = {}) {
  const token = await googleAccessToken(env);
  const resp = await fetch(`${SECRET_MANAGER_API}/${path}`, {
    ...init,
    headers: {
      ...(init.headers || {}),
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
    },
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.error?.message || `Secret Manager HTTP ${resp.status}`);
  }
  return data;
}

async function readLatestSecrets(env) {
  const data = await googleFetch(env, `${secretResource(env)}/versions/latest:access`);
  const raw = decodeBase64Std(data.payload?.data || "");
  const secrets = JSON.parse(raw || "{}");
  if (!secrets || typeof secrets !== "object" || Array.isArray(secrets)) {
    throw new Error("Secret JSON invalido.");
  }
  const version = String(data.name || "").split("/").pop() || "";
  return { version, secrets };
}

async function addSecretVersion(env, secrets) {
  const bytes = new TextEncoder().encode(JSON.stringify(secrets));
  const data = await googleFetch(env, `${secretResource(env)}:addVersion`, {
    method: "POST",
    body: JSON.stringify({ payload: { data: base64Std(bytes) } }),
  });
  return data.name || "";
}

async function destroyOldVersions(env, keepName) {
  const listed = await googleFetch(env, `${secretResource(env)}/versions`);
  const versions = Array.isArray(listed.versions) ? listed.versions : [];
  await Promise.all(versions.map(async (item) => {
    const name = item.name || "";
    const state = item.state || "";
    if (!name || name === keepName || state === "DESTROYED") return;
    await googleFetch(env, `${name}:destroy`, { method: "POST", body: "{}" });
  }));
}

function cleanSecrets(payload) {
  const allowed = new Set([
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "GEMINI_AGENT_API_KEY",
    "GROQ_API_KEY",
  ]);
  const source = payload && typeof payload === "object" ? payload : {};
  const result = {};
  for (const [rawKey, rawValue] of Object.entries(source)) {
    const key = String(rawKey || "").trim().toUpperCase();
    const value = String(rawValue || "").trim();
    if (allowed.has(key) && value) result[key] = value;
  }
  return result;
}

async function handleDownload(request, env) {
  const token = bearerToken(request);
  if (!env.DOWNLOAD_TOKEN || token !== env.DOWNLOAD_TOKEN) {
    return jsonResponse({ success: false, message: "Nao autorizado." }, 401);
  }
  const { version, secrets } = await readLatestSecrets(env);
  return jsonResponse({ success: true, version, secrets: cleanSecrets(secrets) });
}

async function handleAdminUpdate(request, env) {
  const token = bearerToken(request);
  if (!env.ADMIN_TOKEN || token !== env.ADMIN_TOKEN) {
    return jsonResponse({ success: false, message: "Nao autorizado." }, 401);
  }
  const body = await request.json().catch(() => ({}));
  const secrets = cleanSecrets(body.secrets || body);
  if (!Object.keys(secrets).length) {
    return jsonResponse({ success: false, message: "Nenhuma chave valida recebida." }, 400);
  }
  const keepName = await addSecretVersion(env, secrets);
  await destroyOldVersions(env, keepName);
  return jsonResponse({ success: true, version: keepName.split("/").pop() || "" });
}

export default {
  async fetch(request, env) {
    try {
      const url = new URL(request.url);
      if (request.method === "GET" && url.pathname === "/health") {
        return jsonResponse({ ok: true });
      }
      if (request.method === "POST" && url.pathname === "/download") {
        return handleDownload(request, env);
      }
      if (request.method === "POST" && url.pathname === "/admin/secrets") {
        return handleAdminUpdate(request, env);
      }
      return jsonResponse({ success: false, message: "Rota nao encontrada." }, 404);
    } catch (error) {
      return jsonResponse({ success: false, message: error.message || "Erro interno." }, 500);
    }
  },
};
