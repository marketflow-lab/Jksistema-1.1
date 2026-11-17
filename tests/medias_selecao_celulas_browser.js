'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'medias_compras.html'), 'utf8');
const meses = ['2026-05', '2026-06', '2026-07', '2026-08', '2026-09', '2026-10'];
const itens = Array.from({ length: 100 }, (_, indice) => {
  const vendas = indice === 0 ? [1, 0, 0, 14, 29, 18] : indice === 1 ? [0, 0, 0, 2, 3, 4] : [0, 0, 0, 1, 2, 3];
  return {
    sku: String(indice + 1).padStart(3, '0'),
    titulo_anuncio: 'Sensor Ford Ranger 3.0 Troller T4',
    vendas_mensais: Object.fromEntries(meses.map((mes, coluna) => [mes, vendas[coluna]])),
    total_vendas_periodo: vendas.reduce((soma, valor) => soma + valor, 0),
    media_mensal: 11.83, saldo_atual_estoque: 71, estoque_em_transito: 0,
    posicao_estoque: 71, compra_sugerida: 12, planejamento_versao: 1,
    media_mensal_prevista: 11.83, tendencia_vendas: 'alta', variacao_vendas_percentual: 20,
    estoque_previsto_antes_chegada: 0, estoque_previsto_apos_chegada: 12,
    reserva_antes_chegada_quantidade: 11.83, reserva_antes_chegada_atendida: false,
    estoque_alvo_apos_chegada: 82.81, risco_ruptura_antes_chegada: false,
    planejamento_requer_revisao: false, avisos_planejamento: [],
  };
});

function seletorCelula(linha, coluna) {
  return `#tblResultado tbody tr[data-virtual-index="${linha}"] td:nth-child(${coluna + 1})`;
}

async function esperarSoma(page, valor) {
  await page.waitForFunction(esperado => {
    const balao = document.getElementById('balaoSomaSelecao');
    return balao && !balao.hidden && balao.textContent.includes('Soma: ' + esperado + ' ');
  }, valor, { timeout: 5000 });
}

async function arrastar(page, linhaInicio, colunaInicio, linhaFim, colunaFim) {
  const inicio = page.locator(seletorCelula(linhaInicio, colunaInicio));
  const fim = page.locator(seletorCelula(linhaFim, colunaFim));
  const a = await inicio.boundingBox();
  const b = await fim.boundingBox();
  assert(a && b, 'As células do arrasto devem estar visíveis');
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 });
  await page.mouse.up();
}

async function copiar(page) {
  return page.evaluate(() => {
    const dados = {};
    const evento = new Event('copy', { bubbles: true, cancelable: true });
    Object.defineProperty(evento, 'clipboardData', { value: { setData(tipo, texto) { dados[tipo] = texto; } } });
    const alvo = document.activeElement?.closest('#tblResultado') ? document.activeElement : document.getElementById('tblResultado');
    alvo.dispatchEvent(evento);
    return dados['text/plain'];
  });
}

