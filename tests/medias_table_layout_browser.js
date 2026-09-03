'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'medias_compras.html'), 'utf8');
const staticHtml = fs.readFileSync(path.join(root, 'static', 'medias_compras.html'), 'utf8');

assert.strictEqual(staticHtml, html, 'os espelhos de Medias e Compras devem permanecer identicos');

const meses12 = [
  '2025-10', '2025-11', '2025-12',
  '2026-01', '2026-02', '2026-03',
  '2026-04', '2026-05', '2026-06',
  '2026-07', '2026-08', '2026-09',
];

const chavesFixasAntesMeses = ['sku', 'foto', 'titulo_anuncio'];
const chavesFixasDepoisMeses = [
  'total_periodo',
  'media_mensal',
  'saldo_estoque',
  'estoque_transito',
  'posicao_estoque',
  'cobertura_meses',
  'compra_sugerida',
  'acao',
];
const minimosBase = {
  sku: 80,
  foto: 64,
  titulo_anuncio: 200,
  total_periodo: 72,
  media_mensal: 56,
  saldo_estoque: 80,
  estoque_transito: 80,
  posicao_estoque: 96,
  cobertura_meses: 92,
  compra_sugerida: 92,
  acao: 72,
};

function mesesDoPeriodo(periodo) {
  return meses12.slice(-Number(periodo));
}

function chavesDoPeriodo(periodo) {
  return chavesFixasAntesMeses
    .concat(Array.from({ length: Number(periodo) }, (_valor, indice) => `mes_${indice}`))
    .concat(chavesFixasDepoisMeses);
}

function minimosDoPeriodo(periodo) {
  const minimos = { ...minimosBase };
  for (let indice = 0; indice < Number(periodo); indice += 1) minimos[`mes_${indice}`] = 52;
  return minimos;
}

function hashEscopoPreferencias(valor) {
  let hashA = 0x811c9dc5;
  let hashB = 0x9e3779b9;
  const texto = String(valor || '');
  for (let indice = 0; indice < texto.length; indice += 1) {
    const codigo = texto.charCodeAt(indice);
    hashA = Math.imul(hashA ^ codigo, 0x01000193);
    hashB = Math.imul(hashB ^ codigo, 0x85ebca6b);
    hashB ^= hashB >>> 13;
  }
  return (hashA >>> 0).toString(16).padStart(8, '0')
    + (hashB >>> 0).toString(16).padStart(8, '0');
}

function chavePerfilUsuario(clientId, username, periodo) {
  const hash = hashEscopoPreferencias(
    `jk-medias-widths-v3\u0000${String(clientId).trim()}\u0000${String(username).trim().toLowerCase()}`,
  );
  return `medias_compras_larguras_colunas_v3_${hash}_${periodo}m`;
}

function responderJson(route, body) {
  return route.fulfill({
    status: 200,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  });
}

function criarControle(opcoes = {}) {
  return {
    variante: opcoes.variante || 'normal',
    quantidadeItens: Number(opcoes.quantidadeItens || 24),
    periodosVazios: new Set(opcoes.periodosVazios || []),
    atrasosMs: { ...(opcoes.atrasosMs || {}) },
  };
}

function criarItem(indiceItem, meses, variante) {
  const vendasMensais = Object.fromEntries(meses.map((mes, indice) => [mes, indice + 1]));
  let sku = String(indiceItem + 1).padStart(3, '0');
  let titulo = 'Produto de teste para validar o layout da tabela';
  let total = 78;

  if (variante === 'curto') {
    titulo = 'Item';
    total = 6;
  } else if (variante === 'longo' && indiceItem === 0) {
    sku = 'SKU-CONTEUDO-MAIS-LONGO-1234567890';
    titulo = 'Produto internacional com descricao extensa para comprovar que o primeiro ajuste considera o conteudo real da celula';
    vendasMensais[meses[0]] = 987654321;
    total = 987654321;
  } else if (variante === 'cobertura-mista') {
    titulo = indiceItem === 0 ? 'Primeira linha sem venda' : 'Produto com cobertura numerica';
  }

  const primeiraLinhaSemVenda = variante === 'cobertura-mista' && indiceItem === 0;
  const segundaLinhaCoberturaLonga = variante === 'cobertura-mista' && indiceItem === 1;

  return {
    sku,
    titulo_anuncio: titulo,
    vendas_mensais: vendasMensais,
    total_vendas_periodo: total,
    media_mensal: primeiraLinhaSemVenda
      ? 0
      : (segundaLinhaCoberturaLonga ? 0.01 : (variante === 'longo' && indiceItem === 0 ? 123456.75 : 6.5)),
    saldo_atual_estoque: variante === 'longo' && indiceItem === 0 ? 987654321 : 25,
    estoque_em_transito: 10,
    estoque_em_transito_listas: [{ lista_id: 'lista-1', nome_lista: 'Pedido teste', quantidade: 10 }],
    posicao_estoque: segundaLinhaCoberturaLonga
      ? 9876543210
      : (variante === 'longo' && indiceItem === 0 ? 987654331 : 35),
    compra_sugerida: variante === 'longo' && indiceItem === 0 ? 987654300 : 43,
  };
}

