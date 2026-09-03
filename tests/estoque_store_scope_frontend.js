'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const served = fs.readFileSync(path.join(root, 'static', 'estoque.html'), 'utf8');
const helperStart = served.indexOf('    function obterLojaPorStoreId(');
const helperEnd = served.indexOf('    function obterClientId()', helperStart);
assert(helperStart >= 0 && helperEnd > helperStart, 'helpers de identidade de loja ausentes');

const context = {};
vm.createContext(context);
vm.runInContext(`
let lojasDisponiveis = [];
let lojaSelecionada = '__todas';
${served.slice(helperStart, helperEnd)}
globalThis.estoqueScope = {
  setState(lojas, selecionada) {
    lojasDisponiveis = lojas;
    lojaSelecionada = selecionada;
  },
  normalizarPreferenciaLoja,
  rotuloLoja,
  linhaPertenceLojaSelecionada,
  nomeLojaSelecionada,
};
`, context);

const api = context.estoqueScope;
const homonyms = [
  { store_id: 'store-a', nome: 'Loja Homonima' },
  { store_id: 'store-b', nome: 'Loja Homonima' },
];
api.setState(homonyms, 'store-a');
assert.strictEqual(api.normalizarPreferenciaLoja('store-a'), 'store-a');
assert.strictEqual(api.normalizarPreferenciaLoja('Loja Homonima'), '__todas');
assert.strictEqual(api.nomeLojaSelecionada(), 'Loja Homonima');
assert(api.rotuloLoja(homonyms[0]).includes('store-a'));
assert(api.rotuloLoja(homonyms[1]).includes('store-b'));
assert.strictEqual(
  api.linhaPertenceLojaSelecionada({ store_id: 'store-a', loja_sync: 'Loja Homonima' }),
  true,
);
assert.strictEqual(
  api.linhaPertenceLojaSelecionada({ store_id: 'store-b', loja_sync: 'Loja Homonima' }),
  false,
);
assert.strictEqual(
  api.linhaPertenceLojaSelecionada({ store_id: '', loja_sync: 'Loja Homonima' }),
  false,
  'linha legada ambigua nao deve aparecer em uma loja individual',
);
api.setState(homonyms, '__todas');
assert.strictEqual(
  api.linhaPertenceLojaSelecionada({ store_id: '', loja_sync: 'Loja Homonima' }),
  true,
  'linha legada ambigua deve continuar visivel em Todas',
);

api.setState([{ store_id: 'store-a', nome: 'Loja Unica' }], 'store-a');
assert.strictEqual(api.normalizarPreferenciaLoja('Loja Unica'), 'store-a');
assert.strictEqual(
  api.linhaPertenceLojaSelecionada({ store_id: '', loja_sync: 'Loja Unica' }),
  true,
  'fallback legado por nome exato deve funcionar quando unico',
);

for (const contract of [
  "store_id: String(lojaAlvo.store_id || '').trim()",
  "loja: String(lojaAlvo.nome || '').trim()",
  "btn.dataset.storeId = storeId",
  "lojaSelecionada = storeId",
  "lista.filter(row => linhaPertenceLojaSelecionada(row))",
  "loja: nomeLojaSelecionada()",
]) {
  assert(served.includes(contract), `contrato frontend ausente: ${contract}`);
}

