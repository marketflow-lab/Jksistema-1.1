'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..', 'static');
const origin = 'http://jk.test';
const listasApi = '/api/medias-compras/listas-pedidos';
const painelEmbarque = {
  cards: '#embarqueDetalheCards .emb-resumo-card', valor: '.emb-value', complemento: '.emb-value-sub',
};
const painelLista = {
  cards: '#resumoTopo .resumo-card', valor: '.value', complemento: '.value-sub',
};

function pedido(completo) {
  const impostos = { II: 10, IPI: 5, PIS: 2, COFINS: 8, ICMS: 4 };
  return {
    id: 'A', nome_lista: 'Pedido sintético', numero_invoice: 'INV-TESTE',
    status: 'Analisando orçamento', store_id: 'store-a', loja: 'Loja sintética',
    total_usd: 100, total_itens: 2, total_quantidade: 5, total_m3: completo ? 1.25 : 0.4,
    m3_completo: completo, m3_pendente_itens: completo ? 0 : 1,
    m3_fonte: completo ? 'packing_list' : 'lista', packing_list_ready: completo,
    ...(completo ? {
      exchange_rate: 4.254,
      packing_list_totais: { total_m3: 1.25, total_caixas: 10, total_trays: 0, peso_bruto_total_kg: 42 },
    } : {}),
    itens: [
      { SKU: '001', Quantidade: 2, 'Valor unidade': 50, 'Valor total': 100,
        valor_total_informado: true, 'Frete Internacional': '10,50', NCM: '1234.56.78',
        M3: 0.4, 'Cubagem status': 'historico', analise_concorrentes: { margens: {} }, ...impostos },
      { SKU: '002', Quantidade: 3, 'Valor unidade': 99, 'Valor total': 0,
        valor_total_informado: true, 'Frete Internacional': 0, ncm: '12345678',
        M3: completo ? 0.2 : 0, 'Cubagem status': completo ? 'historico' : 'pendente',
        analise_concorrentes: { margens: {} }, ...impostos },
    ],
  };
}

async function ambiente(browser, completo, rascunhoM3 = null) {
  const context = await browser.newContext();
  context.setDefaultTimeout(10000);
  const lista = pedido(completo);
  const { itens, ...resumo } = lista;
  const erros = [];
  let liberarCotacao;
  const cotacao = new Promise(resolve => { liberarCotacao = resolve; });
  if (completo) liberarCotacao();

  context.on('page', page => page.on('pageerror', error => erros.push(error.message)));
  await context.addInitScript(({ rascunhoM3, origin }) => {
    if (location.origin !== origin || window !== window.top) return;
    localStorage.setItem('user_data', JSON.stringify({ client_id: 'synthetic', username: 'tester' }));
    localStorage.setItem('access_token', 'synthetic');
    localStorage.setItem('jk_embarques_importacoes', JSON.stringify([
      { id: 'E1', nome: 'Embarque sintético', lista_ids: ['A'] },
    ]));
    if (rascunhoM3 !== null) {
      localStorage.setItem('importacoes_lista_resumo_editavel', JSON.stringify({
        A: { valor_por_m3: rascunhoM3 },
      }));
    }
  }, { rascunhoM3, origin });

  const json = (route, body) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(body),
  });
  await context.route('**/*', async route => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    if (url.hostname === 'economia.awesomeapi.com.br') {
      await cotacao;
      return json(route, { USDBRL: { bid: '6.00' } });
    }
    if (url.origin !== origin) return route.fulfill({ status: 200, contentType: 'text/css', body: '' });
    if (p === '/auth.js') {
      return route.fulfill({
        contentType: 'application/javascript',
        body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic"});',
      });
    }
    if (p === '/importacoes.html' || p === '/importacoes_lista.html'
        || p === '/cache-persistente.js' || p.startsWith('/medias_compras/')) {
      const type = p.endsWith('.html') ? 'text/html'
        : p.endsWith('.css') ? 'text/css' : 'application/javascript';
      return route.fulfill({
        contentType: type + '; charset=utf-8', body: fs.readFileSync(path.join(root, p.slice(1))),
      });
    }
    if (p === listasApi || p === listasApi + '/A') {
      assert.strictEqual(req.method(), 'GET');
      assert.strictEqual(req.headers().authorization, 'Bearer synthetic');
      return json(route, p === listasApi ? { listas: [resumo] } : { lista });
    }
    if (p === '/api/lojas') return json(route, [{ nome: lista.loja, store_id: lista.store_id }]);
    if (p === '/api/impostos/produtos') return json(route, { produtos: [] });
    if (p === '/api/vendas/grafico') return json(route, { valores_vendas: [], valores_devolucoes: [] });
    if (p.startsWith('/api/cadastro/')) return json(route, []);
    if (p.startsWith('/api/')) return json(route, {});
    return route.fulfill({ status: 200, body: '' });
  });
  return { context, erros, liberarCotacao };
}

