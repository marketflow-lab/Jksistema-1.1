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
    let liberarRespostaTodas;
    let registrarRequisicaoTodas;
    let registrarRespostaTodasEntregue;
    const respostaTodasPodeSeguir = new Promise((resolve) => { liberarRespostaTodas = resolve; });
    const requisicaoTodasRecebida = new Promise((resolve) => { registrarRequisicaoTodas = resolve; });
    const respostaTodasEntregue = new Promise((resolve) => { registrarRespostaTodasEntregue = resolve; });
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
        const loja = url.searchParams.get('loja');
        const ehTodasAsLojas = !loja;
        if (ehTodasAsLojas) {
          registrarRequisicaoTodas();
          await respostaTodasPodeSeguir;
        }
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
                estoque_em_transito: ehTodasAsLojas ? 250 : 9,
                estoque_em_transito_listas: ehTodasAsLojas
                  ? [
                    { lista_id: 'jk-48', nome_lista: 'JK 48', quantidade: 100 },
                    { lista_id: 'uai-45', nome_lista: 'UAI 45', quantidade: 100 },
                    { lista_id: 'uai-53', nome_lista: 'UAI 53', quantidade: 50 },
                  ]
                  : [
                    { lista_id: 'a', nome_lista: 'Importacao A', quantidade: 5 },
                    { lista_id: 'b', nome_lista: 'Importacao B', quantidade: 4 },
                  ],
                posicao_estoque: ehTodasAsLojas ? 250 : 9,
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
        if (ehTodasAsLojas) registrarRespostaTodasEntregue();
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
    await requisicaoTodasRecebida;
    const respostaJk = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === '/api/medias-compras/visao' && url.searchParams.get('loja') === 'JK Pecas';
    });
    await page.getByRole('button', { name: /JK Pecas/i }).click();
    await respostaJk;

    const somas = page.locator('.transito-quantidade--soma');
    await somas.first().waitFor();
    liberarRespostaTodas();
    await respostaTodasEntregue;
    await page.waitForTimeout(100);
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

    const alvoAntesRender = await somas.first().elementHandle();
    await page.evaluate(() => atualizarPesquisaSku('SEM-RESULTADO'));
    await page.waitForFunction(() => document.getElementById('balaoEstoqueEmTransito').getAttribute('aria-hidden') === 'true');
    assert.strictEqual(
      await alvoAntesRender.evaluate((alvo) => alvo.isConnected),
      false,
      'O rerender deve substituir a linha que abriu o balao',
    );
    assert.strictEqual(await somas.count(), 0);

    await page.evaluate(() => atualizarPesquisaSku(''));
    await somas.first().waitFor();
    await somas.first().hover();
    await balao.waitFor({ state: 'visible' });
    await page.evaluate(() => window.dispatchEvent(new Event('scroll')));
    await page.waitForFunction(() => document.getElementById('balaoEstoqueEmTransito').getAttribute('aria-hidden') === 'true');

    await page.mouse.move(5, 5);
    await somas.first().hover();
    await balao.waitFor({ state: 'visible' });
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.waitForFunction(() => document.getElementById('balaoEstoqueEmTransito').getAttribute('aria-hidden') === 'true');

    await somas.first().focus();
    await balao.waitFor({ state: 'visible' });
    assert.strictEqual(await balao.getAttribute('aria-hidden'), 'false');
    await page.evaluate(() => window.dispatchEvent(new Event('blur')));
    await page.waitForFunction(() => document.getElementById('balaoEstoqueEmTransito').getAttribute('aria-hidden') === 'true');

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
