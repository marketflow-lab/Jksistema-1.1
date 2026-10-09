'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1306, height: 850 } });
    const errors = [];
    const unexpectedRequests = [];
    const marginPalette = {
      low: ['rgba(255, 59, 77, 0.38)', 'rgb(255, 238, 241)'],
      mid: ['rgba(253, 126, 20, 0.28)', 'rgb(255, 232, 210)'],
      high: ['rgba(255, 214, 10, 0.42)', 'rgb(255, 251, 224)'],
      top: ['rgba(34, 197, 94, 0.34)', 'rgb(234, 255, 242)'],
      neutral: ['rgba(0, 0, 0, 0)', 'rgb(240, 244, 252)'],
    };
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => { unexpectedRequests.push(new URL(route.request().url()).origin); return route.abort(); });
    await page.route('http://promo.test/**', route => {
      const pathname = new URL(route.request().url()).pathname;
      if (['/auth.js', '/ia-sidebar.js'].includes(pathname)) return route.fulfill({ contentType: 'text/javascript', body: '' });
      if (pathname === '/frontend_promo/init.js') return route.fulfill({ contentType: 'text/javascript', body: 'loadColumnPrefs(); loadPagePrefs(); loadActionTolerance();' });
      if (pathname === '/frontend_promo.html') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(root, 'static/frontend_promo.html')) });
      if (/^\/frontend_promo\/[\w-]+\.(?:js|css)$/.test(pathname) || pathname === '/table_columns.js') {
        return route.fulfill({ contentType: pathname.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
      }
      unexpectedRequests.push(pathname);
      return route.abort();
    });
    await page.route('https://http2.mlstatic.com/**', route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/promo-fixture-failed.png') return route.abort();
      if (!/^\/promo-fixture-[12]\.png$/.test(pathname)) {
        unexpectedRequests.push(route.request().url());
        return route.abort();
      }
      return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><rect width="80" height="80" fill="white"/><path d="M18 51h44l-4-20H25z" fill="#7a8898"/><circle cx="28" cy="54" r="6" fill="#27384a"/><circle cx="54" cy="54" r="6" fill="#27384a"/></svg>' });
    });
    await page.goto('http://promo.test/frontend_promo.html');
    await page.evaluate(() => {
      document.getElementById('apiLojaSelect').innerHTML = '<option>Loja de teste</option>';
      document.getElementById('apiPromoASelect').innerHTML = '<option value="P1">Promo Outubro</option>';
      renderApiPromoBButtons([{ id: 'P2', type: 'LIGHTNING', name: 'Ofertas relâmpago', active_count: 0, eligible_count: 4 }]);
      const base = { Tipo: 'Clássico', '%': '15%', SKU: 'EXEMPLO-001', 'Título': 'Kit de reparo — exemplo de validação',
        'Frete': 'R$ 8,00', 'Frete ML': 'R$ 6,00', 'Frete Gratis': 'Sim', 'Frete Gratis ML': 'Sim', 'Custo': 'R$ 45,00',
        'Tarifa': 'R$ 13,50', 'Tarifa ML': 'R$ 12,30', MLB: 'MLB-EXEMPLO-001', 'Campanha ML': 'Oferta relâmpago',
        '% Fixa': '10%', 'ML % Campanha': '15%', 'Preço Final': 'R$ 89,90', 'Imposto %': '5%', 'Imposto': 'R$ 4,50',
        'Preço Final ML': 'R$ 84,90', 'Imposto ML': 'R$ 4,25', 'Desconto ML': 'R$ 3,00', 'Valor Líquido': 'R$ 18,90',
        'Valor líquido ML': 'R$ 21,40', Status: 'Ativo', Margem: '18,20%', 'Margem ML': '21,40%',
        'Ação': 'Participar', 'Participar ou não': 'Participar', action_financeiro_exato: true,
        action_promotion_type: 'LIGHTNING', lightning_action_blocked: false, lightning_stock_min: 1, lightning_stock_max: 10, lightning_stock: 2 };
      currentData = [
        { ...base, thumbnail: 'https://http2.mlstatic.com/promo-fixture-1.png', analysis_client_ref: 'teste:0', lightning_offer_key: 'oferta-1', lightning_start_date: '2026-10-10T12:00:00Z', lightning_finish_date: '2026-10-10T20:00:00Z' },
        { ...base, thumbnail: 'http://http2.mlstatic.com/promo-fixture-2.png', analysis_client_ref: 'teste:1', lightning_offer_key: 'oferta-2', SKU: 'EXEMPLO-002', 'Título': 'Jogo de juntas completo — exemplo',
          'Ação': 'Não participar', 'Participar ou não': 'Não participar', lightning_stock: 3, lightning_schedule_pending: true,
          _jk_tarifa_ml_estimada: true, action_financeiro_estimado: true, action_financeiro_estimativa_motivo: 'Tarifa aproximada. <img src=x onerror=alert(1)>',
          action_financeiro_motivo: 'Margem estimada para revisão.' },
        { ...base, thumbnail: 'https://http2.mlstatic.com/promo-fixture-failed.png', MLB: 'MLB-EXEMPLO-003', SKU: 'EXEMPLO-003', 'Título': 'Filtro de combustível — exemplo', analysis_client_ref: 'teste:2', lightning_offer_key: 'oferta-3',
          'Tarifa ML': 'R$ 0,00', 'Desconto ML': 'Não informado pela API', action_desconto_ml: null,
          'Valor líquido ML': '', 'Margem ML': 'A calcular', action_ja_participa: true },
        { ...base, thumbnail: 'javascript:alert(1)', MLB: 'MLB-EXEMPLO-004', SKU: 'EXEMPLO-004', 'Título': '<img src=x onerror=alert(1)>', analysis_client_ref: 'teste:3', lightning_offer_key: 'oferta-4',
          lightning_action_blocked: true, lightning_action_reason: 'Oferta pendente de dados.', 'Ação': 'Não participar', 'Participar ou não': 'Não participar' },
      ];
      apiAnalisesPorCampanha = [{ promo_b_id: 'P2', promo_b_type: 'LIGHTNING', promo_b_nome: 'Ofertas relâmpago', data: currentData, participacao_confirmada: true, participacao_contexto: JSON.stringify({ promoA: 'P1' }) }];
      window.initialRows = JSON.stringify(currentData);
      localStorage.setItem(getColumnWidthPrefsKey(), JSON.stringify({ SKU: 155, 'Ação': 175 }));
      window.initialColumnState = JSON.stringify(columnState);
      window.initialWidthPrefs = localStorage.getItem(getColumnWidthPrefsKey());
      renderApiAnalysisTabs();
      renderTable(currentData);
      document.getElementById('resultsArea').style.display = 'block';
    });
    await page.evaluate(() => {
      const option = document.createElement('option'); option.value = 'P1-NOVA'; option.textContent = 'Nova referência';
      document.getElementById('apiPromoASelect').appendChild(option);
      document.getElementById('apiPromoASelect').value = 'P1-NOVA';
      atualizarCabecalhoComparacaoPromocoes();
    });
    assert.strictEqual(await page.locator('#promoReferenceName').textContent(), 'Promo Outubro', 'changing setup must not relabel the completed analysis');
    await page.evaluate(() => { document.getElementById('apiPromoASelect').value = 'P1'; });
    const rows = page.locator('.promo-comparison-row');
    assert.strictEqual(await rows.count(), 4);
    await page.waitForFunction(() => [...document.querySelectorAll('.promo-comparison-row')].slice(0, 2).every(row => row.querySelector('.promo-ad-image')?.naturalWidth > 0));
    await rows.nth(2).locator('.promo-photo-placeholder').waitFor({ state: 'visible' });
    assert.strictEqual(await rows.nth(0).locator('.promo-ad-image').getAttribute('alt'), 'Foto de Kit de reparo — exemplo de validação');
    assert.strictEqual(await rows.nth(0).locator('.promo-ad-image').getAttribute('loading'), 'lazy');
    assert.strictEqual(await rows.nth(1).locator('.promo-ad-image').getAttribute('src'), 'https://http2.mlstatic.com/promo-fixture-2.png');
    assert.strictEqual(await rows.nth(2).locator('.promo-ad-image').count(), 0, 'broken image has a stable placeholder');
    assert.strictEqual(await rows.nth(3).locator('.promo-ad-image').count(), 0, 'unsafe URL must never create an image request');
    assert.strictEqual(await rows.nth(3).locator('.promo-photo-placeholder').textContent(), 'Sem foto');
    assert(await page.evaluate(() => [undefined, '', 'javascript:alert(1)', 'data:image/svg+xml,<svg/>', 'https://http2.mlstatic.com.evil.test/a.png', 'https://http2.mlstatic.com@evil.test/a.png', 'https://user:pass@http2.mlstatic.com/a.png', 'https://http2.mlstatic.com:8443/a.png', '/info/another-client/a.png', {}, 123].every(thumbnail => promoProductPhotoUrl({ thumbnail }) === '')), 'malformed, foreign and executable URLs are rejected');
    const decisionColors = await rows.locator('.decision-select').evaluateAll(items => items.map(select => getComputedStyle(select).backgroundColor));
    const rgb = color => color.match(/\d+/g).slice(0, 3).map(Number);
    assert(rgb(decisionColors[0])[1] > rgb(decisionColors[0])[0] + 30, 'Participar has a filled green background');
    assert(rgb(decisionColors[1])[0] > rgb(decisionColors[1])[1] + 30, 'Não participar has a filled red background');
    const photoSize = await rows.nth(0).locator('.promo-ad-photo').boundingBox();
    assert(photoSize.width === 64 && photoSize.height === 64, 'photo dimensions stay fixed and compact');
    assert.strictEqual(await page.locator('#tabelaAnalise thead th').count(), 2, 'hidden staging prepares only the original action and stock controls');
    assert.strictEqual(await page.evaluate(() => JSON.stringify(currentData) === initialRows), true, 'rendering must preserve canonical values and decisions');
    assert.strictEqual(await page.evaluate(() => JSON.stringify(columnState) === initialColumnState), true);
    assert.strictEqual(await page.locator('#promoLegacyTableWrap').isVisible(), false);
    assert.strictEqual(await rows.nth(0).locator('[data-promo-display-key="Valor líquido ML"]').textContent(), 'R$ 21,40');
    assert.strictEqual(await rows.nth(0).locator('.promo-ad [data-promo-display-key="%"]').textContent(), '15%');
    assert.strictEqual(await rows.nth(0).locator('.promo-one [data-promo-display-key="%"]').count(), 0, 'the shared sale rate must not be attributed to promotion 1');
    assert.strictEqual(await rows.nth(0).locator('[data-promo-delta-key="Margem ML"]').textContent(), '+3,20 p.p.');
    assert.strictEqual(await rows.nth(1).locator('[data-promo-display-key="Margem ML"] .promo-approx').count(), 1);
    assert.strictEqual(await rows.nth(1).locator('[data-promo-delta-key="Margem ML"]').textContent(), '≈ +3,20 p.p.');
    assert.strictEqual(await rows.nth(2).locator('[data-promo-display-key="Tarifa ML"]').textContent(), 'R$ 0,00');
    assert.strictEqual(await rows.nth(2).locator('[data-promo-display-key="Valor líquido ML"]').textContent(), 'Não informado');
    assert.strictEqual(await rows.nth(2).locator('[data-promo-display-key="Margem ML"]').textContent(), 'A calcular');
    assert.strictEqual(await rows.nth(2).locator('[data-promo-delta-key="Margem ML"]').textContent(), '—');
    assert.strictEqual(await rows.nth(2).locator('.promo-p2-status').textContent(), 'P2: Já participa');
    assert.strictEqual(await rows.nth(0).locator('.promo-lightning-stock input').count(), 1);
    assert.strictEqual(await rows.nth(2).locator('.promo-lightning-stock input').count(), 0);
    assert.strictEqual(await rows.nth(3).locator('.promo-lightning-stock input').count(), 0);
    assert.strictEqual(await page.locator('#promoComparison img:not(.promo-ad-image)').count(), 0, 'server strings must remain text outside the dedicated photo field');
    assert.strictEqual(await rows.nth(1).locator('.promo-lightning-window').textContent(), 'Data e horário serão informados pelo Mercado Livre após a inclusão.');
    assert.strictEqual(await page.evaluate(() => promoNumericValue('Não informado') === null && promoNumericValue('R$ 0,00') === 0 && promoNumericValue('R$ 1.234,56') === 1234.56), true);
    const liveMarginColors = await rows.nth(1).locator('[data-promo-display-key="Margem"], [data-promo-display-key="Margem ML"]').evaluateAll(items => items.map(item => {
      const style = getComputedStyle(item);
      return [style.backgroundColor, style.color];
    }));
    assert.deepStrictEqual(liveMarginColors, [marginPalette.top, marginPalette.top], 'both campaign margins retain the original filled green color, including estimates');
    assert.strictEqual(await rows.nth(0).locator('[data-promo-display-key="Preço Final"], [data-promo-display-key="Valor Líquido"], [data-promo-display-key="Preço Final ML"], [data-promo-display-key="Valor líquido ML"]').evaluateAll(items => items.every(item => getComputedStyle(item).backgroundColor === 'rgba(0, 0, 0, 0)')), true, 'margin highlighting does not spread to prices or net values');
    const marginCases = [
      ['-1,00%', 'low'], ['9,99%', 'low'], ['10,00%', 'mid'], ['12,99%', 'mid'],
      ['13,00%', 'high'], ['14,99%', 'high'], ['15,00%', 'top'], ['23,78%', 'top'],
      ['', 'neutral'], ['A calcular', 'neutral'], ['Não informado', 'neutral'], ['NaN', 'neutral'],
    ];
    const marginSnapshots = await page.evaluate(cases => {
      const canonicalBefore = JSON.stringify(currentData);
      const host = document.createElement('div');
      host.hidden = true;
      document.body.appendChild(host);
      try {
        const values = cases.map(([value]) => {
          const fixture = { ...currentData[0], Margem: value, 'Margem ML': value };
          const fixtureBefore = JSON.stringify(fixture);
          host.replaceChildren(promoCampaignMetrics(fixture, false), promoCampaignMetrics(fixture, true));
          const colors = [...host.querySelectorAll('[data-promo-display-key="Margem"], [data-promo-display-key="Margem ML"]')].map(item => {
            const style = getComputedStyle(item);
            return [style.backgroundColor, style.color];
          });
          return { colors, preserved: JSON.stringify(fixture) === fixtureBefore };
        });
        return { values, canonicalPreserved: JSON.stringify(currentData) === canonicalBefore };
      } finally { host.remove(); }
    }, marginCases);
    marginCases.forEach(([value, band], index) => {
      assert.deepStrictEqual(marginSnapshots.values[index].colors, [marginPalette[band], marginPalette[band]], 'original margin colors apply to both campaigns at ' + JSON.stringify(value));
      assert(marginSnapshots.values[index].preserved, 'margin presentation preserves fixture values');
    });
    assert(marginSnapshots.canonicalPreserved, 'boundary rendering preserves canonical analysis values and decisions');
    const aligned = await rows.nth(0).evaluate(row => {
      const p1 = [...row.querySelectorAll('.promo-one .promo-value-number')].map(el => el.getBoundingClientRect().top);
      const p2 = [...row.querySelectorAll('.promo-two .promo-value-number')].map(el => el.getBoundingClientRect().top);
      return p1.every((value, index) => Math.abs(value - p2[index]) < 1);
    });
    assert(aligned, 'campaign values must align for comparison');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'desktop must have no horizontal overflow');
    await page.locator('#promoSearch').fill('EXEMPLO-002');
    assert.strictEqual(await page.locator('.promo-comparison-row:visible').count(), 1);
    await rows.nth(1).locator('.decision-select').selectOption('Participar');
    assert.strictEqual(await rows.nth(1).locator('.decision-select').evaluate(select => getComputedStyle(select).backgroundColor), decisionColors[0], 'decision color updates immediately with the canonical control');
    await rows.nth(1).locator('.promo-lightning-stock input').fill('7');
    await rows.nth(1).locator('.promo-lightning-stock input').dispatchEvent('change');
    assert.deepStrictEqual(await page.evaluate(() => [currentData[0].lightning_stock, currentData[1].lightning_stock, currentData[1]['Ação'], currentData[1]['Participar ou não']]), [2, 7, 'Participar', 'Participar']);
    assert.strictEqual(await page.evaluate(() => apiAnalisesPorCampanha[0].participacao_confirmada), false);
    assert.strictEqual(await page.evaluate(() => apiAnalisesPorCampanha[0].participacao_selecao_revisao), 2);
    assert.strictEqual(await page.locator('#totalParticipar').textContent(), '3', 'metrics cover the entire dataset, not the filtered display');
    await page.evaluate(() => {
      window.originalSelect = document.querySelectorAll('.promo-comparison-row')[1].querySelector('select');
      window.originalStock = document.querySelectorAll('.promo-comparison-row')[1].querySelector('input');
      currentData[1]['Preço Final ML'] = 'R$ 82,00';
      currentData[1]['Margem ML'] = '12,55%';
      atualizarLinhaFinanceiraParticipacaoPromocoes(currentData[1]);
    });
    assert.strictEqual(await rows.nth(1).locator('[data-promo-display-key="Preço Final ML"]').textContent(), 'R$ 82,00');
    const refreshedMargin = rows.nth(1).locator('[data-promo-display-key="Margem ML"]');
    assert.deepStrictEqual(await refreshedMargin.evaluate(item => {
      const style = getComputedStyle(item);
      return [style.backgroundColor, style.color];
    }), marginPalette.mid, 'financial refresh replaces the previous green margin band with orange');
    assert.strictEqual(await refreshedMargin.textContent(), '≈12,55%', 'financial refresh preserves the estimate marker and canonical percentage');
    assert.strictEqual(await page.evaluate(() => originalSelect === document.querySelectorAll('.promo-comparison-row')[1].querySelector('select') && originalStock === document.querySelectorAll('.promo-comparison-row')[1].querySelector('input')), true, 'financial refresh preserves active controls');
    assert.strictEqual(await page.locator('#promoSearch').inputValue(), 'EXEMPLO-002');
    await page.evaluate(() => {
      currentData[3].action_financeiro_motivo = 'Frete atualizado após a verificação.';
      currentData[3].action_ja_participa = true;
      atualizarLinhaFinanceiraParticipacaoPromocoes(currentData[3]);
    });
    assert.strictEqual(await rows.nth(3).locator('.promo-decision-reason').first().textContent(), 'Frete atualizado após a verificação.');
    assert.strictEqual(await rows.nth(3).locator('.promo-p2-status').textContent(), 'P2: Já participa');
    await page.evaluate(() => {
      window.exported = null;
      window.originalExport = exportarAnaliseApi;
      exportarAnaliseApi = async (index, data) => { window.exported = [index, data.length, data[1]['Ação'], data[1].lightning_stock]; };
    });
    await page.getByRole('button', { name: 'Baixar planilha', exact: true }).click();
    assert.deepStrictEqual(await page.evaluate(() => exported), [0, 4, 'Participar', 7], 'export keeps all canonical rows after filtering');
    await rows.nth(1).locator('.promo-ficha').click();
    assert.strictEqual(await page.locator('#promoDetailsDialog').isVisible(), true);
    assert.strictEqual(await page.locator('#promoDetailsFields dt').count(), 29);
    await page.locator('#promoDetailsClose').click();
    await page.locator('#promoDensity').selectOption('resumido');
    assert.strictEqual(await rows.nth(1).locator('.promo-full').first().isVisible(), false);
    await page.locator('#promoDensity').selectOption('completo');
    await page.locator('#promoDifferences').uncheck();
    assert.strictEqual(await rows.nth(1).locator('.promo-delta').first().isVisible(), false);
    await page.locator('#promoDifferences').check();
    await page.evaluate(() => { saveColumnWidthPrefsFromDom(); saveColumnWidthPrefsFromDomRobust(true); });
    await page.waitForTimeout(180);
    assert.strictEqual(await page.evaluate(() => localStorage.getItem(getColumnWidthPrefsKey()) === initialWidthPrefs), true, 'hidden table must not erase saved widths');
    await page.getByRole('button', { name: 'Planilha', exact: true }).click();
    assert.strictEqual(await page.locator('#promoLegacyTableWrap').isVisible(), true);
    assert.strictEqual(await page.locator('#tabelaAnalise tbody tr').nth(1).locator('.decision-select').inputValue(), 'Participar');
    await page.evaluate(() => { setColumnVisible('Ação', false); setColumnVisible('Campanha ML', false); });
    const preferences = await page.evaluate(() => JSON.stringify(columnState));
    await page.getByRole('button', { name: 'Comparar campanhas', exact: true }).click();
    assert.strictEqual(await rows.nth(1).locator('.decision-select').count(), 1, 'compact action is available even if hidden in spreadsheet preferences');
    assert.strictEqual(await rows.nth(1).locator('.promo-lightning-stock input').inputValue(), '7');
    assert.strictEqual(await page.evaluate(() => JSON.stringify(columnState)), preferences, 'compact mode must not change spreadsheet visibility');
    await page.locator('#promoSearch').fill('');
    await page.locator('#promoDecisionFilter').selectOption('nao');
    assert.strictEqual(await page.locator('.promo-comparison-row:visible').count(), 1);
    await page.locator('#promoDecisionFilter').selectOption('todos');
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'mobile must have no horizontal overflow');
    assert.strictEqual(await rows.nth(0).locator('.promo-value-label').first().isVisible(), true);
    assert.strictEqual(await page.locator('#apiPromoBSelect .api-promo-card').count(), 1);
    assert.strictEqual(await page.locator('section.promo-campaign-picker').isVisible(), true, 'Campaign panel is permanent on mobile');
    assert.strictEqual(await page.locator('#apiPromoBSelect').isVisible(), true);
    const campaignGeometry = await page.locator('section.promo-campaign-picker').evaluate(panel => {
      const rect = panel.getBoundingClientRect(), setup = document.getElementById('painelApi').getBoundingClientRect();
      return { top: rect.top, left: rect.left, right: rect.right, setupBottom: setup.bottom, setupLeft: setup.left, setupRight: setup.right };
    });
    assert(campaignGeometry.top >= campaignGeometry.setupBottom, 'Campaign panel remains below the filters with analysis results displayed');
    assert(Math.abs(campaignGeometry.left - campaignGeometry.setupLeft) <= 1 && Math.abs(campaignGeometry.right - campaignGeometry.setupRight) <= 1);
    const campaignSelection = await page.evaluate(() => Array.from(apiPromoBSelectedIds));
    await page.locator('h1').click();
    assert.strictEqual(await page.locator('#apiPromoBSelect').isVisible(), true, 'Outside click must not hide the permanent campaign panel');
    assert.deepStrictEqual(await page.evaluate(() => Array.from(apiPromoBSelectedIds)), campaignSelection);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Permanent campaign panel must fit mobile');
    await page.setViewportSize({ width: 1306, height: 850 });
    if (process.env.JK_PROMO_PREVIEW_DIR) {
      await page.evaluate(() => {
        document.querySelector('.promo-page-subtitle').textContent = 'Validação do layout · dados ilustrativos';
        exportarAnaliseApi = originalExport;
      });
      await page.screenshot({ path: path.join(process.env.JK_PROMO_PREVIEW_DIR, 'promocoes-implementacao.png') });
      await page.setViewportSize({ width: 1306, height: 653 });
      await page.screenshot({ path: path.join(process.env.JK_PROMO_PREVIEW_DIR, 'promocoes-implementacao-653.png') });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({ path: path.join(process.env.JK_PROMO_PREVIEW_DIR, 'promocoes-implementacao-mobile.png') });
    }
    assert.deepStrictEqual(errors, []);
    assert.deepStrictEqual(unexpectedRequests, [], 'browser validation must not call live APIs');
    console.log('Comparação: fotos, fallback seguro, cores de decisão e margem, limites e atualização financeira, dados preservados, controles por oferta, filtros, exportação e responsividade aprovados.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
