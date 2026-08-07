(function (global) {
  'use strict';

  const feature = global.FavoritosV2.promotionEffectuation;
  const internal = feature.internal;
  const state = feature.runtime.state;
  const adapters = feature.runtime.adapters;
  if (internal.components.has('07-history')) return;

  function clonarAnuncioHistoricoAlteracaoFavoritos(anuncio) {
      if (!anuncio || typeof anuncio !== 'object') return {};
      try {
          if (typeof adapters.anuncioHistoricoPayload === 'function') {
              const payloadPadrao = adapters.anuncioHistoricoPayload(anuncio);
              const custoAnuncio = anuncio.custo ?? anuncio.custo_unitario ?? anuncio.custo_produto ?? anuncio.preco_custo ?? anuncio.valor_custo ?? '';
              const custoFrete = anuncio.custo_frete ?? anuncio.frete_ml ?? anuncio.shipping_cost ?? anuncio.shipping_seller_cost ?? '';
              return {
                  ...payloadPadrao,
                  custo: payloadPadrao.custo ?? custoAnuncio,
                  custo_unitario: payloadPadrao.custo_unitario ?? custoAnuncio,
                  custo_produto: payloadPadrao.custo_produto ?? custoAnuncio,
                  preco_custo: payloadPadrao.preco_custo ?? custoAnuncio,
                  valor_custo: payloadPadrao.valor_custo ?? custoAnuncio,
                  custo_frete: payloadPadrao.custo_frete ?? custoFrete
              };
          }
      } catch (err) {
          console.warn('Nao foi possivel usar payload padrao do historico de favoritos:', err);
      }
      const url = String(anuncio.url || anuncio.permalink || anuncio.link || '').trim();
      const id = String(anuncio.id || anuncio.mlb || anuncio.item_id || adapters.extrairItemIdAnuncio(url) || '').trim();
      const precos = typeof adapters.obterPrecosAnuncioFavoritos === 'function'
          ? adapters.obterPrecosAnuncioFavoritos(anuncio)
          : { preco: anuncio.preco ?? anuncio.price ?? null, promocional: anuncio.preco_promocional ?? anuncio.promotional_price ?? null, desconto: '' };
      const imagem = typeof adapters.obterImagemAnuncioFavoritos === 'function'
          ? adapters.obterImagemAnuncioFavoritos(anuncio)
          : (anuncio.imagem || anuncio.thumbnail || anuncio.foto || '');
      const tipoAnuncio = typeof internal.obterTipoAnuncioFavoritos === 'function'
          ? internal.obterTipoAnuncioFavoritos(anuncio)
          : (anuncio.tipo_anuncio || anuncio.listing_type_name || '');
      const listingTypeId = typeof internal.obterListingTypeIdAnuncioFavoritos === 'function'
          ? internal.obterListingTypeIdAnuncioFavoritos(anuncio)
          : (anuncio.listing_type_id || anuncio.listingTypeId || '');
      return {
          id,
          mlb: id,
          url,
          permalink: url,
          link: url,
          titulo: String(anuncio.titulo || anuncio.title || '').trim(),
          title: String(anuncio.titulo || anuncio.title || '').trim(),
          vendedor: anuncio.vendedor || anuncio.seller_name || anuncio.sellerNickname || anuncio.nickname || '',
          imagem,
          thumbnail: imagem,
          foto: imagem,
          preco: precos.preco,
          price: precos.promocional !== null && precos.promocional !== undefined ? precos.promocional : precos.preco,
          preco_original: precos.promocional !== null && precos.promocional !== undefined ? precos.preco : '',
          preco_promocional: precos.promocional,
          discount_pct: precos.desconto || '',
          custo: anuncio.custo ?? anuncio.custo_unitario ?? anuncio.custo_produto ?? anuncio.preco_custo ?? anuncio.valor_custo ?? '',
          custo_unitario: anuncio.custo_unitario ?? anuncio.custo ?? anuncio.custo_produto ?? anuncio.preco_custo ?? anuncio.valor_custo ?? '',
          custo_produto: anuncio.custo_produto ?? anuncio.custo ?? anuncio.custo_unitario ?? anuncio.preco_custo ?? anuncio.valor_custo ?? '',
          preco_custo: anuncio.preco_custo ?? anuncio.custo ?? anuncio.custo_unitario ?? anuncio.custo_produto ?? anuncio.valor_custo ?? '',
          valor_custo: anuncio.valor_custo ?? anuncio.custo ?? anuncio.custo_unitario ?? anuncio.custo_produto ?? anuncio.preco_custo ?? '',
          custo_frete: anuncio.custo_frete ?? anuncio.frete_ml ?? anuncio.shipping_cost ?? anuncio.shipping_seller_cost ?? '',
          moeda: anuncio.moeda || anuncio.currency_id || 'BRL',
          currency_id: anuncio.currency_id || anuncio.moeda || 'BRL',
          tipo_anuncio: tipoAnuncio,
          listing_type_id: listingTypeId,
          listing_type_name: tipoAnuncio,
          media_mensal: anuncio.media_mensal ?? anuncio.ritmo_atual ?? '',
          vendas: anuncio.vendas,
          data_criacao: anuncio.data_criacao || ''
      };
  }

  function montarLinhaRelatorioInicialAlteracaoFavoritos(registro) {
      const itemId = String(registro && (registro.itemId || adapters.obterIdAnuncioFavoritos(registro.anuncio)) || '-').trim();
      const sim = registro && registro.sim || {};
      const partes = [];
      const loja = String(registro && registro.loja || '').trim();
      if (loja) partes.push(`Loja: ${loja}`);
      const tipo = internal.textoTipoEnvioFavoritos(registro || {});
      if (tipo) partes.push(`tipo previsto: ${tipo}`);
      if (sim && sim.ok) {
          partes.push(`preco cheio previsto: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.preco)}`);
          const precoPromocional = sim.precoPromocionalCalculado ?? sim.precoPromocional ?? sim.precoCompetitivo;
          partes.push(`preco final promocional previsto: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(precoPromocional)}`);
          if (sim.ajustePrecoUnico) partes.push(`ajuste preco unico: +${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.ajustePrecoUnico)}`);
          if (sim.limiteMargemAplicado) partes.push('limite de margem 15% aplicado');
      } else if (sim && sim.status) {
          partes.push(`simulacao invalida: ${internal.textoCurtoStatusSimuladorFavoritos(sim.status)}`);
      }
      return {
          tipo: 'info',
          itemId,
          titulo: `${itemId} - alteracao planejada`,
          detalhe: partes.filter(Boolean).join(' | ') || 'Alteracao planejada.'
      };
  }

  function montarLinhaRelatorioFinalAlteracaoFavoritos(item, sucesso, pendente = false) {
      const itemId = String(item && item.itemId || item && item.data && item.data.item_id || '-').trim();
      if (sucesso) {
          const fallback = !!(item && item.data && item.data.fallback_sem_promocao_aplicado);
          return {
              tipo: 'success',
              itemId,
              titulo: `${itemId} - alteracao feita${fallback ? ' com fallback' : ''}`,
              detalhe: internal.descreverSucessoEfetivacaoFavoritos(item)
          };
      }
      if (pendente) {
          return {
              tipo: 'warning',
              itemId,
              titulo: `${itemId} - pendente de revisao`,
              detalhe: internal.descreverPendenteEfetivacaoFavoritos(item)
          };
      }
      const parcial = internal.resultadoEfetivacaoParcialFavoritos(item && item.resultado);
      const incerto = internal.resultadoEfetivacaoIncertoFavoritos(item && item.resultado);
      return {
          tipo: 'error',
          itemId,
          titulo: `${itemId} - ${incerto ? 'estado remoto incerto' : (parcial ? 'alteracao parcial' : 'alteracao nao feita')}`,
          detalhe: internal.descreverFalhaEfetivacaoFavoritos(item)
      };
  }

  function montarSimulacaoHistoricoAlteracaoFavoritos(registro, data = null) {
      const sim = registro && registro.sim || {};
      const anuncio = registro && registro.anuncio || {};
      const observados = internal.obterPrecosObservadosEfetivacaoFavoritos(data || {});
      const comparacao = internal.obterComparacaoPromocaoEfetivacaoFavoritos(data || {}, sim);
      const segurancaComercial = internal.normalizarSegurancaComercialEfetivacaoFavoritos(data || {});
      const custoBase = sim.custo ?? anuncio.custo ?? anuncio.custo_unitario ?? anuncio.custo_produto ?? anuncio.preco_custo ?? anuncio.valor_custo ?? null;
      const custoIdeal = sim.custoIdealAbaixoBase ?? sim.custo_ideal_abaixo_base ?? null;
      const precoAlvoCustoIdeal = sim.precoAlvoCustoIdeal ?? sim.preco_alvo_custo_ideal ?? null;
      const estadoAtual = data && data.current_state && typeof data.current_state === 'object' ? data.current_state : {};
      const verificacaoMargem = data && data.verificacao_margem && typeof data.verificacao_margem === 'object'
          ? data.verificacao_margem
          : {};
      const precoAtualMl = data && data.preco_anuncio_atual !== undefined
          ? data.preco_anuncio_atual
          : (estadoAtual.price !== undefined
              ? estadoAtual.price
              : (observados.precoDiretoFallback ?? observados.precoCheio));
      return {
          ok: !!sim.ok,
          preco_previsto: sim.preco ?? null,
          preco_ideal: sim.precoCompetitivo ?? null,
          preco_promocional_previsto: sim.precoPromocionalCalculado ?? sim.precoPromocional ?? sim.precoCompetitivo ?? null,
          preco_final_solicitado: comparacao.precoFinalSolicitado,
          preco_final_observado: comparacao.precoFinalObservado,
          percentual_promocao_previsto: comparacao.percentualSolicitado,
          percentual_promocao_observado: comparacao.percentualObservado,
          preco_atual_ml: precoAtualMl,
          preco_aplicado: observados.precoDiretoFallback ?? observados.precoCheio,
          preco_promocional_aplicado: observados.precoPromocional,
          margem_prevista: sim.margem ?? null,
          margem_aplicada: data && data.margem_estimada_contingencia !== undefined
              ? data.margem_estimada_contingencia
              : (verificacaoMargem.margem_estimada ?? null),
          custo: custoBase,
          custo_base: custoBase,
          custo_unitario: custoBase,
          custo_produto: custoBase,
          preco_custo: custoBase,
          valor_custo: custoBase,
          limite_margem_aplicado: !!sim.limiteMargemAplicado,
          limiteMargemAplicado: !!sim.limiteMargemAplicado,
          preco_minimo_margem: sim.precoMinimoMargem ?? null,
          preco_alvo_custo_ideal: precoAlvoCustoIdeal,
          precoAlvoCustoIdeal: precoAlvoCustoIdeal,
          custo_ideal_abaixo_base: custoIdeal,
          custoIdealAbaixoBase: custoIdeal,
          preco_custo_necessario: custoIdeal,
          custo_para_concorrer: custoIdeal,
          custo_maximo_para_concorrer: custoIdeal,
          reducao_custo_ideal: sim.reducaoCustoIdeal ?? null,
          tipo_anuncio_atual: sim.tipoAnuncioAtual || '',
          tipo_anuncio_alvo: sim.tipoAnuncioAlvo || '',
          campanha_id: data && data.promotion_id || '',
          campanha_nome: data && data.campanha_nome || '',
          fallback_sem_promocao: !!(data && data.fallback_sem_promocao_aplicado),
          promocao_ainda_ativa: segurancaComercial.promotion_active,
          commercial_safety: JSON.parse(JSON.stringify(segurancaComercial))
      };
  }

  function montarVinculoHistoricoAlteracaoFavoritos(item, sucesso, sku, pendente = false) {
      const registro = item && item.registro || {};
      const itemId = String(item && item.itemId || registro.itemId || adapters.obterIdAnuncioFavoritos(registro.anuncio) || '').trim();
      const relatorioFinal = internal.montarLinhaRelatorioFinalAlteracaoFavoritos(item, sucesso, pendente);
      const fallback = !!(sucesso && item && item.data && item.data.fallback_sem_promocao_aplicado);
      const resultadoFalha = item && item.resultado && typeof item.resultado === 'object' ? item.resultado : null;
      const resultado = sucesso || pendente
          ? (item && item.data && typeof item.data === 'object' ? item.data : {})
          : (resultadoFalha || {});
      const parcial = internal.resultadoEfetivacaoParcialFavoritos(resultadoFalha);
      const incerto = internal.resultadoEfetivacaoIncertoFavoritos(resultadoFalha);
      const segurancaComercial = internal.normalizarSegurancaComercialEfetivacaoFavoritos(resultado);
      const promocaoDivergenteAtiva = segurancaComercial.state === 'unsafe' && segurancaComercial.promotion_active;
      return {
          ordem: Number(registro.index) + 1 || 0,
          sku: String(sku || '').trim(),
          itemId,
          loja: String(registro.loja || '').trim(),
          status: sucesso ? 'success' : (pendente ? 'warning' : 'error'),
           status_texto: sucesso
               ? (fallback ? 'Feito sem campanha' : 'Alterado')
               : (pendente ? 'Pendente de revisao' : (promocaoDivergenteAtiva ? 'Promoção divergente ainda ativa' : (incerto ? 'Estado remoto incerto' : (parcial ? 'Alteracao parcial' : 'Nao concluido')))),
          nosso: internal.clonarAnuncioHistoricoAlteracaoFavoritos(registro.anuncio),
          base: internal.clonarAnuncioHistoricoAlteracaoFavoritos(registro.ranking),
          simulacao: internal.montarSimulacaoHistoricoAlteracaoFavoritos(
              registro,
              resultado
          ),
          outcome: String(resultado.outcome || '').trim(),
          retryable: typeof resultado.retryable === 'boolean' ? resultado.retryable : null,
          retry_requires_approval: typeof resultado.retry_requires_approval === 'boolean'
              ? resultado.retry_requires_approval
              : null,
           terminal: internal.resultadoEfetivacaoTerminalFavoritos(resultado),
          commercial_safety: JSON.parse(JSON.stringify(segurancaComercial)),
          stages: resultado.stages && typeof resultado.stages === 'object'
              ? JSON.parse(JSON.stringify(resultado.stages))
              : {},
          relatorio_inicial: internal.montarLinhaRelatorioInicialAlteracaoFavoritos(registro),
          relatorio_final: relatorioFinal
      };
  }

  function montarLinhaRelatorioNaoEnviadoFavoritos(item) {
      const itemId = String(item && item.itemId || item && item.registro && item.registro.itemId || '-').trim();
      const motivo = internal.normalizarErroEfetivacaoFavoritos(item && item.motivo || 'Lote interrompido por segurança.');
      return {
          tipo: 'warning',
          itemId,
          titulo: `${itemId} - não enviado por bloqueio de segurança`,
          detalhe: `${motivo}${item && item.bloqueadoPor ? ` Anúncio que acionou o bloqueio: ${item.bloqueadoPor}.` : ''}`
      };
  }

  function montarVinculoHistoricoNaoEnviadoFavoritos(item, sku) {
      const registro = item && item.registro || {};
      const itemId = String(item && item.itemId || registro.itemId || adapters.obterIdAnuncioFavoritos(registro.anuncio) || '').trim();
      return {
          ordem: Number(registro.index) + 1 || 0,
          sku: String(sku || '').trim(),
          itemId,
          loja: String(registro.loja || '').trim(),
          status: 'warning',
          status_texto: 'Não enviado por bloqueio de segurança',
          nosso: internal.clonarAnuncioHistoricoAlteracaoFavoritos(registro.anuncio),
          base: internal.clonarAnuncioHistoricoAlteracaoFavoritos(registro.ranking),
          simulacao: internal.montarSimulacaoHistoricoAlteracaoFavoritos(registro, null),
          outcome: 'not_sent_safety_block',
          retryable: null,
          retry_requires_approval: true,
          terminal: false,
          stages: {},
          nao_enviado: true,
          bloqueado_por: String(item && item.bloqueadoPor || '').trim(),
          motivo_bloqueio: internal.normalizarErroEfetivacaoFavoritos(item && item.motivo || ''),
          relatorio_inicial: internal.montarLinhaRelatorioInicialAlteracaoFavoritos(registro),
          relatorio_final: internal.montarLinhaRelatorioNaoEnviadoFavoritos(item)
      };
  }

  function montarHistoricoAlteracoesFavoritosPayload({ sku, opcoesPromocao, incluirOutrasContas, validos, bloqueadosPreEnvio, sucessos, pendentes, falhas, mensagemFinal }) {
      const feitos = Array.isArray(sucessos) ? sucessos : [];
      const aguardando = Array.isArray(pendentes) ? pendentes : [];
      const naoFeitos = Array.isArray(falhas) ? falhas : [];
      const naoEnviados = Array.isArray(arguments[0] && arguments[0].naoEnviados)
          ? arguments[0].naoEnviados
          : [];
      const grupoAtual = typeof adapters.obterGrupoRankingFavoritosSku === 'function'
          ? adapters.obterGrupoRankingFavoritosSku(sku)
          : null;
      const tituloGrupo = String(grupoAtual && grupoAtual.grupo && grupoAtual.grupo.titulo || '').trim();
      const vinculos = [
          ...feitos.map(item => internal.montarVinculoHistoricoAlteracaoFavoritos(item, true, sku)),
          ...aguardando.map(item => internal.montarVinculoHistoricoAlteracaoFavoritos(item, false, sku, true)),
          ...naoFeitos.map(item => internal.montarVinculoHistoricoAlteracaoFavoritos(item, false, sku)),
          ...naoEnviados.map(item => internal.montarVinculoHistoricoNaoEnviadoFavoritos(item, sku))
      ].filter(item => item && (item.itemId || item.nosso && item.nosso.id || item.base && item.base.id));
      const registrosIniciais = [
          ...(Array.isArray(validos) ? validos : []),
          ...(Array.isArray(bloqueadosPreEnvio) ? bloqueadosPreEnvio.map(item => item && item.registro).filter(Boolean) : [])
      ];
      return {
          data_iso: new Date().toISOString(),
          sku: String(sku || '').trim(),
          titulo: tituloGrupo,
          loja: adapters.favoritosLojaSelecionadaParaApi(state.favMlLojaSelecionada || '') || state.favMlLojaSelecionada || '',
          usuario: typeof adapters.nomeUsuarioHistoricoFavoritosAtual === 'function' ? adapters.nomeUsuarioHistoricoFavoritosAtual() : '',
          origem_ranking_id: state.favMlHistoricoExecucaoSelecionadaId || '',
          escopo: incluirOutrasContas ? 'todas_contas' : 'loja_atual',
          opcoes_promocao: internal.clonarOpcoesPromocaoFavoritos(opcoesPromocao || {}),
          mensagem_final: mensagemFinal || '',
          relatorio_inicial: {
              titulo: 'Relatorio inicial',
              resumo: `${registrosIniciais.length} anuncio(s) planejado(s) para alteracao.`,
              linhas: registrosIniciais.map(internal.montarLinhaRelatorioInicialAlteracaoFavoritos)
          },
          relatorio_final: {
              titulo: 'Relatorio final',
              resumo: mensagemFinal || '',
              sucessos: feitos.length,
               pendentes: aguardando.length,
               falhas: naoFeitos.length,
              nao_enviados: naoEnviados.length,
               linhas: [
                   ...feitos.map(item => internal.montarLinhaRelatorioFinalAlteracaoFavoritos(item, true)),
                   ...aguardando.map(item => internal.montarLinhaRelatorioFinalAlteracaoFavoritos(item, false, true)),
                   ...naoFeitos.map(item => internal.montarLinhaRelatorioFinalAlteracaoFavoritos(item, false)),
                  ...naoEnviados.map(item => internal.montarLinhaRelatorioNaoEnviadoFavoritos(item))
               ]
          },
          vinculos
      };
  }

  async function salvarHistoricoAlteracoesFavoritosProcesso(payload) {
      if (!payload || !Array.isArray(payload.vinculos) || !payload.vinculos.length) return null;
      if (typeof adapters.registrarHistoricoAlteracoesFavoritos !== 'function') {
          console.warn('registrarHistoricoAlteracoesFavoritos ainda nao esta disponivel.');
          return null;
      }
      const entrada = adapters.registrarHistoricoAlteracoesFavoritos(payload);
      if (!entrada) return null;
      if (typeof adapters.confirmarSalvamentoHistoricoFavoritosServidor !== 'function') {
          return {
              entrada,
              confirmado: false,
              pendente: true,
              erro: 'O historico foi mantido no cache local, mas a confirmacao do servidor ainda nao esta disponivel.'
          };
      }
      try {
          const confirmacao = await adapters.confirmarSalvamentoHistoricoFavoritosServidor([entrada.id]);
          const confirmado = !!(confirmacao && confirmacao.success === true);
          return {
              entrada,
              confirmado,
              pendente: !confirmado,
              confirmacao: confirmacao || null,
              erro: confirmado ? '' : internal.normalizarErroEfetivacaoFavoritos(
                  confirmacao && confirmacao.erro
                  || 'O servidor ainda nao confirmou o registro do historico.'
              )
          };
      } catch (err) {
          return {
              entrada,
              confirmado: false,
              pendente: true,
              erro: internal.normalizarErroEfetivacaoFavoritos(err)
          };
      }
  }

  Object.assign(internal, {
    clonarAnuncioHistoricoAlteracaoFavoritos,
    montarLinhaRelatorioInicialAlteracaoFavoritos,
    montarLinhaRelatorioFinalAlteracaoFavoritos,
    montarSimulacaoHistoricoAlteracaoFavoritos,
    montarVinculoHistoricoAlteracaoFavoritos,
    montarLinhaRelatorioNaoEnviadoFavoritos,
    montarVinculoHistoricoNaoEnviadoFavoritos,
    montarHistoricoAlteracoesFavoritosPayload,
    salvarHistoricoAlteracoesFavoritosProcesso
  });
  internal.components.add('07-history');
})(window);
