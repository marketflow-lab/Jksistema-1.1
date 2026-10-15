'use strict';

const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const repo = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(repo, 'static/vendas/visual-novo-fotos.js'), 'utf8');
const navigation = fs.readFileSync(path.join(repo, 'static/auth/navigation.js'), 'utf8');

function classes() {
  const values = new Set();
  return { add: value => values.add(value), remove: value => values.delete(value), contains: value => values.has(value) };
}
function image(sku = '001', declaredStore = '') {
  const attrs = new Map([['data-vendas-foto', sku]]);
  if (declaredStore) attrs.set('data-vendas-foto-store-id', declaredStore);
  const listeners = new Map();
  const frame = { classList: classes() };
  return {
    hidden: false, classList: classes(), frame,
    matches: () => true, querySelectorAll: () => [], closest: () => frame,
    getAttribute: key => attrs.has(key) ? attrs.get(key) : null,
    setAttribute: (key, value) => attrs.set(key, String(value)), removeAttribute: key => attrs.delete(key),
    addEventListener(type, handler) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(handler); },
    removeEventListener(type, handler) { listeners.get(type)?.delete(handler); },
    fire(type) { [...listeners.get(type) || []].forEach(handler => handler()); },
  };
}
function root(...items) { return { matches: () => false, querySelectorAll: () => items }; }
function reference(storeId = 'store-a', name = '001.png') {
  return `cadastro_fotos/lojas/sid-${crypto.createHash('sha256').update(storeId).digest('hex')}/${name}`;
}
function payload(clientId = 'tenant-a', storeId = 'store-a', photo = reference(storeId)) {
  return { photo_client_id: clientId, rows: [{ sku: '001', store_id: storeId, foto: photo, valor: 123 }], totals: { valor: 123 } };
}
function snapshot(overrides = {}) {
  return { clientId: 'tenant-a', contextKey: 'unused-context-key', loading: false,
    filters: { inicio: '2026-04-01', fim: '2026-10-15', loja: 'Loja A', unidade: '__todos' }, ...overrides };
}
function harness(fetchImpl = async () => ({ ok: true, json: async () => payload() })) {
  const calls = [], scheduled = [], revoked = [];
    let clock = 1000;
  const storage = new Map([['user_data', JSON.stringify({ client_id: 'tenant-a' })]]);
  const context = {
    console, URL, URLSearchParams, AbortController, Date: { now: () => clock },
    location: { href: 'https://jk.local/vendas_nova.html', origin: 'https://jk.local' },
    document: { readyState: 'loading', addEventListener() {}, querySelectorAll: () => [] },
    addEventListener() {},
    localStorage: { getItem: key => storage.get(key) || null },
    obterAuthHeaders: () => ({ Authorization: 'Bearer test-session' }),
    fetch: (...args) => { calls.push(args); return fetchImpl(...args); },
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(navigation.slice(navigation.indexOf('(function instalarMidiaAutenticadaJK')), context);
  context.JKAuthenticatedMedia = { ...context.JKAuthenticatedMedia,
    hidratar: img => scheduled.push(img), liberar() {}, revogarImagem: img => revoked.push(img) };
  vm.runInContext(source, context, { filename: 'visual-novo-fotos.js' });
  return { context, api: context.JKVendasNovaFotos, calls, scheduled, revoked, storage, advance: amount => { clock += amount; } };
}

(async () => {
  {
    const h = harness(), first = image(), second = image(), a = root(first), b = root(second);
    const original = snapshot(), frozen = JSON.stringify(original);
    await Promise.all([h.api.hydrate(a, original), h.api.hydrate(b, original), h.api.hydrate(a, original)]);
    assert.strictEqual(h.calls.length, 1, 'todos os cards e renders devem compartilhar uma única consulta de metadados');
    const [url, options] = h.calls[0];
    assert(url.startsWith('/api/vendas/resumo?'));
    assert(!url.includes('test-session'), 'credenciais não podem ser colocadas na URL');
    assert.strictEqual(options.headers['X-JK-Vendas-Fotos'], '1');
    assert.strictEqual(options.headers.Authorization, 'Bearer test-session');
    assert(!h.calls.some(call => call[0].startsWith('/api/cadastro/')), 'não pode consultar metadados do Cadastro ou baixar fotos antecipadamente');
    assert.strictEqual(JSON.stringify(original), frozen, 'metadados de foto não podem alterar o snapshot financeiro');
    assert.strictEqual(first.getAttribute('src'), null, 'bytes da imagem ficam sob o carregamento lazy compartilhado');
    assert.strictEqual(first.getAttribute('data-jk-auth-src'), `/api/cadastro/foto/tenant-a/lojas/${reference().split('/')[2]}/001.png?store_id=store-a`);
    assert.strictEqual(h.scheduled.length, 2, 'somente as imagens novas devem entrar no observer compartilhado');
    first.fire('load');
    assert(first.frame.classList.contains('vn-photo-ready'));
    assert.strictEqual(first.getAttribute('data-vendas-foto-estado'), 'ready');
    h.api.release(a);
    assert(first.hidden);
    assert(!first.frame.classList.contains('vn-photo-ready'));
    assert.strictEqual(first.getAttribute('data-jk-auth-src'), null);
    h.api.release(b);
  }
  for (const invalid of [
    snapshot({ clientId: '' }), snapshot({ clientId: 'tenant-b' }), snapshot({ loading: true }),
  ]) {
    const h = harness(), img = image();
    await h.api.hydrate(root(img), invalid);
    assert.strictEqual(h.calls.length, 0, 'escopo ausente não deve disparar consulta de fotos');
    assert(img.hidden);
  }
  {
    const h = harness(), img = image();
    await h.api.hydrate(root(img), snapshot({ filters: { inicio: '2026-04-01', fim: '2026-10-15', loja: '__todas' } }));
    assert(!img.hidden, 'a projeção global segura pode entregar uma única loja exata');
    assert(img.getAttribute('data-jk-auth-src').endsWith('store_id=store-a'));
    const empty = harness(async () => ({ ok: true, json: async () => ({ photo_client_id: 'tenant-a', rows: [{ sku: '001', foto: '', store_id: '' }] }) }));
    const missing = image();
    await empty.api.hydrate(root(missing), snapshot({ filters: { inicio: '2026-04-01', fim: '2026-10-15', loja: '__todas' } }));
    assert(missing.hidden, 'a projeção global ambígua deve exibir fallback');
  }
  {
    const h = harness(); h.context.obterAuthHeaders = () => ({});
    const img = image(); await h.api.hydrate(root(img), snapshot());
    assert.strictEqual(h.calls.length, 0);
    assert(img.hidden, 'sem autenticação, exibe o fallback');
  }
  for (const badPayload of [
    payload('tenant-b'),
    { photo_client_id: 'tenant-a', rows: [payload().rows[0], payload('tenant-a', 'store-b').rows[0]] },
    { photo_client_id: 'tenant-a', rows: [payload().rows[0], payload().rows[0]] },
  ]) {
    const h = harness(async () => ({ ok: true, json: async () => badPayload })), img = image();
    await h.api.hydrate(root(img), snapshot());
    assert(img.hidden);
    assert.strictEqual(h.scheduled.length, 0, 'metadados ambíguos não podem originar URL de imagem');
  }
  for (const unsafe of [
    'cadastro_fotos/001.png', 'cadastro_fotos/lojas/store-b/001.png',
    '/api/cadastro/foto/tenant-b/lojas/store-a/001.png', 'cadastro_fotos/lojas/store-a/../001.png',
    'javascript:alert(1)', 'data:image/png;base64,AAAA', 'file:///C:/001.png',
    'https://secret:credential@images.test/001.png', 'https://other.test/api/cadastro/foto/tenant-a/lojas/store-a/001.png',
  ]) {
    const h = harness(async () => ({ ok: true, json: async () => payload('tenant-a', 'store-a', unsafe) })), img = image();
    await h.api.hydrate(root(img), snapshot());
    assert(img.hidden, `referência insegura deve deixar o fallback: ${unsafe}`);
    assert.strictEqual(h.scheduled.length, 0);
  }
  {
    const h = harness(), img = image('001', 'store-b');
    await h.api.hydrate(root(img), snapshot());
    assert(img.hidden, 'a loja declarada na linha não pode ser substituída por outra loja');
  }
  {
    let attempts = 0;
    const h = harness(async () => {
      attempts++;
      if (attempts === 1) throw new Error('Falha temporária');
      return { ok: true, json: async () => payload() };
    });
    const img = image(), owner = root(img);
    await h.api.hydrate(owner, snapshot());
    assert(img.hidden);
    await h.api.hydrate(owner, snapshot());
    assert.strictEqual(h.calls.length, 1, 'uma falha temporária não pode causar uma consulta a cada render');
    h.advance(15001);
    await h.api.hydrate(owner, snapshot());
    assert.strictEqual(h.calls.length, 2, 'após o TTL de falha, uma nova hidratação deve recuperar as fotos');
    assert(!img.hidden);
    h.advance(120001);
    await h.api.hydrate(owner, snapshot());
    assert.strictEqual(h.calls.length, 3, 'metadados válidos também devem ser revalidados após o TTL');
    h.api.release(owner);
  }
  {
    const pending = [];
    const h = harness((url, options) => new Promise(resolve => pending.push({ url, options, resolve })));
    const img = image(), owner = root(img), a = snapshot();
    const first = h.api.hydrate(owner, a);
    const second = h.api.hydrate(owner, snapshot({ filters: { ...a.filters, loja: 'Loja B', unidade: 'Virtual 1' } }));
    assert(pending[0].options.signal.aborted, 'troca de loja deve abortar metadados antigos');
    assert(new URL(pending[1].url, 'https://jk.local').searchParams.get('unidade_negocio') === 'Virtual 1');
    pending[1].resolve({ ok: true, json: async () => payload('tenant-a', 'store-b') });
    await second;
    const freshUrl = img.getAttribute('data-jk-auth-src');
    assert(freshUrl.endsWith('store_id=store-b'));
    pending[0].resolve({ ok: true, json: async () => payload() });
    await first;
    assert.strictEqual(img.getAttribute('data-jk-auth-src'), freshUrl, 'resposta tardia não pode restaurar foto da loja anterior');
    h.api.release(owner);
  }
  {
    const pending = [];
    const h = harness((_url, options) => new Promise(resolve => pending.push({ options, resolve })));
    const a = root(image()), b = root(image());
    const first = h.api.hydrate(a, snapshot()), second = h.api.hydrate(b, snapshot());
    h.api.release(a);
    assert(!pending[0].options.signal.aborted, 'outro proprietário ainda usa a consulta compartilhada');
    h.api.release(b);
    assert(pending[0].options.signal.aborted, 'liberar o último proprietário deve abortar a consulta');
    pending[0].resolve({ ok: true, json: async () => payload() });
    await Promise.all([first, second]);
    assert.strictEqual(h.scheduled.length, 0);
  }
  console.log('vendas nova fotos: tenant/loja, cache, lazy, abort e fallback aprovados.');
})().catch(error => { console.error(error); process.exitCode = 1; });
