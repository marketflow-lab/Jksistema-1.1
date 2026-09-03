'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const canonicalPath = path.join(root, 'static', 'medias_compras.html');
const mirrorPath = path.join(root, 'medias_compras.html');
const moduleDirectory = path.join(root, 'static', 'medias_compras');
const canonical = fs.readFileSync(canonicalPath, 'utf8');
const mirror = fs.readFileSync(mirrorPath, 'utf8');

const moduleFiles = [
  'core.js',
  'state.js',
  'api.js',
  'transito.js',
  'filtros.js',
  'tabela-colunas.js',
  'listas.js',
  'editor.js',
  'editor-produtos.js',
  'importacao.js',
  'tabela.js',
  'init.js',
];

assert.strictEqual(mirror, canonical, 'o HTML da raiz deve espelhar a fonte oficial em static');
assert(!/<style(?:\s|>)/i.test(canonical), 'o HTML não pode conter CSS embutido');
assert(
  canonical.includes('/medias_compras/styles.css?v=20260901-medias-responsive-width-v1'),
  'o stylesheet responsivo deve ser carregado com cache-bust atualizado',
);
assert(!/\son(?:click|input|change|mouseover|mouseout)=/i.test(canonical), 'o HTML não pode conter handlers inline');

const inlineScripts = [...canonical.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(match => match[1])
  .filter(source => source.trim());
assert.deepStrictEqual(inlineScripts, [], 'o HTML não pode conter JavaScript aplicativo embutido');

let previousIndex = -1;
for (const file of moduleFiles) {
  const sourcePath = path.join(moduleDirectory, file);
  assert(fs.existsSync(sourcePath), `módulo ausente: ${file}`);
  const source = fs.readFileSync(sourcePath, 'utf8');
  assert(/^\(function /m.test(source), `${file} deve usar uma fábrica/IIFE`);
  assert(!/\son(?:click|input|change)=/i.test(source), `${file} não pode gerar handler inline`);
  const lines = source.split(/\r?\n/).length;
  assert(lines <= 500, `${file} ultrapassou o limite de 500 linhas: ${lines}`);

  const scriptIndex = canonical.indexOf(`/medias_compras/${file}`);
  assert(scriptIndex > previousIndex, `ordem de carregamento incorreta para ${file}`);
  previousIndex = scriptIndex;
}

const context = {
  console,
  document: { getElementById: () => null },
  location: { href: '' },
  localStorage: {
    getItem(key) {
      if (key === 'access_token') return 'test-token';
      if (key === 'permissions') return '{}';
      if (key === 'user_data') return JSON.stringify({ client_id: 'tenant-arquitetura', username: 'operador' });
      return null;
    },
    setItem() {},
  },
  addEventListener() {},
  AbortController,
  fetch: async () => ({ ok: true, json: async () => ({}) }),
  alert() {},
};
context.globalThis = context;
context.window = context;
vm.createContext(context);

for (const file of moduleFiles.filter(name => name !== 'init.js')) {
  const source = fs.readFileSync(path.join(moduleDirectory, file), 'utf8');
  vm.runInContext(source, context, { filename: file });
}

assert(context.JKMedias, 'namespace JKMedias ausente');
assert(context.JKMedias.state, 'estado central de Médias ausente');
assert.strictEqual(context.JKMedias.state.lojaSelecionada, '__todas');
assert.strictEqual(context.JKMedias.state.periodoAtual, 12);
assert.match(context.JKMedias.state.LS_COL_WIDTHS_KEY, /^medias_compras_larguras_colunas_v3_[0-9a-f]{16}$/);
assert(!context.JKMedias.state.LS_COL_WIDTHS_KEY.includes('tenant-arquitetura'), 'a chave de preferencia nao deve expor o cliente');
assert(!context.JKMedias.state.LS_COL_WIDTHS_KEY.includes('operador'), 'a chave de preferencia nao deve expor o usuario');
assert.strictEqual(context.JKMedias.state.temEscopoPersistenciaLarguras, true);
assert.strictEqual(context.JKMedias.state.PERFIL_LARGURAS_COLUNAS_SCHEMA, 'jk.medias.column-widths.v3');
assert.strictEqual(context.JKMedias.state.LARGURAS_MINIMAS_COLUNAS.mes, 52);
assert.strictEqual(context.JKMedias.modules['tabela-colunas'].normalizarLarguraColuna('mes_2026-09', 8), 52);
assert.strictEqual(context.JKMedias.modules['tabela-colunas'].normalizarLarguraColuna('saldo_estoque', 12), 80);
assert.strictEqual(context.JKMedias.modules['tabela-colunas'].normalizarLarguraColuna('posicao_estoque', 120), 120);
assert.strictEqual(context.JKMedias.modules['tabela-colunas'].normalizarLarguraColuna('titulo_anuncio', 'invalida'), 220);
assert.strictEqual(context.JKMedias.modules['tabela-colunas'].normalizarLarguraColuna('titulo_anuncio', 999999), 1200);
assert.strictEqual(
  context.JKMedias.modules['tabela-colunas'].obterChaveLargurasColunas(6),
  context.JKMedias.state.LS_COL_WIDTHS_KEY + '_6m',
);
assert.strictEqual(
  context.JKMedias.modules['tabela-colunas'].obterChaveLargurasColunas(12),
  context.JKMedias.state.LS_COL_WIDTHS_KEY + '_12m',
);

function carregarEstadoParaUsuario(userData) {
  const isolated = {
    console,
    document: { getElementById: () => null },
    localStorage: {
      getItem(key) {
        return key === 'user_data' ? JSON.stringify(userData) : null;
      },
      setItem() {},
    },
  };
  isolated.globalThis = isolated;
  isolated.window = isolated;
  vm.createContext(isolated);
  for (const file of ['core.js', 'state.js']) {
    vm.runInContext(fs.readFileSync(path.join(moduleDirectory, file), 'utf8'), isolated, { filename: file });
  }
  return isolated.JKMedias.state;
}

const estadoOutroUsuario = carregarEstadoParaUsuario({ client_id: 'tenant-arquitetura', username: 'outro-operador' });
const estadoOutroCliente = carregarEstadoParaUsuario({ client_id: 'outro-tenant', username: 'operador' });
const estadoSemUsuario = carregarEstadoParaUsuario({ client_id: 'tenant-arquitetura' });
assert.notStrictEqual(estadoOutroUsuario.LS_COL_WIDTHS_KEY, context.JKMedias.state.LS_COL_WIDTHS_KEY, 'usuarios do mesmo cliente devem ter perfis distintos');
assert.notStrictEqual(estadoOutroCliente.LS_COL_WIDTHS_KEY, context.JKMedias.state.LS_COL_WIDTHS_KEY, 'o mesmo usuario em clientes distintos deve ter perfis distintos');
assert.strictEqual(estadoSemUsuario.temEscopoPersistenciaLarguras, false, 'identidade incompleta deve desativar persistencia');
assert.strictEqual(estadoSemUsuario.LS_COL_WIDTHS_KEY, '', 'identidade incompleta nao pode cair em perfil compartilhado');
assert.strictEqual(typeof context.JKMedias.modules.api.requestLatest, 'function', 'API deve controlar respostas atrasadas');
assert.strictEqual(typeof context.JKMedias.modules.api.cancelarRequisicao, 'function', 'API deve permitir cancelamento explícito');
assert(context.JKMedias.assertReady(), 'dependências dos módulos não foram satisfeitas');

const manifest = JSON.parse(fs.readFileSync(path.join(root, 'electron_app', 'installer-required-resources.json'), 'utf8'));
for (const file of ['styles.css', ...moduleFiles]) {
  assert(
    manifest.requiredSourceFiles.includes(`static/medias_compras/${file}`),
    `manifest source não exige ${file}`,
  );
  assert(
    manifest.requiredPackagedFiles.includes(`local_app/static/medias_compras/${file}`),
    `manifest empacotado não exige ${file}`,
  );
}

(async () => {
  const requisicoes = [];
  context.fetch = (input, init) => new Promise((resolve, reject) => {
    const registro = { input, init, resolve, reject };
    requisicoes.push(registro);
    init.signal.addEventListener('abort', () => {
      const erro = new Error('cancelada');
      erro.name = 'AbortError';
      reject(erro);
    }, { once: true });
  });

  const primeira = context.JKMedias.modules.api.requestLatest('visao', '/primeira');
  const segunda = context.JKMedias.modules.api.requestLatest('visao', '/segunda');
  assert.strictEqual(requisicoes[0].init.signal.aborted, true, 'a requisição anterior deve ser cancelada');
  requisicoes[1].resolve({ ok: true });
  await segunda;
  await assert.rejects(primeira, error => error && error.name === 'AbortError');
  console.log('medias frontend architecture: OK');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
