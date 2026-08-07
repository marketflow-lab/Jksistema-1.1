(function (global) {
  'use strict';

  const feature = global.FavoritosV2.promotionEffectuation;
  const internal = feature.internal;
  const state = feature.runtime.state;
  const adapters = feature.runtime.adapters;
  if (internal.components.has('12-batch-execution')) return;

  function atualizarPainelEfetivarFavoritos() {
      if (!state.favMlEfetivarPanelEl || !state.favMlEfetivarInfoEl || !state.favMlEfetivarBtnEl) return;
      const sku = String(state.favMlSkuSelecionado || '').trim();
      const lista = Array.isArray(state.favMlAnunciosSkuAtual) ? state.favMlAnunciosSkuAtual : [];
      const rankingSelecionado = sku ? adapters.obterRankingFavoritosParaSimulador(sku) : [];
      const temRankingSelecionado = rankingSelecionado.length > 0;
      if (!sku || (!lista.length && !temRankingSelecionado)) {
          state.favMlEfetivarPanelEl.classList.add('hidden');
          if (state.favMlPromocaoBtnEl) state.favMlPromocaoBtnEl.classList.add('hidden');
          return;
      }
      state.favMlEfetivarPanelEl.classList.remove('hidden');
      const opcoesPromocao = internal.obterOpcoesPromocaoFavoritosSku(sku);
      const promocaoPronta = internal.opcoesPromocaoFavoritosProntas(opcoesPromocao);
      if (!state.favMlEfetivacaoEmExecucao) state.favMlEfetivarBtnEl.textContent = 'Aprovar e alterar';
      if (state.favMlPromocaoBtnEl) {
          state.favMlPromocaoBtnEl.classList.remove('hidden');
          state.favMlPromocaoBtnEl.disabled = state.favMlEfetivacaoEmExecucao || !sku;
          state.favMlPromocaoBtnEl.textContent = promocaoPronta ? 'Alterar campanha/%' : 'Campanha e %';
      }
      if (state.favMlEfetivacaoEmPreparacao) {
          state.favMlEfetivarInfoEl.textContent = 'Validando anuncios e preparando a confirmacao...';
          state.favMlEfetivarBtnEl.disabled = true;
          state.favMlEfetivarBtnEl.textContent = 'Validando...';
          if (state.favMlPromocaoBtnEl) state.favMlPromocaoBtnEl.disabled = true;
          return;
      }
      const registros = lista.length
          ? internal.obterRegistrosSimulacaoFavoritos(lista, sku, opcoesPromocao, state.favMlSimulacoesSkuAtual)
          : [];
      const registrosSelecionados = internal.filtrarRegistrosSelecionadosAlteracaoFavoritos(registros, sku);
      const totalDesmarcados = registros.length - registrosSelecionados.length;
      const validos = internal.filtrarRegistrosEfetivaveisFavoritos(registrosSelecionados);
      const invalidos = registrosSelecionados.filter(registro => !validos.includes(registro));
      if (!promocaoPronta) {
          state.favMlEfetivarInfoEl.textContent = 'Ranking alinhado. Escolha campanha e % agora, ou clique em Aprovar e alterar para informar antes de enviar ao Mercado Livre.';
          state.favMlEfetivarBtnEl.disabled = state.favMlEfetivacaoEmExecucao || !temRankingSelecionado;
          return;
      }
      if (!lista.length) {
          const origemRanking = state.favMlHistoricoExecucaoSelecionadaId === state.FAV_ML_RANKING_ATUAL_ID
              ? 'Ranking atual'
              : 'Ranking do historico';
          state.favMlEfetivarInfoEl.textContent = `${origemRanking} habilitado para aprovar e alterar. Clique para buscar os anuncios do SKU e simular.`;
          state.favMlEfetivarBtnEl.disabled = state.favMlEfetivacaoEmExecucao || !temRankingSelecionado;
          return;
      }
      const exemploErro = invalidos.find(registro => registro && registro.sim && !registro.sim.ok);
      const motivo = exemploErro && exemploErro.sim && exemploErro.sim.status
          ? ` Linhas sem valor: ${internal.textoCurtoStatusSimuladorFavoritos(exemploErro.sim.status)}.`
          : '';
      if (!registrosSelecionados.length && registros.length) {
          state.favMlEfetivarInfoEl.textContent = `Nenhum anuncio selecionado para alterar. Marque ao menos um anuncio.${totalDesmarcados ? ` ${totalDesmarcados} desmarcado(s).` : ''}`;
          state.favMlEfetivarBtnEl.disabled = true;
          return;
      }
      const textoDesmarcados = totalDesmarcados ? ` ${totalDesmarcados} desmarcado(s) nao serao alterados.` : '';
      state.favMlEfetivarInfoEl.textContent = validos.length
          ? `${validos.length} anuncio(s) selecionado(s) com simulacao pronta para aprovar.${textoDesmarcados}${motivo}`
          : `Nenhum anuncio selecionado com simulacao valida para aprovar.${textoDesmarcados}${motivo}`;
      state.favMlEfetivarBtnEl.disabled = state.favMlEfetivacaoEmExecucao || !validos.length;
  }

  function falharPreparacaoEfetivacaoFavoritos(mensagem, opcoes = {}) {
      window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(mensagem, {
          erro: true,
          larga: opcoes.larga !== false,
          tempoMs: opcoes.tempoMs
      });
      internal.atualizarPainelEfetivarFavoritos();
      return null;
  }

  async function prepararLoteEfetivacaoFavoritos(sku, opcoesPromocao) {
      const incluirOutrasContas = !!(state.favMlEfetivarOutrasContasEl && state.favMlEfetivarOutrasContasEl.checked);
      let anuncios = Array.isArray(state.favMlAnunciosSkuAtual) ? state.favMlAnunciosSkuAtual : [];
      let cache = state.favMlSimulacoesSkuAtual;
      let buscouAnunciosAoAprovar = false;
      if (incluirOutrasContas || !anuncios.length) {
          const mensagemBusca = incluirOutrasContas
              ? 'Buscando anuncios deste SKU em todas as contas integradas...'
              : 'Buscando anuncios deste SKU para aprovar o ranking selecionado...';
          window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(mensagemBusca, { larga: true });
          try {
              anuncios = await internal.carregarFavoritosAnunciosSkuTodasContas(sku);
          } catch (err) {
              return internal.falharPreparacaoEfetivacaoFavoritos(`Erro ao buscar anuncios do SKU: ${err && err.message ? err.message : err}`);
          }
          cache = null;
          buscouAnunciosAoAprovar = true;
      }
      const lojaAtualNorm = adapters.skuNormalizarLoja(adapters.favoritosLojaSelecionadaParaApi(state.favMlLojaSelecionada || ''));
      let registros = internal.obterRegistrosSimulacaoFavoritos(anuncios, sku, opcoesPromocao, cache);
      const registrosAntesFiltroLoja = registros;
      if (!incluirOutrasContas && lojaAtualNorm) {
          registros = registros.filter(registro => adapters.skuNormalizarLoja(registro.loja) === lojaAtualNorm);
      }
      const registrosAntesFiltroSelecao = registros;
      registros = internal.filtrarRegistrosSelecionadosAlteracaoFavoritos(registros, sku);
      if (!registros.length && registrosAntesFiltroSelecao.length) {
          return internal.falharPreparacaoEfetivacaoFavoritos(
              'Nenhum anuncio selecionado para alterar. Marque ao menos um anuncio na coluna Alterar.',
              { tempoMs: 7500 }
          );
      }
      let validos = internal.filtrarRegistrosEfetivaveisFavoritos(registros);
      if (!validos.length) {
          const motivo = internal.explicarRegistrosNaoEfetivaveisFavoritos(registros, registrosAntesFiltroLoja);
          return internal.falharPreparacaoEfetivacaoFavoritos(`Nenhum anuncio com simulacao valida para aprovar. ${motivo}`, { tempoMs: 7500 });
      }
      let bloqueadosPreEnvio = [];
      try {
          const validacaoEnvio = await internal.validarRegistrosEfetivaveisFavoritosMercadoLivre(validos);
          validos = validacaoEnvio.validos || [];
          bloqueadosPreEnvio = validacaoEnvio.bloqueados || [];
      } catch (err) {
          return internal.falharPreparacaoEfetivacaoFavoritos(
              `Erro ao validar anuncios no Mercado Livre antes de alterar: ${err && err.message ? err.message : err}`,
              { tempoMs: 8000 }
          );
      }
      if (!validos.length) {
          const primeiroBloqueio = bloqueadosPreEnvio[0];
          const motivo = primeiroBloqueio && primeiroBloqueio.erro
              ? ` Primeiro bloqueio em ${primeiroBloqueio.itemId}: ${primeiroBloqueio.erro}`
              : ' Nenhum MLB passou na validacao final do Mercado Livre.';
          return internal.falharPreparacaoEfetivacaoFavoritos(`Nenhum anuncio valido para enviar ao Mercado Livre.${motivo}`, { tempoMs: 9000 });
      }
      return { sku, opcoesPromocao, incluirOutrasContas, buscouAnunciosAoAprovar, validos, bloqueadosPreEnvio };
  }

  async function confirmarLoteEfetivacaoFavoritos(contexto) {
      const nomeCampanha = contexto.opcoesPromocao.campanha
          && (contexto.opcoesPromocao.campanha.nome || contexto.opcoesPromocao.campanha.id)
          || 'campanha selecionada';
      return internal.perguntarConfirmacaoEfetivarFavoritos({
          total: contexto.validos.length,
          incluirOutrasContas: contexto.incluirOutrasContas,
          buscouAnunciosAoAprovar: contexto.buscouAnunciosAoAprovar,
          nomeCampanha,
          totalTrocaTipo: contexto.validos.filter(registro => internal.registroFavoritosExigeTrocaTipoAnuncio(registro)).length,
          totalTrocaAposPromocao: contexto.validos.filter(registro => registro && registro.sim && registro.sim.trocaTipoAposPromocaoMl).length
      });
  }

  function iniciarLoteEfetivacaoFavoritos(contexto) {
      internal.limparLogEfetivarFavoritos();
      internal.adicionarStatusEfetivarFavoritos('info', 'Iniciando alteracoes no Mercado Livre', `${contexto.validos.length} anuncio(s) seguirao a sequencia: remover promocao atual, ajustar tipo quando necessario, confirmar o preco cheio calculado, aplicar campanha/percentual e verificar os precos observados. Se a campanha nao for mantida ou a margem ficar insegura, sera tentado fallback direto sem promocao.`);
      if (state.favMlEfetivarBtnEl) {
          state.favMlEfetivarBtnEl.disabled = true;
          state.favMlEfetivarBtnEl.textContent = 'Alterando...';
      }
      const resultados = {
          sucessos: [],
          pendentes: [],
          naoEnviados: [],
          interrompidoPorSeguranca: false
      };
      resultados.falhas = contexto.bloqueadosPreEnvio.map(item => ({
          itemId: item.itemId,
          erro: item.erro,
          registro: item.registro,
          resultado: item.validacao || null
      }));
      contexto.bloqueadosPreEnvio.forEach(item => {
              internal.adicionarStatusEfetivarFavoritos(
                  'error',
                  `${item.itemId} nao foi enviado ao Mercado Livre`,
                  item.erro || 'Mercado Livre nao disponibiliza essa troca Premium/Classico para o anuncio agora.'
              );
      });
      contexto.validos.filter(registro => registro && registro.sim && registro.sim.trocaTipoAposPromocaoMl).forEach(registro => {
              internal.adicionarStatusEfetivarFavoritos(
                  'info',
                  `${registro.itemId} tentara trocar tipo direto no Mercado Livre`,
                  `${registro.sim.trocaTipoAposPromocaoMotivo || 'Mercado Livre nao listou a troca em available_*.'} O sistema vai sair apenas da promocao ativa quando existir e tentar a troca Premium/Classico pelo endpoint oficial.`
              );
      });
      return resultados;
  }

  function registrarSucessoLoteFavoritos(registro, data, resultados) {
      const itemId = registro.itemId;
      resultados.sucessos.push({ itemId, data, registro });
      const observados = internal.obterPrecosObservadosEfetivacaoFavoritos(data || {});
      if (data && data.fallback_sem_promocao_aplicado) {
          const margemFallback = data.margem_estimada_contingencia !== null && data.margem_estimada_contingencia !== undefined
              ? ` | Margem estimada: ${adapters.formatarMargemAnuncioFavoritos(data.margem_estimada_contingencia)}`
              : '';
          internal.adicionarStatusEfetivarFavoritos(
              'info',
              `${itemId} ajustado sem campanha (fallback)`,
              `${observados.precoDiretoFallback !== null ? `Preco direto observado sem promocao: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(observados.precoDiretoFallback)}` : 'Fallback concluido sem preco observado no retorno'} | Referencia ranking: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(data.preco_ranking_referencia)}${margemFallback} | Motivo: ${internal.normalizarErroEfetivacaoFavoritos(data.fallback_motivo || data.message || '')}`
          );
          return;
      }
      const tipoUpdate = data && data.listing_type_update;
      const tipoTxt = tipoUpdate && tipoUpdate.target_name
          ? ` | Tipo: ${tipoUpdate.current_name || '-'} -> ${tipoUpdate.target_name}${tipoUpdate.changed ? '' : ' (ja estava)'}`
          : '';
      internal.adicionarStatusEfetivarFavoritos(
          'success',
          `${itemId} com sequencia concluida`,
          `${observados.precoCheio !== null ? `Preco cheio observado: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(observados.precoCheio)}` : `Preco cheio solicitado: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(data && data.preco_anuncio)} (sem observado no retorno)`} | ${observados.precoPromocional !== null ? `Preco promocional observado: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(observados.precoPromocional)}` : `Preco promocional solicitado: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(data && data.preco_promocional)} (sem observado no retorno)`} | Campanha: ${(data && (data.campanha_nome || data.promotion_id)) || '-'}${tipoTxt}`
      );
  }

  function registrarFalhaLoteFavoritos(registro, err, resultados) {
      const itemId = registro.itemId;
      const resultado = err && err.favoritosResultado && typeof err.favoritosResultado === 'object' ? err.favoritosResultado : null;
      const erroTxt = internal.normalizarErroEfetivacaoFavoritos(resultado && resultado.message || err);
      const terminal = internal.resultadoEfetivacaoTerminalFavoritos(resultado);
      const seguranca = internal.normalizarSegurancaComercialEfetivacaoFavoritos(resultado);
      if (resultado && typeof resultado === 'object') {
          resultado.commercial_safety = seguranca;
      }
      resultados.falhas.push({ itemId, erro: erroTxt, registro, resultado });
      const promocaoDivergenteAtiva = seguranca.state === 'unsafe' && seguranca.promotion_active;
      internal.adicionarStatusEfetivarFavoritos(
          'error',
          `${itemId} ${promocaoDivergenteAtiva ? 'ficou com promoção divergente ainda ativa' : (internal.resultadoEfetivacaoIncertoFavoritos(resultado) ? 'ficou com estado remoto incerto' : (internal.resultadoEfetivacaoParcialFavoritos(resultado) ? 'teve alteracao parcial' : 'nao foi alterado'))}${terminal ? ' - bloqueio terminal' : ''}`,
          terminal
              ? `${erroTxt || 'O Mercado Livre recusou a alteracao.'} Corrija a restricao no Mercado Livre antes de aprovar novamente; nao ha repeticao automatica.`
              : (promocaoDivergenteAtiva
                  ? `Promoção divergente ainda ativa. ${seguranca.reason || erroTxt || 'O fallback seguro não foi confirmado.'} O restante do lote não será enviado.`
                  : (erroTxt || seguranca.reason || 'O Mercado Livre recusou a alteracao sem detalhar o motivo.'))
      );
      return {
          interromperLote: internal.resultadoEfetivacaoRequerInterrupcaoLoteFavoritos(resultado),
          seguranca,
          motivo: seguranca.reason || erroTxt || 'Estado comercial remoto inseguro.'
      };
  }

  function registrarNaoEnviadosPorSegurancaFavoritos(contexto, resultados, indiceInicial, controle, itemBloqueador) {
      const motivo = internal.normalizarErroEfetivacaoFavoritos(
          controle && controle.motivo
          || 'O lote foi interrompido porque o estado comercial do anúncio anterior não pôde ser confirmado com segurança.'
      );
      const restantes = contexto.validos.slice(indiceInicial);
      restantes.forEach(registro => {
          resultados.naoEnviados.push({
              itemId: registro.itemId,
              registro,
              naoEnviado: true,
              motivo,
              bloqueadoPor: itemBloqueador || ''
          });
      });
      if (restantes.length) {
          internal.adicionarStatusEfetivarFavoritos(
              'warning',
              `${restantes.length} anúncio(s) não enviado(s) por bloqueio de segurança`,
              `${motivo}${itemBloqueador ? ` Anúncio que acionou o bloqueio: ${itemBloqueador}.` : ''}`
          );
      }
  }

  async function processarRegistroLoteFavoritos(contexto, resultados, registro, indice) {
      const itemId = registro.itemId;
      let mutacaoIniciada = false;
      window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Alterando ${indice + 1}/${contexto.validos.length}: ${itemId}...`, { larga: true });
      if (state.favMlEfetivarInfoEl) state.favMlEfetivarInfoEl.textContent = `Alterando ${indice + 1}/${contexto.validos.length}: ${itemId}.`;
              internal.adicionarStatusEfetivarFavoritos(
                  'info',
          `${indice + 1}/${contexto.validos.length} - Enviando alteracao para ${itemId}`,
                  `Conta: ${registro.loja || '-'} | Tipo: ${internal.textoTipoEnvioFavoritos(registro)} | Preco cheio: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(registro.sim && registro.sim.preco)} | Preco final promocional: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(registro.sim && (registro.sim.precoPromocionalCalculado ?? registro.sim.precoPromocional ?? registro.sim.precoCompetitivo))}${registro.sim && registro.sim.ajustePrecoUnico ? ` | Ajuste preco unico: +${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(registro.sim.ajustePrecoUnico)}` : ''}`
              );
              try {
          const opcoesLoja = await internal.resolverOpcoesPromocaoEfetivacaoParaLoja(contexto.opcoesPromocao, registro.loja);
                  mutacaoIniciada = true;
                  const data = await internal.efetivarFavoritoMercadoLivre(
                      registro.anuncio,
                      registro.ranking,
                      registro.sim,
                      opcoesLoja,
                      null,
                      {
                          confirmar: false,
                          mostrarStatus: false,
                          atualizarStatus: false,
                          recarregar: false,
                          propagarErro: true
                      }
                  );
                  if (data && data.outcome === 'pending_review') {
              resultados.pendentes.push({ itemId, data, registro });
                      internal.adicionarStatusEfetivarFavoritos(
                          'warning',
                          `${itemId} pendente de revisao no Mercado Livre`,
                          internal.descreverPendenteEfetivacaoFavoritos({ itemId, data, registro })
                      );
              return { interromperLote: false };
                  }
          if (internal.resultadoEfetivacaoRequerInterrupcaoLoteFavoritos(data)) {
              const erroSeguranca = new Error(
                  internal.normalizarSegurancaComercialEfetivacaoFavoritos(data).reason
                  || 'O retorno da alteração não comprovou um estado comercial seguro.'
              );
              erroSeguranca.favoritosResultado = data;
              throw erroSeguranca;
          }
          internal.registrarSucessoLoteFavoritos(registro, data, resultados);
          return { interromperLote: false };
              } catch (err) {
          const resultadoErro = err && err.favoritosResultado && typeof err.favoritosResultado === 'object'
              ? err.favoritosResultado
              : null;
          const segurancaErro = resultadoErro && resultadoErro.commercial_safety && typeof resultadoErro.commercial_safety === 'object'
              ? resultadoErro.commercial_safety
              : null;
          const segurancaAutoritativa = !!(
              segurancaErro
              && (
                  ['safe', 'unsafe', 'unknown'].includes(String(segurancaErro.state || '').trim().toLowerCase())
                  || segurancaErro.batch_abort_required === true
              )
          );
          const etapasErro = resultadoErro && resultadoErro.stages && typeof resultadoErro.stages === 'object'
              ? resultadoErro.stages
              : null;
          const etapasAutoritativas = !!(
              etapasErro
              && Object.values(etapasErro).some(etapa => {
                  if (etapa && typeof etapa === 'object') return !!String(etapa.status || '').trim();
                  return !!String(etapa || '').trim();
              })
          );
          const resultadoAutoritativo = !!(
              resultadoErro
              && (
                  String(resultadoErro.outcome || '').trim()
                  || String(resultadoErro.operation_state || '').trim()
                  || segurancaAutoritativa
                  || etapasAutoritativas
                  || resultadoErro.stop_batch === true
              )
          );
          if (mutacaoIniciada && !resultadoAutoritativo) {
              const mensagem = internal.normalizarErroEfetivacaoFavoritos(err || 'A resposta da alteração não pôde ser reconciliada.');
              const erroMutacaoIncerta = err instanceof Error ? err : new Error(mensagem);
              erroMutacaoIncerta.favoritosResultado = {
                  success: false,
                  completed: false,
                  outcome: 'partial_unknown',
                  operation_state: 'partial_unknown',
                  message: mensagem,
                  item_id: itemId,
                  loja: registro.loja || '',
                  commercial_safety: {
                      state: 'unknown',
                      reason: 'A requisição de alteração foi iniciada, mas o estado comercial remoto não pôde ser confirmado.',
                      batch_abort_required: true,
                      promotion_active: false,
                      active_promotions: [],
                      sale_price_has_promotion: false,
                      sale_price: null,
                      observed_discount_pct: null,
                      stable_reads: null,
                      required_stable_reads: null
                  }
              };
              err = erroMutacaoIncerta;
          }
          return internal.registrarFalhaLoteFavoritos(registro, err, resultados);
              }
  }

  async function processarLoteEfetivacaoFavoritos(contexto, resultados) {
      let protecaoElectronAtiva = await internal.definirProtecaoAutomacaoMlFavoritos(true, 'favoritos-efetivar-preco');
      if (protecaoElectronAtiva) {
          internal.adicionarStatusEfetivarFavoritos('info', 'Protecao ativada', 'Atualizacoes automaticas serao adiadas ate terminar a alteracao no Mercado Livre.');
      }
      try {
          for (let indice = 0; indice < contexto.validos.length; indice += 1) {
              const registro = contexto.validos[indice];
              const controle = await internal.processarRegistroLoteFavoritos(contexto, resultados, registro, indice);
              if (controle && controle.interromperLote) {
                  resultados.interrompidoPorSeguranca = true;
                  internal.registrarNaoEnviadosPorSegurancaFavoritos(
                      contexto,
                      resultados,
                      indice + 1,
                      controle,
                      registro && registro.itemId
                  );
                  break;
              }
          }
          if (resultados.sucessos.length || resultados.pendentes.length || resultados.falhas.length) {
              await adapters.carregarFavoritosAnunciosSku(contexto.sku, state.favMlLojaSelecionada);
          }
      } finally {
          if (protecaoElectronAtiva) await internal.definirProtecaoAutomacaoMlFavoritos(false, 'favoritos-efetivar-preco');
      }
  }

  function finalizarMensagemLoteFavoritos(resultados) {
      const { sucessos, pendentes, falhas } = resultados;
      const naoEnviados = Array.isArray(resultados.naoEnviados) ? resultados.naoEnviados : [];
          const totalFallbackSemCampanha = sucessos.filter(item => item && item.data && item.data.fallback_sem_promocao_aplicado).length;
          const totalBloqueiosTerminais = falhas.filter(item => internal.resultadoEfetivacaoTerminalFavoritos(item && item.resultado)).length;
          const totalEstadosIncertos = falhas.filter(item => internal.resultadoEfetivacaoIncertoFavoritos(item && item.resultado)).length;
      let mensagemFinalEfetivacao = '';
      if (falhas.length) {
              const primeiraFalha = falhas[0];
              const txtFallback = totalFallbackSemCampanha ? ` ${totalFallbackSemCampanha} concluido(s) em fallback sem campanha.` : '';
              const txtPendentes = pendentes.length ? ` ${pendentes.length} pendente(s) de revisao.` : '';
              const txtTerminais = totalBloqueiosTerminais
                  ? ` ${totalBloqueiosTerminais} bloqueio(s) terminal(is); corrija no Mercado Livre antes de aprovar novamente. Nao ha repeticao automatica.`
                  : '';
              const txtIncertos = totalEstadosIncertos
                  ? ` ${totalEstadosIncertos} estado(s) remoto(s) incerto(s); confira esses anuncios antes de aprovar novamente.`
                  : '';
              const txtNaoEnviados = naoEnviados.length
                  ? ` ${naoEnviados.length} não enviado(s) por bloqueio de segurança.`
                  : '';
              mensagemFinalEfetivacao = `${sucessos.length} favorito(s) feito(s). ${falhas.length} nao concluido(s).${txtPendentes}${txtFallback}${txtIncertos}${txtTerminais}${txtNaoEnviados} Primeiro erro em ${primeiraFalha.itemId}: ${primeiraFalha.erro}`;
              window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(mensagemFinalEfetivacao, {
                  erro: true,
                  larga: true
              });
              if (state.favMlStatusEl) state.favMlStatusEl.textContent = `${sucessos.length} favorito(s) feito(s); ${pendentes.length} pendente(s); ${falhas.length} nao concluido(s); ${naoEnviados.length} não enviado(s); ${totalEstadosIncertos} incerto(s); ${totalFallbackSemCampanha} fallback(s) sem campanha.`;
              internal.adicionarStatusEfetivarFavoritos('error', resultados.interrompidoPorSeguranca ? 'Processo interrompido por segurança' : 'Processo concluido com falhas', `Sucesso: ${sucessos.length}. Pendentes: ${pendentes.length}. Nao concluidos: ${falhas.length}. Não enviados: ${naoEnviados.length}. Estados remotos incertos: ${totalEstadosIncertos}. Fallback sem campanha: ${totalFallbackSemCampanha}. Veja acima o estado de cada MLB.`);
      } else if (pendentes.length) {
              mensagemFinalEfetivacao = `${sucessos.length} favorito(s) feito(s). ${pendentes.length} anuncio(s) ficaram pendentes de revisao no Mercado Livre; o preco e a nova campanha desses anuncios nao foram enviados.`;
              window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(mensagemFinalEfetivacao, {
                  aviso: true,
                  larga: true
              });
              if (state.favMlStatusEl) state.favMlStatusEl.textContent = `${sucessos.length} favorito(s) feito(s); ${pendentes.length} pendente(s) de revisao.`;
              internal.adicionarStatusEfetivarFavoritos('warning', 'Processo aguardando revisao', `${pendentes.length} anuncio(s) devem voltar a ativo antes de uma nova aprovacao. Nao ha repeticao automatica.`);
      } else {
              const txtFallback = totalFallbackSemCampanha ? ` (${totalFallbackSemCampanha} em fallback sem campanha)` : '';
              mensagemFinalEfetivacao = `Tudo certo: ${sucessos.length} favorito(s) feito(s) no Mercado Livre${txtFallback}.`;
              window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(mensagemFinalEfetivacao, {
                  tempoMs: 7500
              });
              if (state.favMlStatusEl) state.favMlStatusEl.textContent = `${sucessos.length} favorito(s) feito(s) no Mercado Livre${txtFallback}.`;
              if (totalFallbackSemCampanha) {
                  internal.adicionarStatusEfetivarFavoritos('info', 'Processo concluido', `${sucessos.length} anuncio(s) ajustado(s). ${totalFallbackSemCampanha} em fallback direto sem promocao apos campanha nao mantida ou protecao de margem.`);
              } else {
                  internal.adicionarStatusEfetivarFavoritos('success', 'Processo concluido', `${sucessos.length} anuncio(s) alterado(s), com promocao aplicada e conferida no Mercado Livre.`);
              }
      }
      return mensagemFinalEfetivacao;
  }

  async function persistirHistoricoLoteFavoritos(contexto, resultados, mensagemFinalEfetivacao) {
      let historicoAlteracoesResultado = null;
      try {
          const historicoAlteracoes = internal.montarHistoricoAlteracoesFavoritosPayload({
                  sku: contexto.sku,
                  opcoesPromocao: contexto.opcoesPromocao,
                  incluirOutrasContas: contexto.incluirOutrasContas,
                  validos: contexto.validos,
                  bloqueadosPreEnvio: contexto.bloqueadosPreEnvio,
                  sucessos: resultados.sucessos,
                  pendentes: resultados.pendentes,
                  falhas: resultados.falhas,
                  mensagemFinal: mensagemFinalEfetivacao,
                  naoEnviados: resultados.naoEnviados
              });
              historicoAlteracoesResultado = historicoAlteracoes;
              const persistenciaHistorico = await internal.salvarHistoricoAlteracoesFavoritosProcesso(historicoAlteracoes);
              if (persistenciaHistorico && persistenciaHistorico.confirmado === true) {
                  internal.adicionarStatusEfetivarFavoritos(
                      'info',
                      'Historico de favoritos confirmado',
                      `${historicoAlteracoes.vinculos.length} vinculo(s) e relatorio(s) da alteracao foram salvos para consulta.`
                  );
              } else if (persistenciaHistorico && (persistenciaHistorico.entrada || persistenciaHistorico.id)) {
                  internal.adicionarStatusEfetivarFavoritos(
                      'warning',
                      'Historico mantido no cache local',
                      persistenciaHistorico.erro || 'O servidor ainda nao confirmou o salvamento; o registro permanece pendente de confirmacao.'
                  );
              }
      } catch (err) {
              console.warn('Nao foi possivel salvar o historico estatico das alteracoes de favoritos:', err);
              internal.adicionarStatusEfetivarFavoritos(
                  'error',
                  'Historico de favoritos nao salvo',
                  err && err.message ? err.message : String(err || 'Falha desconhecida ao salvar historico.')
              );
      }
      return historicoAlteracoesResultado;
  }

  async function efetivarFavoritosMercadoLivreAprovados() {
      if (state.favMlEfetivacaoEmExecucao || state.favMlEfetivacaoEmPreparacao) return;
      state.favMlEfetivacaoEmPreparacao = true;
      try {
          internal.atualizarPainelEfetivarFavoritos();
          const sku = String(state.favMlSkuSelecionado || '').trim();
          if (!sku) return internal.falharPreparacaoEfetivacaoFavoritos('Selecione um SKU antes de aprovar as alteracoes.', { tempoMs: 6000, larga: false });
          const opcoesPromocao = await internal.garantirPromocaoFavoritosSkuAtual(sku);
          if (!opcoesPromocao) return;
          const contexto = await internal.prepararLoteEfetivacaoFavoritos(sku, opcoesPromocao);
          if (!contexto || !await internal.confirmarLoteEfetivacaoFavoritos(contexto)) return;
          state.favMlEfetivacaoEmExecucao = true;
          state.favMlEfetivacaoEmPreparacao = false;
          const resultados = internal.iniciarLoteEfetivacaoFavoritos(contexto);
          await internal.processarLoteEfetivacaoFavoritos(contexto, resultados);
          const mensagemFinal = internal.finalizarMensagemLoteFavoritos(resultados);
          const historico = await internal.persistirHistoricoLoteFavoritos(contexto, resultados, mensagemFinal);
          internal.renderizarComparativoEfetivacaoFavoritos(
              resultados.sucessos,
              mensagemFinal,
              resultados.pendentes,
              resultados.falhas,
              historico,
              resultados.naoEnviados
          );
          internal.agendarOcultarStatusEfetivarFavoritos(5000);
      } finally {
          state.favMlEfetivacaoEmExecucao = false;
          if (state.favMlEfetivarBtnEl) state.favMlEfetivarBtnEl.textContent = 'Aprovar e alterar';
          state.favMlEfetivacaoEmPreparacao = false;
          internal.atualizarPainelEfetivarFavoritos();
      }
  }

  Object.assign(internal, {
    atualizarPainelEfetivarFavoritos,
    falharPreparacaoEfetivacaoFavoritos,
    prepararLoteEfetivacaoFavoritos,
    confirmarLoteEfetivacaoFavoritos,
    iniciarLoteEfetivacaoFavoritos,
    registrarSucessoLoteFavoritos,
    registrarFalhaLoteFavoritos,
    registrarNaoEnviadosPorSegurancaFavoritos,
    processarRegistroLoteFavoritos,
    processarLoteEfetivacaoFavoritos,
    finalizarMensagemLoteFavoritos,
    persistirHistoricoLoteFavoritos,
    efetivarFavoritosMercadoLivreAprovados
  });
  internal.components.add('12-batch-execution');
})(window);
