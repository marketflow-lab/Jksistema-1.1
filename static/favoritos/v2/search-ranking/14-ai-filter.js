(function (global) {
  'use strict';

  const searchRanking = global.FavoritosV2.searchRanking;
  const internal = searchRanking.internal;
  if (internal.components.has('14-ai-filter')) return;

  function mostrarBalaoFavoritosStatus(...args) {
    const implementation = internal.mostrarBalaoFavoritosStatus;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: mostrarBalaoFavoritosStatus');
    return implementation(...args);
  }

  function sinalFavoritosAtual(...args) {
    const implementation = internal.sinalFavoritosAtual;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: sinalFavoritosAtual');
    return implementation(...args);
  }

  function normalizarIdAnuncioFavoritosIa(...args) {
    const implementation = internal.normalizarIdAnuncioFavoritosIa;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: normalizarIdAnuncioFavoritosIa');
    return implementation(...args);
  }

  function obterDescricaoAnuncioFavoritosIa(...args) {
    const implementation = internal.obterDescricaoAnuncioFavoritosIa;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: obterDescricaoAnuncioFavoritosIa');
    return implementation(...args);
  }

  function anuncioFavoritosIaPayload(...args) {
    const implementation = internal.anuncioFavoritosIaPayload;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: anuncioFavoritosIaPayload');
    return implementation(...args);
  }

  function normalizarAnuncioRemovidoIa(...args) {
    const implementation = internal.normalizarAnuncioRemovidoIa;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: normalizarAnuncioRemovidoIa');
    return implementation(...args);
  }

  async function buscarAnunciosPropriosFavoritosIa(info, itemSidebar = null) {
      const sku = String(info && info.sku || '').trim();
      if (!sku || grupoRankingFavoritosEhAvulso(info)) return [];
      const loja = favoritosLojaSelecionadaParaApi((info && info.loja) || (itemSidebar && itemSidebar.loja) || '');
      const mlbs = extrairMlbsItemSkuSidebar(itemSidebar);
      const cacheKey = [
          skuChaveSku(sku),
          skuNormalizarLoja(loja),
          mlbs.join(',')
      ].join('|');
      if (mlFavoritosAnunciosPropriosIaCache.has(cacheKey)) {
          return mlFavoritosAnunciosPropriosIaCache.get(cacheKey).map(item => ({ ...item }));
      }
      const params = new URLSearchParams({ sku });
      if (loja) params.set('loja', loja);
      if (mlbs.length) params.set('mlbs', mlbs.join(','));
      const response = await fetch(`/api/favoritos/ml/anuncios-sku?${params.toString()}`, {
          headers: obterAuthHeaders(),
          cache: 'no-store',
          signal: sinalFavoritosAtual()
      });
      if (!response.ok) {
          let detalhe = `HTTP ${response.status}`;
          try {
              const dataErro = await response.json();
              detalhe = dataErro.detail || detalhe;
          } catch (_err) {}
          throw new Error(detalhe);
      }
      const data = await response.json();
      const anuncios = (Array.isArray(data.anuncios) ? data.anuncios : [])
          .filter(Boolean)
          .map((anuncio, index) => ({
              ...anuncio,
              rank: index + 1,
              imagem: obterImagemAnuncioFavoritos(anuncio),
              descricao: obterDescricaoAnuncioFavoritosIa(anuncio)
          }));
      mlFavoritosAnunciosPropriosIaCache.set(cacheKey, anuncios.map(item => ({ ...item })));
      return anuncios;
  }

  async function filtrarAnunciosFavoritosPorIa(info, anuncios, opcoes = {}) {
      const lista = Array.isArray(anuncios) ? anuncios.filter(Boolean) : [];
      const usarIa = !!(opcoes && opcoes.usarIa);
      if (!usarIa || !lista.length) {
          return { anuncios: lista, removidos: [], removidosTotal: 0, usouIa: false };
      }

      const maxConfirmados = Math.max(1, Math.min(20, Number(opcoes.maxConfirmados) || 8));
      const itemSidebar = opcoes.itemSidebar || null;
      let meusAnuncios = Array.isArray(opcoes.meusAnuncios) ? opcoes.meusAnuncios.filter(Boolean) : [];
      if (!meusAnuncios.length) {
          try {
              meusAnuncios = await buscarAnunciosPropriosFavoritosIa(info, itemSidebar);
          } catch (err) {
              console.warn('Nao foi possivel carregar nossos anuncios para IA:', err);
              mostrarBalaoFavoritosStatus(`SKU ${info && info.sku || ''}: nao consegui carregar nossos anuncios para IA. Vou comparar usando o cadastro.`, {
                  erro: true,
                  tempoMs: 4500
              });
          }
      }

      const pesquisas = normalizarTermosPesquisaFavoritos(info && info.termos)
          .map(item => item.termo)
          .filter(Boolean);
      mostrarBalaoFavoritosStatus(`SKU ${info && info.sku || ''}: IA verificando anuncios fora do produto sem limitar o ranking...`);
      const response = await fetch('/api/favoritos/ranking/filtrar-ia', {
          method: 'POST',
          headers: headersJsonAutenticado(),
          signal: sinalFavoritosAtual(),
          body: JSON.stringify({
              sku: info && info.sku || '',
              titulo: info && info.titulo || '',
              descricao: info && info.descricao || '',
              pesquisas,
              meus_anuncios: meusAnuncios.map(anuncioFavoritosIaPayload).slice(0, 12),
              anuncios: lista.map(anuncioFavoritosIaPayload),
              max_anuncios: Math.min(180, lista.length),
              max_confirmados: maxConfirmados,
              usar_imagem: true
          })
      });
      if (!response.ok) {
          let detalhe = `HTTP ${response.status}`;
          try {
              const dataErro = await response.json();
              detalhe = dataErro.detail || detalhe;
          } catch (_err) {}
          throw new Error(`Erro na IA de favoritos: ${detalhe}`);
      }

      const data = await response.json();
      const manterIds = new Set((data.manter_ids || []).map(id => String(id || '').trim().toUpperCase().replace(/-/g, '')).filter(Boolean));
      const removerIds = new Set((data.remover_ids || []).map(id => String(id || '').trim().toUpperCase().replace(/-/g, '')).filter(Boolean));
      const motivos = new Map();
      (Array.isArray(data.removidos) ? data.removidos : []).forEach(item => {
          const id = String(item && (item.id || item.mlb) || '').trim().toUpperCase().replace(/-/g, '');
          if (id) motivos.set(id, String(item.motivo || item.reason || 'Removido pela IA').trim());
      });

      const removidos = [];
      const confirmados = [];
      lista.forEach(anuncio => {
          const id = normalizarIdAnuncioFavoritosIa(anuncio);
          if (id && (removerIds.has(id) || motivos.has(id))) {
              removidos.push(normalizarAnuncioRemovidoIa(anuncio, motivos.get(id)));
              return;
          }
          if (!manterIds.size || (id && manterIds.has(id))) {
              confirmados.push(anuncio);
          }
      });

      return {
          anuncios: confirmados,
          removidos,
          removidosTotal: removidos.length,
          usouIa: true,
          confirmados: confirmados.length,
          meusAnunciosTotal: meusAnuncios.length,
          maxConfirmados
      };
  }

  Object.assign(internal, {
    buscarAnunciosPropriosFavoritosIa,
    filtrarAnunciosFavoritosPorIa
  });
  internal.components.add('14-ai-filter');
})(window);
