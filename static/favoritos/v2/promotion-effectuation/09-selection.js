(function (global) {
  'use strict';

  const feature = global.FavoritosV2.promotionEffectuation;
  const internal = feature.internal;
  const state = feature.runtime.state;
  const adapters = feature.runtime.adapters;
  if (internal.components.has('09-selection')) return;

  function obterLojaAnuncioFavoritos(anuncio) {
      return String(anuncio && (anuncio.loja || anuncio.loja_sync || anuncio.loja_conta) || '').trim();
  }

  function chaveSkuSelecaoAlteracaoFavoritos(sku) {
      return adapters.skuChaveSku(sku || state.favMlSkuSelecionado || '') || '';
  }

  function obterSetNaoAlterarFavoritosSku(sku, criar = false) {
      const chaveSku = internal.chaveSkuSelecaoAlteracaoFavoritos(sku);
      if (!chaveSku) return null;
      if (!state.favMlAnunciosNaoAlterarPorSku || !(state.favMlAnunciosNaoAlterarPorSku instanceof Map)) {
          state.favMlAnunciosNaoAlterarPorSku = new Map();
      }
      let set = state.favMlAnunciosNaoAlterarPorSku.get(chaveSku);
      if (!set && criar) {
          set = new Set();
          state.favMlAnunciosNaoAlterarPorSku.set(chaveSku, set);
      }
      return set || null;
  }

  function chavesAnuncioAlteracaoFavoritos(anuncio, registro = null) {
      const itemId = adapters.obterIdAnuncioFavoritos(anuncio || registro && registro.anuncio || {});
      if (!itemId) return [];
      const loja = adapters.skuNormalizarLoja(
          registro && registro.loja
          || internal.obterLojaEfetivarFavoritos(anuncio)
          || internal.obterLojaAnuncioFavoritos(anuncio)
          || ''
      );
      const chaves = new Set([`id:${itemId}`]);
      if (loja) chaves.add(`loja:${loja}|${itemId}`);
      return Array.from(chaves);
  }

  function anuncioSelecionadoAlteracaoFavoritos(sku, anuncio, registro = null) {
      const set = internal.obterSetNaoAlterarFavoritosSku(sku, false);
      if (!set || !set.size) return true;
      const chaves = internal.chavesAnuncioAlteracaoFavoritos(anuncio, registro);
      if (!chaves.length) return true;
      return !chaves.some(chave => set.has(chave));
  }

  function definirAnuncioSelecionadoAlteracaoFavoritos(sku, anuncio, selecionado) {
      const set = internal.obterSetNaoAlterarFavoritosSku(sku, !selecionado);
      const chaves = internal.chavesAnuncioAlteracaoFavoritos(anuncio);
      if (!set || !chaves.length) return true;
      chaves.forEach(chave => {
          if (selecionado) set.delete(chave);
          else set.add(chave);
      });
      if (!set.size) {
          const chaveSku = internal.chaveSkuSelecaoAlteracaoFavoritos(sku);
          if (chaveSku && state.favMlAnunciosNaoAlterarPorSku) state.favMlAnunciosNaoAlterarPorSku.delete(chaveSku);
      }
      return selecionado;
  }

  function limparSelecaoAlteracaoFavoritosSku(sku) {
      const chaveSku = internal.chaveSkuSelecaoAlteracaoFavoritos(sku);
      if (chaveSku && state.favMlAnunciosNaoAlterarPorSku) {
          state.favMlAnunciosNaoAlterarPorSku.delete(chaveSku);
      }
  }

  function filtrarRegistrosSelecionadosAlteracaoFavoritos(registros, sku) {
      return (Array.isArray(registros) ? registros : []).filter(registro => {
          return internal.anuncioSelecionadoAlteracaoFavoritos(sku, registro && registro.anuncio, registro);
      });
  }

  function contarRegistrosDesmarcadosAlteracaoFavoritos(registros, sku) {
      const lista = Array.isArray(registros) ? registros : [];
      return lista.length - internal.filtrarRegistrosSelecionadosAlteracaoFavoritos(lista, sku).length;
  }

  function obterRegistrosSimulacaoFavoritos(anuncios, sku, opcoesPromocao, cachePreferencial = null) {
      const lista = Array.isArray(anuncios) ? anuncios : [];
      const ranking = adapters.obterRankingFavoritosParaSimulador(sku);
      const precosFinaisReservados = new Set();
      const precosCheiosReservados = new Set();
      const assinaturaPromocaoAtual = internal.assinaturaOpcoesPromocaoFavoritos(opcoesPromocao);
      return lista.map((anuncio, index) => {
          const rankingLinha = ranking[index];
          const itemId = adapters.obterIdAnuncioFavoritos(anuncio);
          const cache = Array.isArray(cachePreferencial) ? cachePreferencial[index] : null;
          let sim = cache && cache.itemId === itemId
              ? cache.sim
              : null;
          if (sim && sim.ok && assinaturaPromocaoAtual && sim.assinaturaPromocao !== assinaturaPromocaoAtual) {
              sim = null;
          }
          if (sim && sim.ok) {
              const chaveFinalCache = adapters.chavePrecoCentavosFavoritos(adapters.obterPrecoFinalSimulacaoFavoritos(sim));
              const chaveCheioCache = adapters.chavePrecoCentavosFavoritos(sim.preco);
              if (
                  (chaveFinalCache && precosFinaisReservados.has(chaveFinalCache))
                  || (chaveCheioCache && precosCheiosReservados.has(chaveCheioCache))
              ) {
                  sim = null;
              } else {
                  if (chaveFinalCache) precosFinaisReservados.add(chaveFinalCache);
                  if (chaveCheioCache) precosCheiosReservados.add(chaveCheioCache);
              }
          }
          if (!sim) {
              sim = internal.calcularSimulacaoPrecoFavoritos(anuncio, rankingLinha, opcoesPromocao, {
                  precosFinaisReservados,
                  precosCheiosReservados
              });
          }
          return {
              anuncio,
              ranking: rankingLinha,
              sim,
              index,
              itemId,
              selecionadoAlteracao: internal.anuncioSelecionadoAlteracaoFavoritos(sku, anuncio),
              loja: internal.obterLojaEfetivarFavoritos(anuncio),
              lojaOriginal: internal.obterLojaAnuncioFavoritos(anuncio)
          };
      });
  }

  function filtrarRegistrosEfetivaveisFavoritos(registros) {
      const vistos = new Set();
      return (Array.isArray(registros) ? registros : []).filter(registro => {
          const sim = registro && registro.sim;
          const chave = `${adapters.skuNormalizarLoja(registro && registro.loja)}|${registro && registro.itemId}`;
          if (!registro || !registro.itemId || !registro.loja || !sim || !sim.ok || sim.percentualPromocao === null) return false;
          if (vistos.has(chave)) return false;
          vistos.add(chave);
          return true;
      });
  }

  function explicarRegistrosNaoEfetivaveisFavoritos(registros, registrosAntesFiltro = null) {
      const lista = Array.isArray(registros) ? registros : [];
      const listaOriginal = Array.isArray(registrosAntesFiltro) ? registrosAntesFiltro : lista;
      if (!lista.length && listaOriginal.length) {
          return 'Os anuncios encontrados pertencem a outra loja. Marque "Fazer tambem nas outras contas" ou selecione a loja do anuncio.';
      }
      if (!lista.length) return 'Nenhum anuncio proprio foi encontrado para este SKU.';
      const semPercentual = lista.find(registro => registro && registro.sim && registro.sim.ok && registro.sim.percentualPromocao === null);
      if (semPercentual) return 'A simulacao estava sem percentual de promocao. Salve campanha e % novamente para recalcular.';
      const semSimulacao = lista.find(registro => !registro || !registro.sim || !registro.sim.ok);
      if (semSimulacao) {
          return semSimulacao && semSimulacao.sim && semSimulacao.sim.status
              ? internal.textoCurtoStatusSimuladorFavoritos(semSimulacao.sim.status)
              : 'A simulacao nao ficou valida.';
      }
      const semDados = lista.find(registro => !registro || !registro.itemId || !registro.loja);
      if (semDados) return 'Faltou MLB ou loja em pelo menos um anuncio.';
      return 'Confira os motivos exibidos na coluna Simulador.';
  }

  Object.assign(internal, {
    obterLojaAnuncioFavoritos,
    chaveSkuSelecaoAlteracaoFavoritos,
    obterSetNaoAlterarFavoritosSku,
    chavesAnuncioAlteracaoFavoritos,
    anuncioSelecionadoAlteracaoFavoritos,
    definirAnuncioSelecionadoAlteracaoFavoritos,
    limparSelecaoAlteracaoFavoritosSku,
    filtrarRegistrosSelecionadosAlteracaoFavoritos,
    contarRegistrosDesmarcadosAlteracaoFavoritos,
    obterRegistrosSimulacaoFavoritos,
    filtrarRegistrosEfetivaveisFavoritos,
    explicarRegistrosNaoEfetivaveisFavoritos
  });
  internal.components.add('09-selection');
})(window);
