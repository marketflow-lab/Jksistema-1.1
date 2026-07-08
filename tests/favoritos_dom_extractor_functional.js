const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const repoRoot = path.resolve(__dirname, '..');

function read(rel) {
  return fs.readFileSync(path.join(repoRoot, rel), 'utf8');
}

function htmlMercadoLivreFake() {
  const card = ({ id, title, price, image, seller, sales, monthly, created, position }) => `
    <li class="ui-search-layout__item" data-position="${position}">
      <article class="poly-card">
        <a class="poly-component__title" href="https://produto.mercadolivre.com.br/MLB-${id}-${title.replace(/\s+/g, '-')}-_JM" title="${title}">${title}</a>
        <img src="${image}" width="160" height="160" alt="${title}">
        <span class="andes-money-amount">R$ ${price}</span>
        <section class="avantpro-product-info">
          <div class="avantpro-product-info-row">
            <span class="avantpro-product-info-row-label">Vendas do produto</span>
            <span class="avantpro-product-info-row-value">${sales}</span>
          </div>
          <div class="avantpro-product-info-row">
            <span class="avantpro-product-info-row-label">Ritmo atual</span>
            <span class="avantpro-product-info-row-value">${monthly}</span>
          </div>
          <div class="avantpro-product-info-row">
            <span class="avantpro-product-info-row-label">Nome do vendedor</span>
            <span class="avantpro-product-info-row-value">${seller}</span>
          </div>
          <div class="avantpro-product-info-row">
            <span class="avantpro-product-info-row-label">Anuncio criado em</span>
            <span class="avantpro-product-info-row-value">${created}</span>
          </div>
        </section>
      </article>
    </li>
  `;
  return `<!doctype html>
    <html>
      <body>
        <main>
          <ol class="ui-search-layout">
            ${card({
              id: '1687891923',
              title: 'Cebolao Radiador Cebolinha Honda Cb500 Shadow Hornet Cbr900',
              price: '79,00',
              image: 'https://http2.mlstatic.com/D_NQ_NP_001.webp',
              seller: 'KARHUB AUTOPARTS',
              sales: '135',
              monthly: '4,94',
              created: '26/03/2024',
              position: 1
            })}
            ${card({
              id: '3512477523',
              title: 'Sensor Cebolao Ventoinha Radiador Cb 500 1997 1998 1999 2000',
              price: '42,00',
              image: 'https://http2.mlstatic.com/D_NQ_NP_002.webp',
              seller: 'AUTOPECASMOLINA',
              sales: '67',
              monthly: '0,83',
              created: '01/01/2020',
              position: 2
            })}
            ${card({
              id: '1758609649',
              title: 'Interruptor Termico Radiador Cb 600f Hornet 2004 2005 2006',
              price: '126,90',
              image: 'https://http2.mlstatic.com/D_NQ_NP_003.webp',
              seller: 'SUL PECAS DISTRIBUIDORA',
              sales: '7',
              monthly: '0,13',
              created: '10/05/2022',
              position: 3
            })}
          </ol>
        </main>
      </body>
    </html>`;
}

