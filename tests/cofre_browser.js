'use strict';

// Browser regression with fictional content and fully intercepted requests.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const origin = 'https://cofre.test';
const token = 'fixture-cofre-token';
const assetPath = '/api/cofre/arquivos/fixture-guild/' + 'a'.repeat(64) + '/referencia.png';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5RkAAAAASUVORK5CYII=', 'base64');
const server = { id: 'fixture-guild', name: 'Servidor de exemplo', exported_at: '2026-10-21T13:00:00Z', channel_count: 3, message_count: 4, attachment_count: 2, history_complete: false };
const channels = [
  { id: 'notices', name: 'comunicados', category: 'Informações', topic: 'Decisões, referências e tudo o que precisa ficar guardado.', message_count: 3, history_complete: true, url: 'https://discord.com/channels/fixture-guild/notices' },
  { id: 'references', name: 'referências', category: 'Conhecimento', topic: 'Materiais e arquivos para consultar depois.', message_count: 1, history_complete: false },
  { id: 'empty', name: 'sem-mensagens', category: 'Conhecimento', message_count: 0, history_complete: false }
];
const messages = [
  { id: 'm1', author: 'Pessoa Exemplo', timestamp: '2026-10-20T13:30:00Z', content: 'Bem-vindos ao acervo. Aqui ficam as decisões e os materiais que vale guardar.\nA organização por canais preserva o contexto de cada conversa.', source_url: 'https://discord.com/channels/fixture-guild/notices/m1', attachments: [] },
  { id: 'm2', author: 'Equipe Modelo', timestamp: '2026-10-21T14:00:00Z', content: 'Referência para nossa próxima conversa: https://example.com/material\n<img src=x onerror="window.fixtureInjected=true">', links: [{ url: 'javascript:alert(1)', text: 'Não executar' }, { url: 'https://example.com/consulta', text: 'Consultar material de apoio ↗' }], attachments: [
    { filename: 'referencia.png', content_type: 'image/png', local_url: assetPath, url: 'https://external-assets.test/original.png' },
    { filename: 'arquivo-original.pdf', content_type: 'application/pdf', url: 'https://external-assets.test/arquivo.pdf' },
    { filename: 'invalido.png', content_type: 'image/png', local_url: 'https://evil.test/api/cofre/arquivos/fixture-guild/' + 'b'.repeat(64) + '/invalido.png', url: 'javascript:alert(1)' }
  ], embeds: [{ text: 'Um cartão de referência preservado na cópia.', links: [{ url: 'https://example.com/nota', text: 'Leia a referência' }] }] },
  { id: 'm3', author: 'Pessoa Exemplo', timestamp: '2026-10-21T15:10:00Z', content: 'Mensagem da página seguinte.' }
];
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }
async function json(route, payload, status = 200) { await route.fulfill({ status, contentType: 'application/json; charset=utf-8', body: JSON.stringify(payload) }); }
async function screenshot(page, name) {
  if (!process.env.COFRE_TEST_SCREENSHOT_DIR) return;
  const dir = path.resolve(process.env.COFRE_TEST_SCREENSHOT_DIR);
  fs.mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: path.join(dir, name + '.png'), fullPage: true });
}
async function noOverflow(page) {
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1), 'Documento deve caber na viewport.');
}
async function scenario(browser, options = {}) {
  const context = await browser.newContext({ viewport: options.viewport || { width: 1440, height: 920 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  if (options.clock) await page.clock.install({ time: new Date('2026-10-22T12:00:00Z') });
  const state = { requests: [], errors: [], unexpected: [], imported: false, assetCalls: 0, failMessages: !!options.failMessages, slowStarted: deferred(), slowRelease: deferred(), overrides: new Map(), deleted: new Set(), channelOverrides: new Map(), failMutation: false, holdEdit: false, editStarted: deferred(), editRelease: deferred(), lastEditPayload: null, lastAppliedEdit: null, sharing: { configured: false, enabled: false, can_publish: false, revision: null, last_sync: null, ...(options.sharing || {}) }, syncCalls: 0, publishCalls: 0, syncChanged: false, failSharingSync: !!options.failSharingSync, failSharingStatus: !!options.failSharingStatus, conflictPublish: !!options.conflictPublish, holdSharingSync: false, sharingSyncStarted: deferred(), sharingSyncRelease: deferred(), remoteContent: null };
  const manyMessages = Array.from({ length: 150 }, (_, index) => ({ id: 'many' + (index + 1), author: 'Pessoa Exemplo', timestamp: '2026-10-21T14:00:00Z', content: 'Item ' + (index + 1) + ' preservado.' }));
  function allMessages(channelId) {
    const base = channelId === 'references' ? [{ id: 'ref1', author: 'Pessoa Exemplo', timestamp: '2026-10-21T16:00:00Z', content: 'Referência do segundo canal.' }] : channelId === 'empty' ? [] : options.manyMessages ? manyMessages : messages;
    return base.map(message => ({ ...message, ...(message.id === 'm1' && state.remoteContent ? { content: state.remoteContent } : {}), ...(state.overrides.get(message.id) || {}), edited: state.overrides.has(message.id), deleted: state.deleted.has(message.id) }));
  }
  function counts(channelId) {
    const all = allMessages(channelId);
    return { total_ativos: all.filter(message => !message.deleted).length, total_excluidos: all.filter(message => message.deleted).length };
  }
  function channelData(channel) {
    const count = counts(channel.id);
    return { ...channel, ...(state.channelOverrides.get(channel.id) || {}), message_count: count.total_ativos, deleted_message_count: count.total_excluidos, archive_message_count: count.total_ativos + count.total_excluidos };
  }
  function serverData() { return { ...server, message_count: (options.manyMessages ? 151 : 4) - state.deleted.size, deleted_message_count: state.deleted.size, archive_message_count: options.manyMessages ? 151 : 4 }; }
  page.on('pageerror', error => state.errors.push(error.message));
  await page.addInitScript(({ allowed }) => {
    localStorage.setItem('permissions', JSON.stringify(allowed ? { cofre: true } : {}));
    localStorage.setItem('access_token', 'fixture-cofre-token');
    localStorage.setItem('user_data', JSON.stringify({ client_id: 'fixture-client-a', username: 'fixture-user-a' }));
    const revoke = URL.revokeObjectURL.bind(URL);
    window.fixtureRevokedUrls = [];
    window.fixtureInjected = false;
    URL.revokeObjectURL = url => { window.fixtureRevokedUrls.push(url); revoke(url); };
  }, { allowed: options.allowed !== false });
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    state.requests.push({ url: url.href, authorization: request.headers().authorization || '', method: request.method() });
    try {
      assert(!url.href.includes(token), 'Token jamais aparece em URL.');
      if (url.origin !== origin) {
        state.unexpected.push(url.href);
        return route.abort();
      }
      if (url.pathname.startsWith('/api/cofre/')) assert.strictEqual(request.headers().authorization, 'Bearer ' + token, 'Toda API e anexo local deve receber sessão.');
      if (url.pathname === '/cofre.html') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: fs.readFileSync(path.join(root, 'cofre.html'), 'utf8') });
      if (url.pathname === '/dashboard.html') return route.fulfill({ contentType: 'text/html', body: '<main>Dashboard fixture</main>' });
      if (url.pathname === '/auth.js') return route.fulfill({ contentType: 'application/javascript', body: `window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:'Bearer '+localStorage.getItem('access_token')});` });
      if (url.pathname === '/cofre/cofre.js' || url.pathname === '/cofre/cofre.css') return route.fulfill({ contentType: url.pathname.endsWith('.js') ? 'application/javascript; charset=utf-8' : 'text/css; charset=utf-8', body: fs.readFileSync(path.join(root, 'static', url.pathname), 'utf8') });
      if (['/cofre/assets/cofre-cover.png', '/cofre/assets/cofre-library.png'].includes(url.pathname)) return route.fulfill({ contentType: 'image/png', body: fs.readFileSync(path.join(root, 'static', url.pathname)) });
      if (url.pathname === assetPath) { state.assetCalls += 1; return route.fulfill({ contentType: 'image/png', body: png }); }
      if (url.pathname === '/api/cofre/compartilhamento') {
        if (state.failSharingStatus) return json(route, { detail: 'Estado temporariamente indisponível.' }, 503);
        return json(route, state.sharing);
      }
      if (url.pathname === '/api/cofre/compartilhamento/sincronizar') {
        assert.strictEqual(request.method(), 'POST');
        state.syncCalls += 1;
        if (state.holdSharingSync) { state.sharingSyncStarted.resolve(); await state.sharingSyncRelease.promise; }
        if (state.failSharingSync) return route.abort('internetdisconnected');
        const changed = state.syncChanged;
        state.syncChanged = false;
        return json(route, { success: true, changed, ...state.sharing });
      }
      if (url.pathname === '/api/cofre/compartilhamento/publicar') {
        assert.strictEqual(request.method(), 'POST');
        state.publishCalls += 1;
        if (state.conflictPublish) return json(route, { detail: 'Sincronize antes de publicar' }, 409);
        assert(state.sharing.can_publish, 'Leitor não publica acervo.');
        state.sharing.enabled = true;
        state.sharing.revision = Number(state.sharing.revision || 0) + 1;
        return json(route, { success: true, changed: true, ...state.sharing });
      }
      if (url.pathname === '/api/cofre/servidores' && options.failViewAfterPublish && state.publishCalls) return json(route, { detail: 'Visualização temporariamente indisponível.' }, 503);
      if (url.pathname === '/api/cofre/servidores') return json(route, { servidores: options.empty && !state.imported ? [] : [serverData()] });
      if (url.pathname === '/api/cofre/servidores/fixture-guild/canais') return json(route, { servidor: serverData(), canais: channels.map(channelData) });
      if (url.pathname === '/api/cofre/importar') {
        assert.strictEqual(request.method(), 'POST');
        assert(request.headers()['content-type'].startsWith('multipart/form-data; boundary='));
        assert(request.postDataBuffer().includes(Buffer.from('name="arquivo"')), 'Upload usa campo arquivo.');
        state.imported = true;
        return json(route, { servidor: server, imported_messages: 4, total_messages: 4 });
      }
      if (url.pathname.startsWith('/api/cofre/canais/')) {
        assert.strictEqual(url.searchParams.get('servidor_id'), server.id, 'Canal sempre identifica servidor.');
        const channel = url.pathname.split('/')[4];
        const mutation = url.pathname.match(/^\/api\/cofre\/canais\/([^/]+)\/mensagens\/([^/]+)(\/restaurar)?$/);
        if (mutation) {
          const id = mutation[2];
          if (request.method() === 'PATCH') {
            assert(request.headers()['content-type'].startsWith('application/json'));
            const payload = request.postDataJSON();
            state.lastEditPayload = payload;
            assert(Array.isArray(payload.links) && Array.isArray(payload.embeds));
            if (state.failMutation) return json(route, { detail: 'Não foi possível salvar nesta tentativa.' }, 500);
            if (state.holdEdit) { state.editStarted.resolve(); await state.editRelease.promise; }
            state.overrides.set(id, payload);
            state.lastAppliedEdit = id;
          } else if (request.method() === 'DELETE') state.deleted.add(id);
          else if (request.method() === 'POST' && mutation[3]) state.deleted.delete(id);
          else throw new Error('Mutação inesperada.');
          return json(route, { mensagem: allMessages(channel).find(message => message.id === id), ...counts(channel) });
        }
        if (request.method() === 'PATCH') {
          const payload = request.postDataJSON();
          state.channelOverrides.set(channel, payload);
          return json(route, { canal: channelData(channels.find(item => item.id === channel)) });
        }
        if (state.failMessages) return json(route, { detail: 'Falha temporária de teste.' }, 500);
        const query = url.searchParams.get('q');
        const offset = Number(url.searchParams.get('offset'));
        if (query === 'lenta') {
          state.slowStarted.resolve();
          await state.slowRelease.promise;
          return json(route, { mensagens: [{ id: 'stale', author: 'Resposta antiga', content: 'ESTE RESULTADO ESTÁ OBSOLETO' }], total: 1, has_more: false, ...counts(channel) });
        }
        if (query === 'atual') return json(route, { mensagens: [{ id: 'search', author: 'Resultado', content: 'Busca atual: ' + query }], total: 1, offset: 0, limit: 100, has_more: false, ...counts(channel) });
        const trash = url.searchParams.get('excluidos') === 'true';
        let selected = allMessages(channel).filter(message => message.deleted === trash);
        if (query) selected = selected.filter(message => JSON.stringify(message).toLowerCase().includes(query.toLowerCase()));
        const pageSize = Math.min(options.manyMessages ? 100 : 2, Number(url.searchParams.get('limit')) || 100);
        const pageMessages = selected.slice(offset, offset + pageSize);
        return json(route, { mensagens: pageMessages, total: selected.length, offset, limit: 100, has_more: offset + pageMessages.length < selected.length, ...counts(channel) });
      }
      if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
      state.unexpected.push(url.href);
      return route.abort();
    } catch (error) {
      if (request.failure()?.errorText === 'net::ERR_ABORTED') return;
      state.errors.push(error.message);
      await route.abort().catch(() => {});
    }
  });
  await page.goto(origin + '/cofre.html');
  return { page, state };
}
async function clean(result) {
  assert.deepStrictEqual(result.state.errors, [], 'Sem erros de página ou contrato.');
  assert.deepStrictEqual(result.state.unexpected, [], 'Sem requisição remota ou inesperada.');
  await result.page.context().close();
}
(async () => {
  for (const file of ['cofre.html', 'dashboard.html', 'admin_usuarios.html', 'configuracoes.html']) assert.strictEqual(fs.readFileSync(path.join(root, file), 'utf8'), fs.readFileSync(path.join(root, 'static', file), 'utf8'), 'Espelho fiel: ' + file);
  const browser = await chromium.launch({ headless: true });
  try {
    const desktop = await scenario(browser);
    const { page, state } = desktop;
    assert.strictEqual(await page.title(), 'Cofre de Informações — JK Sistema');
    await page.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    assert.strictEqual(await page.locator('.channel-category').count(), 2);
    assert.strictEqual(await page.locator('#coverageBadge').textContent(), 'Histórico parcial');
    assert((await page.locator('.message-text').nth(1).textContent()).includes('<img src=x'), 'Conteúdo potencialmente ativo permanece texto.');
    assert.strictEqual(await page.evaluate(() => window.fixtureInjected), false);
    assert.strictEqual(await page.locator('a[href^="javascript:"]').count(), 0);
    await page.waitForFunction(() => document.querySelector('.attachment-preview img')?.complete);
    assert((await page.locator('.attachment-preview img').getAttribute('src')).startsWith('blob:'), 'Prévia usa blob autenticado.');
    assert.strictEqual(state.assetCalls, 1);
    await page.getByRole('button', { name: 'Abrir imagem referencia.png', exact: true }).click();
    assert(await page.locator('#mediaDialog').isVisible());
    assert((await page.locator('#mediaImage').getAttribute('src')).startsWith('blob:'));
    await page.locator('#closeMedia').click();
    assert(await page.locator('#mediaDialog').isHidden());
    await page.locator('.channel-category > summary').first().click();
    assert.strictEqual(await page.locator('.channel-category').first().getAttribute('open'), null, 'Categoria recolhe de verdade.');
    await page.locator('.channel-category > summary').first().click();
    await noOverflow(page);
    await screenshot(page, 'cofre-desktop');
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Baixar referencia.png', exact: true }).click();
    assert.strictEqual((await download).suggestedFilename(), 'referencia.png');
    assert.strictEqual(state.assetCalls, 1, 'Download reutiliza blob local.');
    await page.locator('#loadMore').click();
    await page.waitForFunction(() => document.querySelectorAll('.message').length === 3);
    assert.strictEqual(await page.locator('.day-divider').count(), 2, 'Paginação conserva divisores de data.');
    await page.locator('#messageSearch').fill('lenta');
    await state.slowStarted.promise;
    await page.locator('#messageSearch').fill('atual');
    await page.waitForFunction(() => document.querySelector('#messages').textContent.includes('Busca atual: atual'));
    state.slowRelease.resolve();
    await page.waitForTimeout(80);
    assert(!(await page.locator('#messages').textContent()).includes('OBSOLETO'), 'Busca atrasada não substitui busca nova.');
    assert((await page.evaluate(() => window.fixtureRevokedUrls)).length > 0, 'Troca de contexto revoga mídia.');
    await page.locator('#messageSearch').fill('');
    await page.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    await page.getByRole('button', { name: /referências/ }).click();
    await page.waitForFunction(() => document.querySelector('#messages').textContent.includes('Referência do segundo canal.'));
    assert.strictEqual(await page.locator('#messageSearch').inputValue(), '');
    await page.getByRole('button', { name: /sem-mensagens/ }).click();
    await page.waitForFunction(() => document.querySelector('#timelineStatus').textContent.includes('Nenhuma mensagem nesta cópia'));
    await clean(desktop);

    const editing = await scenario(browser);
    const ep = editing.page;
    const es = editing.state;
    const firstItem = () => ep.locator('.message[data-message-id="m1"]');
    await ep.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    await firstItem().locator('[data-action="edit-message"]').click();
    await ep.locator('#editAuthor').fill('Autor Editado');
    await ep.locator('#editContent').fill('Texto atualizado no acervo.');
    await ep.locator('#addMessageLink').click();
    await ep.getByRole('textbox', { name: 'Link 1: nome', exact: true }).fill('Material editado');
    await ep.getByRole('textbox', { name: 'Link 1: endereço', exact: true }).fill('https://example.com/editado');
    await ep.locator('#addMessageEmbed').click();
    await ep.getByRole('textbox', { name: 'Texto do cartão 1', exact: true }).fill('Cartão atualizado.');
    await ep.locator('#saveEditMessage').click();
    await ep.waitForFunction(() => document.querySelector('.message[data-message-id="m1"]')?.textContent.includes('Texto atualizado no acervo.'));
    assert.strictEqual(await firstItem().locator('.message-state').textContent(), 'Editado');
    assert.strictEqual(await firstItem().locator('.message-source').getAttribute('href'), messages[0].source_url, 'Edição preserva a origem.');
    assert.deepStrictEqual(es.lastEditPayload, { author: 'Autor Editado', content: 'Texto atualizado no acervo.', links: [{ text: 'Material editado', url: 'https://example.com/editado' }], embeds: [{ text: 'Cartão atualizado.', links: [] }] });
    await ep.locator('#messageSearch').fill('atualizado');
    await ep.waitForFunction(() => document.querySelectorAll('.message').length === 1);
    assert((await firstItem().textContent()).includes('Texto atualizado'));
    await ep.locator('#messageSearch').fill('');
    await ep.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    await firstItem().locator('[data-action="delete-message"]').click();
    assert(await ep.locator('#deleteMessageDialog').isVisible());
    await ep.locator('#cancelDeleteMessage').click();
    assert(!es.deleted.has('m1'), 'Cancelar confirmação preserva item.');
    await firstItem().locator('[data-action="delete-message"]').click();
    await ep.locator('#confirmDeleteMessage').click();
    await ep.waitForFunction(() => !document.querySelector('.message[data-message-id="m1"]'));
    assert.strictEqual(es.deleted.has('m1'), true);
    await ep.locator('#toggleTrash').click();
    await ep.waitForFunction(() => document.querySelector('.message[data-message-id="m1"] .restore-action'));
    assert.strictEqual(await firstItem().locator('[data-action="edit-message"]').count(), 0);
    await firstItem().locator('[data-action="restore-message"]').click();
    await ep.waitForFunction(() => document.querySelector('#timelineStatus').textContent.includes('lixeira deste canal está vazia'));
    assert.strictEqual(es.deleted.has('m1'), false);
    await ep.locator('#toggleTrash').click();
    await ep.waitForFunction(() => document.querySelector('.message[data-message-id="m1"] .message-text')?.textContent === 'Texto atualizado no acervo.');

    es.failMutation = true;
    await firstItem().locator('[data-action="edit-message"]').click();
    await ep.locator('#editContent').fill('Rascunho mantido após falha.');
    await ep.locator('#saveEditMessage').click();
    await ep.waitForFunction(() => document.querySelector('#editMessageFeedback').textContent.includes('rascunho foi mantido'));
    await ep.locator('#cancelEditMessage').click();
    await firstItem().locator('[data-action="edit-message"]').click();
    assert.strictEqual(await ep.locator('#editContent').inputValue(), 'Rascunho mantido após falha.');
    es.failMutation = false;
    es.holdEdit = true;
    await ep.locator('#editContent').fill('Versão enviada.');
    await ep.locator('#saveEditMessage').click();
    await es.editStarted.promise;
    await ep.locator('#editContent').fill('Texto mais recente no rascunho.');
    es.editRelease.resolve();
    await ep.waitForFunction(() => document.querySelector('#editMessageFeedback').textContent.includes('alterações mais recentes'));
    assert(await ep.locator('#editMessageDialog').isVisible());
    assert.strictEqual(await ep.locator('#editContent').inputValue(), 'Texto mais recente no rascunho.');
    await ep.waitForFunction(() => !document.querySelector('#saveEditMessage').disabled);
    es.holdEdit = false;
    await ep.locator('#saveEditMessage').click();
    await ep.waitForFunction(() => document.querySelector('.message[data-message-id="m1"] .message-text')?.textContent === 'Texto mais recente no rascunho.');

    await ep.locator('#editChannel').click();
    await ep.locator('#editChannelName').fill('canal-atualizado');
    await ep.locator('#editChannelCategory').fill('Categoria atualizada');
    await ep.locator('#editChannelTopic').fill('Descrição atualizada.');
    await ep.locator('#saveEditChannel').click();
    await ep.waitForFunction(() => document.querySelector('#channelName').textContent === 'canal-atualizado');
    assert((await ep.locator('#channelList').textContent()).includes('Categoria atualizada'));
    assert.strictEqual(await ep.locator('#channelTopic').textContent(), 'Descrição atualizada.');

    es.holdEdit = true;
    es.editStarted = deferred();
    es.editRelease = deferred();
    es.lastAppliedEdit = null;
    await firstItem().locator('[data-action="edit-message"]').click();
    await ep.locator('#editContent').fill('Salvamento iniciado no primeiro canal.');
    await ep.locator('#saveEditMessage').click();
    await es.editStarted.promise;
    await ep.locator('#closeEditMessage').click();
    await ep.getByRole('button', { name: /referências/ }).click();
    await ep.waitForFunction(() => document.querySelector('.message[data-message-id="ref1"]'));
    await ep.locator('.message[data-message-id="ref1"] [data-action="edit-message"]').click();
    await ep.locator('#editContent').fill('Rascunho independente do segundo canal.');
    es.editRelease.resolve();
    await ep.waitForFunction(() => document.querySelector('#statMessages').textContent === '4');
    await ep.waitForTimeout(80);
    assert.strictEqual(es.lastAppliedEdit, 'm1');
    assert.strictEqual(await ep.locator('#channelName').textContent(), 'referências', 'Resposta de edição antiga não troca canal.');
    assert(await ep.locator('#editMessageDialog').isVisible(), 'Resposta antiga não fecha outro editor.');
    assert.strictEqual(await ep.locator('#editContent').inputValue(), 'Rascunho independente do segundo canal.', 'Resposta antiga preserva rascunho do segundo item.');
    await ep.locator('#cancelEditMessage').click();
    await screenshot(ep, 'cofre-edicao');
    await clean(editing);

    const longArchive = await scenario(browser, { manyMessages: true });
    const lp = longArchive.page;
    await lp.waitForFunction(() => document.querySelectorAll('.message').length === 100);
    await lp.locator('#loadMore').click();
    await lp.waitForFunction(() => document.querySelectorAll('.message').length === 150);
    const lateItem = lp.locator('.message[data-message-id="many120"]');
    await lateItem.evaluate(article => article.scrollIntoView({ block: 'center' }));
    const beforeScroll = await lp.locator('#messageScroll').evaluate(scroll => scroll.scrollTop);
    await lateItem.locator('[data-action="edit-message"]').click();
    await lp.locator('#editContent').fill('Item 120 atualizado no acervo.');
    await lp.locator('#saveEditMessage').click();
    await lp.waitForFunction(() => document.querySelectorAll('.message').length === 150 && document.querySelector('.message[data-message-id="many120"] .message-text')?.textContent === 'Item 120 atualizado no acervo.');
    assert(Math.abs((await lp.locator('#messageScroll').evaluate(scroll => scroll.scrollTop)) - beforeScroll) <= 2, 'Edição além da primeira página mantém a posição.');
    await lateItem.locator('[data-action="delete-message"]').click();
    await lp.locator('#confirmDeleteMessage').click();
    await lp.waitForFunction(() => document.querySelectorAll('.message').length === 149 && !document.querySelector('.message[data-message-id="many120"]'));
    assert(await lp.locator('.message[data-message-id="many150"]').count(), 'Exclusão mantém carregado todo o trecho posterior à primeira página.');
    assert(Math.abs((await lp.locator('#messageScroll').evaluate(scroll => scroll.scrollTop)) - beforeScroll) <= 2, 'Exclusão preserva a âncora de leitura.');
    await clean(longArchive);

    const changedIdentity = await scenario(browser);
    const ip = changedIdentity.page;
    await ip.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    await ip.locator('.message[data-message-id="m1"] [data-action="edit-message"]').click();
    await ip.locator('#editContent').fill('Rascunho privado da primeira conta.');
    // Same-tab changes do not emit storage events here: submission must check identity itself.
    await ip.evaluate(() => {
      localStorage.setItem('access_token', 'fixture-other-session');
      localStorage.setItem('user_data', JSON.stringify({ client_id: 'fixture-client-b', username: 'fixture-user-b' }));
    });
    await ip.locator('#saveEditMessage').click();
    await ip.waitForFunction(() => document.querySelector('#notice').textContent.includes('Sua sessão mudou'));
    assert(await ip.locator('#editMessageDialog').isHidden());
    assert.strictEqual(await ip.locator('#editContent').inputValue(), '');
    assert.strictEqual(await ip.locator('.message').count(), 0);
    assert.strictEqual(changedIdentity.state.requests.filter(item => item.method === 'PATCH').length, 0, 'Rascunho antigo não é enviado na sessão nova.');
    await clean(changedIdentity);

    const changedInFlight = await scenario(browser);
    const fp = changedInFlight.page;
    const fstate = changedInFlight.state;
    await fp.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    await fp.locator('.message[data-message-id="m1"] [data-action="edit-message"]').click();
    await fp.locator('#editContent').fill('Versão enviada na sessão original.');
    fstate.holdEdit = true;
    await fp.locator('#saveEditMessage').click();
    await fstate.editStarted.promise;
    const secondTab = await fp.context().newPage();
    await secondTab.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<main>Troca de sessão fictícia</main>' }));
    await secondTab.goto(origin + '/session-fixture');
    await secondTab.evaluate(() => {
      localStorage.setItem('access_token', 'fixture-second-tab-session');
      localStorage.setItem('user_data', JSON.stringify({ client_id: 'fixture-client-b', username: 'fixture-user-b' }));
    });
    await fp.waitForFunction(() => document.querySelector('#notice').textContent.includes('Sua sessão mudou'));
    fstate.editRelease.resolve();
    await fp.waitForTimeout(80);
    assert.strictEqual(await fp.locator('.message').count(), 0, 'Resposta da sessão anterior não repopula o acervo.');
    assert(await fp.locator('#editMessageDialog').isHidden());
    assert.strictEqual(fstate.requests.filter(item => item.method === 'PATCH').length, 1, 'Somente a mutação já iniciada na sessão original ocorreu.');
    await secondTab.close();
    await clean(changedInFlight);

    const mobile = await scenario(browser, { viewport: { width: 390, height: 844 } });
    await mobile.page.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    await noOverflow(mobile.page);
    assert(await mobile.page.locator('#archiveDetails').isHidden());
    await mobile.page.locator('#openChannels').click();
    assert.strictEqual(await mobile.page.locator('#openChannels').getAttribute('aria-expanded'), 'true');
    await mobile.page.getByRole('button', { name: /referências/ }).click();
    await mobile.page.waitForFunction(() => document.querySelector('#channelName').textContent === 'referências');
    assert.strictEqual(await mobile.page.locator('#openChannels').getAttribute('aria-expanded'), 'false');
    await mobile.page.locator('.message [data-action="edit-message"]').click();
    await noOverflow(mobile.page);
    await mobile.page.locator('#editContent').fill('Rascunho no celular.');
    await mobile.page.keyboard.press('Escape');
    assert(await mobile.page.locator('#editMessageDialog').isHidden());
    await mobile.page.locator('#toggleDetails').click();
    assert(await mobile.page.locator('#archiveDetails').isVisible());
    await mobile.page.locator('#closeDetails').click();
    await screenshot(mobile.page, 'cofre-mobile');
    await clean(mobile);

    const empty = await scenario(browser, { empty: true });
    await empty.page.waitForFunction(() => document.querySelector('#timelineStatus').textContent === '' && document.querySelector('#serverList').children.length === 0);
    assert(await empty.page.locator('#emptyState').isVisible());
    await screenshot(empty.page, 'cofre-vazio');
    await empty.page.locator('#emptyImport').click();
    assert(await empty.page.locator('#importDialog').isVisible());
    await empty.page.locator('#importFile').setInputFiles({ name: 'fixture-cofre.json', mimeType: 'application/json', buffer: Buffer.from('{"fixture":true}') });
    await empty.page.locator('#submitImport').click();
    await empty.page.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    assert(empty.state.imported);
    assert((await empty.page.locator('#notice').textContent()).includes('4 mensagens importadas'));
    assert(await empty.page.locator('#importDialog').isHidden());
    await clean(empty);

    const failure = await scenario(browser, { failMessages: true });
    await failure.page.waitForFunction(() => document.querySelector('#loadMore').textContent === 'Tentar novamente');
    assert((await failure.page.locator('#notice').textContent()).includes('Falha temporária'));
    failure.state.failMessages = false;
    await failure.page.locator('#loadMore').click();
    await failure.page.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    await clean(failure);

    const reader = await scenario(browser, { sharing: { configured: true, enabled: true, can_publish: false, revision: 7, last_sync: '2026-10-22T11:00:00Z' } });
    const rp = reader.page;
    await rp.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    assert.strictEqual(await rp.locator('#sharingLabel').textContent(), 'Compartilhado com a empresa');
    assert((await rp.locator('#sharingDescription').textContent()).includes('Somente leitura'));
    for (const id of ['railImport', 'sidebarImport', 'emptyImport', 'detailImport', 'footerImport', 'editChannel', 'publishSharing']) assert(await rp.locator('#' + id).isHidden(), 'Leitor não vê mutação: ' + id);
    assert.strictEqual(await rp.locator('.message-action').count(), 0);
    await rp.evaluate(() => document.querySelector('#importForm').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
    assert.strictEqual(reader.state.requests.filter(item => /PATCH|DELETE/.test(item.method)).length, 0);
    assert.strictEqual(reader.state.imported, false, 'Leitor também é bloqueado na entrada de importação.');
    const initialOrder = reader.state.requests.map(item => new URL(item.url).pathname);
    assert(initialOrder.indexOf('/api/cofre/compartilhamento/sincronizar') < initialOrder.indexOf('/api/cofre/servidores'), 'Sincronização antecede primeira leitura.');
    await screenshot(rp, 'cofre-compartilhado-leitor');
    await clean(reader);

    const localReader = await scenario(browser, { sharing: { configured: true, enabled: false, can_publish: false, revision: 0 } });
    await localReader.page.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    assert.strictEqual(await localReader.page.locator('#sharingLabel').textContent(), 'Cópia local');
    assert.strictEqual(await localReader.page.locator('.message-action').count(), 0, 'Empresa configurada bloqueia escrita de leitor antes da primeira publicação.');
    for (const id of ['railImport', 'editChannel', 'publishSharing']) assert(await localReader.page.locator('#' + id).isHidden());
    await clean(localReader);

    const offline = await scenario(browser, { sharing: { configured: true, enabled: true, can_publish: false, revision: 7 }, failSharingSync: true });
    await offline.page.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    assert.strictEqual(await offline.page.locator('#sharingLabel').textContent(), 'Compartilhado com a empresa');
    assert((await offline.page.locator('#sharingDescription').textContent()).includes('cópia disponível foi mantida'));
    assert((await offline.page.locator('.message[data-message-id="m1"] .message-text').textContent()).includes('Bem-vindos ao acervo'));
    assert.strictEqual(await offline.page.locator('.message-action').count(), 0, 'Falha de rede não promove leitor a escritor.');
    await clean(offline);

    const unavailableStatus = await scenario(browser, { failSharingStatus: true });
    await unavailableStatus.page.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    assert.strictEqual(await unavailableStatus.page.locator('.message-action').count(), 0, 'Estado desconhecido não libera escrita.');
    assert(await unavailableStatus.page.locator('#syncSharing').isVisible());
    await clean(unavailableStatus);

    const publishing = await scenario(browser, { viewport: { width: 390, height: 844 }, sharing: { configured: true, enabled: false, can_publish: true, revision: 0 } });
    const pp = publishing.page;
    await pp.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    assert.strictEqual(await pp.locator('#sharingLabel').textContent(), 'Cópia local');
    assert(await pp.locator('#publishSharing').isVisible());
    assert(await pp.locator('#railImport').isVisible());
    await noOverflow(pp);
    await pp.locator('#publishSharing').click();
    assert(await pp.locator('#publishSharingDialog').isVisible());
    assert((await pp.locator('#publishSharingDialog').textContent()).includes('pessoas da sua empresa'));
    await noOverflow(pp);
    await pp.locator('#cancelPublishSharing').click();
    assert.strictEqual(publishing.state.publishCalls, 0, 'Cancelar confirmação não publica.');
    await pp.locator('#publishSharing').click();
    await pp.locator('#confirmPublishSharing').click();
    await pp.waitForFunction(() => document.querySelector('#sharingLabel').textContent === 'Compartilhado com a empresa'
      && !document.querySelector('#publishSharingDialog').open
      && document.querySelector('#notice').textContent === 'Acervo compartilhado com a empresa.'
      && !document.querySelector('#publishSharing').disabled);
    assert.strictEqual(publishing.state.publishCalls, 1);
    await pp.locator('.message[data-message-id="m1"] [data-action="edit-message"]').waitFor();
    assert(await pp.locator('.message[data-message-id="m1"] [data-action="edit-message"]').isVisible());
    await clean(publishing);

    const publishedWithViewError = await scenario(browser, { failViewAfterPublish: true, sharing: { configured: true, enabled: false, can_publish: true, revision: 0 } });
    await publishedWithViewError.page.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    await publishedWithViewError.page.locator('#publishSharing').click();
    await publishedWithViewError.page.locator('#confirmPublishSharing').click();
    await publishedWithViewError.page.waitForFunction(() => document.querySelector('#notice').textContent.includes('Não foi possível atualizar a visualização'));
    assert(publishedWithViewError.state.sharing.enabled, 'Publicação concluída continua marcada mesmo se atualizar a visualização falhar.');
    assert(await publishedWithViewError.page.locator('#publishSharingDialog').isHidden());
    assert((await publishedWithViewError.page.locator('#notice').textContent()).startsWith('Acervo compartilhado com a empresa.'));
    await clean(publishedWithViewError);

    const conflict = await scenario(browser, { sharing: { configured: true, enabled: false, can_publish: true, revision: 1 }, conflictPublish: true });
    await conflict.page.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    await conflict.page.locator('#publishSharing').click();
    await conflict.page.locator('#confirmPublishSharing').click();
    await conflict.page.waitForFunction(() => document.querySelector('#publishSharingFeedback').textContent.includes('Sincronize antes de publicar'));
    assert((await conflict.page.locator('.message[data-message-id="m1"] .message-text').textContent()).includes('Bem-vindos ao acervo'), 'Conflito mantém acervo disponível.');
    await conflict.page.locator('#syncBeforePublish').click();
    await conflict.page.waitForFunction(() => !document.querySelector('#publishSharingDialog').open && !document.querySelector('#syncSharing').disabled);
    assert.strictEqual(conflict.state.syncCalls, 2);
    assert.strictEqual(conflict.state.publishCalls, 1, 'Sincronizar conflito não publica automaticamente.');
    await clean(conflict);

    const focusSharing = await scenario(browser, { clock: true, sharing: { configured: true, enabled: true, can_publish: true, revision: 7 } });
    const sp = focusSharing.page;
    const ss = focusSharing.state;
    await sp.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    assert.strictEqual(ss.syncCalls, 1);
    await sp.clock.fastForward(5000);
    assert.strictEqual(ss.syncCalls, 1, 'Não existe polling a cada5segundos.');
    await sp.evaluate(() => window.dispatchEvent(new Event('focus')));
    await sp.locator('#refreshMessages').click();
    assert.strictEqual(ss.syncCalls, 1, 'Foco e Atualizar respeitam intervalo mínimo.');
    await sp.locator('.message[data-message-id="m1"] [data-action="edit-message"]').click();
    await sp.locator('#editContent').fill('Meu rascunho permanece durante a sincronização.');
    await sp.locator('#cancelEditMessage').click();
    await sp.clock.fastForward(60000);
    ss.remoteContent = 'Revisão nova recebida da empresa.';
    ss.syncChanged = true;
    ss.sharing.revision = 8;
    await sp.evaluate(() => window.dispatchEvent(new Event('focus')));
    await sp.waitForFunction(() => document.querySelector('.message[data-message-id="m1"] .message-text')?.textContent === 'Revisão nova recebida da empresa.');
    assert.strictEqual(ss.syncCalls, 2);
    await sp.locator('.message[data-message-id="m1"] [data-action="edit-message"]').click();
    assert.strictEqual(await sp.locator('#editContent').inputValue(), 'Meu rascunho permanece durante a sincronização.');
    await sp.locator('#cancelEditMessage').click();
    await sp.clock.fastForward(5000);
    assert.strictEqual(ss.syncCalls, 2);
    await clean(focusSharing);

    const changedCompany = await scenario(browser, { sharing: { configured: true, enabled: true, can_publish: true, revision: 7 } });
    const cp = changedCompany.page;
    const cs = changedCompany.state;
    await cp.waitForFunction(() => document.querySelectorAll('.message').length === 2);
    cs.holdSharingSync = true;
    await cp.locator('#syncSharing').click();
    await cs.sharingSyncStarted.promise;
    await cp.evaluate(() => {
      // Company change is rejected even if a client keeps the same token/client id.
      const user = JSON.parse(localStorage.getItem('user_data'));
      user.company_id = 'fixture-company-b';
      localStorage.setItem('user_data', JSON.stringify(user));
      window.dispatchEvent(new Event('focus'));
    });
    await cp.waitForFunction(() => document.querySelector('#notice').textContent.includes('Sua sessão mudou'));
    cs.sharingSyncRelease.resolve();
    await cp.waitForTimeout(80);
    assert.strictEqual(await cp.locator('.message').count(), 0, 'Sincronização da empresa anterior não preenche contexto novo.');
    assert(await cp.locator('#syncSharing').isHidden());
    assert(await cp.locator('#publishSharing').isHidden());
    await clean(changedCompany);

    const denied = await scenario(browser, { allowed: false });
    await denied.page.waitForURL(origin + '/dashboard.html');
    assert.strictEqual(denied.state.requests.filter(item => item.url.includes('/api/cofre/')).length, 0, 'Sem permissão não consulta acervo.');
    await clean(denied);
    console.log('PASS cofre_browser: navigation, categories, pagination, stale search, XSS, authenticated media/downloads, object URL cleanup, import, edit/search, delete/trash/restore, channel edits, draft recovery, concurrent saves, 150-message pagination/scroll, cross-session/company isolation, sharing/offline/reader access, publication/CAS conflict, focus throttling without polling, retry, permissions and mobile layout. Fictional fixtures only.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
