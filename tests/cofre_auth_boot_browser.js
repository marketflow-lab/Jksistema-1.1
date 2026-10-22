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
    if (location.pathname !== '/cofre.html') return;
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
    const reply = body => route.fulfill({ status: options.backendDenied ? 403 : options.backendUnauthorized || state.backendUnauthorized ? 401 : 200,
      contentType: 'application/json', body: JSON.stringify(options.backendDenied ? { detail: 'Acesso ao Cofre negado pela API.' }
        : options.backendUnauthorized || state.backendUnauthorized ? { detail: 'A autorização do Cofre expirou ou não está disponível nesta sessão.' } : body) });
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
        mensagens: [{ id: 'fixture-message', author: 'Fixture', content, timestamp: '2026-10-22T12:00:00Z',
          attachments: options.backendAssetUnauthorized ? [{ filename: 'fixture.png', content_type: 'image/png',
            local_url: '/api/cofre/arquivos/fixture-guild/' + 'a'.repeat(64) + '/fixture.png' }] : [] }],
        total: 1, offset: 0, limit: 100, total_ativos: 1, total_excluidos: 0, has_more: false });
      if (options.backendAssetUnauthorized && url.pathname.startsWith('/api/cofre/arquivos/')) return route.fulfill({ status: 401, body: '' });
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
    const restored = await scenario(browser, { backendUnauthorized: true });
    await restored.page.getByRole('button', { name: 'Entrar novamente', exact: true }).waitFor();
    assert.equal(await restored.page.locator('#sharingLabel').textContent(), 'Login necessário');
    assert.equal(await restored.page.locator('#coverageBadge').textContent(), 'Aguardando login');
    assert.equal(await restored.page.locator('#syncSharing').isVisible(), false);
    assert.equal(await restored.page.locator('#emptyImport').isVisible(), false);
    assert.equal(await restored.page.evaluate(() => localStorage.getItem('access_token')), token,
      'A tela não encerra a sessão do JK sem uma ação da pessoa.');
    await assertQuiet(restored);
    assert.deepEqual(restored.state.api.map(request => request.path), ['/api/cofre/compartilhamento'],
      'Uma sessão sem capability encerra a consulta sem repetir pedidos ou declarar acervo vazio.');
    await restored.page.evaluate(() => localStorage.setItem('fixture-preserved', 'saved'));
    await restored.page.getByRole('button', { name: 'Entrar novamente', exact: true }).click();
    await restored.page.waitForURL(origin + '/frontend_index.html');
    assert.deepEqual(await restored.page.evaluate(() => ['access_token', 'user_data', 'permissions'].map(key => localStorage.getItem(key))), [null, null, null]);
    assert.equal(await restored.page.evaluate(() => localStorage.getItem('fixture-preserved')), 'saved');
    await restored.context.close();
    const expired = await scenario(browser);
    await expired.page.getByText(content, { exact: true }).waitFor();
    expired.state.backendUnauthorized = true;
    await expired.page.locator('#syncSharing').click();
    await expired.page.getByRole('button', { name: 'Entrar novamente', exact: true }).waitFor();
    assert.equal(await expired.page.locator('#messages').textContent(), '', 'O acervo sai da tela quando a autorização expira.');
    assert.equal(await expired.page.locator('#emptyTitle').textContent(), 'Entre novamente para abrir o Cofre');
    assert.equal(await expired.page.locator('#sharingLabel').textContent(), 'Login necessário');
    const expiredRequests = expired.state.api.length;
    await assertQuiet(expired);
    assert.equal(expired.state.api.length, expiredRequests, 'Foco e timers não repetem consultas após 401.');
    await expired.context.close();
    const assetExpired = await scenario(browser, { backendAssetUnauthorized: true });
    await assetExpired.page.getByRole('button', { name: 'Entrar novamente', exact: true }).waitFor();
    assert.equal(await assetExpired.page.locator('#messages').textContent(), '', 'Anexo sem autorização também encerra a visualização do acervo.');
    assert.equal(await assetExpired.page.locator('#sharingLabel').textContent(), 'Login necessário');
    const assetRequests = assetExpired.state.api.length;
    assert.equal(assetExpired.state.api.filter(request => request.path.startsWith('/api/cofre/arquivos/')).length, 1);
    await assertQuiet(assetExpired);
    assert.equal(assetExpired.state.api.length, assetRequests, 'Um anexo com 401 não inicia novas tentativas.');
    await assetExpired.context.close();
    await normalFacade(browser, false);
    await normalFacade(browser, true);
    console.log('Cofre auth boot: 11 browser scenarios passed; zero Firebase/global boot requests.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
