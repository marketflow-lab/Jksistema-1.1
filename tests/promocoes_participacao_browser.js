'use strict';

const assert = require('assert');
const path = require('path');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setContent('<!doctype html><html lang="pt-BR"><body></body></html>');
    await page.addStyleTag({ path: path.join(__dirname, '..', 'static/frontend_promo/styles.css') });
    await page.evaluate(() => {
      window.escapeHtml = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
      window.TABLE_COLUMNS_MODERN = [];
      window.TABLE_COLUMN_ALIASES = {};
    });
    for (const file of ['participacao.js', 'tabela-preferencias.js']) {
      await page.addScriptTag({ path: path.join(__dirname, '..', 'static/frontend_promo', file) });
    }
    await page.evaluate(() => {
      const items = Array.from({ length: 351 }, (_, i) => ({ item_id: `MLB${i + 100}`, client_ref: `0:${i}` }));
      mostrarResumoParticipacaoPromocoes(
        { promocoes: [{ promotion_id: 'P-TESTE', nome: '<img src=x onerror=alert(1)>', items }] },
        { detalhes: items.map(item => ({ ...item, promotion_id: 'P-TESTE', success: true, outcome: 'applied' })) },
      );
    });
    const list = page.locator('[data-result-list="0-confirmados"]');
    assert.strictEqual(await list.locator('.promo-result-item').count(), 50);
    assert.strictEqual(await page.locator('.promo-result-campaign-title img').count(), 0);
    for (let i = 1; i <= 7; i++) await list.getByRole('button', { name: 'Próxima', exact: true }).click();
    assert.strictEqual(await list.locator('.promo-result-item').count(), 1);
    assert.strictEqual(await list.locator('.promo-result-item-id').textContent(), 'MLB450');
    assert.strictEqual(await list.getByRole('button', { name: 'Próxima', exact: true }).isDisabled(), true);
    await list.getByRole('button', { name: 'Anterior', exact: true }).click();
    assert.strictEqual(await list.locator('.promo-result-item').count(), 50);
    await page.getByRole('button', { name: 'OK', exact: true }).click();
    assert.strictEqual(await page.locator('.promo-result-modal-backdrop').count(), 0);

    await page.evaluate(() => {
      window.parsePromoCount = raw => raw === undefined ? null : Number(raw);
      window.resolvePromoActiveCount = () => 261;
      window.resolvePromoEligibleCount = () => 65;
      window.formatPromoDateRange = () => '-';
      window.confirmation = pedirConfirmacaoParticipacaoCampanhaApi({
        nome: 'Campanha Teste', total: 236, loja: 'Loja Teste',
        promo: { promotion_id: 'P-TESTE', promotion_type: 'SMART', items: [] },
        analise: {}, campanha: {},
      });
    });
    assert.strictEqual(await page.locator('.promo-confirm-card--highlight strong').textContent(), '236');
    await page.getByRole('button', { name: 'Cancelar', exact: true }).click();
    assert.strictEqual(await page.evaluate(() => confirmation), false);
    assert.deepStrictEqual(errors, []);
    console.log('promocoes participation browser checks passed');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
