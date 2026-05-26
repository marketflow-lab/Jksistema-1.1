const assert = require('assert');
const fs = require('fs');
const path = require('path');

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
  return valor === 'avantpro_anuncio' || valor === 'avantpro_dom';
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
    api: 6,
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
  assert.equal(deveAtualizarVendas(1200, 'avantpro_card', 9, 'api'), false, 'API nao deve sobrescrever AvantPro');
  assert.equal(deveAtualizarVendas(1200, 'api_search', 1300, 'avantpro_anuncio'), true, 'AvantPro do anuncio deve sobrescrever origem publica');
  assert.equal(deveAtualizarVendas(null, '', 1300, 'avantpro_produto'), false, 'vendas do produto/catalogo nao devem entrar como vendas do anuncio');
  assert.equal(vendasAvantAnuncioDoTexto('Vendas do produto: 100'), null, 'texto de produto nao pode virar venda do anuncio');
  assert.equal(vendasAvantAnuncioDoTexto('Vendas do catalogo: 100 Vendas do anuncio: 7'), 7, 'deve priorizar somente o rotulo de anuncio');
  assert.equal(vendasAvantAnuncioDoTexto('Vendas deste anúncio = 1,2k'), 1200, 'deve ler rotulo explicito deste anuncio');
  assert.equal(deveAtualizarVendedor('Loja API Exata', 'api', 'Loja Vizinha Avant', 'avantpro_card'), false, 'vendedor do card nao sobrescreve API');
  assert.equal(deveAtualizarVendedor('Loja Vizinha Avant', 'avantpro_card', 'Loja API Exata', 'api'), true, 'API sobrescreve vendedor fraco do card');

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

  console.log('✅ Testes externos de validação de favoritos concluídos com sucesso.');
}

try {
  run();
} catch (err) {
  console.error('❌ Teste externo falhou:', err && err.message ? err.message : err);
  process.exitCode = 1;
}
