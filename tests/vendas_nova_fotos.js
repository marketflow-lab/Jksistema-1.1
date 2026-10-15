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
function tenantPayload({ clientId = 'tenant-a', storeId = '', photo = '/api/cadastro/foto/tenant-a/001.png?tenant_only=true', scope = 'tenant' } = {}) {
  const result = payload(clientId, storeId, photo);
  result.rows[0].photo_scope = scope;
  return result;
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
  for (const scenario of [
    { name: 'foto global explícita em Todas as lojas', data: tenantPayload(),
      state: snapshot({ filters: { ...snapshot().filters, loja: '__todas' } }) },
    { name: 'foto global explícita em uma loja sem store_id de origem', data: tenantPayload(), state: snapshot() },
    { name: 'foto global explícita com store_id de origem', data: tenantPayload({ storeId: 'store-a' }),
      state: snapshot({ storeId: 'store-a' }), declaredStore: 'store-a' },
    { name: 'URL absoluta local e nome codificado',
      data: tenantPayload({ photo: 'https://jk.local/api/cadastro/foto/tenant-a/001%20frente.JPEG?tenant_only=true&store_id=ignorado' }),
      state: snapshot(), expected: '/api/cadastro/foto/tenant-a/001%20frente.JPEG?tenant_only=true' },
  ]) {
    const frozen = JSON.stringify(scenario.data), original = JSON.stringify(scenario.state);
    const h = harness(async () => ({ ok: true, json: async () => scenario.data }));
    const img = image('001', scenario.declaredStore), owner = root(img);
    await h.api.hydrate(owner, scenario.state);
    assert(!img.hidden, scenario.name);
    assert.strictEqual(img.getAttribute('data-jk-auth-src'), scenario.expected || '/api/cadastro/foto/tenant-a/001.png?tenant_only=true');
    assert.strictEqual(img.getAttribute('data-vendas-foto-resolved-store-id'), scenario.data.rows[0].store_id);
    assert.strictEqual(img.getAttribute('src'), null, 'foto global também deve usar o loader lazy autenticado');
    assert.strictEqual(h.scheduled.length, 1);
    assert.strictEqual(h.calls.length, 1);
    assert.strictEqual(h.calls[0][1].headers['X-JK-Vendas-Fotos'], '1');
    assert.strictEqual(JSON.stringify(scenario.data), frozen, 'a resposta financeira deve permanecer intacta');
    assert.strictEqual(JSON.stringify(scenario.state), original, 'o snapshot financeiro deve permanecer intacto');
    img.fire('load');
    assert.strictEqual(img.getAttribute('data-vendas-foto-estado'), 'ready');
    assert(img.frame.classList.contains('vn-photo-ready'));
    await h.api.hydrate(owner, scenario.state);
    assert.strictEqual(h.calls.length, 1, 'foto global deve compartilhar o mesmo cache de metadados');
    assert.strictEqual(h.scheduled.length, 1);
    h.api.release(owner);
    assert(img.hidden);
    assert.strictEqual(img.getAttribute('data-jk-auth-src'), null);
    assert(!img.frame.classList.contains('vn-photo-ready'));
  }
  for (const scenario of [
    { name: 'escopo tenant ausente', data: tenantPayload({ scope: null }) },
    { name: 'escopo tenant não explícito', data: tenantPayload({ scope: 'global' }) },
    { name: 'escopo tenant com capitalização diferente', data: tenantPayload({ scope: 'Tenant' }) },
    { name: 'tenant da resposta diferente', data: tenantPayload({ clientId: 'tenant-b' }) },
    { name: 'metadados sem foto', data: tenantPayload({ photo: '' }) },
    { name: 'URL externa sem store_id', data: payload('tenant-a', '', 'https://images.test/001.png') },
    { name: 'store_id incompatível com contexto', data: tenantPayload({ storeId: 'store-b' }), state: snapshot({ storeId: 'store-a' }) },
    { name: 'store_id incompatível com linha', data: tenantPayload({ storeId: 'store-a' }), declaredStore: 'store-b' },
    { name: 'store_id vazio não comprova loja declarada', data: tenantPayload(), declaredStore: 'store-a' },
    { name: 'SKU duplicado em escopos diferentes', data: { photo_client_id: 'tenant-a', rows: [tenantPayload().rows[0], payload().rows[0]] } },
  ]) {
    const h = harness(async () => ({ ok: true, json: async () => scenario.data })), img = image('001', scenario.declaredStore);
    await h.api.hydrate(root(img), scenario.state || snapshot());
    assert(img.hidden, scenario.name);
    assert.strictEqual(h.scheduled.length, 0, scenario.name);
    assert.strictEqual(img.getAttribute('data-jk-auth-src'), null, scenario.name);
  }
  for (const ambiguous of [true, false, 'true', null]) {
    const data = { photo_client_id: 'tenant-a', rows: [{ sku: '001', foto: '', store_id: '', photo_ambiguous: ambiguous, photo_store_name: 'Nome sem foto' }] };
    const h = harness(async () => ({ ok: true, json: async () => data })), img = image(), owner = root(img);
    img.setAttribute('title', 'Origem anterior');
    await h.api.hydrate(owner, snapshot());
    assert(img.hidden);
    assert.strictEqual(img.getAttribute('data-vendas-foto-ambiguo'), ambiguous === true ? 'true' : null, 'somente o booleano explícito pode sinalizar ambiguidade');
    assert.strictEqual(img.getAttribute('data-jk-auth-src'), null);
    assert.strictEqual(img.getAttribute('title'), null, 'ausência de foto não deve declarar uma loja de origem');
    assert.strictEqual(h.scheduled.length, 0);
    await h.api.hydrate(owner, snapshot({ loading: true }));
    assert.strictEqual(img.getAttribute('data-vendas-foto-ambiguo'), null, 'carregamento de outro contexto deve limpar a ambiguidade');
    await h.api.hydrate(owner, snapshot());
    h.api.release(owner);
    assert.strictEqual(img.getAttribute('data-vendas-foto-ambiguo'), null, 'release deve limpar o sinal de ambiguidade');
    assert.strictEqual(img.getAttribute('title'), null);
  }
  {
    const data = payload(), name = 'Loja <img src=x onerror="alert(1)"> & "Principal"';
    Object.assign(data.rows[0], { photo_store_name: name, photo_ambiguous: true });
    const h = harness(async () => ({ ok: true, json: async () => data })), img = image(), owner = root(img);
    await h.api.hydrate(owner, snapshot());
    assert.strictEqual(img.getAttribute('title'), `Foto cadastrada em ${name}`, 'proveniência deve ser texto literal do atributo');
    assert.strictEqual(img.getAttribute('data-vendas-foto-ambiguo'), null, 'uma URL válida não deve sinalizar ambiguidade');
    img.fire('error');
    assert.strictEqual(img.getAttribute('title'), null, 'falha de imagem deve limpar a proveniência');
    h.api.release(owner);
    assert.strictEqual(img.getAttribute('title'), null);
  }
  {
    const data = tenantPayload();
    data.rows[0].photo_store_name = 'Nome não scoped';
    const h = harness(async () => ({ ok: true, json: async () => data })), img = image();
    await h.api.hydrate(root(img), snapshot());
    assert.strictEqual(img.getAttribute('title'), null, 'foto do tenant não deve receber proveniência de loja');
  }
  for (const unsafe of [
    'cadastro_fotos/001.png', '/api/cadastro/foto-arquivo/001.png',
    '/api/cadastro/foto/tenant-b/001.png?tenant_only=true', '/api/cadastro/foto/tenant-a/lojas/store-a/001.png',
    '/api/cadastro/foto/tenant-a/../tenant-a/001.png', '/api/cadastro/foto/tenant-a/%2e%2e/tenant-a/001.png',
    '/api/cadastro/foto/tenant-a%2fother/001.png', '/api/cadastro/foto/tenant-a/%2e%2e%2f001.png',
    '/api/cadastro/foto/tenant-a/%252e%252e%252f001.png', '/api/cadastro/foto/tenant-a//001.png',
    '/api/cadastro/foto/tenant-a/001.svg', '/api/cadastro/foto/tenant-a/001.html',
    '/api/cadastro/foto/tenant-a/001%00.png', '/api/cadastro/foto/tenant-a/001%ZZ.png',
    'https://other.test/api/cadastro/foto/tenant-a/001.png',
    'https://secret:credential@jk.local/api/cadastro/foto/tenant-a/001.png',
    'https://images.test/001.png', '//jk.local/api/cadastro/foto/tenant-a/001.png',
    'javascript:alert(1)', 'data:image/png;base64,AAAA', 'file:///C:/001.png',
  ]) {
    for (const storeId of ['', 'store-a']) {
      const h = harness(async () => ({ ok: true, json: async () => tenantPayload({ photo: unsafe, storeId }) })), img = image();
      await h.api.hydrate(root(img), snapshot());
      assert(img.hidden, `escopo tenant não pode aceitar referência insegura (${storeId || 'sem loja'}): ${unsafe}`);
      assert.strictEqual(h.scheduled.length, 0);
    }
  }
  {
    const h = harness(async () => ({ ok: true, json: async () => payload('tenant-a', 'store-a', 'https://images.test/001.png') })), img = image();
    await h.api.hydrate(root(img), snapshot());
    assert.strictEqual(img.getAttribute('data-jk-auth-src'), 'https://images.test/001.png', 'o contrato scoped existente deve continuar aceitando URL externa com loja comprovada');
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
    const fresh = payload('tenant-a', 'store-b');
    fresh.rows[0].photo_store_name = 'Loja B';
    pending[1].resolve({ ok: true, json: async () => fresh });
    await second;
    const freshUrl = img.getAttribute('data-jk-auth-src');
    assert(freshUrl.endsWith('store_id=store-b'));
    pending[0].resolve({ ok: true, json: async () => ({ photo_client_id: 'tenant-a', rows: [{ sku: '001', store_id: '', foto: '', photo_ambiguous: true }] }) });
    await first;
    assert.strictEqual(img.getAttribute('data-jk-auth-src'), freshUrl, 'resposta tardia não pode restaurar foto da loja anterior');
    assert.strictEqual(img.getAttribute('data-vendas-foto-ambiguo'), null, 'resposta tardia não pode restaurar o aviso de outra loja');
    assert.strictEqual(img.getAttribute('title'), 'Foto cadastrada em Loja B');
    h.api.release(owner);
    assert.strictEqual(img.getAttribute('title'), null, 'release deve limpar a loja de origem');
  }
  {
    const pending = [];
    const h = harness((_url, options) => new Promise(resolve => pending.push({ options, resolve })));
    const img = image(), owner = root(img);
    const first = h.api.hydrate(owner, snapshot());
    h.storage.set('user_data', JSON.stringify({ client_id: 'tenant-b' }));
    const second = h.api.hydrate(owner, snapshot({ clientId: 'tenant-b' }));
    assert(pending[0].options.signal.aborted, 'trocar tenant deve cancelar a consulta global anterior');
    pending[1].resolve({ ok: true, json: async () => tenantPayload({ clientId: 'tenant-b', photo: '/api/cadastro/foto/tenant-b/001.png?tenant_only=true' }) });
    await second;
    const freshUrl = img.getAttribute('data-jk-auth-src');
    assert.strictEqual(freshUrl, '/api/cadastro/foto/tenant-b/001.png?tenant_only=true');
    pending[0].resolve({ ok: true, json: async () => tenantPayload() });
    await first;
    assert.strictEqual(img.getAttribute('data-jk-auth-src'), freshUrl, 'resposta global tardia não pode restaurar a foto de outro tenant');
    assert.strictEqual(h.scheduled.length, 1);
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
  console.log('vendas nova fotos: escopos tenant/loja, URLs canônicas, cache, lazy, abort e fallback aprovados.');
})().catch(error => { console.error(error); process.exitCode = 1; });
