'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

class Clock {
  constructor() { this.now = 1000; this.next = 0; this.timers = new Map(); }
  setTimeout(fn, ms) { const id = ++this.next; this.timers.set(id, { at: this.now + Math.max(0, ms), fn }); return id; }
  clearTimeout(id) { this.timers.delete(id); }
  async flush() { for (let i = 0; i < 80; i += 1) await Promise.resolve(); }
  async tick(ms) {
    const target = this.now + ms;
    await this.flush();
    for (;;) {
      const due = [...this.timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at);
      if (!due.length) break;
      const [id, timer] = due[0]; this.now = timer.at; this.timers.delete(id); timer.fn(); await this.flush();
    }
    this.now = target; await this.flush();
  }
}

function environment() {
  const clock = new Clock();
  const FakeDate = class extends Date { static now() { return clock.now; } };
  const key = item => `mlb:${item.id}`;
  const merge = (a, b) => [...new Map([...a, ...b].map(item => [item.id, { ...item }])).values()];
  const context = {
    console, Promise, Map, Set, WeakMap, AbortController, Date: FakeDate,
    setTimeout: clock.setTimeout.bind(clock), clearTimeout: clock.clearTimeout.bind(clock),
    FavoritosV2: { browser: { pageScripts: { render: name => JSON.stringify(name) } } },
    ML_FAVORITOS_COLETA_ANUNCIOS_MAX: 80,
    normalizarFonte: value => value.toLowerCase(), fonteVendasConfiavel: value => value === 'avantpro_dom' || value === 'api',
    hasNumeroVendas: value => Number.isFinite(value), parseNumeroVendas: value => value === null || value === '' || value === undefined ? null : Number(value),
    chaveCanonicaAnuncioFavoritos: key, mesclarAnunciosAvant: merge,
    resolverWebviewFavoritosColeta: options => options.webview,
    completarBaseMercadoLivreComApiFavoritos: async items => items,
    obterMetricaRolagemMercadoLivreFavoritos: async () => ({ y: 0, view: 560, height: 560 }),
    rolarMercadoLivreFavoritos: async () => null,
    alturaVarreduraPrimeiraPaginaFavoritos: () => 560,
    montarPosicoesVarreduraPrimeiraPaginaFavoritos: () => [0],
    capturarAvantProCardsVisiveisRapidoWebview: async () => ({ anuncios: [] }),
    extrairAnunciosAvantProDomWebview: async () => ({ anuncios: [] }),
    extrairCacheAvantProCardsWebview: async () => ({ anuncios: [] }),
    aguardarDadosAvantProEstaveisWebview: async () => null,
    assinaturaEstabilidadePrimeiraPaginaFavoritos: items => JSON.stringify(items),
    deveEncerrarPlateauPrimeiraPaginaFavoritos: () => false,
    resumoPrimeiraPaginaFavoritos: (total, items, extra) => ({ visiveis: total, com_dados_avant: items.filter(item => item.vendasFonte === 'avantpro_dom').length, ...extra }),
    emitirProgressoPrimeiraPaginaFavoritos: (callback, payload) => callback && callback(payload)
  };
  context.window = context;
  vm.createContext(context);
  let source = fs.readFileSync(path.resolve(__dirname, '../static/favoritos/v2/browser/first-page-controller.js'), 'utf8');
  source = source.replace('  const api = { coletarPrimeiraPaginaFavoritosControlada };',
    '  global.__budgetTest = { criarControleCancelamentoPrimeiraPagina, criarContextoPrimeiraPagina };\n  const api = { coletarPrimeiraPaginaFavoritosControlada };');
  vm.runInContext(source, context);
  function raw(workerId) {
    const page = { Date: FakeDate, Promise, setTimeout: clock.setTimeout.bind(clock), clearTimeout: clock.clearTimeout.bind(clock), writes: 0 };
    page.window = page; vm.createContext(page);
    const browser = { workerId, calls: [], executeJavaScript(code) {
      assert.strictEqual(this, browser, 'adapter must preserve native/worker receiver');
      browser.calls.push({ code, at: clock.now });
      try { return Promise.resolve(vm.runInContext(code, page)); } catch (err) { return Promise.reject(err); }
    } };
    return { browser, page };
  }
  return { clock, context, raw, ...context.__budgetTest };
}

