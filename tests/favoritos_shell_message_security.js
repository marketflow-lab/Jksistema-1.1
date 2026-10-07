'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(process.env.FAVORITOS_TEST_SOURCE_ROOT || path.join(__dirname, '..'));
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');
const resultChannels = ['jk-ml-browser-execute-result', 'jk-ml-browser-click-result', 'jk-ml-browser-type-result', 'jk-ml-browser-status-result'];

class Clock {
  constructor() { this.now = 1000; this.next = 0; this.timers = new Map(); }
  setTimeout(fn, ms) { const id = ++this.next; this.timers.set(id, { at: this.now + ms, fn }); return id; }
  clearTimeout(id) { this.timers.delete(id); }
  async flush() { for (let index = 0; index < 30; index += 1) await Promise.resolve(); }
  async tick(ms) {
    const target = this.now + ms;
    await this.flush();
    for (;;) {
      const due = [...this.timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at);
      if (!due.length) break;
      const [id, timer] = due[0]; this.now = timer.at; this.timers.delete(id); timer.fn(); await this.flush();
    }
    this.now = target;
    await this.flush();
  }
}

function harness(options = {}) {
  const sent = [];
  const listeners = [];
  const handled = [];
  const clock = new Clock();
  const top = { postMessage: message => sent.push(message) };
  const parent = options.nested ? { postMessage() {} } : top;
  const context = {
    console, Promise, Map, Set,
    Date: class extends Date { static now() { return clock.now; } },
    setTimeout: clock.setTimeout.bind(clock), clearTimeout: clock.clearTimeout.bind(clock),
    FavoritosV2: { browser: {}, searchRanking: { publicApi: {
      status: { mostrarBalaoFavoritosStatus() {} },
      control: { cancelarFavoritosEmExecucao() { context.cancelCalls += 1; context.mlFavoritosCancelado = true; } }
    } } },
    location: { origin: 'http://127.0.0.1:8000' }, top, parent,
    addEventListener(name, listener) { if (name === 'message') listeners.push(listener); },
    document: {},
    mlShellBrowserProxy: null, mlBrowserHost: {}, mlUrlInput: { value: '' },
    ML_DEFAULT_URL: 'https://www.mercadolivre.com.br/',
    mlFavoritosEmExecucao: true, mlFavoritosExecucaoEmSegundoPlano: false,
    mlFavoritosCancelado: false, mlFavoritosPausado: false, mlFavoritosJobIdAtual: '',
    mlFavoritosJobUltimoStatus: null, cancelCalls: 0,
    navegadorMlEmSegundoPlano: () => false, balaoResultadosMlAberto: () => true,
    urlsMercadoLivreEquivalentes: (left, right) => left === right,
    atualizarFiltroAzulFavoritos() {}, atualizarContadorSkuSidebarSelecionados() {},
    obterElectronApiFavoritosExecucao: () => null
  };
  context.window = context;
  if (options.localApi !== false) context.electronAPI = { executeEmbeddedMlBrowser() {}, getEmbeddedMlBrowserState() {} };
  vm.createContext(context);
  vm.runInContext(read('static/favoritos/v2/browser/shell-bridge.js'), context);
  vm.runInContext(read('static/favoritos/v2/browser/shell-runtime.js'), context);
  vm.runInContext(read('static/favoritos/v2/execution/07-job-controls.js'), context);
  const bridge = context.favoritosBrowserShellBridge;
  const originalHandle = bridge.handleMessage;
  bridge.handleMessage = data => { const result = originalHandle(data); handled.push({ channel: data.channel, result }); return result; };
  const proxy = context.criarProxyNavegadorMlShell();
  const dispatch = (data, origin = 'file://', source = top) => listeners.forEach(listener => listener({ data, origin, source }));
  return { context, self: vm.runInContext('window', context), top, parent, sent, handled, clock, bridge, proxy, dispatch };
}

