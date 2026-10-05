'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const staticRoot = path.resolve(__dirname, '..', 'static');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 940 } });
  const errors = [];
  const mutations = [];
  let records = [];
  let holdRead = false;
  let holdSave = false;
  let failSave = false;
  let failRead = false;
  let loadsLojas = 0;
  let readReceived;
  let saveReceived;
  let releaseRead;
  let releaseSave;
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.addInitScript(() => localStorage.setItem('user_data', JSON.stringify({ client_id: 'cliente-a' })));
    await page.route('**/*', async route => {
      const req = route.request();
      const url = new URL(req.url());
      const json = (payload, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
      if (url.pathname === '/cadastro.html' || url.pathname.startsWith('/cadastro/')) {
        const file = path.resolve(staticRoot, url.pathname.slice(1));
        assert(file.startsWith(staticRoot + path.sep));
        const contentType = file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'application/javascript' : 'text/html';
        return route.fulfill({ status: 200, contentType, body: fs.readFileSync(file) });
      }
      if (url.pathname === '/auth.js') return route.fulfill({ contentType: 'application/javascript', body: "window.obterAuthHeaders=extra=>({...extra,Authorization:'Bearer test'});" });
      if (url.pathname === '/ncm-sync.js') return route.fulfill({ contentType: 'application/javascript', body: 'window.NCM_SYNC={init(){},isRunning(){return false;}};' });
      if (['/table_columns.js', '/ia-sidebar.js'].includes(url.pathname)) return route.fulfill({ contentType: 'application/javascript', body: '' });
      if (url.pathname === '/api/lojas') {
        loadsLojas += 1;
        return json([{ store_id: 'store-a', nome: 'Loja A' }, { store_id: 'store-b', nome: 'Loja B' }]);
      }
      if (url.pathname === '/api/cadastro/fornecedores') return json([]);
      if (/\/importador-loja$/.test(url.pathname)) return json({});
      if (url.pathname === '/api/cadastro/lojas/produtos') return json({ produtos: [], lojas: [], total: 0, partial: false });
      if (/\/produtos$/.test(url.pathname)) return json([]);
      if (url.pathname.startsWith('/api/cadastro/nacionalizadores')) {
        if (req.method() === 'GET') {
          const snapshot = structuredClone(records);
          if (holdRead) {
            holdRead = false;
            await new Promise(resolve => { releaseRead = resolve; readReceived(); });
          }
          if (failRead) { failRead = false; return json({ detail: 'Falha de leitura simulada.' }, 500); }
          return json(snapshot);
        }
        mutations.push({ method: req.method(), pathname: url.pathname, payload: req.postDataJSON() });
        if (holdSave) {
          holdSave = false;
          await new Promise(resolve => { releaseSave = resolve; saveReceived(); });
        }
        if (failSave) { failSave = false; return json({ detail: 'Falha de gravação simulada.' }, 500); }
        const id = req.method() === 'POST' ? `registro-${records.length + 1}` : url.pathname.split('/').pop();
        const record = { id, ...req.postDataJSON() };
        records = records.filter(item => item.id !== id).concat(record);
        return json({ success: true, nacionalizador: record });
      }
      return json({ detail: 'Rota não simulada.' }, 404);
    });
    await page.goto('http://cadastro.test/cadastro.html?store_id=store-a');
    await page.waitForFunction(() => window.JKCadastro?.__mainInitialized && window.JKCadastro.runtime.state.storeIdSelecionado === 'store-a');
    await page.click('#tabNacionalizadores');
    await page.waitForFunction(() => !document.querySelector('#btnAtualizarNacionalizadores').disabled);
    assert.strictEqual(await page.getAttribute('#tabNacionalizadores', 'aria-selected'), 'true');
    assert(await page.locator('#painelImportadorLoja').isHidden());
    await page.fill('#nacionalizadorNomeEmpresa', 'Empresa Exemplo Ltda.');
    await page.fill('#nacionalizadorCnpj', '00.000.000/0001-00');
    await page.fill('#nacionalizadorNomeContato', 'Contato de Exemplo');
    await page.fill('#nacionalizadorTelefone', '+55 00 00000 0000');
    await page.fill('#nacionalizadorEnderecoEmpresa', 'Endereço de exemplo, 123, Brasil');
    assert.strictEqual(await page.evaluate(() => window.jkCadastroPodeReceberSync()), false, 'edição deve adiar recargas de sync');
    await page.click('#btnSalvarNacionalizador');
    await page.waitForFunction(() => document.querySelector('#nacionalizadorStatus').textContent.includes('sucesso'));
    assert.strictEqual(mutations[0].method, 'POST');
    assert.deepStrictEqual(Object.keys(mutations[0].payload).sort(), ['cnpj', 'endereco_empresa', 'nome_contato', 'nome_empresa', 'telefone']);
    assert.strictEqual(await page.inputValue('#nacionalizadorNomeEmpresa'), '');
    assert((await page.locator('#listaNacionalizadores').innerText()).includes('Empresa Exemplo Ltda.'));
    assert.strictEqual(await page.evaluate(() => window.jkCadastroPodeReceberSync()), true);

    await page.locator('#listaNacionalizadores button').click();
    await page.fill('#nacionalizadorNomeEmpresa', 'Empresa Atualizada');
    failSave = true;
    await page.click('#btnSalvarNacionalizador');
    await page.waitForFunction(() => document.querySelector('#nacionalizadorStatus').textContent.includes('Falha de gravação'));
    assert.strictEqual(await page.inputValue('#nacionalizadorNomeEmpresa'), 'Empresa Atualizada', 'falha deve preservar edição');
    holdSave = true;
    const editSaveStarted = new Promise(resolve => { saveReceived = resolve; });
    await page.click('#btnSalvarNacionalizador');
    await editSaveStarted;
    const loadsAntesSync = loadsLojas;
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('jk:machine-sync-updated', { detail: { received_scopes: ['cadastro'] } })));
    assert.strictEqual(loadsLojas, loadsAntesSync, 'sync deve aguardar a gravação');
    releaseSave();
    await page.waitForFunction(() => document.querySelector('#nacionalizadorStatus').textContent.includes('sucesso'));
    await page.waitForFunction(() => !window.JKCadastro.runtime.state.carregandoProdutos);
    for (let i = 0; i < 40 && loadsLojas === loadsAntesSync; i += 1) await page.waitForTimeout(25);
    assert(loadsLojas > loadsAntesSync, 'sync pendente deve retomar após salvar sem novo foco');
    assert.strictEqual(mutations[2].method, 'PUT');
    assert.strictEqual(mutations[2].pathname, '/api/cadastro/nacionalizadores/registro-1');
    assert.strictEqual(records.length, 1);

    await page.click('.loja-btn[data-store-id="store-b"]');
    assert((await page.locator('#listaNacionalizadores').innerText()).includes('Empresa Atualizada'), 'outra loja do mesmo cliente compartilha lista');
    await page.click('.loja-btn[data-store-id=""]');
    assert(await page.locator('#btnSalvarNacionalizador').isDisabled());
    assert.strictEqual(await page.locator('#listaNacionalizadores button').count(), 0);
    assert.strictEqual(await page.evaluate(() => window.JKCadastro.nacionalizadores.salvar({ preventDefault() {} })), false);
    await page.click('.loja-btn[data-store-id="store-a"]');
    await page.locator('#listaNacionalizadores button').click();
    await page.fill('#nacionalizadorNomeContato', 'Edição em andamento');
    await page.click('#btnAtualizarNacionalizadores');
    await page.waitForFunction(() => !document.querySelector('#btnAtualizarNacionalizadores').disabled);
    assert.strictEqual(await page.inputValue('#nacionalizadorNomeContato'), 'Edição em andamento', 'refresh não deve substituir formulário');

    records = [{ id: 'registro-1', nome_empresa: '<img src=x onerror="window.injected=true">', cnpj: '<b>texto</b>' }];
    await page.click('#btnAtualizarNacionalizadores');
    await page.waitForFunction(() => !document.querySelector('#btnAtualizarNacionalizadores').disabled);
    assert.strictEqual(await page.locator('#listaNacionalizadores img, #listaNacionalizadores b').count(), 0, 'conteúdo deve ser escapado');
    assert.strictEqual(await page.evaluate(() => window.injected), undefined);
    failRead = true;
    await page.click('#btnAtualizarNacionalizadores');
    await page.waitForFunction(() => document.querySelector('#nacionalizadorStatus').textContent.includes('Falha de leitura'));
    assert.strictEqual(await page.locator('#listaNacionalizadores article').count(), 1, 'falha de leitura preserva lista atual');

    holdRead = true;
    const readStarted = new Promise(resolve => { readReceived = resolve; });
    await page.evaluate(() => { window.oldRead = window.JKCadastro.nacionalizadores.carregar(); });
    await readStarted;
    records = [{ id: 'registro-novo', nome_empresa: 'Resposta mais recente' }];
    await page.evaluate(() => window.JKCadastro.nacionalizadores.carregar());
    releaseRead();
    await page.evaluate(() => window.oldRead);
    assert((await page.locator('#listaNacionalizadores').innerText()).includes('Resposta mais recente'), 'resposta antiga deve ser descartada');

    await page.click('#btnLimparNacionalizador');
    await page.fill('#nacionalizadorNomeEmpresa', 'Empresa Segunda');
    holdSave = true;
    const saveStarted = new Promise(resolve => { saveReceived = resolve; });
    await page.evaluate(() => { window.oldSave = window.JKCadastro.nacionalizadores.salvar({ preventDefault() {} }); });
    await saveStarted;
    const count = mutations.length;
    assert.strictEqual(await page.evaluate(() => window.JKCadastro.nacionalizadores.salvar({ preventDefault() {} })), false, 'duplo envio deve ser bloqueado');
    assert.strictEqual(mutations.length, count);
    assert(await page.locator('#nacionalizadorNomeEmpresa').isDisabled());
    await page.evaluate(() => {
      localStorage.setItem('user_data', JSON.stringify({ client_id: 'cliente-b' }));
      window.JKCadastro.runtime.state.clientId = 'cliente-b';
      window.JKCadastro.nacionalizadores.atualizarEstadoMutacoes();
    });
    releaseSave();
    await page.evaluate(() => window.oldSave);
    assert.strictEqual(await page.locator('#listaNacionalizadores article').count(), 0, 'resposta do cliente anterior deve ser descartada');
    assert.strictEqual(await page.inputValue('#nacionalizadorNomeEmpresa'), '');
    records = [];
    holdRead = true;
    const tenantReadStarted = new Promise(resolve => { readReceived = resolve; });
    await page.evaluate(() => { window.oldRead = window.JKCadastro.nacionalizadores.carregar(); });
    await tenantReadStarted;
    await page.evaluate(() => {
      localStorage.setItem('user_data', JSON.stringify({ client_id: 'cliente-c' }));
      window.JKCadastro.nacionalizadores.atualizarEstadoMutacoes();
    });
    releaseRead();
    await page.evaluate(() => window.oldRead);
    assert.strictEqual(await page.locator('#listaNacionalizadores article').count(), 0);
    assert.deepStrictEqual(errors, []);
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'layout deve caber em tela estreita');
    console.log('cadastro nacionalizadores browser: inclusão, edição, falhas, contexto, concorrência e layout OK');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