function htmlMercadoLivreFakePainelAvantFlutuanteBody() {
  const card = ({ id, title, price, image, position }) => `
    <li class="ui-search-layout__item" data-position="${position}">
      <article class="poly-card">
        <a class="poly-component__title" href="https://produto.mercadolivre.com.br/MLB-${id}-${title.replace(/\s+/g, '-')}-_JM" title="${title}">${title}</a>
        <img src="${image}" width="120" height="120" alt="${title}">
        <span class="andes-money-amount">R$ ${price}</span>
      </article>
    </li>
  `;
  const panel = ({ index, seller, sales, monthly, created }) => `
    <aside class="avant-floating-panel avant-floating-panel-${index}">
      <div class="avantpro-title">Informacoes Avantpro</div>
      <div class="metric-line">
        <span>Vendas do produto</span>
        <strong>${sales}</strong>
      </div>
      <div class="metric-line">
        <span>Ritmo atual (vendas/mes)</span>
        <strong>${monthly}</strong>
      </div>
      <div class="metric-line">
        <span>Nome do vendedor</span>
        <strong>${seller}</strong>
      </div>
      <div class="metric-line">
        <span>Anuncio criado em</span>
        <strong>${created}</strong>
      </div>
    </aside>
  `;
  return `
    <style>
      body { margin: 0; font-family: sans-serif; }
      .fixture-wrap { position: relative; min-height: 760px; width: 860px; }
      .ui-search-layout { list-style: none; margin: 0; padding: 0; width: 360px; }
      .ui-search-layout__item { position: relative; height: 230px; margin: 0 0 16px 0; border: 1px solid #ddd; }
      .poly-card { display: block; height: 210px; padding: 10px; width: 340px; }
      .poly-component__title { display: block; margin-bottom: 12px; }
      .andes-money-amount { display: block; margin-top: 8px; }
      .avant-floating-panel { position: absolute; left: 430px; width: 330px; min-height: 150px; border: 1px solid #7857ff; background: white; padding: 10px; }
      .avant-floating-panel-1 { top: 12px; }
      .avant-floating-panel-2 { top: 258px; }
      .avant-floating-panel-3 { top: 504px; }
      .metric-line { display: grid; grid-template-columns: 160px 1fr; gap: 8px; margin: 6px 0; }
    </style>
    <main class="fixture-wrap">
      <ol class="ui-search-layout">
        ${card({
          id: '1687891923',
          title: 'Cebolao Radiador Cebolinha Honda Cb500 Shadow Hornet Cbr900',
          price: '79,00',
          image: 'https://http2.mlstatic.com/D_NQ_NP_001.webp',
          position: 1
        })}
        ${card({
          id: '3512477523',
          title: 'Sensor Cebolao Ventoinha Radiador Cb 500 1997 1998 1999 2000',
          price: '42,00',
          image: 'https://http2.mlstatic.com/D_NQ_NP_002.webp',
          position: 2
        })}
        ${card({
          id: '1758609649',
          title: 'Interruptor Termico Radiador Cb 600f Hornet 2004 2005 2006',
          price: '126,90',
          image: 'https://http2.mlstatic.com/D_NQ_NP_003.webp',
          position: 3
        })}
      </ol>
      ${panel({
        index: 1,
        seller: 'KARHUB AUTOPARTS',
        sales: '135',
        monthly: '4,94',
        created: '26/03/2024'
      })}
      ${panel({
        index: 2,
        seller: 'AUTOPECASMOLINA',
        sales: '67',
        monthly: '0,83',
        created: '01/01/2020'
      })}
      ${panel({
        index: 3,
        seller: 'SUL PECAS DISTRIBUIDORA',
        sales: '7',
        monthly: '0,13',
        created: '10/05/2022'
      })}
    </main>
  `;
}

async function loadFavoritosScripts(page) {
  await page.addScriptTag({
    content: `
      window.electronAPI = {};
      localStorage.setItem('user_data', JSON.stringify({ username: 'codex', nome: 'Codex Teste' }));
      localStorage.setItem('permissions', JSON.stringify({ full: true, favoritos: true }));
      localStorage.setItem('access_token', 'codex-test-token');
      window.AVANT_PRO_LOGIN_EMAIL = '';
      window.AVANT_PRO_ESTABILIDADE_MIN_MS = 0;
      window.AVANT_PRO_ESTABILIDADE_MS = 250;
      window.AVANT_PRO_ESTABILIDADE_MAX_MS = 1000;
      window.AVANT_PRO_ESPERA_POS_CLIQUE_MS = 0;
      window.favMlLojaSelecionada = '';
      window.favMlSkuSelecionado = '';
      try { favMlLojaSelecionada = ''; } catch (_err) {}
      try { favMlSkuSelecionado = ''; } catch (_err) {}
      window.headersJsonAutenticado = function () { return {}; };
      window.sinalFavoritosAtual = function () { return undefined; };
      window.mostrarBalaoFavoritosStatus = function () {};
      window.esperar = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms || 0); }); };
    `
  });
  await page.addScriptTag({ content: read('static/favoritos/v2/browser/url-utils.js') });
  await page.addScriptTag({ content: read('static/favoritos/v2/browser/shell-bridge.js') });
  await page.addScriptTag({ content: read('static/favoritos/v2/browser/avant-cache.js') });
  await page.addScriptTag({ content: read('static/favoritos/ml-browser.js') });
  await page.addScriptTag({ content: read('static/favoritos/ranking.js') });
  await page.addScriptTag({ content: read('static/favoritos/promocoes-efetivacao.js') });
  await page.addScriptTag({ content: read('static/favoritos/tabelas-layout/01-ml-base-busca.js') });
  await page.addScriptTag({ content: read('static/favoritos/tabelas-layout/04-promocoes-busca-ranking.js') });
  await page.evaluate(() => {
    window.mlWebviewEl = {
      async executeJavaScript(script) {
        window.__JK_TEST_LAST_EXECUTE_SCRIPT__ = String(script || '').slice(0, 1200);
        window.__JK_TEST_EXECUTE_SCRIPTS__ = window.__JK_TEST_EXECUTE_SCRIPTS__ || [];
        window.__JK_TEST_EXECUTE_SCRIPTS__.push(String(script || ''));
        return (0, window.eval)(script);
      }
    };
    window.mlUrlInput = { value: '' };
    window.abrirMercadoLivreNoPrograma = async function () {
      window.__JK_TEST_ABRIU_ML_PROGRAMA__ = true;
      return true;
    };
    window.construirUrlPesquisaMercadoLivre = function (termo) {
      return 'https://lista.mercadolivre.com.br/' + encodeURIComponent(String(termo || '').trim());
    };
  });
  await page.addScriptTag({ content: 'mlWebviewEl = window.mlWebviewEl;' });
}

