'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const host = 'http://jk.test/importacoes.html';
const previewDirectory = process.env.JK_DATA_PEDIDO_PREVIEW_DIR;
const mainContainer = '#listaWrap';
const shipmentContainer = '#embarqueDetalheListas';
const suppliers = [
  { id: 'supplier-a', nome_empresa: 'Fornecedor A' },
  { id: 'supplier-b', nome_empresa: 'Fornecedor B' },
];
const nationalizers = [
  { id: 'nationalizer-a', nome_empresa: 'Nacionalizador A' },
  { id: 'nationalizer-b', nome_empresa: 'Nacionalizador B' },
];

function fixture() {
  const base = {
    loja: 'Loja sintética', store_id: 'synthetic-store', status: 'Analisando orçamento',
    fornecedor_id: 'supplier-a', supplier: 'Fornecedor A',
    nacionalizador_id: 'nationalizer-a', nacionalizador: 'Nacionalizador A',
    created_at: '2026-10-05T12:00:00Z', updated_at: '2026-10-05T12:00:00Z',
    invoice: 'SYN-001', total_itens: 1, total_quantidade: 2, total_usd: 20, total_m3: 0.03,
    itens: [{ SKU: 'SYN-001', Quantidade: 2, 'Valor unidade': 10, 'Valor total': 20 }],
  };
  return {
    lists: [
      { ...structuredClone(base), id: 'order', nome_lista: 'Pedido sintético' },
      { ...structuredClone(base), id: 'legacy-br', nome_lista: 'Pedido com data brasileira', order_date: '06/10/2026' },
      { ...structuredClone(base), id: 'legacy-text', nome_lista: 'Pedido com data histórica', order_date: 'Data histórica pendente' },
      { ...structuredClone(base), id: 'iso-date', nome_lista: 'Pedido com data ISO', order_date: '2026-01-01' },
    ],
    updates: [], routeErrors: [], dateGate: null, failNextDate: false, staleDateResponse: false,
  };
}

const json = (route, body, status = 200) => route.fulfill({
  status, contentType: 'application/json', body: JSON.stringify(body),
});

async function installRoutes(page, state) {
  await page.route('**/*', async route => {
    try {
      const request = route.request();
      const url = new URL(request.url());
      const pathname = url.pathname;
      if (url.origin !== 'http://jk.test') {
        if (url.hostname === 'economia.awesomeapi.com.br') return json(route, { USDBRL: { bid: '5.2' } });
        return route.fulfill({ status: 200, contentType: 'text/css', body: '' });
      }
      if (pathname === '/importacoes.html') {
        return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8',
          body: fs.readFileSync(path.join(root, 'static', 'importacoes.html')) });
      }
      if (pathname.startsWith('/medias_compras/')) {
        return route.fulfill({ status: 200,
          contentType: pathname.endsWith('.css') ? 'text/css' : 'application/javascript',
          body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
      }
      if (pathname === '/auth.js') {
        return route.fulfill({ status: 200, contentType: 'application/javascript',
          body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic"});' });
      }
      if (pathname === '/api/lojas') return json(route, [{ nome: 'Loja sintética', store_id: 'synthetic-store' }]);
      if (pathname === '/api/cadastro/fornecedores') return json(route, suppliers);
      if (pathname === '/api/cadastro/nacionalizadores') return json(route, nationalizers);
      if (pathname === '/api/medias-compras/listas-pedidos') {
        return json(route, { listas: structuredClone(state.lists) });
      }
      const match = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)$/);
      if (match) {
        const id = decodeURIComponent(match[1]);
        const list = state.lists.find(item => item.id === id);
        assert(list, 'A requisição deve referenciar uma lista sintética existente');
        if (request.method() === 'GET') return json(route, { lista: structuredClone(list) });
        assert.strictEqual(request.method(), 'PUT');
        assert.strictEqual(request.headers().authorization, 'Bearer synthetic');
        const payload = request.postDataJSON();
        const original = structuredClone(list);
        const isDate = Object.hasOwn(payload, 'order_date');
        if (isDate) assert.deepStrictEqual(Object.keys(payload), ['order_date'], 'Data deve enviar somente order_date');
        state.updates.push({ id, payload });
        const fail = isDate && state.failNextDate;
        if (isDate) state.failNextDate = false;
        const gate = isDate && state.dateGate;
        const stale = isDate && state.staleDateResponse;
        if (gate) await gate;
        if (fail) return json(route, { detail: 'Falha sintética ao salvar data do pedido' }, 503);
        Object.assign(list, payload);
        if (Object.hasOwn(payload, 'fornecedor_id')) {
          list.supplier = suppliers.find(item => item.id === payload.fornecedor_id)?.nome_empresa || '';
        }
        if (Object.hasOwn(payload, 'nacionalizador_id')) {
          list.nacionalizador = nationalizers.find(item => item.id === payload.nacionalizador_id)?.nome_empresa || '';
        }
        return json(route, { lista: stale ? { ...original, ...payload } : structuredClone(list) });
      }
      if (pathname.startsWith('/api/')) return json(route, {});
      return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    } catch (error) {
      state.routeErrors.push(error.message);
      return json(route, { detail: error.message }, 500);
    }
  });
}

