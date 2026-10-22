'use strict';

// Real Cofre HTML, auth facade, auth helpers and Cofre UI; every request is intercepted.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const origin = 'https://cofre-boot.test';
const token = 'fixture-auth-boot-token';
const cofreChunks = ['session.js', 'navigation.js', 'guards.js'];
const normalChunks = ['session.js', 'navigation.js', 'shared-sync-boot.js', 'guards.js', 'permissions.js', 'global-ui.js'];
const content = 'Informação fictícia carregada com a sessão do Cofre.';
const server = { id: 'fixture-guild', name: 'Acervo fictício', channel_count: 1, message_count: 1, attachment_count: 0 };
const channel = { id: 'fixture-channel', name: 'Guias', message_count: 1 };
const sharing = { success: true, configured: true, enabled: true, can_publish: false, revision: 1, last_sync: '2026-10-22T12:00:00Z' };

function source(relative) { return fs.readFileSync(path.join(root, relative), 'utf8'); }
function assertMirrors() {
  assert.equal(source('auth.js'), source('static/auth.js'), 'As duas fachadas de auth devem permanecer iguais.');
  assert.equal(source('cofre.html'), source('static/cofre.html'), 'Os dois HTML do Cofre devem permanecer iguais.');
}

async function scenario(browser, options = {}) {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  await page.clock.install({ time: new Date('2026-10-22T12:00:00Z') });
  const state = { requests: [], chunks: [], forbidden: [], errors: [], api: [] };
  page.on('pageerror', error => state.errors.push(error.message));
  await page.addInitScript(({ token, permissions, identity }) => {
    if (identity) {
      localStorage.setItem('access_token', token);
      localStorage.setItem('user_data', JSON.stringify({ client_id: 'fixture-company', username: 'fixture-user' }));
    }
    localStorage.setItem('permissions', JSON.stringify(permissions));
  }, { token, permissions: options.permissions || { cofre: true }, identity: options.identity !== false });

  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    state.requests.push(url.pathname);
    const reply = body => route.fulfill({ status: options.backendDenied ? 403 : 200,
      contentType: 'application/json', body: JSON.stringify(options.backendDenied ? { detail: 'Acesso ao Cofre negado pela API.' } : body) });
    if (url.origin !== origin) { state.forbidden.push(url.origin + url.pathname); return route.abort(); }
    if (url.pathname === '/cofre.html') {
      let html = source(options.mirror ? 'static/cofre.html' : 'cofre.html');
      if (options.dynamic) html = html.replace(/<script\b[^>]*src="\/(?:auth\.js|cofre\/cofre\.js)[^"]*"[^>]*><\/script>/g, '');
      return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
    }
    if (url.pathname === '/auth.js') return route.fulfill({ contentType: 'application/javascript', body: source(options.mirror ? 'static/auth.js' : 'auth.js') });
    if (url.pathname.startsWith('/auth/')) {
      const file = url.pathname.slice('/auth/'.length);
      state.chunks.push(file);
      if (!cofreChunks.includes(file)) { state.forbidden.push(url.pathname); return route.abort(); }
      return route.fulfill({ contentType: 'application/javascript', body: source('static/auth/' + file) });
    }
    if (url.pathname === '/cofre/cofre.js' || url.pathname === '/cofre/cofre.css') {
      return route.fulfill({ contentType: url.pathname.endsWith('.js') ? 'application/javascript' : 'text/css', body: source('static' + url.pathname) });
    }
    if (url.pathname.startsWith('/api/cofre/')) {
      state.api.push({ path: url.pathname, authorization: request.headers().authorization, method: request.method() });
      assert.equal(request.headers().authorization, 'Bearer ' + token, 'A API preserva Authorization de sessão.');
      if (url.pathname === '/api/cofre/compartilhamento') return reply(sharing);
      if (url.pathname === '/api/cofre/compartilhamento/sincronizar') return reply({ ...sharing, changed: false });
      if (url.pathname === '/api/cofre/servidores') return reply({ servidores: [server] });
      if (url.pathname === '/api/cofre/servidores/fixture-guild/canais') return reply({ servidor: server, canais: [channel] });
      if (url.pathname === '/api/cofre/canais/fixture-channel/mensagens') return reply({
        mensagens: [{ id: 'fixture-message', author: 'Fixture', content, timestamp: '2026-10-22T12:00:00Z' }],
        total: 1, offset: 0, limit: 100, total_ativos: 1, total_excluidos: 0, has_more: false });
    }
    if (['/dashboard.html', '/frontend_index.html'].includes(url.pathname)) return route.fulfill({ contentType: 'text/html', body: '<main>Destino fictício</main>' });
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
    // Includes Firebase scripts/RTDB, Shared Sync, heartbeat, RAG, SSE/messages and Vendas.
    state.forbidden.push(url.pathname);
    return route.abort();
  });

  await page.goto(origin + '/cofre.html');
  if (options.dynamic) {
    await page.addScriptTag({ url: origin + '/auth.js' });
    await page.evaluate(() => window.__JK_AUTH_READY__);
    await page.addScriptTag({ url: origin + '/cofre/cofre.js' });
  }
  return { context, page, state };
}

