'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const PRIVATE_CREDENTIAL_FORMAT = 'jk-private-credentials-v1';
const PRIVATE_CREDENTIAL_AAD = Buffer.from(PRIVATE_CREDENTIAL_FORMAT, 'utf8');
const PRIVATE_CREDENTIAL_MIN_ITERATIONS = 200000;
const PRIVATE_CREDENTIAL_MAX_ITERATIONS = 2000000;
const PRIVATE_CREDENTIAL_MAX_PAYLOAD_BYTES = 4 * 1024 * 1024;
const PRIVATE_CREDENTIAL_MAX_SECRET_CHARS = 64 * 1024;

const PRIVATE_CREDENTIAL_ALLOWED_ENV_KEYS = new Set([
    'BRAVE_SEARCH_API_KEY',
    'DAILY_API_KEY',
    'DATALASTIC_API_KEY',
    'DEEPSEEK_API_KEY',
    'FIREBASE_API_KEY',
    'FIREBASE_APP_ID',
    'FIREBASE_AUTH_DOMAIN',
    'FIREBASE_DATABASE_URL',
    'FIREBASE_PROJECT_ID',
    'FIREBASE_REALTIME_DATABASE_URL',
    'FIREBASE_WEB_API_KEY',
    'FIREBASE_WEB_APP_ID',
    'FIREBASE_WEB_AUTH_DOMAIN',
    'GEMINI_AGENT_API_KEY',
    'GEMINI_API_KEY',
    'GOOGLE_API_KEY',
    'GOOGLE_CLIENT_ID',
    'GOOGLE_CLIENT_SECRET',
    'GOOGLE_GENAI_API_KEY',
    'GOOGLE_LOGIN_CLIENT_ID',
    'GOOGLE_LOGIN_CLIENT_SECRET',
    'GROQ_API_KEY',
    'IA_AGENT_API_KEY',
    'IA_AGENT_ENDPOINT_API_KEY',
    'IA_DEEPSEEK_API_KEY',
    'IA_GROQ_API_KEY',
    'IA_OPENAI_API_KEY',
    'JK_AGENT_ENDPOINT_API_KEY',
    'JK_DAILY_API_KEY',
    'JK_DATALASTIC_API_KEY',
    'JK_FIREBASE_API_KEY',
    'JK_FIREBASE_APP_ID',
    'JK_FIREBASE_AUTH_DOMAIN',
    'JK_FIREBASE_DATABASE_URL',
    'JK_FIREBASE_EXPECTED_PROJECT_ID',
    'JK_FIREBASE_PROJECT_ID',
    'JK_FIREBASE_REALTIME_DATABASE_URL',
    'JK_FIREBASE_WEB_API_KEY',
    'JK_FIREBASE_WEB_APP_ID',
    'OPENAI_API_KEY',
    'OPENAI_PROJECT_ID',
    'SERPAPI_KEY',
    'TAVILY_API_KEY',
    'VERTEX_AGENT_API_KEY',
    'VERTEX_AI_API_KEY',
    'VERTEX_AI_PROJECT_ID'
]);

function privateCredentialError(code, message) {
    const error = new Error(message);
    error.code = code;
    return error;
}

function decodeBase64Field(value, field, expectedBytes = null) {
    const text = String(value || '').trim();
    if (!text || !/^[A-Za-z0-9+/]+={0,2}$/.test(text)) {
        throw privateCredentialError('PRIVATE_BUNDLE_INVALID', `Campo ${field} invalido.`);
    }
    const decoded = Buffer.from(text, 'base64');
    if (!decoded.length || decoded.toString('base64').replace(/=+$/, '') !== text.replace(/=+$/, '')) {
        throw privateCredentialError('PRIVATE_BUNDLE_INVALID', `Campo ${field} invalido.`);
    }
    if (expectedBytes !== null && decoded.length !== expectedBytes) {
        throw privateCredentialError('PRIVATE_BUNDLE_INVALID', `Tamanho invalido em ${field}.`);
    }
    return decoded;
}

function normalizePrivateCredentialTarget(value) {
    const target = String(value || '').trim();
    if (
        target === 'firebase-service-account.json'
        || target === 'credentials.json'
        || /^jkjkjk-[A-Za-z0-9._-]+\.json$/.test(target)
    ) {
        return target;
    }
    throw privateCredentialError('PRIVATE_BUNDLE_INVALID', 'Destino de credencial nao permitido.');
}

