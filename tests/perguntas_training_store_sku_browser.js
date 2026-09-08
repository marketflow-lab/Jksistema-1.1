'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const staticRoot = path.join(root, 'static');
const html = fs.readFileSync(path.join(staticRoot, 'perguntas_pos_venda.html'), 'utf8');
const lojas = [
  { store_id: 'store-alpha', nome: 'Loja Alpha', mercadolivre_conectado: true },
  { store_id: 'store-beta', nome: 'Loja Beta', mercadolivre_conectado: true },
];
const trainingByStore = {
  'store-alpha': {
    orientacoes_perguntas: 'Responder de forma cordial e objetiva.',
    contexto_loja: 'Postagem em até um dia útil.',
    compatibilidade_autopecas: 'Confirmar modelo, motor e ano.',
    proibicoes: 'Não inventar código OEM.',
    notas_sku: { '001': 'Aplicação confirmada somente no modelo Alpha.' },
    exemplos: { perguntas_anuncio: [] },
    profile_scope: 'store',
  },
  'store-beta': {
    orientacoes_perguntas: 'Usar a saudação da Loja Beta.',
    contexto_loja: '',
    compatibilidade_autopecas: '',
    proibicoes: '',
    notas_sku: {},
    exemplos: { perguntas_anuncio: [] },
    profile_scope: 'store',
  },
};
const productsByStore = {
  'store-alpha': [
    { sku: '001', nome: 'Produto Alpha 1', marca: 'Marca A' },
    { sku: '002', nome: 'Produto Alpha 2', marca: 'Marca A' },
    { sku: '003', nome: 'Produto Alpha 3', marca: 'Marca B' },
  ],
  'store-beta': [{ sku: '001', nome: 'Produto exclusivo Beta', marca: 'Marca B' }],
};

