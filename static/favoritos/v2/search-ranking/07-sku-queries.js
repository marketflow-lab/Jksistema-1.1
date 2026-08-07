(function (global) {
  'use strict';

  const searchRanking = global.FavoritosV2.searchRanking;
  const internal = searchRanking.internal;
  if (internal.components.has('07-sku-queries')) return;

  function erroColetaMercadoLivreFavoritos(...args) {
    const implementation = internal.erroColetaMercadoLivreFavoritos;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: erroColetaMercadoLivreFavoritos');
    return implementation(...args);
  }

  function obterCadastroSkuFavoritos(sku, loja = '') {
      const chave = skuChaveSku(sku);
      if (!chave || !Array.isArray(skuDados)) return null;
      const candidatos = skuDados.filter(row => skuChaveSku(skuObterSku(row)) === chave);
      if (!candidatos.length) return null;
      const lojaAlvo = skuNormalizarLoja(loja || favoritosLojaSelecionadaParaApi());
      const temPesquisa = (row) => [1, 2, 3].some(numero => String(skuObterPesquisa(row, numero) || '').trim());
      const candidatoLoja = candidatos.find(row => lojaAlvo && skuNormalizarLoja(skuObterLoja(row)) === lojaAlvo) || null;
      return (candidatoLoja && temPesquisa(candidatoLoja))
          ? candidatoLoja
          : (candidatos.find(temPesquisa) || candidatoLoja || candidatos[0]);
  }

  function normalizarTermoPesquisaFavoritos(termo) {
      return String(termo || '').replace(/[.,/]+/g, ' ').replace(/\s+/g, ' ').trim();
  }

  function montarPesquisasFavoritosSku(item, quantidade) {
      const cadastro = obterCadastroSkuFavoritos(item.sku, item.loja);
      const descricaoCadastro = cadastro
          ? String(cadastro.descricao_ml || cadastro.descricao || cadastro['descrição'] || cadastro.description || '').trim()
          : '';
      const termos = [];
      for (let numero = 1; numero <= quantidade; numero += 1) {
          const termo = normalizarTermoPesquisaFavoritos(cadastro ? skuObterPesquisa(cadastro, numero) : '');
          if (!termo) continue;
          if (!termos.some(t => t.termo.toLowerCase() === termo.toLowerCase())) {
              termos.push({ campo: numero, termo });
          }
      }
      return {
          sku: item.sku,
          loja: item.loja || (cadastro && skuObterLoja(cadastro)) || favoritosLojaSelecionadaParaApi(),
          titulo: (cadastro && skuObterProduto(cadastro)) || item.titulo || '',
          descricao: descricaoCadastro,
          cadastro,
          termos
      };
  }

  function validarAnunciosFavoritosPertencemAoTermo(termo, anuncios) {
      const tokens = normalizarTextoMl(termo)
          .split(' ')
          .filter(token => token.length >= 3)
          .slice(0, 6);
      if (!tokens.length || !Array.isArray(anuncios) || anuncios.length < 3) return true;
      const amostra = anuncios.slice(0, 14).map(anuncio => normalizarTextoMl([
          anuncio && anuncio.titulo,
          anuncio && anuncio.title,
          anuncio && anuncio.url
      ].filter(Boolean).join(' ')));
      const correspondentes = amostra.filter(texto => tokens.some(token => texto.includes(token))).length;
      if (correspondentes > 0 || amostra.length < 3) return true;
      throw erroColetaMercadoLivreFavoritos(`Os anuncios coletados nao correspondem a pesquisa "${termo}". Reabra a busca no navegador interno e tente novamente.`);
  }

  function filtrarAnunciosFavoritosComDadosAvant(anuncios) {
      return (Array.isArray(anuncios) ? anuncios : []).filter(anuncio => {
          const fonteVendas = normalizarFonte(anuncio && (anuncio.vendasFonte || anuncio.vendas_fonte || anuncio.fonte_vendas || ''));
          return !!(anuncio && (
              fonteVendasConfiavel(fonteVendas)
              || vendedorValido(anuncio.vendedor)
              || anuncio.data_criacao
              || anuncio.cacheAvant
              || anuncio.origem_dados === 'avantpro_fast_dom'
          ));
      });
  }

  function preparacaoAvantPrePesquisaConfirmada(resultado) {
      return !!(resultado && (
          resultado.loginAvantConfirmado === true
          || resultado.confirmed === true
          || (resultado.entrada && resultado.entrada.confirmed === true)
      ));
  }

  Object.assign(internal, {
    obterCadastroSkuFavoritos,
    normalizarTermoPesquisaFavoritos,
    montarPesquisasFavoritosSku,
    validarAnunciosFavoritosPertencemAoTermo,
    filtrarAnunciosFavoritosComDadosAvant,
    preparacaoAvantPrePesquisaConfirmada
  });
  internal.components.add('07-sku-queries');
})(window);
