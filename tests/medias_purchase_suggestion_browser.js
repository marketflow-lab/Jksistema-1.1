'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'medias_compras.html'), 'utf8');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    page.on('pageerror', error => console.error('[pageerror]', error.message));
    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/medias_compras.html') {
        await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html });
        return;
      }
      if (url.pathname.startsWith('/medias_compras/')) {
        const relativePath = url.pathname.replace(/^\//, '');
        const body = fs.readFileSync(path.join(root, 'static', relativePath));
        const contentType = relativePath.endsWith('.css') ? 'text/css' : 'application/javascript';
        await route.fulfill({ status: 200, contentType, body });
        return;
      }
      if (url.pathname === '/api/lojas') {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ nome: 'JK Pecas', store_id: 'store-jk' }]) });
        return;
      }
      if (url.pathname === '/api/medias-compras/preferencias-skus-ocultos') {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ skus_ocultos: [] }) });
        return;
      }
      if (url.pathname === '/api/medias-compras/visao') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            colunas_meses: [{ key: '2026-07' }],
            itens: [{
              sku: '001',
              titulo_anuncio: 'Produto de teste',
              vendas_mensais: { '2026-07': 10 },
              total_vendas_periodo: 10,
              media_mensal: 10,
              saldo_atual_estoque: 5,
              estoque_em_transito: 0,
              posicao_estoque: 5,
              compra_sugerida: 55,
            }],
          }),
        });
        return;
      }
      if (url.pathname === '/auth.js') {
        await route.fulfill({
          status: 200,
          contentType: 'application/javascript',
          body: 'window.verificarSessao = () => true; window.obterAuthHeaders = () => ({});',
        });
        return;
      }
      await route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    });

    await page.goto('http://jk.test/medias_compras.html');
    const botao = page.locator('.compra-sugerida-editavel').first();
    await botao.waitFor();
    assert.strictEqual((await botao.textContent()).trim(), '55');

    await botao.click();
    const input = page.locator('.compra-sugerida-input');
    await input.fill('165');
    await input.press('Enter');
    assert.strictEqual((await page.locator('.compra-sugerida-editavel').first().textContent()).trim(), '165');
    assert.deepStrictEqual(
      await page.evaluate(() => obterQuantidadesSugeridasEditadas()),
      { '001': 165 },
    );

    await page.locator('.compra-sugerida-editavel').first().click();
    await page.locator('.compra-sugerida-input').fill('22');
    await page.locator('.compra-sugerida-input').press('Escape');
    assert.strictEqual((await page.locator('.compra-sugerida-editavel').first().textContent()).trim(), '165');

    await page.locator('.compra-sugerida-editavel').first().click();
    await page.locator('.compra-sugerida-input').fill('0');
    await page.locator('.compra-sugerida-input').press('Enter');
    assert.strictEqual((await page.locator('.compra-sugerida-editavel').first().textContent()).trim(), '0');
    assert.deepStrictEqual(
      await page.evaluate(() => obterQuantidadesSugeridasEditadas()),
      { '001': 0 },
    );

    console.log('medias purchase suggestion browser: OK');
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
