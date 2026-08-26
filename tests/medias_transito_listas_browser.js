'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'medias_compras.html'), 'utf8');
const staticHtml = fs.readFileSync(path.join(root, 'static', 'medias_compras.html'), 'utf8');

assert.strictEqual(html, staticHtml, 'As copias root/static de medias_compras.html devem ser identicas');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1365, height: 768 } });
    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/medias_compras.html') {
        await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html });
        return;
      }
      if (url.pathname === '/api/lojas') {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ nome: 'JK Pecas' }]) });
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
            colunas_meses: [{ key: '2026-08' }],
            itens: [
              {
                sku: '001',
                titulo_anuncio: 'Produto com duas importacoes',
                vendas_mensais: { '2026-08': 1 },
                total_vendas_periodo: 1,
                media_mensal: 1,
                saldo_atual_estoque: 0,
                estoque_em_transito: 9,
                estoque_em_transito_listas: [
                  { lista_id: 'a', nome_lista: 'Importacao A', quantidade: 5 },
                  { lista_id: 'b', nome_lista: 'Importacao B', quantidade: 4 },
                ],
                posicao_estoque: 9,
                compra_sugerida: 0,
              },
              {
                sku: '002',
                titulo_anuncio: 'Produto com uma importacao',
                vendas_mensais: { '2026-08': 1 },
                total_vendas_periodo: 1,
                media_mensal: 1,
                saldo_atual_estoque: 0,
                estoque_em_transito: 3,
                estoque_em_transito_listas: [
                  { lista_id: 'c', nome_lista: 'Importacao unica', quantidade: 3 },
                ],
                posicao_estoque: 3,
                compra_sugerida: 0,
              },
            ],
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

    const somas = page.locator('.transito-quantidade--soma');
    await somas.first().waitFor();
    assert.strictEqual(await somas.count(), 1, 'Somente o total composto por mais de uma lista deve abrir balao');
    assert.strictEqual((await somas.first().textContent()).trim(), '9');
    assert.strictEqual(
      await somas.first().getAttribute('aria-label'),
      'Em trânsito: 9. Importacao A: 5; Importacao B: 4',
    );

    await somas.first().hover();
    const balao = page.locator('#balaoEstoqueEmTransito');
    await balao.waitFor({ state: 'visible' });
    const textoBalao = (await balao.textContent()).replace(/\s+/g, ' ').trim();
    assert(textoBalao.includes('Em trânsito'));
    assert(textoBalao.includes('Total 9'));
    assert(textoBalao.includes('Importacao A5'));
    assert(textoBalao.includes('Importacao B4'));
    const caixaBalao = await balao.boundingBox();
    assert(caixaBalao && caixaBalao.x >= 0 && caixaBalao.y >= 0, 'O balao deve ficar dentro da viewport');
    assert(caixaBalao.x + caixaBalao.width <= 1365, 'O balao nao deve ultrapassar a largura da viewport');
    assert(caixaBalao.y + caixaBalao.height <= 768, 'O balao nao deve ultrapassar a altura da viewport');

    await page.mouse.move(5, 5);
    await page.waitForFunction(() => document.getElementById('balaoEstoqueEmTransito').getAttribute('aria-hidden') === 'true');
    await somas.first().focus();
    await balao.waitFor({ state: 'visible' });
    assert.strictEqual(await balao.getAttribute('aria-hidden'), 'false');

    const quantidades = page.locator('.transito-quantidade');
    assert.strictEqual((await quantidades.nth(1).textContent()).trim(), '3');
    assert.strictEqual(await quantidades.nth(1).getAttribute('tabindex'), null);

    console.log('medias transit lists browser: OK');
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