async function assertQuiet(result) {
  await result.page.clock.fastForward('03:00');
  await result.page.evaluate(() => { window.dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange')); });
  await result.page.waitForTimeout(50);
  assert.deepEqual(result.state.chunks, cofreChunks);
  assert.deepEqual(result.state.forbidden, [], 'Cofre não inicia outros módulos nem consulta Firebase.');
  assert.deepEqual(result.state.errors, []);
  assert(result.state.api.every(request => request.authorization === 'Bearer ' + token));
}

async function normalFacade(browser, dynamic) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const chunks = [];
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/normal.html') return route.fulfill({ contentType: 'text/html', body:
      '<!doctype html><html><head>' + (dynamic ? '' : '<script src="/auth.js"></script>') + '</head><body>Fixture</body></html>' });
    if (url.pathname === '/auth.js') return route.fulfill({ contentType: 'application/javascript', body: source('auth.js') });
    if (url.pathname.startsWith('/auth/')) {
      chunks.push(url.pathname.slice('/auth/'.length));
      return route.fulfill({ contentType: 'application/javascript', body: 'window.fixtureChunkCount=(window.fixtureChunkCount||0)+1;' });
    }
    return route.fulfill({ status: 204 });
  });
  await page.goto(origin + '/normal.html');
  if (dynamic) { await page.addScriptTag({ url: origin + '/auth.js' }); await page.evaluate(() => window.__JK_AUTH_READY__); }
  assert.deepEqual(chunks, normalChunks, 'Outros módulos preservam todos os boots e a ordem original.');
  assert.equal(await page.evaluate(() => window.fixtureChunkCount), 6);
  await context.close();
}

(async () => {
  assertMirrors();
  const browser = await chromium.launch({ headless: true });
  try {
    for (const options of [{}, { mirror: true, permissions: { full: true } }, { dynamic: true }]) {
      const result = await scenario(browser, options);
      await result.page.getByText(content, { exact: true }).waitFor();
      const auth = await result.page.evaluate(() => ({ valid: verificarSessao(), headers: obterAuthHeaders({ 'X-Fixture': 'preserved' }) }));
      assert.equal(auth.valid, true);
      assert.deepEqual(auth.headers, { Authorization: 'Bearer ' + token, 'X-Fixture': 'preserved' });
      await assertQuiet(result);
      await result.context.close();
    }
    for (const [options, target] of [[{ permissions: {} }, '/dashboard.html'], [{ identity: false }, '/frontend_index.html']]) {
      const result = await scenario(browser, options);
      await result.page.waitForURL(origin + target);
      assert.equal(result.state.api.length, 0, 'Sessão/permissão ausente bloqueia a consulta antes da API.');
      assert.deepEqual(result.state.chunks, cofreChunks);
      assert.deepEqual(result.state.forbidden, []);
      assert.deepEqual(result.state.errors, []);
      await result.context.close();
    }
    const denied = await scenario(browser, { permissions: { full: true }, backendDenied: true });
    await denied.page.getByText('Acesso ao Cofre negado pela API.', { exact: true }).first().waitFor();
    assert.equal(await denied.page.getByText(content, { exact: true }).count(), 0, 'Permissão local não contorna a decisão da API.');
    await assertQuiet(denied);
    await denied.context.close();
    await normalFacade(browser, false);
    await normalFacade(browser, true);
    console.log('Cofre auth boot: 8 browser scenarios passed; zero Firebase/global boot requests.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