async function run() {
  {
    const env = harness();
    let settled = false;
    let received = null;
    env.proxy.executeJavaScript('({ total: 1 })').then(value => { settled = true; received = value; });
    const requestId = env.sent.at(-1).payload.requestId;
    const expected = { total: 1, anuncios: [{ id: 'MLB111111111' }] };
    env.dispatch({ channel: 'jk-ml-browser-execute-result', requestId, result: expected });
    await env.clock.flush();
    assert.strictEqual(settled, true, 'resposta file:// do shell deve resolver a leitura pelo listener real, sem esperar timeout');
    assert.deepStrictEqual(JSON.parse(JSON.stringify(received)), expected);
  }
  {
    const env = harness();
    const trusted = env.context.FavoritosV2.browser.shellBridge.isTrustedMessage;
    assert.strictEqual(typeof trusted, 'function', 'validador compartilhado deve existir');
    const event = (origin, source = env.top, data = { channel: resultChannels[0] }) => ({ origin, source, data });
    for (const origin of ['', 'null', env.context.location.origin, 'file://']) {
      assert.strictEqual(trusted(event(origin), resultChannels), true, 'origem conhecida permitida: ' + origin);
    }
    for (const origin of ['https://untrusted.test', 'http://127.0.0.1:9000', 'file:///outro.html']) {
      assert.strictEqual(trusted(event(origin), resultChannels), false, 'origem externa deve ser bloqueada: ' + origin);
    }
    for (const origin of ['', 'null', env.context.location.origin, 'file://']) {
      assert.strictEqual(trusted(event(origin, {}), resultChannels), false, 'janela desconhecida deve ser bloqueada mesmo com origem permitida');
    }
    assert.strictEqual(trusted(event('file://', env.self), resultChannels), false, 'file:// nao pode ser autorizado como mensagem do proprio frame HTTP');
    assert.strictEqual(trusted(event(env.context.location.origin, env.self), resultChannels), true, 'mensagem local na mesma origem continua valida');
    for (const data of [null, undefined, false, 'texto', [], { channel: 'jk-unexpected' }, {}]) {
      assert.strictEqual(trusted({ origin: 'file://', source: env.top, data }, resultChannels), false, 'payload/canal invalido deve ser bloqueado');
    }
  }
  {
    const env = harness({ nested: true });
    const trusted = env.context.FavoritosV2.browser.shellBridge.isTrustedMessage;
    assert.strictEqual(trusted({ source: env.parent, origin: 'file://', data: { channel: resultChannels[0] } }, resultChannels), false, 'pai intermediario nao pode representar o shell file://');
    assert.strictEqual(trusted({ source: env.top, origin: 'file://', data: { channel: resultChannels[0] } }, resultChannels), true, 'shell superior continua autorizado no frame aninhado');
    assert.strictEqual(trusted({ source: env.parent, origin: env.context.location.origin, data: { channel: resultChannels[0] } }, resultChannels), true, 'pai intermediario na mesma origem permanece compativel');
  }
  {
    const env = harness({ localApi: false });
    const trusted = env.context.FavoritosV2.browser.shellBridge.isTrustedMessage;
    let topReads = 0;
    Object.defineProperty(env.top, 'electronAPI', { get() { topReads += 1; throw new Error('nao ler API do top'); } });
    Object.defineProperty(env.top, 'location', { get() { topReads += 1; throw new Error('nao ler location do top'); } });
    const event = { source: env.top, origin: 'file://', data: { channel: resultChannels[0] } };
    assert.strictEqual(trusted(event, resultChannels), false, 'pagina externa sem API local nao confia em file://');
    assert.strictEqual(topReads, 0, 'validador nao acessa propriedades do shell superior');
    env.context.electronAPI = { executeEmbeddedMlBrowser() {} };
    assert.strictEqual(trusted(event, resultChannels), false, 'uma unica funcao Electron nao autoriza o shell');
    env.context.top = env.self;
    env.context.parent = env.self;
    env.context.electronAPI = { executeEmbeddedMlBrowser() {}, getEmbeddedMlBrowserState() {} };
    assert.strictEqual(trusted({ ...event, source: env.self }, resultChannels), false, 'janela sem shell superior nao ganha excecao file://');
  }

  const requests = [
    ['execute', 'jk-ml-browser-execute-result', env => env.proxy.executeJavaScript('({ total: 1 })')],
    ['click', 'jk-ml-browser-click-result', env => env.proxy.clickAt({ x: 10, y: 20 })],
    ['type', 'jk-ml-browser-type-result', env => env.proxy.typeText({ text: 'consulta' })],
    ['status', 'jk-ml-browser-status-result', env => env.bridge.verificarEstado('https://www.mercadolivre.com.br/', 1000)]
  ];
  for (const [name, responseChannel, start] of requests) {
    const env = harness();
    let settled = false;
    const pending = start(env).then(value => { settled = true; return value; });
    const requestId = env.sent.at(-1).payload.requestId;
    const wrongChannel = resultChannels.find(channel => channel !== responseChannel);
    env.dispatch({ channel: wrongChannel, requestId, result: 'resposta indevida' });
    await env.clock.flush();
    assert.strictEqual(settled, false, name + ': canal errado nao consome requisicao pendente');
    env.dispatch({ channel: responseChannel, requestId: 'desconhecido', result: 'resposta indevida' });
    env.dispatch({ channel: responseChannel, requestId, result: 'resposta indevida' }, 'https://untrusted.test');
    env.dispatch({ channel: responseChannel, requestId, result: 'resposta indevida' }, 'file://', {});
    await env.clock.flush();
    assert.strictEqual(settled, false, name + ': origem ou requestId invalido nao resolve requisicao');
    const expected = { success: true, total: 1, anuncios: [{ id: 'MLB111111111' }] };
    env.dispatch({ channel: responseChannel, requestId, result: expected });
    assert.deepStrictEqual(JSON.parse(JSON.stringify(await pending)), expected, name + ': resposta file:// confiavel chega pelo listener real');
    const calls = env.handled.length;
    env.dispatch({ channel: responseChannel, requestId, result: 'duplicada' });
    assert.strictEqual(env.handled.length, calls + 1);
    assert.strictEqual(env.handled.at(-1).result, false, name + ': resposta duplicada deve ser ignorada');
    await env.clock.tick(20000);
    assert.strictEqual(settled, true, name + ': timer antigo nao modifica promessa resolvida');
  }
  {
    const env = harness();
    const pending = env.proxy.executeJavaScript('true').catch(error => error);
    const requestId = env.sent.at(-1).payload.requestId;
    env.dispatch({ channel: 'jk-ml-browser-execute-result', requestId, error: 'Falha controlada' });
    assert.strictEqual((await pending).message, 'Falha controlada', 'erro autentico deve rejeitar a promessa');
  }
  {
    const env = harness();
    const pending = env.proxy.executeJavaScript('true').catch(error => error);
    const requestId = env.sent.at(-1).payload.requestId;
    await env.clock.tick(20000);
    assert.match((await pending).message, /Tempo limite/, 'ausencia de resposta continua respeitando timeout de vinte segundos');
    env.dispatch({ channel: 'jk-ml-browser-execute-result', requestId, result: 'tardia' });
    assert.strictEqual(env.handled.at(-1).result, false, 'resposta depois do timeout nao ressuscita a requisicao');
  }
  {
    const env = harness();
    const action = { channel: 'jk-favoritos-worker-action', action: 'cancel' };
    env.dispatch(action, 'https://untrusted.test');
    env.dispatch(action, 'file://', {});
    env.dispatch({ ...action, channel: 'jk-unexpected' });
    env.dispatch(Object.assign([], action));
    assert.strictEqual(env.context.cancelCalls, 0, 'mensagens nao confiaveis nao chegam ao cancelamento');
    env.dispatch({ channel: 'jk-favoritos-worker-action', action: 'pause' });
    assert.strictEqual(env.context.mlFavoritosPausado, true, 'pausa confiavel chega ao controlador real');
    env.dispatch({ channel: 'jk-favoritos-worker-action', payload: { action: 'resume' } });
    assert.strictEqual(env.context.mlFavoritosPausado, false, 'retomada confiavel chega ao controlador real');
    env.dispatch(action);
    assert.strictEqual(env.context.cancelCalls, 1, 'cancelamento do shell confiavel chega ao controlador uma vez');
    assert.strictEqual(env.context.mlFavoritosCancelado, true);
  }
  console.log('OK: ponte Favoritos valida emissor, origem, canal e respostas correlacionadas; controles seguros continuam funcionais.');
}

run().catch(error => { console.error(error && error.stack || error); process.exitCode = 1; });