assert(!/body:\s*JSON\.stringify\(\{\s*loja:\s*lojaSelecionada\s*\}\)/.test(served));
assert(
  /new URLSearchParams\(\{\s*loja:\s*nomeLojaSelecionada\(\),\s*store_id:\s*lojaSelecionada,/.test(served),
  'consulta de historico deve enviar store_id',
);
assert(
  /body:\s*JSON\.stringify\(\{\s*loja:\s*nomeLojaSelecionada\(\),\s*store_id:\s*lojaSelecionada,/.test(served),
  'sincronizacao de historico deve enviar store_id',
);

const resultHelperStart = served.indexOf('    function escaparHtmlSync(');
const resultHelperEnd = served.indexOf('    function renderProgresso(', resultHelperStart);
assert(resultHelperStart >= 0 && resultHelperEnd > resultHelperStart, 'helpers de resultado do sync ausentes');
const resultContext = {};
vm.createContext(resultContext);
vm.runInContext(`
${served.slice(resultHelperStart, resultHelperEnd)}
globalThis.estoqueSyncResult = {
  classificarFinalSync,
  resumoContasSync,
};
`, resultContext);

const resultApi = resultContext.estoqueSyncResult;
assert.strictEqual(resultApi.classificarFinalSync({ sync_meta: { outcome: 'completed' } }).classe, 'success');
assert.strictEqual(resultApi.classificarFinalSync({ sync_meta: { outcome: 'partial' } }).classe, 'warning');
assert.strictEqual(resultApi.classificarFinalSync({ sync_meta: { outcome: 'failed' } }).classe, 'error');
assert.strictEqual(resultApi.classificarFinalSync({ sync_meta: { outcome: 'cancelled' } }).classe, 'error');
assert.strictEqual(resultApi.classificarFinalSync({ active: true, sync_meta: { outcome: 'running' } }).terminal, false);
assert.strictEqual(resultApi.classificarFinalSync({ active: false, sync_meta: { outcome: 'running' } }).terminal, true);
assert.strictEqual(
  resultApi.classificarFinalSync({ active: false, progress: { percentual: 35 } }).terminal,
  true,
  'worker inativo sem outcome terminal deve encerrar como erro',
);
assert.strictEqual(
  resultApi.classificarFinalSync({ sync_meta: { outcome: 'failed', error: { detail: 'Credencial ausente' } } }).mensagem,
  'Credencial ausente',
  'erro estruturado deve usar detail legivel',
);
const resumoContas = resultApi.resumoContasSync({
  resultados: [
    { store_id: 'store-a', loja: 'Loja <A>', outcome: 'completed', mensagem: '10 SKUs' },
    { store_id: 'store-b', loja: 'Loja B', outcome: 'failed', error: { message: 'Sem <credencial>' } },
    { store_id: 'store-c', loja: 'Loja C', status: 'queued' },
    { store_id: 'store-d', loja: 'Loja D', status: 'running' },
  ],
});
assert.match(resumoContas, /Resumo por conta \(4\)/);
assert.match(resumoContas, /Loja &lt;A&gt;/, 'resumo deve escapar dados retornados pelo backend');
assert.match(resumoContas, /Sem &lt;credencial&gt;/, 'error.message deve ser exibido como texto seguro');
assert(!resumoContas.includes('[object Object]'), 'erro estruturado nao pode virar texto generico');
assert.match(resumoContas, /sync-account-result success/);
assert.match(resumoContas, /sync-account-result error/);
assert.strictEqual(
  (resumoContas.match(/sync-account-result loading/g) || []).length,
  2,
  'contas queued/running devem permanecer neutras',
);

const syncRequestStart = served.indexOf('    async function solicitarSyncEstoque(');
const syncRequestEnd = served.indexOf('    function chavePreferenciaLoja()', syncRequestStart);
const syncRequestSource = served.slice(syncRequestStart, syncRequestEnd);
assert(syncRequestStart >= 0 && syncRequestEnd > syncRequestStart, 'fluxo de solicitacao de estoque ausente');
assert.match(syncRequestSource, /if \(solicitandoSync \|\| syncEmAndamento\) return;/, 'duplo clique deve falhar fechado');
assert.match(
  syncRequestSource,
  /todas_lojas: true,[\s\S]*store_id: '__todas',[\s\S]*loja: 'Todas as lojas'/,
  'Todas as lojas deve iniciar um unico lote no backend',
);
assert.match(
  syncRequestSource,
  /store_id: String\(lojaAlvo\.store_id \|\| ''\)\.trim\(\),[\s\S]*loja: String\(lojaAlvo\.nome \|\| ''\)\.trim\(\)/,
  'sincronizacao individual deve preservar a identidade exata',
);
assert(!/for\s*\(|\.forEach\s*\(/.test(syncRequestSource), 'frontend nao deve criar fila de lojas');

const progressStart = served.indexOf('    async function verificarProgressoEstoque(');
const progressEnd = served.indexOf('    function iniciarMonitoramentoProgresso(', progressStart);
const progressSource = served.slice(progressStart, progressEnd);
assert.strictEqual(
  (progressSource.match(/await carregar\(false\)/g) || []).length,
  1,
  'finalizacao deve recarregar o estoque uma unica vez',
);
assert.match(progressSource, /syncFinalizacaoProcessada = true;[\s\S]*await carregar\(false\)/);
assert.match(
  progressSource,
  /const estoqueRecarregado = await carregar\(false\);[\s\S]*if \(estoqueRecarregado\) \{[\s\S]*renderFinalSync\(final, payload\.sync_meta \|\| \{\}\)/,
  'status e resumo final devem ser restaurados depois da recarga do estoque',
);
assert.match(progressSource, /jobIdPayloadSync\(payload\) !== jobIdEsperado/);
assert.match(
  progressSource,
  /sync\/progress\?job_id=\$\{encodeURIComponent\(jobIdEsperado\)\}/,
  'polling deve consultar explicitamente o job iniciado pelo usuario',
);

const pollingStart = served.indexOf('    function renderProgresso(');
const pollingEnd = served.indexOf('    function formatarDataInput(', pollingStart);
const pollingSource = served.slice(pollingStart, pollingEnd);
assert(pollingStart >= 0 && pollingEnd > pollingStart, 'ciclo de polling de estoque ausente');
assert(!pollingSource.includes('setInterval('), 'polling de estoque deve ser serial, sem setInterval concorrente');
assert.match(pollingSource, /syncPollEmAndamento[\s\S]*return null;/);

const postAwaitIndex = syncRequestSource.indexOf("await fetchComTimeout('/api/estoque/sync'");
const monitorCallIndex = syncRequestSource.indexOf('iniciarMonitoramentoProgresso(');
assert(postAwaitIndex >= 0 && monitorCallIndex > postAwaitIndex, 'polling so pode iniciar depois da resposta do POST');
assert.strictEqual(
  (syncRequestSource.match(/iniciarMonitoramentoProgresso\(/g) || []).length,
  1,
  'solicitacao deve iniciar um unico monitor depois de conhecer o job_id',
);
assert(!syncRequestSource.includes('await verificarProgressoEstoque('), 'erro ou timeout do POST nao pode aderir a job anterior');

const initStart = served.indexOf('    async function inicializarPaginaEstoque()');
const initEnd = served.indexOf('    inicializarPaginaEstoque();', initStart);
const initSource = served.slice(initStart, initEnd);
assert(initStart >= 0 && initEnd > initStart, 'inicializacao da pagina de estoque ausente');
assert.match(initSource, /if \(await adotarSincronizacaoEstoqueAtiva\(\)\) return;/);
assert(
  initSource.indexOf('adotarSincronizacaoEstoqueAtiva') < initSource.indexOf('solicitarSyncEstoque(lojaAuto, true)'),
  'pagina recarregada deve adotar job ativo antes de tentar auto-sync',
);

async function executarSolicitacao(storeId, opcoes = {}) {
  const requestContext = {
    chamadas: [],
    eventos: [],
    monitoramentos: [],
    respostaSync: opcoes.resposta || { ok: true, status: 200, data: { started: true, job_id: 'job-1' } },
    respostaSyncPromise: opcoes.respostaPromise || null,
    erroSync: opcoes.erro || null,
  };
  vm.createContext(requestContext);
  vm.runInContext(`
let solicitandoSync = false;
let syncEmAndamento = false;
let syncFinalizacaoProcessada = true;
let syncGeracaoAtual = 0;
let syncJobIdAtual = '';
let syncPollEmAndamento = null;
let progressTimer = null;
let clientId = 'cliente-1';
const spinnerHtml = '';
const statusEl = { className: '', innerHTML: '', textContent: '' };
const lojasDisponiveis = [{ store_id: 'store-a', nome: 'Loja A' }];
${served.slice(resultHelperStart, resultHelperEnd)}
function obterLojaPorStoreId(storeId) {
  return lojasDisponiveis.find(loja => loja.store_id === storeId) || null;
}
function obterClientId() { return clientId; }
function setSyncButton() {}
function iniciarMonitoramentoProgresso(geracao, jobId) {
  eventos.push('monitor');
  monitoramentos.push({ geracao, jobId });
}
function pararMonitoramentoProgresso() {}
function mostrarStatusCarregando() {}
function obterAuthHeaders() { return {}; }
async function fetchComTimeout(url, options) {
  eventos.push('post');
  chamadas.push({ url, options });
  if (erroSync) throw erroSync;
  const respostaResolvida = respostaSyncPromise ? await respostaSyncPromise : respostaSync;
  return {
    ok: respostaResolvida.ok !== false,
    status: respostaResolvida.status || 200,
    json: async () => respostaResolvida.data || {},
  };
}
${syncRequestSource}
globalThis.executarSolicitacaoCapturada = solicitarSyncEstoque;
globalThis.estadoSolicitacaoCapturado = () => ({
  chamadas,
  eventos,
  monitoramentos,
  statusClass: statusEl.className,
  statusText: statusEl.textContent,
  syncJobIdAtual,
});
`, requestContext);
  const execucao = requestContext.executarSolicitacaoCapturada(storeId, false);
  if (opcoes.inspecionarPendente) await opcoes.inspecionarPendente(requestContext);
  await execucao;
  if (opcoes.repetir) await requestContext.executarSolicitacaoCapturada(storeId, false);
  return requestContext.estadoSolicitacaoCapturado();
}

function respostaPolling(payload) {
  return { ok: true, status: 200, json: async () => payload };
}

function criarDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function criarHarnessPolling(geracao = 1, jobId = 'job-1', opcoes = {}) {
  const pollingContext = {
    respostasPolling: [],
    carregarGate: opcoes.carregarPromise || null,
    carregarResultado: opcoes.carregarResultado !== false,
  };
  vm.createContext(pollingContext);
  vm.runInContext(`
let syncGeracaoAtual = ${JSON.stringify(geracao)};
let syncJobIdAtual = ${JSON.stringify(jobId)};
let syncFinalizacaoProcessada = false;
let syncPollEmAndamento = null;
let syncEmAndamento = true;
let solicitandoSync = false;
let progressTimer = null;
const SYNC_PROGRESS_TIMEOUT_MS = 8000;
const spinnerHtml = '';
const statusEl = { className: 'status-bar inicial', innerHTML: 'INICIAL', textContent: 'INICIAL' };
const respostasPolling = globalThis.respostasPolling;
const carregarGate = globalThis.carregarGate;
const carregarResultado = globalThis.carregarResultado;
let fetches = 0;
let recargas = 0;
const botoes = [];
function obterAuthHeaders() { return {}; }
function setSyncButton(valor) { botoes.push(valor); }
async function fetchComTimeout() {
  fetches += 1;
  const proxima = respostasPolling.shift();
  if (!proxima) throw new Error('Resposta de polling ausente no teste.');
  return await proxima;
}
async function carregar() {
  recargas += 1;
  if (carregarGate) await carregarGate;
  statusEl.className = 'status-bar empty';
  statusEl.innerHTML = 'Nenhum registro encontrado.';
  statusEl.textContent = 'Nenhum registro encontrado.';
  if (!carregarResultado) {
    statusEl.className = 'status-bar error';
    statusEl.innerHTML = 'Erro ao carregar estoque: HTTP 500';
    statusEl.textContent = 'Erro ao carregar estoque: HTTP 500';
  }
  return carregarResultado;
}
${served.slice(resultHelperStart, resultHelperEnd)}
${pollingSource}
globalThis.pollingApi = {
  verificarProgressoEstoque,
  setContexto(novaGeracao, novoJobId, finalizado = false) {
    syncGeracaoAtual = novaGeracao;
    syncJobIdAtual = novoJobId;
    syncFinalizacaoProcessada = finalizado;
  },
  estado() {
    return {
      className: statusEl.className,
      innerHTML: statusEl.innerHTML,
      textContent: statusEl.textContent,
      fetches,
      recargas,
      finalizado: syncFinalizacaoProcessada,
      syncEmAndamento,
      botoes: Array.from(botoes),
    };
  },
};
`, pollingContext);
  return pollingContext;
}

(async () => {
  const todas = await executarSolicitacao('__todas', { repetir: true });
  assert.strictEqual(todas.chamadas.length, 1, 'duplo clique nao pode iniciar uma segunda solicitacao');
  assert.strictEqual(todas.chamadas[0].url, '/api/estoque/sync');
  assert.strictEqual(
    todas.chamadas[0].options.body,
    JSON.stringify({ todas_lojas: true, store_id: '__todas', loja: 'Todas as lojas' }),
  );
  assert.deepStrictEqual(Array.from(todas.eventos), ['post', 'monitor']);
  assert.strictEqual(todas.monitoramentos[0].jobId, 'job-1');

  const loja = await executarSolicitacao('store-a');
  assert.strictEqual(loja.chamadas.length, 1);
  assert.strictEqual(
    loja.chamadas[0].options.body,
    JSON.stringify({ store_id: 'store-a', loja: 'Loja A' }),
  );

  const detalhe = await executarSolicitacao('store-a', {
    resposta: { ok: false, status: 409, data: { detail: { message: 'Falha detalhada' } } },
  });
  assert.strictEqual(detalhe.statusText, 'Erro ao sincronizar: Falha detalhada');
  assert(!detalhe.statusText.includes('[object Object]'));

  const semJob = await executarSolicitacao('__todas', {
    resposta: { ok: true, status: 200, data: { started: false, already_running: true } },
  });
  assert.strictEqual(semJob.monitoramentos.length, 0, 'already_running sem job_id nao pode iniciar polling');
  assert.match(semJob.statusText, /sem um job_id válido/);

  const jobExistente = await executarSolicitacao('__todas', {
    resposta: { ok: true, status: 200, data: { started: false, already_running: true, job_id: 'job-existente' } },
  });
  assert.strictEqual(jobExistente.monitoramentos.length, 1);
  assert.strictEqual(jobExistente.monitoramentos[0].jobId, 'job-existente');

  const postPendente = criarDeferred();
  const depoisDoPost = await executarSolicitacao('__todas', {
    respostaPromise: postPendente.promise,
    inspecionarPendente(contexto) {
      assert.deepStrictEqual(Array.from(contexto.eventos), ['post']);
      assert.strictEqual(contexto.monitoramentos.length, 0, 'monitor nao pode iniciar enquanto o POST esta pendente');
      postPendente.resolve({ ok: true, status: 200, data: { started: true, job_id: 'job-depois-post' } });
    },
  });
  assert.strictEqual(depoisDoPost.monitoramentos.length, 1);
  assert.strictEqual(depoisDoPost.monitoramentos[0].jobId, 'job-depois-post');

  const timeoutError = new Error('timeout');
  timeoutError.name = 'AbortError';
  const timeout = await executarSolicitacao('__todas', { erro: timeoutError });
  assert.deepStrictEqual(Array.from(timeout.eventos), ['post']);
  assert.strictEqual(timeout.monitoramentos.length, 0);
  assert.strictEqual(timeout.syncJobIdAtual, '');
  assert.match(timeout.statusText, /resultado é incerto/);

  const recargaPendente = criarDeferred();
  const finalHarness = criarHarnessPolling(1, 'job-vazio', { carregarPromise: recargaPendente.promise });
  finalHarness.respostasPolling.push(Promise.resolve(respostaPolling({
    active: false,
    progress: { percentual: 100, mensagem: 'Lote encerrado' },
    logs: [],
    sync_meta: {
      job_id: 'job-vazio',
      outcome: 'partial',
      resultados: [{ store_id: 'store-a', loja: 'Loja A', status: 'failed', erro: { message: 'Sem itens' } }],
    },
  })));
  const pollFinal = finalHarness.pollingApi.verificarProgressoEstoque(1, 'job-vazio');
  await new Promise(resolve => setImmediate(resolve));
  const estadoDuranteRecarga = finalHarness.pollingApi.estado();
  assert.strictEqual(estadoDuranteRecarga.recargas, 1);
  assert.strictEqual(estadoDuranteRecarga.syncEmAndamento, true, 'sync deve continuar bloqueada durante a recarga final');
  assert(!estadoDuranteRecarga.botoes.includes(false), 'botao nao pode ser liberado antes do fim da recarga');
  recargaPendente.resolve();
  await pollFinal;
  const estadoFinal = finalHarness.pollingApi.estado();
  assert.strictEqual(estadoFinal.recargas, 1);
  assert.strictEqual(estadoFinal.className, 'status-bar warning');
  assert.match(estadoFinal.innerHTML, /Resumo por conta \(1\)/);
  assert.match(estadoFinal.innerHTML, /Sem itens/);
  assert(!estadoFinal.innerHTML.includes('Nenhum registro encontrado.'), 'estoque vazio nao pode apagar o resumo final');
  assert.strictEqual(estadoFinal.syncEmAndamento, false);
  assert.strictEqual(estadoFinal.botoes.at(-1), false);

  const falhaRecargaHarness = criarHarnessPolling(1, 'job-recarga-falhou', { carregarResultado: false });
  falhaRecargaHarness.respostasPolling.push(Promise.resolve(respostaPolling({
    active: false,
    progress: { percentual: 100, mensagem: 'Lote encerrado' },
    logs: [],
    sync_meta: { job_id: 'job-recarga-falhou', outcome: 'completed', resultados: [] },
  })));
  await falhaRecargaHarness.pollingApi.verificarProgressoEstoque(1, 'job-recarga-falhou');
  const estadoFalhaRecarga = falhaRecargaHarness.pollingApi.estado();
  assert.strictEqual(estadoFalhaRecarga.className, 'status-bar error');
  assert.match(estadoFalhaRecarga.innerHTML, /Erro ao carregar estoque: HTTP 500/);
  assert(!estadoFalhaRecarga.innerHTML.includes('Atualização concluída'));
  assert.strictEqual(estadoFalhaRecarga.syncEmAndamento, false);
  assert.strictEqual(estadoFalhaRecarga.botoes.at(-1), false);

  const corridaHarness = criarHarnessPolling(1, 'job-antigo');
  const respostaAntiga = criarDeferred();
  corridaHarness.respostasPolling.push(respostaAntiga.promise);
  const pollAntigo = corridaHarness.pollingApi.verificarProgressoEstoque(1, 'job-antigo');
  corridaHarness.pollingApi.setContexto(2, 'job-novo');
  corridaHarness.respostasPolling.push(Promise.resolve(respostaPolling({
    active: true,
    progress: { percentual: 25, mensagem: 'JOB NOVO' },
    logs: [],
    sync_meta: { job_id: 'job-novo', outcome: 'running', resultados: [] },
  })));
  await corridaHarness.pollingApi.verificarProgressoEstoque(2, 'job-novo');
  const htmlNovo = corridaHarness.pollingApi.estado().innerHTML;
  assert.match(htmlNovo, /JOB NOVO/);
  respostaAntiga.resolve(respostaPolling({
    active: false,
    progress: { percentual: 100, mensagem: 'JOB ANTIGO' },
    logs: [],
    sync_meta: { job_id: 'job-antigo', outcome: 'completed', resultados: [] },
  }));
  await pollAntigo;
  assert.strictEqual(corridaHarness.pollingApi.estado().innerHTML, htmlNovo, 'resposta antiga fora de ordem deve ser ignorada');
  assert.strictEqual(corridaHarness.pollingApi.estado().recargas, 0);

  const jobErradoHarness = criarHarnessPolling(3, 'job-esperado');
  jobErradoHarness.respostasPolling.push(Promise.resolve(respostaPolling({
    active: false,
    progress: { percentual: 100, mensagem: 'JOB ERRADO' },
    logs: [],
    sync_meta: { job_id: 'job-diferente', outcome: 'completed', resultados: [] },
  })));
  await jobErradoHarness.pollingApi.verificarProgressoEstoque(3, 'job-esperado');
  assert.strictEqual(jobErradoHarness.pollingApi.estado().innerHTML, 'INICIAL');
  assert.strictEqual(jobErradoHarness.pollingApi.estado().recargas, 0);

  const concorrenteHarness = criarHarnessPolling(4, 'job-unico');
  const respostaPendente = criarDeferred();
  concorrenteHarness.respostasPolling.push(respostaPendente.promise);
  const primeiroPoll = concorrenteHarness.pollingApi.verificarProgressoEstoque(4, 'job-unico');
  await concorrenteHarness.pollingApi.verificarProgressoEstoque(4, 'job-unico');
  assert.strictEqual(concorrenteHarness.pollingApi.estado().fetches, 1, 'poll concorrente do mesmo job deve ser bloqueado');
  respostaPendente.resolve(respostaPolling({
    active: true,
    progress: { percentual: 50, mensagem: 'Em andamento' },
    logs: [],
    sync_meta: { job_id: 'job-unico', outcome: 'running', resultados: [] },
  }));
  await primeiroPoll;

  const finalizadoHarness = criarHarnessPolling(5, 'job-finalizado');
  finalizadoHarness.pollingApi.setContexto(5, 'job-finalizado', true);
  finalizadoHarness.respostasPolling.push(Promise.resolve(respostaPolling({
    active: true,
    progress: { percentual: 10, mensagem: 'Resposta tardia' },
    sync_meta: { job_id: 'job-finalizado', outcome: 'running' },
  })));
  await finalizadoHarness.pollingApi.verificarProgressoEstoque(5, 'job-finalizado');
  assert.strictEqual(finalizadoHarness.pollingApi.estado().fetches, 0, 'nenhuma resposta deve ser consultada depois da finalizacao');

  const expiradoHarness = criarHarnessPolling(6, 'job-expirado');
  expiradoHarness.respostasPolling.push(Promise.resolve({
    ok: false,
    status: 404,
    json: async () => ({ detail: { message: 'Job de estoque não encontrado ou expirado.' } }),
  }));
  await expiradoHarness.pollingApi.verificarProgressoEstoque(6, 'job-expirado');
  const estadoExpirado = expiradoHarness.pollingApi.estado();
  assert.strictEqual(estadoExpirado.className, 'status-bar error');
  assert.match(estadoExpirado.textContent, /não encontrado ou expirado/);
  assert.strictEqual(estadoExpirado.finalizado, true);
  assert.strictEqual(estadoExpirado.syncEmAndamento, false);
  assert.strictEqual(estadoExpirado.botoes.at(-1), false);

  console.log('estoque store scope frontend: OK');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
