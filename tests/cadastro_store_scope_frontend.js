'use strict';

const assert = require('assert');
const crypto = require('crypto');
const vm = require('vm');
const { read } = require('./helpers/cadastro_frontend_sources');

const stored = new Map([
  ['user_data', JSON.stringify({ client_id: 'cliente-1' })],
  ['cadastro_store_id_por_cliente:cliente-1', 'store-b'],
]);
const context = {
  URLSearchParams,
  document: { createElement() { return {}; } },
  fetch: async () => { throw new Error('fetch não esperado'); },
  location: { pathname: '/cadastro.html', search: '' },
  localStorage: {
    getItem(key) { return stored.has(key) ? stored.get(key) : null; },
    removeItem(key) { stored.delete(key); },
    setItem(key, value) { stored.set(key, String(value)); },
  },
};
context.window = context;
vm.createContext(context);
vm.runInContext(read('static/cadastro/form/00-core.js'), context, { filename: 'static/cadastro/form/00-core.js' });

const stores = context.JKCadastroStore;
assert(context.JKCadastroForm.garantirCamposProduto([]).includes('nome'), 'loja vazia deve manter formulário útil para primeiro produto');
const lojas = stores.normalizarLojas([
  { store_id: 'store-b', nome: 'Loja B' },
  { store_id: 'store-a', nome: 'Loja A' },
  { nome: 'Sem identidade estável' },
]);
assert.strictEqual(Array.from(lojas, item => item.store_id).join('|'), 'store-a|store-b');
assert.strictEqual(stores.resolverStoreId(lojas, { clientId: 'cliente-1', search: '' }), 'store-b', 'preferência deve ser isolada por cliente');
assert.strictEqual(stores.resolverStoreId(lojas, { clientId: 'cliente-1', search: '?store_id=store-a' }), 'store-a', 'URL válida deve prevalecer sobre preferência local');
assert.throws(
  () => stores.resolverStoreId(lojas, { clientId: 'cliente-1', search: '?store_id=store-inexistente' }),
  /Loja inválida/,
  'store_id desconhecido na URL deve falhar fechado',
);

const seletorDuplicado = {
  children: [],
  innerHTML: '',
  value: '',
  appendChild(option) { this.children.push(option); },
};
stores.preencherSeletor(seletorDuplicado, [
  { store_id: 'store-a', nome: 'Loja Principal' },
  { store_id: 'store-b', nome: 'Loja-Principál' },
], { permitirTodas: false });
assert.strictEqual(seletorDuplicado.children[1].textContent, 'Loja Principal (store-a)');
assert.strictEqual(seletorDuplicado.children[2].textContent, 'Loja-Principál (store-b)');

assert.strictEqual(stores.apiLoja('store-a', 'produtos'), '/api/cadastro/lojas/store-a/produtos');
assert.strictEqual(
  stores.urlPagina('/cadastro_editar_item.html', 'store-a', { sku: 'SKU 1/2' }),
  '/cadastro_editar_item.html?store_id=store-a&sku=SKU+1%2F2',
);
assert.strictEqual(
  stores.urlFoto('cliente-1', 'store-a', 'cadastro_fotos/lojas/store-a/foto 1.png'),
  '/api/cadastro/foto/cliente-1/lojas/store-a/foto%201.png',
);
const storeComPontoEspaco = 'Loja . Centro';
const segmentoOpaco = `sid-${crypto.createHash('sha256').update(storeComPontoEspaco, 'utf8').digest('hex')}`;
assert.strictEqual(
  stores.urlFoto(
    'cliente-1',
    storeComPontoEspaco,
    `cadastro_fotos/lojas/${segmentoOpaco}/foto 1.png`,
  ),
  `/api/cadastro/foto/cliente-1/lojas/${segmentoOpaco}/foto%201.png?store_id=Loja%20.%20Centro`,
  'segmento opaco deve manter o store_id exato apenas como contexto validado pelo backend',
);
assert.strictEqual(
  stores.urlFoto('cliente-1', storeComPontoEspaco, `lojas/${segmentoOpaco}/foto 1.png`),
  `/api/cadastro/foto/cliente-1/lojas/${segmentoOpaco}/foto%201.png?store_id=Loja%20.%20Centro`,
  'referência scoped sem o prefixo cadastro_fotos deve preservar segmento e loja exata',
);
assert.strictEqual(
  stores.urlFoto('cliente-1', storeComPontoEspaco, 'cadastro_fotos/lojas/sid-invalido/foto.png'),
  '',
  'segmento opaco malformado deve falhar fechado',
);
assert.strictEqual(
  stores.urlFoto('cliente-1', 'store-a', 'cadastro_fotos/foto antiga.png'),
  '/api/cadastro/foto/cliente-1/foto%20antiga.png',
  'foto de sombra legada deve permanecer na rota legada',
);
assert.strictEqual(
  stores.urlFoto('cliente-1', 'store-a', 'cadastro_fotos/lojas/store-b/foto.png'),
  '',
  'caminho que declara outra loja deve ser rejeitado',
);

