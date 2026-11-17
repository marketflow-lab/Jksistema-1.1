'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'medias_compras.html'), 'utf8');
const avisoPedidosEmAnalise = 'Pedidos em análise: 700 un consideradas provisoriamente. '
  + 'O estoque após a chegada depende de confirmar a compra e o prazo.';
const base = {
  titulo_anuncio: 'Produto simulado', vendas_mensais: { '2026-10': 100 },
  total_vendas_periodo: 600, media_mensal: 100, saldo_atual_estoque: 700,
  estoque_em_transito: 0, posicao_estoque: 700, planejamento_versao: 1,
  meses_historico: 6, data_chegada_prevista: '2027-05-17',
  meses_estoque_apos_chegada: 7, falta_antes_chegada_quantidade: 0,
  estoque_previsto_apos_chegada: 700, estoque_alvo_apos_chegada: 700,
  risco_ruptura_antes_chegada: false, planejamento_requer_revisao: false,
  avisos_planejamento: [],
};
const itens = [
  { ...base, sku: '001', saldo_atual_estoque: 800, posicao_estoque: 800,
    media_mensal_prevista: 120, tendencia_vendas: 'alta', variacao_vendas_percentual: 20,
    tendencia_motivo: 'Alta consistente nos meses completos', estoque_previsto_antes_chegada: 80,
    reserva_antes_chegada_quantidade: 120, reserva_antes_chegada_atendida: false, compra_sugerida: 760,
    estoque_previsto_apos_chegada: 840, estoque_alvo_apos_chegada: 840 },
  { ...base, sku: '002', saldo_atual_estoque: 910, posicao_estoque: 910,
    media_mensal_prevista: 70, tendencia_vendas: 'queda', variacao_vendas_percentual: -30,
    tendencia_motivo: 'Queda nos meses completos', estoque_previsto_antes_chegada: 490,
    estoque_previsto_apos_chegada: 490, estoque_alvo_apos_chegada: 490,
    reserva_antes_chegada_quantidade: 70, reserva_antes_chegada_atendida: true, compra_sugerida: 0 },
  { ...base, sku: '003', media_mensal: 0, total_vendas_periodo: 0, vendas_mensais: { '2026-10': 0 },
    saldo_atual_estoque: 0, posicao_estoque: 0, estoque_previsto_apos_chegada: 0, estoque_alvo_apos_chegada: 0,
    media_mensal_prevista: 0, tendencia_vendas: 'insuficiente', variacao_vendas_percentual: null,
    tendencia_motivo: '"><img src=x onerror="window.injecaoPrevisao=true">', estoque_previsto_antes_chegada: 0,
    reserva_antes_chegada_quantidade: 0, reserva_antes_chegada_atendida: true, compra_sugerida: 0,
    planejamento_requer_revisao: true, avisos_planejamento: ['Histórico insuficiente para identificar tendência'] },
  { ...base, sku: '035', saldo_atual_estoque: 0, posicao_estoque: 0,
    media_mensal_prevista: 80, tendencia_vendas: 'indeterminada', variacao_vendas_percentual: -20,
    tendencia_motivo: 'Queda nas vendas com estoque zerado; confirme se houve falta de produto.',
    estoque_previsto_antes_chegada: 0, reserva_antes_chegada_quantidade: 80,
    reserva_antes_chegada_atendida: false, risco_ruptura_antes_chegada: true,
    falta_antes_chegada_quantidade: 480, compra_sugerida: 560, estoque_previsto_apos_chegada: 560,
    estoque_alvo_apos_chegada: 560, planejamento_requer_revisao: true },
  { ...base, sku: '004', saldo_atual_estoque: 0, estoque_em_transito: 1000, posicao_estoque: 1000,
    transito_confirmado: 1000, media_mensal_prevista: 100, tendencia_vendas: 'estavel', variacao_vendas_percentual: 0,
    estoque_previsto_antes_chegada: 1000, estoque_previsto_apos_chegada: 1000,
    reserva_antes_chegada_quantidade: 100,
    reserva_antes_chegada_atendida: false, risco_ruptura_antes_chegada: true, compra_sugerida: 0,
    falta_antes_chegada_quantidade: 600,
    avisos_planejamento: ['Pedido confirmado de 1.000 unidades chega no sexto mês; há falta até essa entrada.'] },
  { ...base, sku: '005', saldo_atual_estoque: 0, posicao_estoque: 0, media_mensal_prevista: 4.83,
    tendencia_vendas: 'indeterminada', variacao_vendas_percentual: null,
    tendencia_motivo: 'Há meses sem vendas; confirme a disponibilidade do produto antes de interpretar alta ou queda.',
    estoque_previsto_antes_chegada: 0, reserva_antes_chegada_quantidade: 4.83,
    reserva_antes_chegada_atendida: false, risco_ruptura_antes_chegada: true,
    falta_antes_chegada_quantidade: 29, compra_sugerida: 35, estoque_previsto_apos_chegada: 35,
    estoque_alvo_apos_chegada: 33.83, planejamento_requer_revisao: true,
    avisos_planejamento: [
      'Há meses sem vendas; confirme a disponibilidade do produto antes de interpretar alta ou queda.',
      'Orçamentos ou outras quantidades sem compra confirmada não abastecem esta previsão.',
      'Há previsão de falta antes da chegada. A nova compra não repõe as vendas perdidas nesse intervalo.',
    ] },
  { ...base, sku: '79', media_mensal: 124.33, total_vendas_periodo: 770,
    saldo_atual_estoque: 0, estoque_em_transito: 700, posicao_estoque: 700,
    transito_confirmado: 0, transito_em_analise_quantidade: 700,
    pedidos_considerados_quantidade: 700, abatimento_pedidos_quantidade: 700,
    media_mensal_prevista: 152.2, tendencia_vendas: 'alta', variacao_vendas_percentual: 254.88,
    tendencia_motivo: 'Alta consistente nos meses completos', estoque_previsto_antes_chegada: 0,
    reserva_antes_chegada_quantidade: 152.2, reserva_antes_chegada_atendida: false,
    compra_sugerida: 370, estoque_previsto_apos_chegada: 1070, estoque_alvo_apos_chegada: 1065.4,
    falta_antes_chegada_quantidade: 913.2, risco_ruptura_antes_chegada: true,
    planejamento_requer_revisao: true, avisos_planejamento: [avisoPedidosEmAnalise] },
];

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/medias_compras.html') {
        return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html });
      }
      if (url.pathname.startsWith('/medias_compras/')) {
        const relativePath = url.pathname.replace(/^\//, '');
        const body = fs.readFileSync(path.join(root, 'static', relativePath));
        return route.fulfill({ status: 200, contentType: relativePath.endsWith('.css') ? 'text/css' : 'application/javascript', body });
      }
      if (url.pathname === '/api/lojas') {
        return route.fulfill({ json: [{ nome: 'Loja simulada', store_id: 'store-simulado' }] });
      }
      if (url.pathname === '/api/medias-compras/preferencias-skus-ocultos') return route.fulfill({ json: { skus_ocultos: [] } });
      if (url.pathname === '/api/medias-compras/visao') return route.fulfill({ json: { colunas_meses: [{ key: '2026-10' }], itens } });
      if (url.pathname === '/auth.js') {
        return route.fulfill({ contentType: 'application/javascript', body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({});' });
      }
      return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    });

    await page.goto('http://jk.test/medias_compras.html');
    const linha = sku => page.locator('#tblResultado tbody tr').filter({ has: page.locator('.compra-sugerida-editavel[data-sku="' + sku + '"]') });
    await linha('001').locator('.previsao-tendencia--alta').waitFor();
    assert.strictEqual((await linha('001').locator('.previsao-tendencia').textContent()).trim(), 'Alta +20%');
    assert.strictEqual((await linha('001').locator('.compra-sugerida-cell .previsao-seta--alta').textContent()).trim(), '↑');
    assert.strictEqual(await linha('001').locator('.previsao-seta--alta').evaluate(el => getComputedStyle(el).color), 'rgb(74, 222, 128)');
    assert.strictEqual(await linha('001').locator('.compra-sugerida-cell .previsao-tendencia').count(), 1, 'Tendência precisa estar na coluna Sugestão de compra');
    assert.strictEqual(await linha('001').locator('.previsao-detalhe .previsao-tendencia').count(), 0);
    assert.strictEqual((await linha('001').locator('.previsao-detalhe').textContent()).trim(), 'Prev. 120');
    const balao = page.locator('#balaoPlanejamentoCompra');
    const botao = linha('001').locator('.compra-sugerida-editavel');
    for (const sku of ['001', '002', '003', '004', '035', '79']) {
      const texto = await linha(sku).locator('.compra-sugerida-cell').textContent();
      for (const detalhe of ['Antes:', 'Reserva:', 'Reserva insuficiente', 'Falta antes da entrega', 'Revisar previsão', 'Ajuste manual']) {
        assert(!texto.includes(detalhe), detalhe + ' só deve aparecer no balão da quantidade');
      }
    }
    assert.strictEqual(await balao.isVisible(), false);
    await linha('001').locator('.previsao-seta--alta').hover();
    assert.strictEqual(await balao.isVisible(), false, 'A seta não é gatilho do balão');
    await botao.hover();
    await balao.waitFor({ state: 'visible' });
    assert.strictEqual(await botao.getAttribute('title'), null, 'O balão não depende do title nativo');
    assert.strictEqual(await botao.getAttribute('aria-describedby'), 'balaoPlanejamentoCompra');
    const textoAntes = await balao.textContent();
    const metrica = rotulo => balao.locator('.compra-planejamento-metrica').filter({ has: page.locator('dt', { hasText: rotulo }) }).locator('dd');
    assert.strictEqual(await metrica('Antes de receber').textContent(), '80 un');
    assert.strictEqual(await metrica('Reserva de 1 mês').textContent(), '120 un');
    assert(textoAntes.includes('Reserva insuficiente'));
    assert(textoAntes.includes('17/05/2027'));
    assert.strictEqual(await metrica('Após receber').textContent(), '840 un');
    assert(textoAntes.includes('Meta: 7 meses após receber.'));
    assert(await balao.evaluate(el => el.parentElement === document.body), 'Balão precisa escapar do recorte da tabela');
    assert(await balao.evaluate(el => {
      const r = el.getBoundingClientRect();
      return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight;
    }), 'Balão precisa caber na janela');
    await balao.hover();
    await page.waitForTimeout(180);
    assert(await balao.isVisible(), 'Mover para o interior do balão não deve fechá-lo');
    await page.mouse.move(12, 12);
    await balao.waitFor({ state: 'hidden' });
    await botao.focus();
    await balao.waitFor({ state: 'visible' });
    await page.keyboard.press('Escape');
    await balao.waitFor({ state: 'hidden' });
    await page.keyboard.press('Tab');
    assert((await linha('001').getAttribute('class')).includes('risco-atencao'));
    assert.strictEqual((await linha('002').locator('.previsao-tendencia').textContent()).trim(), 'Queda -30%');
    assert.strictEqual((await linha('002').locator('.compra-sugerida-cell .previsao-seta--queda').textContent()).trim(), '↓');
    assert.strictEqual(await linha('002').locator('.previsao-seta--queda').evaluate(el => getComputedStyle(el).color), 'rgb(248, 113, 113)');
    await linha('002').locator('.compra-sugerida-editavel').hover();
    await balao.waitFor({ state: 'visible' });
    assert.strictEqual(await metrica('Antes de receber').textContent(), '490 un');
    assert(!(await balao.textContent()).includes('Reserva insuficiente'));
    assert(!(await linha('002').getAttribute('class')).includes('risco-'));
    assert.strictEqual((await linha('003').locator('.previsao-tendencia').textContent()).trim(), 'Dados insuficientes');
    await linha('003').locator('.compra-sugerida-editavel').hover();
    await balao.waitFor({ state: 'visible' });
    assert((await balao.textContent()).includes('Histórico insuficiente'));
    assert((await balao.textContent()).includes('<img src=x'));
    assert.strictEqual(await balao.locator('img').count(), 0, 'Motivos devem ser texto sem executar HTML');
    assert.strictEqual(await page.evaluate(() => Boolean(window.injecaoPrevisao)), false);
    assert((await linha('004').getAttribute('class')).includes('risco-critico'), 'Ruptura precisa aparecer mesmo com compra zero');
    await linha('004').locator('.compra-sugerida-editavel').hover();
    await balao.waitFor({ state: 'visible' });
    assert((await balao.locator('.compra-planejamento-alerta').textContent()).includes('600 un antes da entrega'));
    assert.strictEqual((await linha('035').locator('.previsao-tendencia').textContent()).trim(), 'Queda a confirmar');
    assert.strictEqual(await linha('035').locator('.previsao-seta').count(), 0, 'Queda incerta não recebe seta de tendência confirmada');
    await linha('035').locator('.compra-sugerida-editavel').hover();
    await balao.waitFor({ state: 'visible' });
    assert((await balao.textContent()).includes('O produto está sem estoque; a queda nas vendas precisa ser confirmada.'));
    assert((await balao.locator('.compra-planejamento-alerta').textContent()).includes('480 un antes da entrega'));
    await linha('005').locator('.compra-sugerida-editavel').hover();
    await balao.waitFor({ state: 'visible' });
    assert.strictEqual(await balao.locator('.compra-planejamento-quantidade').textContent(), '35 un');
    assert.strictEqual(await metrica('Antes de receber').textContent(), '0 un');
    assert.strictEqual(await metrica('Reserva de 1 mês').textContent(), '4,83 un');
    assert.strictEqual(await metrica('Após receber').textContent(), '35 un');
    const resumoBaixoGiro = await balao.textContent();
    assert.strictEqual(await balao.locator('.compra-planejamento-alerta').count(), 1, 'A falta e a reserva não devem gerar alertas duplicados');
    assert(resumoBaixoGiro.includes('29 un antes da entrega'), 'O resumo deve preservar a quantidade prevista em falta');
    assert.strictEqual(resumoBaixoGiro.split('Houve meses sem venda.').length - 1, 1);
    assert(!resumoBaixoGiro.includes('Reserva insuficiente'), 'O alerta de falta já explica o risco principal');
    assert(!resumoBaixoGiro.includes('33,83'), 'A meta em unidades não precisa repetir o saldo após receber');
    assert(resumoBaixoGiro.includes('Só compras confirmadas entram nessa conta.'));
    assert(await balao.evaluate(el => el.getBoundingClientRect().height < 470), 'O resumo deve caber sem um bloco extenso de texto');
    if (process.env.JK_MEDIAS_TOOLTIP_PREVIEW) {
      await page.screenshot({ path: process.env.JK_MEDIAS_TOOLTIP_PREVIEW });
    }
    await page.mouse.move(12, 12);
    await balao.waitFor({ state: 'hidden' });
    const celulaPedidoEmAnalise = linha('79').locator('.compra-sugerida-cell');
    assert.strictEqual((await celulaPedidoEmAnalise.textContent()).replace(/\s+/g, ''), '370↑Alta+254,88%',
      'A célula deve mostrar somente quantidade, seta e tendência');
    await celulaPedidoEmAnalise.locator('.previsao-seta--alta').hover();
    assert.strictEqual(await balao.isVisible(), false, 'O aviso provisório só abre na quantidade');
    await celulaPedidoEmAnalise.locator('.compra-sugerida-editavel').hover();
    await balao.waitFor({ state: 'visible' });
    assert.strictEqual(await balao.locator('.compra-planejamento-quantidade').textContent(), '370 un');
    assert.strictEqual(await metrica('Antes de receber').textContent(), '0 un', 'Orçamento não garante estoque antes da chegada');
    assert.strictEqual(await metrica('Reserva de 1 mês').textContent(), '152,2 un');
    assert.strictEqual(await metrica('Após receber').textContent(), '1.070 un');
    assert((await balao.locator('.compra-planejamento-alerta').textContent()).includes('913,2 un antes da entrega'));
    assert.strictEqual(await balao.locator('.compra-planejamento-avisos li').textContent(), avisoPedidosEmAnalise,
      'O aviso deve preservar a quantidade provisória e a condição de confirmar compra e prazo');
    assert(!(await balao.textContent()).includes('Só compras confirmadas entram nessa conta.'));
    if (process.env.JK_MEDIAS_PENDING_PREVIEW) {
      await balao.screenshot({ path: process.env.JK_MEDIAS_PENDING_PREVIEW });
    }
    await page.mouse.move(12, 12);
    await balao.waitFor({ state: 'hidden' });
    assert((await page.locator('#planejamentoPolitica').textContent()).includes('7 meses no total após receber'));
    await botao.click();
    await page.locator('.compra-sugerida-input').fill('500');
    await page.locator('.compra-sugerida-input').press('Enter');
    assert.strictEqual((await linha('001').locator('.compra-sugerida-editavel').textContent()).trim(), '500');
    await linha('001').locator('.compra-sugerida-editavel').hover();
    await balao.waitFor({ state: 'visible' });
    assert((await balao.locator('.compra-planejamento-manual').textContent()).includes('Os saldos são do cálculo automático'));
    assert.strictEqual(await metrica('Antes de receber').textContent(), '80 un', 'Ajuste manual não inventa saldo');
    assert.deepStrictEqual(await page.evaluate(() => obterQuantidadesSugeridasEditadas()), { '001': 500 });
    await page.mouse.move(12, 12);
    await balao.waitFor({ state: 'hidden' });

    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.locator('#planejamentoPolitica').evaluate(el => el.getBoundingClientRect().right <= document.documentElement.clientWidth + 1));
    await linha('001').locator('.compra-sugerida-editavel').hover();
    await balao.waitFor({ state: 'visible' });
    assert(await balao.evaluate(el => { const r = el.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight; }), 'Balão deve caber no celular');
    const transbordamento = await linha('001').locator('.previsao-tendencia, .planejamento-detalhe').evaluateAll(els => els.some(el => el.scrollWidth > el.clientWidth + 1));
    assert.strictEqual(transbordamento, false, 'Detalhes precisam quebrar linha em colunas estreitas');
    assert.deepStrictEqual(errors, []);
    console.log('medias previsao compras browser: OK');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
