'use strict';
// In-memory fixture with a controlled browser clock. No real API or tenant data.
const assert = require('assert');
const path = require('path');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.JK_TEST_BROWSER_PATH
    ? { executablePath: process.env.JK_TEST_BROWSER_PATH } : { channel: 'chrome' }) });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.clock.install({ time: new Date('2026-09-09T12:00:00Z') });
    await page.clock.pauseAt(new Date('2026-09-09T12:00:01Z'));
    await page.setContent('<div id="ai-training-sku-details"></div><div id="ai-training-sku-editor" class="hidden"></div><div id="ai-training-sku-popover"></div>');
    await page.evaluate(() => {
      Object.assign(window, {
        token: 'tenant-a-token', activeStore: 'store-a',
        treinamentoSync: { stores: new Map(), requestId: 0, skuRequestId: 0 },
        treinamentoDetalhesRequestId: 0, treinamentoDetalhesController: null, treinamentoCatalogoRequestId: 0,
        state: { lojas: [{ store_id: 'store-a', seller_id: '101', site_id: 'MLB' }],
          treinamentoContexto: { notas_sku: {} }, treinamentoDados: { perguntas_anuncio: { exemplos: [] } }, produtosTreinamento: [] },
        aiTrainingStatus: {}, aiTrainingSku: { value: '001' },
        aiTrainingSkuEditor: document.getElementById('ai-training-sku-editor'),
        aiTrainingSkuPopover: document.getElementById('ai-training-sku-popover'),
        aiTrainingOrientacoes: {}, aiTrainingContextoLoja: {}, aiTrainingCompatibilidade: {}, aiTrainingProibicoes: {}, aiTrainingNotasSku: {},
        obterAuthHeaders: () => ({ Authorization: token }), lojaEscopoTreinamento: () => activeStore,
        iguaisTreinamento: (a, b) => JSON.stringify(a) === JSON.stringify(b),
        exemplosDoSkuTreinamento: (examples, sku) => (examples || []).filter(item => item.sku === sku),
        erroRespostaTreinamento: (data, fallback) => data.detail?.message || data.detail || fallback,
        mensagemErro: error => error.message, guardarEdicaoTreinamento() {},
        renderizarNotasSkuTreinamento() {}, atualizarEstadoSincronizacaoTreinamento() {}, renderizarConflitosTreinamento() {},
        calls: [], queue: [], fallback: { status: 503, body: { detail: 'Leitura indisponível' } }
      });
      window.sessaoTreinamento = () => {
        const key = identidadeSessaoTreinamento();
        if (!treinamentoSync.stores.has(key)) treinamentoSync.stores.set(key, { identity: key, snapshot: null, drafts: {} });
        return treinamentoSync.stores.get(key);
      };
      window.renderizarDetalhesSkuTreinamento = () => {
        const session = sessaoTreinamento();
        document.getElementById('ai-training-sku-details').textContent = [session.skuDetails?.[aiTrainingSku.value]?.source_body, session.detailsError].filter(Boolean).join('|');
      };
      window.exibirLeituraOrientacaoSku = renderizarDetalhesSkuTreinamento;
      window.ficha = (revision = 'r1', text = 'conteúdo A', note = 'orientação A', status = 'ready') => ({ body: {
        success: true, store_id: 'store-a', seller_id: '101', site_id: 'MLB', sku: '001',
        sku_details: { sku: '001', source_body: text, source_revision: 'source-1', characteristics: [] },
        editorial: { revision, skus: { '001': { status: 'draft' } } },
        notas_sku: { '001': note }, caracteristicas_sku: { '001': {} }, exemplos: { perguntas_anuncio: [] },
        snapshot: { generation: revision, checked_at: revision, state: status }
      } });
      window.fetch = async (url, options) => {
        calls.push({ url, at: Date.now(), signal: options.signal, method: options.method || 'GET' });
        if (options.method === 'POST') return { ok: true, status: 202, json: async () => ({ success: true }) };
        const response = queue.shift() || fallback;
        const result = () => ({ ok: !response.status || response.status < 400, status: response.status || 200, json: async () => response.body });
        if (response.hold) return new Promise(resolve => { window.release = () => resolve(result()); });
        if (response.hang) return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('timeout', 'AbortError')), { once: true }));
        return result();
      };
    });
    await page.addScriptTag({ path: path.join(__dirname, '../static/perguntas_pos_venda/treinamento-ficha.js') });
    const start = () => page.evaluate(() => { void carregarDetalhesSkuTreinamento(true); });
    await page.evaluate(() => {
      const session = sessaoTreinamento();
      session.snapshot = { orientacoes_perguntas: 'general untouched', editorial: { revision: 'general-r0', general: { status: 'published' } },
        notas_sku: { '002': 'other SKU untouched' }, exemplos: { perguntas_anuncio: [{ sku: '002', resposta: 'other example' }] } };
      queue.push(ficha());
    });
    await start();
    assert.strictEqual(await page.locator('#ai-training-sku-details').textContent(), 'conteúdo A');
    assert.deepStrictEqual(await page.evaluate(() => [sessaoTreinamento().snapshot.editorial.revision,
      sessaoTreinamento().snapshot.orientacoes_perguntas, sessaoTreinamento().snapshot.notas_sku['002'], revisaoEditorTreinamento(sessaoTreinamento(), 'sku:001')]),
      ['general-r0', 'general untouched', 'other SKU untouched', 'r1']);

    await page.evaluate(() => {
      const invalid = ficha('invalid-r2', 'technical still available', '');
      invalid.body.sku_details.editorial_state = 'invalid';
      invalid.body.editorial.skus['001'].status = 'invalid';
      queue.push(invalid);
    });
    await start();
    assert.deepStrictEqual(await page.evaluate(() => [sessaoTreinamento().snapshot.notas_sku['001'], revisaoEditorTreinamento(sessaoTreinamento(), 'sku:001')]),
      ['orientação A', 'r1'], 'an invalid editorial refresh cannot promote a revision over old content');

    await page.evaluate(() => {
      sessaoTreinamento().drafts['sku:001'] = { revision: 'r1', sourceRevision: 'source-1',
        base: { notas: 'orientação A', exemplos: [], caracteristicas: {} },
        value: { notas: 'edição local', exemplos: [], caracteristicas: {} } };
      queue.push(ficha('r2', 'conteúdo novo', 'mudança externa'));
    });
    await start();
    assert.deepStrictEqual(await page.evaluate(() => { const d = sessaoTreinamento().drafts['sku:001']; return [d.revision, d.base.notas, d.value.notas, d.conflict]; }),
      ['r1', 'orientação A', 'edição local', true]);

    await page.evaluate(() => { calls.length = 0; });
    await start();
    assert.match(await page.locator('#ai-training-sku-details').textContent(), /conteúdo novo/);
    await page.clock.runFor(30000);
    assert.deepStrictEqual(await page.evaluate(() => { const reads = calls.filter(c => c.method === 'GET'); return reads.map(c => c.at - reads[0].at); }), [0, 2000, 6000, 14000, 22000]);
    assert.strictEqual(await page.evaluate(() => calls.filter(c => c.method === 'POST').length), 1);
    assert.strictEqual(await page.evaluate(() => sessaoTreinamento().detailsLoading), '');
    assert.strictEqual(await page.evaluate(() => sessaoTreinamento().detailsRetryStopped), true);

    // A request already in flight when saving starts cannot roll back the saved UI.
    await page.evaluate(() => queue.push({ ...ficha('old', 'old response'), hold: true }));
    await start();
    await page.evaluate(() => {
      invalidarConsultasFichaTreinamento();
      confirmarFichaSalvaTreinamento(sessaoTreinamento(), { editorial: { revision: 'saved-r3' } }, '001');
      sessaoTreinamento().skuDetails['001'].source_body = 'saved content';
      renderizarDetalhesSkuTreinamento(); release();
    });
    assert.strictEqual(await page.locator('#ai-training-sku-details').textContent(), 'saved content');

    await page.evaluate(() => queue.push({ ...ficha('old-session', 'forbidden old session'), hold: true }));
    await start();
    await page.evaluate(() => { token = 'tenant-b-token'; dispatchEvent(new Event('storage')); release(); });
    assert.strictEqual(await page.locator('#ai-training-sku-details').textContent(), '');
    assert.strictEqual(await page.evaluate(() => [...treinamentoSync.stores.values()].some(s => Object.keys(s.drafts).length)), false);
    await page.evaluate(() => { aiTrainingSku.value = '001'; queue.push(ficha()); });
    await start();
    await page.evaluate(() => queue.push({ status: 403, body: { detail: 'revoked' } }));
    await start();
    assert.strictEqual(await page.locator('#ai-training-sku-details').textContent(), '');
    const denied = await page.evaluate(() => calls.length);
    await page.clock.runFor(30000);
    assert.strictEqual(await page.evaluate(() => calls.length), denied);

    await page.evaluate(() => { aiTrainingSku.value = '001'; fallback = { hang: true }; calls.length = 0; });
    await start(); await page.clock.runFor(30000);
    assert.strictEqual(await page.evaluate(() => sessaoTreinamento().detailsLoading), '');
    assert(await page.evaluate(() => calls.filter(c => c.method === 'GET').every(c => c.signal.aborted)));
    await page.evaluate(() => dispatchEvent(new Event('jk:logout')));
    assert.strictEqual(await page.locator('#ai-training-sku-details').textContent(), '');
    assert.deepStrictEqual(errors, []);
    console.log('Treinamento ficha browser: OK (partial merge, CAS, stale, deadline, session/revocation, save supersession)');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
