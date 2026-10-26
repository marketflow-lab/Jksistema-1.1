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
  const state = { requests: [], chunks: [], forbidden: [], errors: [], api: [],
    backendUnavailable: !!options.backendUnavailable, backendUnauthorized: false,
    syncUnavailable: !!options.syncUnavailable,
    holdSync: !!options.holdSync, heldSync: null,
    mutationSyncUnavailable: false,
    mutationUnavailable: false, messagesUnavailable: false, redirectMessages: false,
    holdMessages: false, heldMessage: null, messageStarted: false };
  page.on('pageerror', error => state.errors.push(error.message));
  await page.addInitScript(({ token, permissions, identity }) => {
    if (location.pathname !== '/cofre.html') return;
    if (!sessionStorage.getItem('fixture-initialized')) {
      if (identity) {
        localStorage.setItem('access_token', token);
        localStorage.setItem('user_data', JSON.stringify({ client_id: 'fixture-company', username: 'fixture-user' }));
      }
      localStorage.setItem('permissions', JSON.stringify(permissions));
      sessionStorage.setItem('fixture-initialized', 'true');
    }
  }, { token, permissions: options.permissions || { cofre: true }, identity: options.identity !== false });

  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    state.requests.push(url.pathname);
    const unavailable = () => route.fulfill({ status: 503, contentType: 'application/json',
      body: JSON.stringify({ detail: { code: 'cofre_session_unavailable', message: 'Acervo temporariamente indisponível.' } }) });
    const reply = body => state.backendUnavailable ? unavailable() : route.fulfill({ status: options.backendDenied ? 403 : state.backendUnauthorized ? 401 : 200,
      headers: state.backendUnauthorized ? { 'WWW-Authenticate': 'Bearer' } : {},
      contentType: 'application/json', body: JSON.stringify(options.backendDenied ? { detail: 'Acesso ao Cofre negado pela API.' }
        : state.backendUnauthorized ? { detail: 'Token de autenticação inválido.' } : body) });
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
    if (['/cofre/assets/cofre-cover.png', '/cofre/assets/cofre-library.png'].includes(url.pathname)) {
      return route.fulfill({ contentType: 'image/png', body: fs.readFileSync(path.join(root, 'static', url.pathname)) });
    }
    if (url.pathname.startsWith('/api/cofre/')) {
      state.api.push({ path: url.pathname, authorization: request.headers().authorization, method: request.method() });
      assert.equal(request.headers().authorization, 'Bearer ' + token, 'A API preserva Authorization de sessão.');
      if (request.method() === 'PATCH') {
        if (state.mutationSyncUnavailable) return route.fulfill({ status: 503, contentType: 'application/json',
          body: JSON.stringify({ detail: { code: 'cofre_sync_unavailable', message: 'Compartilhamento temporariamente indisponível.' } }) });
        assert(state.mutationUnavailable, 'A fixture não aceita mutações reais.');
        return unavailable();
      }
      if (url.pathname === '/api/cofre/compartilhamento') return reply({ ...sharing,
        enabled: options.sharingEnabled !== false, can_publish: !!options.writer });
      if (url.pathname === '/api/cofre/compartilhamento/sincronizar') {
        if (state.holdSync) {
          await new Promise(resolve => { state.heldSync = resolve; });
          state.holdSync = false;
        }
        if (state.syncUnavailable) return route.fulfill({ status: 503, contentType: 'application/json',
          body: JSON.stringify({ detail: { code: 'cofre_sync_unavailable', message: 'Compartilhamento temporariamente indisponível.' } }) });
        return reply({ ...sharing, enabled: options.sharingEnabled !== false, can_publish: !!options.writer, changed: false });
      }
      if (url.pathname === '/api/cofre/servidores') return reply({ servidores: [server] });
      if (url.pathname === '/api/cofre/servidores/fixture-guild/canais') return reply({ servidor: server, canais: [channel] });
      if (url.pathname === '/api/cofre/canais/fixture-channel/mensagens') {
        if (state.holdMessages) {
          state.messageStarted = true;
          await new Promise(resolve => { state.heldMessage = resolve; });
          state.holdMessages = false;
        }
        if (state.messagesUnavailable) return unavailable();
        if (state.redirectMessages) return route.fulfill({ status: 302, headers: { location: origin + '/frontend_index.html' } });
        return reply({
        mensagens: [{ id: 'fixture-message', author: 'Fixture', content, timestamp: '2026-10-22T12:00:00Z',
          attachments: options.backendAssetUnauthorized ? [{ filename: 'fixture.png', content_type: 'image/png',
            local_url: '/api/cofre/arquivos/fixture-guild/' + 'a'.repeat(64) + '/fixture.png' }] : [] }],
        total: 1, offset: 0, limit: 100, total_ativos: 1, total_excluidos: 0, has_more: false });
      }
      if (options.backendAssetUnauthorized && url.pathname.startsWith('/api/cofre/arquivos/')) return route.fulfill({ status: 401, headers: { 'WWW-Authenticate': 'Bearer' }, body: '' });
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
    for (const options of [{ backendRenewed: true }, { mirror: true, permissions: { full: true } }, { dynamic: true }]) {
      const result = await scenario(browser, options);
      await result.page.getByText(content, { exact: true }).waitFor();
      const auth = await result.page.evaluate(() => ({ valid: verificarSessao(), headers: obterAuthHeaders({ 'X-Fixture': 'preserved' }) }));
      assert.equal(auth.valid, true);
      assert.deepEqual(auth.headers, { Authorization: 'Bearer ' + token, 'X-Fixture': 'preserved' });
      await assertQuiet(result);
      assert.equal(await result.page.locator('#reauthenticateCofre').isVisible(), false,
        'A sessão já renovada pelo backend carrega com o mesmo JWT JK, sem segundo login.');
      if (options.backendRenewed) {
        result.state.holdMessages = true;
        const pending = result.page.waitForRequest(request => new URL(request.url()).pathname.endsWith('/mensagens'));
        await result.page.locator('#refreshMessages').click();
        await pending;
        while (!result.state.heldMessage) await result.page.waitForTimeout(10);
        await result.page.evaluate(() => {
          const user = JSON.parse(localStorage.getItem('user_data'));
          localStorage.setItem('user_data', JSON.stringify({ ...user, company_id: 'fixture-other-company' }));
        });
        result.state.heldMessage();
        await result.page.waitForFunction(() => document.querySelector('#sharingLabel').textContent === 'Sessão alterada');
        assert.equal(await result.page.getByText(content, { exact: true }).count(), 0,
          'Uma resposta da empresa anterior não volta à tela após troca de empresa.');
        assert.equal(await result.page.evaluate(() => localStorage.getItem('access_token')), token);
      }
      await result.context.close();
    }
    for (const [options, target] of [[{ permissions: {} }, '/dashboard.html'], [{ identity: false }, '/frontend_index.html']]) {
      const result = await scenario(browser, options);
      await result.page.waitForURL(origin + target);
      assert.equal(result.state.api.length, 0, 'Sessão/permissão ausente bloqueia a consulta antes da API.');
      assert.deepEqual(result.state.chunks, cofreChunks);
      assert.deepEqual(result.state.forbidden, []);
      assert.deepEqual(result.state.errors, []);
      if (options.identity === false) {
        await result.page.evaluate(() => {
          localStorage.setItem('user_data', JSON.stringify({ client_id: 'fixture-legacy-local-only' }));
          localStorage.setItem('permissions', JSON.stringify({ cofre: true }));
        });
        await result.page.goto(origin + '/cofre.html');
        await result.page.getByText('Não foi possível iniciar o Cofre de Informações. Atualize a página para tentar novamente.', { exact: true }).waitFor();
        assert.equal(result.state.api.length, 0, 'Um client_id no localStorage não substitui o JWT JK.');
        assert.equal(await result.page.locator('#reauthenticateCofre').isVisible(), false);
        assert.equal(await result.page.evaluate(() => JSON.parse(localStorage.getItem('user_data')).client_id), 'fixture-legacy-local-only');
        assert.deepEqual(result.state.forbidden, []);
      }
      await result.context.close();
    }
    const denied = await scenario(browser, { permissions: { full: true }, backendDenied: true });
    await denied.page.getByText('Acesso ao Cofre negado pela API.', { exact: true }).first().waitFor();
    assert.equal(await denied.page.getByText(content, { exact: true }).count(), 0, 'Permissão local não contorna a decisão da API.');
    await assertQuiet(denied);
    await denied.context.close();
    const slowSync = await scenario(browser, { holdSync: true });
    await slowSync.page.getByText(content, { exact: true }).waitFor();
    while (!slowSync.state.heldSync) await slowSync.page.waitForTimeout(10);
    assert.equal(await slowSync.page.evaluate(() => localStorage.getItem('access_token')), token);
    assert.equal(await slowSync.page.locator('#reauthenticateCofre').isVisible(), false,
      'A cópia local abre enquanto a conexão compartilhada ainda está pendente.');
    slowSync.state.heldSync();
    await slowSync.page.waitForFunction(() => !document.querySelector('#syncSharing').disabled);
    await slowSync.context.close();
    const cached = await scenario(browser, { syncUnavailable: true, writer: true });
    await cached.page.getByText(content, { exact: true }).waitFor();
    await cached.page.waitForFunction(() => document.querySelector('#sharingLabel').textContent === 'Cópia local');
    assert.equal(await cached.page.locator('#sharingLabel').textContent(), 'Cópia local',
      'Uma falha de sincronização não bloqueia a leitura do acervo já disponível.');
    assert.equal(await cached.page.evaluate(() => localStorage.getItem('access_token')), token);
    assert.equal(await cached.page.locator('#reauthenticateCofre').isVisible(), false);
    assert.equal(await cached.page.locator('#publishSharing').isVisible(), false);
    assert.equal(await cached.page.getByRole('button', { name: 'Editar item de Fixture', exact: true }).count(), 0,
      'Uma cópia compartilhada fica em consulta enquanto não puder publicar alterações.');
    assert.equal(cached.state.requests.includes('/frontend_index.html'), false);
    const sharingRequests = () => cached.state.api.filter(request => request.path.includes('/compartilhamento')).length;
    const cachedRequests = sharingRequests();
    await assertQuiet(cached);
    assert.equal(sharingRequests(), cachedRequests, 'A consulta local não inicia novas tentativas de login ou sincronização.');
    cached.state.syncUnavailable = false;
    await cached.page.locator('#syncSharing').click();
    await cached.page.waitForFunction(() => document.querySelector('#sharingLabel').textContent === 'Compartilhado com a empresa');
    await cached.page.getByRole('button', { name: 'Editar item de Fixture', exact: true }).waitFor();
    assert.equal(await cached.page.evaluate(() => localStorage.getItem('access_token')), token,
      'A sincronização recupera sem trocar a sessão do JK.');
    assert.equal(cached.state.api.filter(request => request.method === 'PATCH').length, 0);
    await cached.context.close();
    const original = await scenario(browser, { syncUnavailable: true, sharingEnabled: false, writer: true });
    await original.page.getByText(content, { exact: true }).waitFor();
    await original.page.getByRole('button', { name: 'Editar item de Fixture', exact: true }).waitFor();
    assert.equal(await original.page.locator('#sharingLabel').textContent(), 'Cópia local');
    assert.equal(await original.page.evaluate(() => localStorage.getItem('access_token')), token,
      'O login JK permite trabalhar no acervo local ainda não publicado.');
    assert.equal(original.state.requests.includes('/frontend_index.html'), false);
    await assertQuiet(original);
    await original.context.close();
    const cloudMutation = await scenario(browser, { writer: true });
    await cloudMutation.page.getByText(content, { exact: true }).waitFor();
    if (!cloudMutation.state.api.some(request => request.path.endsWith('/sincronizar'))) {
      await cloudMutation.page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/sincronizar'));
    }
    await cloudMutation.page.waitForFunction(() => !document.querySelector('#syncSharing').disabled);
    cloudMutation.state.mutationSyncUnavailable = true;
    await cloudMutation.page.getByRole('button', { name: 'Editar item de Fixture', exact: true }).click();
    await cloudMutation.page.locator('#editContent').fill('Rascunho local ainda não publicado.');
    await cloudMutation.page.locator('#saveEditMessage').click();
    await cloudMutation.page.waitForFunction(() => document.querySelector('#sharingLabel').textContent === 'Cópia local');
    assert.equal(await cloudMutation.page.getByText(content, { exact: true }).count(), 1);
    assert.equal(await cloudMutation.page.getByRole('button', { name: 'Editar item de Fixture', exact: true }).count(), 0);
    assert.equal(await cloudMutation.page.evaluate(() => localStorage.getItem('access_token')), token);
    await assertQuiet(cloudMutation);
    cloudMutation.state.mutationSyncUnavailable = false;
    await cloudMutation.page.locator('#syncSharing').click();
    await cloudMutation.page.waitForFunction(() => !document.querySelector('#syncSharing').disabled && document.querySelector('#sharingLabel').textContent === 'Compartilhado com a empresa');
    await cloudMutation.page.getByRole('button', { name: 'Editar item de Fixture', exact: true }).click();
    assert.equal(await cloudMutation.page.locator('#editContent').inputValue(), 'Rascunho local ainda não publicado.',
      'Falhar uma alteração compartilhada preserva o rascunho até a recuperação.');
    assert.equal(cloudMutation.state.api.filter(request => request.method === 'PATCH').length, 1,
      'Recuperar a sincronização nunca repete uma alteração que falhou.');
    await cloudMutation.context.close();
    const restored = await scenario(browser, { backendUnavailable: true });
    await restored.page.locator('#reauthenticateCofre').waitFor();
    assert.equal(await restored.page.locator('#reauthenticateCofre').textContent(), 'Tentar novamente');
    assert.equal(await restored.page.locator('#sharingLabel').textContent(), 'Informações indisponíveis');
    assert.equal(await restored.page.locator('#coverageBadge').textContent(), 'Consulta indisponível');
    assert.equal(await restored.page.locator('#syncSharing').isVisible(), true);
    assert.equal(await restored.page.locator('#emptyImport').isVisible(), false);
    assert.equal(await restored.page.evaluate(() => localStorage.getItem('access_token')), token,
      '503 scoped preserva a sessão JK já restaurada, mesmo sem credencial disponível.');
    await assertQuiet(restored);
    assert.deepEqual(restored.state.api.map(request => request.path), ['/api/cofre/compartilhamento'],
      'Uma credencial indisponível pausa a consulta sem repetir pedidos ou declarar acervo vazio.');
    await restored.page.evaluate(() => localStorage.setItem('fixture-preserved', 'saved'));
    restored.state.backendUnavailable = false;
    await restored.page.locator('#reauthenticateCofre').click();
    await restored.page.getByText(content, { exact: true }).waitFor();
    assert.equal(await restored.page.evaluate(() => localStorage.getItem('access_token')), token,
      'Retry de leitura não troca token nem pede login novo.');
    assert.equal(restored.state.requests.includes('/frontend_index.html'), false);
    assert.equal(await restored.page.evaluate(() => localStorage.getItem('fixture-preserved')), 'saved');
    restored.state.redirectMessages = true;
    await restored.page.locator('#refreshMessages').click();
    await restored.page.getByText('Não foi possível consultar o acervo. Tente novamente.', { exact: true }).waitFor();
    assert.equal(await restored.page.getByText(content, { exact: true }).count(), 1,
      '302 não vira resposta JSON vazia nem apaga os itens exibidos.');
    await restored.page.evaluate(() => {
      localStorage.removeItem('access_token');
      window.dispatchEvent(new StorageEvent('storage', { key: 'access_token' }));
    });
    await restored.page.waitForFunction(() => document.querySelector('#sharingLabel').textContent === 'Sessão alterada');
    assert.equal(await restored.page.getByText(content, { exact: true }).count(), 0,
      'Quando o JWT JK sai da sessão, o fallback legado não mantém dados da conta anterior na tela.');
    await restored.context.close();
    const recovering = await scenario(browser, { writer: true });
    await recovering.page.getByText(content, { exact: true }).waitFor();
    recovering.state.mutationUnavailable = true;
    await recovering.page.getByRole('button', { name: 'Editar item de Fixture', exact: true }).click();
    await recovering.page.locator('#editContent').fill('Rascunho fictício sem aplicação.');
    await recovering.page.locator('#saveEditMessage').click();
    await recovering.page.waitForFunction(() => document.querySelector('#sharingLabel').textContent === 'Informações indisponíveis');
    assert.equal(await recovering.page.getByText(content, { exact: true }).count(), 1,
      '503 na alteração mantém o item e não encerra a sessão.');
    const pausedRequests = recovering.state.api.length;
    await assertQuiet(recovering);
    assert.equal(recovering.state.api.length, pausedRequests, 'Foco/timers não fazem retry automático após 503.');
    recovering.state.mutationUnavailable = false;
    await recovering.page.locator('#syncSharing').click();
    await recovering.page.getByText(content, { exact: true }).waitFor();
    assert.equal(recovering.state.api.filter(request => request.method === 'PATCH').length, 1,
      'Retry manual recarrega leituras e nunca repete PATCH.');
    assert(recovering.state.api.filter(request => request.method !== 'GET' && !request.path.endsWith('/sincronizar')).every(request => request.method === 'PATCH'));
    recovering.state.messagesUnavailable = true;
    await recovering.page.locator('#refreshMessages').click();
    await recovering.page.waitForFunction(() => document.querySelector('#sharingLabel').textContent === 'Informações indisponíveis');
    assert.equal(await recovering.page.getByText(content, { exact: true }).count(), 1,
      '503 durante refresh preserva o item previamente carregado.');
    assert.equal(await recovering.page.evaluate(() => localStorage.getItem('access_token')), token);
    await recovering.context.close();
    const assetExpired = await scenario(browser, { backendAssetUnauthorized: true });
    await assetExpired.page.waitForURL(origin + '/frontend_index.html');
    assert.equal(await assetExpired.page.evaluate(() => localStorage.getItem('access_token')), null,
      'Somente 401 JWT realmente inválido é entregue ao guard global JK.');
    const assetRequests = assetExpired.state.api.length;
    assert.equal(assetExpired.state.api.filter(request => request.path.startsWith('/api/cofre/arquivos/')).length, 1);
    await assertQuiet(assetExpired);
    assert.equal(assetExpired.state.api.length, assetRequests, 'Um anexo com 401 não inicia novas tentativas.');
    await assetExpired.context.close();
    await normalFacade(browser, false);
    await normalFacade(browser, true);
    console.log('Cofre auth boot: 15 browser scenarios passed; zero Firebase/global boot requests.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