function card(page, painel, rotulo) {
  return page.locator(painel.cards).filter({ has: page.getByText(rotulo, { exact: true }) });
}

function valor(page, painel, rotulo, complemento = false) {
  return card(page, painel, rotulo).locator(complemento ? painel.complemento : painel.valor);
}

function textoNormalizado(texto) {
  return texto.replace(/\s+/g, ' ').trim();
}

async function lerValor(page, painel, rotulo, complemento = false) {
  const campo = valor(page, painel, rotulo, complemento);
  if (complemento) return textoNormalizado(await campo.innerText());
  return campo.evaluate((el, seletorComplemento) => {
    const copia = el.cloneNode(true);
    copia.querySelectorAll(seletorComplemento).forEach(node => node.remove());
    return copia.textContent.replace(/\s+/g, ' ').trim();
  }, painel.complemento);
}

async function abrirEmbarque(page) {
  await page.getByRole('tab', { name: 'Embarques', exact: true }).click();
  await page.locator('#embarquesWrap .embarque-item').getByRole('button', { name: /^Ver embarque\b/ }).click();
  await valor(page, painelEmbarque, 'VMLE').waitFor();
}

const listaEmBrl = page => page.locator('#listaWrap .lista-valor-brl strong');

function usd(texto) {
  assert.match(texto, /^(?:US)?\$\s*[\d.,]+$/);
  const montante = texto.replace(/^(?:US)?\$\s*/, '');
  return Number(montante.lastIndexOf(',') > montante.lastIndexOf('.')
    ? montante.replace(/\./g, '').replace(',', '.') : montante.replace(/,/g, ''));
}

function m3(texto) {
  return Number(texto.replace(/\s*m³$/, '').replace(',', '.'));
}

async function conferirCartoes(page, painel, esperado) {
  for (const [rotulo, valorEsperado, complemento] of esperado) {
    assert.strictEqual(await lerValor(page, painel, rotulo, complemento), valorEsperado, rotulo);
  }
}

async function comCotacaoSalva(browser, rascunhoM3, valorM3Esperado) {
  const { context, erros } = await ambiente(browser, true, rascunhoM3);
  try {
    const page = await context.newPage();
    await page.goto(origin + '/importacoes.html');
    await page.locator('#fxValue').filter({ hasText: '6,00' }).waitFor();
    assert.strictEqual(textoNormalizado(await listaEmBrl(page).innerText()), 'R$ 425,00');
    await abrirEmbarque(page);
    const valores = [
      ['Cotação na Compra', 'R$ 4,25'],
      ['VMLE', 'R$ 425,00', true],
      ['Frete Internacional', 'R$ 44,63'],
      ['Seguro Internacional 0,28%', 'R$ 1,31'],
      ['VMLD', 'R$ 470,94'],
      ['II', 'R$ 46,96', true],
      ['IPI', 'R$ 23,60', true],
      ['PIS', 'R$ 9,39', true],
      ['COFINS', 'R$ 37,57', true],
      ['ICMS', 'R$ 18,79', true],
      ['Armazenagem', 'R$ 16,48'],
      ['Pick-up Origem', 'R$ 1.627,23'],
      ['Taxas de Origem', 'R$ 1.557,39'],
      ['Transporte Rodoviário', 'R$ 1.657,78'],
      ['Capatazia', 'R$ 37,50'],
      ['Honorários Trading', 'R$ 14,13'],
      ['AFRMM', 'R$ 3,57'],
      ['Taxa Siscomex', 'R$ 154,23'],
      ['Total NCM', '1'],
    ];

    async function conferirPainel(painel) {
      assert.strictEqual(usd(await lerValor(page, painel, 'Valor por M³')), valorM3Esperado,
        'Valor por M³ com rascunho ' + rascunhoM3 + ' em ' + painel.cards);
      if (rascunhoM3 !== '0') {
        await conferirCartoes(page, painel, valores);
        assert.strictEqual(m3(await lerValor(page, painel, 'M³ Total')), 1.25);
        assert.strictEqual(usd(await lerValor(page, painel, 'VMLE')), 100);
      }
    }

    await conferirPainel(painelEmbarque);
    await page.locator('#embarqueDetalheListas .lista-item').click();
    await page.waitForURL(origin + '/importacoes_lista.html?lista_id=A&from_embarque=E1');
    await conferirPainel(painelLista);
    await page.locator('#btnVoltar').click();
    await page.waitForURL(origin + '/importacoes.html?embarque_id=E1');
    await valor(page, painelEmbarque, 'VMLE').waitFor();
    assert.strictEqual(await page.getByRole('tab', { name: 'Embarques', exact: true }).getAttribute('aria-selected'), 'true');
    assert.deepStrictEqual(erros, []);
  } finally {
    await context.close();
  }
}

