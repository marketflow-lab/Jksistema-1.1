'use strict';

// All accounts, messages and uploads in this browser test are fictional.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const origin = 'https://cofre.test';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5RkAAAAASUVORK5CYII=', 'base64');
const tick = () => new Promise(resolve => setTimeout(resolve, 40));
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }

async function fixture(browser, admin = false, viewport = { width: 1440, height: 920 }) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const state = { errors: [], unexpected: [], posts: [], deletes: [], failPost: false, holdPost: false, postStarted: deferred(), postReleased: deferred(), channels: ['general', 'references'], messages: Array.from({ length: 130 }, (_, i) => ({ id: 'item-' + i, author: i === 0 ? 'Autor do JK' : 'Outra pessoa', content: 'Informação fictícia ' + i, timestamp: '2026-10-26T12:00:00Z', can_delete: admin || i === 0, can_edit: admin })), offsets: [], sharing: { configured: true, enabled: false, can_publish: admin, is_admin: admin, can_post: true } };
  const server = () => ({ id: 'fictional', name: 'Empresa de exemplo', channel_count: state.channels.length, message_count: state.messages.length, attachment_count: 0, history_complete: true });
  const channels = () => state.channels.map(id => ({ id, name: id === 'general' ? 'geral' : 'referências', category: 'Empresa', topic: 'Informações compartilhadas', message_count: id === 'general' ? state.messages.length : 0, archive_message_count: id === 'general' ? state.messages.length : 0, history_complete: true }));
  const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json; charset=utf-8', body: JSON.stringify(body) });
  page.on('pageerror', error => state.errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem('permissions', JSON.stringify({ cofre: true }));
    localStorage.setItem('access_token', 'fixture-token');
    localStorage.setItem('user_data', JSON.stringify({ client_id: 'fixture-company', username: 'fixture-owner' }));
    window.fixtureRevoked = [];
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.revokeObjectURL = url => { window.fixtureRevoked.push(url); revoke(url); };
  });
  await page.route('**/*', async route => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.origin !== origin) { state.unexpected.push(url.href); return route.abort(); }
    if (url.pathname === '/cofre.html') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(root, 'cofre.html'), 'utf8') });
    if (url.pathname === '/auth.js') return route.fulfill({ contentType: 'application/javascript', body: "window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:'Bearer '+localStorage.getItem('access_token')});" });
    if (url.pathname.startsWith('/cofre/')) return route.fulfill({ contentType: url.pathname.endsWith('.js') ? 'application/javascript' : url.pathname.endsWith('.css') ? 'text/css' : 'image/png', body: fs.readFileSync(path.join(root, 'static', url.pathname.slice(1))) });
    assert.strictEqual(req.headers().authorization, 'Bearer fixture-token', 'A API usa a sessão geral do JK.');
    if (url.pathname === '/api/cofre/compartilhamento') return json(route, state.sharing);
    if (url.pathname === '/api/cofre/compartilhamento/sincronizar') return json(route, { ...state.sharing, success: true, changed: false });
    if (url.pathname === '/api/cofre/servidores') return json(route, { servidores: [server()] });
    if (url.pathname === '/api/cofre/servidores/fictional/canais') return json(route, { servidor: server(), canais: channels() });
    if (url.pathname === '/api/cofre/canais/general/mensagens' && req.method() === 'POST') {
      const raw = req.postDataBuffer().toString('utf8');
      const payloadPart = raw.match(/name="payload"\r\n\r\n([^]*?)\r\n--/);
      assert(payloadPart, 'Envio multipart tem payload JSON.');
      const payload = JSON.parse(payloadPart[1]);
      assert.deepStrictEqual(Object.keys(payload).sort(), ['content', 'links', 'request_id']);
      assert.match(payload.request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
      const uploaded = Array.from(raw.matchAll(/name="arquivos"; filename="([^"]+)"/g), match => match[1]);
      state.posts.push({ payload, uploaded });
      if (state.holdPost) { state.postStarted.resolve(); await state.postReleased.promise; }
      if (state.failPost) return json(route, { detail: { code: 'cofre_sync_unavailable', message: 'Sincronização temporariamente indisponível.' } }, 503);
      const mensagem = { id: 'new-' + state.posts.length, author: 'Autor do JK', content: payload.content, timestamp: '2026-10-26T13:00:00Z', attachments: [], can_delete: true, can_edit: admin };
      state.messages.push(mensagem);
      return json(route, { success: true, mensagem, total_ativos: state.messages.length, message_offset: Math.max(0, state.messages.length - 100) });
    }
    if (/^\/api\/cofre\/canais\/(general|references)\/mensagens$/.test(url.pathname)) {
      const offset = Number(url.searchParams.get('offset'));
      const limit = Number(url.searchParams.get('limit'));
      const all = url.pathname.includes('/general/') ? state.messages : [];
      state.offsets.push(offset);
      return json(route, { mensagens: all.slice(offset, offset + limit), total: all.length, total_ativos: all.length, total_excluidos: 0, has_more: offset + limit < all.length });
    }
    if (url.pathname === '/api/cofre/canais/general/mensagens/item-0' && req.method() === 'DELETE') {
      state.deletes.push('item-0');
      state.messages = state.messages.filter(message => message.id !== 'item-0');
      return json(route, { success: true, total_ativos: state.messages.length, total_excluidos: 1 });
    }
    if (url.pathname === '/api/cofre/canais/general' && req.method() === 'DELETE') {
      assert(admin, 'Somente administrador exclui canal.');
      state.deletes.push('general');
      state.channels = state.channels.filter(id => id !== 'general');
      return json(route, { success: true });
    }
    state.unexpected.push(req.method() + ' ' + url.pathname);
    return route.abort();
  });
  await page.goto(origin + '/cofre.html');
  await page.locator('#messageContent:not([disabled])').waitFor();
  await page.locator('#messages .message').first().waitFor();
  return { page, state, context };
}

