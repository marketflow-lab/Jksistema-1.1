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
assert(canonical.includes('/medias_compras/styles.css'), 'o stylesheet modular deve ser carregado');
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