let revision = 1;
let failCatalog = false;
let conflictOnSave = false;
let conflictModelOnSave = false;
let delayedAlpha = null;
let failDetails = false;
let delayedDetails = null;
let conflictCharacteristicOnSave = false;
let generation = 'gen-1';
let failSavedDetails = false;
function skuDetails(storeId, sku) {
  const overrides = trainingByStore[storeId]?.caracteristicas_sku?.[sku] || {};
  const product = (productsByStore[storeId] || []).find(item => item.sku === sku);
  const base = { marca: product?.marca || '', tensao: storeId === 'store-alpha' ? '12 V' : '24 V' };
  return {
    sku,
    canonical_document: { sku, title: product?.nome, marca: base.marca, tensao: base.tensao, description: `Cadastro completo ${storeId}/${sku}` },
    evidence: [{ fact: `Evidência exclusiva ${storeId}/${sku}` }],
    guidance: { notas: trainingByStore[storeId]?.notas_sku?.[sku] || '' },
    source_body: `# ${product?.nome}\n\nCadastro completo ${storeId}/${sku}\nDescrição preservada\n<script>window.skuInjected = true</script>`,
    characteristics: Object.entries(base).map(([key, value]) => ({ key, label: key === 'marca' ? 'Marca' : 'Tensão', value: overrides[key] ?? value, source: 'Obsidian', original_value: value, edited: key in overrides })),
    documents: [{ title: `Evidências de ${sku}`, relative_path: `lojas/${storeId}/evidencias/${sku}.md`, body: `Evidência exclusiva ${storeId}/${sku}\nCompatibilidade confirmada para o produto ${sku}.\n<script>window.skuInjected = true</script>` }],
    revision: `${storeId}-${revision}`,
  };
}
function snapshot(storeId, sku = '') {
  const content = trainingByStore[storeId] || {};
  return { success: true, store_id: storeId, ...content, caracteristicas_sku: content.caracteristicas_sku || {}, ...(sku ? { sku_details: skuDetails(storeId, sku) } : {}), context_generation_id: generation, editorial: { revision: `${storeId}-${revision}`, general: { status: content.generalStatus || 'published', hash: `general-${revision}`, source_body: content.orientacoes_perguntas }, skus: Object.fromEntries(Object.keys(content.notas_sku || {}).map(sku => [sku, { status: 'draft', hash: `sku-${revision}`, requires_catalog_sync: content.pendingCatalogSku === sku }])) } };
}
function json(route, payload, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

function contentType(filePath) {
  if (filePath.endsWith('.css')) return 'text/css; charset=utf-8';
  if (filePath.endsWith('.js')) return 'application/javascript; charset=utf-8';
  return 'application/octet-stream';
}

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.JK_TEST_BROWSER_PATH ? { executablePath: process.env.JK_TEST_BROWSER_PATH } : {}) });
  const requests = [];
  const pageErrors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.setDefaultTimeout(12000);
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.addInitScript(() => {
      localStorage.setItem('permissions', JSON.stringify({ perguntas_pos_venda: true }));
      localStorage.setItem('token', 'test-token');
    });
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      requests.push({ method: request.method(), pathname: url.pathname, search: url.search });

      if (url.pathname === '/perguntas_pos_venda.html') {
        await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html });
        return;
      }
      if (url.pathname === '/auth.js') {
        await route.fulfill({
          status: 200,
          contentType: 'application/javascript; charset=utf-8',
          body: 'window.verificarSessao = () => true; window.obterAuthHeaders = () => ({ Authorization: "Bearer test-token" });',
        });
        return;
      }
      if (url.pathname === '/ia-sidebar.js') {
        await route.fulfill({ status: 200, contentType: 'application/javascript; charset=utf-8', body: '' });
        return;
      }
      if (url.pathname.startsWith('/perguntas_pos_venda/')) {
        const relative = url.pathname.replace(/^\/+/, '');
        const target = path.resolve(staticRoot, relative);
        assert(target.startsWith(staticRoot + path.sep), 'asset deve permanecer dentro de static');
        await route.fulfill({ status: 200, contentType: contentType(target), body: fs.readFileSync(target) });
        return;
      }
      if (url.pathname === '/api/mercadolivre/perguntas/lojas') {
        await json(route, { lojas });
        return;
      }
      if (url.pathname === '/api/mercadolivre/ia-treinamento/skus') {
        const storeId = url.searchParams.get('store_id');
        await json(route, failCatalog ? { detail: 'Catálogo indisponível' } : { success: true, store_id: storeId, produtos: productsByStore[storeId] || [] }, failCatalog ? 503 : 200);
        return;
      }
      if (url.pathname === '/api/mercadolivre/ia-treinamento' && request.method() === 'GET') {
        const storeId = url.searchParams.get('store_id');
        const sku = url.searchParams.get('sku') || '';
        if (sku && failDetails) { await json(route, { detail: 'Detalhes do Obsidian indisponíveis' }, 503); return; }
        const result = snapshot(storeId, sku);
        if (sku && delayedDetails?.storeId === storeId && delayedDetails?.sku === sku) {
          const delayed = delayedDetails;
          delayedDetails = null;
          delayed.requested();
          await delayed.wait;
        }
        if (storeId === 'store-alpha' && delayedAlpha) { const wait = delayedAlpha; delayedAlpha = null; await wait; }
        await json(route, result);
        return;
      }
      if (url.pathname === '/api/mercadolivre/ia-treinamento' && request.method() === 'POST') {
        const payload = request.postDataJSON();
        assert(['store-alpha', 'store-beta'].includes(payload.store_id));
        assert(['general', 'sku'].includes(payload.edit_target));
        assert(payload.expected_revision, 'gravação exige versão conhecida');
        if (conflictOnSave) {
          conflictOnSave = false;
          trainingByStore[payload.store_id].orientacoes_perguntas = 'Nova edição concorrente no Obsidian';
          revision++;
        }
        if (conflictModelOnSave) {
          conflictModelOnSave = false;
          trainingByStore[payload.store_id].exemplos.perguntas_anuncio[0].resposta = 'Alterado no Obsidian';
          revision++;
        }
        if (conflictCharacteristicOnSave) {
          conflictCharacteristicOnSave = false;
          trainingByStore[payload.store_id].caracteristicas_sku[payload.sku].tensao = 'Tensão externa durante salvamento';
          revision++;
        }
        if (payload.expected_revision !== `${payload.store_id}-${revision}`) {
          await json(route, { detail: { code: 'editorial_revision_conflict', message: 'O Obsidian mudou.' } }, 409);
          return;
        }
        const data = trainingByStore[payload.store_id];
        if (payload.edit_target === 'sku') {
          assert(!('orientacoes' in payload), 'gravar SKU não pode enviar orientações gerais');
          data.notas_sku[payload.sku] = payload.notas_sku;
          if (payload.caracteristicas_sku) {
            assert.strictEqual(typeof payload.caracteristicas_sku, 'object');
            assert(Object.values(payload.caracteristicas_sku).every(value => typeof value === 'string'));
            data.caracteristicas_sku ||= {};
            data.caracteristicas_sku[payload.sku] = { ...payload.caracteristicas_sku };
          }
          assert(payload.exemplos.every(item => item.sku === payload.sku));
          data.exemplos.perguntas_anuncio = [
            ...data.exemplos.perguntas_anuncio.filter(item => item.sku !== payload.sku), ...payload.exemplos
          ];
        } else {
          assert.strictEqual(payload.sku, '', 'gravar geral não pode gravar SKU aberto');
          assert(payload.exemplos.every(item => !item.sku));
          const retained = data.exemplos.perguntas_anuncio.filter(item => item.sku);
          Object.assign(data, { orientacoes_perguntas: payload.orientacoes, contexto_loja: payload.contexto_loja, compatibilidade_autopecas: payload.compatibilidade_autopecas, proibicoes: payload.proibicoes, exemplos: { perguntas_anuncio: [...payload.exemplos, ...retained] }, generalStatus: 'draft' });
        }
        revision++;
        const saved = { ...snapshot(payload.store_id, payload.edit_target === 'sku' ? payload.sku : ''), storage: 'obsidian_context_hub_draft', requires_review: true };
        if (failSavedDetails && payload.edit_target === 'sku') {
          failSavedDetails = false;
          delete saved.sku_details;
          saved.sku_details_error = 'A ficha não pôde ser relida após salvar.';
        }
        await json(route, saved);
        return;
      }
      if (url.pathname === '/api/mercadolivre/perguntas/automacao/status') {
        await json(route, { lojas: [], worker_iniciado: false, executando: false });
        return;
      }
      if (url.pathname.startsWith('/api/mercadolivre/perguntas')) {
        await json(route, { questions: [], total: 0, status_resumo: {} });
        return;
      }
      await json(route, { success: true, lojas: [], pendentes: [] });
    });

    await page.goto('http://jk.local/perguntas_pos_venda.html', { waitUntil: 'load' });
    await page.getByRole('tab', { name: 'Treinar IA' }).click();
    assert.strictEqual(await page.locator('#ai-training-scope').inputValue(), '', 'Todas as contas não deve escolher a primeira loja');
    assert.match(await page.locator('#ai-training-sku-list').innerText(), /Selecione uma loja/);
    await page.locator('#lojas-grid .store-card[data-loja="Loja Alpha"]').click();
    await page.locator('#ai-training-sku-count').filter({ hasText: '3 SKU(s)' }).waitFor();

    assert.strictEqual(await page.locator('#ai-training-scope').inputValue(), 'store-alpha');
    assert.strictEqual(await page.locator('#ai-training-scope option').count(), 3, 'somente escolha obrigatória e lojas exatas');
    assert.match(await page.locator('#ai-training-general-summary').innerText(), /Responder de forma cordial e objetiva/);
    assert.strictEqual(await page.locator('#ai-training-sku-list .training-sku-card').count(), 3, 'todos os SKUs da loja devem aparecer');

    await page.locator('[data-training-sku="002"]').click();
    await page.getByRole('dialog').waitFor({ state: 'visible' });
    assert.match(await page.getByRole('dialog').innerText(), /Produto Alpha 2/);
    assert.match(await page.getByRole('dialog').innerText(), /ainda não possui orientação específica/);

    await page.getByRole('button', { name: 'Adicionar orientação', exact: true }).last().click();
    await page.locator('#ai-training-notas-sku').fill('Orientação nova e isolada do SKU 002.');
    await page.getByRole('button', { name: 'Salvar orientação do SKU' }).click();
    await page.locator('#ai-training-status').filter({ hasText: 'salvo no Obsidian' }).waitFor();
    assert.match(await page.getByRole('dialog').innerText(), /Orientação nova e isolada do SKU 002/);
    if (process.env.JK_CAPTURE_TEST_SCREENSHOT === '1') {
      const outputDir = path.join(root, 'test-results', 'perguntas-training-store-sku');
      fs.mkdirSync(outputDir, { recursive: true });
      await page.screenshot({ path: path.join(outputDir, 'sku-guidance-popover.png'), fullPage: true });
    }

    await page.getByRole('button', { name: 'Fechar orientações do SKU' }).click();

    // An SKU without guidance still exposes its complete Obsidian content safely.
    await page.locator('[data-training-sku="003"]').click();
    const details = page.locator('#ai-training-sku-details');
    await details.filter({ hasText: 'Cadastro completo store-alpha/003' }).waitFor();
    assert.match(await page.locator('#ai-training-sku-guidance-view').innerText(), /ainda não possui orientação específica/);
    assert.match(await details.textContent(), /Evidência exclusiva store-alpha\/003/);
    assert.match(await details.textContent(), /<script>window.skuInjected = true<\/script>/);
    assert.strictEqual(await page.evaluate(() => window.skuInjected), undefined);
    if (process.env.JK_CAPTURE_TEST_SCREENSHOT === '1') {
      const outputDir = path.join(root, 'test-results', 'perguntas-training-store-sku');
      fs.mkdirSync(outputDir, { recursive: true });
      await page.getByRole('dialog').screenshot({ path: path.join(outputDir, 'sku-complete-details.png') });
    }
    const tensionValue = () => page.locator('[data-sku-characteristic="tensao"] .training-characteristic-value');
    const tensionInput = () => page.locator('textarea[data-sku-characteristic-input="tensao"]');
    assert.strictEqual(await tensionValue().innerText(), '12 V');
    await tensionValue().click();
    assert.strictEqual(await tensionInput().count(), 0, 'um clique somente seleciona a característica');
    await tensionValue().dblclick();
    await tensionInput().fill('  12 V e 24 V\nConferir aplicação  ');
    await page.locator('#btn-ai-training-cancelar-sku').click();
    assert.strictEqual(await tensionValue().innerText(), '12 V', 'cancelar descarta a alteração de característica');
    assert.strictEqual(trainingByStore['store-alpha'].caracteristicas_sku?.['003'], undefined);
    await tensionValue().dblclick();
    const savedTension = '  12 V e 24 V\nConferir aplicação  ';
    await tensionInput().fill(savedTension);
    await page.locator('#btn-ai-training-salvar-sku').click();
    await page.locator('#ai-training-sku-editor').waitFor({ state: 'hidden' });
    assert.strictEqual(trainingByStore['store-alpha'].caracteristicas_sku['003'].tensao, savedTension);
    assert.strictEqual(trainingByStore['store-alpha'].notas_sku['003'], '', 'editar característica não inventa orientação');
    await page.locator('#btn-ai-training-fechar-sku').click();
    await page.locator('[data-training-sku="003"]').click();
    await page.waitForFunction(expected => document.querySelector('[data-sku-characteristic="tensao"] .training-characteristic-value')?.textContent === expected, savedTension);

    // Concurrent edits preserve typed text and expose both values before retrying.
    await tensionValue().dblclick();
    await tensionInput().fill('Tensão local em conflito');
    trainingByStore['store-alpha'].caracteristicas_sku['003'].tensao = 'Tensão externa no Obsidian';
    revision++;
    await page.evaluate(() => carregarTreinamentoAI(true));
    await page.locator('#ai-training-sku-conflict').filter({ hasText: 'Tensão externa no Obsidian' }).waitFor();
    assert.strictEqual(await tensionInput().inputValue(), 'Tensão local em conflito');
    await page.locator('#ai-training-sku-conflict').getByRole('button', { name: 'Continuar com minha edição após comparar' }).click();
    conflictCharacteristicOnSave = true;
    await page.locator('#btn-ai-training-salvar-sku').click();
    await page.locator('#ai-training-sku-conflict').filter({ hasText: 'Tensão externa durante salvamento' }).waitFor();
    assert.strictEqual(await tensionInput().inputValue(), 'Tensão local em conflito', '409 preserva a característica digitada');
    await page.locator('#ai-training-sku-conflict').getByRole('button', { name: 'Usar versão do Obsidian' }).click();
    assert.strictEqual(await tensionValue().textContent(), 'Tensão externa durante salvamento');

    // A canonical generation change also protects a pending characteristic edit.
    await tensionValue().dblclick();
    await tensionInput().fill('Rascunho durante atualização da base');
    generation = 'gen-2';
    revision++;
    await page.evaluate(() => carregarTreinamentoAI(true));
    await page.locator('#ai-training-sku-conflict').filter({ hasText: 'informações de origem do SKU mudaram' }).waitFor();
    assert.strictEqual(await tensionInput().inputValue(), 'Rascunho durante atualização da base');
    await page.locator('#ai-training-sku-conflict').getByRole('button', { name: 'Usar versão do Obsidian' }).click();

    // A successful write followed by an unavailable details read remains a saved edit.
    await tensionValue().dblclick();
    await tensionInput().fill('Valor confirmado antes da falha de releitura');
    failSavedDetails = true;
    await page.locator('#btn-ai-training-salvar-sku').click();
    await page.locator('#ai-training-sku-editor').waitFor({ state: 'hidden' });
    assert.strictEqual(trainingByStore['store-alpha'].caracteristicas_sku['003'].tensao, 'Valor confirmado antes da falha de releitura');
    await page.locator('#btn-ai-training-retry-details').waitFor({ state: 'visible' });
    await page.locator('#btn-ai-training-retry-details').click();
    await tensionValue().filter({ hasText: 'Valor confirmado antes da falha de releitura' }).waitFor();
    await page.locator('#btn-ai-training-fechar-sku').click();

    // A failed details request is recoverable and cannot masquerade as absent guidance.
    failDetails = true;
    await page.locator('[data-training-sku="003"]').click();
    await page.locator('#btn-ai-training-retry-details').waitFor({ state: 'visible' });
    assert.match(await details.innerText(), /indispon|Falha|carregar/i);
    failDetails = false;
    await page.locator('#btn-ai-training-retry-details').click();
    await details.filter({ hasText: 'Cadastro completo store-alpha/003' }).waitFor();
    await page.locator('#btn-ai-training-fechar-sku').click();

    // Responses from a previous SKU or store must never replace the current details.
    async function delaySkuDetails(storeId, sku) {
      let release;
      let requested;
      const requestSeen = new Promise(resolve => { requested = resolve; });
      const wait = new Promise(resolve => { release = resolve; });
      delayedDetails = { storeId, sku, requested, wait };
      return { release, requestSeen };
    }
    const oldSku = await delaySkuDetails('store-alpha', '003');
    await page.locator('[data-training-sku="003"]').click();
    await oldSku.requestSeen;
    await page.evaluate(() => abrirBalaoSkuTreinamento('001'));
    await details.filter({ hasText: 'Cadastro completo store-alpha/001' }).waitFor();
    const oldSkuResponse = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).searchParams.get('sku') === '003');
    oldSku.release();
    await oldSkuResponse;
    await page.evaluate(() => carregarTreinamentoAI(true));
    assert.match(await details.textContent(), /Cadastro completo store-alpha\/001/);
    assert(!await details.textContent().then(text => text.includes('Cadastro completo store-alpha/003')));
    await page.locator('#btn-ai-training-fechar-sku').click();
    const oldStore = await delaySkuDetails('store-alpha', '003');
    await page.locator('[data-training-sku="003"]').click();
    await oldStore.requestSeen;
    await page.evaluate(() => { aiTrainingScope.value = 'store-beta'; aiTrainingScope.dispatchEvent(new Event('change')); });
    await page.locator('#ai-training-sku-count').filter({ hasText: '1 SKU(s)' }).waitFor();
    await page.locator('[data-training-sku="001"]').click();
    await details.filter({ hasText: 'Cadastro completo store-beta/001' }).waitFor();
    const oldStoreResponse = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).searchParams.get('store_id') === 'store-alpha' && new URL(response.url()).searchParams.get('sku') === '003');
    oldStore.release();
    await oldStoreResponse;
    await page.evaluate(() => carregarTreinamentoAI(true));
    assert.match(await details.textContent(), /Cadastro completo store-beta\/001/);
    assert.strictEqual(await tensionValue().innerText(), '24 V');
    await tensionValue().dblclick();
    await tensionInput().fill('48 V somente Loja Beta');
    await page.locator('#btn-ai-training-salvar-sku').click();
    await page.locator('#ai-training-sku-editor').waitFor({ state: 'hidden' });
    assert.strictEqual(trainingByStore['store-beta'].caracteristicas_sku['001'].tensao, '48 V somente Loja Beta');
    assert.strictEqual(trainingByStore['store-alpha'].caracteristicas_sku['001'], undefined, 'SKU 001 mantém o isolamento entre lojas');
    assert.strictEqual(trainingByStore['store-beta'].caracteristicas_sku['1'], undefined, 'o SKU preserva os zeros iniciais');
    await page.locator('#btn-ai-training-fechar-sku').click();
    await page.locator('#ai-training-scope').selectOption('store-alpha');
    await page.locator('#ai-training-sku-count').filter({ hasText: '3 SKU(s)' }).waitFor();
    await page.locator('[data-training-sku="002"]').click();
    await details.filter({ hasText: 'Cadastro completo store-alpha/002' }).waitFor();
    await page.locator('#btn-ai-training-fechar-sku').click();

    // Models target their exact note, preserve other SKU drafts, and retain leading zeroes.
    await page.evaluate(async () => {
      aiTrainingExemploEscopo.value = 'sku';
      aiTrainingExemploPergunta.value = 'Tensão do produto 002?';
      aiTrainingExemploResposta.value = '12 V';
      await adicionarExemploTreinamento();
    });
    assert.strictEqual(trainingByStore['store-alpha'].exemplos.perguntas_anuncio[0].sku, '002');
    assert.strictEqual(trainingByStore['store-alpha'].notas_sku['002'], 'Orientação nova e isolada do SKU 002.');
    conflictModelOnSave = true;
    await page.evaluate(async () => {
      aiTrainingExemploPergunta.value = 'Modelo em conflito';
      aiTrainingExemploResposta.value = 'Rascunho preservado';
      await adicionarExemploTreinamento();
    });
    await page.waitForFunction(() => sessaoTreinamento().snapshot.exemplos.perguntas_anuncio[0].resposta === 'Alterado no Obsidian');
    assert.strictEqual(trainingByStore['store-alpha'].exemplos.perguntas_anuncio.length, 1);
    assert.strictEqual(trainingByStore['store-alpha'].orientacoes_perguntas, 'Responder de forma cordial e objetiva.');
    assert(await page.evaluate(() => sessaoTreinamento().drafts['sku:002'].value.exemplos.some(item => item.resposta === 'Rascunho preservado')));
    await page.evaluate(() => abrirBalaoSkuTreinamento('002'));
    await page.locator('#ai-training-sku-conflict').getByRole('button', { name: 'Usar versão do Obsidian' }).click();
    await page.locator('#btn-ai-training-fechar-sku').click();
    await page.evaluate(async () => {
      aiTrainingExemploEscopo.value = 'geral';
      aiTrainingExemploPergunta.value = 'Horário de atendimento?';
      aiTrainingExemploResposta.value = 'Dias úteis';
      await adicionarExemploTreinamento();
    });
    assert.strictEqual(trainingByStore['store-alpha'].exemplos.perguntas_anuncio.length, 2);
    await page.evaluate(() => abrirBalaoSkuTreinamento('001'));
    await page.locator('#btn-ai-training-fechar-sku').click();
    const removed = page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes('/ia-treinamento'));
    await page.locator('.training-example-item').filter({ hasText: 'Tensão do produto 002?' }).getByRole('button', { name: 'Remover' }).click();
    await removed;
    await page.waitForFunction(() => !sessaoTreinamento().saving);
    assert.strictEqual(trainingByStore['store-alpha'].exemplos.perguntas_anuncio.length, 1);
    assert.strictEqual(trainingByStore['store-alpha'].exemplos.perguntas_anuncio[0].sku, '');
    assert.strictEqual(trainingByStore['store-alpha'].notas_sku['002'], 'Orientação nova e isolada do SKU 002.');

    await page.locator('#ai-training-scope').selectOption('store-beta');
    await page.locator('#ai-training-sku-count').filter({ hasText: '1 SKU(s)' }).waitFor();
    assert.match(await page.locator('#ai-training-general-summary').innerText(), /saudação da Loja Beta/);
    assert.match(await page.locator('#ai-training-sku-list').innerText(), /Produto exclusivo Beta/);
    assert(await page.locator('#lojas-grid .store-card[data-loja="Loja Beta"]').evaluate(el => el.classList.contains('active')));
    assert(!await page.locator('#ai-training-sku-list').innerText().then(text => text.includes('Produto Alpha')));

    // Same SKU in two stores never inherits the other store's guidance.
    await page.locator('[data-training-sku="001"]').click();
    assert.match(await page.locator('#ai-training-sku-guidance-view').innerText(), /ainda não possui/);
    await page.getByRole('button', { name: 'Fechar orientações do SKU' }).click();

    // A real polling cycle reflects external text verbatim, including whitespace and markup.
    const external = '  Texto do Obsidian\n\n- lista com espaços  \n<script>window.injected = true</script>  ';
    trainingByStore['store-beta'].orientacoes_perguntas = external;
    revision++;
    await page.waitForFunction(expected => document.querySelector('#ai-training-orientacoes').value === expected, external);
    assert.strictEqual(await page.locator('#ai-training-general-summary p').first().textContent(), external);
    assert.strictEqual(await page.evaluate(() => window.injected), undefined);
    assert.strictEqual(await page.locator('#ai-training-general-source pre').textContent(), external);

    // Dirty forms survive background synchronization and account changes.
    await page.getByRole('button', { name: 'Editar orientações', exact: true }).click();
    const localEdit = '  Minha edição local\ncom quebra e espaços  ';
    await page.locator('#ai-training-orientacoes').fill(localEdit);
    trainingByStore['store-beta'].orientacoes_perguntas = 'Alteração externa depois de começar a editar';
    revision++;
    await page.locator('#ai-training-general-conflict').waitFor({ state: 'visible' });
    assert.strictEqual(await page.locator('#ai-training-orientacoes').inputValue(), localEdit);
    if (process.env.JK_CAPTURE_TEST_SCREENSHOT === '1') {
      const outputDir = path.join(root, 'test-results', 'perguntas-training-store-sku');
      fs.mkdirSync(outputDir, { recursive: true });
      await page.screenshot({ path: path.join(outputDir, 'obsidian-conflict-preserved.png'), fullPage: true });
    }
    assert.match(await page.locator('#ai-training-general-conflict').innerText(), /Alteração externa depois/);
    await page.locator('#lojas-grid .store-card[data-loja="Loja Alpha"]').click();
    await page.waitForFunction(() => document.querySelector('#ai-training-orientacoes').value.includes('cordial'));
    await page.locator('#ai-training-scope').selectOption('store-beta');
    await page.waitForFunction(expected => document.querySelector('#ai-training-orientacoes').value === expected, localEdit);
    await page.locator('#ai-training-general-conflict').getByRole('button', { name: 'Continuar com minha edição após comparar' }).click();
    await page.getByRole('button', { name: 'Salvar orientações gerais' }).click();
    await page.waitForFunction(expected => document.querySelector('#ai-training-general-summary p')?.textContent === expected, localEdit);
    assert.strictEqual(trainingByStore['store-beta'].orientacoes_perguntas, localEdit);

    // Saving an unrelated general field must preserve every existing model verbatim.
    const manyModels = Array.from({ length: 61 }, (_, index) => ({ pergunta: `  Pergunta ${index}  `, resposta: ` Resposta ${index}\n `, sku: '', observacao: '  Nota  ', custom_metadata: { retain: index } }));
    trainingByStore['store-beta'].exemplos = { perguntas_anuncio: manyModels };
    revision++;
    await page.evaluate(() => carregarTreinamentoAI(true));
    await page.waitForFunction(() => document.querySelectorAll('.training-example-item').length === 61);
    await page.getByRole('button', { name: 'Editar orientações', exact: true }).click();
    await page.locator('#ai-training-contexto-loja').fill('Política revisada');
    await page.getByRole('button', { name: 'Salvar orientações gerais' }).click();
    await page.locator('#ai-training-general-editor').waitFor({ state: 'hidden' });
    assert.deepStrictEqual(trainingByStore['store-beta'].exemplos.perguntas_anuncio, manyModels);

    // Server-side 409 between read and save retains local text and offers the latest version.
    await page.getByRole('button', { name: 'Editar orientações', exact: true }).click();
    await page.locator('#ai-training-orientacoes').fill('Edição durante corrida');
    conflictOnSave = true;
    await page.getByRole('button', { name: 'Salvar orientações gerais' }).click();
    await page.locator('#ai-training-general-conflict').filter({ hasText: 'Nova edição concorrente' }).waitFor();
    assert.strictEqual(await page.locator('#ai-training-orientacoes').inputValue(), 'Edição durante corrida');
    await page.locator('#ai-training-general-conflict').getByRole('button', { name: 'Usar versão do Obsidian' }).click();
    assert.strictEqual(await page.locator('#ai-training-orientacoes').inputValue(), 'Nova edição concorrente no Obsidian');
    await page.locator('#btn-ai-training-cancelar-gerais').click();

    // SKU edits also survive conflict and account switching.
    await page.locator('[data-training-sku="001"]').click();
    await page.locator('#btn-ai-training-editar-sku').click();
    await page.locator('#ai-training-notas-sku').fill('Rascunho SKU Beta');
    trainingByStore['store-beta'].notas_sku['001'] = 'SKU Beta editado no Obsidian';
    revision++;
    await page.locator('#ai-training-sku-conflict').waitFor({ state: 'visible' });
    assert.strictEqual(await page.locator('#ai-training-notas-sku').inputValue(), 'Rascunho SKU Beta');
    await page.locator('#btn-ai-training-fechar-sku').click();
    await page.locator('#lojas-grid .store-card[data-loja="Loja Alpha"]').click();
    await page.locator('#ai-training-sku-count').filter({ hasText: '3 SKU(s)' }).waitFor();
    await page.locator('#ai-training-scope').selectOption('store-beta');
    await page.locator('#ai-training-sku-count').filter({ hasText: '1 SKU(s)' }).waitFor();
    await page.locator('[data-training-sku="001"]').click();
    assert.strictEqual(await page.locator('#ai-training-notas-sku').inputValue(), 'Rascunho SKU Beta');
    await page.locator('#ai-training-sku-conflict').getByRole('button', { name: 'Usar versão do Obsidian' }).click();
    await page.locator('#btn-ai-training-fechar-sku').click();

    // Deletion removes stale content, and API failures are distinct from an empty catalog.
    trainingByStore['store-beta'].orientacoes_perguntas = '';
    trainingByStore['store-beta'].contexto_loja = '';
    trainingByStore['store-beta'].exemplos = { perguntas_anuncio: [] };
    trainingByStore['store-beta'].generalStatus = 'deleted';
    trainingByStore['store-beta'].notas_sku = {};
    revision++;
    await page.locator('#ai-training-status').filter({ hasText: 'Arquivo excluído' }).waitFor();
    assert.match(await page.locator('#ai-training-general-summary').innerText(), /ainda não possui/);
    failCatalog = true;
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.locator('#ai-training-sku-count').filter({ hasText: 'Falha ao carregar' }).waitFor();
    await page.evaluate(() => carregarTreinamentoAI(true));
    assert.strictEqual(await page.locator('#ai-training-sku-count').innerText(), 'Falha ao carregar');
    failCatalog = false;

    // Late response from an old selection cannot flash another store's content.
    let releaseAlpha;
    delayedAlpha = new Promise(resolve => { releaseAlpha = resolve; });
    await page.locator('#lojas-grid .store-card[data-loja="Loja Alpha"]').click();
    await page.locator('#ai-training-scope').selectOption('store-beta');
    await page.locator('#ai-training-status').filter({ hasText: 'Arquivo excluído' }).waitFor();
    releaseAlpha();
    await page.evaluate(() => carregarTreinamentoAI(true));
    assert.strictEqual(await page.locator('#ai-training-scope').inputValue(), 'store-beta');
    assert(!await page.locator('#ai-training-general-summary').innerText().then(text => text.includes('cordial')));

    // Hidden tabs stop polling. Returning to training refreshes the current note.
    await page.getByRole('tab', { name: 'Perguntas', exact: true }).click();
    const beforeHidden = requests.filter(item => item.method === 'GET' && item.pathname === '/api/mercadolivre/ia-treinamento').length;
    await page.waitForTimeout(5200);
    assert.strictEqual(requests.filter(item => item.method === 'GET' && item.pathname === '/api/mercadolivre/ia-treinamento').length, beforeHidden);
    trainingByStore['store-beta'].orientacoes_perguntas = 'Recuperado após reabrir aba';
    trainingByStore['store-beta'].generalStatus = 'published';
    revision++;
    await page.getByRole('tab', { name: 'Treinar IA' }).click();
    await page.waitForFunction(() => document.querySelector('#ai-training-orientacoes').value === 'Recuperado após reabrir aba');

    for (const [status, label] of [['validated', 'Pendente de publicação'], ['rejected', 'Revisão reprovada'], ['future-status', 'Estado de publicação indisponível']]) {
      trainingByStore['store-beta'].generalStatus = status;
      revision++;
      await page.evaluate(() => carregarTreinamentoAI(true));
      await page.locator('#ai-training-status').filter({ hasText: label }).waitFor();
      assert.strictEqual(await page.locator('#ai-training-general-sync').innerText(), label);
    }

    // An individual SKU awaiting the canonical catalog must not appear ready for publication.
    trainingByStore['store-beta'].generalStatus = 'published';
    trainingByStore['store-beta'].notas_sku = { '001': 'Orientação preservada aguardando o cadastro' };
    trainingByStore['store-beta'].pendingCatalogSku = '001';
    revision++;
    await page.evaluate(() => carregarTreinamentoAI(true));
    await page.locator('#ai-training-status').filter({ hasText: 'Pendente de sincronização do cadastro' }).waitFor();
    assert.match(await page.locator('[data-training-sku="001"]').innerText(), /Pendente de sincronização do cadastro/);
    assert.strictEqual(await page.locator('#ai-training-general-sync').innerText(), 'Sincronizado');
    await page.locator('[data-training-sku="001"]').click();
    assert.match(await page.locator('#ai-training-sku-sync').innerText(), /A orientação está salva no Obsidian. Sincronize a base de conhecimento desta loja antes de publicar/);
    assert.match(await page.locator('#ai-training-sku-guidance-view').innerText(), /Orientação preservada aguardando o cadastro/);
    await page.locator('#btn-ai-training-fechar-sku').click();

    const trainingGets = requests.filter(item => item.method === 'GET' && item.pathname === '/api/mercadolivre/ia-treinamento');
    assert(trainingGets.every(item => /store_id=store-(alpha|beta)/.test(item.search)), 'treinamento nunca pode consultar escopo global');
    assert.deepStrictEqual(pageErrors, [], `erros no navegador: ${pageErrors.join(' | ')}`);
    console.log('Treinar IA por loja no navegador: OK');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
