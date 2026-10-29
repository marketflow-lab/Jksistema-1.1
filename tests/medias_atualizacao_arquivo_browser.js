'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const editorFixture = require('./helpers/medias_autosalvamento_fixture');

const root = path.resolve(__dirname, '..');
const helper = path.join(root, 'static/medias_compras/atualizacao-arquivo.js');
const stylesheet = path.join(root, 'static/medias_compras/atualizacao-arquivo.css');
const excluded = Array.from({ length: 75 }, (_, index) => ({
  tipo: 'sku_ausente', sku: 'Ausente-' + String(index + 1).padStart(3, '0'),
  titulo_atual: 'Produto removido ' + (index + 1),
}));
const injection = '<img src=x onerror="window.injected=true">';
const changes = [{ sku: '001', campos: [
  { campo: 'Quantidade', antes: 2, depois: 5 },
  { campo: 'Valor unidade', antes: 10, depois: 6 },
  { campo: 'Valor total', antes: 20, depois: 30 },
  { campo: 'Descrição', antes: 'Original', depois: injection },
] }];

async function install(page, options = {}) {
  await page.setViewportSize({ width: 1080, height: 680 });
  await page.goto('https://arquivo.test/');
  if (fs.existsSync(stylesheet)) await page.addStyleTag({ path: stylesheet });
  await page.addScriptTag({ path: helper });
  await page.evaluate(({ options, excluded, changes, injection }) => {
    window.requests = [];
    window.statuses = [];
    window.finished = false;
    window.refreshed = 0;
    window.contextValid = true;
    window.injected = false;
    const pendencias = options.noPending ? [] : [
      { tipo: 'sku_novo', sku: '003', titulo_arquivo: 'Novo' },
      { tipo: 'titulo_diferente', sku: '001', titulo_atual: 'Original', titulo_arquivo: injection },
      ...(options.additive ? [] : excluded),
    ];
    const preview = {
      success: false, previsualizacao: true, requer_confirmacao: pendencias.length > 0,
      token_confirmacao: 'synthetic-state-token', pendencias,
      resumo_pendencias: { total: pendencias.length, total_sku_novo: options.noPending ? 0 : 1,
        total_titulo_diferente: options.noPending ? 0 : 1,
        total_sku_ausente: options.additive || options.noPending ? 0 : excluded.length },
      relatorio: { atualizados: changes, incluidos: [{ sku: '003', item: { Quantidade: 7, 'Valor unidade': 2 } }],
        excluidos: excluded.map(row => ({ sku: row.sku, item: { Quantidade: 3, 'Valor unidade': 4 } })),
        mantidos: [], inalterados: [], lista: [{ campo: 'Status', antes: 'Em Orçamento', depois: 'Analisando orçamento' }] },
    };
    window.config = {
      listaId: 'synthetic-list', nomeLista: 'Pedido de teste', sincronizar: !options.additive,
      headers: () => ({ 'X-Synthetic-Header': 'test-only' }),
      vigente: () => window.contextValid,
      preparar: async () => {},
      status: (text, error) => window.statuses.push({ text, error: !!error }),
      recarregar: async () => {
        window.refreshed += 1;
        if (options.refreshError) throw new Error('Falha de recarga sintética');
      },
      fetch: async (url, init) => {
        const fields = Object.fromEntries(Array.from(init.body.entries())
          .filter(([key]) => key !== 'file'));
        window.requests.push({ url, fields, headers: init.headers });
        if (fields.previsualizar === '1') return { ok: true, json: async () => structuredClone(preview) };
        if (options.saveError) return { ok: false, status: 409, json: async () => ({ detail: 'A lista mudou. Recarregue.' }) };
        if (options.unsuccessful) return { ok: true, json: async () => ({ success: false, mensagem: 'Atualização não aplicada.' }) };
        const report = structuredClone(preview.relatorio);
        if (fields.confirmar_exclusoes !== '1' || options.noPending || options.additive) {
          report.mantidos = options.noPending || options.additive ? [] : report.excluidos;
          report.excluidos = [];
        }
        return { ok: true, json: async () => ({
          success: true, relatorio: report, lista: { id: 'synthetic-list' },
          total_skus_atualizados: 1, total_quantidades_atualizadas: 1,
          total_skus_incluidos: 1, total_skus_excluidos: report.excluidos.length,
          total_skus_lidos: 2,
        }) };
      },
    };
    document.getElementById('start').onclick = () => {
      window.completion = Promise.resolve(window.JKAtualizacaoArquivo.atualizar(window.config))
        .then(() => { window.finished = true; })
        .catch(error => { window.failure = String(error); window.finished = true; });
    };
  }, { options, excluded, changes, injection });
}

