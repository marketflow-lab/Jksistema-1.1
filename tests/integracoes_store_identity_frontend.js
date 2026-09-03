const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const served = fs.readFileSync(path.join(root, 'static', 'integracoes.html'), 'utf8');
const mirror = fs.readFileSync(path.join(root, 'integracoes.html'), 'utf8');

assert.strictEqual(mirror, served, 'espelho de integracoes deve seguir static/integracoes.html');
assert.match(
  served,
  /getStore:\s*\(store\)[\s\S]*?\/api\/lojas\/\$\{encodeURIComponent\(store\.nome\)\}\?store_id=\$\{encodeURIComponent\(store\.store_id\)\}/,
  'GET de loja deve enviar o store_id exato',
);
assert.match(
  served,
  /deleteStore:\s*\(store\)[\s\S]*?\/api\/lojas\/\$\{encodeURIComponent\(store\.nome\)\}\?store_id=\$\{encodeURIComponent\(store\.store_id\)\}/,
  'DELETE de loja deve enviar o store_id exato',
);
assert.match(
  served,
  /saveTurbo:\s*\(store, token\)[\s\S]*?turbo\?store_id=\$\{encodeURIComponent\(store\.store_id\)\}/,
  'mutacao do Turbo deve enviar o store_id exato',
);
assert.match(
  served,
  /disconnectIntegration:\s*\(store, servico\)[\s\S]*?\?store_id=\$\{encodeURIComponent\(store\.store_id\)\}/,
  'desconexao deve enviar o store_id exato',
);
assert.strictEqual(
  (served.match(/store_id:\s*selectedStore\.store_id/g) || []).length,
  2,
  'os dois inicios OAuth devem enviar store_id no corpo',
);
assert.match(
  served,
  /li\.dataset\.storeId = store\.store_id[\s\S]*?stores\.find\(item => item\.store_id === storeId\)/,
  'selecao visual deve usar identidade, nao o nome de exibicao',
);

console.log('integracoes store identity frontend: OK');
