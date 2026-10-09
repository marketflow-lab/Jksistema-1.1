'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const campaign = 'LGH-SYNTHETIC';
const dated = day => ({ start_date: `2026-10-${day}T15:00:00+00:00`, finish_date: `2026-10-${day}T23:00:00+00:00` });
const baseWindows = ['09','10','12'].map((day, index) => ({ selection_id: 'window-' + day, lightning_window: dated(day), active_count: index, eligible_count: index + 2 }));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const json = (route, value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
const waitUntil = async predicate => { const until = Date.now() + 10000; while (!predicate()) { if (Date.now() > until) throw new Error('Synthetic request condition timed out'); await new Promise(resolve => setTimeout(resolve, 15)); } };

async function createPage(browser, prefs) {
 const page = await browser.newPage({ viewport: { width: 1306, height: 850 } });
 const state = { windows: structuredClone(baseWindows), requests: [], failures: [], pageErrors: [], windowError: false, gate: null, storeGate: null, countsGate: null, formMeta: null };
 page.on('pageerror', error => state.pageErrors.push(error.message));
 await page.addInitScript(prefs => {
  localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-synthetic', username: 'operator-synthetic' }));
  if (prefs) localStorage.setItem('promo_prefs_v4__api_auto__tenant-synthetic__operator-synthetic', JSON.stringify(prefs));
 }, prefs);
 await page.route('**/*', async route => {
  const url = new URL(route.request().url());
  const pathname = url.pathname;
  if (url.origin !== 'http://promo.test') { state.failures.push(url.origin); return route.abort(); }
  if (['/auth.js','/ia-sidebar.js'].includes(pathname)) return route.fulfill({ contentType: 'text/javascript', body: '' });
  if (pathname === '/frontend_promo/init.js') return route.fulfill({ contentType: 'text/javascript', body: 'loadColumnPrefs(); loadPagePrefs(); loadActionTolerance();' });
  if (pathname === '/frontend_promo.html') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
  if (/^\/frontend_promo\/[\w-]+\.(?:js|css)$/.test(pathname) || pathname === '/table_columns.js') return route.fulfill({ contentType: pathname.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
  state.requests.push({ path: pathname, store: url.searchParams.get('loja'), expected: url.searchParams.get('expected_min_items') });
  if (pathname === '/api/mercadolivre/promocoes') return json(route, { success: true, campaigns: [
   { id: 'P1-SYNTHETIC', type: 'SELLER_CAMPAIGN', name: 'Referência sintética' },
   { id: campaign, type: 'LIGHTNING', name: 'Ofertas relâmpago', active_count: 999, eligible_count: 999 },
   { id: 'DEAL-SYNTHETIC', type: 'DEAL', name: 'Campanha sintética', active_count: 1, eligible_count: 2 },
  ] });
  if (pathname === `/api/promo/campanhas/${campaign}/janelas`) {
   const snapshot = structuredClone(state.windows);
   const fail = state.windowError;
   const gate = url.searchParams.get('loja') === 'store-a' ? state.storeGate || state.gate : state.gate;
   if (gate) await gate;
   if (fail) return json(route, { detail: 'Datas indisponíveis na consulta sintética' }, 503);
   return json(route, { promotion_id: campaign, promotion_type: 'LIGHTNING', windows: snapshot });
  }
  if (pathname === '/api/mercadolivre/promocoes/contagens') {
   if (state.countsGate) await state.countsGate;
   return json(route, { counts: { [campaign]: { active_count: 200, eligible_count: 300 } } });
  }
  if (pathname === '/api/promo/preferencias-colunas') return json(route, { success: true, widths: {} });
  if (pathname === '/api/promo/analise-via-api/start') {
   const body = route.request().postData() || '';
   const match = body.match(/name="promocoes_b_meta"\r\n\r\n([^\r]+)/);
   assert(match, 'Analysis request must include metadata'); state.formMeta = JSON.parse(match[1]);
   return json(route, { success: true, job_id: 'synthetic-job' });
  }
  if (pathname === '/api/promo/analise-via-api-arquivos/progresso/synthetic-job') return json(route, { success: true, status: 'completed', job_id: 'synthetic-job', result: { analises: state.formMeta.map(meta => ({ ...meta, data: [] })) } });
  state.failures.push(pathname); return route.abort();
 });
 await page.goto('http://promo.test/frontend_promo.html');
 await page.evaluate(() => { document.getElementById('apiLojaSelect').innerHTML = '<option value="store-a">Loja A sintética</option><option value="store-b">Loja B sintética</option>'; });
 return { page, state };
}
const selections = page => page.evaluate(() => getApiPromoBSelections().map(value => ({ id: value.value, key: value.selectionId, window: value.lightningWindow, count: value.eligibleCount })));
const picker = page => page.locator('.promo-campaign-picker');
const check = (page, key) => page.locator(`#apiPromoBSelect input[value="${key}"]`);

(async () => {
 const browser = await chromium.launch({ headless: true });
 try {
  const { page, state } = await createPage(browser, { promoBSelectedIds: [campaign], approvalRequired: true });
  state.windows.push({ selection_id: 'window-unknown', lightning_window: { undated: true }, active_count: 0, eligible_count: 2 },
   { selection_id: 'window-partial', lightning_window: { start_date: dated('15').start_date, finish_date: '' }, active_count: 0, eligible_count: 1 });
  const countsGate = deferred(); state.countsGate = countsGate.promise;
  await page.evaluate(() => carregarPromocoesApi());
  assert.strictEqual(await page.locator('#apiPromoBSelect .api-promo-card').count(), 6);
  assert.strictEqual(state.requests.find(request => request.path.endsWith('/janelas')).expected, '1998');
  assert.deepStrictEqual((await selections(page)).map(value => value.key), ['window-09','window-10','window-12','window-unknown','window-partial']);
  const labels = await page.locator('#apiPromoBSelect .api-promo-card-title').allTextContents();
  assert(labels[0].includes('9/out/2026 às 12:00'));
  assert(labels.some(label => label.includes('Data não informada')));
  assert(labels.some(label => label.includes('término não informado')));
  const naiveLabel = await page.evaluate(() => formatarJanelaSelecaoRelampago({ start_date: '2026-10-09T15:00:00', finish_date: '2026-10-09T23:00:00' }));
  assert(naiveLabel.includes('15:00')); assert(naiveLabel.includes('fuso não informado')); assert(!naiveLabel.includes('Brasília'));
  countsGate.resolve(); state.countsGate = null;
  await page.waitForTimeout(40);
  assert.strictEqual(await page.locator('#apiPromoBSelect .api-promo-card').count(), 6, 'Aggregate count refresh must preserve date expansion');
  assert.strictEqual((await selections(page))[0].count, 2, 'Window counts must not be overwritten with aggregate totals');
  const migration = await page.evaluate(() => JSON.parse(localStorage.getItem(getApiAutoPrefsKey())).promoBSelectedIds);
  assert(!migration.includes(campaign)); assert(migration.includes('window-09'));
  console.log('PASS dates/partial/unknown, legacy migration and per-window counts');

  await picker(page).locator('summary').click();
  for (const width of [1306, 430]) {
   await page.setViewportSize({ width, height: 850 });
   const visibility = await page.locator('.api-promo-card--lightning .api-promo-card-title').evaluateAll(titles => titles.map(title => ({
    clamp: getComputedStyle(title).webkitLineClamp, height: title.clientHeight, textHeight: title.scrollHeight,
   })));
   assert(visibility.length >= 3); assert(visibility.every(title => title.clamp === 'none' && title.textHeight <= title.height + 1), 'The full Lightning time window must be visible on desktop and mobile');
   assert.strictEqual(await page.locator('.api-promo-card:not(.api-promo-card--lightning) .api-promo-card-title').first().evaluate(title => getComputedStyle(title).webkitLineClamp), '2', 'Other campaign titles keep the existing layout');
  }
  await page.setViewportSize({ width: 1306, height: 850 });
  await check(page, 'window-10').uncheck();
  assert.strictEqual(await picker(page).evaluate(element => element.open), true);
  await page.getByRole('button', { name: 'Limpar', exact: true }).click();
  assert.strictEqual(await picker(page).evaluate(element => element.open), true, 'Rerendered inner button must not close the picker');
  assert.deepStrictEqual(await selections(page), []);
  await page.getByRole('button', { name: 'Selecionar todas', exact: true }).click();
  assert.strictEqual(await picker(page).evaluate(element => element.open), true);
  await page.locator('h1').click();
  assert.strictEqual(await picker(page).evaluate(element => element.open), false);
  await picker(page).locator('summary').click();
  await page.getByRole('button', { name: 'Limpar', exact: true }).click();
  await check(page, 'window-12').check();
  await page.locator('h1').click();
  assert.deepStrictEqual((await selections(page)).map(value => value.key), ['window-12']);
  assert((await picker(page).locator('summary').textContent()).includes('12/out/2026'), 'Closed selection must show its date');
  assert.deepStrictEqual(await page.evaluate(() => getApiAutoPrefs().promoBSelectedIds), ['window-12']);
  const autoMeta = await page.evaluate(() => montarPayloadApiAutoServidor().promocoes_b_meta);
  assert.strictEqual(autoMeta[0].promo_b_id, campaign); assert.strictEqual(autoMeta[0].selection_id, 'window-12');
  assert.deepStrictEqual(autoMeta[0].lightning_window, dated('12'));
  const result = await page.evaluate(() => processarAnaliseApi());
  assert.strictEqual(result.status, 'completed'); assert.deepStrictEqual(state.formMeta, autoMeta);
  const tab = await page.locator('.api-analysis-tab-group button').first().textContent();
  assert(tab.includes('12/out/2026'));
  state.windows.push({ selection_id: 'window-future', lightning_window: dated('30'), active_count: 0, eligible_count: 1 });
  await page.evaluate(() => carregarPromocoesApi());
  assert.deepStrictEqual((await selections(page)).map(value => value.key), ['window-12'], 'New future window must stay unselected');
  await page.evaluate(() => aplicarApiAutoPrefsTela({ ...getApiAutoPrefs(), promoBSelectedIds: [], promoBSelectionDefined: true }));
  await page.evaluate(() => carregarPromocoesApi());
  assert.deepStrictEqual(await selections(page), [], 'Explicit empty preference must survive rerender and reload');
  console.log('PASS inside/outside, explicit empty state, metadata, persistence and future dates');

  // A response for a former store never changes the new store's cards or selection.
  const gate = deferred(); state.storeGate = gate.promise;
  const beforeRequests = state.requests.filter(request => request.path.endsWith('/janelas') && request.store === 'store-a').length;
  await page.evaluate(() => { window.oldLoad = carregarPromocoesApi(); });
  await waitUntil(() => state.requests.filter(request => request.path.endsWith('/janelas') && request.store === 'store-a').length > beforeRequests);
  await page.locator('#apiLojaSelect').selectOption('store-b');
  await check(page, 'window-future').waitFor({ state: 'attached' });
  gate.resolve(); state.storeGate = null;
  await page.evaluate(() => oldLoad);
  assert.strictEqual(await page.locator('#apiLojaSelect').inputValue(), 'store-b');
  assert.strictEqual((await page.evaluate(() => apiPromoBSelectionScope)), JSON.stringify(['tenant-synthetic','store-b']));
  await picker(page).locator('summary').click();
  await page.getByRole('button', { name: 'Limpar', exact: true }).click();
  await check(page, 'window-10').check();
  await page.locator('h1').click();
  await page.locator('#apiLojaSelect').selectOption('store-a');
  await page.waitForFunction(() => apiPromoBSelectionReady && apiPromoBSelectionScope === JSON.stringify(['tenant-synthetic','store-a']));
  assert.deepStrictEqual(await selections(page), [], 'Returning to A restores its explicit empty selection');
  await page.locator('#apiLojaSelect').selectOption('store-b');
  await page.waitForFunction(() => apiPromoBSelectionReady && apiPromoBSelectionScope === JSON.stringify(['tenant-synthetic','store-b']));
  assert.deepStrictEqual((await selections(page)).map(value => value.key), ['window-10'], 'Returning to B restores its own selected date');
  const byStore = await page.evaluate(() => JSON.parse(localStorage.getItem(getApiAutoPrefsKey())).promoBSelectedIdsByStore);
  assert.deepStrictEqual(byStore['store-a'], []); assert.deepStrictEqual(byStore['store-b'], ['window-10']);
  state.windowError = true;
  await page.evaluate(() => carregarPromocoesApi());
  assert.strictEqual(await page.locator(`#apiPromoBSelect input[data-promotion-id="${campaign}"]`).isDisabled(), true);
  assert((await page.locator('#apiPromoBSelect').textContent()).includes('Datas indisponíveis'));
  assert(!(await selections(page)).some(value => value.id === campaign), 'Failed dates cannot silently select aggregated LIGHTNING');
  console.log('PASS stale store load, per-store preferences and failed date endpoint blocking');

  // Two analyses sharing a campaign update only their own canonical row references.
  const updates = await page.evaluate(({ campaign, windows }) => {
   const make = (id, window) => ({ promo_b_id: campaign, promo_b_type: 'LIGHTNING', selection_id: id, lightning_window: window, job_id: 'job', origem_loja: 'store-b', origem_client_id: 'tenant-synthetic', data: [{ MLB: 'MLB1234567', analysis_client_ref: 'ref-' + id, action_deal_price: 10 }] });
   const a = make('a', windows[0].lightning_window), b = make('b', windows[1].lightning_window);
   apiAnalisesPorCampanha = [a,b];
   const payload = { loja: 'store-b', analysis_job_id: 'job', promocoes: [a,b].map(analysis => ({ promotion_id: campaign, items: [{ item_id: 'MLB1234567', client_ref: analysis.data[0].analysis_client_ref }] })) };
   aplicarAtualizacoesParticipacaoPromocoes(payload, { detalhes: [{ promotion_id: campaign, item_id: 'MLB1234567', client_ref: 'ref-b', outcome: 'applied', success: true, analysis_row_updates: { action_deal_price: 17 } }] }, [a,b]);
   const signatureA = capturarParametrosParticipacaoPromocoes();
   apiPromoBCampaigns = [{ id: campaign, type: 'LIGHTNING', selection_id: 'b', lightning_window: windows[1].lightning_window }]; apiPromoBSelectedIds = new Set(['b']); apiPromoBSelectionReady = true;
   const signatureB = capturarParametrosParticipacaoPromocoes();
   invalidarContextoParticipacaoPromocoes();
   return { prices: [a.data[0].action_deal_price,b.data[0].action_deal_price], invalidated: a.participacao_contexto_invalidado && b.participacao_contexto_invalidado, differentSignature: signatureA !== signatureB };
  }, { campaign, windows: baseWindows });
  assert.deepStrictEqual(updates.prices, [10,17]); assert(updates.invalidated); assert(updates.differentSignature);
  assert.deepStrictEqual(state.pageErrors, []); assert.deepStrictEqual(state.failures, []);
  console.log('PASS confirmation isolation by canonical client_ref and selection invalidation');

  const migrated = await createPage(browser, { promoBSelectedIds: [campaign] });
  await migrated.page.evaluate(() => carregarPromocoesApi());
  migrated.state.windows.push({ selection_id: 'new-date', lightning_window: dated('31'), active_count: 0, eligible_count: 1 });
  await migrated.page.evaluate(() => carregarPromocoesApi());
  await migrated.page.evaluate(campaign => aplicarApiAutoPrefsTela({ ...getApiAutoPrefs(), promoBSelectedIds: [campaign], promoBSelectionDefined: true }), campaign);
  assert(!(await selections(migrated.page)).some(value => value.key === 'new-date'), 'Polling legacy server preferences cannot select a newly added date');
  const fileMatches = await migrated.page.evaluate(() => {
   const same = ['09', '10', '12'].map(day => ({ value: 'LGH-MLB1000', selectionId: 'w-' + day, matchName: 'LGH-MLB1000' }));
   const file = { name: 'LGH-MLB1000.xlsx' };
   const correct = matchApiPromoFilesToSelections(same, [file]);
   const duplicateFiles = matchApiPromoFilesToSelections(same, [file, { ...file }]);
   const differentCampaigns = matchApiPromoFilesToSelections([same[0], { value: 'LGH-MLB2000', matchName: 'LGH-MLB2000' }], [{ name: 'LGH-MLB1000 LGH-MLB2000.xlsx' }]);
   return { reused: correct.every(result => result.file === file && result.matched), blockedFiles: duplicateFiles.every(result => !result.matched), blockedIds: differentCampaigns.every(result => !result.matched) };
  });
  assert(fileMatches.reused); assert(fileMatches.blockedFiles); assert(fileMatches.blockedIds);
  const empty = await createPage(browser, { promoBSelectedIds: [] });
  await empty.page.evaluate(() => carregarPromocoesApi()); assert.deepStrictEqual(await selections(empty.page), []);
  assert.deepStrictEqual(migrated.state.pageErrors, []); assert.deepStrictEqual(empty.state.pageErrors, []);
  console.log('PASS empty initial preferences, one-time migration and unambiguous shared XLSX matching');
  if (process.env.JK_PROMO_WINDOW_PREVIEW_DIR) {
   const preview = await createPage(browser, { promoBSelectedIds: [] });
   preview.state.windows.push({ selection_id: 'window-unknown', lightning_window: { undated: true }, active_count: 0, eligible_count: 2 });
   await preview.page.evaluate(() => carregarPromocoesApi());
   await picker(preview.page).locator('summary').click();
   await check(preview.page, 'window-12').check();
   await preview.page.evaluate(() => document.getElementById('apiPromoBSelect').querySelector('input[data-promotion-id="DEAL-SYNTHETIC"]').closest('.api-promo-card').remove());
   fs.mkdirSync(process.env.JK_PROMO_WINDOW_PREVIEW_DIR, { recursive: true });
   await preview.page.screenshot({ path: path.join(process.env.JK_PROMO_WINDOW_PREVIEW_DIR, 'promotion-window-picker-desktop.png'), fullPage: true });
   await preview.page.setViewportSize({ width: 430, height: 930 });
   await preview.page.screenshot({ path: path.join(process.env.JK_PROMO_WINDOW_PREVIEW_DIR, 'promotion-window-picker-mobile.png'), fullPage: true });
   assert.strictEqual(await picker(preview.page).evaluate(element => element.open), true);
   await preview.page.locator('h1').click(); assert.strictEqual(await picker(preview.page).evaluate(element => element.open), false);
   assert.deepStrictEqual(preview.state.pageErrors, []); assert.deepStrictEqual(preview.state.failures, []);
   console.log('PASS synthetic desktop/mobile preview and outside close');
  }
 } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