async function comCotacaoAtrasada(browser) {
  const { context, erros, liberarCotacao } = await ambiente(browser, false);
  try {
    const indice = await context.newPage();
    await indice.goto(origin + '/importacoes.html');
    assert.strictEqual(textoNormalizado(await listaEmBrl(indice).innerText()), '-');
    await abrirEmbarque(indice);

    const detalhe = await context.newPage();
    await detalhe.goto(origin + '/importacoes_lista.html?lista_id=A');
    const telas = [[indice, painelEmbarque], [detalhe, painelLista]];
    for (const [page, painel] of telas) {
      assert.strictEqual(await lerValor(page, painel, 'Cotação na Compra'), '-');
      assert.strictEqual(await lerValor(page, painel, 'VMLE', true), '-');
      assert.strictEqual(m3(await lerValor(page, painel, 'M³ Total parcial')), 0.4);
    }

    liberarCotacao();
    for (const [page, painel] of telas) {
      await valor(page, painel, 'VMLD').filter({ hasText: '664,86' }).waitFor();
      await valor(page, painel, 'Armazenagem').filter({ hasText: 'Cubagem pendente' }).waitFor();
      await conferirCartoes(page, painel, [
        ['Cotação na Compra', '-'],
        ['VMLE', 'R$ 600,00', true],
        ['Frete Internacional', 'R$ 63,00'],
        ['Seguro Internacional 0,28%', 'R$ 1,86'],
        ['VMLD', 'R$ 664,86'],
        ['Valor por M³', '-'],
        ['Armazenagem', 'Cubagem pendente'],
        ['Pick-up Origem', 'Cubagem pendente'],
        ['Taxas de Origem', 'Cubagem pendente'],
        ['Capatazia', 'Cubagem pendente'],
        ['Transporte Rodoviário', 'Cubagem pendente'],
        ['Taxa Siscomex', 'R$ 154,23'],
      ]);
    }
    await indice.locator('#btnVoltarEmbarque').click();
    await indice.locator('#embarquesWrap .embarque-item').waitFor();
    await indice.getByRole('tab', { name: 'Pedidos', exact: true }).click();
    assert.strictEqual(textoNormalizado(await listaEmBrl(indice).innerText()), 'R$ 600,00');
    assert.deepStrictEqual(erros, []);
  } finally {
    liberarCotacao();
    await context.close();
  }
}

(async () => {
  const channel = process.env.JK_IMPORTACOES_BROWSER_CHANNEL;
  const browser = await chromium.launch({ headless: true, ...(channel ? { channel } : {}) });
  try {
    await comCotacaoSalva(browser, '91,50', 91.5);
    await comCotacaoSalva(browser, '0', 80);
    await comCotacaoAtrasada(browser);
    console.log('Custos de Importações: lista, embarque unitário, câmbio salvo e tardio, Packing List e cubagem parcial conferidos.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
