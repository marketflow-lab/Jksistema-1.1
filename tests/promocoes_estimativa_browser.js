'use strict';

const assert = require('assert');
const path = require('path');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1100, height: 700 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://promo.test/**', route => route.fulfill({ contentType: 'text/html', body: `
      <!doctype html><html lang="pt-BR"><body>
      <span id="totalAnuncios"></span><span id="totalParticipar"></span><span id="totalNaoParticipar"></span>
      <table id="tabelaAnalise"><thead><tr></tr></thead><tbody></tbody></table>
      </body></html>` }));
    await page.goto('http://promo.test/');
    await page.addStyleTag({ path: path.join(__dirname, '..', 'static/frontend_promo/styles.css') });
    for (const file of ['runtime.js', 'tabela-preferencias.js', 'arquivos-render.js']) {
      await page.addScriptTag({ path: path.join(__dirname, '..', 'static/frontend_promo', file) });
    }
    await page.evaluate(() => {
      window.parsePercentValue = value => value ? Number(String(value).replace('%', '').replace(',', '.')) : null;
      window.getVisibleColumns = () => [
        { key: 'MLB', label: 'MLB' }, { key: 'Tarifa ML', label: 'Tarifa ML' },
        { key: 'Valor líquido ML', label: 'Valor líquido ML' }, { key: 'Margem ML', label: 'Margem ML' },
        { key: 'Ação', label: 'Ação', editable: true },
      ];
      for (const name of ['applyColumnWidthPrefsToDom', 'bindPromoMinimizeOnDblClick', 'agendarSalvarColumnWidthPrefsServidor']) {
        window[name] = () => {};
      }
      const motivo = 'Tarifa-base na campanha; coparticipação desconhecida. <img src=x onerror="window.injected=true">';
      currentData = [
        { MLB: 'MLB123', 'Tarifa ML': 'R$ 4,04', 'Valor líquido ML': 'R$ 4,28', 'Margem ML': '28,01%',
          'Ação': 'Não participar', _jk_tarifa_ml_estimada: true, _jk_tarifa_ml_estimativa_motivo: motivo,
          action_financeiro_estimado: true, action_financeiro_estimativa_motivo: motivo,
          action_financeiro_exato: false, action_financeiro_motivo: 'Margem estimada para revisão.' },
        { MLB: 'MLB456', 'Tarifa ML': 'R$ 3,00', 'Valor líquido ML': 'R$ 5,32', 'Margem ML': '34,82%',
          'Ação': 'Participar', _jk_tarifa_ml_estimada: false, action_financeiro_estimado: false,
          action_financeiro_exato: true },
        { MLB: 'MLB789', 'Tarifa ML': 'R$ 0,00', 'Valor líquido ML': '', 'Margem ML': '',
          'Ação': 'Não participar', _jk_tarifa_ml_estimada: true, _jk_tarifa_ml_estimativa_motivo: ' ',
          action_financeiro_estimado: false },
        { MLB: 'MLB987', 'Tarifa ML': 'R$ 3,00', 'Valor líquido ML': 'R$ 5,32', 'Margem ML': '34,82%',
          'Ação': 'Participar', _jk_tarifa_ml_estimada: 'false', action_financeiro_estimado: 'false' },
      ];
      window.originalRows = JSON.stringify(currentData);
      renderTable(currentData);
    });
    const rows = page.locator('#tabelaAnalise tbody tr');
    const estimated = rows.nth(0).locator('.promo-estimate-badge');
    assert.strictEqual(await estimated.count(), 3, 'tariff and derived financial results must be marked');
    for (let index = 0; index < 3; index++) {
      assert.strictEqual(await estimated.nth(index).textContent(), 'Estimado');
      assert.strictEqual(await estimated.nth(index).isVisible(), true);
      assert((await estimated.nth(index).getAttribute('title')).includes('coparticipação desconhecida'));
    }
    assert.strictEqual(await estimated.first().evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(246, 207, 101)');
    assert.strictEqual(await estimated.first().evaluate(el => getComputedStyle(el).color), 'rgb(52, 37, 0)');
    assert((await estimated.first().getAttribute('aria-label')).includes('Tarifa ML: R$ 4,04. Estimado.'));
    assert.strictEqual(await rows.nth(0).locator('img').count(), 0, 'source reasons must remain safe text');
    assert.strictEqual(await page.evaluate(() => window.injected), undefined);
    assert((await estimated.first().getAttribute('title')).includes('<img src=x'));
    assert((await rows.nth(0).locator('td').nth(3).getAttribute('class')).includes('margin-top'),
      'numeric margin coloring must use the original value');
    assert.strictEqual(await rows.nth(1).locator('.promo-estimate-badge').count(), 0, 'exact values have no badge');
    assert.strictEqual(await rows.nth(1).locator('td').nth(1).textContent(), 'R$ 3,00');
    assert.strictEqual(await rows.nth(2).locator('.promo-estimate-badge').count(), 1,
      'a zero tariff is valid, but missing dependent financial values must remain blank');
    assert((await rows.nth(2).locator('.promo-estimate-badge').getAttribute('title')).includes('Tarifa aproximada'),
      'missing or whitespace reasons must have an explanation');
    assert.strictEqual(await rows.nth(2).locator('td').nth(2).textContent(), '');
    assert.strictEqual(await rows.nth(3).locator('.promo-estimate-badge').count(), 0, 'string false must not enable estimation');
    assert.strictEqual(await page.evaluate(() => JSON.stringify(currentData) === originalRows), true,
      'rendering must preserve raw numeric values and backend decisions');
    assert.strictEqual(await page.evaluate(() => parsePercentValue(getTableCellValue(currentData[0], 'Margem ML'))), 28.01);
    await page.addStyleTag({ content: `
      #tabelaAnalise { table-layout: fixed; width: 325px; }
      #tabelaAnalise th, #tabelaAnalise td { width: 65px; min-width: 65px; max-width: 65px; box-sizing: border-box; }
    ` });
    const narrowBadges = await estimated.evaluateAll(badges => badges.map(badge => {
      const cell = badge.parentElement.getBoundingClientRect();
      const rect = badge.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(badge);
      const text = range.getBoundingClientRect();
      return { cellWidth: cell.width, badgeFits: rect.left >= cell.left && rect.right <= cell.right,
        textFits: text.left >= cell.left && text.right <= cell.right };
    }));
    for (const narrow of narrowBadges) {
      assert.strictEqual(narrow.cellWidth, 65, 'exercise the effective width, including cell padding');
      assert(narrow.badgeFits && narrow.textFits, 'the full Estimado badge must remain visible in a 65 px cell');
    }
    await rows.nth(0).locator('select').selectOption('Participar');
    await page.evaluate(() => renderTable(currentData));
    assert.strictEqual(await rows.nth(0).locator('select').inputValue(), 'Participar', 'manual decisions survive rerender');
    assert.strictEqual(await rows.nth(0).locator('.promo-estimate-badge').count(), 3, 'rerender must retain exactly one badge per value');
    await page.evaluate(() => {
      currentData[0]._jk_tarifa_ml_estimada = false;
      currentData[0].action_financeiro_estimado = false;
      currentData[0].action_financeiro_exato = true;
      renderTable(currentData);
    });
    assert.strictEqual(await rows.nth(0).locator('.promo-estimate-badge').count(), 0, 'confirmed data clears stale badges');
    assert.strictEqual(await rows.nth(0).locator('td').nth(1).getAttribute('title'), null, 'confirmed data clears stale tooltips');
    assert.deepStrictEqual(errors, []);
    console.log('promocoes estimate browser checks passed');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
