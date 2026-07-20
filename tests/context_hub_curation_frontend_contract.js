'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const canonical = fs.readFileSync(path.join(root, 'configuracoes.html'), 'utf8');
const served = fs.readFileSync(path.join(root, 'static', 'configuracoes.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'static', 'configuracoes-context-hub-curation.js'), 'utf8');

assert.strictEqual(served, canonical, 'espelhos da tela de configuracoes divergentes');
for (const id of [
  'contextHubWatcherEnabled',
  'btnContextHubCreateNote',
  'btnContextHubRefreshNotes',
  'btnContextHubPublishCuration',
  'btnContextHubCreateBackup',
  'contextHubBackupPassphrase',
]) {
  assert(canonical.includes(`id="${id}"`), `controle ausente: ${id}`);
}
assert(!canonical.includes('id="contextHubAutoPublish"'));
assert(canonical.includes('auto_publish_enabled: false'));
assert(canonical.includes('watch_enabled: contextHubWatcherEnabled.checked'));

for (const endpoint of [
  '/api/admin/context-hub/curation/notes',
  '/api/admin/context-hub/curation/publish',
  '/api/admin/context-hub/curation/backups',
]) {
  assert(script.includes(endpoint), `endpoint ausente na UI: ${endpoint}`);
}
for (const action of ['validate', 'review', 'approve', 'reject']) {
  assert(script.includes(`runNoteAction(note, '${action}')`), `acao de nota ausente: ${action}`);
}
assert(script.includes("input.value = ''"), 'senha precisa ser removida do input imediatamente');
assert(!/localStorage\.setItem\([^)]*passphrase/i.test(script), 'senha nunca pode ser persistida');
assert(!script.includes('.innerHTML'), 'renderizacao deve usar textContent/DOM seguro');

console.log('context hub curation frontend contract: OK');
