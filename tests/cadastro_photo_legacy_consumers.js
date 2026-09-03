'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');

const vendas = read('static/vendas/assistente.js');
const treinamento = read('static/perguntas_pos_venda/treinamento-ia.js');
const mediasCore = read('static/medias_compras/core.js');
const mediasEditor = read('static/medias_compras/editor.js');
const mediasTabela = read('static/medias_compras/tabela.js');
const sidebar = read('static/ia-sidebar/01-bootstrap-storage-render.part.js');
const importacoesSku = read('static/importacoes_sku.html');
const importacoesLista = read('static/importacoes_lista.html');
const navigation = read('static/auth/navigation.js');

for (const [arquivo, source] of [
  ['vendas/assistente.js', vendas],
  ['perguntas_pos_venda/treinamento-ia.js', treinamento],
  ['medias_compras/core.js', mediasCore],
  ['ia-sidebar/01-bootstrap-storage-render.part.js', sidebar],
  ['importacoes_sku.html', importacoesSku],
  ['importacoes_lista.html', importacoesLista],
]) {
  assert.match(source, /data-jk-auth-src/, `${arquivo} deve declarar imagens protegidas para o helper comum`);
  assert.match(
    source,
    /normalizarUrlFotoCadastro/,
    `${arquivo} deve preservar o caminho completo pelo normalizador compartilhado`,
  );
}

