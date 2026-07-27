'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'static', 'anunciosml.html'), 'utf8');
const inlineScript = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(match => match[1])
  .filter(source => source.trim())
  .join('\n');

assert.doesNotThrow(() => new Function(inlineScript), 'JavaScript inline da tela de anúncios deve compilar');
assert(html.includes('id="btnExportarExcel"'), 'botão de exportação deve existir');
assert(html.includes('Exportar ativos (Excel)'), 'botão deve explicar que exporta anúncios ativos em Excel');
assert(html.includes('id="exportarExcelMsg"'), 'status acessível da exportação deve existir');
assert(/id="exportarExcelMsg"[^>]*role="status"[^>]*aria-live="polite"/.test(html), 'status deve ser anunciado por leitor de tela');
assert(html.includes('aria-describedby="exportarExcelMsg"'), 'botão deve apontar para o status da exportação');
assert(html.includes("btnExportarExcel.addEventListener('click', exportarAnunciosAtivosExcel)"), 'clique deve acionar o download');

function extractFunction(source, name) {
  const signature = new RegExp(`(?:async\\s+)?function\\s+${name}\\s*\\(`).exec(source);
  assert(signature, `função ausente: ${name}`);
  const openBrace = source.indexOf('{', signature.index);
  let depth = 0;
  for (let index = openBrace; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1;
    if (source[index] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(signature.index, index + 1);
    }
  }
  throw new Error(`não foi possível extrair a função ${name}`);
}

const functionNames = [
  'cancelarRequisicaoAtual',
  'atualizarDisponibilidadeExportacao',
  'definirStatusExportacao',
  'nomeArquivoExportacaoAnuncios',
  'mensagemErroExportacao',
  'exportarAnunciosAtivosExcel',
];
const functionSource = functionNames.map(name => extractFunction(inlineScript, name)).join('\n');
const exportSource = extractFunction(inlineScript, 'exportarAnunciosAtivosExcel');

assert(exportSource.includes('/api/mercadolivre/anuncios/exportar?loja=${encodeURIComponent(loja)}'));
assert(exportSource.includes('headers: { ...obterAuthHeaders() }'));
assert(exportSource.includes("cache: 'no-store'"));
assert(exportSource.includes('const exportAbortController = new AbortController()'));
assert(exportSource.includes('_exportAbortController = exportAbortController'));
assert(exportSource.includes('signal: exportAbortController.signal'));
assert(exportSource.includes('showLoader(mensagemGeracao, true)'));
assert(exportSource.includes("e.name === 'AbortError'"));
assert(exportSource.includes("? 'Exportação cancelada.'"));
assert(exportSource.includes('if (_exportAbortController === exportAbortController) _exportAbortController = null'));
assert(exportSource.includes('hideLoader()'));
assert(!exportSource.includes('setTimeout('), 'exportação não deve impor timeout curto para lojas grandes');
for (const forbidden of ['filtroSku', 'paginaAtualAnuncios', 'anunciosCache', '_cacheAnuncios', 'limparCacheFrontend', 'atualizarAnuncios']) {
  assert(!exportSource.includes(forbidden), `exportação não pode depender da listagem paginada: ${forbidden}`);
}

function createElement(overrides = {}) {
  const attributes = new Map();
  return {
    value: '',
    textContent: '',
    className: '',
    disabled: false,
    style: {},
    setAttribute(name, value) { attributes.set(name, String(value)); },
    removeAttribute(name) { attributes.delete(name); },
    getAttribute(name) { return attributes.has(name) ? attributes.get(name) : null; },
    ...overrides,
  };
}

function headers(values = {}) {
  const normalized = Object.fromEntries(
    Object.entries(values).map(([name, value]) => [name.toLowerCase(), value]),
  );
  return {
    get(name) { return normalized[String(name || '').toLowerCase()] || null; },
  };
}

const lojaSelect = createElement({ value: 'Loja São / Centro' });
const btnExportarExcel = createElement({ textContent: 'Exportar ativos (Excel)' });
const exportarExcelMsg = createElement();
const fetchCalls = [];
const links = [];
const revokedUrls = [];
const abortControllers = [];
const showLoaderCalls = [];
let hideLoaderCalls = 0;
let objectUrlSequence = 0;

class AbortControllerMock {
  constructor() {
    const listeners = [];
    this.signal = {
      aborted: false,
      addEventListener(type, callback) {
        if (type === 'abort') listeners.push(callback);
      },
    };
    this._listeners = listeners;
    abortControllers.push(this);
  }