function datePill(page, id = 'order', container = mainContainer) {
  return page.locator(container + ' [data-order-date-pill][data-lista-order-date-id="' + id + '"]');
}

async function waitClosed(page, id, text, container = mainContainer) {
  await page.waitForFunction(({ selector, text }) => {
    const pill = document.querySelector(selector);
    return pill && !pill.querySelector('input') && pill.textContent.includes(text) && pill.getAttribute('aria-busy') !== 'true';
  }, { selector: container + ' [data-order-date-pill][data-lista-order-date-id="' + id + '"]', text });
  return datePill(page, id, container);
}

async function openDate(page, id = 'order', container = mainContainer, keyboard = null) {
  const pill = datePill(page, id, container);
  const url = page.url();
  assert.strictEqual(await pill.getAttribute('role'), 'button', 'Pill de data deve ter semântica de botão');
  assert.strictEqual(await pill.getAttribute('tabindex'), '0', 'Pill de data deve aceitar foco pelo teclado');
  if (keyboard) await pill.press(keyboard);
  else await pill.dblclick();
  const input = pill.locator('input[type="date"]');
  await input.waitFor({ state: 'visible' });
  assert.match(await input.getAttribute('aria-label'), /^Data do pedido da lista /, 'Editor deve ter nome acessível');
  assert.strictEqual(await input.isDisabled(), false);
  assert.strictEqual(page.url(), url, 'Abrir o editor não pode navegar para o detalhe da lista');
  return input;
}