async function safeLayout(page) {
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'A interface cabe na largura.');
  assert(await page.evaluate(() => {
    const message = document.getElementById('messageScroll').getBoundingClientRect();
    const composer = document.getElementById('composerWrap').getBoundingClientRect();
    return message.bottom <= composer.top + 1 && message.height > 80 && composer.bottom <= innerHeight;
  }), 'Mensagens rolam sem sobrepor o compositor fixo.');
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  let passed = 0;
  try {
    {
      const { page, state, context } = await fixture(browser);
      assert.strictEqual(await page.locator('[data-message-id="item-0"] [data-action="delete-message"]').count(), 1);
      assert.strictEqual(await page.locator('[data-message-id="item-0"] [data-action="edit-message"]').count(), 0);
      assert.strictEqual(await page.locator('[data-message-id="item-1"] .message-action').count(), 0);
      assert(await page.locator('#deleteChannel').isHidden(), 'Leitor não tem exclusão de canal.');
      await page.locator('#messageContent').fill('Texto e link https://example.com');
      await page.locator('#messageContent').press('Shift+Enter');
      await page.locator('#messageContent').pressSequentially('segunda linha');
      assert.strictEqual(state.posts.length, 0);
      if (process.env.COFRE_TEST_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.COFRE_TEST_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.COFRE_TEST_SCREENSHOT_DIR, 'cofre-composer-desktop.png') });
      }
      await page.locator('#messageContent').press('Enter');
      await page.locator('[data-message-id="new-1"]').waitFor();
      assert.strictEqual(state.posts[0].payload.content, 'Texto e link https://example.com\nsegunda linha');
      assert(state.offsets.includes(31), 'Envio em canal longo carrega somente a última página.');
      assert.strictEqual(await page.locator('#messages .message').count(), 100);
      await page.locator('#loadBeginning').click();
      await page.locator('[data-message-id="item-0"]').waitFor();
      await page.locator('[data-message-id="item-0"] [data-action="delete-message"]').click();
      await page.locator('#confirmDeleteMessage').click();
      await page.waitForFunction(() => !document.getElementById('deleteMessageDialog').open);
      assert(state.deletes.includes('item-0'), 'Autor exclui sua mensagem usando a sessão JK.');
      await safeLayout(page);
      assert.deepStrictEqual(state.errors, []);
      assert.deepStrictEqual(state.unexpected, []);
      passed += 1;
      await context.close();
    }
    {
      const { page, state, context } = await fixture(browser);
      await page.locator('#messageFiles').setInputFiles([{ name: 'imagem.png', mimeType: 'image/png', buffer: png }, { name: 'documento.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-fixture') }, { name: 'vetor.svg', mimeType: 'image/svg+xml', buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"/>') }, { name: 'svg-disfarcado.png', mimeType: 'image/png', buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"/>') }]);
      await page.locator('.composer-file-image').waitFor();
      assert.strictEqual(await page.locator('.composer-file-image').count(), 1, 'SVG e documentos não ganham prévia ativa.');
      await page.getByRole('button', { name: 'Remover anexo imagem.png', exact: true }).click();
      assert.strictEqual(await page.evaluate(() => window.fixtureRevoked.length), 1, 'A prévia removida libera Object URL.');
      await page.locator('#messageFiles').setInputFiles({ name: 'foto.png', mimeType: 'image/png', buffer: png });
      await page.locator('.composer-file-image').waitFor();
      await page.locator('#messageContent').fill('Veja os anexos');
      await page.locator('#sendMessage').click();
      await page.locator('[data-message-id="new-1"]').waitFor();
      assert.deepStrictEqual(state.posts[0].uploaded, ['documento.pdf', 'vetor.svg', 'svg-disfarcado.png', 'foto.png']);
      assert.strictEqual(await page.locator('.composer-file').count(), 0);
      assert.strictEqual(await page.evaluate(() => window.fixtureRevoked.length), 2, 'O envio confirmado libera as prévias do rascunho.');
      await page.locator('#messageFiles').setInputFiles(Array.from({ length: 6 }, (_, i) => ({ name: 'a' + i + '.txt', mimeType: 'text/plain', buffer: Buffer.from('x') })));
      assert((await page.locator('#composerFeedback').innerText()).includes('até 5'));
      assert.strictEqual(await page.locator('.composer-file').count(), 0, 'O lote acima do limite é rejeitado inteiro.');
      await page.locator('#messageFiles').setInputFiles({ name: 'grande.txt', mimeType: 'text/plain', buffer: Buffer.alloc(10 * 1024 * 1024 + 1) });
      assert((await page.locator('#composerFeedback').innerText()).includes('10 MB'));
      assert.strictEqual(state.posts.length, 1);
      assert.deepStrictEqual(state.errors, []);
      passed += 1;
      await context.close();
    }
    {
      const { page, state, context } = await fixture(browser);
      await page.locator('#messageContent').fill('Rascunho do primeiro canal');
      await page.getByRole('button', { name: /referências/ }).click();
      assert.strictEqual(await page.locator('#messageContent').inputValue(), '');
      await page.locator('#messageContent').fill('Rascunho do segundo canal');
      await page.getByRole('button', { name: /geral/ }).click();
      assert.strictEqual(await page.locator('#messageContent').inputValue(), 'Rascunho do primeiro canal');
      state.sharing.enabled = true;
      await page.locator('#syncSharing').click();
      await page.waitForFunction(() => document.getElementById('sharingLabel').textContent === 'Compartilhado com a empresa');
      state.failPost = true;
      await page.locator('#sendMessage').click();
      await page.waitForFunction(() => document.getElementById('composerFeedback').textContent.includes('rascunho foi mantido'));
      assert.strictEqual(await page.locator('#messageContent').inputValue(), 'Rascunho do primeiro canal');
      assert(await page.locator('#sendMessage').isDisabled(), 'Cópia compartilhada indisponível não envia.');
      await page.evaluate(() => { window.dispatchEvent(new Event('focus')); });
      await tick();
      assert.strictEqual(state.posts.length, 1, 'Falha não é repetida pelo foco ou sincronização.');
      state.failPost = false;
      await page.locator('#syncSharing').click();
      await page.locator('#sendMessage:not([disabled])').waitFor();
      await page.locator('#sendMessage').click();
      await page.locator('[data-message-id="new-2"]').waitFor();
      assert.strictEqual(state.posts[0].payload.request_id, state.posts[1].payload.request_id, 'Tentativa manual do mesmo rascunho mantém idempotência.');
      assert.deepStrictEqual(state.errors, []);
      passed += 1;
      await context.close();
    }
    {
      const { page, state, context } = await fixture(browser, true);
      assert(await page.locator('#deleteChannel').isVisible());
      await page.locator('#deleteChannel').click();
      assert((await page.locator('#deleteChannelPreview').innerText()).includes('130 mensagens'));
      assert(await page.locator('#confirmDeleteChannel').isDisabled());
      await page.locator('#deleteChannelName').fill('outro');
      assert(await page.locator('#confirmDeleteChannel').isDisabled());
      await page.locator('#deleteChannelName').fill('geral');
      await page.locator('#confirmDeleteChannel').click();
      await page.waitForFunction(() => document.getElementById('channelName').textContent === 'referências');
      assert(!state.channels.includes('general'));
      assert.strictEqual(await page.getByRole('button', { name: /geral/ }).count(), 0);
      assert.deepStrictEqual(state.errors, []);
      assert.deepStrictEqual(state.unexpected, []);
      passed += 1;
      await context.close();
    }
    {
      const { page, state, context } = await fixture(browser);
      state.holdPost = true;
      await page.locator('#messageFiles').setInputFiles({ name: 'imagem.png', mimeType: 'image/png', buffer: png });
      await page.locator('.composer-file-image').waitFor();
      await page.locator('#messageContent').fill('Segredo da primeira conta');
      await page.locator('#sendMessage').click();
      await state.postStarted.promise;
      assert(await page.locator('#composerProgress').isVisible());
      await page.evaluate(() => { localStorage.setItem('access_token', 'other-fixture-token'); window.dispatchEvent(new StorageEvent('storage', { key: 'access_token' })); });
      await page.locator('#composerWrap').waitFor({ state: 'hidden' });
      assert.strictEqual(await page.locator('#messageContent').inputValue(), '');
      assert.strictEqual(await page.locator('.composer-file').count(), 0);
      assert(await page.evaluate(() => window.fixtureRevoked.length > 0));
      state.postReleased.resolve();
      await tick();
      assert.strictEqual(await page.locator('#messages .message').count(), 0, 'Resposta da sessão anterior não entra na nova conta.');
      assert.strictEqual(state.posts.length, 1);
      assert.deepStrictEqual(state.errors, []);
      passed += 1;
      await context.close();
    }
    {
      const { page, state, context } = await fixture(browser, false, { width: 390, height: 844 });
      await page.locator('#messageContent').fill('Mensagem no celular');
      await safeLayout(page);
      if (process.env.COFRE_TEST_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.COFRE_TEST_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.COFRE_TEST_SCREENSHOT_DIR, 'cofre-composer-mobile.png') });
      }
      assert.deepStrictEqual(state.errors, []);
      passed += 1;
      await context.close();
    }
    console.log('Cofre composer: ' + passed + ' cenários de navegador aprovados.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
