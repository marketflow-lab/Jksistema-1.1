'use strict';

const assert = require('assert');
const vm = require('vm');
const { read } = require('./helpers/cadastro_frontend_sources');

(async () => {
  const fetchCalls = [];
  const objectUrls = [];
  const revokedUrls = [];
  class BrowserURL extends URL {
    static createObjectURL(blob) {
      const url = `blob:cadastro-${objectUrls.length + 1}`;
      objectUrls.push({ url, blob });
      return url;
    }
    static revokeObjectURL(url) { revokedUrls.push(url); }
  }
  const storage = new Map([['user_data', JSON.stringify({ client_id: 'client-1' })]]);
  const elements = new Map();
  const context = {
    console,
    document: {
      createElement() { return {}; },
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, {});
        return elements.get(id);
      },
    },
    fetch: async (url, options) => {
      fetchCalls.push({ url, options });
      return { ok: true, status: 200, blob: async () => ({ type: 'image/png', size: 3 }) };
    },
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      removeItem(key) { storage.delete(key); },
      setItem(key, value) { storage.set(key, String(value)); },
    },
    location: { origin: 'https://jk.local', pathname: '/cadastro.html', search: '' },
    URL: BrowserURL,
    URLSearchParams,
  };
  context.window = context;
  vm.createContext(context);

  const formCore = read('static/cadastro/form/00-core.js');
  vm.runInContext(formCore, context, { filename: 'static/cadastro/form/00-core.js' });
  const stores = context.JKCadastroStore;
  const auth = () => ({ Authorization: 'Bearer segredo-teste' });

  const protegida = '/api/cadastro/foto/client-1/lojas/store-1/foto.png';
  const carregada = await stores.carregarFotoAutenticada(protegida, auth);
  assert.strictEqual(fetchCalls[0].url, protegida, 'o token não pode ser acrescentado à URL da foto');
  assert.deepStrictEqual(fetchCalls[0].options.headers, auth());
  assert.strictEqual(carregada.url, 'blob:cadastro-1');
  assert.strictEqual(carregada.revogavel, true);
  stores.revogarFotoCarregada(carregada);
  assert.deepStrictEqual(revokedUrls, ['blob:cadastro-1']);

  const quantidadeAntesSemAuth = fetchCalls.length;
  await assert.rejects(
    stores.carregarFotoAutenticada(protegida, () => ({})),
    /Autenticação indisponível/,
  );
  assert.strictEqual(fetchCalls.length, quantidadeAntesSemAuth, 'Cadastro não deve tentar foto protegida sem credencial');

  const quantidadeAntesDataUrl = fetchCalls.length;
  const pendente = await stores.carregarFotoAutenticada('data:image/png;base64,AAAA', auth);
  assert.strictEqual(pendente.url, 'data:image/png;base64,AAAA');
  assert.strictEqual(pendente.revogavel, false);
  assert.strictEqual(fetchCalls.length, quantidadeAntesDataUrl, 'data URL pendente não deve sofrer novo fetch');

  const quantidadeAntesExterna = fetchCalls.length;
  const urlExterna = 'https://imagens.externa.test/foto.png';
  const externa = await stores.carregarFotoAutenticada(urlExterna, auth);
  assert.strictEqual(externa.url, urlExterna);
  assert.strictEqual(externa.revogavel, false);
  assert.strictEqual(fetchCalls.length, quantidadeAntesExterna, 'imagem externa deve manter src direto, sem fetch/CORS e sem token');

  const mediaFetchCalls = [];
  const mediaObjectUrls = [];
  const mediaRevokedUrls = [];
  const mediaListeners = {};
  let resolverPrimeiraCorrida = null;
  class MediaURL extends URL {
    static createObjectURL(blob) {
      const url = `blob:media-${mediaObjectUrls.length + 1}`;
      mediaObjectUrls.push({ url, blob });
      return url;
    }
    static revokeObjectURL(url) { mediaRevokedUrls.push(url); }
  }
  class MutationObserverStub {
    observe() {}
    disconnect() {}
  }
  class IntersectionObserverStub {
    constructor(callback) { this.callback = callback; }
    disconnect() {}
    observe() {}
    unobserve() {}
  }
  const criarElementoFake = () => {
    const attrs = new Map();
    return {
      attrs,
      classList: { add() {}, remove() {} },
      closest() { return null; },
      getAttribute(name) { return attrs.has(name) ? attrs.get(name) : null; },
      matches(selector) { return selector === 'img[data-jk-auth-src]'; },
      removeAttribute(name) { attrs.delete(name); },
      setAttribute(name, value) { attrs.set(name, String(value)); },
    };
  };
  const mediaContext = {
    AbortController,
    console,
    document: {
      documentElement: {},
      querySelectorAll() { return []; },
      readyState: 'complete',
    },
    fetch: (url, options) => {
      mediaFetchCalls.push({ url, options });
      if (String(url).includes('race-one')) {
        return new Promise(resolve => { resolverPrimeiraCorrida = resolve; });
      }
      return Promise.resolve({ ok: true, status: 200, blob: async () => ({ type: 'image/png', size: 5 }) });
    },
    location: { href: 'https://jk.local/tela.html', origin: 'https://jk.local' },
    IntersectionObserver: IntersectionObserverStub,
    MutationObserver: MutationObserverStub,
    obterAuthHeaders: auth,
    URL: MediaURL,
    addEventListener(name, handler) { mediaListeners[name] = handler; },
  };
  mediaContext.window = mediaContext;
  vm.createContext(mediaContext);
  const navigation = read('static/auth/navigation.js');
  const helperInicio = navigation.indexOf('(function instalarMidiaAutenticadaJK');
  assert(helperInicio >= 0, 'helper compartilhado de mídia autenticada ausente');
  vm.runInContext(navigation.slice(helperInicio), mediaContext, { filename: 'static/auth/navigation.js#authenticated-media' });

  const segmentoLojaA = `sid-${'a'.repeat(64)}`;
  const segmentoLojaB = `sid-${'b'.repeat(64)}`;
  const fotoLojaA = mediaContext.JKAuthenticatedMedia.normalizarUrlFotoCadastro(
    `cadastro_fotos/lojas/${segmentoLojaA}/mesmo nome.png`,
  );
  const fotoLojaB = mediaContext.JKAuthenticatedMedia.normalizarUrlFotoCadastro(
    `cadastro_fotos/lojas/${segmentoLojaB}/mesmo nome.png`,
  );
  assert.strictEqual(
    fotoLojaA,
    `/api/cadastro/foto-arquivo/lojas/${segmentoLojaA}/mesmo%20nome.png`,
  );
  assert.strictEqual(
    fotoLojaB,
    `/api/cadastro/foto-arquivo/lojas/${segmentoLojaB}/mesmo%20nome.png`,
  );
  assert.strictEqual(
    mediaContext.JKAuthenticatedMedia.normalizarUrlFotoCadastro(
      `lojas/${segmentoLojaA}/mesmo nome.png`,
    ),
    fotoLojaA,
    'referência scoped sem cadastro_fotos deve manter o caminho completo',
  );
  assert.notStrictEqual(fotoLojaA, fotoLojaB, 'mesmo basename de duas lojas não pode colidir');
  assert.strictEqual(
    mediaContext.JKAuthenticatedMedia.normalizarUrlFotoCadastro('cadastro_fotos/foto antiga.png'),
    '/api/cadastro/foto-arquivo/foto%20antiga.png',
    'foto legada na raiz deve continuar disponível',
  );
  assert.strictEqual(
    mediaContext.JKAuthenticatedMedia.normalizarUrlFotoCadastro(
      `/api/cadastro/foto/client-1/lojas/${segmentoLojaA}/mesmo%20nome.png?store_id=StoreA`,
    ),
    `/api/cadastro/foto/client-1/lojas/${segmentoLojaA}/mesmo%20nome.png?store_id=StoreA`,
    'rota tenant-scoped já autorizada deve ser preservada',
  );
  for (const referenciaExterna of [
    urlExterna,
    'http://cdn.externo.test/mesmo nome.png?versao=1#foto',
    '//cdn.externo.test/mesmo nome.png',
    'ftp://cdn.externo.test/produtos/009.jpg',
    's3://bucket/produtos/009.jpg',
    'gs://bucket/produtos/009.png',
    'blob:https://cdn.externo.test/produtos/009.jpg',
    'custom+photo://catalogo/produtos/009.webp',
    'data:image/jpeg;base64,AA==',
  ]) {
    assert.strictEqual(
      mediaContext.JKAuthenticatedMedia.normalizarUrlFotoCadastro(referenciaExterna),
      referenciaExterna,
      `referência externa deve ser preservada: ${referenciaExterna}`,
    );
  }
  for (const [referenciaLocal, urlEsperada] of [
    ['file:///C:/Fotos/foto local.png', '/api/cadastro/foto-arquivo/foto%20local.png'],
    ['C:\\Fotos\\foto local.png', '/api/cadastro/foto-arquivo/foto%20local.png'],
    ['C:/Fotos/foto local.png', '/api/cadastro/foto-arquivo/foto%20local.png'],
    ['imagens/foto local.png', '/api/cadastro/foto-arquivo/foto%20local.png'],
  ]) {
    assert.strictEqual(
      mediaContext.JKAuthenticatedMedia.normalizarUrlFotoCadastro(referenciaLocal),
      urlEsperada,
      `referência local deve continuar na rota autenticada segura: ${referenciaLocal}`,
    );
  }
  assert.strictEqual(mediaContext.JKAuthenticatedMedia.ehUrlProtegidaCadastro(urlExterna), false);
  for (const caminhoInseguro of [
    'cadastro_fotos/../segredo.png',
    `cadastro_fotos/lojas/${segmentoLojaA}/../segredo.png`,
    'cadastro_fotos/lojas/%2e%2e/segredo.png',
    `cadastro_fotos/lojas/${segmentoLojaA}/%2e%2e`,
    `cadastro_fotos\\lojas\\${segmentoLojaA}\\segredo.png`,
    `cadastro_fotos/lojas/${segmentoLojaA}%2foutra/segredo.png`,
    'https://jk.local/api/cadastro/foto-arquivo/%2e%2e/segredo.png',
    'file:///C:/Fotos/../segredo.png',
    'C:\\Fotos\\..\\segredo.png',
  ]) {
    assert.strictEqual(
      mediaContext.JKAuthenticatedMedia.normalizarUrlFotoCadastro(caminhoInseguro),
      '',
      `traversal deve ser rejeitado: ${caminhoInseguro}`,
    );
  }

  const imagemProtegida = criarElementoFake();
  const fonteUm = '/api/cadastro/foto-arquivo/001.png';
  await mediaContext.JKAuthenticatedMedia.hidratarImagem(imagemProtegida, fonteUm);
  assert.strictEqual(mediaFetchCalls[0].url, fonteUm);
  assert.deepStrictEqual(mediaFetchCalls[0].options.headers, auth());
  assert.strictEqual(imagemProtegida.getAttribute('src'), 'blob:media-1');

  const fonteDois = '/api/cadastro/foto/client-1/002.png';
  await mediaContext.JKAuthenticatedMedia.hidratarImagem(imagemProtegida, fonteDois);
  assert.strictEqual(mediaFetchCalls[1].url, fonteDois, 'troca de fonte deve iniciar nova busca autenticada');
  assert(mediaRevokedUrls.includes('blob:media-1'), 'rerender/troca deve revogar o object URL anterior');
  assert.strictEqual(imagemProtegida.getAttribute('src'), 'blob:media-2');

  imagemProtegida.setAttribute('data-jk-auth-src', '/api/cadastro/foto-arquivo/offscreen-three.png');
  mediaContext.JKAuthenticatedMedia.hidratar(imagemProtegida);
  assert(mediaRevokedUrls.includes('blob:media-2'), 'troca offscreen deve revogar imediatamente o Blob anterior');
  assert.strictEqual(imagemProtegida.getAttribute('src'), null, 'troca offscreen não deve manter a foto anterior visível');
  assert.strictEqual(mediaFetchCalls.length, 2, 'imagem fora da viewport deve aguardar o observador antes do fetch');

  const imagemExterna = criarElementoFake();
  await mediaContext.JKAuthenticatedMedia.hidratarImagem(imagemExterna, urlExterna);
  assert.strictEqual(imagemExterna.getAttribute('src'), urlExterna);
  assert.strictEqual(mediaFetchCalls.length, 2, 'helper comum não deve fazer fetch cross-origin');

  const imagemSemAuth = criarElementoFake();
  mediaContext.obterAuthHeaders = () => ({});
  const resultadoSemAuth = await mediaContext.JKAuthenticatedMedia.hidratarImagem(
    imagemSemAuth,
    '/api/cadastro/foto-arquivo/sem-auth.png',
  );
  assert.strictEqual(resultadoSemAuth, '', 'helper deve falhar fechado sem cabeçalho de autenticação');
  assert.strictEqual(mediaFetchCalls.length, 2, 'helper não deve fazer fallback anônimo');
  mediaContext.obterAuthHeaders = auth;

  const imagemCorrida = criarElementoFake();
  const primeiraCorrida = mediaContext.JKAuthenticatedMedia.hidratarImagem(
    imagemCorrida,
    '/api/cadastro/foto-arquivo/race-one.png',
  );
  const segundaCorrida = mediaContext.JKAuthenticatedMedia.hidratarImagem(
    imagemCorrida,
    '/api/cadastro/foto-arquivo/race-two.png',
  );
  await segundaCorrida;
  resolverPrimeiraCorrida({ ok: true, status: 200, blob: async () => ({ type: 'image/png', size: 7 }) });
  await primeiraCorrida;
  assert.strictEqual(imagemCorrida.getAttribute('src'), 'blob:media-3', 'resposta obsoleta não pode substituir a foto atual');
  mediaListeners.beforeunload();
  assert(mediaRevokedUrls.includes('blob:media-2'), 'beforeunload deve revogar URLs ainda ativas');
  assert(mediaRevokedUrls.includes('blob:media-3'), 'beforeunload deve revogar URL criada após corrida');

  vm.runInContext(read('static/cadastro/00-runtime.js'), context, { filename: 'static/cadastro/00-runtime.js' });
  vm.runInContext(read('static/cadastro/01-core.js'), context, { filename: 'static/cadastro/01-core.js' });
  const markup = context.JKCadastro.core.renderConteudoCelula('foto', {
    foto: 'cadastro_fotos/lojas/store-1/foto.png',
    store_id: 'store-1',
  });
  assert.match(markup, /data-foto-url="\/api\/cadastro\/foto\/client-1\/lojas\/store-1\/foto\.png"/);
  assert.doesNotMatch(markup, /<img\b|Authorization|[?&](?:access_)?token=/i, 'HTML inicial não deve expor rota protegida em img nem token');

  const produtos = read('static/cadastro/main/01-produtos.js');
  const editor = read('static/cadastro/editar-item.js');
  const inclusao = read('static/cadastro/incluir-item.js');
  const mercadoLivreForm = read('static/cadastro/form/01-mercado-livre.js');
  assert.match(produtos, /carregarFotoAutenticada\(url, authHeaders\)/);
  assert.match(produtos, /createElement\('img'\)[\s\S]*image\.src = foto\.url/);
  assert.match(produtos, /beforeunload[\s\S]*revogarFotosTabela/);
  assert.match(produtos, /revogarFotosTabela\(\);[\s\S]*elements\.tBody\.innerHTML = ''/, 'rerender deve revogar URLs anteriores antes de substituir a tabela');
  assert.match(editor, /carregarFotoAutenticada\(url, authHeaders\)/);
  assert.match(editor, /beforeunload[\s\S]*revogarPreviewFotoAtual/);
  assert.match(editor, /payload && payload\.produto[\s\S]*produtoAtualizado\.foto/);
  assert.match(inclusao, /carregarFotoAutenticada\(url, authHeaders\)/);
  assert.match(inclusao, /beforeunload[\s\S]*revogarPreviewFotoAtual/);
  assert.match(inclusao, /requestSeq !== fotoPreviewSeq[\s\S]*revogarFotoCarregada\(foto\)/);
  assert.match(editor, /if \(state\.fotoDataUrl\)[\s\S]*renderizarFotoPendente\(elements\.fotoPreview, state\.fotoDataUrl\)/, 'prévia pendente deve continuar usando a data URL local');
  assert.match(mercadoLivreForm, /image\.src = dataUrl;[\s\S]*container\.replaceChildren\(image, note\)/, 'prévia pendente deve usar nós DOM, sem interpolação HTML');
  assert.doesNotMatch(`${formCore}\n${produtos}\n${editor}\n${inclusao}\n${mercadoLivreForm}`, /[?&](?:access_)?token=/i);

  console.log('cadastro authenticated photo frontend: OK');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
