'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const cssSource = fs.readFileSync(path.join(root, 'static', 'vendas', 'styles.css'), 'utf8');
const runtimeSource = fs.readFileSync(path.join(root, 'static', 'vendas', 'runtime.js'), 'utf8');
const periodoSource = fs.readFileSync(path.join(root, 'static', 'vendas', 'periodo-cache.js'), 'utf8');
const syncSource = fs.readFileSync(path.join(root, 'static', 'vendas', 'sync.js'), 'utf8');
const dadosSource = fs.readFileSync(path.join(root, 'static', 'vendas', 'dados-render.js'), 'utf8');
const canonicalHtml = fs.readFileSync(path.join(root, 'static', 'vendas.html'), 'utf8');
const mirrorHtml = fs.readFileSync(path.join(root, 'vendas.html'), 'utf8');

const cacheKey = '20260831-vendas-lojas-visiveis-v1';
const lojaRule = cssSource.match(/\.loja-group\s*\{([^}]+)\}/);

assert.ok(lojaRule, 'a regra CSS da faixa de lojas deve existir');
assert.match(lojaRule[1], /flex-wrap\s*:\s*wrap\s*;/);
assert.match(lojaRule[1], /overflow\s*:\s*visible\s*;/);
assert.doesNotMatch(lojaRule[1], /flex-wrap\s*:\s*nowrap\s*;/);
assert.doesNotMatch(lojaRule[1], /overflow\s*:\s*hidden\s*;/);

assert.strictEqual(mirrorHtml, canonicalHtml, 'o espelho raiz de vendas deve acompanhar o HTML canonico');
assert.match(canonicalHtml, /aria-label="Selecionar loja virtual"/);
assert.match(canonicalHtml, />Todas as lojas virtuais<\/option>/);
for (const asset of ['styles.css', 'runtime.js', 'periodo-cache.js', 'sync.js', 'dados-render.js']) {
  assert.ok(
    canonicalHtml.includes(`/vendas/${asset}?v=${cacheKey}`),
    `o cache-buster de ${asset} deve refletir a correcao`,
  );
}
assert.match(canonicalHtml, /\/vendas\/init\.js\?v=20260819-vendas-meses-completos-v1/);

assert.match(runtimeSource, /let carregarLojasPromise = null;/);
assert.match(dadosSource, /if \(carregarLojasPromise\) return carregarLojasPromise;/);
assert.match(dadosSource, /cache:\s*'no-store'/);
assert.match(dadosSource, /if \(!Array\.isArray\(lojas\)\) throw new Error/);
assert.match(dadosSource, /renderBotoesLojas\(lojasDisponiveis\)/);
assert.match(periodoSource, /event\.persisted/);
assert.match(periodoSource, /carregarLojas\(\{ silencioso: true \}\)/);
assert.match(syncSource, /Todas as lojas virtuais/);
assert.match(syncSource, /N[aã]o foi poss[ií]vel atualizar a lista de lojas/);
assert.match(syncSource, /A loja selecionada n[aã]o est[aá] mais cadastrada/);

const lockIndex = syncSource.indexOf('syncEmAndamento = true;');
const controllerIndex = syncSource.indexOf('syncController = new AbortController();', lockIndex);
const cancelPreflightIndex = syncSource.indexOf('btnCancel.disabled = true;', lockIndex);
const refreshIndex = syncSource.indexOf("const lojasAtualizadas = await carregarLojas({ silencioso: true });", lockIndex);
const enableCancelIndex = syncSource.indexOf('setSyncButtons(true);', refreshIndex);
const snapshotIndex = syncSource.indexOf('const lojasParaSincronizar =', enableCancelIndex);
assert.ok(lockIndex >= 0 && refreshIndex > lockIndex, 'a trava deve anteceder a atualizacao das lojas');
assert.ok(controllerIndex > lockIndex && controllerIndex < refreshIndex, 'o monitor deve reconhecer o preflight como lote local');
assert.ok(cancelPreflightIndex > controllerIndex && cancelPreflightIndex < refreshIndex, 'cancelar deve ficar desabilitado durante o preflight');
assert.ok(enableCancelIndex > refreshIndex, 'cancelar so deve ser habilitado depois da atualizacao');
assert.ok(snapshotIndex > enableCancelIndex, 'a lista usada no sync deve ser capturada depois da atualizacao');
assert.match(syncSource, /const lojaSelecionadaNoInicio = lojaSelecionada;/);
assert.match(syncSource, /cancelSolicitado \|\| syncController\.signal\.aborted/);
assert.match(syncSource, /Os filtros mudaram durante a atualização das lojas/);