function validateServiceAccountBytes(raw, target) {
    let payload;
    try {
        payload = JSON.parse(raw.toString('utf8'));
    } catch (_err) {
        throw privateCredentialError('PRIVATE_BUNDLE_INVALID', `Credencial JSON invalida: ${target}.`);
    }
    if (
        !payload
        || payload.type !== 'service_account'
        || !String(payload.project_id || '').trim()
        || !String(payload.client_email || '').trim().endsWith('.gserviceaccount.com')
        || !String(payload.private_key || '').includes('-----BEGIN PRIVATE KEY-----')
        || !String(payload.private_key || '').includes('-----END PRIVATE KEY-----')
    ) {
        throw privateCredentialError('PRIVATE_BUNDLE_INVALID', `Service account invalida: ${target}.`);
    }
}

function validatePrivateCredentialPayload(payload) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload) || Number(payload.version) !== 1) {
        throw privateCredentialError('PRIVATE_BUNDLE_INVALID', 'Payload privado invalido.');
    }

    const environment = {};
    const sourceEnvironment = payload.environment && typeof payload.environment === 'object'
        ? payload.environment
        : {};
    for (const [rawKey, rawValue] of Object.entries(sourceEnvironment)) {
        const key = String(rawKey || '').trim().toUpperCase();
        const value = String(rawValue || '').trim();
        if (!PRIVATE_CREDENTIAL_ALLOWED_ENV_KEYS.has(key)) {
            throw privateCredentialError('PRIVATE_BUNDLE_INVALID', `Chave de ambiente nao permitida: ${key || 'vazia'}.`);
        }
        if (!value || value.length > PRIVATE_CREDENTIAL_MAX_SECRET_CHARS || /[\0\r\n]/.test(value)) {
            throw privateCredentialError('PRIVATE_BUNDLE_INVALID', `Valor invalido para ${key}.`);
        }
        environment[key] = value;
    }

    const files = [];
    const seenTargets = new Set();
    for (const entry of Array.isArray(payload.files) ? payload.files : []) {
        if (!entry || typeof entry !== 'object') {
            throw privateCredentialError('PRIVATE_BUNDLE_INVALID', 'Entrada de credencial invalida.');
        }
        const target = normalizePrivateCredentialTarget(entry.target);
        if (seenTargets.has(target.toLowerCase())) {
            throw privateCredentialError('PRIVATE_BUNDLE_INVALID', `Destino duplicado: ${target}.`);
        }
        const raw = decodeBase64Field(entry.content_base64, `files.${target}.content_base64`);
        if (raw.length > PRIVATE_CREDENTIAL_MAX_PAYLOAD_BYTES) {
            throw privateCredentialError('PRIVATE_BUNDLE_INVALID', `Credencial excede o limite: ${target}.`);
        }
        const digest = crypto.createHash('sha256').update(raw).digest('hex');
        if (!/^[a-f0-9]{64}$/.test(String(entry.sha256 || '')) || digest !== String(entry.sha256)) {
            throw privateCredentialError('PRIVATE_BUNDLE_INVALID', `Hash divergente: ${target}.`);
        }
        validateServiceAccountBytes(raw, target);
        seenTargets.add(target.toLowerCase());
        files.push({ target, raw, sha256: digest });
    }

    if (!Object.keys(environment).length && !files.length) {
        throw privateCredentialError('PRIVATE_BUNDLE_INVALID', 'O cofre privado esta vazio.');
    }
    return { version: 1, environment, files };
}

