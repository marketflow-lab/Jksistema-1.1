(function (global) {
  'use strict';

  const feature = global.FavoritosV2.promotionEffectuation;
  const internal = feature.internal;
  const state = feature.runtime.state;
  const adapters = feature.runtime.adapters;
  if (internal.components.has('04-client-single-execution')) return;

  async function definirProtecaoAutomacaoMlFavoritos(ativa, motivo = 'favoritos-efetivar') {
      if (!(window.electronAPI && typeof window.electronAPI.setMlAutomationActive === 'function')) {
          return false;
      }
      try {
          const resultado = await window.electronAPI.setMlAutomationActive(!!ativa, motivo);
          return !!(resultado && resultado.success !== false);
      } catch (err) {
          console.warn('Nao foi possivel atualizar a protecao da automacao ML:', err);
          return false;
      }
  }

  function montarPayloadEfetivacaoFavoritos({ loja, itemId, anuncioConta, sim, campanha, campanhaId, precoPromocional }) {
      return {
          loja,
          sku: state.favMlSkuSelecionado || (anuncioConta && anuncioConta.sku) || '',
          item_id: itemId,
          preco_anuncio: sim.preco,
          preco_promocional: precoPromocional,
          preco_ideal: sim.precoCompetitivo,
          preco_competitivo: sim.precoCompetitivo,
          percentual_promocao: sim.percentualPromocao,
          campanha_id: campanhaId,
          campanha_nome: campanha.nome || campanha.name || '',
          promotion_type: campanha.tipo || campanha.type || campanha.promotion_type || 'SELLER_CAMPAIGN',
          listing_type_id_alvo: sim.listingTypeIdAlvo || '',
          tipo_anuncio_alvo: sim.tipoAnuncioAlvo || '',
          tipo_anuncio_atual: sim.tipoAnuncioAtual || '',
          simulacao: sim,
          anuncio: anuncioConta || {}
      };
  }

  function mensagemErroRespostaEfetivacaoFavoritos(resultado, status) {
      const mensagem = resultado && typeof resultado === 'object'
          ? (resultado.message || resultado.error || (typeof resultado.detail === 'string' ? resultado.detail : ''))
          : resultado;
      return internal.normalizarErroEfetivacaoFavoritos(mensagem || `HTTP ${status}`);
  }

  async function efetivarFavoritoMercadoLivre(anuncioConta, anuncioRanking, sim, opcoesPromocao, botao, config = {}) {
      const itemId = String(anuncioConta && (anuncioConta.mlb || anuncioConta.id || anuncioConta.item_id || '') || '').trim();
      const campanha = opcoesPromocao && opcoesPromocao.campanha || {};
      const campanhaId = String(campanha.id || campanha.campaign_id || '').trim();
      const loja = internal.obterLojaEfetivarFavoritos(anuncioConta);
      const precoPromocional = sim && (sim.precoPromocionalCalculado ?? sim.precoPromocional ?? sim.precoCompetitivo);
      if (!itemId || !loja || !campanhaId || !sim || !sim.ok || !precoPromocional) {
          window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Nao foi possivel efetivar: confira loja, MLB, promocao e simulacao.', {
              erro: true,
              tempoMs: 6000
          });
          return;
      }

      if (config.confirmar !== false) {
          const pctTxt = sim.percentualPromocao !== null && sim.percentualPromocao !== undefined
              ? `${Number(sim.percentualPromocao).toFixed(2).replace('.', ',')}%`
              : '';
          const pergunta = [
              `Efetivar favorito do ${itemId}?`,
              `Preco do anuncio: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.preco)}`,
              `Preco final na promocao: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(precoPromocional)}`,
              pctTxt ? `Promocao: ${campanha.nome || campanhaId} (${pctTxt})` : `Promocao: ${campanha.nome || campanhaId}`
          ].join('\n');
          if (!window.confirm(pergunta)) return null;
      }

      const textoOriginal = botao ? botao.textContent : '';
      if (botao) {
          botao.disabled = true;
          botao.textContent = '...';
      }
      if (config.mostrarStatus !== false) {
          window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Efetivando favorito ${itemId}: removendo a promocao atual, ajustando o preco cheio calculado, aplicando e conferindo a campanha; se ela falhar, sera tentado o preco direto seguro sem promocao...`, {
              larga: true
          });
      }
      try {
          const body = internal.montarPayloadEfetivacaoFavoritos({
              loja, itemId, anuncioConta, sim, campanha, campanhaId, precoPromocional
          });
          const response = await fetch('/api/favoritos/ml/efetivar-promocao', {
              method: 'POST',
              headers: adapters.headersJsonAutenticado(),
              body: JSON.stringify(body)
          });
          const data = await response.json().catch(() => ({}));
          const resultado = data && typeof data.detail === 'object' && data.detail !== null
              ? data.detail
              : data;
          if (resultado && resultado.outcome === 'pending_review') {
              if (config.mostrarStatus !== false) {
                  window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(resultado.message || `${itemId} ficou pendente de revisao no Mercado Livre.`, {
                      aviso: true,
                      larga: true
                  });
              }
              if (state.favMlStatusEl && config.atualizarStatus !== false) {
                  state.favMlStatusEl.textContent = `${itemId} pendente: aguarde o anuncio voltar a ativo e aprove novamente.`;
              }
              if (config.recarregar !== false) {
                  await adapters.carregarFavoritosAnunciosSku(state.favMlSkuSelecionado, loja);
              }
              return resultado;
          }
          if (!response.ok || !resultado || !resultado.success) {
              const erro = new Error(mensagemErroRespostaEfetivacaoFavoritos(resultado, response.status));
              erro.favoritosResultado = resultado && typeof resultado === 'object' ? resultado : null;
              throw erro;
          }
          const fallbackSemPromocao = !!(resultado && resultado.fallback_sem_promocao_aplicado);
          const precosObservados = internal.obterPrecosObservadosEfetivacaoFavoritos(resultado || {});
          if (config.mostrarStatus !== false) {
              if (fallbackSemPromocao) {
                  const margemFallback = resultado.margem_estimada_contingencia !== null && resultado.margem_estimada_contingencia !== undefined
                      ? ` Margem estimada: ${adapters.formatarMargemAnuncioFavoritos(resultado.margem_estimada_contingencia)}.`
                      : '';
                  window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(
                      precosObservados.precoDiretoFallback !== null
                          ? `A campanha nao foi mantida no ML para ${itemId}. Fallback direto sem promocao confirmado em ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(precosObservados.precoDiretoFallback)}.${margemFallback}`
                          : `A campanha nao foi mantida no ML para ${itemId}. O fallback direto sem promocao foi concluido, mas o retorno nao trouxe preco observado.${margemFallback}`,
                      { tempoMs: 8000, larga: true }
                  );
              } else {
                  window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Tudo certo: favorito feito no Mercado Livre para ${itemId}.`, {
                      tempoMs: 7000
                  });
              }
          }
          if (state.favMlStatusEl && config.atualizarStatus !== false) {
              if (fallbackSemPromocao) {
                  state.favMlStatusEl.textContent = precosObservados.precoDiretoFallback !== null
                      ? `Favorito ajustado sem campanha: ${itemId} teve preco direto confirmado em ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(precosObservados.precoDiretoFallback)}.`
                      : `Favorito ajustado sem campanha: ${itemId} concluiu o fallback, sem preco observado no retorno.`;
              } else {
                  state.favMlStatusEl.textContent = precosObservados.precoPromocional !== null
                      ? `Favorito feito: ${itemId} teve preco promocional observado em ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(precosObservados.precoPromocional)} com ${campanha.nome || campanhaId}.`
                      : `Favorito feito: ${itemId} teve a campanha ${campanha.nome || campanhaId} confirmada, sem preco promocional observado no retorno.`;
              }
          }
          if (config.recarregar !== false) {
              await adapters.carregarFavoritosAnunciosSku(state.favMlSkuSelecionado, loja);
          }
          return resultado;
      } catch (err) {
          if (config.mostrarStatus !== false) {
              window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Erro ao efetivar favorito: ${err && err.message ? err.message : err}`, {
                  erro: true,
                  larga: true
              });
          }
          if (config.propagarErro) throw err;
          return null;
      } finally {
          if (botao) {
              botao.disabled = false;
              botao.textContent = textoOriginal || 'Efetivar';
          }
      }
  }

  Object.assign(internal, {
    definirProtecaoAutomacaoMlFavoritos,
    montarPayloadEfetivacaoFavoritos,
    efetivarFavoritoMercadoLivre
  });
  internal.components.add('04-client-single-execution');
})(window);