assert.match(vendas, /data-jk-auth-link/, 'a imagem do assistente de Vendas deve declarar o link autenticado');
assert.match(sidebar, /data-jk-auth-link/, 'a imagem da sidebar deve declarar o link autenticado');
assert.doesNotMatch(
  vendas,
  /<a class="sidebar-ai-img-link" href="\$\{src\}"[\s\S]{0,180}<img class="sidebar-ai-img" src="\$\{src\}"/,
  'Vendas não pode injetar a rota protegida diretamente em href/src',
);
assert.doesNotMatch(
  sidebar,
  /<a class="jk-ia-img-link" href="\$\{src\}"[\s\S]{0,220}<img class="jk-ia-img" src="\$\{src\}"/,
  'a sidebar não pode injetar a rota protegida diretamente em href/src',
);
assert.match(
  sidebar,
  /if \(_ehUrlFotoCadastroProtegida\(srcOriginal\)\) return;/,
  'fallback legado deve ignorar rotas que pertencem ao helper autenticado',
);
assert.doesNotMatch(
  sidebar,
  /addPath\(`\/api\/cadastro\/foto\/\$\{tenant\}/,
  'fallback legado não deve fabricar uma segunda rota protegida sem autenticação',
);

assert.doesNotMatch(treinamento, /<img src="\$\{escapeHtml\(foto\)\}"/, 'treinamento não pode usar src direto para foto protegida');
assert.doesNotMatch(mediasEditor, /<img class="foto-sku" src="' \+ escaparHtml\(fotoUrl\)/, 'editor de Médias não pode usar src direto');
assert.doesNotMatch(mediasTabela, /<img class="foto-sku" src="' \+ escaparHtml\(fotoUrl\)/, 'tabela de Médias não pode usar src direto');
assert.doesNotMatch(importacoesSku, /box\.innerHTML = '<img src="' \+ segura/, 'detalhe de Importações não pode usar src direto');
assert.doesNotMatch(importacoesLista, /<img class="foto" src="' \+ escaparHtml\(fotoUrl\)/, 'lista de Importações não pode usar src direto');
assert.doesNotMatch(vendas, /src\s*=\s*src\.split\('\/'\)\.pop\(\)/, 'Vendas não pode achatar foto de loja para basename');
assert.doesNotMatch(sidebar, /src\s*=\s*src\.split\('\/'\)\.pop\(\)/, 'sidebar não pode achatar foto de loja para basename');
for (const [arquivo, source] of [
  ['treinamento-ia.js', treinamento],
  ['medias_compras/core.js', mediasCore],
  ['importacoes_sku.html', importacoesSku],
  ['importacoes_lista.html', importacoesLista],
]) {
  assert.doesNotMatch(
    source,
    /const nomeArquivo\s*=\s*foto\.split/,
    `${arquivo} não pode achatar foto de loja para basename`,
  );
}

const mediaContext = {
  console,
  document: {
    addEventListener() {},
    documentElement: {},
    querySelectorAll() { return []; },
    readyState: 'loading',
  },
  location: { href: 'https://jk.local/medias_compras.html', origin: 'https://jk.local' },
  URL,
  addEventListener() {},
};
mediaContext.window = mediaContext;
vm.createContext(mediaContext);
const helperInicio = navigation.indexOf('(function instalarMidiaAutenticadaJK');
assert(helperInicio >= 0, 'normalizador compartilhado de foto deve existir em auth/navigation');
vm.runInContext(navigation.slice(helperInicio), mediaContext, { filename: 'static/auth/navigation.js#authenticated-media' });

const context = {
  console,
  JKAuthenticatedMedia: mediaContext.JKAuthenticatedMedia,
};
context.globalThis = context;
context.window = context;
vm.createContext(context);
vm.runInContext(mediasCore, context, { filename: 'static/medias_compras/core.js' });

const segmentoA = `sid-${'a'.repeat(64)}`;
const segmentoB = `sid-${'b'.repeat(64)}`;
assert.strictEqual(
  context.obterUrlFoto(`cadastro_fotos/lojas/${segmentoA}/produto.png`),
  `/api/cadastro/foto-arquivo/lojas/${segmentoA}/produto.png`,
);
assert.strictEqual(
  context.obterUrlFoto(`cadastro_fotos/lojas/${segmentoB}/produto.png`),
  `/api/cadastro/foto-arquivo/lojas/${segmentoB}/produto.png`,
);
assert.notStrictEqual(
  context.obterUrlFoto(`cadastro_fotos/lojas/${segmentoA}/produto.png`),
  context.obterUrlFoto(`cadastro_fotos/lojas/${segmentoB}/produto.png`),
  'duas lojas com o mesmo basename devem gerar URLs distintas',
);
assert.strictEqual(
  context.obterUrlFoto('cadastro_fotos/produto legado.png'),
  '/api/cadastro/foto-arquivo/produto%20legado.png',
);
assert.strictEqual(context.obterUrlFoto('cadastro_fotos/../produto.png'), '', 'traversal deve falhar fechado');

assert.strictEqual(
  context.atributoSrcFotoCadastro('/api/cadastro/foto-arquivo/produto.png'),
  'data-jk-auth-src="/api/cadastro/foto-arquivo/produto.png"',
);
assert.strictEqual(
  context.atributoSrcFotoCadastro('/api/cadastro/foto/client-1/lojas/store-1/produto.png'),
  'data-jk-auth-src="/api/cadastro/foto/client-1/lojas/store-1/produto.png"',
);
assert.strictEqual(
  context.atributoSrcFotoCadastro('https://cdn.externo.test/produto.png'),
  'src="https://cdn.externo.test/produto.png"',
  'URL HTTP(S) externa deve continuar usando src direto',
);
assert.strictEqual(
  context.atributoSrcFotoCadastro('//cdn.externo.test/produto.png'),
  'src="//cdn.externo.test/produto.png"',
  'URL externa protocol-relative deve continuar direta e sem autenticação',
);

const vendasContext = { JKAuthenticatedMedia: mediaContext.JKAuthenticatedMedia };
vendasContext.globalThis = vendasContext;
vm.createContext(vendasContext);
const vendasRenderInicio = vendas.indexOf('function escaparHtmlAssistenteVendas');
const vendasRenderFim = vendas.indexOf('function definirTextoMensagemAssistenteVendas', vendasRenderInicio);
assert(vendasRenderInicio >= 0 && vendasRenderFim > vendasRenderInicio, 'render de Vendas deve ser extraível para teste');
vm.runInContext(vendas.slice(vendasRenderInicio, vendasRenderFim), vendasContext, {
  filename: 'static/vendas/assistente.js#render',
});
const vendasHtml = vendasContext.formatarMarkdownBasicoAssistenteVendas(
  `cadastro_fotos/lojas/${segmentoA}/produto.png lojas/${segmentoB}/produto.png`,
);
assert.match(vendasHtml, new RegExp(`data-jk-auth-src="/api/cadastro/foto-arquivo/lojas/${segmentoA}/produto\\.png"`));
assert.match(vendasHtml, new RegExp(`data-jk-auth-src="/api/cadastro/foto-arquivo/lojas/${segmentoB}/produto\\.png"`));
const vendasExterna = vendasContext.formatarMarkdownBasicoAssistenteVendas(
  '![externa](https://cdn.externo.test/produto.png)',
);
assert.match(vendasExterna, /src="https:\/\/cdn\.externo\.test\/produto\.png"/);
assert.doesNotMatch(vendasExterna, /data-jk-auth-src/);
assert.doesNotMatch(
  vendasContext.formatarMarkdownBasicoAssistenteVendas('![x](cadastro_fotos/lojas/%2e%2e/produto.png)'),
  /\/api\/cadastro\/foto/,
  'Vendas deve rejeitar traversal codificado',
);

const sidebarContext = { JKAuthenticatedMedia: mediaContext.JKAuthenticatedMedia };
sidebarContext.globalThis = sidebarContext;
vm.createContext(sidebarContext);
const sidebarRenderInicio = sidebar.indexOf('  function _ehUrlFotoCadastroProtegida');
const sidebarRenderFim = sidebar.indexOf('  function _uniqueList', sidebarRenderInicio);
assert(sidebarRenderInicio >= 0 && sidebarRenderFim > sidebarRenderInicio, 'render da sidebar deve ser extraível para teste');
vm.runInContext(
  `${sidebar.slice(sidebarRenderInicio, sidebarRenderFim)}; this.renderTexto = _renderTexto;`,
  sidebarContext,
  { filename: 'static/ia-sidebar/01-bootstrap-storage-render.part.js#render' },
);
const sidebarHtml = sidebarContext.renderTexto(
  `cadastro_fotos/lojas/${segmentoA}/produto.png lojas/${segmentoB}/produto.png`,
);
assert.match(sidebarHtml, new RegExp(`data-jk-auth-src="/api/cadastro/foto-arquivo/lojas/${segmentoA}/produto\\.png"`));
assert.match(sidebarHtml, new RegExp(`data-jk-auth-src="/api/cadastro/foto-arquivo/lojas/${segmentoB}/produto\\.png"`));

const treinamentoContext = { JKAuthenticatedMedia: mediaContext.JKAuthenticatedMedia };
treinamentoContext.globalThis = treinamentoContext;
vm.createContext(treinamentoContext);
const treinamentoFim = treinamento.indexOf('function produtoTreinamentoSelecionado');
vm.runInContext(treinamento.slice(0, treinamentoFim), treinamentoContext, {
  filename: 'static/perguntas_pos_venda/treinamento-ia.js#foto',
});
assert.strictEqual(
  treinamentoContext.obterFotoProdutoCadastro({ foto: `lojas/${segmentoA}/produto.png` }),
  `/api/cadastro/foto-arquivo/lojas/${segmentoA}/produto.png`,
);
assert.strictEqual(
  treinamentoContext.obterFotoProdutoCadastro({ foto: 'cadastro_fotos/../produto.png' }),
  '',
);

console.log('cadastro authenticated photo legacy consumers: OK');