const pageHtml = `<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <style>${cssSource}</style>
</head>
<body>
  <div class="container">
    <div id="status" class="status-bar"></div>
    <div class="filters">
      <div class="periodo-linha">
        <div class="periodo-range">
          <select id="mesAno"><option>Selecione um mes</option></select>
          <span class="periodo-separador">ou</span>
          <input id="dataIni" value="01/08/2026">
          <input id="dataFim" value="31/08/2026">
        </div>
        <div id="periodoTexto" class="periodo-texto"></div>
      </div>
      <div class="menus-linha">
        <div class="loja-group" id="lojaBotoes" aria-label="Selecionar loja"></div>
        <select id="unidadeNegocioSelect" aria-label="Selecionar loja virtual">
          <option value="__todos">Todas as lojas virtuais</option>
        </select>
        <label class="sync-toggle"><input id="forcarSync" type="checkbox"><span>Re-sincronizar</span></label>
        <div class="sync-actions">
          <button id="btnSync" type="button">Atualizar</button>
          <button id="btnCancel" type="button">Cancelar</button>
        </div>
      </div>
    </div>
    <input id="filtroTexto">
  </div>
</body>
</html>`;

const testBootstrap = `
var REQUEST_TIMEOUT_MS = 1000;
var statusEl = document.getElementById('status');
var lojaBotoes = document.getElementById('lojaBotoes');
var unidadeNegocioSelect = document.getElementById('unidadeNegocioSelect');
var filtroTexto = document.getElementById('filtroTexto');
var dataIni = document.getElementById('dataIni');
var dataFim = document.getElementById('dataFim');
var btnSync = document.getElementById('btnSync');
var btnCancel = document.getElementById('btnCancel');
var spinnerHtml = '';
var periodoApplyTimer = null;
var autoSyncTimer = null;
var clientId = null;
var lojaSelecionada = '__todas';
var lojasDisponiveis = [];
var carregarLojasPromise = null;
var vendasResumoMeta = null;
var mapeamentoUnidades = {};
var syncEmAndamento = false;
var cancelSolicitado = false;
var syncController = null;
var periodoSelecionadoPeloUsuario = false;
var dados = [];
var devolucaoItens = [];
var obterAuthHeaders = () => ({ Authorization: 'Bearer test' });
var fecharCalendariosData = () => {};
var setSyncButtons = isSyncing => {
  btnSync.disabled = isSyncing;
  btnCancel.disabled = !isSyncing;
};
var getDataIniISO = () => window.__dataIniIso;
var getDataFimISO = () => window.__dataFimIso;
window.__fetchCalls = [];
window.__fetchDelayMs = 25;
window.__dataIniIso = '2026-08-01';
window.__dataFimIso = '2026-08-01';
window.__stores = [
  { nome: 'JK Pecas' },
  { nome: 'Uai Mineirinho' },
  { nome: 'Carlos Jose' },
  { nome: 'Deckas' },
  { nome: 'Leri' },
  { nome: 'RCL' }
];
window.fetch = async (url, options = {}) => {
  window.__fetchCalls.push({ url: String(url), cache: options.cache || '' });
  await new Promise(resolve => setTimeout(resolve, window.__fetchDelayMs));
  return new Response(JSON.stringify(window.__stores), {
    status: 200,
    headers: { 'Content-Type': 'application/json' }
  });
};
localStorage.setItem('user_data', JSON.stringify({ client_id: '000002' }));
`;

function assertLayout(metrics, expectedButtons, viewportLabel) {
  assert.strictEqual(metrics.buttonCount, expectedButtons, `${viewportLabel}: quantidade de botoes`);
  assert.ok(metrics.labels.includes('RCL'), `${viewportLabel}: a loja RCL deve estar visivel`);
  assert.strictEqual(metrics.overflow, 'visible', `${viewportLabel}: a faixa nao pode recortar lojas`);
  assert.ok(metrics.allInsideGroup, `${viewportLabel}: todos os botoes devem ficar dentro da faixa`);
  assert.ok(metrics.allInsideFilter, `${viewportLabel}: todos os botoes devem ficar dentro do filtro`);
  assert.ok(metrics.rowCount >= 2, `${viewportLabel}: as lojas devem quebrar em mais de uma linha`);
  assert.ok(metrics.scrollWidth <= metrics.clientWidth + 1, `${viewportLabel}: nao deve haver rolagem horizontal oculta`);
}