function decryptPrivateCredentialBundle(bundle, password) {
    if (!bundle || typeof bundle !== 'object' || Array.isArray(bundle) || bundle.format !== PRIVATE_CREDENTIAL_FORMAT) {
        throw privateCredentialError('PRIVATE_BUNDLE_INVALID', 'Formato do cofre privado invalido.');
    }
    const passwordText = String(password || '');
    if (passwordText.length < 16 || passwordText.length > 512 || /[\0\r\n]/.test(passwordText)) {
        throw privateCredentialError('PRIVATE_BUNDLE_PASSWORD_INVALID', 'A senha privada deve ter entre 16 e 512 caracteres.');
    }
    const iterations = Number(bundle.kdf && bundle.kdf.iterations);
    if (!Number.isInteger(iterations) || iterations < PRIVATE_CREDENTIAL_MIN_ITERATIONS || iterations > PRIVATE_CREDENTIAL_MAX_ITERATIONS) {
        throw privateCredentialError('PRIVATE_BUNDLE_INVALID', 'Parametros de derivacao invalidos.');
    }
    if (String(bundle.kdf && bundle.kdf.name) !== 'pbkdf2-sha256' || String(bundle.cipher && bundle.cipher.name) !== 'aes-256-gcm') {
        throw privateCredentialError('PRIVATE_BUNDLE_INVALID', 'Algoritmos do cofre privado invalidos.');
    }

    const salt = decodeBase64Field(bundle.kdf.salt, 'kdf.salt', 32);
    const iv = decodeBase64Field(bundle.cipher.iv, 'cipher.iv', 12);
    const tag = decodeBase64Field(bundle.cipher.tag, 'cipher.tag', 16);
    const ciphertext = decodeBase64Field(bundle.ciphertext, 'ciphertext');
    if (ciphertext.length > PRIVATE_CREDENTIAL_MAX_PAYLOAD_BYTES) {
        throw privateCredentialError('PRIVATE_BUNDLE_INVALID', 'O cofre privado excede o limite permitido.');
    }

    try {
        const key = crypto.pbkdf2Sync(passwordText, salt, iterations, 32, 'sha256');
        const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
        decipher.setAAD(PRIVATE_CREDENTIAL_AAD);
        decipher.setAuthTag(tag);
        const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
        if (plaintext.length > PRIVATE_CREDENTIAL_MAX_PAYLOAD_BYTES) {
            throw privateCredentialError('PRIVATE_BUNDLE_INVALID', 'Payload privado excede o limite permitido.');
        }
        return validatePrivateCredentialPayload(JSON.parse(plaintext.toString('utf8')));
    } catch (err) {
        if (err && err.code === 'PRIVATE_BUNDLE_INVALID') throw err;
        throw privateCredentialError('PRIVATE_BUNDLE_UNLOCK_FAILED', 'Senha incorreta ou cofre privado corrompido.');
    }
}

function applyPrivateCredentialPayload(payload, runtimeDir, options = {}) {
    const normalized = validatePrivateCredentialPayload({
        version: payload.version,
        environment: payload.environment,
        files: payload.files.map((entry) => ({
            target: entry.target,
            content_base64: entry.raw.toString('base64'),
            sha256: entry.sha256
        }))
    });
    const targetRoot = path.resolve(String(runtimeDir || ''));
    if (!targetRoot) {
        throw privateCredentialError('PRIVATE_BUNDLE_INVALID_TARGET', 'Destino do backend local ausente.');
    }
    const infoDir = path.join(targetRoot, 'info');
    fs.mkdirSync(infoDir, { recursive: true });

    let writtenFiles = 0;
    let preservedFiles = 0;
    for (const entry of normalized.files) {
        const targetPath = path.resolve(infoDir, entry.target);
        if (!targetPath.startsWith(`${path.resolve(infoDir)}${path.sep}`)) {
            throw privateCredentialError('PRIVATE_BUNDLE_INVALID_TARGET', 'Destino privado fora da pasta info.');
        }
        if (fs.existsSync(targetPath)) {
            const existingDigest = crypto.createHash('sha256').update(fs.readFileSync(targetPath)).digest('hex');
            if (existingDigest !== entry.sha256) {
                throw privateCredentialError(
                    'PRIVATE_BUNDLE_CREDENTIAL_CONFLICT',
                    `Ja existe uma credencial diferente em info/${entry.target}; ela foi preservada.`
                );
            }
            preservedFiles += 1;
            continue;
        }
        const tempPath = path.join(infoDir, `.${entry.target}.${process.pid}.${Date.now()}.tmp`);
        fs.writeFileSync(tempPath, entry.raw, { mode: 0o600, flag: 'wx' });
        fs.renameSync(tempPath, targetPath);
        try { fs.chmodSync(targetPath, 0o600); } catch (_err) {}
        writtenFiles += 1;
    }

    const targetEnvironment = options.environment || process.env;
    for (const [key, value] of Object.entries(normalized.environment)) {
        targetEnvironment[key] = value;
    }
    return {
        environmentKeys: Object.keys(normalized.environment).length,
        credentialFiles: normalized.files.length,
        writtenFiles,
        preservedFiles
    };
}

function privateCredentialBundleDigest(raw) {
    return crypto.createHash('sha256').update(raw).digest('hex');
}

module.exports = {
    PRIVATE_CREDENTIAL_AAD,
    PRIVATE_CREDENTIAL_ALLOWED_ENV_KEYS,
    PRIVATE_CREDENTIAL_FORMAT,
    applyPrivateCredentialPayload,
    decryptPrivateCredentialBundle,
    privateCredentialBundleDigest,
    validatePrivateCredentialPayload
};