async function aguardarFrames(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
    page.setDefaultTimeout(5000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/medias_compras.html') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
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
      if (url.pathname === '/auth.js') return route.fulfill({ contentType: 'application/javascript', body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({});' });
      return route.fulfill({ contentType: 'application/javascript', body: '' });
    });
    await page.goto('http://jk.test/medias_compras.html');
    const balao = page.locator('#balaoSomaSelecao');
    const selecionadas = page.locator('#tblResultado .celula-selecionada');
    await page.locator(seletorCelula(0, 6)).waitFor();

    await arrastar(page, 0, 6, 0, 8);
    await esperarSoma(page, '61');
    assert.strictEqual(await selecionadas.count(), 3);
    assert.strictEqual(await page.evaluate(() => getSelection().toString()), '', 'O arrasto deve selecionar células inteiras');
    assert.strictEqual(await copiar(page), '14\t29\t18');

    await arrastar(page, 0, 6, 1, 8);
    await esperarSoma(page, '70');
    assert.strictEqual(await selecionadas.count(), 6, 'O arrasto deve formar um retângulo de duas linhas e três colunas');
    assert.strictEqual(await copiar(page), '14\t29\t18\n2\t3\t4');
    const clip = await page.evaluate(() => {
      const tabela = document.getElementById('secaoTabelaPrincipal').getBoundingClientRect();
      const soma = document.getElementById('balaoSomaSelecao').getBoundingClientRect();
      const x = Math.max(0, Math.min(tabela.left, soma.left) - 5);
      const y = Math.max(0, Math.min(tabela.top, soma.top) - 5);
      return { x, y, width: Math.min(innerWidth, Math.max(tabela.right, soma.right) + 5) - x,
        height: Math.min(innerHeight, Math.max(tabela.bottom, soma.bottom) + 5) - y };
    });
    fs.mkdirSync(path.join(root, 'test-results'), { recursive: true });
    await page.screenshot({ path: path.join(root, 'test-results', 'medias-selecao-excel.png'), clip });

    await page.locator(seletorCelula(0, 6)).click();
    await page.locator(seletorCelula(1, 8)).click({ modifiers: ['Shift'] });
    await esperarSoma(page, '70');
    assert.strictEqual(await selecionadas.count(), 6, 'Shift + clique deve estender a seleção a partir da âncora');
    await page.locator(seletorCelula(0, 6)).click({ modifiers: ['Control'] });
    await esperarSoma(page, '56');
    assert.strictEqual(await selecionadas.count(), 5);
    await page.locator(seletorCelula(0, 6)).click({ modifiers: ['Control'] });
    await esperarSoma(page, '70');
    assert.strictEqual(await selecionadas.count(), 6);

    await page.locator(seletorCelula(0, 6)).click();
    await page.keyboard.press('ArrowRight');
    await esperarSoma(page, '29');
    assert(await page.locator(seletorCelula(0, 7)).evaluate(el => el.classList.contains('celula-ativa')));
    await page.keyboard.press('Shift+ArrowRight');
    await esperarSoma(page, '47');
    assert.strictEqual(await selecionadas.count(), 2);
    await page.keyboard.press('ArrowDown');
    await esperarSoma(page, '4');
    await page.keyboard.press('Shift+ArrowLeft');
    await esperarSoma(page, '7');
    assert.strictEqual(await copiar(page), '3\t4');
    await page.keyboard.press('Escape');
    assert.strictEqual(await selecionadas.count(), 0);
    await balao.waitFor({ state: 'hidden' });

    await arrastar(page, 0, 6, 0, 8);
    await page.evaluate(() => document.activeElement.blur());
    await page.evaluate(() => { const raiz = document.getElementById('secaoTabelaPrincipal'); raiz.scrollTop = raiz.scrollHeight; });
    await page.locator(seletorCelula(99, 8)).waitFor();
    await aguardarFrames(page);
    assert.strictEqual(await page.locator(seletorCelula(0, 6)).count(), 0, 'O teste deve atravessar a janela da tabela virtual');
    await page.evaluate(() => { document.getElementById('secaoTabelaPrincipal').scrollTop = 0; });
    await page.locator(seletorCelula(0, 6)).waitFor();
    await aguardarFrames(page);
    assert.strictEqual(await selecionadas.count(), 3, 'Rolar e voltar deve preservar as células selecionadas');
    await page.locator(seletorCelula(0, 8)).focus();
    assert.strictEqual(await copiar(page), '14\t29\t18');

    // Shift may select values whose rows are not currently mounted in the DOM.
    await page.evaluate(() => { const raiz = document.getElementById('secaoTabelaPrincipal'); raiz.scrollTop = raiz.scrollHeight; });
    await page.locator(seletorCelula(99, 8)).waitFor();
    await aguardarFrames(page);
    await page.locator(seletorCelula(99, 8)).click({ modifiers: ['Shift'] });
    await esperarSoma(page, '658');
    const copiado = await copiar(page);
    assert.strictEqual(copiado.split('\n').length, 100, 'A cópia deve incluir as linhas fora da janela virtual');
    assert.strictEqual(copiado.split('\n')[0], '14\t29\t18');
    assert.strictEqual(copiado.split('\n')[99], '1\t2\t3');

    await page.keyboard.press('Control+a');
    const todas = (await copiar(page)).split('\n');
    assert.strictEqual(todas.length, 100, 'Ctrl + A deve selecionar todas as linhas da tabela');
    assert.strictEqual(todas[0].split('\t').length, 16, 'A seleção completa deve excluir a coluna de ações');
    assert.strictEqual(todas[0].split('\t')[6], '14');

    await page.evaluate(() => renderAbaAtual());
    assert.strictEqual(await selecionadas.count(), 0, 'Uma nova renderização deve limpar a seleção anterior');
    await balao.waitFor({ state: 'hidden' });
    await page.evaluate(() => { document.getElementById('secaoTabelaPrincipal').scrollTop = 0; });
    const botao = page.locator('#tblResultado .compra-sugerida-editavel[data-sku="001"]');
    await botao.waitFor();
    await botao.click();
    const input = page.locator('#tblResultado .compra-sugerida-input');
    await input.waitFor();
    assert.strictEqual(await input.inputValue(), '12', 'O clique no botão deve continuar abrindo o editor da sugestão');
    await input.fill('15');
    await page.keyboard.press('ArrowLeft');
    assert.strictEqual(await input.inputValue(), '15', 'As setas do campo em edição não devem navegar pela tabela');
    await page.keyboard.press('Escape');
    await input.waitFor({ state: 'hidden' });
    assert.strictEqual(await page.locator('#tblResultado .compra-sugerida-editavel[data-sku="001"]').textContent(), '12');
    assert.deepStrictEqual(errors, [], 'A seleção de células não deve emitir erros de JavaScript');
    console.log('medias selecao celulas browser: OK');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