async function run() {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const pageErrors = [];
    page.on('pageerror', (err) => pageErrors.push(err && err.message ? err.message : String(err)));
    page.on('dialog', async (dialog) => {
      pageErrors.push(`dialog:${dialog.message()}`);
      await dialog.dismiss().catch(() => {});
    });
    await page.setContent(htmlMercadoLivreFake(), { waitUntil: 'domcontentloaded' });
    await loadFavoritosScripts(page);
    const preflight = await page.evaluate(() => ({
      mlWebviewEl: typeof mlWebviewEl,
      extrairCards: typeof extrairCardsMercadoLivreBasicoWebview,
      extrairAvant: typeof extrairAnunciosAvantProDomWebview,
      userData: typeof userData,
      href: location.href
    }));
    assert.equal(preflight.mlWebviewEl, 'object', `mlWebviewEl deve estar configurado no teste\n${JSON.stringify({ preflight, pageErrors }, null, 2)}`);

    const result = await page.evaluate(async () => {
      const base = await extrairCardsMercadoLivreBasicoWebview({ limite: 80 });
      const avant = await extrairAnunciosAvantProDomWebview({ limite: 80 });
      const merged = mesclarAnunciosAvant(base.anuncios, avant.anuncios);
      const controlada = await coletarPrimeiraPaginaFavoritosControlada({
        maxAnuncios: 20,
        tempoLimiteMs: 30000,
        maxPassadas: 1,
        loteCliques: 0
      });
      const wrapperNovo = await buscarAnunciosFavoritosPorTermo('cebolao sensor', {
        maxAnuncios: 20,
        tempoLimiteMs: 30000,
        maxPassadas: 1,
        loteCliques: 0
      });
      return {
        base,
        avant,
        merged,
        controlada,
        wrapperNovo,
        resumo: resumoPrimeiraPaginaFavoritos(base.total, merged),
        globals: {
          mlWebviewEl: typeof mlWebviewEl,
          runtimeUserData: typeof userData
        },
        lastScript: window.__JK_TEST_LAST_EXECUTE_SCRIPT__ || '',
        scripts: window.__JK_TEST_EXECUTE_SCRIPTS__ || []
      };
    });
    const debugResult = () => JSON.stringify({
      baseTotal: result.base && result.base.total,
      baseDebug: result.base && result.base.debug,
      baseError: result.base && result.base.error,
      lastScript: result.lastScript,
      globals: result.globals,
      pageErrors,
      scripts: result.scripts,
      baseIds: (result.base && result.base.anuncios || []).map(item => item.id || item.link),
      avantTotal: result.avant && result.avant.total,
      avantDebug: result.avant && result.avant.debug,
      avantIds: (result.avant && result.avant.anuncios || []).map(item => item.id || item.link),
      mergedIds: (result.merged || []).map(item => item.id || item.link),
      controladaTotal: result.controlada && result.controlada.totalVisiveis,
      controladaResumo: result.controlada && result.controlada.resumo,
      controladaIds: (result.controlada && result.controlada.anuncios || []).map(item => item.id || item.link),
      wrapperNovoIds: (result.wrapperNovo || []).map(item => item.id || item.link),
      resumo: result.resumo
    }, null, 2);
    if (result.base && result.base.error && Array.isArray(result.scripts) && result.scripts[0]) {
      fs.writeFileSync(path.join(repoRoot, '.tmp_favoritos_inner_base_actual.js'), result.scripts[0], 'utf8');
    }

    assert.equal(result.base.total, 3, `base ML deve coletar todos os cards da primeira pagina fake\n${debugResult()}`);
    assert.equal(result.avant.total, 3, `DOM Avant deve vincular dados aos mesmos 3 cards\n${debugResult()}`);
    assert.equal(result.merged.length, 3, `merge deve manter todos os anuncios por MLB\n${debugResult()}`);
    assert.equal(result.controlada.totalVisiveis, 3, `coleta controlada deve contar todos os cards visiveis\n${debugResult()}`);
    assert.equal(result.controlada.anuncios.length, 3, `coleta controlada deve preservar todos os anuncios da base ML\n${debugResult()}`);
    assert.equal(result.controlada.resumo.com_titulo, 3, 'coleta controlada deve manter titulo em todos os anuncios');
    assert.equal(result.controlada.resumo.com_foto, 3, 'coleta controlada deve manter foto em todos os anuncios');
    assert.equal(result.controlada.resumo.com_preco, 3, 'coleta controlada deve manter preco em todos os anuncios');
    assert.equal(result.controlada.resumo.com_link, 3, 'coleta controlada deve manter link em todos os anuncios');
    assert.equal(result.controlada.resumo.com_dados_avant, 3, 'coleta controlada deve mesclar dados Avant passivos');
    assert.equal(result.wrapperNovo.length, 3, `wrapper antigo deve redirecionar para a coleta nova sem exigir login/vinculacao\n${debugResult()}`);
    assert.equal(result.wrapperNovo.filter(item => item && item.vendasFonte === 'avantpro_dom').length, 3, 'wrapper novo deve preservar dados Avant DOM nos tres anuncios');
    assert.equal(result.resumo.com_titulo, 3, 'todos devem manter titulo real do ML');
    assert.equal(result.resumo.com_foto, 3, 'todos devem manter foto real do ML');
    assert.equal(result.resumo.com_preco, 3, 'todos devem manter preco do ML');
    assert.equal(result.resumo.com_link, 3, 'todos devem manter link do ML');
    assert.equal(result.resumo.com_dados_avant, 3, 'todos devem receber dados Avant');
    assert.equal(result.resumo.incompletos, 0, 'nenhum anuncio completo deve ser marcado como incompleto');

    const first = result.merged.find(item => item.id === 'MLB1687891923');
    assert.ok(first, 'primeiro MLB deve existir no merge');
    assert.match(first.titulo, /Cebolao Radiador/i, 'titulo nao pode virar JM');
    assert.equal(Number(first.vendas), 135, 'vendas devem vir do DOM Avant');
    assert.equal(Number(first.media_mensal), 4.94, 'media mensal deve vir do DOM Avant');
    assert.equal(first.vendedor, 'KARHUB AUTOPARTS', 'vendedor deve vir do DOM Avant correto');
    assert.equal(first.preco, 79, 'preco deve vir estruturado do ML');
    assert.equal(first.vendasFonte, 'avantpro_dom', 'fonte de vendas deve ser Avant DOM');
    assert.equal(first.precoFonte, 'mercado_livre_dom', 'fonte de preco deve ser Mercado Livre DOM');

    await page.evaluate((bodyHtml) => {
      document.body.innerHTML = bodyHtml;
      window.__JK_AVANT_CARD_DATA_CACHE = {};
      window.__JK_AVANT_CARD_QUEUE_CLICKED_KEYS = {};
      window.scrollTo(0, 0);
    }, htmlMercadoLivreFakePainelAvantFlutuanteBody());
    const floating = await page.evaluate(async () => {
      const base = await extrairCardsMercadoLivreBasicoWebview({ limite: 80 });
      const avant = await extrairAnunciosAvantProDomWebview({ limite: 80 });
      const merged = mesclarAnunciosAvant(base.anuncios, avant.anuncios);
      return {
        base,
        avant,
        merged,
        resumo: resumoPrimeiraPaginaFavoritos(base.total, merged)
      };
    });
    const debugFloating = () => JSON.stringify({
      baseIds: (floating.base && floating.base.anuncios || []).map(item => item.id),
      avant: (floating.avant && floating.avant.anuncios || []).map(item => ({
        id: item.id,
        vendedor: item.vendedor,
        vendas: item.vendas,
        media_mensal: item.media_mensal
      })),
      merged: (floating.merged || []).map(item => ({
        id: item.id,
        vendedor: item.vendedor,
        vendas: item.vendas,
        media_mensal: item.media_mensal,
        titulo: item.titulo
      })),
      resumo: floating.resumo,
      avantDebug: floating.avant && floating.avant.debug
    }, null, 2);
    assert.equal(floating.base.total, 3, `base ML flutuante deve coletar os tres cards\n${debugFloating()}`);
    assert.equal(floating.avant.total, 3, `DOM Avant flutuante deve gerar tres anuncios, um por painel\n${debugFloating()}`);
    assert.equal(floating.merged.length, 3, `merge flutuante deve manter tres anuncios\n${debugFloating()}`);
    assert.equal(floating.resumo.com_dados_avant, 3, `todos os tres anuncios devem receber dados Avant no painel flutuante\n${debugFloating()}`);
    const byId = new Map(floating.merged.map(item => [item.id, item]));
    assert.equal(byId.get('MLB1687891923').vendedor, 'KARHUB AUTOPARTS', `painel 1 deve cair no primeiro MLB\n${debugFloating()}`);
    assert.equal(byId.get('MLB3512477523').vendedor, 'AUTOPECASMOLINA', `painel 2 deve cair no segundo MLB, nao no primeiro\n${debugFloating()}`);
    assert.equal(byId.get('MLB1758609649').vendedor, 'SUL PECAS DISTRIBUIDORA', `painel 3 deve cair no terceiro MLB\n${debugFloating()}`);

    await page.evaluate(() => {
      document.body.innerHTML = `
        <main>
          <ol class="ui-search-layout">
            <li class="ui-search-layout__item">
              <article class="poly-card">
                <a class="poly-component__title" href="https://produto.mercadolivre.com.br/MLB-999888777-Sensor-Teste-Avant-_JM" title="Sensor Teste Avant">Sensor Teste Avant</a>
                <img src="https://http2.mlstatic.com/D_NQ_NP_999.webp" width="120" height="120" alt="Sensor Teste Avant">
                <span class="andes-money-amount">R$ 88,90</span>
                <button class="avant-info-button" type="button">Informacoes Avantpro</button>
              </article>
            </li>
          </ol>
        </main>
      `;
      window.__JK_AVANT_CARD_DATA_CACHE = {};
      window.__JK_AVANT_CARD_QUEUE_CLICKED_KEYS = {};
      window.__JK_AVANT_LAST_PANEL_SNAPSHOT = '';
      window.__JK_AVANT_LAST_CARD_CLICKED = null;
      window.__JK_AVANT_CARD_CLICKED_AT = 0;
      document.querySelector('.avant-info-button').addEventListener('click', () => {
        let painel = document.querySelector('.avant-floating-compact-panel');
        if (!painel) {
          painel = document.createElement('aside');
          painel.className = 'avant-floating-compact-panel';
          painel.style.cssText = 'position:absolute;left:360px;top:20px;width:260px;background:#fff;border:1px solid #7857ff;padding:10px;';
          painel.innerHTML = `
            <div>Informacoes Avantpro</div>
            <div>Vendas do produto 23</div>
            <div>Ritmo atual 2,5</div>
            <div>Nome do vendedor LOJA TESTE AVANT</div>
            <div>Anuncio criado em 05/02/2024</div>
          `;
          document.body.appendChild(painel);
        }
      });
      window.scrollTo(0, 0);
    });
    const clickedCompact = await page.evaluate(async () => {
      const base = await extrairCardsMercadoLivreBasicoWebview({ limite: 10 });
      const fila = await acionarCardsAvantProFilaWebview({
        maxClicks: 1,
        maxRuntimeMs: 4500,
        maxTentativasPorCard: 1
      });
      const avant = await extrairAnunciosAvantProDomWebview({ limite: 10 });
      const merged = mesclarAnunciosAvant(base.anuncios, avant.anuncios);
      return {
        base,
        fila,
        avant,
        merged,
        cache: window.__JK_AVANT_CARD_DATA_CACHE || {},
        resumo: resumoPrimeiraPaginaFavoritos(base.total, merged)
      };
    });
    const debugClickedCompact = () => JSON.stringify({
      fila: clickedCompact.fila,
      avant: clickedCompact.avant && clickedCompact.avant.anuncios,
      merged: clickedCompact.merged,
      cacheKeys: Object.keys(clickedCompact.cache || {}),
      cache: clickedCompact.cache,
      resumo: clickedCompact.resumo
    }, null, 2);
    assert.equal(clickedCompact.fila.clicked, 1, `fila deve clicar no botao explicito do Avant\n${debugClickedCompact()}`);
    assert.equal(clickedCompact.avant.total, 1, `leitura DOM apos o clique deve ler painel flutuante compacto\n${debugClickedCompact()}`);
    assert.equal(clickedCompact.resumo.com_dados_avant, 1, `merge deve aplicar os dados Avant do painel compacto\n${debugClickedCompact()}`);
    assert.equal(Number(clickedCompact.merged[0].vendas), 23, `vendas compactas devem ser extraidas pelo DOM Avant\n${debugClickedCompact()}`);
    assert.equal(Number(clickedCompact.merged[0].media_mensal), 2.5, `ritmo compacto deve ser extraido pelo DOM Avant\n${debugClickedCompact()}`);
    assert.equal(clickedCompact.merged[0].vendedor, 'LOJA TESTE AVANT', `vendedor compacto deve ser extraido pelo DOM Avant\n${debugClickedCompact()}`);
    assert.equal(clickedCompact.merged[0].data_criacao, '05/02/2024', `data compacta deve ser extraida pelo DOM Avant\n${debugClickedCompact()}`);

    await page.evaluate(() => {
      const cards = [
        { id: '1111111111', title: 'Cebolao Teste Fluxo Um', price: '50,00', sales: '10', monthly: '1,1', seller: 'LOJA UM', created: '01/01/2024' },
        { id: '2222222222', title: 'Cebolao Teste Fluxo Dois', price: '60,00', sales: '20', monthly: '2,2', seller: 'LOJA DOIS', created: '02/02/2024' },
        { id: '3333333333', title: 'Cebolao Teste Fluxo Tres', price: '70,00', sales: '30', monthly: '3,3', seller: 'LOJA TRES', created: '03/03/2024' }
      ];
      document.body.innerHTML = `
        <style>
          .ui-search-layout__item { min-height: 180px; border: 1px solid #ddd; margin-bottom: 16px; }
          .poly-card { padding: 10px; position: relative; }
          .avant-generated-panel { position: absolute; left: 360px; width: 270px; background: #fff; border: 1px solid #7857ff; padding: 10px; z-index: 2; }
        </style>
        <main>
          <ol class="ui-search-layout">
            ${cards.map((item, index) => `
              <li class="ui-search-layout__item" data-index="${index}">
                <article class="poly-card" data-mlb="MLB${item.id}">
                  <a class="poly-component__title" href="https://produto.mercadolivre.com.br/MLB-${item.id}-${item.title.replace(/\s+/g, '-')}-_JM" title="${item.title}">${item.title}</a>
                  <img src="https://http2.mlstatic.com/D_NQ_NP_${index + 11}.webp" width="120" height="120" alt="${item.title}">
                  <span class="andes-money-amount">R$ ${item.price}</span>
                  <button class="avant-info-button" type="button" data-id="${item.id}">Informacoes Avantpro</button>
                </article>
              </li>
            `).join('')}
          </ol>
        </main>
      `;
      window.__JK_AVANT_CARD_DATA_CACHE = {};
      window.__JK_AVANT_CARD_QUEUE_CLICKED_KEYS = {};
      window.__JK_AVANT_LAST_PANEL_SNAPSHOT = '';
      window.__JK_AVANT_LAST_CARD_CLICKED = null;
      window.__JK_AVANT_CARD_CLICKED_AT = 0;
      document.querySelectorAll('.avant-info-button').forEach((button, index) => {
        button.addEventListener('click', () => {
          const item = cards[index];
          let painel = document.querySelector(`.avant-generated-panel[data-id="${item.id}"]`);
          if (!painel) {
            painel = document.createElement('aside');
            painel.className = 'avant-generated-panel';
            painel.dataset.id = item.id;
            painel.style.top = `${20 + (index * 196)}px`;
            painel.innerHTML = `
              <div>Informacoes Avantpro</div>
              <div>Vendas do produto ${item.sales}</div>
              <div>Ritmo atual ${item.monthly}</div>
              <div>Nome do vendedor ${item.seller}</div>
              <div>Anuncio criado em ${item.created}</div>
            `;
            document.body.appendChild(painel);
          }
        });
      });
      window.scrollTo(0, 0);
    });
    const controlledClicks = await page.evaluate(async () => {
      const progress = [];
      const resultado = await coletarPrimeiraPaginaFavoritosControlada({
        maxAnuncios: 10,
        tempoLimiteMs: 30000,
        maxPassadas: 2,
        loteCliques: 3,
        onProgress: (payload) => progress.push(Object.assign({}, payload || {}))
      });
      return {
        resultado,
        progress,
        queue: window.__JK_AVANT_LAST_QUEUE_RESULT || null,
        cache: window.__JK_AVANT_CARD_DATA_CACHE || {}
      };
    });
    const debugControlledClicks = () => JSON.stringify({
      resumo: controlledClicks.resultado && controlledClicks.resultado.resumo,
      totalVisiveis: controlledClicks.resultado && controlledClicks.resultado.totalVisiveis,
      anuncios: (controlledClicks.resultado && controlledClicks.resultado.anuncios || []).map(item => ({
        id: item.id,
        titulo: item.titulo,
        preco: item.preco,
        foto: !!(item.foto || item.imagem || item.thumbnail),
        vendedor: item.vendedor,
        vendas: item.vendas,
        media_mensal: item.media_mensal,
        data_criacao: item.data_criacao
      })),
      queue: controlledClicks.queue,
      cacheKeys: Object.keys(controlledClicks.cache || {}),
      progress: controlledClicks.progress.slice(-8)
    }, null, 2);
    assert.equal(controlledClicks.resultado.success, true, `coleta controlada deve finalizar com sucesso\n${debugControlledClicks()}`);
    assert.equal(controlledClicks.resultado.totalVisiveis, 3, `coleta controlada deve enxergar os tres cards\n${debugControlledClicks()}`);
    assert.equal(controlledClicks.resultado.anuncios.length, 3, `coleta controlada deve preservar os tres anuncios\n${debugControlledClicks()}`);
    assert.equal(controlledClicks.resultado.resumo.com_titulo, 3, `coleta controlada deve manter tres titulos\n${debugControlledClicks()}`);
    assert.equal(controlledClicks.resultado.resumo.com_foto, 3, `coleta controlada deve manter tres fotos\n${debugControlledClicks()}`);
    assert.equal(controlledClicks.resultado.resumo.com_preco, 3, `coleta controlada deve manter tres precos\n${debugControlledClicks()}`);
    assert.equal(controlledClicks.resultado.resumo.com_link, 3, `coleta controlada deve manter tres links\n${debugControlledClicks()}`);
    assert.equal(controlledClicks.resultado.resumo.com_dados_avant, 3, `coleta controlada deve mesclar Avant nos tres cards\n${debugControlledClicks()}`);
    const controlledById = new Map(controlledClicks.resultado.anuncios.map(item => [item.id, item]));
    assert.equal(controlledById.get('MLB1111111111').vendedor, 'LOJA UM', `dados do primeiro card nao podem misturar\n${debugControlledClicks()}`);
    assert.equal(controlledById.get('MLB2222222222').vendedor, 'LOJA DOIS', `dados do segundo card nao podem misturar\n${debugControlledClicks()}`);
    assert.equal(controlledById.get('MLB3333333333').vendedor, 'LOJA TRES', `dados do terceiro card nao podem misturar\n${debugControlledClicks()}`);
    assert.equal(Number(controlledById.get('MLB3333333333').media_mensal), 3.3, `media mensal do terceiro card deve vir do painel correto\n${debugControlledClicks()}`);
  } finally {
    await browser.close();
  }
  console.log('Teste funcional DOM Favoritos/Avant concluido com sucesso.');
}

run().catch(async (err) => {
  console.error('Teste funcional DOM Favoritos/Avant falhou:', err && err.message ? err.message : err);
  process.exitCode = 1;
});