for (const referenciaExterna of [
  'https://cdn.exemplo.invalid/produtos/009.jpg',
  '//cdn.exemplo.invalid/produtos/009.jpg',
  'ftp://cdn.exemplo.invalid/produtos/009.jpg',
  's3://bucket/produtos/009.jpg',
  'gs://bucket/produtos/009.png',
  'blob:https://cdn.exemplo.invalid/produtos/009.jpg',
  'custom+photo://catalogo/produtos/009.webp',
  'data:image/jpeg;base64,AA==',
]) {
  assert.strictEqual(
    stores.urlFoto('cliente-1', 'store-a', referenciaExterna),
    referenciaExterna,
    `referência externa não pode virar rota local: ${referenciaExterna}`,
  );
}
assert.strictEqual(
  stores.urlFoto('cliente-1', 'store-a', 'file:///C:/Fotos/foto local.png'),
  '/api/cadastro/foto/cliente-1/foto%20local.png',
  'file: deve continuar tratado como referência local segura',
);
assert.strictEqual(
  stores.urlFoto('cliente-1', 'store-a', 'C:\\Fotos\\foto local.png'),
  '/api/cadastro/foto/cliente-1/foto%20local.png',
  'caminho absoluto do Windows deve continuar tratado como referência local segura',
);
assert.strictEqual(
  stores.urlFoto('cliente-1', 'store-a', 'imagens/foto local.png'),
  '/api/cadastro/foto/cliente-1/foto%20local.png',
  'caminho relativo local deve continuar limitado ao basename seguro',
);
for (const [referenciaLocal, urlEsperada] of [
  ['imagem.jpg', '/api/cadastro/foto/cliente-1/imagem.jpg'],
  ['cadastro_fotos/imagem.jpg', '/api/cadastro/foto/cliente-1/imagem.jpg'],
  ['api/cadastro/foto-arquivo/imagem.jpg', '/api/cadastro/foto/cliente-1/imagem.jpg'],
  ['x/../api/cadastro/foto/cliente-1/imagem.jpg', '/api/cadastro/foto/cliente-1/imagem.jpg'],
  ['https:/api/cadastro/foto-arquivo/imagem.jpg', '/api/cadastro/foto/cliente-1/imagem.jpg'],
  ['C:/dados/cadastro_fotos/imagem.jpg', '/api/cadastro/foto/cliente-1/imagem.jpg'],
  ['file:///C:/dados/cadastro_fotos/imagem.jpg', '/api/cadastro/foto/cliente-1/imagem.jpg'],
  ['foo/imagem.jpg', '/api/cadastro/foto/cliente-1/imagem.jpg'],
  ['/qualquer/imagem.jpg', '/api/cadastro/foto/cliente-1/imagem.jpg'],
  ['lojas/store-a/imagem.jpg', '/api/cadastro/foto/cliente-1/lojas/store-a/imagem.jpg'],
  ["'cadastro_fotos/imagem.jpg'", '/api/cadastro/foto/cliente-1/imagem.jpg'],
  ['"imagem.jpg"', '/api/cadastro/foto/cliente-1/imagem.jpg'],
  ['`api/cadastro/foto-arquivo/imagem.jpg`', '/api/cadastro/foto/cliente-1/imagem.jpg'],
  ['%27cadastro_fotos/imagem.jpg%27', '/api/cadastro/foto/cliente-1/imagem.jpg'],
]) {
  assert.strictEqual(
    stores.urlFoto('cliente-1', 'store-a', referenciaLocal),
    urlEsperada,
    `referência local deve continuar na rota segura: ${referenciaLocal}`,
  );
}

