const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function parseVendas(value) {
  if (value === null || value === undefined) return null;
  const texto = String(value).trim();
  if (!texto) return null;
  const match = texto.match(/([0-9][0-9\.,]*)\s*(k|mil)?\b/i);
  if (!match) return null;
  let numeroTexto = String(match[1]).replace(/\s+/g, '');
  if (numeroTexto.includes('.') && numeroTexto.includes(',')) {
    numeroTexto = numeroTexto.lastIndexOf('.') > numeroTexto.lastIndexOf(',')
      ? numeroTexto.replace(/,/g, '')
      : numeroTexto.replace(/\./g, '').replace(',', '.');
  } else if (numeroTexto.includes(',')) {
    numeroTexto = /^\d{1,3}(?:,\d{3})+$/.test(numeroTexto)
      ? numeroTexto.replace(/,/g, '')
      : numeroTexto.replace(',', '.');
  } else if (numeroTexto.includes('.')) {
    numeroTexto = /^\d{1,3}(?:\.\d{3})+$/.test(numeroTexto)
      ? numeroTexto.replace(/\./g, '')
      : numeroTexto;
  }
  const numero = Number(numeroTexto);
  if (!Number.isFinite(numero)) return null;
  const sufixo = String(match[2] || '').toLowerCase();
  const total = (sufixo === 'k' || sufixo === 'mil') ? numero * 1000 : numero;
  return Number.isFinite(total) ? Math.round(total) : null;
}