async function selectFile(page) {
  const chooser = page.waitForEvent('filechooser');
  await page.click('#start');
  await (await chooser).setFiles({ name: 'synthetic.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('synthetic') });
}

async function finish(page) {
  await page.waitForFunction(() => window.finished);
  assert.strictEqual(await page.evaluate(() => window.failure), undefined);
}

async function closeReport(page) {
  await page.click('#jkAtualizacaoArquivoFechar');
  await finish(page);
  assert.strictEqual(await page.locator('#jkAtualizacaoArquivoRelatorio').count(), 0);
}

async function explicitFileTotalsSurviveEditor(browser) {
  for (const [total, unit] of [[0, 10], [7, 10], [7, 1.2345]]) {
    const test = await editorFixture.fixture(browser, { rows: 1 });
    const { page } = test;
    try {
      await page.evaluate(({ total, unit }) => {
        Object.assign(__autosaveFixture.lists['list-a'].itens[0], {
          Quantidade: 2, 'Valor unidade': unit, 'Valor total': total, valor_total_informado: true,
        });
      }, { total, unit });
      await editorFixture.openList(page, 'B');
      await editorFixture.openList(page, 'A');
      assert.strictEqual(await editorFixture.field(page, 'vtot').inputValue(), String(total));
      assert((await page.locator('#tfootVtot').textContent()).includes(total + ',00'));
      let captured = await page.evaluate(() => JKMedias.modules.editor.capturarItensListaPedidoDoEditor()[0]);
      assert.strictEqual(captured['Valor total'], total);
      assert.strictEqual(captured['Valor unidade'], unit);
      assert.strictEqual(captured.valor_total_informado, true);

      await editorFixture.field(page, 'vun').focus();
      await page.locator('#nomeListaPedidoEdit').click();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
      assert.strictEqual(await editorFixture.field(page, 'vtot').inputValue(), String(total),
        'focus/blur without a price edit must preserve the total from the file');
      assert.strictEqual((await editorFixture.calls(page, 'save:list-a')).length, 0);

      await editorFixture.field(page, 'embalagem').fill('Nova embalagem');
      await editorFixture.field(page, 'embalagem').press('Tab');
      await editorFixture.waitServer(page, () => __autosaveFixture.lists['list-a'].itens[0]['Individual packaging'] === 'Nova embalagem');
      let saved = (await editorFixture.serverList(page)).itens[0];
      assert.strictEqual(saved['Valor total'], total, 'an unrelated edit must retain the imported total');
      assert.strictEqual(saved['Valor unidade'], unit, 'formatting must not reduce imported precision');
      assert.strictEqual(saved.valor_total_informado, true);

      await editorFixture.field(page, 'qtd').fill('3');
      await editorFixture.field(page, 'qtd').press('Tab');
      await editorFixture.waitServer(page, () => __autosaveFixture.lists['list-a'].itens[0].Quantidade === 3);
      saved = (await editorFixture.serverList(page)).itens[0];
      assert.strictEqual(saved['Valor total'], Math.round(3 * unit * 100) / 100);
      assert(!saved.valor_total_informado, 'a real quantity edit intentionally recalculates the total');
      if (unit === 1.2345) {
        await editorFixture.field(page, 'vun').fill('1,23');
        await editorFixture.field(page, 'vun').press('Tab');
        await editorFixture.waitServer(page, () => __autosaveFixture.lists['list-a'].itens[0]['Valor unidade'] === 1.23);
        assert.strictEqual((await editorFixture.serverList(page)).itens[0]['Valor total'], 3.69);
      }
    } finally {
      await editorFixture.clean(test);
    }
  }
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.route('https://arquivo.test/', route => route.fulfill({
    contentType: 'text/html; charset=utf-8',
    body: '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"></head>' +
      '<body><button id="start">Atualizar por arquivo</button></body></html>',
  }));
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    // The confirmation contains all removals, including those beyond the old eight-item sample.
    await install(page);
    await selectFile(page);
    const confirmation = page.locator('#jkAtualizacaoArquivoConfirmacao');
    await confirmation.waitFor({ state: 'visible' });
    assert((await confirmation.textContent()).includes('Ausente-075'));
    assert.strictEqual(await page.evaluate(() => window.requests.length), 1);
    await page.click('#jkAtualizacaoArquivoCancelar');
    await finish(page);
    assert.strictEqual(await page.evaluate(() => window.requests.length), 1);
    assert.strictEqual(await page.evaluate(() => window.refreshed), 0);

    // Keeping missing items is an explicit choice, distinct from canceling all work.
    await install(page);
    await selectFile(page);
    await page.click('#jkAtualizacaoArquivoManter');
    const report = page.locator('#jkAtualizacaoArquivoRelatorio');
    await report.waitFor({ state: 'visible' });
    let requests = await page.evaluate(() => window.requests);
    assert.strictEqual(requests.length, 2);
    assert.strictEqual(requests[0].fields.previsualizar, '1');
    assert.strictEqual(requests[0].fields.sincronizar_arquivo, '1');
    assert.strictEqual(requests[1].fields.confirmar_exclusoes, '0');
    assert.strictEqual(requests[1].fields.confirmar_inclusoes, '1');
    assert.strictEqual(requests[1].fields.token_confirmacao, 'synthetic-state-token');
    assert((await report.textContent()).includes('Ausente-075'));
    await closeReport(page);

    // Explicit removal preserves the preview token and opens a complete, safe report.
    await install(page);
    await selectFile(page);
    await page.click('#jkAtualizacaoArquivoExcluir');
    await report.waitFor({ state: 'visible' });
    requests = await page.evaluate(() => window.requests);
    assert.strictEqual(requests[1].fields.confirmar_exclusoes, '1');
    const reportText = await report.textContent();
    for (const text of ['Ausente-075', '003', 'Quantidade', 'Valor unidade', 'Valor total', injection]) {
      assert(reportText.includes(text), 'relatório deve conter: ' + text);
    }
    assert.strictEqual(await report.locator('img').count(), 0);
    assert.strictEqual(await page.evaluate(() => window.injected), false);
    assert(await page.evaluate(() => [...document.querySelectorAll('#jkAtualizacaoArquivoRelatorio, #jkAtualizacaoArquivoRelatorio *')]
      .some(node => node.scrollHeight > node.clientHeight && /auto|scroll/.test(getComputedStyle(node).overflowY))),
    'relatório longo deve ter rolagem');
    if (process.env.JK_REPORT_SCREENSHOT) await page.screenshot({ path: process.env.JK_REPORT_SCREENSHOT });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await report.evaluate(node => node.getBoundingClientRect().right <= window.innerWidth &&
      node.getBoundingClientRect().left >= 0 && node.scrollWidth <= node.clientWidth),
    'relatório deve caber na tela estreita sem rolagem horizontal');
    if (process.env.JK_REPORT_MOBILE_SCREENSHOT) await page.screenshot({ path: process.env.JK_REPORT_MOBILE_SCREENSHOT });
    await closeReport(page);

    // An HTTP failure, or an unsuccessful body, never becomes a success report.
    for (const options of [{ saveError: true }, { unsuccessful: true }]) {
      await install(page, { ...options, noPending: true });
      await selectFile(page);
      await finish(page);
      assert.strictEqual(await page.locator('#jkAtualizacaoArquivoRelatorio').count(), 0);
      assert.strictEqual(await page.evaluate(() => window.refreshed), 0);
      assert(await page.evaluate(() => window.statuses.some(row => row.error)));
    }

    // A failed refresh does not hide what was already saved.
    await install(page, { refreshError: true, noPending: true });
    await selectFile(page);
    await report.waitFor({ state: 'visible' });
    await page.waitForFunction(() => window.refreshed === 1);
    assert.strictEqual(await page.evaluate(() => window.requests.length), 2);
    assert((await report.textContent()).includes('Quantidade'));
    assert((await page.locator('#jkAtualizacaoArquivoAviso').textContent()).includes('foi salva'));
    await closeReport(page);

    // Changing scope while the question is open invalidates the pending write.
    await install(page);
    await selectFile(page);
    await confirmation.waitFor({ state: 'visible' });
    await page.evaluate(() => { window.contextValid = false; });
    await page.click('#jkAtualizacaoArquivoExcluir');
    await finish(page);
    assert.strictEqual(await page.evaluate(() => window.requests.length), 1);

    // Existing additive UI keeps its semantics and shares the report.
    await install(page, { additive: true });
    await selectFile(page);
    await page.click('#jkAtualizacaoArquivoConfirmar');
    await report.waitFor({ state: 'visible' });
    assert.strictEqual(await page.evaluate(() => window.requests[0].fields.sincronizar_arquivo), '0');
    await closeReport(page);

    // Reentry cannot create a second pending upload while confirmation is active.
    await install(page);
    await selectFile(page);
    await confirmation.waitFor({ state: 'visible' });
    await page.evaluate(() => window.JKAtualizacaoArquivo.atualizar(window.config));
    assert.strictEqual(await page.locator('#jkAtualizacaoArquivoConfirmacao').count(), 1);
    assert.strictEqual(await page.evaluate(() => window.requests.length), 1);
    await page.keyboard.press('Escape');
    await finish(page);
    assert.strictEqual(await page.evaluate(() => window.requests.length), 1);
    assert.deepStrictEqual(errors, []);
    await explicitFileTotalsSurviveEditor(browser);
    console.log('medias file reconciliation browser: OK');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
