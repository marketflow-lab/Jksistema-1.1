(function (global) {
  'use strict';

  const feature = global.FavoritosV2.promotionEffectuation;
  const internal = feature.internal;
  const state = feature.runtime.state;
  const adapters = feature.runtime.adapters;
  if (internal.components.has('02-promotion-options')) return;

  function clonarOpcoesPromocaoFavoritos(opcoes) {
      if (!opcoes || typeof opcoes !== 'object') return null;
      try {
          return JSON.parse(JSON.stringify(opcoes));
      } catch (err) {
          return {
              ...opcoes,
              campanha: opcoes.campanha && typeof opcoes.campanha === 'object' ? { ...opcoes.campanha } : opcoes.campanha,
              desconto: opcoes.desconto && typeof opcoes.desconto === 'object' ? { ...opcoes.desconto } : opcoes.desconto
          };
      }
  }

  function extrairOpcoesPromocaoGrupoFavoritos(grupo) {
      if (!grupo || typeof grupo !== 'object') return null;
      const candidatos = [
          grupo.opcoes_promocao,
          grupo.opcoesPromocao,
          grupo.opcoes_promocao_favoritos,
          grupo.promocao_favoritos,
          grupo.promocaoFavoritos,
          grupo.promotion_options,
          grupo.promotionOptions
      ];
      for (const candidato of candidatos) {
          if (candidato && typeof candidato === 'object') return candidato;
      }
      if (
          Object.prototype.hasOwnProperty.call(grupo, 'usar_promocao')
          || grupo.campanha
          || grupo.desconto
      ) {
          return grupo;
      }
      return null;
  }

  function salvarOpcoesPromocaoFavoritosSku(sku, opcoes) {
      const chave = adapters.skuChaveSku(sku);
      if (!chave || !opcoes || typeof opcoes !== 'object') return;
      state.mlFavoritosOpcoesPromocaoPorSku.set(chave, internal.clonarOpcoesPromocaoFavoritos(opcoes));
  }

  function obterOpcoesPromocaoSalvasFavoritosSku(sku) {
      const chave = adapters.skuChaveSku(sku);
      if (!chave) return null;
      const opcoes = state.mlFavoritosOpcoesPromocaoPorSku.get(chave);
      return internal.clonarOpcoesPromocaoFavoritos(opcoes);
  }

  function resolverOpcoesPromocaoGrupoFavoritos(grupo, sku) {
      const opcoesGrupo = internal.extrairOpcoesPromocaoGrupoFavoritos(grupo);
      if (opcoesGrupo) return internal.clonarOpcoesPromocaoFavoritos(opcoesGrupo);
      return internal.obterOpcoesPromocaoSalvasFavoritosSku(sku);
  }

  function obterOpcoesPromocaoFavoritosSku(sku) {
      const resultadoRanking = adapters.obterGrupoRankingFavoritosSku(sku);
      const grupo = resultadoRanking && resultadoRanking.grupo;
      const opcoesGrupo = internal.resolverOpcoesPromocaoGrupoFavoritos(grupo, sku);
      if (opcoesGrupo) return opcoesGrupo;
      return internal.obterOpcoesPromocaoSalvasFavoritosSku(sku);
  }

  function obterPercentualPromocaoFixaFavoritos(opcoesPromocao) {
      if (!opcoesPromocao || !opcoesPromocao.usar_promocao) return null;
      const desconto = opcoesPromocao.desconto || {};
      const modo = String(desconto.modo || opcoesPromocao.modo || '').trim();
      if (modo && modo !== 'percentual_fixo') return null;
      const numero = Number(String(
          desconto.percentual ??
          opcoesPromocao.percentual ??
          opcoesPromocao.desconto_percentual ??
          opcoesPromocao.discount_percentage ??
          ''
      ).replace(',', '.').trim());
      if (!Number.isFinite(numero) || numero <= 0 || numero >= 100) return null;
      return numero;
  }

  function assinaturaOpcoesPromocaoFavoritos(opcoesPromocao) {
      const campanha = opcoesPromocao && opcoesPromocao.campanha || {};
      const campanhaId = String(campanha.id || campanha.campaign_id || '').trim();
      const percentual = internal.obterPercentualPromocaoFixaFavoritos(opcoesPromocao);
      if (!campanhaId || percentual === null) return '';
      return `${campanhaId}|${Number(percentual).toFixed(4)}`;
  }

  function promocaoFavoritosSemPercentualFixo(opcoesPromocao) {
      return !!(
          opcoesPromocao
          && opcoesPromocao.usar_promocao
          && internal.obterPercentualPromocaoFixaFavoritos(opcoesPromocao) === null
      );
  }

  function opcoesPromocaoFavoritosProntas(opcoesPromocao) {
      const campanha = opcoesPromocao && opcoesPromocao.campanha || {};
      const campanhaId = String(campanha.id || campanha.campaign_id || '').trim();
      return !!(
          opcoesPromocao
          && opcoesPromocao.usar_promocao
          && campanhaId
          && internal.obterPercentualPromocaoFixaFavoritos(opcoesPromocao) !== null
      );
  }

  function aplicarOpcoesPromocaoFavoritosSku(sku, opcoesPromocao) {
      const chave = adapters.skuChaveSku(sku);
      const opcoes = internal.clonarOpcoesPromocaoFavoritos(opcoesPromocao);
      if (!chave || !internal.opcoesPromocaoFavoritosProntas(opcoes)) return null;
      internal.salvarOpcoesPromocaoFavoritosSku(sku, opcoes);
      state.mlFavoritosOpcoesPromocaoAtual = internal.clonarOpcoesPromocaoFavoritos(opcoes);
      const rankingAtual = adapters.obterGrupoRankingFavoritosSku(sku);
      if (rankingAtual && rankingAtual.grupo && typeof rankingAtual.grupo === 'object') {
          rankingAtual.grupo.opcoes_promocao = internal.clonarOpcoesPromocaoFavoritos(opcoes);
      }
      adapters.favoritosSalvarEstadoLojaAtual();
      return internal.clonarOpcoesPromocaoFavoritos(opcoes);
  }

  async function escolherPromocaoFavoritosSkuAtual(opcoes = {}) {
      const sku = String(state.favMlSkuSelecionado || '').trim();
      if (!sku) {
          window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Selecione um SKU antes de escolher campanha e porcentagem.', {
              erro: true,
              tempoMs: 4500
          });
          return null;
      }
      const opcoesPromocao = await window.FavoritosV2.searchRanking.publicApi.promotions.perguntarOpcoesPromocaoFavoritos({
          exigirPromocao: true,
          loja: state.favMlLojaSelecionada || state.mlSkuLojaSelecionada || state.skuLojaSelecionada || ''
      });
      const aplicadas = internal.aplicarOpcoesPromocaoFavoritosSku(sku, opcoesPromocao);
      if (!aplicadas) {
          internal.atualizarPainelEfetivarFavoritos();
          return null;
      }
      if (Array.isArray(state.favMlAnunciosSkuAtual) && state.favMlAnunciosSkuAtual.length) {
          adapters.renderizarFavoritosAnunciosMl(state.favMlAnunciosSkuAtual, sku);
      } else {
          internal.atualizarPainelEfetivarFavoritos();
      }
      if (opcoes.mostrarStatus !== false) {
          window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Campanha e porcentagem salvas para ${sku}. Simulacao recalculada para aprovar e alterar.`, {
              tempoMs: 5500,
              larga: true
          });
      }
      return aplicadas;
  }

  async function garantirPromocaoFavoritosSkuAtual(sku) {
      let opcoesPromocao = internal.obterOpcoesPromocaoFavoritosSku(sku);
      if (internal.opcoesPromocaoFavoritosProntas(opcoesPromocao)) return opcoesPromocao;
      window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Escolha a campanha e a porcentagem para recalcular antes de enviar ao Mercado Livre.', {
          larga: true
      });
      opcoesPromocao = await internal.escolherPromocaoFavoritosSkuAtual({ mostrarStatus: false });
      if (!internal.opcoesPromocaoFavoritosProntas(opcoesPromocao)) {
          window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Campanha e porcentagem nao foram informadas. A alteracao no Mercado Livre nao foi enviada.', {
              erro: true,
              tempoMs: 6500,
              larga: true
          });
          internal.atualizarPainelEfetivarFavoritos();
          return null;
      }
      return opcoesPromocao;
  }

  Object.assign(internal, {
    clonarOpcoesPromocaoFavoritos,
    extrairOpcoesPromocaoGrupoFavoritos,
    salvarOpcoesPromocaoFavoritosSku,
    obterOpcoesPromocaoSalvasFavoritosSku,
    resolverOpcoesPromocaoGrupoFavoritos,
    obterOpcoesPromocaoFavoritosSku,
    obterPercentualPromocaoFixaFavoritos,
    assinaturaOpcoesPromocaoFavoritos,
    promocaoFavoritosSemPercentualFixo,
    opcoesPromocaoFavoritosProntas,
    aplicarOpcoesPromocaoFavoritosSku,
    escolherPromocaoFavoritosSkuAtual,
    garantirPromocaoFavoritosSkuAtual
  });
  internal.components.add('02-promotion-options');
})(window);