  abort() {
    if (this.signal.aborted) return;
    this.signal.aborted = true;
    this._listeners.forEach(callback => callback());
  }
}

const sandbox = {
  console,
  Date,
  decodeURIComponent,
  encodeURIComponent,
  lojaSelect,
  btnExportarExcel,
  exportarExcelMsg,
  AbortController: AbortControllerMock,
  obterAuthHeaders() { return { Authorization: 'Bearer teste-contrato' }; },
  showLoader(message, canCancel) { showLoaderCalls.push({ message, canCancel }); },
  hideLoader() { hideLoaderCalls += 1; },
  document: {
    body: { appendChild(link) { link.appended = true; } },
    createElement(tagName) {
      assert.strictEqual(tagName, 'a');
      const link = createElement({
        tagName,
        clicked: false,
        removed: false,
        click() { this.clicked = true; },
        remove() { this.removed = true; },
      });
      links.push(link);
      return link;
    },
  },
  window: {
    URL: {
      createObjectURL(blob) {
        assert(blob && blob.size > 0, 'URL só pode ser criada para Blob não vazio');
        objectUrlSequence += 1;
        return `blob:anuncios-${objectUrlSequence}`;
      },
      revokeObjectURL(url) { revokedUrls.push(url); },
    },
  },
};

vm.createContext(sandbox);
vm.runInContext(`
  let _abortController = null;
  let _exportAbortController = null;
  let exportacaoAnunciosEmAndamento = false;
  ${functionSource}
  this.anunciosExportTeste = {
    cancelarRequisicaoAtual,
    exportarAnunciosAtivosExcel,
    nomeArquivoExportacaoAnuncios,
    getAbortController: () => _exportAbortController
  };
`, sandbox);

const xlsxMime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

