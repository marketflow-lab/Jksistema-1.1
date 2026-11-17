'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'medias_compras.html'), 'utf8');
const meses = ['2026-05', '2026-06', '2026-07', '2026-08', '2026-09', '2026-10'];
const itemBase = {
  titulo_anuncio: 'Sensor Ford Ranger 3.0 Troller T4', media_mensal: 11.83,
  saldo_atual_estoque: 71, estoque_em_transito: 0, posicao_estoque: 71,
  compra_sugerida: 12, planejamento_versao: 1, media_mensal_prevista: 11.83,
  tendencia_vendas: 'alta', variacao_vendas_percentual: 20,
  estoque_previsto_antes_chegada: 0, estoque_previsto_apos_chegada: 12,
  reserva_antes_chegada_quantidade: 11.83, reserva_antes_chegada_atendida: false,
  estoque_alvo_apos_chegada: 82.81, risco_ruptura_antes_chegada: false,
  planejamento_requer_revisao: false, avisos_planejamento: [],
};
const itens = [
  { ...itemBase, sku: '001', total_vendas_periodo: 62,
    vendas_mensais: Object.fromEntries(meses.map((mes, index) => [mes, [1, 0, 0, 14, 29, 18][index]])) },
  { ...itemBase, sku: '002', total_vendas_periodo: 1232.25,
    vendas_mensais: Object.fromEntries(meses.map((mes, index) => [mes, [0, 0, 0, 1234.5, -2.25, 0][index]])) },
];

async function selecionar(page, inicio, fim = inicio, invertida = false) {
  await page.locator(invertida ? inicio : fim).scrollIntoViewIfNeeded();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.evaluate(({ inicio, fim, invertida }) => {
    const primeiro = document.querySelector(inicio);
    const ultimo = document.querySelector(fim);
    const selecao = getSelection();
    selecao.removeAllRanges();
    if (invertida) {
      selecao.setBaseAndExtent(ultimo, ultimo.childNodes.length, primeiro, 0);
    } else {
      const range = document.createRange();
      range.setStart(primeiro, 0);
      range.setEnd(ultimo, ultimo.childNodes.length);
      selecao.addRange(range);
    }
  }, { inicio, fim, invertida });
}