const produtoA = { sku: 'SKU-IGUAL', nome: 'Descrição da loja A' };
assert.strictEqual(stores.salvarCacheEdicao('cliente-1', 'store-a', produtoA), true);
assert.strictEqual(stores.lerCacheEdicao('cliente-1', 'store-a', 'SKU-IGUAL').nome, produtoA.nome);
assert.strictEqual(stores.lerCacheEdicao('cliente-1', 'store-b', 'SKU-IGUAL'), null, 'mesma SKU de outra loja não pode reutilizar cache');
stored.set('cadastro_editar_item', JSON.stringify({ sku: 'SKU-IGUAL', nome: 'cache legado' }));
assert.strictEqual(stores.lerCacheEdicao('cliente-1', 'store-a', 'SKU-IGUAL'), null, 'cache antigo sem envelope deve ser rejeitado');

const actions = read('static/cadastro/main/03-actions.js');
const productLoader = read('static/cadastro/main/03-produtos-carregamento.js');
const init = read('static/cadastro/main/04-init.js');
const html = read('static/cadastro.html');
const styles = read('static/cadastro/styles/main.css');
const core = read('static/cadastro/01-core.js');
const products = read('static/cadastro/main/01-produtos.js');
const secondary = read('static/cadastro/main/02-custos-mlb.js');
const selector = read('static/cadastro/seletor-edicao.js');
const editor = read('static/cadastro/editar-item.js');
const inclusion = read('static/cadastro/incluir-item.js');