async function instalarRotas(page, controle) {
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/medias_compras.html') {
      await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html });
      return;
    }
    if (url.pathname.startsWith('/medias_compras/')) {
      const relativePath = url.pathname.replace(/^\//, '');
      const body = fs.readFileSync(path.join(root, 'static', relativePath));
      await route.fulfill({
        status: 200,
        contentType: relativePath.endsWith('.css') ? 'text/css' : 'application/javascript',
        body,
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
    if (url.pathname === '/api/lojas') {
      await responderJson(route, [{ nome: 'JK Pecas' }]);
      return;
    }
    if (url.pathname === '/api/medias-compras/preferencias-skus-ocultos') {
      await responderJson(route, { skus_ocultos: [] });
      return;
    }
    if (url.pathname === '/api/medias-compras/visao') {
      const periodoSolicitado = Number(url.searchParams.get('meses') || 12);
      const periodo = [3, 6, 12].includes(periodoSolicitado) ? periodoSolicitado : 12;
      const meses = mesesDoPeriodo(periodo);
      const atrasoMs = Math.max(0, Number(controle.atrasosMs[periodo] || 0));
      if (atrasoMs > 0) await new Promise(resolve => setTimeout(resolve, atrasoMs));
      const itens = controle.periodosVazios.has(periodo)
        ? []
        : Array.from(
          { length: controle.quantidadeItens },
          (_valor, indice) => criarItem(indice, meses, controle.variante),
        );
      await responderJson(route, {
        colunas_meses: meses.map(key => ({ key })),
        itens,
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
  });
}

async function aguardarPeriodo(page, periodo, quantidadeItens) {
  await page.waitForFunction(({ periodoEsperado, itensEsperados, colunasEsperadas }) => {
    const app = window.JKMedias;
    const botao = document.getElementById(`btnPeriodo${periodoEsperado}`);
    const linhas = document.querySelectorAll('#tblResultado tbody tr').length;
    return Boolean(
      app
      && app.state
      && Number(app.state.periodoAtual) === periodoEsperado
      && Array.isArray(app.state.colunasMesesAtuais)
      && app.state.colunasMesesAtuais.length === periodoEsperado
      && Array.isArray(app.state.itensVisaoAtual)
      && app.state.itensVisaoAtual.length === itensEsperados
      && document.querySelectorAll('#tblResultado thead th[data-col-key]').length === colunasEsperadas
      && linhas === (itensEsperados > 0 ? itensEsperados : 1)
      && botao
      && botao.classList.contains('active')
    );
  }, {
    periodoEsperado: Number(periodo),
    itensEsperados: Number(quantidadeItens),
    colunasEsperadas: chavesDoPeriodo(periodo).length,
  });
}

async function abrirCenario(browser, opcoes = {}) {
  const clientId = opcoes.clientId || 'tenant-layout';
  const username = opcoes.username || 'usuario-layout';
  const controle = opcoes.controle || criarControle();
  const viewport = opcoes.viewport || { width: 1176, height: 768 };
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const errosPagina = [];
  page.on('pageerror', error => errosPagina.push(error.message));

  await page.addInitScript(({ clientIdInicial, usernameInicial, storageInicial }) => {
    if (!localStorage.getItem('user_data')) {
      localStorage.setItem('user_data', JSON.stringify({
        client_id: clientIdInicial,
        username: usernameInicial,
      }));
    }
    Object.entries(storageInicial || {}).forEach(([chave, valor]) => {
      if (localStorage.getItem(chave) === null) localStorage.setItem(chave, valor);
    });
    localStorage.setItem('access_token', 'medias-layout-browser-test');
  }, {
    clientIdInicial: clientId,
    usernameInicial: username,
    storageInicial: opcoes.storageInicial || {},
  });
  await instalarRotas(page, controle);
  await page.goto('http://jk.test/medias_compras.html');
  await aguardarPeriodo(
    page,
    12,
    controle.periodosVazios.has(12) ? 0 : controle.quantidadeItens,
  );

  return { context, page, controle, errosPagina, clientId, username };
}

async function executarCenario(browser, opcoes, executar) {
  const sessao = await abrirCenario(browser, opcoes);
  try {
    const resultado = await executar(sessao);
    assert.deepStrictEqual(
      sessao.errosPagina,
      [],
      `erros JavaScript inesperados: ${sessao.errosPagina.join(' | ')}`,
    );
    return resultado;
  } finally {
    await sessao.context.close();
  }
}

async function lerPerfil(page, periodo) {
  return page.evaluate((periodoAtual) => {
    const app = window.JKMedias;
    const tabelaColunas = app.modules['tabela-colunas'];
    const baseKey = app.state.LS_COL_WIDTHS_KEY;
    const storageKey = tabelaColunas.obterChaveLargurasColunas(periodoAtual);
    const raw = storageKey ? localStorage.getItem(storageKey) : null;
    let perfil = null;
    try {
      perfil = raw === null ? null : JSON.parse(raw);
    } catch (_error) {
      perfil = null;
    }
    const storageKeys = [];
    for (let indice = 0; indice < localStorage.length; indice += 1) {
      const chave = localStorage.key(indice);
      if (chave && chave.startsWith('medias_compras_larguras_colunas_v3_')) storageKeys.push(chave);
    }
    return {
      baseKey,
      storageKey,
      raw,
      perfil,
      userScopeKey: app.state.userScopeKey,
      storageKeys: storageKeys.sort(),
    };
  }, Number(periodo));
}

function assertPerfilValido(info, periodo, contexto, identidade = {}) {
  assert.match(
    info.baseKey,
    /^medias_compras_larguras_colunas_v3_[0-9a-f]{16}$/,
    `${contexto}: a chave base deve usar o hash v3 por usuario`,
  );
  assert.strictEqual(
    info.storageKey,
    `${info.baseKey}_${periodo}m`,
    `${contexto}: a chave deve terminar com o periodo ${periodo}m`,
  );
  if (identidade.username) {
    assert.ok(!info.storageKey.includes(identidade.username), `${contexto}: a chave nao deve expor o username`);
  }
  if (identidade.clientId) {
    assert.ok(!info.storageKey.includes(identidade.clientId), `${contexto}: a chave nao deve expor o tenant`);
  }
  assert.ok(info.perfil, `${contexto}: perfil persistido ausente`);
  assert.strictEqual(info.perfil.schema, 'jk.medias.column-widths.v3', `${contexto}: schema inesperado`);
  assert.strictEqual(info.perfil.period, Number(periodo), `${contexto}: periodo persistido incorreto`);
  assert.strictEqual(info.perfil.initialized, true, `${contexto}: perfil deve estar inicializado`);
  assert.ok(info.perfil.widths && typeof info.perfil.widths === 'object', `${contexto}: mapa de larguras ausente`);

  const chavesEsperadas = chavesDoPeriodo(periodo).slice().sort();
  const chavesPersistidas = Object.keys(info.perfil.widths).slice().sort();
  assert.deepStrictEqual(chavesPersistidas, chavesEsperadas, `${contexto}: mapa deve conter somente colunas posicionais do periodo`);
  assert.ok(
    chavesPersistidas.filter(chave => chave.startsWith('mes_')).every(chave => /^mes_\d+$/.test(chave)),
    `${contexto}: meses persistidos devem usar chaves posicionais`,
  );
}

async function lerMetricas(page, periodo) {
  const perfilInfo = await lerPerfil(page, periodo);
  return page.evaluate(({ minimosEsperados, perfil }) => {
    function contarLinhas(elemento) {
      const range = document.createRange();
      range.selectNodeContents(elemento);
      const tops = Array.from(range.getClientRects())
        .filter(rect => rect.width > 0 && rect.height > 0)
        .map(rect => Math.round(rect.top * 10) / 10);
      return new Set(tops).size;
    }

    const headers = Array.from(document.querySelectorAll('#tblResultado thead th[data-col-key]'));
    const larguras = Object.fromEntries(headers.map((th) => [
      th.dataset.colKey,
      th.getBoundingClientRect().width,
    ]));
    const linhasCabecalhos = Object.fromEntries(headers.map((th) => {
      const label = th.querySelector('.cabecalho-coluna-label') || th;
      return [th.dataset.colKey, contarLinhas(label)];
    }));
    const palavrasCabecalhos = Object.fromEntries(headers.map((th) => [
      th.dataset.colKey,
      String(th.textContent || '').trim().split(/\s+/).filter(Boolean).length,
    ]));
    const spansMeses = Array.from(document.querySelectorAll(
      '#tblResultado thead th.th-mes .mes-ano-topo, #tblResultado thead th.th-mes .mes-nome-base',
    ));
    const wrap = document.getElementById('secaoTabelaPrincipal');
    const tabela = document.getElementById('tblResultado');
    const headRow = document.getElementById('headRow');
    const doc = document.documentElement;
    const container = document.querySelector('.container');
    const card = wrap.closest('.card');
    const menuCard = document.querySelector('.card.menu-compacto');
    const home = document.querySelector('.topbar-nav-card.home');
    const bodyStyle = getComputedStyle(document.body);
    const cardStyle = getComputedStyle(card);
    const containerRect = container.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    const menuCardRect = menuCard.getBoundingClientRect();
    const wrapRect = wrap.getBoundingClientRect();
    const tableRect = tabela.getBoundingClientRect();
    const homeRect = home.getBoundingClientRect();
    const bodyPaddingLeft = Number.parseFloat(bodyStyle.paddingLeft || '0');
    const bodyPaddingRight = Number.parseFloat(bodyStyle.paddingRight || '0');
    const cardInsetHorizontal = [
      cardStyle.paddingLeft,
      cardStyle.paddingRight,
      cardStyle.borderLeftWidth,
      cardStyle.borderRightWidth,
    ].reduce((total, valor) => total + Number.parseFloat(valor || '0'), 0);

    return {
      chavesEsperadas: Object.keys(minimosEsperados),
      larguras,
      linhasCabecalhos,
      palavrasCabecalhos,
      spansMeses: spansMeses.map((span) => ({
        texto: String(span.textContent || '').trim(),
        linhas: contarLinhas(span),
        clientWidth: span.clientWidth,
        scrollWidth: span.scrollWidth,
      })),
      alturaCabecalho: headRow.getBoundingClientRect().height,
      overflowX: getComputedStyle(wrap).overflowX,
      overflowY: getComputedStyle(wrap).overflowY,
      wrapClientWidth: wrap.clientWidth,
      wrapScrollWidth: wrap.scrollWidth,
      wrapClientHeight: wrap.clientHeight,
      wrapScrollHeight: wrap.scrollHeight,
      tabelaMinWidth: Number.parseFloat(tabela.style.minWidth || '0'),
      tabelaRenderedWidth: tableRect.width,
      documentScrollWidth: doc.scrollWidth,
      documentClientWidth: doc.clientWidth,
      viewportWidth: innerWidth,
      bodyPaddingLeft,
      bodyPaddingRight,
      larguraUtilPagina: doc.clientWidth - bodyPaddingLeft - bodyPaddingRight,
      containerLeft: containerRect.left,
      containerRight: containerRect.right,
      containerWidth: containerRect.width,
      cardWidth: cardRect.width,
      menuCardWidth: menuCardRect.width,
      cardContentWidth: cardRect.width - cardInsetHorizontal,
      wrapLeft: wrapRect.left,
      wrapRight: wrapRect.right,
      wrapRenderedWidth: wrapRect.width,
      homeLeft: homeRect.left,
      homeRight: homeRect.right,
      largurasPersistidas: perfil && perfil.widths ? perfil.widths : {},
    };
  }, {
    minimosEsperados: minimosDoPeriodo(periodo),
    perfil: perfilInfo.perfil,
  });
}

function validarMetricas(metricas, contexto, opcoes = {}) {
  const tolerancia = 1;
  const minimos = minimosDoPeriodo(opcoes.periodo || 12);
  for (const [chave, minimo] of Object.entries(minimos)) {
    assert.ok(
      Object.prototype.hasOwnProperty.call(metricas.larguras, chave),
      `${contexto}: cabecalho ausente para ${chave}`,
    );
    assert.ok(
      metricas.larguras[chave] >= minimo - tolerancia,
      `${contexto}: ${chave} ficou abaixo do minimo (${metricas.larguras[chave]}px)`,
    );
    assert.ok(
      Number(metricas.largurasPersistidas[chave]) >= minimo,
      `${contexto}: ${chave} permaneceu invalida no perfil`,
    );
  }

  assert.strictEqual(
    metricas.spansMeses.length,
    Number(opcoes.periodo || 12) * 2,
    `${contexto}: quantidade inesperada de fragmentos mensais`,
  );
  for (const span of metricas.spansMeses) {
    assert.strictEqual(span.linhas, 1, `${contexto}: o fragmento mensal "${span.texto}" quebrou em mais de uma linha`);
    assert.ok(
      span.scrollWidth <= span.clientWidth + tolerancia,
      `${contexto}: o fragmento mensal "${span.texto}" foi recortado`,
    );
  }

  for (const chave of [
    'saldo_estoque',
    'estoque_transito',
    'posicao_estoque',
    'cobertura_meses',
    'compra_sugerida',
  ]) {
    assert.ok(
      metricas.linhasCabecalhos[chave] <= metricas.palavrasCabecalhos[chave],
      `${contexto}: ${chave} quebrou palavras em letras`,
    );
  }

  assert.ok(metricas.alturaCabecalho <= 110, `${contexto}: cabecalho ficou alto demais`);
  assert.ok(
    ['auto', 'scroll'].includes(metricas.overflowX),
    `${contexto}: a rolagem horizontal deve ficar contida na tabela`,
  );
  assert.ok(
    ['auto', 'scroll'].includes(metricas.overflowY),
    `${contexto}: a rolagem vertical deve manter o cabecalho fixo`,
  );
  if (opcoes.exigeOverflowHorizontal) {
    assert.ok(
      metricas.wrapScrollWidth > metricas.wrapClientWidth,
      `${contexto}: a tabela deve usar rolagem horizontal interna quando nao couber`,
    );
  }
  if (opcoes.exigeOverflowVertical !== false) {
    assert.ok(
      metricas.wrapScrollHeight > metricas.wrapClientHeight,
      `${contexto}: a grade extensa deve usar rolagem vertical interna`,
    );
  }
  assert.ok(
    metricas.documentScrollWidth <= metricas.viewportWidth + tolerancia,
    `${contexto}: a tabela vazou horizontalmente para a pagina`,
  );
}

function validarPaginaUsaLarguraTela(metricas, contexto) {
  const tolerancia = 2;
  assert.ok(
    Math.abs(metricas.containerWidth - metricas.larguraUtilPagina) <= tolerancia,
    `${contexto}: o container usa ${metricas.containerWidth}px de ${metricas.larguraUtilPagina}px disponiveis`,
  );
  assert.ok(
    Math.abs(metricas.containerLeft - metricas.bodyPaddingLeft) <= tolerancia,
    `${contexto}: a borda esquerda do container nao acompanha o padding da pagina`,
  );
  assert.ok(
    Math.abs((metricas.documentClientWidth - metricas.containerRight) - metricas.bodyPaddingRight) <= tolerancia,
    `${contexto}: a borda direita do container nao acompanha a largura da pagina`,
  );
  assert.ok(
    Math.abs(metricas.cardWidth - metricas.containerWidth) <= tolerancia,
    `${contexto}: o card principal nao ocupa toda a largura do container`,
  );
  assert.ok(
    Math.abs(metricas.menuCardWidth - metricas.containerWidth) <= tolerancia,
    `${contexto}: o card superior nao ocupa toda a largura do container`,
  );
  assert.ok(
    Math.abs(metricas.wrapRenderedWidth - metricas.cardContentWidth) <= tolerancia,
    `${contexto}: a area rolavel da tabela nao ocupa toda a area interna do card`,
  );
  assert.ok(
    metricas.documentScrollWidth <= metricas.documentClientWidth + tolerancia,
    `${contexto}: o redimensionamento criou rolagem horizontal na pagina`,
  );
  assert.ok(
    metricas.homeLeft >= -tolerancia && metricas.homeRight <= metricas.documentClientWidth + tolerancia,
    `${contexto}: o atalho Home saiu da largura visivel da pagina`,
  );
}

function assertLargurasAproximadas(atual, esperada, contexto, tolerancia = 2) {
  for (const [chave, larguraEsperada] of Object.entries(esperada)) {
    assert.ok(
      Math.abs(Number(atual[chave]) - Number(larguraEsperada)) <= tolerancia,
      `${contexto}: ${chave} mudou de ${larguraEsperada}px para ${atual[chave]}px`,
    );
  }
}

async function arrastarColuna(page, colKey, deltaX) {
  const cabecalho = page.locator(`#tblResultado thead th[data-col-key="${colKey}"]`);
  await cabecalho.scrollIntoViewIfNeeded();
  const larguraAntes = await cabecalho.evaluate(th => th.getBoundingClientRect().width);
  const grip = cabecalho.locator('.col-resizer');
  const caixaGrip = await grip.boundingBox();
  assert.ok(caixaGrip, `o redimensionador de ${colKey} deve estar visivel`);
  const xInicial = caixaGrip.x + (caixaGrip.width / 2);
  const y = caixaGrip.y + (caixaGrip.height / 2);
  await page.mouse.move(xInicial, y);
  await page.mouse.down();
  await page.mouse.move(xInicial + deltaX, y, { steps: 6 });
  await page.mouse.up();
  const larguraDepois = await cabecalho.evaluate(th => th.getBoundingClientRect().width);
  return { larguraAntes, larguraDepois };
}

async function arrastarColunaAteEsquerda(page, colKey) {
  const cabecalho = page.locator(`#tblResultado thead th[data-col-key="${colKey}"]`);
  await cabecalho.scrollIntoViewIfNeeded();
  const grip = cabecalho.locator('.col-resizer');
  const caixaGrip = await grip.boundingBox();
  assert.ok(caixaGrip, `o redimensionador de ${colKey} deve continuar visivel`);
  const y = caixaGrip.y + (caixaGrip.height / 2);
  await page.mouse.move(caixaGrip.x + (caixaGrip.width / 2), y);
  await page.mouse.down();
  await page.mouse.move(0, y, { steps: 6 });
  await page.mouse.up();
}

async function trocarPeriodo(page, periodo, quantidadeItens = 24) {
  await page.locator(`#btnPeriodo${periodo}`).click();
  await aguardarPeriodo(page, periodo, quantidadeItens);
}

async function trocarUsuario(page, clientId, username, quantidadeItens = 24) {
  await page.evaluate(({ clientIdNovo, usernameNovo }) => {
    localStorage.setItem('user_data', JSON.stringify({
      client_id: clientIdNovo,
      username: usernameNovo,
    }));
  }, { clientIdNovo: clientId, usernameNovo: username });
  await page.reload();
  await aguardarPeriodo(page, 12, quantidadeItens);
}

async function validarCabecalhoFixo(page) {
  const topoWrap = await page.locator('#secaoTabelaPrincipal').evaluate(
    wrap => wrap.getBoundingClientRect().top,
  );
  await page.locator('#secaoTabelaPrincipal').evaluate((wrap) => {
    wrap.scrollTop = 520;
    wrap.scrollLeft = 240;
  });
  await page.waitForTimeout(50);
  const topoCabecalhoDuranteScroll = await page.locator('#headRow th').first().evaluate(
    th => th.getBoundingClientRect().top,
  );
  assert.ok(
    Math.abs(topoCabecalhoDuranteScroll - topoWrap) <= 2,
    'o cabecalho deve permanecer fixo no topo da tabela',
  );
  await page.locator('#secaoTabelaPrincipal').evaluate((wrap) => {
    wrap.scrollTop = 0;
    wrap.scrollLeft = 0;
  });
}

async function testarAutoFitPrimeiroUso(browser) {
  const curto = await executarCenario(browser, {
    username: 'auto-fit-curto',
    controle: criarControle({ variante: 'curto' }),
  }, async ({ page, clientId, username }) => {
    const perfil = await lerPerfil(page, 12);
    assertPerfilValido(perfil, 12, 'auto-fit curto', { clientId, username });
    return perfil.perfil.widths;
  });

  const longo = await executarCenario(browser, {
    username: 'auto-fit-longo',
    controle: criarControle({ variante: 'longo' }),
  }, async ({ page, clientId, username }) => {
    const perfil = await lerPerfil(page, 12);
    assertPerfilValido(perfil, 12, 'auto-fit longo', { clientId, username });
    const metricas = await lerMetricas(page, 12);
    validarMetricas(metricas, 'auto-fit longo', { periodo: 12, exigeOverflowHorizontal: true });
    return perfil.perfil.widths;
  });

  assert.ok(
    longo.titulo_anuncio >= curto.titulo_anuncio + 60,
    'o auto-fit deve ampliar a coluna de titulo quando o conteudo for maior',
  );
  assert.ok(
    longo.sku >= curto.sku + 40,
    'o auto-fit deve ampliar a coluna de SKU quando o conteudo for maior',
  );
  assert.ok(
    longo.mes_0 >= longo.mes_1 + 12,
    'o auto-fit deve medir o valor da celula mensal pela posicao da coluna',
  );
}

async function testarCoberturaNumericaDepoisDeSemVenda(browser) {
  await executarCenario(browser, {
    username: 'cobertura-mista',
    controle: criarControle({ variante: 'cobertura-mista' }),
  }, async ({ page, clientId, username }) => {
    const perfil = await lerPerfil(page, 12);
    assertPerfilValido(perfil, 12, 'cobertura mista', { clientId, username });
    const cobertura = await page.evaluate(() => {
      const celulas = Array.from(document.querySelectorAll(
        '#tblResultado tbody td.col-cobertura-estoque',
      ));
      const primeira = celulas[0];
      const numerica = celulas[1];
      return {
        primeiraTexto: String(primeira && primeira.textContent || '').trim(),
        primeiraTemSemVenda: Boolean(primeira && primeira.querySelector('.cobertura-sem-venda')),
        numericaTexto: String(numerica && numerica.textContent || '').trim(),
        numericaTemStrong: Boolean(numerica && numerica.querySelector('strong')),
        clientWidth: numerica ? numerica.clientWidth : 0,
        scrollWidth: numerica ? numerica.scrollWidth : 0,
      };
    });

    assert.strictEqual(cobertura.primeiraTexto, 'Sem venda', 'a primeira linha deve exercitar a metrica Sem venda');
    assert.strictEqual(cobertura.primeiraTemSemVenda, true, 'a primeira cobertura deve usar o marcador Sem venda');
    assert.strictEqual(cobertura.numericaTemStrong, true, 'a linha posterior deve renderizar cobertura numerica');
    assert.ok(cobertura.numericaTexto.length >= 12, 'a cobertura posterior deve ser numericamente longa');
    assert.ok(
      cobertura.scrollWidth <= cobertura.clientWidth + 1,
      `a cobertura numerica nao pode ser recortada: scroll=${cobertura.scrollWidth}, client=${cobertura.clientWidth}`,
    );
  });
}

async function testarReloadPreservaAutoFit(browser) {
  const controle = criarControle({ variante: 'longo' });
  await executarCenario(browser, { username: 'reload-auto-fit', controle }, async ({ page, clientId, username }) => {
    const perfilAntes = await lerPerfil(page, 12);
    const metricasAntes = await lerMetricas(page, 12);
    assertPerfilValido(perfilAntes, 12, 'antes do reload', { clientId, username });

    controle.variante = 'curto';
    await page.reload();
    await aguardarPeriodo(page, 12, controle.quantidadeItens);

    const perfilDepois = await lerPerfil(page, 12);
    const metricasDepois = await lerMetricas(page, 12);
    assert.deepStrictEqual(
      perfilDepois.perfil,
      perfilAntes.perfil,
      'reload nao deve recalcular um perfil ja inicializado',
    );
    assertLargurasAproximadas(
      metricasDepois.larguras,
      metricasAntes.larguras,
      'reload do perfil autoajustado',
    );
    validarMetricas(metricasDepois, 'reload do perfil autoajustado', {
      periodo: 12,
      exigeOverflowHorizontal: true,
    });
  });
}

async function testarPeriodosIndependentes(browser) {
  await executarCenario(browser, {
    username: 'periodos-independentes',
    controle: criarControle({ variante: 'normal' }),
  }, async ({ page, clientId, username }) => {
    const resize12 = await arrastarColuna(page, 'titulo_anuncio', 120);
    assert.ok(
      resize12.larguraDepois >= resize12.larguraAntes + 80,
      'resize manual de 12 meses deve aumentar a coluna de forma material',
    );
    const perfil12 = await lerPerfil(page, 12);
    assertPerfilValido(perfil12, 12, 'perfil manual de 12 meses', { clientId, username });

    await trocarPeriodo(page, 6);
    const perfil6Auto = await lerPerfil(page, 6);
    assertPerfilValido(perfil6Auto, 6, 'primeiro uso de 6 meses', { clientId, username });
    assert.ok(
      Math.abs(perfil6Auto.perfil.widths.titulo_anuncio - perfil12.perfil.widths.titulo_anuncio) >= 50,
      'o primeiro uso de 6 meses nao deve herdar o resize manual de 12 meses',
    );

    const resize6 = await arrastarColuna(page, 'titulo_anuncio', 45);
    assert.ok(
      resize6.larguraDepois >= resize6.larguraAntes + 25,
      'resize manual de 6 meses deve aumentar a coluna de forma material',
    );
    const perfil6 = await lerPerfil(page, 6);
    assertPerfilValido(perfil6, 6, 'perfil manual de 6 meses', { clientId, username });
    assert.notStrictEqual(perfil6.storageKey, perfil12.storageKey, '6 e 12 meses devem usar chaves distintas');

    await trocarPeriodo(page, 3);
    const perfil3 = await lerPerfil(page, 3);
    assertPerfilValido(perfil3, 3, 'primeiro uso de 3 meses', { clientId, username });
    assert.notStrictEqual(perfil3.storageKey, perfil6.storageKey, '3 e 6 meses devem usar chaves distintas');
    assert.notStrictEqual(perfil3.storageKey, perfil12.storageKey, '3 e 12 meses devem usar chaves distintas');

    const perfil12DepoisDoResize6 = await lerPerfil(page, 12);
    assert.deepStrictEqual(
      perfil12DepoisDoResize6.perfil,
      perfil12.perfil,
      'resize de 6 meses nao deve alterar o perfil de 12 meses',
    );

    await trocarPeriodo(page, 12);
    let metricas = await lerMetricas(page, 12);
    assert.ok(
      Math.abs(metricas.larguras.titulo_anuncio - perfil12.perfil.widths.titulo_anuncio) <= 2,
      'voltar para 12 meses deve restaurar a largura configurada nesse modo',
    );
    validarMetricas(metricas, 'restauracao de 12 meses', { periodo: 12, exigeOverflowHorizontal: true });

    await trocarPeriodo(page, 6);
    metricas = await lerMetricas(page, 6);
    assert.ok(
      Math.abs(metricas.larguras.titulo_anuncio - perfil6.perfil.widths.titulo_anuncio) <= 2,
      'voltar para 6 meses deve restaurar a largura configurada nesse modo',
    );
    validarMetricas(metricas, 'restauracao de 6 meses', { periodo: 6 });
  });
}

async function testarPerfilParcialRefazAutoFit(browser) {
  const clientId = 'tenant-perfil-parcial';
  const username = 'usuario-perfil-parcial';
  const periodo = 12;
  const widthsParciais = {
    sku: 80,
    foto: 64,
    titulo_anuncio: 200,
    total_periodo: 72,
    media_mensal: 56,
    estoque_transito: 80,
    cobertura_meses: 92,
    acao: 100,
  };
  for (let indice = 0; indice < periodo; indice += 1) widthsParciais[`mes_${indice}`] = 52;
  const chave = chavePerfilUsuario(clientId, username, periodo);
  const storageInicial = {
    [chave]: JSON.stringify({
      schema: 'jk.medias.column-widths.v3',
      period: periodo,
      initialized: true,
      widths: widthsParciais,
    }),
  };

  await executarCenario(browser, {
    clientId,
    username,
    storageInicial,
    controle: criarControle({ variante: 'longo' }),
  }, async ({ page }) => {
    const perfil = await lerPerfil(page, periodo);
    assertPerfilValido(perfil, periodo, 'perfil parcial reparado', { clientId, username });
    assert.ok(
      perfil.perfil.widths.titulo_anuncio >= widthsParciais.titulo_anuncio + 60,
      'perfil schema-correto mas parcial deve refazer o auto-fit completo',
    );
    for (const colKey of ['saldo_estoque', 'posicao_estoque', 'compra_sugerida']) {
      assert.ok(
        perfil.perfil.widths[colKey] >= minimosBase[colKey] + 12,
        `perfil parcial deve medir o conteudo da coluna ausente ${colKey}`,
      );
    }
  });
}

async function testarCorridaRapidaPeriodos(browser) {
  const controle = criarControle({ variante: 'normal' });
  await executarCenario(browser, {
    username: 'corrida-periodos',
    controle,
  }, async ({ page }) => {
    const perfil12Antes = await lerPerfil(page, 12);
    controle.atrasosMs[6] = 180;
    controle.atrasosMs[12] = 20;

    await page.evaluate(() => {
      const carregarVisao = window.JKMedias.modules.tabela.carregarVisao;
      void carregarVisao(6);
      void carregarVisao(12);
    });
    await page.waitForTimeout(260);
    await aguardarPeriodo(page, 12, controle.quantidadeItens);

    const perfil12Depois = await lerPerfil(page, 12);
    const perfil6 = await lerPerfil(page, 6);
    assert.deepStrictEqual(
      perfil12Depois.perfil,
      perfil12Antes.perfil,
      'a corrida 12-6-12 deve terminar no perfil original de 12 meses',
    );
    assert.strictEqual(perfil6.raw, null, 'resposta obsoleta de 6 meses nao deve inicializar perfil');
    const chavesAtuais = await page.locator('#tblResultado thead th[data-col-key]').evaluateAll(
      ths => ths.map(th => th.dataset.colKey),
    );
    assert.deepStrictEqual(chavesAtuais, chavesDoPeriodo(12), 'a resposta obsoleta nao deve substituir as colunas finais');
  });
}

async function testarUsuariosIndependentes(browser) {
  const clientId = 'tenant-compartilhado';
  const usuarioA = 'ana-layout';
  const usuarioB = 'bruno-layout';
  await executarCenario(browser, {
    clientId,
    username: usuarioA,
    controle: criarControle({ variante: 'normal' }),
  }, async ({ page }) => {
    const larguraAutoA = (await lerMetricas(page, 12)).larguras.titulo_anuncio;
    const resizeA = await arrastarColuna(page, 'titulo_anuncio', 120);
    assert.ok(resizeA.larguraDepois >= larguraAutoA + 80, 'usuario A deve conseguir configurar a propria largura');
    const perfilA = await lerPerfil(page, 12);
    assertPerfilValido(perfilA, 12, 'perfil do usuario A', { clientId, username: usuarioA });

    await trocarUsuario(page, clientId, usuarioB);
    const perfilBAuto = await lerPerfil(page, 12);
    assertPerfilValido(perfilBAuto, 12, 'primeiro uso do usuario B', { clientId, username: usuarioB });
    assert.notStrictEqual(perfilBAuto.baseKey, perfilA.baseKey, 'usuarios do mesmo tenant devem ter hashes distintos');
    assert.ok(
      Math.abs(perfilBAuto.perfil.widths.titulo_anuncio - perfilA.perfil.widths.titulo_anuncio) >= 50,
      'usuario B nao deve herdar a largura manual do usuario A',
    );

    const resizeB = await arrastarColuna(page, 'titulo_anuncio', 45);
    assert.ok(resizeB.larguraDepois >= resizeB.larguraAntes + 25, 'usuario B deve conseguir configurar a propria largura');
    const perfilB = await lerPerfil(page, 12);
    assert.ok(
      Math.abs(perfilB.perfil.widths.titulo_anuncio - perfilA.perfil.widths.titulo_anuncio) >= 30,
      'as larguras manuais dos dois usuarios devem permanecer distintas',
    );
    assert.ok(perfilB.storageKeys.includes(perfilA.storageKey), 'o perfil do usuario A deve continuar no mesmo BrowserContext');
    assert.ok(perfilB.storageKeys.includes(perfilB.storageKey), 'o perfil do usuario B deve existir no mesmo BrowserContext');

    await trocarUsuario(page, clientId, usuarioA);
    let metricas = await lerMetricas(page, 12);
    assert.ok(
      Math.abs(metricas.larguras.titulo_anuncio - perfilA.perfil.widths.titulo_anuncio) <= 2,
      'retornar ao usuario A deve restaurar o perfil A',
    );

    await trocarUsuario(page, clientId, usuarioB);
    metricas = await lerMetricas(page, 12);
    assert.ok(
      Math.abs(metricas.larguras.titulo_anuncio - perfilB.perfil.widths.titulo_anuncio) <= 2,
      'retornar ao usuario B deve restaurar o perfil B',
    );
  });
}

async function testarRespostaVaziaNaoInicializa(browser) {
  const controle = criarControle({ variante: 'longo', periodosVazios: [12] });
  await executarCenario(browser, {
    username: 'resposta-vazia',
    controle,
  }, async ({ page, clientId, username }) => {
    const perfilVazio = await lerPerfil(page, 12);
    assert.match(
      perfilVazio.storageKey,
      /^medias_compras_larguras_colunas_v3_[0-9a-f]{16}_12m$/,
      'o escopo do perfil deve existir mesmo antes de haver dados',
    );
    assert.strictEqual(perfilVazio.raw, null, 'resposta vazia nao deve inicializar o perfil de larguras');

    controle.periodosVazios.delete(12);
    await page.evaluate(() => window.JKMedias.modules.tabela.carregarVisao(12));
    await aguardarPeriodo(page, 12, controle.quantidadeItens);

    const perfilComDados = await lerPerfil(page, 12);
    assertPerfilValido(perfilComDados, 12, 'primeira resposta com dados', { clientId, username });
    const metricas = await lerMetricas(page, 12);
    validarMetricas(metricas, 'auto-fit depois da resposta vazia', {
      periodo: 12,
      exigeOverflowHorizontal: true,
    });
  });
}

async function testarPaginaAcompanhaViewport(browser) {
  const controle = criarControle({ variante: 'curto' });
  await executarCenario(browser, {
    username: 'pagina-responsiva',
    controle,
    viewport: { width: 1920, height: 900 },
  }, async ({ page }) => {
    const perfil12Antes = await lerPerfil(page, 12);
    const metricas12Largas = await lerMetricas(page, 12);
    validarPaginaUsaLarguraTela(metricas12Largas, 'viewport 1920px em 12 meses');

    await trocarPeriodo(page, 3);
    const perfil3Antes = await lerPerfil(page, 3);
    const viewports = [
      { width: 2560, height: 1080 },
      { width: 1920, height: 900 },
      { width: 1600, height: 800 },
      { width: 1366, height: 768 },
      { width: 768, height: 768 },
      { width: 480, height: 720 },
      { width: 1024, height: 768 },
    ];
    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      await page.waitForTimeout(20);
      const metricas = await lerMetricas(page, 3);
      validarPaginaUsaLarguraTela(metricas, `viewport ${viewport.width}px em 3 meses`);
      validarMetricas(metricas, `viewport ${viewport.width}px em 3 meses`, { periodo: 3 });
      assert.strictEqual(
        (await lerPerfil(page, 3)).raw,
        perfil3Antes.raw,
        `redimensionar para ${viewport.width}px nao deve regravar o perfil de 3 meses`,
      );
      if (viewport.width === 1920) {
        assertLargurasAproximadas(
          metricas.larguras,
          perfil3Antes.perfil.widths,
          'viewport largo deve preservar cada largura configurada em 3 meses',
        );
        assert.ok(
          metricas.wrapScrollWidth <= metricas.wrapClientWidth + 2,
          '3 meses com conteudo curto deve caber integralmente na tela larga',
        );
        assert.ok(
          metricas.tabelaRenderedWidth <= metricas.wrapClientWidth + 2,
          'a tabela curta deve caber dentro da area disponivel na tela larga',
        );
        assert.ok(
          Math.abs(metricas.tabelaRenderedWidth - metricas.tabelaMinWidth) <= 2,
          'a tela larga nao deve esticar as larguras persistidas da tabela curta',
        );
      }
    }

    await trocarPeriodo(page, 12);
    const perfil12Depois = await lerPerfil(page, 12);
    assert.strictEqual(
      perfil12Depois.raw,
      perfil12Antes.raw,
      'redimensionar a tela nao deve sobrescrever o perfil de 12 meses',
    );
    const metricas12Estreitas = await lerMetricas(page, 12);
    validarPaginaUsaLarguraTela(metricas12Estreitas, 'viewport 1024px em 12 meses');
    validarMetricas(metricas12Estreitas, 'viewport 1024px em 12 meses', {
      periodo: 12,
      exigeOverflowHorizontal: true,
    });
    await validarCabecalhoFixo(page);

    await page.setViewportSize({ width: 1920, height: 900 });
    await trocarPeriodo(page, 3);
    const perfil3Depois = await lerPerfil(page, 3);
    assert.strictEqual(
      perfil3Depois.raw,
      perfil3Antes.raw,
      'redimensionar a tela nao deve sobrescrever o perfil de 3 meses',
    );
    validarPaginaUsaLarguraTela(await lerMetricas(page, 3), 'retorno ao viewport 1920px');
  });
}

async function testarLayoutResizeESticky(browser) {
  await executarCenario(browser, {
    username: 'layout-resize-sticky',
    controle: criarControle({ variante: 'normal' }),
  }, async ({ page }) => {
    const metricasIniciais = await lerMetricas(page, 12);
    validarMetricas(metricasIniciais, 'layout inicial', { periodo: 12, exigeOverflowHorizontal: true });

    const resize = await arrastarColuna(page, 'saldo_estoque', 60);
    const metricasAposAumento = await lerMetricas(page, 12);
    validarMetricas(metricasAposAumento, 'arrasto para aumentar', { periodo: 12, exigeOverflowHorizontal: true });
    assert.ok(
      resize.larguraDepois >= resize.larguraAntes + 40,
      'o redimensionador deve permitir aumentar a coluna de estoque',
    );
    assert.ok(
      metricasAposAumento.tabelaMinWidth >= metricasIniciais.tabelaMinWidth + 40,
      'a largura minima total deve acompanhar o redimensionamento',
    );

    await arrastarColunaAteEsquerda(page, 'saldo_estoque');
    const metricasAposArrastoExtremo = await lerMetricas(page, 12);
    validarMetricas(metricasAposArrastoExtremo, 'arrasto extremo', { periodo: 12, exigeOverflowHorizontal: true });
    assert.ok(
      metricasAposArrastoExtremo.larguras.saldo_estoque <= metricasIniciais.larguras.saldo_estoque + 2,
      'o arrasto extremo deve respeitar o minimo da coluna',
    );
    assert.ok(
      metricasAposArrastoExtremo.tabelaMinWidth <= metricasAposAumento.tabelaMinWidth - 40,
      'a largura minima total deve diminuir junto com a coluna redimensionada',
    );

    await validarCabecalhoFixo(page);

    if (process.env.JK_MEDIAS_LAYOUT_SCREENSHOT) {
      await page.setViewportSize({ width: 1800, height: 768 });
      await page.evaluate(() => { document.getElementById('secaoTabelaPrincipal').scrollLeft = 0; });
      await page.screenshot({ path: process.env.JK_MEDIAS_LAYOUT_SCREENSHOT, fullPage: true });
    }
  });
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    await testarAutoFitPrimeiroUso(browser);
    await testarCoberturaNumericaDepoisDeSemVenda(browser);
    await testarReloadPreservaAutoFit(browser);
    await testarPeriodosIndependentes(browser);
    await testarPerfilParcialRefazAutoFit(browser);
    await testarCorridaRapidaPeriodos(browser);
    await testarUsuariosIndependentes(browser);
    await testarRespostaVaziaNaoInicializa(browser);
    await testarPaginaAcompanhaViewport(browser);
    await testarLayoutResizeESticky(browser);
    console.log('medias table layout browser: OK');
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
