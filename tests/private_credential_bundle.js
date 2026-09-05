'use strict';

const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');

const core = require('../electron_app/main/modules/private-credential-bootstrap-core.js');

const password = 'senha-sintetica-de-teste-com-32-caracteres';
const serviceAccount = {
  type: 'service_account',
  project_id: 'projeto-sintetico',
  private_key_id: 'unit-test-key',
  private_key: '-----BEGIN PRIVATE KEY-----\nSYNTHETIC_TEST_ONLY\n-----END PRIVATE KEY-----\n',
  client_email: 'firebase-adminsdk@projeto-sintetico.iam.gserviceaccount.com'
};
const serviceAccountRaw = Buffer.from(JSON.stringify(serviceAccount), 'utf8');
const payload = {
  version: 1,
  environment: {
    FIREBASE_PROJECT_ID: 'projeto-sintetico',
    OPENAI_API_KEY: 'synthetic-openai-key-never-real'
  },
  files: [{
    target: 'firebase-service-account.json',
    content_base64: serviceAccountRaw.toString('base64'),
    sha256: crypto.createHash('sha256').update(serviceAccountRaw).digest('hex')
  }]
};

function encryptForTest(value, secret) {
  const salt = crypto.randomBytes(32);
  const iv = crypto.randomBytes(12);
  const key = crypto.pbkdf2Sync(secret, salt, 200000, 32, 'sha256');
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  cipher.setAAD(core.PRIVATE_CREDENTIAL_AAD);
  const plaintext = Buffer.from(JSON.stringify(value), 'utf8');
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  return {
    format: core.PRIVATE_CREDENTIAL_FORMAT,
    kdf: { name: 'pbkdf2-sha256', iterations: 200000, salt: salt.toString('base64') },
    cipher: { name: 'aes-256-gcm', iv: iv.toString('base64'), tag: cipher.getAuthTag().toString('base64') },
    ciphertext: ciphertext.toString('base64')
  };
}

const bundle = encryptForTest(payload, password);
const serializedBundle = JSON.stringify(bundle);
assert(!serializedBundle.includes('synthetic-openai-key-never-real'));
assert(!serializedBundle.includes('SYNTHETIC_TEST_ONLY'));

const decrypted = core.decryptPrivateCredentialBundle(bundle, password);
assert.strictEqual(decrypted.environment.OPENAI_API_KEY, 'synthetic-openai-key-never-real');
assert.strictEqual(decrypted.files.length, 1);
assert.throws(
  () => core.decryptPrivateCredentialBundle(bundle, 'senha-incorreta-com-tamanho-suficiente'),
  error => error && error.code === 'PRIVATE_BUNDLE_UNLOCK_FAILED'
);

const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-private-bundle-'));
try {
  const targetEnvironment = {};
  const first = core.applyPrivateCredentialPayload(decrypted, tempRoot, { environment: targetEnvironment });
  assert.deepStrictEqual(first, {
    environmentKeys: 2,
    credentialFiles: 1,
    writtenFiles: 1,
    preservedFiles: 0
  });
  assert.strictEqual(targetEnvironment.OPENAI_API_KEY, 'synthetic-openai-key-never-real');
  assert.deepStrictEqual(
    JSON.parse(fs.readFileSync(path.join(tempRoot, 'info', 'firebase-service-account.json'), 'utf8')),
    serviceAccount
  );

  const second = core.applyPrivateCredentialPayload(decrypted, tempRoot, { environment: {} });
  assert.strictEqual(second.writtenFiles, 0);
  assert.strictEqual(second.preservedFiles, 1);

  fs.writeFileSync(path.join(tempRoot, 'info', 'firebase-service-account.json'), '{}', 'utf8');
  assert.throws(
    () => core.applyPrivateCredentialPayload(decrypted, tempRoot, { environment: {} }),
    error => error && error.code === 'PRIVATE_BUNDLE_CREDENTIAL_CONFLICT'
  );
} finally {
  fs.rmSync(tempRoot, { recursive: true, force: true });
}

assert.throws(
  () => core.validatePrivateCredentialPayload({
    version: 1,
    environment: { ACCESS_TOKEN: 'nao-pode-entrar' },
    files: []
  }),
  error => error && error.code === 'PRIVATE_BUNDLE_INVALID'
);

const mainSource = fs.readFileSync(path.join(__dirname, '..', 'electron_app', 'main', 'modules', 'private-credential-bootstrap.js'), 'utf8');
const preloadSource = fs.readFileSync(path.join(__dirname, '..', 'private_bootstrap_preload.js'), 'utf8');
assert(mainSource.includes('nodeIntegration: false'));
assert(mainSource.includes('contextIsolation: true'));
assert(mainSource.includes('sandbox: true'));
assert(!preloadSource.includes('sendSync'));
assert(!preloadSource.includes('invoke('));

console.log('private credential bundle: OK');