assert.match(actions, /const semLoja = !state\.storeIdSelecionado;[\s\S]*const somenteLeitura = semLoja \|\| state\.carregandoProdutos/);
assert.match(actions, /btnEditarCadastro[\s\S]*btnIncluirCadastro[\s\S]*btnImportarColunas[\s\S]*btnAtualizarCustosImpostos/);
assert.match(productLoader, /storeId[\s\S]*storeTools\.apiLoja\(storeId, 'produtos'\)[\s\S]*'\/api\/cadastro\/lojas\/produtos'[\s\S]*view=summary/, 'cada escopo deve usar a projeção resumida e Todas deve usar o endpoint consolidado');
assert.doesNotMatch(productLoader, /Promise\.all\(lojasAlvo\.map\(/, 'Todas não deve disparar uma requisição por loja');
assert.match(actions, /function rotuloLojaExibicao[\s\S]*homonimas\.length > 1[\s\S]*\$\{loja\.store_id\}/, 'lojas homônimas devem ser distinguíveis também na visão consolidada');
assert.match(productLoader, /let requestSeq = 0[\s\S]*const minhaSeq = \+\+requestSeq[\s\S]*if \(minhaSeq !== requestSeq \|\| escopoSolicitado !== escopoAtual\(\)\) return false;/, 'troca rápida deve descartar resposta ou escopo obsoleto');
assert.match(productLoader, /const preservarTabela = state\.produtosEscopoCarregado === escopoSolicitado[\s\S]*if \(!preservarTabela\) \{[\s\S]*state\.produtos = \[\]/, 'troca de escopo deve limpar produtos antigos e refresh deve preservar dados úteis');
assert.match(productLoader, /state\.carregandoProdutos = true;[\s\S]*opcoes\.atualizarEstadoMutacoes\(\)[\s\S]*state\.carregandoProdutos = false;/, 'mutações devem permanecer desabilitadas durante a troca');
assert.match(productLoader, /TIMEOUT_MS = 30000[\s\S]*new global\.AbortController\(\)[\s\S]*controller\.abort\(\)/, 'carregamentos devem ter cancelamento e prazo finito');
assert.match(productLoader, /global\.fetch\(urlEscopo\(storeId\)[\s\S]*cache: 'no-store'[\s\S]*signal: controller\.signal/, 'produtos devem ignorar cache e propagar o cancelamento');
assert.match(productLoader, /async function lerRespostaJsonSegura[\s\S]*response\.text\(\)[\s\S]*JSON\.parse\(body\)/, 'corpo vazio, HTML e JSON truncado devem falhar de forma controlada');
assert.match(productLoader, /function validarEnvelopeConsolidado[\s\S]*Array\.isArray\(payload\.lojas\)[\s\S]*Number\.isInteger\(payload\.total\)[\s\S]*typeof payload\.partial !== 'boolean'/, 'Todas deve validar integralmente o envelope consolidado');
assert.match(core, /if \(!state\.storeIdSelecionado\) return escaparHtml\(formatarSkuExibicao\(sku\)\)/, 'Todas não deve oferecer link de edição');
assert.match(products, /if \(!state\.storeIdSelecionado\) return;/, 'Todas não deve autorizar clique de edição');
assert.match(secondary, /`store:\$\{encodeURIComponent\(storeId\)\}:\$\{encodeURIComponent\(sku\)\}`[\s\S]*produto\.store_id[\s\S]*selecao\.storeId/, 'detalhe MLB deve distinguir a mesma SKU entre lojas');
assert.match(init, /cadastroLojaSelect\.addEventListener\('change',[\s\S]*actions\.selecionarLoja/);
assert.match(html, /id="cadastroLojaBotoes"[\s\S]*role="group"[\s\S]*id="cadastroLojaSelect" hidden/, 'Cadastro deve exibir os botões e manter o select oculto compatível');
assert.match(actions, /function renderBotoesLojas[\s\S]*rotulo: 'Todas as lojas'[\s\S]*button\.dataset\.storeId = opcao\.store_id/, 'botões devem representar todas as lojas pelo store_id exato');
assert.match(actions, /function atualizarBotoesLojas[\s\S]*classList\.toggle\('active', ativo\)[\s\S]*aria-pressed/, 'seleção visível e acessível deve acompanhar o estado');
assert.match(actions, /function selecionarLoja[\s\S]*elements\.cadastroLojaSelect\.value = valor;[\s\S]*await carregarProdutos\(\)/, 'botões e seletor compatível devem compartilhar a mesma troca de escopo');
assert.match(actions, /preencherSeletor\([\s\S]*renderBotoesLojas\(\);[\s\S]*try \{[\s\S]*resolverStoreId/, 'botões devem permitir recuperação mesmo quando a URL trouxer store_id inválido');
assert.match(styles, /\.loja-group\s*\{[^}]*flex-wrap:wrap/, 'lojas devem quebrar linha sem sumir em telas estreitas');
assert.match(styles, /\.loja-btn\s*\{[^}]*max-width:100%[^}]*white-space:normal[^}]*overflow-wrap:anywhere/, 'rótulo longo deve quebrar dentro do botão');
assert.match(styles, /\.loja-btn\.active\s*\{[^}]*background:#4facfe/, 'botão ativo deve seguir o padrão visual do Estoque');

assert.match(actions, /formData\.append\('modo', 'geral'\)[\s\S]*apiLoja\(storeId, 'importar-colunas'\)/, 'importação geral deve usar modo geral e rota da loja');
assert.match(actions, /formData\.append\('modo', 'custos'\)[\s\S]*apiLoja\(storeId, 'importar-colunas'\)/, 'importação de custos deve usar modo custos e rota da loja');
assert.match(selector, /apiLoja\(storeIdSelecionado, 'produtos'\)/);
assert.match(editor, /apiLoja\(state\.storeIdSelecionado, `produtos\/\$\{encodeURIComponent\(state\.skuOriginal\)\}`\)/);
assert.match(editor, /!state\.carregadoDaRede/);
assert.match(inclusion, /apiLoja\(state\.storeIdSelecionado, 'produtos'\)/);
assert.doesNotMatch(editor, /catch \(_error\) \{\}[\s\S]*if \(!state\.produto\)/, 'falha de rede não pode cair silenciosamente para cache');

console.log('cadastro frontend store scope: OK');