async function esperarSoma(page, valor) {
  await page.waitForFunction(valorEsperado => {
    const balao = document.getElementById('balaoSomaSelecao');
    return balao && !balao.hidden && new RegExp('Soma:\\s*' + valorEsperado.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?:\\s|$)').test(balao.textContent);
  }, valor);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/medias_compras.html') {
        return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
      }
      if (url.pathname.startsWith('/medias_compras/')) {
        const relativePath = url.pathname.replace(/^\//, '');
        return route.fulfill({
          contentType: relativePath.endsWith('.css') ? 'text/css' : 'application/javascript',
          body: fs.readFileSync(path.join(root, 'static', relativePath)),
        });
      }
      if (url.pathname === '/api/lojas') return route.fulfill({ json: [{ nome: 'Loja simulada', store_id: 'simulado' }] });
      if (url.pathname === '/api/medias-compras/preferencias-skus-ocultos') return route.fulfill({ json: { skus_ocultos: [] } });
      if (url.pathname === '/api/medias-compras/visao') return route.fulfill({ json: { colunas_meses: meses.map(key => ({ key })), itens } });
      if (url.pathname === '/auth.js') {
        return route.fulfill({ contentType: 'application/javascript', body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({});' });
      }
      return route.fulfill({ contentType: 'application/javascript', body: '' });
    });
    await page.goto('http://jk.test/medias_compras.html');
    const primeira = '#tblResultado tbody tr:has(.compra-sugerida-editavel[data-sku="001"])';
    const segunda = '#tblResultado tbody tr:has(.compra-sugerida-editavel[data-sku="002"])';
    const celula = (linha, indice) => `${linha} td:nth-child(${indice + 1})`;
    const mes14 = celula(primeira, 6);
    const mes29 = celula(primeira, 7);
    const mes18 = celula(primeira, 8);
    const balao = page.locator('#balaoSomaSelecao');
    await page.locator(mes14).waitFor();

    // Use the same native mouse drag as the supplied screenshot.
    const coordenadas = await page.evaluate(({ inicio, fim }) => {
      const rectTexto = seletor => {
        const range = document.createRange();
        range.selectNodeContents(document.querySelector(seletor));
        const rect = range.getBoundingClientRect();
        return { left: rect.left, right: rect.right, y: rect.top + rect.height / 2 };
      };
      return { primeiro: rectTexto(inicio), ultimo: rectTexto(fim) };
    }, { inicio: mes14, fim: mes18 });
    await page.mouse.move(coordenadas.primeiro.left - 1, coordenadas.primeiro.y);
    await page.mouse.down();
    await page.mouse.move(coordenadas.ultimo.right + 1, coordenadas.ultimo.y, { steps: 12 });
    await page.mouse.up();
    await esperarSoma(page, '61');
    assert.strictEqual(await balao.getAttribute('role'), 'status');
    assert(await balao.evaluate(el => el.parentElement === document.body), 'O balão deve escapar do recorte da tabela');
    assert(await balao.evaluate(el => getComputedStyle(el).pointerEvents === 'none'), 'O balão não pode interromper a seleção');
    assert(await balao.evaluate(el => {
      const rect = el.getBoundingClientRect();
      return rect.left >= 0 && rect.top >= 0 && rect.right <= innerWidth && rect.bottom <= innerHeight;
    }), 'O balão deve caber na janela');
    const pastaResultado = path.join(root, 'test-results');
    fs.mkdirSync(pastaResultado, { recursive: true });
    await page.screenshot({ path: path.join(pastaResultado, 'medias-soma-selecao.png') });

    await selecionar(page, mes14, mes18, true);
    await esperarSoma(page, '61');
    await selecionar(page, celula(segunda, 6), celula(segunda, 8));
    await esperarSoma(page, '1.232,25');
    await selecionar(page, celula(segunda, 8));
    await esperarSoma(page, '0');
    await page.evaluate(({ linha }) => {
      const celulas = document.querySelector(linha).cells;
      ['0,3', '-0,2', '-0,1'].forEach((valor, indice) => { celulas[6 + indice].textContent = valor; });
    }, { linha: segunda });
    await selecionar(page, celula(segunda, 6), celula(segunda, 8));
    await esperarSoma(page, '0');

    // Numeric SKU and numbers in product titles must not join the calculation.
    await selecionar(page, celula(primeira, 0), mes18);
    await esperarSoma(page, '62');
    await selecionar(page, celula(primeira, 0), celula(primeira, 2));
    await balao.waitFor({ state: 'hidden' });

    // VMM and suggestion cells include secondary forecast/trend text.
    await selecionar(page, celula(primeira, 10));
    await esperarSoma(page, '11,83');
    await selecionar(page, `${primeira} .previsao-detalhe`);
    await balao.waitFor({ state: 'hidden' });
    await selecionar(page, `${primeira} .compra-sugerida-cell`);
    await esperarSoma(page, '12');
    await selecionar(page, `${primeira} .previsao-tendencia`);
    await balao.waitFor({ state: 'hidden' });

    // A range ending at the next cell's first character does not select it.
    await page.locator(mes14).scrollIntoViewIfNeeded();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.evaluate(({ inicio, fim }) => {
      const range = document.createRange();
      range.setStart(document.querySelector(inicio).firstChild, 0);
      range.setEnd(document.querySelector(fim).firstChild, 0);
      getSelection().removeAllRanges();
      getSelection().addRange(range);
    }, { inicio: mes14, fim: mes29 });
    await esperarSoma(page, '14');

    await page.keyboard.press('Escape');
    await balao.waitFor({ state: 'hidden' });
    await selecionar(page, mes14, mes18);
    await esperarSoma(page, '61');
    await page.evaluate(() => getSelection().removeAllRanges());
    await balao.waitFor({ state: 'hidden' });
    await selecionar(page, mes14, mes18);
    await esperarSoma(page, '61');
    await page.evaluate(() => renderAbaAtual());
    await balao.waitFor({ state: 'hidden' });

    await page.locator(`${primeira} .compra-sugerida-editavel`).click();
    const input = page.locator('#tblResultado tbody .compra-sugerida-input');
    await input.waitFor();
    await input.evaluate(el => el.select());
    await page.waitForTimeout(60);
    assert.strictEqual(await balao.isVisible(), false, 'Selecionar o campo em edição não deve abrir o balão');
    assert.deepStrictEqual(errors, [], 'A página não deve emitir erros de JavaScript');
    console.log('medias soma selecao browser: OK');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