async function readLayout(page) {
  return page.evaluate(() => {
    const filter = document.querySelector('.filters').getBoundingClientRect();
    const group = document.getElementById('lojaBotoes');
    const groupRect = group.getBoundingClientRect();
    const buttons = Array.from(group.querySelectorAll('.loja-btn'));
    const rects = buttons.map(button => button.getBoundingClientRect());
    const inside = (rect, parent) => (
      rect.left >= parent.left - 1
      && rect.right <= parent.right + 1
      && rect.top >= parent.top - 1
      && rect.bottom <= parent.bottom + 1
    );
    return {
      buttonCount: buttons.length,
      labels: buttons.map(button => button.textContent.trim()),
      overflow: getComputedStyle(group).overflow,
      allInsideGroup: rects.every(rect => inside(rect, groupRect)),
      allInsideFilter: rects.every(rect => inside(rect, filter)),
      rowCount: new Set(rects.map(rect => Math.round(rect.top))).size,
      scrollWidth: group.scrollWidth,
      clientWidth: group.clientWidth,
    };
  });
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1173, height: 900 } });
    await page.route('http://jk.test/', route => route.fulfill({
      status: 200,
      contentType: 'text/html; charset=utf-8',
      body: pageHtml,
    }));
    await page.goto('http://jk.test/');
    await page.addScriptTag({ content: testBootstrap });
    await page.addScriptTag({ content: syncSource });
    await page.addScriptTag({ content: dadosSource });

    const initialLoad = await page.evaluate(async () => {
      const [first, duplicate] = await Promise.all([
        carregarLojas({ silencioso: true }),
        carregarLojas({ silencioso: true }),
      ]);
      atualizarUnidadesNegocio([], {});
      return {
        first,
        duplicate,
        fetchCalls: window.__fetchCalls.slice(),
        unitLabel: unidadeNegocioSelect.options[0].textContent,
      };
    });

    assert.deepStrictEqual([initialLoad.first, initialLoad.duplicate], [true, true]);
    assert.strictEqual(initialLoad.fetchCalls.length, 1, 'chamadas simultaneas devem compartilhar a consulta de lojas');
    assert.strictEqual(initialLoad.fetchCalls[0].url, '/api/lojas');
    assert.strictEqual(initialLoad.fetchCalls[0].cache, 'no-store');
    assert.strictEqual(initialLoad.unitLabel, 'Todas as lojas virtuais');
    assertLayout(await readLayout(page), 7, 'desktop');

    const refreshed = await page.evaluate(async () => {
      window.__stores = [...window.__stores, { nome: 'Loja Centro' }];
      const loaded = await carregarLojas({ silencioso: true });
      return {
        loaded,
        fetchCalls: window.__fetchCalls.slice(),
        labels: Array.from(lojaBotoes.children, button => button.textContent.trim()),
      };
    });
    assert.strictEqual(refreshed.loaded, true);
    assert.strictEqual(refreshed.fetchCalls.length, 2, 'uma atualizacao posterior deve consultar a lista novamente');
    assert.strictEqual(refreshed.fetchCalls[1].cache, 'no-store');
    assert.ok(refreshed.labels.includes('Loja Centro'), 'uma loja cadastrada depois deve aparecer na atualizacao');
    assertLayout(await readLayout(page), 8, 'desktop atualizado');

    await page.setViewportSize({ width: 390, height: 900 });
    assertLayout(await readLayout(page), 8, 'tela estreita');

    const cancelledPreflight = await page.evaluate(async () => {
      document.getElementById('forcarSync').checked = true;
      lojaSelecionada = 'JK Pecas';
      window.__fetchDelayMs = 40;
      const callsBefore = window.__fetchCalls.length;
      const pending = executarSync();
      await new Promise(resolve => setTimeout(resolve, 5));
      const during = {
        syncing: syncEmAndamento,
        hasController: Boolean(syncController),
        syncDisabled: btnSync.disabled,
        cancelDisabled: btnCancel.disabled,
      };
      cancelSolicitado = true;
      syncController.abort();
      await pending;
      return {
        during,
        syncingAfter: syncEmAndamento,
        hasControllerAfter: Boolean(syncController),
        cancelRequestedAfter: cancelSolicitado,
        statusAfter: statusEl.textContent,
        urls: window.__fetchCalls.slice(callsBefore).map(call => call.url),
      };
    });
    assert.deepStrictEqual(cancelledPreflight.during, {
      syncing: true,
      hasController: true,
      syncDisabled: true,
      cancelDisabled: true,
    });
    assert.strictEqual(cancelledPreflight.syncingAfter, false);
    assert.strictEqual(cancelledPreflight.hasControllerAfter, false);
    assert.strictEqual(cancelledPreflight.cancelRequestedAfter, false);
    assert.match(cancelledPreflight.statusAfter, /cancelada/i);
    assert.deepStrictEqual(cancelledPreflight.urls, ['/api/lojas']);

    const changedFilterPreflight = await page.evaluate(async () => {
      lojaSelecionada = 'JK Pecas';
      window.__fetchDelayMs = 40;
      const callsBefore = window.__fetchCalls.length;
      const pending = executarSync();
      await new Promise(resolve => setTimeout(resolve, 5));
      lojaSelecionada = 'RCL';
      await pending;
      return {
        syncingAfter: syncEmAndamento,
        hasControllerAfter: Boolean(syncController),
        statusAfter: statusEl.textContent,
        urls: window.__fetchCalls.slice(callsBefore).map(call => call.url),
      };
    });
    assert.strictEqual(changedFilterPreflight.syncingAfter, false);
    assert.strictEqual(changedFilterPreflight.hasControllerAfter, false);
    assert.match(changedFilterPreflight.statusAfter, /filtros mudaram/i);
    assert.deepStrictEqual(changedFilterPreflight.urls, ['/api/lojas']);

    console.log('vendas store visibility: ok');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