async function settle(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function saveDate(page, state, value, expected, { id = 'order', container = mainContainer, blur = false } = {}) {
  const before = state.updates.length;
  const input = await openDate(page, id, container);
  await input.fill(value);
  if (blur) await page.locator('#status').click();
  else await input.press('Enter');
  await waitClosed(page, id, expected, container);
  assert.strictEqual(state.updates.length, before + 1, 'Uma edição deve produzir exatamente um PUT');
  assert.deepStrictEqual(state.updates.at(-1), { id, payload: { order_date: value } });
}

async function changeRegistration(page, field, label, expectedId) {
  const pill = datePill(page).locator('xpath=ancestor::div[contains(@class,"lista-item")][1]')
    .locator('[data-' + field + '-pill]');
  await pill.dblclick();
  const input = pill.locator('input');
  await input.waitFor({ state: 'visible' });
  await page.waitForFunction(({ field }) => {
    const input = document.querySelector('#listaWrap [data-' + field + '-pill][data-lista-cadastro-id="order"] input');
    return input?.list?.options.length === 2;
  }, { field });
  await input.fill(label);
  await input.press('Enter');
  await page.waitForFunction(({ field, label }) => {
    const pill = document.querySelector('#listaWrap [data-' + field + '-pill][data-lista-cadastro-id="order"]');
    return pill && !pill.querySelector('input') && pill.textContent.includes(label) && pill.getAttribute('aria-busy') === 'false';
  }, { field, label });
  return { [field + '_id']: expectedId };
}

async function openShipment(page) {
  await page.locator('#embarquesWrap .embarque-item').filter({ hasText: 'Embarque sintético' }).click();
  await datePill(page, 'order', shipmentContainer).waitFor({ state: 'visible' });
}

async function testPendingAndStale(page, state) {
  let release;
  state.dateGate = new Promise(resolve => { release = resolve; });
  state.staleDateResponse = true;
  const before = state.updates.length;
  const input = await openDate(page);
  await input.fill('2026-10-12');
  const pending = page.waitForRequest(request => request.method() === 'PUT' &&
    request.postDataJSON()?.order_date === '2026-10-12');
  await input.press('Enter');
  await pending;
  assert.strictEqual(await input.isDisabled(), true, 'Editor deve bloquear durante a gravação');
  assert.strictEqual(await datePill(page).getAttribute('aria-busy'), 'true');
  assert.strictEqual(await datePill(page).getAttribute('aria-disabled'), 'true');
  assert.match(await page.locator('#status').innerText(), /Salvando data do pedido/);
  // A primeira gravação foi disparada pelo teclado real; estes eventos extras exercitam a reentrada durante o PUT.
  await input.dispatchEvent('keydown', { key: 'Enter', bubbles: true });
  await input.dispatchEvent('blur');
  await input.dispatchEvent('keydown', { key: 'Escape', bubbles: true });
  assert.strictEqual(await input.count(), 1, 'Esc não pode descartar uma gravação em curso');
  const supplierPayload = await changeRegistration(page, 'fornecedor', 'Fornecedor B', 'supplier-b');
  const nationalizerPayload = await changeRegistration(page, 'nacionalizador', 'Nacionalizador B', 'nationalizer-b');
  assert.deepStrictEqual(state.updates.slice(before).map(update => update.payload), [
    { order_date: '2026-10-12' }, supplierPayload, nationalizerPayload,
  ], 'Campos independentes devem salvar sem repetir a data');
  await page.locator('#lojaFiltroListas button').filter({ hasText: 'Todas as lojas' }).click();
  const recreated = datePill(page);
  assert.strictEqual(await recreated.getAttribute('aria-disabled'), 'true', 'Recriar o cartão deve preservar o bloqueio');
  // O mouse direto permite tentar o controle bloqueado sem o filtro de actionability do Playwright.
  await recreated.scrollIntoViewIfNeeded();
  const busyRect = await recreated.boundingBox();
  await page.mouse.click(busyRect.x + busyRect.width / 2, busyRect.y + busyRect.height / 2, { clickCount: 2 });
  assert.strictEqual(await recreated.locator('input').count(), 0, 'Cartão recriado não deve reabrir data pendente');
  await openShipment(page);
  const shipment = datePill(page, 'order', shipmentContainer);
  assert.strictEqual(await shipment.getAttribute('aria-disabled'), 'true', 'Embarque deve compartilhar o bloqueio da lista');
  await shipment.press('Enter');
  assert.strictEqual(await shipment.locator('input').count(), 0);
  assert.strictEqual(state.updates.length, before + 3);
  state.dateGate = null;
  release();
  await waitClosed(page, 'order', '12/10/2026', shipmentContainer);
  await waitClosed(page, 'order', '12/10/2026');
  const cached = await page.evaluate(() => todasListasCache.find(item => item.id === 'order'));
  assert.strictEqual(cached.fornecedor_id, 'supplier-b', 'Resposta antiga de data deve preservar fornecedor novo');
  assert.strictEqual(cached.supplier, 'Fornecedor B');
  assert.strictEqual(cached.nacionalizador_id, 'nationalizer-b', 'Resposta antiga de data deve preservar nacionalizador novo');
  assert.strictEqual(cached.nacionalizador, 'Nacionalizador B');
  assert.strictEqual(cached.loja, 'Loja sintética');
  assert.strictEqual(cached.store_id, 'synthetic-store');
  assert.strictEqual(cached.nome_lista, 'Pedido sintético');
  assert.deepStrictEqual(cached.itens, state.lists[0].itens);
  const card = shipment.locator('xpath=ancestor::div[contains(@class,"lista-item")][1]');
  assert.match(await card.locator('[data-fornecedor-pill]').innerText(), /Fornecedor B/);
  assert.match(await card.locator('[data-nacionalizador-pill]').innerText(), /Nacionalizador B/);
  state.staleDateResponse = false;
  await saveDate(page, state, '2026-10-13', '13/10/2026', { container: shipmentContainer, blur: true });
  await page.locator('#btnVoltarEmbarque').click();
  await waitClosed(page, 'order', '13/10/2026');
}

async function assertLayout(page, container) {
  const geometry = await datePill(page, 'order', container).evaluate(pill => {
    const card = pill.closest('.lista-item');
    const r = pill.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    const actions = [...card.querySelectorAll('button')].map(button => {
      const b = button.getBoundingClientRect();
      return {
        text: button.textContent.trim(), visible: b.width > 0 && b.height > 0,
        contained: b.left >= cardRect.left - 1 && b.right <= cardRect.right + 1,
        overlapsDate: r.left < b.right && r.right > b.left && r.top < b.bottom && r.bottom > b.top,
        disabled: button.disabled, tabIndex: button.tabIndex,
      };
    });
    return {
      cardFits: card.scrollWidth <= card.clientWidth + 1,
      pageFits: document.documentElement.scrollWidth <= window.innerWidth + 1,
      pillFits: r.left >= cardRect.left - 1 && r.right <= cardRect.right + 1,
      actions,
    };
  });
  assert.strictEqual(geometry.cardFits, true, 'Cartão não pode ter overflow horizontal: ' + JSON.stringify(geometry));
  assert.strictEqual(geometry.pageFits, true, 'Página não pode ter overflow horizontal');
  assert.strictEqual(geometry.pillFits, true, 'Pill/editor deve caber dentro do cartão');
  if (container === mainContainer) assert(geometry.actions.length >= 2, 'Ações originais devem permanecer presentes');
  for (const action of geometry.actions) {
    assert(action.text && action.visible && action.contained && !action.overlapsDate && !action.disabled && action.tabIndex >= 0,
      'Ação deve estar visível, acessível e sem sobrepor a data: ' + JSON.stringify(action));
  }
}

async function testLayouts(page, state) {
  const before = state.updates.length;
  for (const width of [1280, 1033, 880, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await assertLayout(page, mainContainer);
    const input = await openDate(page, 'order', mainContainer, 'Enter');
    await assertLayout(page, mainContainer);
    await input.press('Escape');
    await openShipment(page);
    await assertLayout(page, shipmentContainer);
    const detailInput = await openDate(page, 'order', shipmentContainer, 'Space');
    await assertLayout(page, shipmentContainer);
    await detailInput.press('Escape');
    await page.locator('#btnVoltarEmbarque').click();
  }
  assert.strictEqual(state.updates.length, before, 'Verificar layout e fechar o editor não deve salvar');
}

async function newFixturePage(browser, timezoneId) {
  const state = fixture();
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, timezoneId });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem('jk_embarques_importacoes', JSON.stringify([
      { id: 'synthetic-shipment', nome: 'Embarque sintético', lista_ids: ['order', 'iso-date'] },
    ]));
  });
  await installRoutes(page, state);
  await page.goto(host);
  try {
    await waitClosed(page, 'order', 'Não informada');
  } catch (error) {
    console.error('Fixture initialization:', { errors, routeErrors: state.routeErrors,
      status: await page.locator('#status').textContent(), pills: await page.locator('[data-order-date-pill]').count() });
    await context.close();
    throw error;
  }
  return { page, context, state, errors };
}

