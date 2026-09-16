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
        { key: 'MLB', label: 'MLB' }, { key: 'Margem ML', label: 'Margem ML' }, { key: 'Ação', label: 'Ação', editable: true },
      ];
      for (const name of ['applyColumnWidthPrefsToDom', 'bindPromoMinimizeOnDblClick', 'agendarSalvarColumnWidthPrefsServidor']) {
        window[name] = () => {};
      }
      currentData = [
        { MLB: 'MLB123', Status: 'Ativo', 'Margem ML': '28,01%', 'Ação': 'Participar', action_financeiro_exato: true,
          action_tecnico_apto: false, action_impedimento_tecnico: 'Identificador da oferta não informado.' },
        { MLB: 'MLB456', 'Margem ML': '', 'Ação': 'Não participar', action_financeiro_exato: false,
          action_financeiro_motivo: 'Frete não confirmado para o preço da campanha selecionada.' },
        { MLB: 'MLB789', 'Margem ML': '12,00%', 'Ação': 'Não participar', action_financeiro_exato: true,
          action_impedimento_tecnico: '<img src=x onerror=alert(1)>' },
        { MLB: 'MLB987', 'Margem ML': '28,01%', 'Ação': 'Não participar', action_financeiro_exato: true,
          action_ja_participa: true, action_impedimento_tecnico: 'Situacao da oferta exige verificacao antes do envio.' },
      ];
      renderTable(currentData);
    });
    const rows = page.locator('#tabelaAnalise tbody tr');
    assert.strictEqual(await rows.nth(0).locator('td').nth(1).textContent(), '28,01%');
    assert.strictEqual(await rows.nth(0).locator('select').inputValue(), 'Participar');
    assert.strictEqual(await rows.nth(0).locator('.promo-decision-reason').first().isVisible(), false);
    assert.strictEqual(await rows.nth(0).locator('.promo-decision-technical').isVisible(), false,
      'participação na promoção de comparação não confirma a campanha selecionada');
    assert.strictEqual(await rows.nth(1).locator('.promo-decision-reason').first().textContent(), 'Frete não confirmado para o preço da campanha selecionada.');
    assert.strictEqual(await rows.nth(2).locator('img').count(), 0, 'reasons must be plain text');
    assert.strictEqual(await rows.nth(2).locator('.promo-decision-technical').isVisible(), false);
    assert.strictEqual(await rows.nth(3).locator('.promo-decision-technical').textContent(), 'Já participa');
    assert.strictEqual(await rows.nth(3).locator('.promo-decision-technical').isVisible(), true);
    assert(!(await page.locator('#tabelaAnalise').innerText()).includes('Pendência de envio'));
    await rows.nth(0).locator('select').selectOption('Não participar');
    assert.strictEqual(await page.locator('#totalParticipar').textContent(), '0');
    await rows.nth(0).locator('select').selectOption('Participar');
    assert.strictEqual(await page.locator('#totalParticipar').textContent(), '1');
    assert.strictEqual(await rows.nth(0).locator('.promo-decision-technical').isVisible(), false);
    await page.evaluate(() => renderTable(currentData));
    assert.strictEqual(await rows.nth(0).locator('select').inputValue(), 'Participar');
    assert.strictEqual(await rows.nth(0).locator('.promo-decision-technical').isVisible(), false);
    assert.strictEqual(await rows.nth(3).locator('.promo-decision-technical').textContent(), 'Já participa');
    assert.deepStrictEqual(errors, []);
    console.log('promocoes margin browser checks passed');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