async function main() {
  {
    const env = environment(); const controller = new AbortController(); let calls = 0;
    const control = env.criarControleCancelamentoPrimeiraPagina(controller.signal, env.clock.now + 50, () => true);
    controller.abort();
    assert.throws(() => control.aguardar(() => { calls += 1; }), error => error.name === 'AbortError');
    assert.equal(calls, 0, 'cancelled lazy operations cannot start');
  }
  {
    const env = environment(); const { browser } = env.raw('worker-1'); const snapshots = []; const progress = [];
    const seed = [{ id: 'MLB1', titulo: 'Base coletada', vendas: 5, vendasFonte: 'api' }];
    env.context.acionarCardsAvantProFilaWebview = options => options.webview.executeJavaScript('new Promise(function () {})');
    env.context.extrairCardsMercadoLivreBasicoWebview = options => options.webview.executeJavaScript('new Promise(function () {})');
    const start = env.clock.now;
    const operation = env.context.coletarPrimeiraPaginaFavoritosControlada({ webview: browser, tempoLimiteMs: 90000,
      loteCliques: 6, anunciosSeed: seed, confirmedKeys: ['mlb:MLB1'], onSnapshot: value => snapshots.push(value), onProgress: value => progress.push(value) });
    await env.clock.flush();
    await env.clock.tick(85000);
    assert.equal(browser.calls.length, 3, '85 s must stop collection and begin exactly one final read');
    await env.clock.tick(5000);
    const result = await operation;
    assert.equal(env.clock.now - start, 90000);
    assert.equal(result.tempoEsgotado, true);
    assert.equal(result.anuncios[0].titulo, 'Base coletada', 'deadline preserves seeded/collected base');
    assert.equal(result.confirmedKeys.length, 0, 'base without Avant sales cannot become confirmed');
    const count = browser.calls.length; const progressCount = progress.length;
    await env.clock.tick(40000);
    assert.equal(browser.calls.length, count, 'expired phase cannot send additional commands');
    assert.equal(progress.length, progressCount, 'expired attempt cannot emit late progress');
    snapshots.at(-1).anuncios[0].titulo = 'external mutation';
    assert.equal(result.anuncios[0].titulo, 'Base coletada', 'snapshot must be detached from results');
    assert.equal(seed[0].titulo, 'Base coletada', 'caller seed cannot be mutated');
  }
  {
    const env = environment(); const { browser, page } = env.raw('worker-1');
    const first = env.criarContextoPrimeiraPagina({ tempoLimiteMs: 90000 }, browser);
    const pending = first.webview.executeJavaScript('(async function () { await new Promise(function (resolve) { setTimeout(resolve, 100); }); __jkFavoritosVerificar(); window.writes += 1; return true; })()').catch(error => error);
    await env.clock.flush();
    const second = env.criarContextoPrimeiraPagina({ tempoLimiteMs: 90000 }, browser);
    await second.webview.executeJavaScript('true');
    await env.clock.tick(100);
    await pending;
    assert.equal(page.writes, 0, 'old generation cannot write after asynchronous wait');
    const separate = env.criarContextoPrimeiraPagina({ tempoLimiteMs: 90000 }, env.raw('worker-2').browser);
    assert.doesNotThrow(() => separate.controle.verificar());
    assert.doesNotThrow(() => second.controle.verificar(), 'separate pool worker must not invalidate another worker');
    assert.throws(() => first.controle.verificar(), error => error.name === 'AbortError');
    first.fechar(); second.fechar(); separate.fechar();
  }
  {
    const env = environment(); const { browser, page } = env.raw();
    const ctx = env.criarContextoPrimeiraPagina({ tempoLimiteMs: 90000 }, browser);
    const operation = ctx.webview.executeJavaScript('(async function () { await new Promise(function (resolve) { setTimeout(resolve, 86000); }); __jkFavoritosVerificar(); window.writes += 1; })()').catch(error => error);
    await env.clock.flush(); await env.clock.tick(86000); await operation;
    assert.equal(page.writes, 0, 'page deadline blocks cache/click writes after a delayed await');
    const count = browser.calls.length;
    assert.throws(() => ctx.webview.executeJavaScript('window.writes += 1'), error => error.favoritosOrcamentoEsgotado);
    assert.equal(browser.calls.length, count, 'expired lazy script cannot reach IPC');
    ctx.fechar();
  }

  {
    const env = environment();
    vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../static/favoritos/v2/browser/first-page-support.js'), 'utf8'), env.context);
    let resolveApi; const item = { id: 'MLB1', titulo: '' };
    Object.assign(env.context, {
      normalizarMlbFavoritosCanonico: value => value,
      tituloValidoFavoritosCanonico: item => !!item,
      imagemValidaFavoritosCanonico: () => false, precoValidoFavoritosCanonico: () => false,
      limparLinkProdutoMercadoLivreFavoritos: () => '',
      consultarItemApiMercadoLivre: () => new Promise(resolve => { resolveApi = resolve; }),
      prepararAnuncioMercadoLivreCanonico: value => value,
      classificarQualidadeAnuncioFavoritosCanonico: () => 'completo'
    });
    const control = env.criarControleCancelamentoPrimeiraPagina(undefined, env.clock.now + 40, () => true);
    const operation = env.context.completarBaseMercadoLivreComApiFavoritos([item], { controle: control,
      deadlineMs: env.clock.now + 40, timeoutMs: 3500 }).catch(error => error);
    await env.clock.flush(); await env.clock.tick(40); await operation;
    resolveApi({ id: 'MLB1', titulo: 'late response', vendas: 50, vendasFonte: 'api' });
    await env.clock.flush();
    assert.equal(item.titulo, '', 'late API response cannot mutate accumulated announcements');
    assert.equal(env.clock.timers.size, 0, 'helper timers must be cleared after deadline');
  }
  {
    const env = environment();
    vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../static/favoritos/v2/browser/first-page-support.js'), 'utf8'), env.context);
    const snapshots = []; let metricCalls = 0;
    Object.assign(env.context, {
      obterMetricaRolagemMercadoLivreFavoritos: () => ++metricCalls === 1 ? Promise.resolve({ y: 0, view: 560 }) : new Promise(() => {}),
      rolarMercadoLivreFavoritos: async () => null,
      extrairCardsMercadoLivreBasicoWebview: async () => ({ anuncios: [{ id: 'MLB1', titulo: 'checkpoint' }] }),
      esperar: ms => new Promise(resolve => env.clock.setTimeout(resolve, ms))
    });
    const control = env.criarControleCancelamentoPrimeiraPagina(undefined, env.clock.now + 1000, () => true);
    const operation = env.context.materializarCardsPrimeiraPaginaMercadoLivreFavoritos({ webview: {}, controle: control,
      deadlineMs: env.clock.now + 1000, onSnapshot: snapshot => snapshots.push(snapshot) }).catch(error => error);
    await env.clock.flush(); await env.clock.tick(1000); await operation;
    assert.equal(snapshots.length, 1, 'base must checkpoint before the next metric operation can stall');
    assert.equal(snapshots[0].anuncios[0].titulo, 'checkpoint');
  }
  {
    const env = environment(); const { browser } = env.raw(); const cancel = new AbortController();
    env.context.acionarCardsAvantProFilaWebview = options => options.webview.executeJavaScript('new Promise(function () {})');
    env.context.extrairCardsMercadoLivreBasicoWebview = async () => { throw new Error('finalization after cancel is forbidden'); };
    const operation = env.context.coletarPrimeiraPaginaFavoritosControlada({ webview: browser, tempoLimiteMs: 90000,
      loteCliques: 6, anunciosSeed: [{ id: 'MLB1' }], signal: cancel.signal }).catch(error => error);
    await env.clock.flush(); const calls = browser.calls.length; cancel.abort(); await env.clock.flush();
    const result = await operation;
    assert.equal(result.name, 'AbortError');
    await env.clock.tick(100000); assert.equal(browser.calls.length, calls, 'user cancel must not run finalization scripts');
  }


  {
    const env = environment(); const { browser, page } = env.raw('late-response-worker');
    const snapshots = []; const progress = []; const start = env.clock.now;
    page.cache = {};
    env.context.acionarCardsAvantProFilaWebview = async options => {
      await options.controle.esperar(84400);
      return options.webview.executeJavaScript(`(async function () {
        await new Promise(function (resolve) { setTimeout(resolve, ${start + 93240} - Date.now()); });
        __jkFavoritosVerificar();
        window.cache.MLB1 = { vendas: 999, vendasFonte: 'avantpro_dom' };
        window.writes += 1;
        return { clicked: 1, resolvedKeys: ['mlb:MLB1'], capturados: 1 };
      })()`);
    };
    env.context.extrairCardsMercadoLivreBasicoWebview = async () => ({ anuncios: [{ id: 'MLB1', titulo: 'partial base' }] });
    const operation = env.context.coletarPrimeiraPaginaFavoritosControlada({ webview: browser,
      tempoLimiteMs: 90000, loteCliques: 6, anunciosSeed: [{ id: 'MLB1', titulo: 'partial base' }],
      onSnapshot: snapshot => snapshots.push(snapshot), onProgress: value => progress.push(value) });
    await env.clock.flush(); await env.clock.tick(84500);
    const lateCall = browser.calls.find(call => call.code.includes('window.cache.MLB1'));
    assert.ok(lateCall && lateCall.at - start >= 84000 && lateCall.at - start < 85000,
      'the 93,240 ms response operation must actually start near the collection deadline');
    await env.clock.tick(5500);
    const result = await operation;
    assert.ok(result.elapsedMs <= 90000, 'operation that would respond at 93,240 ms must return partial by 90,000 ms');
    assert.equal(result.tempoEsgotado, true);
    assert.equal(result.anuncios[0].titulo, 'partial base');
    const resultBeforeLate = JSON.stringify(result);
    const snapshotsBeforeLate = JSON.stringify(snapshots);
    const progressBeforeLate = JSON.stringify(progress);
    await env.clock.tick(3240);
    assert.equal(env.clock.now - start, 93240, 'advance to the exact late response timestamp');
    assert.equal(JSON.stringify(result), resultBeforeLate, '93,240 ms response cannot modify the returned partial result');
    assert.equal(JSON.stringify(snapshots), snapshotsBeforeLate, '93,240 ms response cannot publish a new snapshot');
    assert.equal(JSON.stringify(progress), progressBeforeLate, '93,240 ms response cannot publish late progress');
    assert.equal(Object.keys(page.cache).length, 0, '93,240 ms response cannot write the page cache');
    assert.equal(page.writes, 0);
  }


  {
    const env = environment(); const id = 'MLB12345678'; const item = { id, titulo: '' };
    vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../static/favoritos/tabelas-layout/01-ml-base-busca.js'), 'utf8').split('        async function executarComConcorrencia')[0], env.context);
    vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../static/favoritos/v2/browser/first-page-support.js'), 'utf8'), env.context);
    let resolveOldElectron; let electronCalls = 0; const requests = [];
    Object.assign(env.context, {
      hasTexto: value => !!String(value || '').trim(), normalizarTextoMl: value => String(value || '').toLowerCase(),
      normalizarMlbFavoritosCanonico: value => value, tituloValidoFavoritosCanonico: value => !!value,
      imagemValidaFavoritosCanonico: () => false, precoValidoFavoritosCanonico: () => false,
      limparLinkProdutoMercadoLivreFavoritos: value => value || '',
      escolherNomeVendedor: () => '', normalizarNomeVendedor: value => value || '', vendedorValido: value => !!value,
      obterImagemAnuncioFavoritos: value => value.imagem || '', prepararAnuncioMercadoLivreCanonico: value => value,
      classificarQualidadeAnuncioFavoritosCanonico: () => 'completo', preencherPrecoAnuncioFavoritos: () => {},
      fetch: async url => { requests.push(url); return { ok: true, json: async () => ({ id, title: 'fresh generation', permalink: `https://produto.mercadolivre.com.br/${id}` }) }; },
      electronAPI: { getMlPublicItemInfo: () => { electronCalls += 1; return new Promise(resolve => { resolveOldElectron = resolve; }); } }
    });
    env.context.FavoritosV2.promotionEffectuation = { publicApi: { listings: {
      obterParcelamentoSemJurosFavoritos: () => null, normalizarTipoAnuncioFavoritos: value => value || '', preencherTipoAnuncioFavoritos: () => {}
    } } };
    const api = env.context.consultarItemApiMercadoLivre;
    assert.equal(api.length, 1, 'controlled API option must preserve public arity');
    assert.equal(env.context.consultarItemApiMercadoLivreSemDedupe.length, 1);
    const sharedPending = new Promise(() => {});
    api.cache = new Map(); api.inflight = new Map([[id, sharedPending]]);
    const raw = env.raw('api-generation-worker').browser;
    const oldAttempt = env.criarContextoPrimeiraPagina({ tempoLimiteMs: 90000 }, raw);
    const control = env.criarControleCancelamentoPrimeiraPagina(undefined, env.clock.now + 40, oldAttempt.atual);
    const pending = env.context.completarBaseMercadoLivreComApiFavoritos([item], { controle: control,
      deadlineMs: env.clock.now + 40, timeoutMs: 3500 }).catch(error => error);
    await env.clock.flush();
    assert.equal(electronCalls, 1, 'controlled query bypasses the pre-existing shared inflight request');
    assert.equal(api.inflight.get(id), sharedPending, 'controlled query cannot replace shared inflight');
    await env.clock.tick(40); await pending;
    const newAttempt = env.criarContextoPrimeiraPagina({ tempoLimiteMs: 90000 }, raw);
    resolveOldElectron({ id, titulo: 'late old generation', vendedor: 'late seller', vendas: 999 });
    await env.clock.flush();
    assert.equal(api.cache.size, 0, 'expired old generation cannot populate global API cache');
    assert.equal(api.inflight.size, 1); assert.equal(api.inflight.get(id), sharedPending);
    assert.equal(requests.length, 0, 'late Electron result cannot start public item/user fallbacks');
    assert.equal(item.titulo, '', 'real API and enrichment helpers cannot mutate the old accumulator');
    env.context.electronAPI = null;
    const freshItem = { id, titulo: '' };
    await env.context.completarBaseMercadoLivreComApiFavoritos([freshItem], { controle: newAttempt.controle,
      deadlineMs: newAttempt.controle.deadline });
    assert.equal(freshItem.titulo, 'fresh generation', 'subsequent generation must issue a fresh controlled query');
    assert.equal(api.cache.get(id).titulo, 'fresh generation');
    assert.equal(api.inflight.get(id), sharedPending, 'fresh controlled generation also leaves shared inflight untouched');
    assert.equal(requests.length, 1);
    oldAttempt.fechar(); newAttempt.fechar();
  }
  {
    const env = environment(); const id = 'MLB12345678'; let resolveJson; const requests = [];
    vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../static/favoritos/tabelas-layout/01-ml-base-busca.js'), 'utf8').split('        async function executarComConcorrencia')[0], env.context);
    Object.assign(env.context, { fetch: async url => { requests.push(url); return { ok: true,
      json: () => new Promise(resolve => { resolveJson = resolve; }) }; } });
    const api = env.context.consultarItemApiMercadoLivre;
    api.cache = new Map(); api.inflight = new Map();
    const control = env.criarControleCancelamentoPrimeiraPagina(undefined, env.clock.now + 40, () => true);
    const operation = api(id, control).catch(error => error);
    await env.clock.flush(); await env.clock.tick(40); await operation;
    resolveJson({ id, title: 'late JSON', seller_id: 123 }); await env.clock.flush();
    assert.equal(api.cache.size, 0, 'late item JSON cannot write shared cache');
    assert.equal(api.inflight.size, 0, 'controlled query cannot leave shared inflight entries');
    assert.equal(requests.length, 1, 'late item JSON cannot trigger seller request');
    assert.equal(env.clock.timers.size, 0);
  }

  console.log('OK: collection 85 + 5 s, partial snapshots, cancel, delayed writes and pool isolation.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