function normalizarTextoMl(value) {
  return String(value || '')
    .replace(/\u002F/g, '/')
    .replace(/\\\//g, '/')
    .replace(/\n/g, ' ')
    .replace(/\t/g, ' ')
    .replace(/\r/g, ' ')
    .replace(/&quot;/g, '"')
    .replace(/\\"/g, '"')
    .trim();
}

function normalizarNomeVendedor(value) {
  return String(value || '')
    .replace(/^(vendido\s+por|loja\s+oficial|oficial\s+loja)\s*/i, '')
    .replace(/&quot;|\\"/g, '"')
    .replace(/\s+/g, ' ')
    .trim();
}

function vendedorValido(valor) {
  const texto = normalizarNomeVendedor(valor);
  if (!texto) return false;
  if (texto.length < 2 || texto.length > 120) return false;
  if (!/[A-Za-z0-9]/.test(texto)) return false;
  if (/^\d+$/.test(texto)) return false;
  const textoBusca = normalizarNomeVendedor(texto).toLowerCase().replace(/[^a-z0-9]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (!textoBusca || textoBusca.length < 2) return false;
  if (/(^|\b)(anuncio criado|an ncio criado|criado em|catalogo criado|cat logo criado|vendas produto|total vendas|quantidade vendas)(\b|$)/i.test(textoBusca)) {
    return false;
  }
  if (/^(vendido|vendedor|anuncio|anunci[oô]o|produto|frete|envio|loja|oferta|ofertas|desconto|comprar|comprando|login|entrar|cadastro|email|senha|contato|perfil|busca|filtro|categoria|condi[cç][aã]o|aviso|informa[cç][aã]o|cria[cç][aã]o|valor|pre[cç]o)$/i.test(textoBusca)) {
    return false;
  }
  return true;
}

function escolherNomeVendedor(candidatos) {
  const itens = Array.isArray(candidatos) ? candidatos : [];
  const opcoes = [];
  for (let i = 0; i < itens.length; i += 1) {
    const item = itens[i];
    const valor = typeof item === 'string' ? item : item && item.valor;
    const prioridade = Number(item && item.prioridade) || 0;
    const nome = normalizarNomeVendedor(valor);
    if (!nome || !vendedorValido(nome)) continue;
    opcoes.push({
      nome,
      prioridade,
      score: nome.length + (/\s/.test(nome) ? 6 : 0),
      ordem: i
    });
  }
  if (!opcoes.length) return '';
  opcoes.sort((a, b) => b.prioridade - a.prioridade || b.score - a.score || a.ordem - b.ordem);
  return opcoes[0].nome;
}

function fonteVendasAvantPro(fonte) {
  const valor = String(fonte || '').trim().toLowerCase();
  return valor === 'avantpro_anuncio' || valor === 'avantpro_dom' || valor === 'avantpro_fast_dom';
}

function deveAtualizarVendas(atual, fonteAtual, novo, fonteNova) {
  const atualNumero = parseVendas(atual);
  const novoNumero = parseVendas(novo);
  if (novoNumero === null) return false;
  if (!fonteVendasAvantPro(fonteNova)) return false;
  if (atualNumero === null) return true;
  const pesos = {
    '': 0,
    api: 2,
    api_search: 2,
    browser_item: 3,
    pagina_produto: 4,
    avantpro_card: 0,
    avantpro_produto: 0,
    avantpro_fast_dom: 8,
    avantpro_dom: 7,
    avantpro_anuncio: 8
  };
  const pesoAtual = pesos[String(fonteAtual || '').trim().toLowerCase()] || 0;
  const pesoNovo = pesos[String(fonteNova || '').trim().toLowerCase()] || 0;
  return pesoNovo > pesoAtual || (pesoNovo === pesoAtual && atualNumero !== novoNumero);
}

function scoreNomeVendedor(valor) {
  if (!vendedorValido(valor)) return -1;
  const normalizado = normalizarNomeVendedor(valor).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  return normalizado.length + (/\s/.test(normalizado) ? 6 : 0);
}

function deveAtualizarVendedor(atual, fonteAtual, novo, fonteNova) {
  if (!vendedorValido(novo)) return false;
  if (!vendedorValido(atual)) return true;
  const chaveAtual = normalizarNomeVendedor(atual).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const chaveNova = normalizarNomeVendedor(novo).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  if (chaveAtual === chaveNova) return false;
  const pesos = {
    '': 0,
    avantpro_card: 1,
    api_search: 2,
    pagina_produto_fonte: 3,
    pagina_produto: 5,
    avantpro_dom: 5,
    avantpro_vendedor: 5,
    avantpro_fast_dom: 5,
    api: 6,
    api_item: 6,
    api_item_redirect: 6,
    mercado_livre_api: 8,
    mercado_livre_dom: 8,
    mercado_livre_dom_contexto_avant: 8,
    mercado_livre_dom_card_clicado: 8,
    browser_item: 7
  };
  const pesoAtual = pesos[String(fonteAtual || '').trim().toLowerCase()] || 0;
  const pesoNovo = pesos[String(fonteNova || '').trim().toLowerCase()] || 0;
  if (pesoNovo > pesoAtual) return true;
  if (pesoNovo < pesoAtual) return false;
  return scoreNomeVendedor(novo) > scoreNomeVendedor(atual);
}

function nomeVendedorDaResposta(item) {
  const seller = item && typeof item.seller === 'object' ? item.seller : {};
  const officialStore = item && typeof item.official_store === 'object' ? item.official_store : {};
  const candidatos = [
    seller.nickname,
    item && item.seller_name,
    item && item.official_store_name,
    seller.name,
    officialStore.nickname,
    officialStore.name
  ];
  for (const candidato of candidatos) {
    const normalizado = normalizarTextoMl(candidato);
    if (normalizado) return normalizado;
  }
  return '';
}

function nomeUsuarioFinal(usuario) {
  const candidatos = [
    usuario && usuario.nickname,
    usuario && usuario.official_store_name,
    usuario && usuario.official_store && usuario.official_store.name
  ];
  return candidatos.find(Boolean) || '';
}

function montarInfoItem(item, user) {
  const sellerId = item && (item.seller_id || item.seller && item.seller.id || item.official_store && item.official_store.seller_id);
  let vendedor = nomeVendedorDaResposta(item);
  if (sellerId && user) {
    const vendedorUsuario = nomeUsuarioFinal(user);
    if (normalizarTextoMl(vendedorUsuario)) {
      vendedor = normalizarTextoMl(vendedorUsuario);
    }
  }
  return {
    id: item.id,
    titulo: item.title || '',
    data_criacao: item.date_created || item.start_time || '',
    vendedor,
    seller_id: sellerId || null,
    vendas: parseVendas(item.sold_quantity ?? item.sold ?? item.soldQuantity)
  };
}

function mesclarAnunciosAvant(destino, origem) {
  const normalizarNumeroVendas = (valor) => {
    const numero = Number(valor);
    return Number.isFinite(numero) ? numero : null;
  };
  const normalizarVendedor = (item, atual) => {
    const candidato = escolherNomeVendedor([
      { valor: item && item.vendedor, prioridade: 90 },
      { valor: atual && atual.vendedor, prioridade: 60 }
    ]);
    if (candidato) return candidato;
    const atualNormalizado = normalizarNomeVendedor(atual && atual.vendedor || '');
    return vendedorValido(atualNormalizado) ? atualNormalizado : '';
  };
  const chave = (item) => {
    const id = item && (item.id || item.url);
    return id ? `id:${String(id).toUpperCase()}` : '';
  };
  const mapa = new Map();
  (destino || []).forEach(item => {
    const key = chave(item);
    if (key) mapa.set(key, item);
  });
  (origem || []).forEach(item => {
    const key = chave(item);
    if (!key) return;
    const atual = mapa.get(key);
    if (!atual) {
      mapa.set(key, { ...item });
      return;
    }
    mapa.set(key, {
      ...atual,
      ...item,
      vendedor: normalizarVendedor(item, atual),
      data_criacao: item.data_criacao || atual.data_criacao || '',
      vendas: normalizarNumeroVendas(item.vendas) ?? normalizarNumeroVendas(atual.vendas)
    });
  });
  return Array.from(mapa.values());
}

function vendasAvantAnuncioDoTexto(text) {
  const base = String(text || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase();
  const patterns = [
    /vendas\s+do\s+(?:anuncio|item)(?:\s+ganhador)?\s*(?:[:\-]|=)?\s*(?:\+\s*)?([\d\.,]+)\s*(mil|k)?/i,
    /vendas\s+deste\s+anuncio\s*(?:[:\-]|=)?\s*(?:\+\s*)?([\d\.,]+)\s*(mil|k)?/i,
    /vendas\s+do\s+vendedor\s+neste\s+anuncio\s*(?:[:\-]|=)?\s*(?:\+\s*)?([\d\.,]+)\s*(mil|k)?/i
  ];
  for (const pattern of patterns) {
    const match = base.match(pattern);
    if (match && match[1]) return parseVendas(`${match[1]}${match[2] ? ` ${match[2]}` : ''}`);
  }
  return null;
}

function chavesAnuncioFavoritos(anuncio) {
  const chaves = new Set();
  const itemId = (valor) => {
    const texto = String(valor || '');
    const match = texto.match(/\b(MLB-?\d{6,})\b/i);
    return match ? match[1].replace('-', '').toUpperCase() : '';
  };
  const id = String((anuncio && anuncio.id) || itemId(anuncio && anuncio.url) || '').trim().toUpperCase();
  const url = String((anuncio && anuncio.url) || '').split('#')[0].trim().toLowerCase();
  if (id) {
    chaves.add(`id:${id}`);
    chaves.add(`chave:${id}`);
  }
  if (url) {
    chaves.add(`url:${url}`);
    if (!id) chaves.add(`chave:${url}`);
  }
  return Array.from(chaves);
}

function encontrarAnuncioEnriquecido(anuncios, info) {
  const mapa = new Map();
  anuncios.forEach((item) => chavesAnuncioFavoritos(item).forEach((chave) => mapa.set(chave, item)));
  return chavesAnuncioFavoritos(info).map((chave) => mapa.get(chave)).find(Boolean) || null;
}

function compilarScriptsInline(filePath) {
  const html = fs.readFileSync(filePath, 'utf8');
  const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gim)]
    .map((match) => match[1] || '')
    .map((script) => script.trim())
    .filter((script) => script.length > 0);

  scripts.forEach((script, index) => {
    assert.doesNotThrow(() => {
      const wrapper = new Function(script);
      assert.equal(typeof wrapper, 'function');
    }, new RegExp(`Falha ao compilar bloco ${index + 1} de ${path.basename(filePath)}`));
  });
}

function normalizarTextoBuscaAvant(value) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase();
}

function contarRotulosAvantNoTexto(value) {
  const busca = normalizarTextoBuscaAvant(value);
  const padroes = [
    /vendas?\s+do\s+produto/,
    /vendas?\s+estimad/,
    /ritmo\s+atual/,
    /visitas\s+do\s+anuncio/,
    /participacao\b/,
    /\bmarca\b/,
    /faturamento\s+do\s+produto/,
    /nome\s+do\s+vendedor/,
    /localizacao\s+do\s+vendedor/,
    /\bmarca\b/,
    /participacao\b/,
    /visitas\s+do\s+anuncio/,
    /comissao\b/,
    /reputacao\s+do\s+vendedor/,
    /anuncio\s+(?:ganhador\s+)?criado\s+em/
  ];
  return padroes.reduce((total, regex) => total + (regex.test(busca) ? 1 : 0), 0);
}

function textoPareceAvantLogado(value) {
  const busca = normalizarTextoBuscaAvant(value);
  const labels = contarRotulosAvantNoTexto(busca);
  return /informacoes?\s+avant(?:\s*pro|pro)?/.test(busca) && labels > 0;
}

function textoPareceLoginAvant(value) {
  const busca = normalizarTextoBuscaAvant(value);
  return /avant\s*pro|avantpro|avantprocloud/.test(busca)
    && /vincule\s+o\s+avantpro|vincular\s+agora|vincular\s+conta|comece\s+a\s+usar|entre\s+na\s+sua\s+conta|liberar\s+os\s+recursos|iniciar\s+sessao|insira\s+suas\s+credenciais|seu\s+e-?mail|seu\s+email|chame\s+o\s+suporte|nao\s+possui\s+uma\s+conta|crie\s+uma\s+aqui|dica\s+avantpro|tutoriais/.test(busca);
}

function extrairFuncaoDeclarada(source, nome) {
  const inicio = source.indexOf(`function ${nome}(`);
  assert.ok(inicio >= 0, `${nome} deve existir no ml-browser.js`);
  const abertura = source.indexOf('{', inicio);
  assert.ok(abertura > inicio, `${nome} deve ter corpo`);
  let profundidade = 0;
  let emString = '';
  let escape = false;
  for (let i = abertura; i < source.length; i += 1) {
    const ch = source[i];
    if (escape) {
      escape = false;
      continue;
    }
    if (emString) {
      if (ch === '\\') {
        escape = true;
      } else if (ch === emString) {
        emString = '';
      }
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') {
      emString = ch;
      continue;
    }
    if (ch === '{') profundidade += 1;
    if (ch === '}') {
      profundidade -= 1;
      if (profundidade === 0) return source.slice(inicio, i + 1);
    }
  }
  throw new Error(`Nao foi possivel extrair ${nome}`);
}

function criarNoTeste(texto, rect, attrs = {}) {
  return {
    innerText: texto || '',
    textContent: texto || '',
    value: attrs.value || '',
    id: attrs.id || '',
    className: attrs.class || '',
    parentElement: null,
    shadowRoot: null,
    getAttribute(name) {
      if (name === 'class') return this.className;
      if (name === 'id') return this.id;
      return attrs[name] || '';
    },
    getBoundingClientRect() {
      return {
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
        right: rect.left + rect.width,
        bottom: rect.top + rect.height
      };
    },
    closest(selector) {
      if (/ui-search|poly-card|result|data-testid|card/.test(String(selector || ''))) return null;
      return this.parentElement || null;
    },
    querySelectorAll() {
      return [];
    },
    getRootNode() {
      return { host: null };
    }
  };
}

function executarLocalizadorFerramentasAvant(nodes, bodyText) {
  const mlBrowserPath = path.join(process.cwd(), 'static', 'favoritos', 'ml-browser.js');
  const source = fs.readFileSync(mlBrowserPath, 'utf8');
  const funcSource = extrairFuncaoDeclarada(source, 'montarScriptLocalizarFerramentasAvantPro');
  const montarScript = vm.runInNewContext(`(${funcSource})`, {});
  const script = montarScript();
  const body = criarNoTeste(bodyText || '', { left: 0, top: 0, width: 1328, height: 676 }, { class: 'body' });
  const allNodes = nodes.map((node) => {
    if (!node.parentElement) node.parentElement = body;
    return node;
  });
  const sandbox = {
    window: {
      innerWidth: 1328,
      innerHeight: 676,
      getComputedStyle: () => ({ display: 'block', visibility: 'visible', opacity: '1' })
    },
    document: {
      body,
      documentElement: { clientWidth: 1328, clientHeight: 676 },
      querySelectorAll: () => allNodes
    },
    location: { href: 'https://www.mercadolivre.com.br/' },
    String,
    Array,
    Number,
    RegExp,
    Math
  };
  return vm.runInNewContext(script, sandbox);
}

function executarLocalizadorBolinhaAvant(nodes, bodyText) {
  const mlBrowserPath = path.join(process.cwd(), 'static', 'favoritos', 'ml-browser.js');
  const source = fs.readFileSync(mlBrowserPath, 'utf8');
  const funcSource = extrairFuncaoDeclarada(source, 'montarScriptLocalizarBolinhaAvantPro');
  const montarScript = vm.runInNewContext(`(${funcSource})`, {});
  const script = montarScript();
  const body = criarNoTeste(bodyText || '', { left: 0, top: 0, width: 1328, height: 676 }, { class: 'body' });
  const allNodes = nodes.map((node) => {
    if (!node.parentElement) node.parentElement = body;
    return node;
  });
  const sandbox = {
    window: {
      innerWidth: 1328,
      innerHeight: 676,
      getComputedStyle: () => ({ display: 'block', visibility: 'visible', opacity: '1', position: 'fixed' })
    },
    document: {
      body,
      documentElement: { clientWidth: 1328, clientHeight: 676 },
      querySelectorAll: () => allNodes
    },
    location: { href: 'https://www.mercadolivre.com.br/' },
    String,
    Array,
    Number,
    RegExp,
    Math
  };
  return vm.runInNewContext(script, sandbox);
}

function validarLocalizadorBolinhaAvantPro() {
  const bolinhaReal = executarLocalizadorBolinhaAvant([
    criarNoTeste('', { left: 1248, top: 585, width: 64, height: 64 }, { class: 'avantpro-speed-dial-fab' })
  ], 'Assine ja Ferramentas Suporte AvantPro');
  assert.equal(bolinhaReal.success, true, 'localizador deve encontrar a bolinha real do AvantPro');
  assert.equal(bolinhaReal.source, 'bolinha_avant_dom');

  const cardMercadoLivre = executarLocalizadorBolinhaAvant([
    criarNoTeste('', { left: 930, top: 432, width: 52, height: 52 }, { class: 'dynamic-access-card-item__image' })
  ], 'Assine ja Ferramentas Suporte AvantPro');
  assert.equal(cardMercadoLivre.success, true, 'localizador deve usar fallback quando so ha card do ML e texto do Avant');
  assert.equal(cardMercadoLivre.source, 'bolinha_avant_estimado');
  assert.notEqual(Math.round(cardMercadoLivre.x), 956, 'localizador nao deve clicar no card dynamic-access do Mercado Livre');
}

function validarLocalizadorFerramentasAvantPro() {
  const resultado = executarLocalizadorFerramentasAvant([
    criarNoTeste('Assine ja', { left: 1168, top: 418, width: 132, height: 46 }, { class: 'avantpro-speed-dial-action' }),
    criarNoTeste('Ferramentas', { left: 1168, top: 480, width: 132, height: 46 }, { class: 'avantpro-speed-dial-action' }),
    criarNoTeste('Suporte', { left: 1168, top: 542, width: 132, height: 46 }, { class: 'avantpro-speed-dial-action' })
  ], 'Assine ja Ferramentas Suporte Avant Pro');
  assert.equal(resultado.success, true, 'localizador deve encontrar o botao Ferramentas visivel');
  assert.equal(resultado.source, 'ferramentas_dom');
  assert.match(resultado.label, /Ferramentas/i);
  assert.equal(Math.round(resultado.x), 1234);
  assert.equal(Math.round(resultado.y), 503);

  const estimado = executarLocalizadorFerramentasAvant([
    criarNoTeste('', { left: 1248, top: 585, width: 64, height: 64 }, { class: 'avantpro-speed-dial-fab' })
  ], 'Assine ja Ferramentas Suporte AvantPro');
  assert.equal(estimado.success, false, 'localizador nao deve usar fallback quando Ferramentas aparece apenas como texto solto');

  const botaoGrande = criarNoTeste('Ferramentas', { left: 1140, top: 480, width: 132, height: 46 }, { class: 'avantpro-speed-dial-item' });
  const rotuloPequeno = criarNoTeste('Ferramentas', { left: 1226, top: 501, width: 30, height: 5 }, { class: 'avantpro-speed-dial-item-label' });
  rotuloPequeno.parentElement = botaoGrande;
  const alvoGrande = executarLocalizadorFerramentasAvant([rotuloPequeno, botaoGrande], 'Assine ja Ferramentas Suporte AvantPro');
  assert.equal(alvoGrande.success, true, 'localizador deve encontrar Ferramentas mesmo quando o texto esta em um rotulo pequeno');
  assert.equal(alvoGrande.source, 'ferramentas_dom');
  assert.ok(alvoGrande.width >= 100, 'localizador deve clicar no botao grande de Ferramentas, nao no rotulo pequeno');
  assert.ok(alvoGrande.height >= 30, 'localizador deve clicar numa area alta o suficiente para receber o clique');

  const apenasRotulo = executarLocalizadorFerramentasAvant([
    criarNoTeste('Ferramentas', { left: 1255, top: 599, width: 30, height: 5 }, { class: 'avantpro-speed-dial-item-label' })
  ], 'Assine ja Ferramentas Suporte AvantPro');
  assert.equal(apenasRotulo.success, true, 'localizador deve usar o menu lateral estimado quando so ha rotulo pequeno no DOM');
  assert.equal(apenasRotulo.source, 'ferramentas_menu_lateral_estimado');
  assert.equal(Math.round(apenasRotulo.x), 1218);
  assert.equal(Math.round(apenasRotulo.y), 496);
}

function validarFluxoFavoritosAvantProSemReload() {
  const repoRoot = process.cwd();
  const mlBrowserCore = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'ml-browser.js'), 'utf8');
  const urlUtils = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'v2', 'browser', 'url-utils.js'), 'utf8');
  const shellBridgeV2 = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'v2', 'browser', 'shell-bridge.js'), 'utf8');
  const avantCacheV2 = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'v2', 'browser', 'avant-cache.js'), 'utf8');
  const mlBrowser = [urlUtils, shellBridgeV2, avantCacheV2, mlBrowserCore].join('\n');
  const statusModal = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'v2', 'ui', 'status-modal.js'), 'utf8');
  const elapsedTimer = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'v2', 'ui', 'elapsed-timer.js'), 'utf8');
  const buscaRanking = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'tabelas-layout', '04-promocoes-busca-ranking.js'), 'utf8');
  const skuSidebar = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'tabelas-layout', '03-sku-sidebar-modal.js'), 'utf8');
  const promocoesEfetivacao = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'promocoes-efetivacao.js'), 'utf8');
  const execucao = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'tabelas-layout', '07-execucao-render-layout.js'), 'utf8');
  const historico = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'tabelas-layout', '05-resultados-historico.js'), 'utf8');
  const historicoUi = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'tabelas-layout', '06-ranking-manual-historico-ui.js'), 'utf8');
  const ranking = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'ranking.js'), 'utf8');
  const init = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'init.js'), 'utf8');
  const styles = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'styles.css'), 'utf8');
  const runtime = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'runtime.js'), 'utf8');
  const favoritosHtml = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos.html'), 'utf8');
  const mlBaseBusca = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'tabelas-layout', '01-ml-base-busca.js'), 'utf8');
  const renderAvantMercadoLivre = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'tabelas-layout', '08-render-avant-mercadolivre.js'), 'utf8');
  const jobsBackend = fs.readFileSync(path.join(repoRoot, 'backend', 'services', 'favoritos_jobs.py'), 'utf8');
  const favoritosStorageBackend = fs.readFileSync(path.join(repoRoot, 'backend', 'services', 'favoritos_storage.py'), 'utf8');
  const schemasFavoritos = fs.readFileSync(path.join(repoRoot, 'backend', 'schemas', 'favoritos.py'), 'utf8');
  const routerFavoritos = fs.readFileSync(path.join(repoRoot, 'backend', 'routers', 'favoritos.py'), 'utf8');
  const localAppPaths = fs.readFileSync(path.join(repoRoot, 'electron_app', 'main', 'modules', 'local-app-paths.js'), 'utf8');
  const backendModule = fs.readFileSync(path.join(repoRoot, 'electron_app', 'main', 'modules', 'backend.js'), 'utf8');
  const ipc = fs.readFileSync(path.join(repoRoot, 'electron_app', 'main', 'modules', 'ipc.js'), 'utf8');
  const windowModule = fs.readFileSync(path.join(repoRoot, 'electron_app', 'main', 'modules', 'window.js'), 'utf8');
  const syncFavoritosRuntime = fs.readFileSync(path.join(repoRoot, 'scripts', 'sync-favoritos-runtime.js'), 'utf8');
  const shell = fs.readFileSync(path.join(repoRoot, 'electron_shell.html'), 'utf8');
  const preloadRoot = fs.readFileSync(path.join(repoRoot, 'preload.js'), 'utf8');
  const preloadApp = fs.readFileSync(path.join(repoRoot, 'electron_app', 'preload.js'), 'utf8');
  const preloadTab = fs.readFileSync(path.join(repoRoot, 'electron_tab_preload.js'), 'utf8');
  const inicioColetaNova = execucao.indexOf('async function coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo');
  const fimColetaNova = execucao.indexOf('async function abrirPrimeiraPesquisaFavoritosAposConfirmacaoAvantPro', inicioColetaNova);
  assert.ok(inicioColetaNova >= 0 && fimColetaNova > inicioColetaNova, 'coleta nova do Favoritos deve existir antes do fluxo pos-login');
  const coletaNovaFavoritos = execucao.slice(inicioColetaNova, fimColetaNova);

  assert.match(coletaNovaFavoritos, /abrirMercadoLivreNoPrograma\(\{[\s\S]*termoPesquisa:\s*termo/, 'coleta nova deve abrir a pesquisa uma vez no navegador interno');
  assert.match(coletaNovaFavoritos, /abrirMercadoLivreNoPrograma\(\{[\s\S]*apenasAbrirUrl:\s*true[\s\S]*confirmarPesquisa:\s*false[\s\S]*agendarPosicaoAntes:\s*false[\s\S]*reposicionarDepois:\s*false/, 'coleta nova deve abrir a URL uma unica vez, sem confirmar/reposicionar a pesquisa antiga');
  assert.match(coletaNovaFavoritos, /coletarPrimeiraPaginaFavoritosControlada\(\{[\s\S]*loteCliques:\s*6[\s\S]*onProgress/, 'coleta nova deve usar fila controlada para acionar os cards Avant da primeira pagina');
  assert.doesNotMatch(coletaNovaFavoritos, /extrairAnunciosWebviewVisivel\(|coletarDadosAvantComRolagem\(/, 'coleta nova pos-login nao deve chamar leitores legados no caminho ativo');
  assert.doesNotMatch(coletaNovaFavoritos, /buscarAnunciosFavoritosPorTermo|aguardarPrimeirosDadosAvantOuCardsWebview|aguardarPesquisaMercadoLivreAtual|garantirPesquisaMercadoLivreSubmetida|validarAnunciosFavoritosPertencemAoTermo|forcarNavegadorMlShellVisivel|location\.reload|recarregarNavegador/i, 'coleta nova pos-login nao pode chamar motor antigo, monitoramento, validacao de termo, forcar BrowserView nem recarregar a pesquisa');
  assert.doesNotMatch(coletaNovaFavoritos, /O Avant Pro pediu login ou vinculacao|loginAvantProNecessario:\s*true|needsAvantLogin|needsAccountLink|accountActionRequired|Continuando coleta|Coleta passiva/, 'coleta nova pos-login nao pode monitorar nem reagir a estado do AvantPro');
  assert.match(renderAvantMercadoLivre, /const apenasAbrirUrl = opcoes\.apenasAbrirUrl === true \|\| opcoes\.confirmarPesquisa === false/, 'abertura do Mercado Livre deve ter modo apenas abrir URL');
  assert.match(renderAvantMercadoLivre, /usarNavegadorMlNoShellElectron\(\) && opcoes\.agendarPosicaoAntes !== false/, 'modo de abertura deve permitir pular reposicionamento antes da navegacao');
  assert.match(renderAvantMercadoLivre, /if \(!apenasAbrirUrl && termoPesquisa && typeof garantirPesquisaMercadoLivreSubmetida === 'function'\)/, 'modo apenas abrir URL deve pular confirmacao/submissao antiga da pesquisa');
  assert.match(renderAvantMercadoLivre, /if \(opcoes\.reposicionarDepois !== false\) \{[\s\S]*agendarAtualizacaoPosicaoNavegadorMlShell\(\)/, 'modo de abertura deve permitir pular reposicionamento depois da navegacao');
  assert.match(mlBrowser, /async function coletarDadosAvantComRolagem\(opcoes = \{\}\)[\s\S]*coletarPrimeiraPaginaFavoritosControlada\(\{[\s\S]*maxAnuncios:[\s\S]*tempoLimiteMs:[\s\S]*maxPassadas:[\s\S]*loteCliques:[\s\S]*onProgress:\s*opcoes\.onProgress[\s\S]*resultadoNovo\.anuncios/, 'fachada legada da rolagem deve delegar ao coletor controlado e preservar limites, cliques e progresso');
  assert.doesNotMatch(mlBrowser.match(/async function coletarDadosAvantComRolagem\(opcoes = \{\}\)[\s\S]*?\n\s*\}/)?.[0] || '', /diagnosticarAvantProNoWebview|aguardarPrimeirosDadosAvantOuCardsWebview|location\.reload/, 'fachada legada da rolagem nao deve reintroduzir monitoramento ou reload');
  assert.match(execucao, /await abrirPrimeiraPesquisaFavoritosAposConfirmacaoAvantPro\(selecionados,\s*quantidade,[\s\S]*return;/, 'fluxo principal deve parar na coleta nova pos-login antes do job/renderer antigo');
  assert.doesNotMatch(execucao, /forcarNavegadorMlShellVisivel/, 'execucao do Favoritos nao deve forcar reexibicao do BrowserView durante a pesquisa');
  assert.match(mlBrowser, /function garantirAvantProProntoParaFavoritos[\s\S]*recarregarSeAusente:\s*false/, 'preparacao do AvantPro para favoritos nao pode pedir reload');
  assert.match(mlBrowser, /function recarregarNavegadorMlParaAvantPro[\s\S]*if \(mlFavoritosEmExecucao\) return false/, 'reload precisa ser bloqueado durante favoritos');
  assert.match(mlBrowser, /if \(opcoes\.recarregarSeAusente && mlFavoritosEmExecucao\)/, 'aguardar AvantPro precisa ignorar reload durante favoritos');
  assert.match(mlBrowser, /function recarregarNavegadorMlAposLoginAvantProFavoritos[\s\S]*login_ou_vinculo_ainda_pendente[\s\S]*ML_FAVORITOS_AVANT_RELOAD_APOS_LOGIN_KEYS[\s\S]*retomada_pos_login_sem_reload/, 'pos-login AvantPro em favoritos deve retomar sem atualizar a pagina');
  assert.doesNotMatch(mlBrowser, /function recarregarNavegadorMlAposLoginAvantProFavoritos[\s\S]*location\.reload\(\)/, 'pos-login AvantPro em favoritos nao deve executar location.reload');
  assert.match(mlBrowser, /reloadAposLoginAvantRecomendado[\s\S]*loginAvantClicadoRecentemente[\s\S]*needsAccountLink:\s*!reloadAposLoginAvantRecomendado/, 'diagnostico deve tratar login AvantPro recente como reload pos-login, nao como login pendente infinito');
  assert.match(mlBrowser, /accountLinkButtonsCards[\s\S]*accountLinkButtonsGlobais[\s\S]*loginGlobalPendente[\s\S]*apenasCardsPedemLogin[\s\S]*loginAvantPendenteVisual = loginGlobalPendente/, 'diagnostico deve separar login global do AvantPro dos logins repetidos dentro dos cards');
  assert.match(mlBrowser, /function statusAvantProPedeLoginOuVinculo[\s\S]*accountLinkButtonsGlobais[\s\S]*accountLinkButtonsCards[\s\S]*botoesLoginSemSeparacao/, 'login repetido somente nos cards nao deve contar como login global pendente');
  assert.doesNotMatch(mlBrowser, /\|\|\s*\(botoesLoginCards\s*>\s*0\s*&&\s*!temDadosFortes\)/, 'botoes dos cards nao devem virar login global sem confirmar paineis de login');
  assert.match(mlBrowser, /function statusAvantProPedeLoginOuVinculo[\s\S]*cardsPedemLoginAvant[\s\S]*return true/, 'login repetido nos cards sem dados deve obrigar conexao do AvantPro');
  assert.match(mlBrowser, /function aguardarPrimeirosDadosAvantOuCardsWebview[\s\S]*cardLoginPanelsAvant[\s\S]*needsAvantLoginCards[\s\S]*needsAvantLogin = needsAvantLoginGlobal \|\| needsAvantLoginCards/, 'detector inicial deve tratar login AvantPro nos cards como login necessario');
  assert.match(mlBrowser, /function aguardarPrimeirosDadosAvantOuCardsWebview[\s\S]*poly-component__title[\s\S]*var resultadoMlVisivel = cardCount > 0 \|\| productLinks\.length > 0 \|\| !!resultadoVisual[\s\S]*var noResults = !loadingScreen && !resultadoMlVisivel && !hasAvantData/, 'detector inicial nao pode marcar sem resultados quando ha cards, contagem visual ou dados AvantPro');
  assert.match(mlBrowser, /function diagnosticarResultadosMercadoLivreWebview[\s\S]*poly-component__title[\s\S]*var resultadoMlVisivel = cardCount > 0 \|\| productLinks\.length > 0 \|\| !!resultadoVisual[\s\S]*var noResults = !loadingScreen && !resultadoMlVisivel && !hasRealAvantData/, 'diagnostico de resultados nao pode marcar sem resultados quando a busca carregou cards ou AvantPro');
  assert.match(mlBrowser, /cardSelectorsParaLoginAvant[\s\S]*cardLoginPanelsAvant[\s\S]*needsAvantLoginCards[\s\S]*needsAvantLogin = needsAvantLoginGlobal \|\| needsAvantLoginCards/, 'extracao principal deve tratar login AvantPro nos cards como login necessario');
  assert.match(mlBrowser, /function fecharModalBloqueanteAvantProNoWebview[\s\S]*textoEscopoGlobalAvant[\s\S]*escopoAvant = cardCountAvant > 0 \? globalBusca : bodyBusca[\s\S]*modalAvantPromocional/, 'fechamento de modal AvantPro deve ignorar texto de login dentro dos cards');
  assert.ok(
    mlBrowser.includes('vincule\\\\s+o\\\\s+avantpro|vincular\\\\s+agora') && mlBrowser.includes("via: 'top_right_point'"),
    'modal de vinculo do AvantPro deve ser fechado por botao ou geometria'
  );
  assert.ok(
    mlBaseBusca.includes('vincular\\\\s+(?:conta|agora)') && mlBaseBusca.includes(')) return false;'),
    'auto-login do AvantPro nao deve clicar em Vincular agora'
  );
  assert.match(mlBrowser, /function diagnosticarAvantProNoWebview[\s\S]*var modalAvantPromocional[\s\S]*modalAvantPromocional:\s*!!modalAvantPromocional/, 'diagnostico deve separar convite de vinculo do login real');
  assert.match(mlBrowser, /function statusAvantProPedeLoginOuVinculo[\s\S]*status\.modalAvantPromocional[\s\S]*return false/, 'convite de vinculo do AvantPro nao deve virar login obrigatorio');
  assert.match(mlBrowser, /async function coletarPrimeiraPaginaFavoritosControlada[\s\S]*if \(clickInfo && clickInfo\.loginBlocked\)[\s\S]*cliquesAvantDesligados = true[\s\S]*loginAvantBloqueado = true[\s\S]*if \(loginAvantBloqueado && anuncios\.length\) break/, 'coleta controlada deve interromper novos cliques Avant quando o login bloquear a pagina');
  assert.match(mlBrowser, /__JK_AVANT_PRO_LOGIN_CLICKED_AT[\s\S]*loginClickAt[\s\S]*cardClickAt[\s\S]*legacyPareceLogin/, 'reload pos-login deve diferenciar login AvantPro de clique em card sem dados');
  assert.match(mlBrowser, /jkAvantCardClickCount[\s\S]*jkAvantCardClickedAt[\s\S]*Date\.now\(\) - ultimoClique < 1800/, 'cards AvantPro sem dados devem permitir novas tentativas controladas');
  assert.match(mlBrowser, /ehLinkProduto[\s\S]*clicarCardsSemDados[\s\S]*clicarCardsAvantSemDados/, 'AvantPro sem dados deve gerar cliques nos widgets dos cards sem abrir link de produto');
  assert.match(mlBrowser, /clicarCardsSemDados:\s*opcoes\.clicarCardsSemDados !== false/, 'extracao deve ativar clique nos cards AvantPro sem dados por padrao');
  assert.match(mlBrowser, /async function acionarCardsAvantProFilaWebview[\s\S]*const maxClicks = Math\.max\(0,\s*Math\.min\(8,[\s\S]*const maxTentativasPorCard = Math\.max\(1,\s*Math\.min\(3,[\s\S]*const maxRuntimeMs = Math\.max\(1200,\s*Math\.min\(8000,/, 'fila controlada deve limitar cliques, tentativas por card e tempo de cada rodada AvantPro');
  assert.doesNotMatch(mlBrowser, /if \(item\.score <= 5\) break;/, 'clique em carregar dados AvantPro nao deve parar no primeiro card da viewport');
  assert.match(mlBrowser, /function mesclarAnunciosAvant[\s\S]*copiarCampoSeVazio[\s\S]*tituloValidoFavoritosCanonico\(atual, id\)[\s\S]*!imagemValidaFavoritosCanonico\(combinado\) && imagemValidaFavoritosCanonico\(item\)/, 'merge AvantPro deve preservar titulo e foto validos e preencher apenas campos ausentes ou fracos');
  assert.match(mlBrowser, /tentouLoginAvant[\s\S]*recarregarAposLoginSePreciso\(status\)/, 'preparacao obrigatoria do AvantPro deve recarregar somente depois de tentar login');
  assert.match(buscaRanking, /statusAvantProLoginConcluidoSemDados[\s\S]*reloadAposLoginAvantRecomendado[\s\S]*recarregarAposLoginAvantProFavoritosSePossivel/, 'acao manual de login AvantPro deve retomar a coleta pelo helper pos-login');
  assert.match(buscaRanking, /reloadedAfterAvantLogin \|\| statusFinal\.resumedAfterAvantLogin/, 'coleta deve repetir extracao quando o pos-login AvantPro retomar sem reload');
  assert.match(mlBrowser, /function normalizarStatusAvantProCarregadoParaColeta[\s\S]*avantShellProntoParaColeta:\s*true/, 'shell do AvantPro carregado deve liberar inicio da coleta');
  assert.match(mlBrowser, /!statusAvantProPedeLoginOuVinculo\(status\) && statusAvantProShellSemDados\(status\)[\s\S]*normalizarStatusAvantProCarregadoParaColeta\(status\)/, 'shell sem login real nao deve abrir login nem travar favoritos antes da coleta');
  assert.match(buscaRanking, /function statusAvantProPodeRetomarColeta[\s\S]*accountActionRequired[\s\S]*infoButtons[\s\S]*bodyHasAvantInfo/, 'tela de conexao deve reconhecer AvantPro carregado nos cards e retomar a coleta');
  assert.match(buscaRanking, /autoLoginAvant:\s*false[\s\S]*statusAvantProPodeRetomarColeta\(novoStatus\)/, 'verificacao manual da conexao nao deve reabrir login quando o AvantPro ja carregou');
  assert.match(buscaRanking, /const statusAntesLogin = await diagnosticarAvantProNoWebview\(\)[\s\S]*statusAvantProPodeRetomarColeta\(statusAntesLogin\)[\s\S]*concluirRetomada/, 'acao automatica de conectar deve diagnosticar antes de abrir login AvantPro');
  assert.match(mlBrowser, /tentouAcionar && !statusAvantProPedeLoginOuVinculo\(ultimo\) && statusAvantProShellSemDados\(ultimo\)[\s\S]*normalizarStatusAvantProCarregadoParaColeta\(ultimo\)/, 'aguardo AvantPro deve devolver shell pronto em vez de abrir login sem login real');
  assert.match(mlBrowser, /const shellAvantOperavel[\s\S]*paginaLoginReal[\s\S]*if \(shellAvantOperavel && !loginRealBloqueante\) return false/, 'AvantPro com widgets carregados nao deve virar tela de login sem login real');
  assert.match(mlBrowser, /var loginRealRegex[\s\S]*var conviteAvantRegex[\s\S]*accountLinkButtonsGlobaisEfetivos/, 'diagnostico deve separar convite promocional do AvantPro de login real');
  assert.match(mlBrowser, /queryAllDeep\('button, a, input\[type="button"\][\s\S]*\[class\*="andes-button"\][\s\S]*div, span'\)/, 'clique AvantPro deve incluir botoes renderizados como div/span');
  assert.match(mlBrowser, /function montarScriptLocalizarBolinhaAvantPro[\s\S]*dynamic-access[\s\S]*bolinha_avant_estimado/, 'fluxo deve abrir a bolinha do AvantPro sem confundir cards do Mercado Livre');
  assert.doesNotMatch(mlBrowser, /reason:\s*'menu_avant_ja_aberto'/, 'pre-login nao deve pular a bolinha quando Ferramentas aparece apenas como texto oculto');
  assert.match(mlBrowser, /const bolinha = await abrirBolinhaAvantProSeNecessario\(\)[\s\S]*tentativas\.push\(\{ via: 'bolinha'[\s\S]*const clicksDom = await acionarControlesAvantProNoWebview/, 'fluxo deve clicar/garantir a bolinha antes de tentar Ferramentas');
  assert.match(mlBrowser, /bolinha_avant_dom_ajuste_borda[\s\S]*originalTarget:\s*alvo/, 'clique real na bolinha deve usar o canto inferior direito que abre o menu AvantPro no navegador interno');
  assert.doesNotMatch(mlBrowser, /const vincularConta = await clicarVincularContaAvantProPorCoordenada/, 'fluxo de login nao deve usar Vincular conta antes de Ferramentas');
  assert.doesNotMatch(mlBrowser, /source:\s*'vincular_conta_avant_estimado'/, 'Vincular conta nao pode usar coordenada estimada para nao clicar no menu do Mercado Livre');
  assert.match(mlBrowser, /rotuloFerramentasMenuVisivel[\s\S]*\/\^\(ferramentas\|tools\)\$\/[\s\S]*source:\s*`\$\{alvo\.source\}_ajuste_\$\{dx\}_\$\{dy\}`/, 'clique em Ferramentas deve mirar o item lateral real e tentar ajustes verticais');
  assert.match(mlBrowser, /diagnostico_entrada_dom[\s\S]*if \(entradaDom && entradaDom\.prontoParaLogin\)[\s\S]*clicarFerramentasAvantProPorCoordenada/, 'fluxo de Ferramentas deve reconhecer login aberto pelo clique DOM antes do clique por coordenada');
  assert.match(mlBrowser, /ehFerramentasAvant\(item\.text, item\.context, item\.visibleText\) \|\| ehMenuFlutuanteAvant\(item\.text, item\.context\)[\s\S]*rect\.width < 40 \|\| rect\.height < 20/, 'clique DOM de Ferramentas deve ignorar rotulos pequenos e deixar o clique real por coordenada agir');
  assert.match(mlBrowser, /if \(\/estimado\|rotulo\|dom\/i\.test\(String\(alvo\.source \|\| ''\)\)\)[\s\S]*\[-45, 45, -75\][\s\S]*\[60, 95, 130\][\s\S]*diagnosticarEntradaAvantProNoWebview\(\)[\s\S]*entrada && entrada\.prontoParaLogin/, 'clique real em Ferramentas deve tentar alvos alternativos quando o AvantPro expuser rotulo/estimativa/DOM deslocado');
  assert.match(mlBrowser, /function tentarLoginAvantProPorDigitacaoNativa[\s\S]*montarScriptLocalizarCampoEmailAvantProParaDigitacao[\s\S]*digitarTextoNativoNoNavegadorMl/, 'login por Ferramentas deve ter fallback de digitacao nativa do e-mail');
  assert.match(mlBrowser, /function tentarLoginAvantProPorElectronNativo[\s\S]*loginAvantProEmbeddedBrowser\(email\)[\s\S]*via:\s*'electron_native_login'/, 'login por Ferramentas deve tentar preenchimento nativo do Electron nos frames da extensao');
  assert.match(mlBrowser, /function tentarLoginAvantProPorElectronNativo[\s\S]*aguardarConfirmacaoLoginAvantProNoWebview\(\{[\s\S]*timeoutMs:\s*Number\(opcoes\.timeoutConfirmacaoMs\) \|\| 6500[\s\S]*tentarPreencherEmail:\s*false/, 'login nativo pelo Electron deve aguardar a mensagem de obrigado antes de cair para outro caminho');
  assert.match(mlBrowser, /tentativaLogin && tentativaLogin\.success && \([\s\S]*tentativaLogin\.emailVisible[\s\S]*campo_email_avant_visivel[\s\S]*return \{ success: true, tentativas/, 'abrir Ferramentas so deve ser considerado sucesso quando o campo de e-mail aparecer');
  assert.match(mlBrowser, /reforco_ferramentas_apos_electron_sem_campo[\s\S]*timeoutConfirmacaoMs:\s*8000[\s\S]*electron_native_login_reforco_ferramentas/, 'quando o preenchimento nativo nao achar campo de e-mail, o fluxo deve reabrir Ferramentas e tentar novamente');
  assert.match(mlBrowser, /entrarAvantProPorFerramentasAntesPesquisa[\s\S]*tentarLoginAvantProPorElectronNativo\([\s\S]*tentarLoginAvantProPorDigitacaoNativa/, 'fluxo antes da pesquisa deve tentar Electron nativo antes da digitacao por coordenada');
  assert.match(mlBrowser, /digitacaoNativaTentativas[\s\S]*tentarLoginAvantProPorDigitacaoNativa[\s\S]*aguardarConfirmacaoLoginAvantProNoWebview/, 'fluxo antes da pesquisa deve tentar digitacao nativa antes de liberar a busca');
  assert.match(mlBrowser, /typeText\(payload\)[\s\S]*jk-ml-browser-type[\s\S]*jk-ml-browser-type-result/, 'proxy do navegador ML deve encaminhar digitacao nativa pelo shell');
  assert.match(ipc, /embedded-ml-browser-type[\s\S]*contents\.insertText\(text\)[\s\S]*pressEnter/, 'Electron deve aceitar digitacao nativa no BrowserView do Mercado Livre');
  assert.match(ipc, /embedded-ml-browser-native-type-start[\s\S]*url:\s*mercadoLivreUrlSeguraParaLog\(contents\.getURL\(\)\)[\s\S]*embedded-ml-browser-native-type-done[\s\S]*url:\s*mercadoLivreUrlSeguraParaLog\(contents\.getURL\(\)\)[\s\S]*embedded-ml-browser-native-click[\s\S]*url:\s*mercadoLivreUrlSeguraParaLog\(contents\.getURL\(\)\)/, 'logs de digitacao e clique durante login nao podem persistir query ou hash');
  assert.match(shell, /jk-ml-browser-type/, 'shell deve receber comando de digitacao nativa');
  assert.match(shell, /typeEmbeddedMlBrowser/, 'shell deve usar API nativa de digitacao quando disponivel');
  assert.match(shell, /jk-ml-browser-type-result/, 'shell deve devolver resultado da digitacao nativa para o Favoritos');
  assert.match(preloadRoot, /typeEmbeddedMlBrowser:\s*\(payload\)\s*=>\s*ipcRenderer\.invoke\('embedded-ml-browser-type'/, 'preload raiz deve expor digitacao nativa do navegador ML');
  assert.match(preloadApp, /typeEmbeddedMlBrowser:\s*\(payload\)\s*=>\s*ipcRenderer\.invoke\('embedded-ml-browser-type'/, 'preload do app deve expor digitacao nativa do navegador ML');
  const authClassifierMatch = ipc.match(/function isMercadoLivreAuthenticationFlowUrl\(targetUrl\)[\s\S]*?^}/m);
  assert.ok(authClassifierMatch, 'IPC deve ter classificador unico para o fluxo de autenticacao do Mercado Livre');
  const classifyMlAuthUrl = vm.runInNewContext(`(${authClassifierMatch[0]})`, { URL, decodeURIComponent });
  [
    'https://www.mercadolivre.com.br/gz/account-verification?go=busca',
    'https://www.mercadolivre.com/jms/mlb/lgz/login?loginType=negative_traffic',
    'https://www.mercadolivre.com.br/login?go=busca',
    'https://www.mercadolivre.com.br/login/challenges',
    'https://www.mercadolivre.com.br/password/validation?transaction=abc',
    'https://www.mercadolivre.com.br/totp/'
  ].forEach(url => assert.strictEqual(classifyMlAuthUrl(url), true, `URL de autenticacao deve ser preservada: ${url}`));
  assert.strictEqual(classifyMlAuthUrl('https://lista.mercadolivre.com.br/furadeira'), false, 'pagina de pesquisa nao deve ser classificada como autenticacao');
  assert.strictEqual(classifyMlAuthUrl('https://lista.mercadolivre.com.br/captcha'), false, 'rota captcha em host de busca nao deve ser tratada como autenticacao');
  assert.strictEqual(classifyMlAuthUrl('https://lista.mercadolivre.com.br/captcha?q=captcha'), false, 'termo captcha em uma pesquisa nao deve gerar falso fluxo de autenticacao');
  assert.strictEqual(classifyMlAuthUrl('https://lista.mercadolivre.com.br/login?q=login'), false, 'rota generica login em host de busca nao deve ser tratada como autenticacao');
  assert.strictEqual(classifyMlAuthUrl('https://example.com/login'), false, 'login de dominio externo nao deve ser classificado como Mercado Livre');
  const preserveClassifierMatch = ipc.match(/function shouldPreserveMercadoLivreAuthenticationNavigation\(currentUrl, requestedUrl\)[\s\S]*?^}/m);
  const stableUrlMatch = ipc.match(/function shouldRememberMercadoLivreStableUrl\(targetUrl\)[\s\S]*?^}/m);
  const safeLogUrlMatch = ipc.match(/function mercadoLivreUrlSeguraParaLog\(targetUrl\)[\s\S]*?^}/m);
  const safeLogMessageMatch = ipc.match(/function mercadoLivreMensagemSeguraParaLog\(value\)[\s\S]*?^}/m);
  assert.ok(preserveClassifierMatch && stableUrlMatch && safeLogUrlMatch && safeLogMessageMatch, 'IPC deve expor decisoes puras para preservar auth, lembrar URL estavel e higienizar log');
  const navigationSandbox = {
    URL,
    decodeURIComponent,
    normalizeComparableUrl: value => String(value || '').replace(/[?#].*$/, '').replace(/\/$/, '').toLowerCase()
  };
  vm.runInNewContext(`
    ${authClassifierMatch[0]}
    ${preserveClassifierMatch[0]}
    ${stableUrlMatch[0]}
    ${safeLogUrlMatch[0]}
    ${safeLogMessageMatch[0]}
    this.shouldPreserve = shouldPreserveMercadoLivreAuthenticationNavigation;
    this.shouldRemember = shouldRememberMercadoLivreStableUrl;
    this.safeLogUrl = mercadoLivreUrlSeguraParaLog;
    this.safeLogMessage = mercadoLivreMensagemSeguraParaLog;
  `, navigationSandbox);
  const searchUrl = 'https://lista.mercadolivre.com.br/furadeira';
  const challengeUrl = 'https://www.mercadolivre.com.br/login/challenges?transaction_id=segredo#etapa';
  assert.strictEqual(navigationSandbox.shouldPreserve(challengeUrl, searchUrl), true, 'busca antiga nao pode substituir challenge atual');
  assert.strictEqual(navigationSandbox.shouldPreserve(searchUrl, challengeUrl), true, 'challenge antigo nao pode substituir busca concluida');
  assert.strictEqual(navigationSandbox.shouldPreserve(searchUrl, 'https://lista.mercadolivre.com.br/parafusadeira'), false, 'duas navegacoes normais nao devem ser bloqueadas');
  assert.strictEqual(navigationSandbox.shouldPreserve('about:blank', searchUrl), false, 'BrowserView vazio deve poder recuperar URL estavel');
  assert.strictEqual(navigationSandbox.shouldRemember(challengeUrl), false, 'challenge nunca deve virar URL restauravel');
  assert.strictEqual(navigationSandbox.shouldRemember(searchUrl), true, 'busca normal deve poder ser restaurada');
  assert.strictEqual(navigationSandbox.safeLogUrl(challengeUrl), 'https://www.mercadolivre.com.br/login/challenges', 'log deve remover query e hash do challenge');
  assert.strictEqual(navigationSandbox.safeLogMessage(`Falha em ${challengeUrl}`), 'Falha em https://www.mercadolivre.com.br/login/challenges', 'mensagem de erro persistida deve remover tokens da URL');
  const showIpcStart = ipc.indexOf("ipcMain.handle('embedded-ml-browser-show'");
  const showIpcEnd = ipc.indexOf("ipcMain.handle('favoritos-job-browser-start'", showIpcStart);
  const showIpcBlock = ipc.slice(showIpcStart, showIpcEnd);
  assert.ok(showIpcBlock.indexOf('embedded-ml-browser-auth-flow-preserved') >= 0, 'show do BrowserView deve preservar autenticacao em andamento');
  assert.match(showIpcBlock, /shouldPreserveMercadoLivreAuthenticationNavigation\(observedUrl, url\)/, 'show deve usar a decisao testada que bloqueia busca antiga sobre login e login antigo sobre busca concluida');
  assert.doesNotMatch(showIpcBlock, /const requestedAuthFlow[\s\S]{0,160}favoritosEmbeddedMlLastUrl = url/, 'URL apenas solicitada nao pode virar URL estavel antes de ser observada');
  assert.match(showIpcBlock, /mercadoLivreUrlSeguraParaLog\(observedUrl\)[\s\S]*mercadoLivreUrlSeguraParaLog\(url\)/, 'log da trava nao deve gravar query ou hash sensivel do login');
  assert.match(showIpcBlock, /restaurarSessaoAvantProAntesDeAbrirNavegador\('before-embedded-ml-browser-show', \{ url: safeUrl \}\)/, 'restauracao AvantPro nao deve receber query sensivel do login');
  assert.match(showIpcBlock, /embedded-ml-browser-show-load-warning-cleared[\s\S]*loadedUrl:\s*mercadoLivreUrlSeguraParaLog\(loadedUrl\)[\s\S]*embedded-ml-browser-avant-monitoring-disabled[\s\S]*mercadoLivreUrlSeguraParaLog\(loadedUrl\)/, 'logs do show devem persistir somente URLs sem query ou hash');
  assert.ok(showIpcBlock.indexOf('preservedAuthFlow: true') < showIpcBlock.indexOf('view.webContents.loadURL(url)'), 'trava de autenticacao deve executar antes de qualquer loadURL da busca antiga');
  assert.match(ipc, /embedded-ml-browser-position[\s\S]*currentUrl = view\.webContents\.getURL\(\)[\s\S]*authFlow:\s*isMercadoLivreAuthenticationFlowUrl\(currentUrl\)/, 'reposicionamento deve devolver URL real e estado de autenticacao');
  assert.match(shell, /resultUrl = String\(result && result\.url[\s\S]*mlBrowserUrlByTab\.set\(embedded\.found\.id, resultUrl\)[\s\S]*event:\s*'url-sync'/, 'shell deve sincronizar a URL real retornada pelo BrowserView');
  const restoreBrowserStart = shell.indexOf('function restaurarMlBrowserParaComando');
  const restoreBrowserEnd = shell.indexOf("if (data.channel === 'jk-ml-browser-execute')", restoreBrowserStart);
  const restoreBrowserBlock = shell.slice(restoreBrowserStart, restoreBrowserEnd);
  assert.ok(restoreBrowserBlock.indexOf('positionEmbeddedMlBrowser') >= 0, 'comandos devem consultar/reposicionar o BrowserView antes de restaurar URL');
  assert.match(restoreBrowserBlock, /return embeddedApi\.positionEmbeddedMlBrowser\(dataBounds\.bounds\)[\s\S]*\.then\(result =>[\s\S]*return restaurarUrlDaAba\(\)/, 'caminho ativo deve consultar position e somente restaurar URL quando a troca real de aba exigir');
  assert.match(restoreBrowserBlock, /mlBrowserRestorableUrlByTab[\s\S]*restaurarUrlDaAba\(true\)/, 'BrowserView vazio deve recuperar somente a ultima URL estavel nao-auth');
  assert.match(restoreBrowserBlock, /const urlSalva = mlBrowserRestorableUrlByTab\.get\(found\.id\) \|\| ''/, 'restauracao nao pode cair para URL auth apenas observada');
  assert.match(restoreBrowserBlock, /if \(donoMudou && authFlow\)[\s\S]*mlBrowserOwnerId = ownerAnterior[\s\S]*return false/, 'comando de outra aba nao pode atuar na tela de autenticacao da aba dona');
  assert.match(restoreBrowserBlock, /if \(!donoMudou \|\| authFlow\)[\s\S]*return true/, 'mesma aba ou autenticacao ativa deve continuar sem reload');
  const updateVisibleStart = shell.indexOf('function updateVisibleState()');
  const updateVisibleEnd = shell.indexOf('function activateTab(', updateVisibleStart);
  const updateVisibleBlock = shell.slice(updateVisibleStart, updateVisibleEnd);
  assert.doesNotMatch(updateVisibleBlock, /mlBrowserOwnerId\s*=/, 'troca visual de aba nao pode assumir ou apagar a propriedade do BrowserView antes do IPC');
  const shellShowStart = shell.indexOf("if (data.channel === 'jk-ml-browser-show' || data.channel === 'jk-ml-browser-position')");
  const shellShowEnd = shell.indexOf("if (data.channel === 'jk-ml-browser-hide')", shellShowStart);
  const shellShowBlock = shell.slice(shellShowStart, shellShowEnd);
  assert.match(shellShowBlock, /const ownerAntesDaAcao = mlBrowserOwnerId[\s\S]*bloqueadoPorAuthDeOutraAba[\s\S]*reposicionarEmbeddedMlBrowserParaDono\(ownerAntesDaAcao, embeddedApi\)[\s\S]*mlBrowserOwnerId = embedded\.found\.id/, 'show de outra aba deve preservar o dono anterior enquanto houver auth');
  assert.doesNotMatch(shellShowBlock, /mlBrowserRestorableUrlByTab\.set\([^\n]*payload\.url/, 'URL solicitada ainda nao observada nao pode virar restauravel');
  assert.match(preloadTab, /typeEmbeddedMlBrowser:\s*\(payload\)\s*=>\s*ipcRenderer\.invoke\('embedded-ml-browser-type'/, 'preload da aba deve expor digitacao nativa do navegador ML');
  assert.match(localAppPaths, /function getAvantProLastGoodStorageRootDir\(\)[\s\S]*_avantpro_storage_last_good[\s\S]*function getAvantProLastGoodStorageDir\(\)[\s\S]*AVANTPRO_CHROME_EXTENSION_ID/, 'Electron deve ter pasta last_good para memoria da extensao AvantPro');
  assert.match(localAppPaths, /async function saveAvantProExtensionStorageSnapshot[\s\S]*flushPersistentSessions[\s\S]*copyDirectoryWithoutLocks[\s\S]*avantpro-storage-snapshot-saved/, 'Electron deve salvar snapshot da sessao AvantPro apos login confirmado');
  assert.match(localAppPaths, /async function restoreAvantProExtensionStorageSnapshot[\s\S]*backupAndResetAvantProExtensionStorage[\s\S]*copyDirectoryWithoutLocks[\s\S]*avantpro-storage-snapshot-restored/, 'Electron deve restaurar snapshot da sessao AvantPro antes de pedir novo login');
  assert.match(localAppPaths, /async function ensureAvantProExtensionStorageFromSnapshot[\s\S]*restoreAvantProExtensionStorageSnapshot/, 'carregamento do navegador deve poder garantir sessao AvantPro a partir do snapshot');
  assert.match(windowModule, /ensureAvantProExtensionStorageFromSnapshot\('before-extension-load'[\s\S]*snapshotAtualOuRestaurado[\s\S]*importAvantProExtensionStorageFromChrome/, 'Electron deve tentar snapshot antes de importar storage do Chrome');
  assert.match(windowModule, /hasAvantLoaded && typeof saveAvantProExtensionStorageSnapshot === 'function'[\s\S]*after-extension-load-current-auth[\s\S]*avantpro-storage-snapshot-autosave-result/, 'Electron deve salvar automaticamente a sessao AvantPro atual quando ela ja estiver valida no carregamento');
  assert.match(windowModule, /function tentarRestaurarSnapshotAvantProParaWebContents[\s\S]*restoreAvantProExtensionStorageSnapshot[\s\S]*avantpro-after-storage-snapshot-restore/, 'quando AvantPro pedir login, Electron deve tentar restaurar o snapshot antes de continuar');
  assert.match(localAppPaths, /function resolveElectronUserDataDir[\s\S]*JK Sistema Cliente[\s\S]*requestSingleInstanceLock\(\)[\s\S]*second-instance/, 'Electron deve usar perfil canonico e bloquear uma segunda instancia');
  assert.doesNotMatch(localAppPaths, /JK_DEFAULT_ELECTRON_USER_DATA_DIR\s*=\s*app\.isPackaged\s*\?/, 'perfil Electron nao pode variar entre dev e pacote');
  assert.match(localAppPaths, /hasAccessToken[\s\S]*hasLoginAt[\s\S]*hasAuthRecord[\s\S]*cacheFresh/, 'AvantPro deve separar auth V2 do vencimento do cache de contas');
  assert.match(localAppPaths, /function avantProStorageAuthLooksUsable\(authInfo\) \{\s*return !!\(authInfo && authInfo\.hasAuthRecord\);\s*\}/, 'snapshot AvantPro deve aceitar auth V2 com cache expirado e rejeitar cadastro antigo sem token');
  assert.match(localAppPaths, /JK_RESET_AUTH_STORAGE_ON_STARTUP_CRASH[\s\S]*entries\.unshift\('Local Storage', 'Session Storage', 'WebStorage'\)/, 'recuperacao de crash deve preservar login por padrao');
  assert.match(backendModule, /ses\.cookies\.flushStore\(\)[\s\S]*ses\.flushStorageData\(\)/, 'persistencia deve descarregar cookies e storage no disco');
  assert.match(backendModule, /cookies\.on\('changed'[\s\S]*schedulePersistentSessionsFlush[\s\S]*async function persistAuthenticationState[\s\S]*saveAvantProExtensionStorageSnapshot/, 'mudancas de auth devem persistir cookies e snapshot AvantPro');
  assert.match(ipc, /before-quit[\s\S]*event\.preventDefault\(\)[\s\S]*persistAuthenticationState\('before-quit-authentication'\)[\s\S]*app\.quit\(\)/, 'encerramento deve aguardar persistencia antes de sair');
  assert.match(ipc, /mercado-livre-auth-flow-completed[\s\S]*saveAvantPro:\s*false/, 'fim do login ML deve descarregar cookies imediatamente');
  assert.match(syncFavoritosRuntime, /electron_app\/main\/modules\/local-app-paths\.js[\s\S]*electron_app\/main\/modules\/backend\.js/, 'sincronizador deve entregar os modulos de persistencia');
  assert.match(ipc, /avantpro-storage-status[\s\S]*avantpro-storage-save-current[\s\S]*avantpro-storage-restore-last-good/, 'IPC deve expor diagnostico, salvamento e restauracao do storage AvantPro');
  assert.match(preloadRoot, /saveAvantProStorageSnapshot[\s\S]*avantpro-storage-save-current[\s\S]*restoreAvantProStorageSnapshot[\s\S]*avantpro-storage-restore-last-good/, 'preload raiz deve expor snapshot AvantPro');
  assert.match(preloadApp, /saveAvantProStorageSnapshot[\s\S]*avantpro-storage-save-current[\s\S]*restoreAvantProStorageSnapshot[\s\S]*avantpro-storage-restore-last-good/, 'preload do app deve expor snapshot AvantPro');
  assert.match(preloadTab, /saveAvantProStorageSnapshot[\s\S]*avantpro-storage-save-current[\s\S]*restoreAvantProStorageSnapshot[\s\S]*avantpro-storage-restore-last-good/, 'preload da aba deve expor snapshot AvantPro');
  assert.match(mlBrowser, /function salvarMemoriaAvantProConfirmadaFavoritos[\s\S]*saveAvantProStorageSnapshot[\s\S]*favoritos_login_confirmado/, 'Favoritos deve pedir snapshot quando o login AvantPro for confirmado');
  assert.match(mlBrowser, /function registrarLoginAvantProConfirmadoFavoritos[\s\S]*salvarMemoriaAvantProConfirmadaFavoritos\('favoritos_login_confirmado'\)/, 'confirmacao de login AvantPro deve salvar a memoria da extensao');
  assert.match(windowModule, /shellAvantOperavel[\s\S]*needsAccountLink = !ok && \(paginaLoginReal \|\| \(accountLinkButtons > 0 && !shellAvantOperavel\)\)/, 'diagnostico Electron nao deve disparar recuperacao de login quando ha shell AvantPro operavel');
  assert.match(mlBrowser, /!\(forceClick && item\.node\.dataset\.jkAvantClicked === '1'\)|!\(forceClick && item\.node\.dataset\.jkAvantClicked === "1"\)|\(!forceClick && item\.node\.dataset\.jkAvantClicked === '1'\)/, 'clique forcado do AvantPro deve poder repetir botao ja tentado');
  assert.match(buscaRanking, /async function buscarAnunciosFavoritosPorTermoFluxoControlado[\s\S]*coletarPrimeiraPaginaFavoritosControlada\(\{[\s\S]*loteCliques:\s*opcoes\.loteCliques === undefined \? 6/, 'fluxo controlado deve acionar os controles Avant em lotes limitados antes de concluir a extracao');
  assert.match(buscaRanking, /buscarAnunciosFavoritosPorTermo\(termo,[\s\S]*exigirAvantPro:\s*true[\s\S]*Avant Pro nao retornou anuncios coletaveis/, 'busca de favoritos deve exigir dados do AvantPro');
  assert.match(buscaRanking, /statusFinalAvantWrapper[\s\S]*precisaConectarAvantWrapper[\s\S]*aguardarConexaoAvantProFavoritos\(statusFinalAvantWrapper, \{ termo \}\)[\s\S]*retryAvantConectado/, 'busca vazia deve reconectar AvantPro e tentar novamente antes de erro');
  assert.match(buscaRanking, /Nao consegui ler dados coletaveis do Avant Pro/, 'falha de leitura do AvantPro deve parar o fluxo');
  assert.doesNotMatch(buscaRanking, /buscarFallbackLegado|\/api\/favoritos\/ml\/primeira-pagina|\/api\/favoritos\/pesquisar|Usando fallback do Mercado Livre|fallback legado|anunciosFallback/, 'busca de favoritos nao deve usar fallback Mercado Livre');
  assert.match(runtime, /ML_FAVORITOS_COLETA_ANUNCIOS_MAX = 80[\s\S]*ML_FAVORITOS_RANKING_ANUNCIOS_MAX = 80[\s\S]*ML_FAVORITOS_HISTORICO_ANUNCIOS_MAX = ML_FAVORITOS_RANKING_ANUNCIOS_MAX/, 'historico deve salvar/listar 80 primeiros do ranking sem reduzir a coleta');
  assert.match(buscaRanking, /Number\(ML_FAVORITOS_COLETA_ANUNCIOS_MAX\) \|\| 80[\s\S]*function limitarAnunciosFavoritosRanking[\s\S]*slice\(0, ML_FAVORITOS_RANKING_ANUNCIOS_MAX\)/, 'ranking salvo deve limitar a 80 sem diminuir limite de coleta');
  assert.match(mlBaseBusca, /function construirUrlItemMercadoLivreFavoritos[\s\S]*produto\.mercadolivre\.com\.br[\s\S]*function normalizarUrlItemMercadoLivreFavoritos/, 'consulta do item ML deve conseguir montar link canonico pelo MLB');
  assert.match(mlBaseBusca, /const permalink = normalizarUrlItemMercadoLivreFavoritos\(item\.permalink \|\| item\.url \|\| item\.link,[\s\S]*url:\s*permalink,[\s\S]*permalink,[\s\S]*link:\s*permalink,[\s\S]*pictures:\s*Array\.isArray\(item\.pictures\)/, 'consulta do item ML deve retornar link, permalink, foto e pictures para enriquecer o ranking');
  assert.match(buscaRanking, /function tituloAnuncioFavoritosPrecisaComplemento[\s\S]*\/\^jm\$\/i[\s\S]*function anuncioFavoritosCandidatoRanking[\s\S]*if \(id\) return true/, 'ranking deve manter anuncio com MLB mesmo quando o titulo vier fraco como JM');
  assert.match(buscaRanking, /function aplicarMetadataBasicaAnuncioFavoritos[\s\S]*normalizarUrlAnuncioFavoritosRanking[\s\S]*preencherImagemAnuncioFavoritos[\s\S]*tituloAnuncioFavoritosPrecisaComplemento/, 'ranking deve aplicar url, titulo e foto vindos da API/cache do item');
  assert.match(buscaRanking, /const semComplemento = pendentes\.filter\(item => item && \(!item\.url \|\| tituloAnuncioFavoritosPrecisaComplemento\(item\.titulo, item\.id\) \|\| !vendedorValido\(item\.vendedor\) \|\| !obterImagemAnuncioFavoritos\(item\)/, 'enriquecimento deve chamar API tambem quando faltar link, foto ou titulo bom');
  assert.match(execucao, /deduplicarAnunciosFavoritos\(coletados\)[\s\S]*\.filter\(anuncioFavoritosCandidatoRanking\)/, 'execucao nao deve descartar MLB antes do enriquecimento de metadata');
  assert.match(historico, /grupo\.anuncios\.slice\(0, ML_FAVORITOS_RANKING_ANUNCIOS_MAX\)\.map\(anuncioHistoricoPayload\)/, 'historico deve gravar os 80 primeiros anuncios do ranking');
  assert.match(schemasFavoritos, /modo_coleta:\s*str \| None[\s\S]*class FavoritosJobColetaTermoRequest/, 'job de favoritos deve aceitar modo avantpro_browser e payload de coleta por termo');
  assert.match(routerFavoritos, /\/jobs\/\{job_id\}\/proxima-coleta[\s\S]*\/jobs\/\{job_id\}\/coleta-termo/, 'router deve expor endpoints para navegador pedir e devolver coletas');
  assert.match(jobsBackend, /FAVORITOS_JOB_MODE_AVANTPRO_BROWSER = "avantpro_browser"[\s\S]*def favoritos_jobs_proxima_coleta[\s\S]*def favoritos_jobs_coleta_termo/, 'backend deve orquestrar coletas AvantPro feitas pelo navegador interno');
  assert.match(jobsBackend, /def _build_resultados_browser_job[\s\S]*total_com_dados_avant = sum\(1 for anuncio in unicos if _anuncio_tem_dados_avant_confiaveis\(anuncio\)\)[\s\S]*_rankear_backend\(unicos\)/, 'backend deve ranquear todos os anuncios validos e usar dados AvantPro apenas como metrica');
  assert.match(jobsBackend, /def _job_find_storage_path[\s\S]*favoritos_jobs[\s\S]*def _job_persist[\s\S]*json\.dump[\s\S]*def _job_load\(job_id: str, client_id: str, username: str\)[\s\S]*_job_owner_matches\(job, client_id, username\)[\s\S]*FAVORITOS_JOB_ACTIVE\[job_id\] = job/, 'job persistido deve sobreviver a perda de memoria sem sair do cliente e usuario autenticados');
  assert.match(jobsBackend, /def favoritos_jobs_latest[\s\S]*_job_load_latest\(client_id, username\)/, 'recuperacao do job recente deve ocorrer somente pela rota latest autenticada');
  const resolverJobBlock = jobsBackend.match(/def _job_resolve_active[\s\S]*?\ndef _job_get/);
  assert.ok(resolverJobBlock, 'resolvedor isolado de jobs deve existir');
  assert.doesNotMatch(resolverJobBlock[0], /_job_load_latest/, 'ID inexistente nao pode cair silenciosamente no job mais recente');
  assert.match(jobsBackend, /def _job_get\(job_id: str, client_id: str, username: str\)[\s\S]*status_code=404/, 'job inexistente ou de outro proprietario deve retornar 404');
  assert.match(execucao, /modo_coleta:\s*'avantpro_browser'[\s\S]*\/proxima-coleta[\s\S]*\/coleta-termo[\s\S]*function iniciarWorkerFavoritosAvantProJob/, 'Fazer favoritos deve usar job backend com worker visual do AvantPro');
  assert.match(execucao, /function receberStatusFavoritosJob\(status\)[\s\S]*jobIdStatus[\s\S]*mlFavoritosJobIdAtual = jobIdStatus/, 'renderer deve atualizar job_id quando backend recuperar o job ativo correto');
  assert.match(execucao, /opcoes\.usarRendererAntigo === true \|\| opcoes\.usarBackendJob === false/, 'renderer antigo deve ficar apenas como caminho explicito');
  assert.match(ranking, /avantpro_fast_dom:\s*8/, 'ranking deve tratar vendas do DOM rapido do AvantPro como fonte confiavel');
  assert.match(ranking, /avantpro_fast_dom:\s*5/, 'ranking deve aceitar vendedor do DOM rapido do AvantPro');
  assert.match(init, /fazerFavoritosSkusSelecionadosCompleto[\s\S]*addEventListener\('click', executarFavoritos\)/, 'botao Fazer favoritos deve executar o fluxo completo que salva historico');
  assert.match(mlBrowser, /function construirUrlPesquisaMercadoLivre[\s\S]*return `https:\/\/lista\.mercadolivre\.com\.br\/\$\{encodeURIComponent\(slug \|\| valor\)\}`/, 'URL de pesquisa do ML nao deve depender do parametro q, que pode apenas preencher o campo');
  assert.match(mlBrowser, /function montarScriptGarantirPesquisaMercadoLivreSubmetida[\s\S]*\['keydown', 'keypress', 'keyup'\][\s\S]*KeyboardEvent\(name[\s\S]*key:\s*'Enter'[\s\S]*requestSubmit[\s\S]*location\.assign\(urlAlvo\)/, 'pesquisa do ML deve forcar Enter/form submit e fallback por URL');
  assert.match(mlBrowser, /var paginaProduto = !!\([\s\S]*ui-pdp-title[\s\S]*if \(cards > 0 && paginaCombinaComTermo && !paginaProduto\)/, 'pagina de produto nao pode ser aceita como resultado de busca do ML');
  assert.match(mlBrowser, /async function garantirPesquisaMercadoLivreSubmetida[\s\S]*montarScriptGarantirPesquisaMercadoLivreSubmetida\(valor,\s*urlAlvo\)/, 'navegador interno deve expor helper para garantir envio da pesquisa');
  assert.match(mlBrowser, /function montarScriptDiagnosticarPesquisaMercadoLivreAtual[\s\S]*paginaProduto[\s\S]*termo_nao_confere[\s\S]*async function aguardarPesquisaMercadoLivreAtual/, 'navegador interno deve validar se a pagina atual corresponde ao termo pesquisado');
  assert.match(buscaRanking, /abrirMercadoLivreNoPrograma\(\{[\s\S]*termoPesquisa:\s*termo[\s\S]*aguardarPesquisaMs:\s*opcoes\.segundoPlano \? 1200 : 900/, 'coleta de favoritos deve submeter a pesquisa do termo no Mercado Livre');
  assert.match(buscaRanking, /opcoes\.loginAvantAntesDaColeta === true[\s\S]*prepararAvantProAntesDaPesquisaFavoritos\([\s\S]*const urlPesquisaMl = construirUrlPesquisaMercadoLivre\(termo\)/, 'primeira pesquisa deve conectar o AvantPro antes de abrir a busca no Mercado Livre');
  assert.match(buscaRanking, /typeof prepararAvantProAntesDaPesquisaFavoritos !== 'function'[\s\S]*throw erroLoginAvantProFavoritos[\s\S]*const preparacaoAvant = await prepararAvantProAntesDaPesquisaFavoritos[\s\S]*!preparacaoAvantPrePesquisaConfirmada\(preparacaoAvant\)[\s\S]*const urlPesquisaMl = construirUrlPesquisaMercadoLivre\(termo\)/, 'coletor nao pode abrir a pesquisa se o login AvantPro nao foi confirmado');
  assert.match(buscaRanking, /let avantStatus = avantStatusPreColeta \|\| null[\s\S]*else if \(!avantStatus\)[\s\S]*diagnosticarAvantProNoWebview/, 'status AvantPro pre-coleta nao deve ser sobrescrito por diagnostico novo');
  assert.match(buscaRanking, /aguardarPesquisaMercadoLivreAtual\(termo,[\s\S]*url:\s*urlPesquisaMl[\s\S]*Pesquisa confirmada para[\s\S]*Acionando Avant Pro para carregar dados/, 'coleta deve validar a busca correta antes de acionar o AvantPro');
  assert.match(buscaRanking, /function validarAnunciosFavoritosPertencemAoTermo[\s\S]*nao correspondem a pesquisa[\s\S]*validarAnunciosFavoritosPertencemAoTermo\(termo,\s*anunciosAvant\)/, 'coleta deve abortar lote de anuncios sem afinidade com o termo pesquisado');
  assert.doesNotMatch(execucao, /mostrarBalaoFavoritosStatus\(`SKU \$\{destino\.sku \|\| ''\}: pesquisando[\s\S]*abrirMercadoLivreNoPrograma\(\{[\s\S]*termoPesquisa:\s*destino\.termo/, 'worker visual nao deve abrir a pesquisa antes da funcao de coleta para evitar reload duplicado');
  assert.match(statusModal, /FavoritosV2[\s\S]*ui\.statusModal[\s\S]*function mostrarBalaoFavoritosStatus[\s\S]*function posicionarBalaoFavoritosStatus/, 'status/modal do Favoritos deve ficar centralizado no namespace V2');
  assert.match(statusModal, /has-status-overlay[\s\S]*--ml-favoritos-status-overlay-height[\s\S]*agendarPosicaoNavegador\(\)/, 'status de favoritos deve reservar faixa e reposicionar BrowserView do Mercado Livre');
  assert.match(styles, /\.browser-frame-wrap\.has-status-overlay \.browser-host[\s\S]*height:\s*calc\(100% - var\(--ml-favoritos-status-overlay-height[\s\S]*margin-top:\s*var\(--ml-favoritos-status-overlay-height/, 'CSS deve reduzir o navegador para a barra de status nao ficar por baixo do Mercado Livre');
  assert.match(styles, /\.ml-favoritos-balloon-layer\.is-over-browser[\s\S]*padding:\s*var\(--ml-favoritos-balloon-layer-top[\s\S]*\.ml-favoritos-balloon-layer\.is-over-browser \.ml-favoritos-balloon/, 'balão de status deve ir para a faixa acima do navegador interno');
  assert.match(favoritosHtml, /id="ml-work-modal-live-status"[\s\S]*role="status"[\s\S]*aria-live="polite"/, 'modal do Mercado Livre deve ter barra fixa de status no cabecalho');
  assert.match(favoritosHtml, /\/favoritos\/v2\/ui\/status-modal\.js[\s\S]*\/favoritos\/ml-browser\.js[\s\S]*\/favoritos\/tabelas-layout\.js/, 'HTML deve carregar status-modal antes dos scripts que usam status/modal');
  assert.match(runtime, /const mlWorkModalLiveStatusEl = document\.getElementById\('ml-work-modal-live-status'\)/, 'runtime deve mapear a barra fixa de status do modal');
  assert.match(statusModal, /liveStatusEl[\s\S]*textContent = textoStatus[\s\S]*classList\.toggle\('hidden', !deveMostrarNoModal\)/, 'mensagens de progresso devem atualizar a barra fixa do modal');
  assert.match(statusModal, /__loaded[\s\S]*return;[\s\S]*statusOverlayNavegadorMl/, 'status-modal deve evitar carga duplicada e manter cache do overlay');
  assert.match(statusModal, /function executarAtualizacaoStatusFavoritosNoNavegadorMl[\s\S]*jk-favoritos-status-overlay[\s\S]*executeJavaScript\(script\)/, 'overlay visual do navegador interno deve morar no status-modal V2');
  assert.match(statusModal, /!statusOverlayNavegadorMl\.visivel && opcoes\.forcar !== true[\s\S]*opcoes\.forcar !== true[\s\S]*statusOverlayNavegadorMl\.ativo === ativoFinal/, 'overlay deve evitar trabalho repetido, mas aceitar remocao forcada no cancelamento');
  assert.match(statusModal, /function limparStatusTerminalFavoritos[\s\S]*has-status-overlay[\s\S]*forcar:\s*true[\s\S]*notificarShellFavoritosWorkerTerminal/, 'cancelamento deve limpar balao, overlay remoto e barra do shell em uma unica rotina');
  assert.match(elapsedTimer, /function formatDuration\(elapsedMs\)[\s\S]*padStart\(2, '0'\)[\s\S]*join\(':'\)/, 'cronometro deve formatar o tempo decorrido como HH:MM:SS');
  assert.match(elapsedTimer, /function createElapsedTimer\(options = \{\}\)[\s\S]*Date\.now\(\)[\s\S]*function start[\s\S]*function finish[\s\S]*root\.FavoritosElapsedTimer/, 'cronometro deve medir tempo de parede e expor inicio e encerramento explicitos');
  assert.match(shell, /id="favoritos-worker-elapsed"[\s\S]*elapsed-timer\.js\?v=[^"']+[\s\S]*FavoritosElapsedTimer\?\.create/, 'shell deve carregar o cronometro e mostrar o tempo na barra do worker');
  assert.match(shell, /function setFavoritosWorkerBar\(payload = \{\}, contexto = \{\}\)[\s\S]*if \(isFinal\)[\s\S]*favoritosWorkerElapsedTimer\.finish[\s\S]*else if \(payload\.active !== false[\s\S]*favoritosWorkerElapsedTimer\.start/, 'barra do worker deve iniciar o cronometro durante a coleta e encerra-lo em estado terminal');
  assert.match(shell, /favoritosWorkerTerminalLocked[\s\S]*if \(favoritosWorkerTerminalLocked && !isFinal && !startSignal\) return[\s\S]*\{ start: data\.channel === 'jk-favoritos-worker-enable' \}/, 'evento atrasado nao deve reiniciar cronometro terminado sem um novo sinal de inicio');
  assert.match(statusModal, /function resolverAcaoBalao[\s\S]*marcarBotaoAcaoBalaoClicado[\s\S]*esperarProximoPaint/, 'acoes do balao devem travar visualmente antes de continuar fluxo assincrono');
  assert.match(mlBrowser, /function posicionarBalaoFavoritosStatus\(\)[\s\S]*FavoritosV2\?\.ui\?\.statusModal\?\.posicionarBalaoFavoritosStatus/, 'ml-browser deve manter wrapper publico para posicionar status');
  assert.match(buscaRanking, /function mostrarBalaoFavoritosStatus\(mensagem, opcoes = \{\}\)[\s\S]*FavoritosV2\?\.ui\?\.statusModal\?\.mostrarBalaoFavoritosStatus/, 'busca/ranking deve manter wrapper publico para mostrar status');
  assert.match(buscaRanking, /function resolverAcaoBalaoFavoritos\(resolve, valor, botao, opcoes = \{\}\)[\s\S]*favoritosResolverAcaoBalao\(resolve, valor, botao, opcoes\)/, 'perguntas devem delegar o feedback visual ao controlador V2');
  assert.match(buscaRanking, /resolverAcaoBalaoFavoritos\(resolve, null, cancelar, \{[\s\S]*esconder:\s*true/, 'acao Cancelar deve fechar o balao antes de resolver o fluxo');
  assert.match(buscaRanking, /function perguntarLoginAvantProAntesFavoritos[\s\S]*Antes de fazer favoritos, o Avant Pro ja esta logado\?[\s\S]*Nao, abrir login/, 'Fazer favoritos deve perguntar se o Avant Pro ja esta logado antes de iniciar a coleta');
  assert.doesNotMatch(buscaRanking, /function perguntarLoginAvantProAntesFavoritos[\s\S]*estadoPersistido\.storageUsable[\s\S]*Sessao salva do Avant Pro encontrada[\s\S]*return true;/, 'sessao AvantPro persistida nao pode pular a confirmacao de cada nova execucao');
  assert.match(buscaRanking, /function mostrarBotaoContinuarLoginAvantProFavoritos[\s\S]*ml-work-modal-continue-login-favoritos[\s\S]*Continuar favoritos[\s\S]*insertBefore\(botao,\s*fechar\)/, 'login manual deve mostrar botao Continuar favoritos fixo no cabecalho do navegador');
  assert.match(buscaRanking, /function urlEmFluxoAutenticacaoMercadoLivreFavoritos[\s\S]*account-verification[\s\S]*password[\s\S]*totp/, 'tela deve reconhecer email, senha, desafio e TOTP do Mercado Livre');
  assert.match(buscaRanking, /indeterminado:\s*!leituraConfiavel[\s\S]*if \(!estado \|\| estado\.indeterminado\)[\s\S]*return false/, 'falha ou transicao na leitura do login deve bloquear Continuar em vez de liberar a coleta');
  assert.match(buscaRanking, /async function validarLoginMercadoLivreAntesDeContinuarFavoritos[\s\S]*estado\.pendente[\s\S]*return false[\s\S]*await onContinuar\(\)/, 'Continuar favoritos deve aguardar o fim real da autenticacao');
  assert.match(buscaRanking, /const validarEFinalizar = botao => validarLoginMercadoLivreAntesDeContinuarFavoritos\([\s\S]*validarLoginAvantProAntesDeContinuarFavoritos\(\(\) => finalizar\(true\), botao\)[\s\S]*mostrarBotaoContinuarLoginAvantProFavoritos\(validarEFinalizar\)/, 'os botoes de continuar devem validar Mercado Livre e Avant Pro antes de salvar a sessao');
  assert.doesNotMatch(buscaRanking, /mostrarBotaoContinuarLoginAvantProFavoritos\(\(\) => finalizar\(true\)\)|continuar\.addEventListener\('click',\s*\(\) => finalizar\(true\)\)/, 'fluxo manual nao pode confirmar login cegamente');
  assert.match(buscaRanking, /function registrarConfirmacaoUsuarioLoginAvantProFavoritos[\s\S]*saveAvantProStorageSnapshot\('favoritos_usuario_confirmou_login_avant'[\s\S]*function perguntarLoginAvantProAntesFavoritos/, 'confirmacao manual do usuario deve salvar a memoria atual do AvantPro');
  assert.match(buscaRanking, /sim\.addEventListener\('click', async[\s\S]*validarLoginAvantProAntesDeContinuarFavoritos\(\(\) => finalizar\(true\), sim\)/, 'ao clicar Sim, o fluxo deve validar o DOM do AvantPro antes de registrar a sessao');
  assert.match(promocoesEfetivacao, /favoritosResolverAcaoBalao\(resolve,[\s\S]*esconder:\s*true/, 'confirmacao de efetivacao deve fechar/desabilitar visualmente antes do fluxo pesado');
  assert.match(promocoesEfetivacao, /const totalProcessados = lista\.length \+ listaFalhas\.length[\s\S]*Comparacao dos anuncios processados/, 'comparacao de efetivacao deve contar sucessos e falhas processadas');
  assert.match(promocoesEfetivacao, /itensComparacao[\s\S]*tipo:\s*'falha'[\s\S]*Nao feito/, 'comparacao de efetivacao deve incluir linha para anuncio nao feito');
  assert.match(promocoesEfetivacao, /Resultado das alteracoes: \$\{totalProcessados\} processado\(s\)/, 'status de efetivacao deve mostrar total processado');
  assert.match(skuSidebar, /function atualizarStatusFavoritosNoNavegadorMl\(mensagem, ativo\)[\s\S]*FavoritosV2\?\.ui\?\.statusModal\?\.atualizarStatusFavoritosNoNavegadorMl/, 'sku sidebar deve manter wrapper publico para overlay do navegador interno');
  assert.match(skuSidebar, /carregarFavoritosAnunciosSku\(item\.sku,\s*item\.loja,\s*\{[\s\S]*manterRankingSelecionado:\s*true/, 'clicar no card do SKU na aba Favoritos deve preservar o ranking/historico aberto');
  assert.match(skuSidebar, /checkbox\.addEventListener\('click',\s*\(event\)\s*=>\s*\{[\s\S]*event\.stopPropagation\(\)/, 'checkbox do SKU nao deve propagar clique para o card');
  assert.match(skuSidebar, /if \(event\.target === checkbox \|\| checkbox\.contains\(event\.target\)\) return;/, 'label do SKU deve ignorar clique vindo do checkbox');
  assert.match(styles, /\.ml-work-modal-live-status[\s\S]*flex:\s*1 1 auto[\s\S]*overflow:\s*hidden[\s\S]*white-space:\s*nowrap[\s\S]*\.ml-work-modal-live-status::before[\s\S]*content:\s*""/, 'barra de status compacta deve ocupar o espaco livre do cabecalho sem quebrar os controles');
  assert.match(mlBrowser, /Extracao completa sem vendas Avant; tentando leitura rapida dos cards visiveis/, 'extracao completa sem vendas deve cair para a leitura rapida');
  assert.match(mlBrowser, /Extracao completa parcial; tentando leitura rapida dos cards visiveis/, 'extracao completa parcial deve cair para a leitura rapida');
  assert.match(mlBrowser, /__JK_ML_FAST_DOM_MAX[\s\S]*maxFastDom[\s\S]*mesclarAnunciosAvant\(resultadoCompleto\.anuncios,\s*rapido\.anuncios\)[\s\S]*complete_fast_dom_merged/, 'extracao completa e DOM rapido devem ser mesclados ate o limite de coleta');
  assert.ok(mlBrowser.includes('carregar\\\\s+dado?s?\\\\s+avant'), 'clique AvantPro deve reconhecer Carregar dado Avantpro sem plural');
  assert.match(buscaRanking, /maxCliquesAvantColeta[\s\S]*Math\.min\([\s\S]*100[\s\S]*Number\(opcoes\.maxCliquesAvant\) \|\| maxAnunciosColeta/, 'coleta principal deve permitir clicar todos os controles AvantPro da primeira pagina');
  assert.match(buscaRanking, /coletarDadosAvantComRolagem\(\{[\s\S]*maxAnuncios:\s*maxAnunciosColeta[\s\S]*mesclarAnunciosAvant\(anunciosAvant,\s*anunciosRolagem\)/, 'coleta principal deve percorrer a primeira pagina e mesclar os cards carregados por rolagem');
  assert.match(buscaRanking, /\(anunciosAvant\.length \|\| resultadosMlVisiveis\)[\s\S]*coletarDadosAvantComRolagem/, 'rolagem deve tentar coletar quando ha cards visiveis mesmo se a primeira leitura Avant veio vazia');
  assert.match(buscaRanking, /extrairAnunciosWebviewVisivel\(\{[\s\S]*clicarCardsSemDados:\s*false[\s\S]*permitirFerramentasAvant:\s*false[\s\S]*permitirAutoLoginAvant:\s*false/, 'coleta de Favoritos nao deve clicar em cards, ferramentas ou auto-login que abrem Vincular AvantPro');
  assert.match(mlBrowser, /const loteCliquesValor = opcoes\.loteCliques === undefined \? 0[\s\S]*const loteCliques = Math\.max\(0, Math\.min[\s\S]*const deveTentarCliqueAvant = loteCliques > 0 && !cliquesAvantDesligados[\s\S]*acionarCardsAvantProFilaWebview/, 'coleta controlada so deve clicar nos controles Avant quando um lote positivo for autorizado');
  assert.match(mlBrowser, /totalVisiveis > 0 && resumo\.com_dados_avant >= totalVisiveis[\s\S]*totalVisiveis > 0 && resumoPassada\.com_dados_avant >= totalVisiveis/, 'coleta nao deve parar apenas pela quantidade de cards sem dados AvantPro');
  assert.match(mlBrowser, /for \(let passada = 1; passada <= maxPassadas[\s\S]*for \(let index = 0; index < posicoesUnicas\.length[\s\S]*Date\.now\(\) < deadline[\s\S]*loginAvantBloqueado/, 'coleta deve percorrer as posicoes da pagina em ate tres passadas, respeitando prazo e bloqueio de login');
  assert.match(buscaRanking, /const maxPosicoesRolagem[\s\S]*segundoPlano \? 28 : 24[\s\S]*maxPosicoes:\s*maxPosicoesRolagem/, 'coleta por rolagem deve varrer a primeira pagina com cobertura suficiente');
  assert.match(execucao, /async function abrirPrimeiraPesquisaFavoritosAposConfirmacaoAvantPro[\s\S]*obterTermoInicialLoginAvantProFavoritos\(selecionadosLista, quantidadePesquisas\)[\s\S]*coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo\(info, pesquisa, \{[\s\S]*maxAnuncios:\s*limiteAnunciosPrimeiraPesquisa[\s\S]*registrarHistoricoRankingSkuFavoritosImediato\(grupoRanking\)/, 'apos confirmar AvantPro, Favoritos deve abrir, coletar e salvar cada ranking da primeira pagina');
  assert.match(execucao, /Ranking salvo[\s\S]*rotina antiga de coleta continua desligada/, 'coleta nova deve salvar ranking sem religar a rotina antiga');
  assert.match(execucao, /function montarResumoPesquisaFavoritos[\s\S]*com_foto[\s\S]*com_link[\s\S]*com_dados_avant[\s\S]*incompletos/, 'execucao deve calcular resumo de foto, link e dados Avant por pesquisa');
  assert.match(execucao, /function formatarResumoPesquisaFavoritos[\s\S]*\$\{resumo\.visiveis\} visiveis[\s\S]*\$\{resumo\.com_dados_avant \|\| 0\} com Avant[\s\S]*\$\{resumo\.incompletos \|\| 0\} incompletos/, 'resumo da coleta deve mostrar visiveis, dados Avant e incompletos');
  assert.match(execucao, /let normalizadosPesquisa = anuncios\.map[\s\S]*await enriquecerAnunciosFavoritosRanking\(normalizadosPesquisa,\s*contextoEnriquecimentoFavoritos\)[\s\S]*mostrarBalaoFavoritosStatus\(formatarResumoPesquisaFavoritos\(resumoPesquisa\)/, 'cada pesquisa deve enriquecer uma vez no contexto da execucao e exibir resumo antes de seguir');
  assert.match(execucao, /resumo_coleta:\s*resumosColeta/, 'ranking salvo deve carregar os resumos de coleta por pesquisa');
  assert.match(historico, /function formatarResumoColetaFavoritosTela[\s\S]*Pesquisa \$\{pesquisa\} concluida[\s\S]*\$\{comDadosAvant\} com Avant[\s\S]*function criarBlocoResumoColetaFavoritos[\s\S]*ml-favoritos-resumo-coleta/, 'resultado deve mostrar resumo persistente de coleta por pesquisa');
  assert.match(historico, /renderizarFavoritosPesquisaResultados[\s\S]*criarBlocoResumoColetaFavoritos\(grupo\)[\s\S]*head\.appendChild\(resumoColeta\)/, 'card do ranking deve exibir o resumo da coleta');
  assert.match(historicoUi, /criarBlocoResumoColetaFavoritos\(grupo\)[\s\S]*bloco\.appendChild\(resumoColeta\)/, 'historico deve exibir o resumo da coleta salvo');
  assert.match(historico, /function enfileirarSalvamentoHistoricoFavoritosServidor\(lista, opcoes = \{\}\)[\s\S]*Promise\.resolve\(anterior\)[\s\S]*salvarHistoricoFavoritosServidor\(historico, opcoes\)[\s\S]*mlHistoricoFavoritosUltimaPersistenciaPromise = tarefa/, 'salvamentos do historico devem ser serializados e manter a promessa da ultima gravacao');
  assert.match(historico, /async function confirmarSalvamentoHistoricoFavoritosServidor\(idsEsperados = \[\], opcoes = \{\}\)[\s\S]*duracaoEsperadaMs[\s\S]*duracaoDivergente[\s\S]*duracaoGruposDivergente[\s\S]*success:\s*faltantes\.length === 0 && duracaoDivergente\.length === 0 && duracaoGruposDivergente\.length === 0/, 'confirmacao do historico deve verificar IDs e tempo total devolvidos pelo servidor');
  assert.match(historico, /if \(opcoes && opcoes\.imediato\)[\s\S]*return enfileirarSalvamentoHistoricoFavoritosServidor\(historico, opcoes\)/, 'salvamento imediato deve participar da fila confirmavel do historico');
  assert.match(runtime, /let mlFavoritosExecucaoIniciadaEmMs = 0/, 'renderer deve manter o inicio canonico da execucao');
  assert.match(statusModal, /startedAt:\s*inicioExecucao/, 'cronometro do shell deve usar o mesmo inicio do historico');
  assert.match(execucao, /mlFavoritosExecucaoIniciadaEmMs = Date\.now\(\)[\s\S]*duracao_execucao_ms: Math\.max\(0, Date\.now\(\) - mlFavoritosExecucaoIniciadaEmMs\)/, 'fluxo ativo deve medir a duracao desde o inicio real');
  assert.match(execucao, /await finalizarDuracaoExecucaoHistoricosFavoritos[\s\S]*duracaoExecucaoPersistidaMs[\s\S]*finishedAt:/, 'duracao total deve ser calculada no commit, confirmada e usada para congelar o cronometro');
  assert.match(historico, /function atualizarDuracaoExecucaoHistoricosFavoritos[\s\S]*duracao_execucao_ms: duracao[\s\S]*salvarHistoricoFavoritos\(historico, \{ imediato: true \}\)/, 'todos os IDs da execucao devem receber a mesma duracao total');
  assert.match(historico, /function formatarDuracaoExecucaoFavoritos[\s\S]*padStart\(2, '0'\)[\s\S]*Tempo total: \$\{duracaoExecucao\}/, 'lista recente deve formatar e exibir a duracao total');
  assert.match(historicoUi, /formatarDuracaoExecucaoFavoritos\(entrada\.duracao_execucao_ms\)[\s\S]*Tempo total: \$\{duracaoExecucao\}/, 'detalhe do historico deve exibir a duracao total');
  assert.match(favoritosStorageBackend, /def _favoritos_duracao_execucao_ms[\s\S]*entrada_saida\["duracao_execucao_ms"\] = duracao_entrada_ms/, 'backend deve preservar a duracao no payload SQLite');
  const inicioFluxoPosLogin = execucao.indexOf('async function abrirPrimeiraPesquisaFavoritosAposConfirmacaoAvantPro');
  const fimFluxoPosLogin = execucao.indexOf('async function fazerFavoritosSkusSelecionadosRendererAntigo', inicioFluxoPosLogin);
  const fluxoPosLogin = execucao.slice(inicioFluxoPosLogin, fimFluxoPosLogin);
  const indiceConfirmacaoHistorico = fluxoPosLogin.indexOf('await finalizarDuracaoExecucaoHistoricosFavoritos');
  const indiceFechamentoSucesso = fluxoPosLogin.indexOf("fecharNavegadorFavoritosAposColeta('favoritos-coleta-concluida'");
  assert.ok(indiceConfirmacaoHistorico >= 0 && indiceFechamentoSucesso > indiceConfirmacaoHistorico, 'navegador trabalhador so deve fechar com sucesso depois da confirmacao do historico no servidor');
  assert.match(execucao, /function fecharNavegadorFavoritosAposColeta[\s\S]*hideEmbeddedMlBrowser\(\{[\s\S]*destroy:\s*true[\s\S]*preserveAvantProSession:\s*true/, 'fim da coleta deve descarregar BrowserView sobreposto preservando sessao AvantPro');
  assert.match(syncFavoritosRuntime, /runtimeCodeFiles\s*=\s*\[[\s\S]*['"]electron_shell\.html['"]/, 'sincronizador deve incluir o shell que exibe o cronometro');
  assert.match(mlBrowser, /function fecharBalaoResultadosMl[\s\S]*ocultarNavegadorMlShellDefinitivo\(\{[\s\S]*descarregarConteudo:[\s\S]*opcoes\.descarregarConteudo[\s\S]*preserveAvantProSession:/, 'fechamento do modal deve repassar destroy/descarregar para o BrowserView');
  assert.match(execucao, /if \(!avantLoginConfirmadoPeloUsuario\) return;\s*await abrirPrimeiraPesquisaFavoritosAposConfirmacaoAvantPro\(selecionados,\s*quantidade,\s*\{[\s\S]*opcoesPromocao,[\s\S]*usarIaRanking[\s\S]*\}\);\s*return;\s*mlFavoritosEmExecucao = true/, 'fluxo antigo deve chamar a coleta nova e manter a rotina antiga depois de um return obrigatorio');
  assert.match(execucao, /if \(!avantLoginConfirmadoPeloUsuario\) return;\s*await abrirPrimeiraPesquisaFavoritosAposConfirmacaoAvantPro\(selecionados,\s*quantidade,\s*\{[\s\S]*opcoesPromocao,[\s\S]*usarIaRanking[\s\S]*\}\);\s*return;\s*const executarEmBackground = opcoes\.background === true/, 'job de favoritos deve chamar a coleta nova e manter o worker antigo depois de um return obrigatorio');
  assert.match(execucao, /async function iniciarWorkerFavoritosAvantProJob\(opcoes = \{\}\) \{[\s\S]*opcoes\.permitirRotinaAntigaAposLoginAvantPro !== true[\s\S]*pararFavoritosAposConfirmacaoAvantProNovaEtapa/, 'worker antigo deve ficar bloqueado ate religacao expressa');
  assert.doesNotMatch(execucao, /if \(loginAvantAntesDaColeta\)[\s\S]*termoPesquisa:\s*destino\.termo[\s\S]*const limiteAnunciosPrimeiraPesquisa/, 'worker visual nao pode abrir a pesquisa no bloco de preparacao do AvantPro');
  assert.match(execucao, /async function coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo[\s\S]*limiteAnunciosPrimeiraPesquisa[\s\S]*ML_FAVORITOS_COLETA_ANUNCIOS_MAX[\s\S]*coletarPrimeiraPaginaFavoritosControlada\(\{[\s\S]*maxAnuncios:\s*limiteAnunciosPrimeiraPesquisa[\s\S]*tempoLimiteMs:\s*180000[\s\S]*maxPassadas:\s*3[\s\S]*loteCliques:\s*6/, 'primeira pesquisa pos-login deve usar o limite completo, tres passadas e lote controlado de cliques');
  assert.doesNotMatch(execucao, /permitirFallbackSemAvant:\s*true/, 'Fazer favoritos nao deve cair em fallback legado quando o AvantPro pede login');
  assert.doesNotMatch(buscaRanking, /permitirFallbackSemAvant/, 'AvantPro obrigatorio deve voltar para conexao/login, nao fallback silencioso');
  assert.ok(mlBrowser.includes('valorNoTexto(card, /vendas?\\\\s+do\\\\s+produto\\\\s+'), 'leitura rapida deve ler valor inline de Vendas do produto');
  assert.match(buscaRanking, /const permitirComplementoProdutoAvant = !mlFavoritosEmExecucao/, 'Fazer favoritos nao deve abrir paginas individuais para complemento');
  assert.match(buscaRanking, /function filtrarAnunciosFavoritosComDadosAvant/, 'ranking deve ter filtro para anuncios com dados ricos do AvantPro');
  assert.match(execucao, /function prepararAnunciosFavoritosRankingComDadosAvant[\s\S]*filtrarAnunciosFavoritosComDadosAvant\(todos\)[\s\S]*const baseRanking = todos[\s\S]*removidosPorDadosAvant:\s*0/, 'ranking deve considerar todos os anuncios validos e nao descartar quem ainda nao tem dados AvantPro');
  assert.match(execucao, /IA verifica anuncios fora do produto/, 'IA do Fazer favoritos nao deve anunciar limite de 8 confirmados');
  assert.match(buscaRanking, /IA verificando anuncios fora do produto sem limitar o ranking[\s\S]*confirmados\.push\(anuncio\)/, 'IA nao deve limitar o ranking aos ids confirmados');
  assert.doesNotMatch(execucao, /Salvando os anuncios encontrados pela pesquisa|usouFallbackSemDadosAvant|fallback_sem_dados_avant/, 'execucao nao deve salvar ranking fallback sem dados AvantPro');
  assert.match(historico, /if \(!entrada\.grupos\.length\) return null/, 'historico nao deve salvar ranking vazio');
  assert.match(urlUtils, /TRACKING_PARAMS[\s\S]*'loader'[\s\S]*function normalizarUrlMercadoLivreParaComparacao[\s\S]*searchParams\.delete/, 'normalizador deve remover loader e parametros de rastreamento');
  assert.match(shellBridgeV2, /set\(url\)[\s\S]*const mesmaPesquisa = areUrlsEquivalent[\s\S]*if \(mesmaPesquisa && proxy\.__visible && !segundoPlano\)[\s\S]*return;/, 'renderer deve ignorar URL equivalente ao decidir reenviar a mesma pesquisa');
  assert.match(ipc, /normalizeComparableUrl\(currentUrl\) !== normalizeComparableUrl\(url\)[\s\S]*loadURL\(url\)/, 'navegador interno nao deve recarregar URL equivalente automaticamente');
  assert.match(windowModule, /function normalizeComparableUrl[\s\S]*searchParams\.delete\('loader'\)[\s\S]*pathname = url\.pathname\.replace/, 'Electron deve comparar URLs do Mercado Livre ignorando loader=true');
  assert.match(ipc, /embedded-ml-browser-avant-monitoring-disabled/, 'abrir navegador interno nao deve diagnosticar AvantPro automaticamente');
  assert.doesNotMatch(ipc, /garantirAvantProWebContents\(view\.webContents/, 'IPC nao deve monitorar a pagina automaticamente ao exibir o BrowserView');
  assert.match(ipc, /function restaurarSessaoAvantProAntesDeAbrirNavegador[\s\S]*ensureAvantProExtensionStorageFromSnapshot/, 'IPC deve ter restauracao da sessao AvantPro a partir da ultima sessao boa');
  assert.match(ipc, /before-embedded-ml-browser-show[\s\S]*await ensureChromeExtensionsForMlSession\(\)/, 'abrir navegador interno deve restaurar a ultima sessao boa do AvantPro antes de carregar extensoes');
  assert.match(ipc, /function salvarSessaoAvantProAntesDeOcultarNavegador[\s\S]*saveAvantProExtensionStorageSnapshot[\s\S]*embedded-ml-browser-hide[\s\S]*preserveAvantProSession[\s\S]*hideEmbeddedMlBrowser\(hideOptions\)/, 'fechar navegador interno deve salvar a sessao AvantPro antes de ocultar ou destruir');
  assert.match(mlBrowser, /function ocultarNavegadorMlShellDefinitivo\(opcoes = \{\}\)[\s\S]*favoritosBrowserShellBridge\?\.ocultarDefinitivo\(opcoes\)/, 'fachada deve delegar o fechamento ao shell V2');
  assert.match(shellBridgeV2, /function ocultarDefinitivo\(opcoes = \{\}\)[\s\S]*preserveAvantProSession:\s*opcoes\.preserveAvantProSession !== false[\s\S]*enviar\('jk-ml-browser-hide', payload\)/, 'fechar o modulo Favoritos deve pedir preservacao da sessao AvantPro');
  assert.match(shell, /const hideOptions = \{[\s\S]*\.\.\.\(payload \|\| \{\}\)[\s\S]*hideEmbeddedMlBrowserShell\(hideOptions\)/, 'shell deve repassar a opcao de preservar sessao AvantPro ao Electron');
  assert.match(mlBrowser, /const ML_FAVORITOS_MONITORAMENTO_PAGINA_AUTOMATICO = false/, 'monitoramento automatico da pagina deve ficar desligado por padrao');
  assert.match(mlBrowser, /function aguardarAvantProNoWebview[\s\S]*statusMonitoramentoPaginaFavoritosDesativado/, 'aguardo do AvantPro deve recusar chamadas automaticas sem acao do usuario');
  assert.match(buscaRanking, /mostrarControles\(status\);\s*\}\);\s*\}/, 'conexao AvantPro deve parar nos botoes manuais, sem verificacao automatica');
  assert.doesNotMatch(buscaRanking, /setInterval\(|const verificarAutomaticamente|setTimeout\(\(\) => abrirLogin|setTimeout\(\(\) => verificarAutomaticamente/, 'conexao AvantPro nao deve usar timer de monitoramento ou abrir login sozinha');
  assert.match(renderAvantMercadoLivre, /function agendarAtualizacaoAvantAutomatica[\s\S]*mlAvantAutoRunId \+= 1[\s\S]*agendado:\s*false[\s\S]*desativado:\s*true/, 'atualizacao automatica antiga do Avant deve permanecer como no-op bloqueado');
  assert.match(renderAvantMercadoLivre, /if \(typeof monitoramentoPaginaFavoritosAutomaticoAtivo === 'function' && monitoramentoPaginaFavoritosAutomaticoAtivo\(\)\) \{[\s\S]*enriquecerDatasCriacaoAnuncios/, 'enriquecimento pos-render so pode rodar se o monitoramento for religado explicitamente');
  assert.match(windowModule, /function destroyEmbeddedMlBrowser[\s\S]*webContents\.destroy\(\)/, 'BrowserView do ML precisa ser destruido no fechamento definitivo');
  assert.match(ipc, /embedded-ml-browser-hide[\s\S]*hideEmbeddedMlBrowser\(hideOptions/, 'IPC deve aceitar opcoes para descarregar o BrowserView');
  assert.match(ipc, /function buildMlProductUrlFromItemId[\s\S]*function pickMlItemImage[\s\S]*ml-public-item-info[\s\S]*url:\s*permalink,[\s\S]*thumbnail:\s*imagem,[\s\S]*pictures:\s*Array\.isArray\(item\.pictures\)/, 'IPC do item publico ML deve devolver link, foto e pictures para enriquecer o ranking');
  assert.match(shell, /jk-ml-browser-hide[\s\S]*const hideOptions = \{[\s\S]*hideOptions\.destroy = true[\s\S]*hideEmbeddedMlBrowserShell\(hideOptions\)/, 'shell deve repassar hide definitivo para descarregar o navegador');
  assert.match(execucao, /function pararNavegadorFavoritosBackground\(opcoes = \{\}\)[\s\S]*hideEmbeddedMlBrowser\(\{ destroy:\s*true, reason \}\)/, 'parada do navegador background deve descarregar o BrowserView com motivo terminal');

  const textoLogado = 'Informacoes Avantpro Marca Honda Vendas do produto 100 Participacao 32,1% Vendas estimadas 155 Ritmo atual vendas mes 6 Visitas do anuncio 0 Nome do vendedor UAI';
  const textoLogin = 'Comece a usar o Avantpro Entre na sua conta para liberar os recursos da extensao Login Nao possui uma conta Crie uma aqui Tutoriais';
  assert.equal(textoPareceAvantLogado(textoLogado), true, 'diagnostico deve reconhecer painel AvantPro com dados reais');
  assert.equal(textoPareceLoginAvant(textoLogin), true, 'diagnostico deve reconhecer tela de login/vinculacao do AvantPro');
}

function validarColetaCanonicaEManifestoFavoritos() {
  const repoRoot = process.cwd();
  const mlBrowserCore = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'ml-browser.js'), 'utf8');
  const urlUtils = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'v2', 'browser', 'url-utils.js'), 'utf8');
  const shellBridgeV2 = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'v2', 'browser', 'shell-bridge.js'), 'utf8');
  const avantCacheV2 = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'v2', 'browser', 'avant-cache.js'), 'utf8');
  const mlBrowser = [urlUtils, shellBridgeV2, avantCacheV2, mlBrowserCore].join('\n');
  const buscaRanking = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'tabelas-layout', '04-promocoes-busca-ranking.js'), 'utf8');
  const execucao = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'tabelas-layout', '07-execucao-render-layout.js'), 'utf8');
  const historico = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'tabelas-layout', '05-resultados-historico.js'), 'utf8');
  const historicoUi = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'tabelas-layout', '06-ranking-manual-historico-ui.js'), 'utf8');
  const mlBaseBusca = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'tabelas-layout', '01-ml-base-busca.js'), 'utf8');
  const renderAvantMercadoLivre = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'tabelas-layout', '08-render-avant-mercadolivre.js'), 'utf8');
  const favoritosHtml = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos.html'), 'utf8');
  const tabelasLayout = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'tabelas-layout.js'), 'utf8');
  const assetManifest = JSON.parse(fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'asset-manifest.json'), 'utf8'));

  assert.match(mlBrowser, /function normalizarMlbFavoritosCanonico/, 'coleta deve normalizar MLB como identidade principal');
  assert.match(mlBrowser, /function limparLinkProdutoMercadoLivreFavoritos/, 'coleta deve limpar links antes de usar como chave');
  assert.match(mlBrowser, /function chaveCanonicaAnuncioFavoritos[\s\S]*mlb:[\s\S]*link:/, 'chave canonica deve usar MLB ou link limpo');
  assert.doesNotMatch(mlBrowser, /return `titulo:|return titulo \? 'titulo:|if \(item && item\.titulo\) return `titulo:/, 'merge/coleta nao podem usar titulo como chave');

  assert.match(mlBrowser, /tituloFracoFavoritosCanonico[\s\S]*\^jm\$[\s\S]*mlb\\d\+/, 'valores fracos como JM e MLB isolado devem ser rejeitados como titulo');
  assert.match(mlBrowser, /prepararAnuncioMercadoLivreCanonico[\s\S]*tituloFonte[\s\S]*fotoFonte[\s\S]*linkFonte[\s\S]*precoFonte/, 'base ML deve guardar fonte dos campos principais');
  assert.match(mlBrowser, /extrairCardsMercadoLivreBasicoWebview[\s\S]*preco_original[\s\S]*preco_promocional[\s\S]*moeda[\s\S]*mercado_livre_dom/, 'base ML deve extrair preco estruturado e origem DOM');
  assert.match(mlBrowser, /completarBaseMercadoLivreComApiFavoritos[\s\S]*consultarItemApiMercadoLivre[\s\S]*concorrencia:\s*4/, 'fallback API ML deve existir com concorrencia baixa');

  assert.match(mlBrowser, /function mesclarAnunciosAvant/, 'merge seguro deve existir');
  assert.match(mlBrowser, /sem_match_mlb_link/, 'dados Avant com MLB\/link divergente devem ser marcados como nao vinculados');
  assert.match(mlBrowser, /sem_base_mercado_livre[\s\S]*__avantNaoVinculado|__avantNaoVinculado[\s\S]*sem_base_mercado_livre/, 'dados Avant sem base ML devem ir para nao vinculados');
  assert.match(mlBrowser, /function mesclarAnunciosAvant[\s\S]*copiarPrecoSeguro[\s\S]*fonteMl\(item\)/, 'Avant nao deve sobrescrever preco confiavel do Mercado Livre');
  assert.match(mlBrowser, /similaridadeTitulosFavoritosCanonico[\s\S]*titulo_divergente_mesmo_mlb_link/, 'merge deve marcar suspeito quando titulo divergir fortemente');
  assert.match(mlBrowser, /classificarQualidadeAnuncioFavoritosCanonico[\s\S]*completo[\s\S]*incompleto[\s\S]*suspeito/, 'anuncios devem receber estado de qualidade');
  assert.match(mlBrowser, /var capturarPainelAvantParaCard[\s\S]*__JK_AVANT_CARD_DATA_CACHE[\s\S]*cache\[canonical\]/, 'clique do Avant deve cachear dados do painel pelo MLB/link do card clicado');
  assert.match(mlBrowser, /origem_dados:\s*'avantpro_card_panel_cache'/, 'cache do painel Avant deve marcar a origem dos dados capturados');
  assert.match(mlBrowser, /var cachePainel = window\.__JK_AVANT_CARD_DATA_CACHE[\s\S]*cached\.chave_canonica = key[\s\S]*rows\.forEach/, 'extracao final do Avant deve ler o cache card->painel antes da heuristica visual');
  assert.match(mlBrowser, /function extrairAnunciosAvantProDomWebview[\s\S]*rootGenericoDemais[\s\S]*rootProdutoSeguro[\s\S]*nearestProductLink/, 'painel Avant flutuante deve ser vinculado pelo link visual mais proximo, sem usar body como card');
  assert.match(mlBrowser, /const avantCacheFinal = await aguardarCancelavel\(extrairCacheAvantProCardsWebview[\s\S]*const avantDomFinal = await aguardarCancelavel\(extrairAnunciosAvantProDomWebview[\s\S]*mesclarAnunciosAvant\(anuncios,\s*\(avantCacheFinal[\s\S]*mesclarAnunciosAvant\(anuncios,\s*\(avantDomFinal/, 'fechamento da coleta deve mesclar cache de cliques e DOM global do Avant sem perder cancelamento');
  assert.match(mlBrowser, /const avantDomDepois = await aguardarCancelavel\(extrairAnunciosAvantProDomWebview[\s\S]*mesclarAnunciosAvant\(anuncios,\s*\(avantDomDepois/, 'apos cliques Avant, coleta deve ler o painel DOM global antes de seguir');
  assert.match(mlBrowser, /async function extrairBaseMercadoLivreEmergencialWebview[\s\S]*extrairAnunciosWebviewVisivel[\s\S]*clicarAvant:\s*false[\s\S]*mercado_livre_dom_emergencial/, 'coleta deve ter fallback emergencial de leitura ML sem acionar Avant ou fluxo antigo');
  assert.match(mlBrowser, /if \(!anuncios\.length\) \{[\s\S]*const emergenciaFinal = await aguardarCancelavel\(extrairBaseMercadoLivreEmergencialWebview[\s\S]*mesclarAnunciosAvant\(emergenciaFinal\.anuncios,\s*anuncios\)/, 'fechamento da coleta deve tentar fallback emergencial antes de retornar ranking vazio');

  assert.match(execucao, /coletarPrimeiraPaginaFavoritosControlada[\s\S]*maxPassadas:\s*3[\s\S]*loteCliques:\s*6/, 'execucao deve usar coleta controlada da primeira pagina com fila segura de cliques Avant');
  assert.match(mlBrowser, /const loteCliquesValor = opcoes\.loteCliques === undefined \? 0/, 'coleta controlada deve manter cliques Avant desligados por padrao');
  assert.match(mlBrowser, /__JK_FAVORITOS_DOWNLOAD_GUARD_REGISTERED[\s\S]*event\.stopImmediatePropagation/, 'coleta deve bloquear downloads do Avant no webview');
  assert.match(mlBrowser, /var cardMaisProximoFila[\s\S]*var cardsSelector = queryAllDeep\(cardSelectors\)[\s\S]*cardsSelector\.forEach[\s\S]*var anchorsProduto = queryAllDeep\('a\[href\]'\)[\s\S]*incluirCard\(cardMaisProximoFila\(anchor\)\)/, 'fila Avant deve montar candidatos pelos mesmos cards/links da base ML');
  assert.match(mlBrowser, /var cardsPorKey = \{\}[\s\S]*duplicateKeys[\s\S]*candidateKeys: Object\.keys\(cardsPorKey\)/, 'fila Avant deve deduplicar candidatos por MLB/link antes de clicar');
  assert.match(mlBrowser, /var textoExplicitoClique[\s\S]*return \/informacoes\?\\\\s\+avant\|informacoes\?\\\\s\+avantpro\|avantpro\\\\s\+info\|carregar\\\\s\+dados\\\\s\+avant[\s\S]*\/\.test\(explicito\)/, 'fila Avant deve clicar apenas nos controles explicitos de Informacoes ou Carregar dados Avantpro');
  assert.match(mlBrowser, /post-purchase/, 'extrator deve bloquear links internos como post-purchase/post-sales');
  assert.doesNotMatch(mlBrowser, /mercadolivre\\\\\.com\\\\\.br\\\\\/\[\^\?\#\/\]\{12,\}/, 'extrator nao deve aceitar slug generico do Mercado Livre como produto');
  assert.match(execucao, /com_titulo[\s\S]*com_preco[\s\S]*suspeitos[\s\S]*avant_nao_vinculado/, 'resumo da execucao deve expor qualidade da coleta');
  assert.match(execucao, /function montarAuditoriaPesquisaFavoritos[\s\S]*motivos_incompletos[\s\S]*origens_dados[\s\S]*amostras_incompletos/, 'resumo da coleta deve guardar motivos, origens e amostras dos incompletos');
  assert.match(execucao, /Pesquisa \$\{resumo\.pesquisa\} concluida[\s\S]*com titulo[\s\S]*com preco[\s\S]*suspeitos/, 'mensagem final deve mostrar titulo, preco e suspeitos');
  assert.match(execucao, /formatarMotivosIncompletosFavoritos[\s\S]*faltas:/, 'status da coleta deve exibir os principais motivos de incompletos');
  assert.match(execucao, /function formatarResumoFalhaRankingFavoritos[\s\S]*formatarResumoPesquisaFavoritos[\s\S]*nenhum anuncio entrou no ranking/, 'falha sem ranking deve exibir resumo real da coleta');
  assert.match(mlBrowser, /async function coletarPrimeiraPaginaFavoritosControlada[\s\S]*__JK_AVANT_CARD_QUEUE_CLICKED_KEYS = \{\}[\s\S]*__JK_AVANT_CARD_DATA_CACHE = \{\}/, 'cada coleta controlada deve reiniciar cache/fila do Avant no webview');
  const blocoAvulso = execucao.match(/async function rankearAvulsoMercadoLivre[\s\S]*?function formatarPrecoFavoritosMl/);
  assert.ok(blocoAvulso, 'fluxo de rankeamento avulso deve estar presente');
  assert.match(blocoAvulso[0], /coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo[\s\S]*fonte_coleta:\s*'avantpro_primeira_pagina_nova'[\s\S]*resumo_coleta:\s*resumosColeta/, 'rankeamento avulso deve usar a coleta nova da primeira pagina');
  assert.doesNotMatch(blocoAvulso[0], /buscarAnunciosFavoritosPorTermo\(/, 'rankeamento avulso nao pode chamar o coletor antigo');
  assert.match(historico, /com_titulo[\s\S]*com_preco[\s\S]*avant_nao_vinculado/, 'historico deve renderizar o novo resumo de qualidade');
  assert.match(historico, /function anuncioHistoricoPayload[\s\S]*vendedorFonte[\s\S]*dataCriacaoFonte[\s\S]*media_mensal[\s\S]*media_mensal_fonte/, 'historico deve preservar fontes e media mensal do AvantPro');
  assert.match(historico, /resumo_coleta:\s*Array\.isArray[\s\S]*motivos_incompletos[\s\S]*origens_dados[\s\S]*amostras_incompletos/, 'historico deve persistir auditoria de coleta por pesquisa');
  assert.match(historico, /formatarResumoColetaFavoritosTela[\s\S]*faltas:/, 'historico deve exibir motivos dos incompletos no resumo da coleta');
  assert.match(historicoUi, /<th>Preco<\/th>[\s\S]*criarCelulaPrecoHistoricoFavoritos|<th>Preco<\/th>[\s\S]*criarCelulaPrecoAnuncioFavoritos/, 'tabela de ranking/historico deve incluir preco');

  assert.match(buscaRanking, /function chaveAnuncioFavoritos[\s\S]*chaveCanonicaAnuncioFavoritos/, 'ranking deve deduplicar pela chave canonica');
  assert.match(buscaRanking, /function anuncioFavoritosCandidatoRanking[\s\S]*return !!link/, 'ranking nao deve aceitar item sem MLB nem link');
  assert.match(buscaRanking, /tituloFonte[\s\S]*fotoFonte[\s\S]*linkFonte[\s\S]*precoFonte[\s\S]*dataCriacaoFonte/, 'ranking deve preservar fontes dos campos');
  assert.match(mlBaseBusca, /const ML_API_WORKERS = 4/, 'API publica ML deve usar concorrencia baixa');
  assert.match(mlBaseBusca, /tituloFonte[\s\S]*linkFonte[\s\S]*fotoFonte[\s\S]*precoFonte/, 'fallback API deve preencher fontes dos campos');

  assert.doesNotMatch(mlBrowser + buscaRanking + execucao, /maxCliquesAvant:\s*100|sempreClicarAvant|forcarCliqueAvant|atualizarDadosAvantAutomaticamente/, 'residuos antigos agressivos devem ficar fora do fluxo');
  assert.doesNotMatch(renderAvantMercadoLivre, /atualizarDadosAvantAutomaticamente/, 'atualizacao automatica antiga nao deve existir com o nome publico');
  assert.match(renderAvantMercadoLivre, /function agendarAtualizacaoAvantAutomatica[\s\S]*desativado:\s*true/, 'agendador antigo deve ser no-op seguro');
  assert.match(favoritosHtml, /\/favoritos\/v2\/ui\/status-modal\.js[\s\S]*\/favoritos\/v2\/browser\/url-utils\.js[\s\S]*\/favoritos\/v2\/browser\/shell-bridge\.js[\s\S]*\/favoritos\/v2\/browser\/avant-cache\.js[\s\S]*\/favoritos\/ml-browser\.js/, 'HTML deve carregar V2 do browser antes da fachada ml-browser');
  const assetVersion = String(assetManifest.version || '');
  assert.match(assetVersion, /^\d{8}-favoritos-[a-z0-9-]+-v\d+$/, 'manifesto deve possuir versao unica de assets');
  const htmlVersions = Array.from(favoritosHtml.matchAll(/\/(?:favoritos\/[^"']+)\?v=([^"']+)/g), match => match[1]);
  assert.ok(htmlVersions.length >= 10, 'HTML deve versionar os assets do Favoritos');
  assert.ok(htmlVersions.every(version => version === assetVersion), 'todos os assets do Favoritos devem usar a versao do manifesto');
  assert.match(favoritosHtml, /planilhas-colar-historico\.js\?v=/, 'HTML servido deve carregar o modulo de colar historico na planilha');
  assert.match(tabelasLayout, new RegExp(`const VERSION = ['"]${assetVersion}['"]`), 'loader das tabelas deve usar a versao do manifesto');
  (assetManifest.assets || []).forEach(asset => {
    assert.ok(fs.existsSync(path.join(repoRoot, 'static', asset)), `asset obrigatorio ausente: ${asset}`);
  });
}

function run() {
  const casosVendas = [
    ['12', 12],
    ['1,2k', 1200],
    ['1.2k', 1200],
    ['15 mil', 15000],
    ['12.345', 12345],
    ['1.234,56', 1235],
    ['0', 0],
    ['', null],
    [null, null],
    [undefined, null],
    ['abc', null]
  ];
  casosVendas.forEach(([entrada, esperado], index) => {
    assert.equal(parseVendas(entrada), esperado, `parseVendas #${index + 1}`);
  });
  assert.equal(deveAtualizarVendas(null, '', 1200, 'api'), false, 'vendas API nao devem preencher valor exato');
  assert.equal(deveAtualizarVendas(null, '', '1.2k', 'avantpro_card'), false, 'fonte antiga de card nao deve preencher vendas');
  assert.equal(deveAtualizarVendas(null, '', '12', 'avantpro_anuncio'), true, 'vendas do anuncio AvantPro devem preencher valor');
  assert.equal(deveAtualizarVendas(null, '', '12', 'avantpro_fast_dom'), true, 'vendas do DOM rapido AvantPro devem preencher valor');
  assert.equal(deveAtualizarVendas(9, 'api_search', 4, 'avantpro_fast_dom'), true, 'DOM rapido AvantPro deve sobrescrever origem publica');
  assert.equal(deveAtualizarVendas(1200, 'avantpro_card', 9, 'api'), false, 'API nao deve sobrescrever AvantPro');
  assert.equal(deveAtualizarVendas(1200, 'api_search', 1300, 'avantpro_anuncio'), true, 'AvantPro do anuncio deve sobrescrever origem publica');
  assert.equal(deveAtualizarVendas(null, '', 1300, 'avantpro_produto'), false, 'vendas do produto/catalogo nao devem entrar como vendas do anuncio');
  assert.equal(vendasAvantAnuncioDoTexto('Vendas do produto: 100'), null, 'texto de produto nao pode virar venda do anuncio');
  assert.equal(vendasAvantAnuncioDoTexto('Vendas do catalogo: 100 Vendas do anuncio: 7'), 7, 'deve priorizar somente o rotulo de anuncio');
  assert.equal(vendasAvantAnuncioDoTexto('Vendas deste anuncio = 1,2k'), 1200, 'deve ler rotulo explicito deste anuncio');
  assert.equal(deveAtualizarVendedor('Loja API Exata', 'api', 'Loja Vizinha Avant', 'avantpro_card'), false, 'vendedor do card nao sobrescreve API');
  assert.equal(deveAtualizarVendedor('Loja Vizinha Avant', 'avantpro_card', 'Loja API Exata', 'api'), true, 'API sobrescreve vendedor fraco do card');
  assert.equal(deveAtualizarVendedor('MULTECCOO', 'mercado_livre_api', 'ESTILO VARIEDADES', 'avantpro_dom'), false, 'Avant DOM nao deve sobrescrever vendedor do Mercado Livre');
  assert.equal(deveAtualizarVendedor('ESTILO VARIEDADES', 'avantpro_dom', 'MULTECCOO', 'mercado_livre_api'), true, 'Mercado Livre deve corrigir vendedor vindo do Avant');

  const infoComNickname = montarInfoItem({
    id: 'MLB12345678',
    title: 'Produto',
    seller: { id: 987 },
    sold_quantity: '3,5k'
  }, {
    nickname: 'João Silva',
    official_store_name: 'Nome alternativo'
  });
  assert.equal(infoComNickname.vendedor, 'João Silva');
  assert.equal(infoComNickname.vendas, 3500);

  const infoSemUsuario = montarInfoItem({
    id: 'MLB12345679',
    title: 'Produto B',
    seller: { name: 'Oficial Loja', id: 111 },
    sold: 0
  });
  assert.equal(infoSemUsuario.vendedor, 'Oficial Loja');
  assert.equal(infoSemUsuario.vendas, 0);

  const infoComFallbackLojas = montarInfoItem({
    id: 'MLB12345680',
    title: 'Produto C',
    seller: { nickname: 'SellerAPI', id: 111 },
    sold: 9
  }, {
    official_store_name: 'Loja Oficial',
    official_store: { name: 'Loja Fallback' }
  });
  assert.equal(infoComFallbackLojas.vendedor, 'Loja Oficial');

  const merge = mesclarAnunciosAvant(
    [{ id: 'MLB12345678', vendedor: 'Loja Base', data_criacao: '2024', vendas: 5 }],
    [{ id: 'MLB12345678', vendedor: '', data_criacao: '', vendas: 0 }]
  );
  assert.equal(merge[0].vendas, 0);

  const mergeSemAlterarComNulo = mesclarAnunciosAvant(
    [{ id: 'MLB24681357', vendedor: 'Loja Ouro', data_criacao: '2024', vendas: 0 }],
    [{ id: 'MLB24681357', vendedor: '', data_criacao: '', vendas: null }]
  );
  assert.equal(mergeSemAlterarComNulo[0].vendas, 0);
  assert.equal(mergeSemAlterarComNulo[0].vendedor, 'Loja Ouro');

  const mergeFallback = mesclarAnunciosAvant(
    [{ id: 'MLB87654321', vendedor: 'Loja X', data_criacao: '', vendas: null }],
    [{ id: 'MLB87654321', vendedor: '', data_criacao: '2025', vendas: null }]
  );
  assert.equal(mergeFallback[0].vendedor, 'Loja X');
  assert.equal(mergeFallback[0].data_criacao, '2025');

  const mergeComRuidoVendedor = mesclarAnunciosAvant(
    [{ id: 'MLB90112233', vendedor: 'Loja Real', data_criacao: '2024', vendas: 10 }],
    [{ id: 'MLB90112233', vendedor: 'Anuncio criado em 2024', data_criacao: '', vendas: 11 }]
  );
  assert.equal(mergeComRuidoVendedor[0].vendedor, 'Loja Real');
  assert.equal(mergeComRuidoVendedor[0].vendas, 11);

  const anuncioMapeado = { id: 'MLB12345678', url: 'https://produto.mercadolivre.com.br/MLB-12345678-produto' };
  assert.strictEqual(
    encontrarAnuncioEnriquecido([anuncioMapeado], { id: '', url: 'https://produto.mercadolivre.com.br/MLB-12345678-produto#reloaded' }),
    anuncioMapeado,
    'enriquecimento deve localizar o mesmo anuncio por MLB mesmo quando a URL muda'
  );

  const scriptFiles = [
    path.join(process.cwd(), 'favoritos.html'),
    path.join(process.cwd(), 'static', 'favoritos.html'),
    path.join(process.cwd(), 'pesquisa_ml.html'),
    path.join(process.cwd(), 'static', 'pesquisa_ml.html')
  ];
  scriptFiles.forEach(compilarScriptsInline);
  validarFluxoFavoritosAvantProSemReload();
  validarColetaCanonicaEManifestoFavoritos();
  validarLocalizadorBolinhaAvantPro();
  validarLocalizadorFerramentasAvantPro();

  console.log('✅ Testes externos de validação de favoritos concluídos com sucesso.');
}

try {
  run();
} catch (err) {
  console.error('❌ Teste externo falhou:', err && err.message ? err.message : err);
  process.exitCode = 1;
}