(async () => {
  sandbox.fetch = async (url, options) => {
    fetchCalls.push({ url, options });
    assert.strictEqual(btnExportarExcel.disabled, true, 'botão deve ficar desabilitado durante a geração');
    assert.strictEqual(btnExportarExcel.textContent, 'Gerando Excel...');
    assert.strictEqual(btnExportarExcel.getAttribute('aria-busy'), 'true');
    assert.strictEqual(showLoaderCalls.length, 1, 'overlay deve abrir antes da requisição');
    assert.strictEqual(showLoaderCalls[0].canCancel, true, 'overlay deve habilitar cancelamento');
    return {
      ok: true,
      status: 200,
      headers: headers({
        'content-type': xlsxMime,
        'content-disposition': "attachment; filename*=UTF-8''anuncios%20ativos%20Loja.xlsx",
      }),
      async blob() { return { size: 128, type: xlsxMime }; },
    };
  };

  await sandbox.anunciosExportTeste.exportarAnunciosAtivosExcel();

  assert.strictEqual(fetchCalls.length, 1);
  assert.strictEqual(fetchCalls[0].url, '/api/mercadolivre/anuncios/exportar?loja=Loja%20S%C3%A3o%20%2F%20Centro');
  assert.strictEqual(fetchCalls[0].options.method, 'GET');
  assert.strictEqual(fetchCalls[0].options.cache, 'no-store');
  assert.strictEqual(fetchCalls[0].options.headers.Authorization, 'Bearer teste-contrato');
  assert.strictEqual(fetchCalls[0].options.signal, abortControllers[0].signal, 'fetch deve receber o signal exclusivo da exportação');
  assert.strictEqual(links.length, 1);
  assert.strictEqual(links[0].download, 'anuncios ativos Loja.xlsx');
  assert.strictEqual(links[0].clicked, true, 'link temporário deve iniciar o download');
  assert.strictEqual(links[0].removed, true, 'link temporário deve ser removido');
  assert.deepStrictEqual(revokedUrls, ['blob:anuncios-1'], 'object URL deve ser revogada');
  assert.match(exportarExcelMsg.textContent, /Download de anuncios ativos Loja\.xlsx iniciado/);
  assert.match(exportarExcelMsg.className, /success/);
  assert.strictEqual(btnExportarExcel.disabled, false, 'botão deve ser restaurado após sucesso');
  assert.strictEqual(btnExportarExcel.textContent, 'Exportar ativos (Excel)');
  assert.strictEqual(btnExportarExcel.getAttribute('aria-busy'), null);
  assert.strictEqual(hideLoaderCalls, 1, 'overlay deve fechar após sucesso');
  assert.strictEqual(sandbox.anunciosExportTeste.getAbortController(), null, 'controller concluído deve ser limpo');

  const fallback = sandbox.anunciosExportTeste.nomeArquivoExportacaoAnuncios('', 'Loja São / Centro');
  assert.match(fallback, /^anuncios_ativos_Loja_Sao_Centro_\d{4}-\d{2}-\d{2}\.xlsx$/);
  const seguro = sandbox.anunciosExportTeste.nomeArquivoExportacaoAnuncios('attachment; filename="../relatorio:ativo"', 'Loja');
  assert.strictEqual(seguro, 'relatorio_ativo.xlsx', 'nome fornecido pelo servidor deve ser sanitizado');

  sandbox.fetch = async () => ({
    ok: false,
    status: 503,
    headers: headers({ 'content-type': 'application/json' }),
    async json() { return { detail: 'Exportação temporariamente indisponível.' }; },
  });
  await sandbox.anunciosExportTeste.exportarAnunciosAtivosExcel();
  assert.strictEqual(links.length, 1, 'erro HTTP não deve criar download');
  assert.strictEqual(exportarExcelMsg.textContent, 'Exportação temporariamente indisponível.');
  assert.match(exportarExcelMsg.className, /error/);
  assert.strictEqual(btnExportarExcel.disabled, false, 'botão deve ser restaurado após erro HTTP');
  assert.strictEqual(btnExportarExcel.getAttribute('aria-busy'), null);

  sandbox.fetch = async () => ({
    ok: true,
    status: 200,
    headers: headers({ 'content-type': xlsxMime }),
    async blob() { return { size: 0, type: xlsxMime }; },
  });
  await sandbox.anunciosExportTeste.exportarAnunciosAtivosExcel();
  assert.strictEqual(links.length, 1, 'arquivo vazio não deve criar download');
  assert.match(exportarExcelMsg.textContent, /arquivo vazio/i);
  assert.match(exportarExcelMsg.className, /error/);
  assert.strictEqual(btnExportarExcel.disabled, false, 'botão deve ser restaurado após arquivo vazio');

  const linksAntesCancelamento = links.length;
  const showsAntesCancelamento = showLoaderCalls.length;
  const hidesAntesCancelamento = hideLoaderCalls;
  let signalCancelado = null;
  sandbox.fetch = (url, options) => {
    fetchCalls.push({ url, options });
    signalCancelado = options.signal;
    return new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => {
        const error = new Error('aborted');
        error.name = 'AbortError';
        reject(error);
      });
    });
  };
  const exportacaoCancelada = sandbox.anunciosExportTeste.exportarAnunciosAtivosExcel();
  assert.strictEqual(showLoaderCalls.length, showsAntesCancelamento + 1, 'cancelamento deve partir de overlay visível');
  assert.strictEqual(showLoaderCalls.at(-1).canCancel, true);
  assert.strictEqual(sandbox.anunciosExportTeste.getAbortController().signal, signalCancelado);
  sandbox.anunciosExportTeste.cancelarRequisicaoAtual();
  await exportacaoCancelada;
  assert.strictEqual(signalCancelado.aborted, true, 'Cancelar deve abortar o signal enviado ao fetch');
  assert.strictEqual(exportarExcelMsg.textContent, 'Exportação cancelada.');
  assert.match(exportarExcelMsg.className, /error/);
  assert.strictEqual(links.length, linksAntesCancelamento, 'cancelamento não deve criar download');
  assert.strictEqual(btnExportarExcel.disabled, false, 'botão deve ser restaurado após cancelamento');
  assert.strictEqual(btnExportarExcel.textContent, 'Exportar ativos (Excel)');
  assert.strictEqual(btnExportarExcel.getAttribute('aria-busy'), null);
  assert.strictEqual(sandbox.anunciosExportTeste.getAbortController(), null, 'controller cancelado deve ser limpo');
  assert.strictEqual(hideLoaderCalls, hidesAntesCancelamento + 2, 'Cancelar e finally devem ocultar o overlay com segurança');

  lojaSelect.value = '';
  await sandbox.anunciosExportTeste.exportarAnunciosAtivosExcel();
  assert.strictEqual(fetchCalls.length, 2, 'sem loja não deve consultar o endpoint');
  assert.match(exportarExcelMsg.textContent, /Selecione uma loja/);
  assert.strictEqual(btnExportarExcel.disabled, true, 'botão deve permanecer indisponível sem loja');

  console.log('anunciosml export contract: OK');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