async function testInteractions(browser) {
  const { page, context, state, errors } = await newFixturePage(browser, 'America/Sao_Paulo');
  try {
    const url = page.url();
    await datePill(page).click();
    assert.strictEqual(await datePill(page).locator('input').count(), 0, 'Clique simples não deve editar nem navegar');
    assert.strictEqual(page.url(), url);
    for (const key of ['Enter', 'Space']) {
      const input = await openDate(page, 'order', mainContainer, key);
      await input.fill('2026-10-07');
      await input.press('Escape');
      await waitClosed(page, 'order', 'Não informada');
      assert.strictEqual(page.url(), url);
      assert.strictEqual(await datePill(page).evaluate(pill => document.activeElement === pill), true,
        'Cancelar pelo teclado deve devolver foco ao pill');
    }
    assert.strictEqual(state.updates.length, 0, 'Esc deve cancelar a inclusão sem gravar');
    await saveDate(page, state, '2026-10-07', '07/10/2026');
    await saveDate(page, state, '2026-10-08', '08/10/2026', { blur: true });
    const sameBefore = state.updates.length;
    const unchanged = await openDate(page);
    await unchanged.fill('2026-10-08');
    await unchanged.press('Enter');
    await waitClosed(page, 'order', '08/10/2026');
    assert.strictEqual(state.updates.length, sameBefore, 'A mesma data não deve produzir PUT');
    await saveDate(page, state, '', 'Não informada');

    for (const [id, display, normalized] of [
      ['legacy-br', '06/10/2026', '2026-10-06'],
      ['legacy-text', 'Data histórica pendente', ''],
    ]) {
      const original = state.lists.find(list => list.id === id).order_date;
      const before = state.updates.length;
      const input = await openDate(page, id);
      assert.strictEqual(await input.inputValue(), normalized);
      await page.locator('#status').click();
      await waitClosed(page, id, display);
      const again = await openDate(page, id);
      await again.press('Enter');
      await waitClosed(page, id, display);
      assert.strictEqual(state.updates.length, before, 'Abrir/fechar uma data legada não deve normalizar nem apagar');
      assert.strictEqual(state.lists.find(list => list.id === id).order_date, original);
    }

    state.failNextDate = true;
    const failing = await openDate(page);
    await failing.fill('2026-10-09');
    await failing.press('Enter');
    await page.waitForFunction(() => document.querySelector('#listaWrap [data-lista-order-date-id="order"] input')?.getAttribute('aria-invalid') === 'true');
    assert.strictEqual(await failing.inputValue(), '2026-10-09', 'Erro deve preservar a data digitada');
    assert.strictEqual(await failing.isDisabled(), false, 'Erro deve liberar nova tentativa');
    assert.strictEqual(state.lists[0].order_date, '', 'Falha não deve alterar o registro da fixture');
    assert.match(await page.locator('#status').innerText(), /Falha sintética ao salvar data do pedido/);
    const retryBefore = state.updates.length;
    await failing.press('Enter');
    await waitClosed(page, 'order', '09/10/2026');
    assert.strictEqual(state.updates.length, retryBefore + 1);
    assert.match(await page.locator('#status').innerText(), /Data do pedido atualizada com sucesso/);
    console.log('PASS edição: teclado, Enter/blur, payload somente order_date, Esc, limpeza, datas legadas e falha/retry');
    await testPendingAndStale(page, state);
    await page.reload();
    await waitClosed(page, 'order', '13/10/2026');
    const card = datePill(page).locator('xpath=ancestor::div[contains(@class,"lista-item")][1]');
    if (previewDirectory) await card.screenshot({ path: path.join(previewDirectory, 'importacoes-data-pedido-preenchida.png') });
    assert.strictEqual(await (await openDate(page)).inputValue(), '2026-10-13', 'Recarga deve recuperar a data persistida');
    if (previewDirectory) await card.screenshot({ path: path.join(previewDirectory, 'importacoes-data-pedido-editor.png') });
    await page.keyboard.press('Escape');
    assert.match(await card.locator('[data-fornecedor-pill]').innerText(), /Fornecedor B/);
    assert.match(await card.locator('[data-nacionalizador-pill]').innerText(), /Nacionalizador B/);
    await testLayouts(page, state);
    await settle(page);
    assert.deepStrictEqual(errors, [], 'Scripts reais da página não devem lançar erros');
    assert.deepStrictEqual(state.routeErrors, [], 'A fixture deve validar todas as requisições');
    console.log('PASS interações: Enter/blur, payload, Esc, limpeza, legadas, falha/retry, PUT lento/antigo, embarque, recarga e layout 1280/1033/880/390');
  } finally {
    await context.close();
  }
}

async function testTimezones(browser) {
  for (const timezoneId of ['Pacific/Honolulu', 'Pacific/Kiritimati']) {
    const { page, context, state, errors } = await newFixturePage(browser, timezoneId);
    try {
      await waitClosed(page, 'iso-date', '01/01/2026');
      const iso = await openDate(page, 'iso-date');
      assert.strictEqual(await iso.inputValue(), '2026-01-01', timezoneId + ': abrir data ISO deve preservar o dia');
      await iso.press('Escape');
      await saveDate(page, state, '2024-02-29', '29/02/2024', { id: 'iso-date' });
      assert.strictEqual(state.lists.find(list => list.id === 'iso-date').order_date, '2024-02-29');
      await page.reload();
      await waitClosed(page, 'iso-date', '29/02/2024');
      await waitClosed(page, 'legacy-br', '06/10/2026');
      assert.deepStrictEqual(errors, []);
      assert.deepStrictEqual(state.routeErrors, []);
      console.log('PASS fuso ' + timezoneId + ': ISO, ano bissexto, persistência na recarga e data brasileira');
    } finally {
      await context.close();
    }
  }
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    await testInteractions(browser);
    await testTimezones(browser);
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
