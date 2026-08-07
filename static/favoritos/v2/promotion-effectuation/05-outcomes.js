(function (global) {
  'use strict';

  const feature = global.FavoritosV2.promotionEffectuation;
  const internal = feature.internal;
  const state = feature.runtime.state;
  const adapters = feature.runtime.adapters;
  if (internal.components.has('05-outcomes')) return;

  function textoCurtoStatusSimuladorFavoritos(status) {
      const texto = String(status || '').trim();
      if (!texto) return 'Sem simulacao';
      if (/faltando/i.test(texto)) return texto;
      if (/margem minima|15%|abaixo de 15/i.test(texto)) return 'Limite de margem 15%';
      if (/sem preco/i.test(texto)) return 'Sem preco do ranking';
      if (/sem % fixa/i.test(texto)) return 'Promocao sem %';
      return texto.length > 42 ? `${texto.slice(0, 39)}...` : texto;
  }

  function normalizarErroEfetivacaoFavoritos(valor, limite = 1100) {
      if (valor === null || valor === undefined) return '';
      if (typeof valor === 'string') {
          return valor.length > limite ? `${valor.slice(0, limite - 3)}...` : valor;
      }
      if (valor instanceof Error) {
          return internal.normalizarErroEfetivacaoFavoritos(valor.message || String(valor), limite);
      }
      if (typeof valor === 'object') {
          const detalhe = valor.detail ?? valor.message ?? valor.error ?? valor.erro;
          if (detalhe && detalhe !== valor) {
              return internal.normalizarErroEfetivacaoFavoritos(detalhe, limite);
          }
          try {
              const texto = JSON.stringify(valor, null, 2);
              return texto.length > limite ? `${texto.slice(0, limite - 3)}...` : texto;
          } catch (_) {
              return String(valor);
          }
      }
      return String(valor);
  }

  function limparLogEfetivarFavoritos() {
      if (!state.favMlEfetivarLogEl) return;
      internal.limparTimerOcultarStatusEfetivarFavoritos();
      state.favMlEfetivarLogEl.innerHTML = '';
      state.favMlEfetivarLogEl.classList.add('hidden');
  }

  function limparTimerOcultarStatusEfetivarFavoritos() {
      if (!state.favMlEfetivarLogHideTimer) return;
      clearTimeout(state.favMlEfetivarLogHideTimer);
      state.favMlEfetivarLogHideTimer = null;
  }

  function agendarOcultarStatusEfetivarFavoritos(delayMs = 5000) {
      internal.limparTimerOcultarStatusEfetivarFavoritos();
      state.favMlEfetivarLogHideTimer = setTimeout(() => {
          state.favMlEfetivarLogHideTimer = null;
          if (!state.favMlEfetivarLogEl) return;
          state.favMlEfetivarLogEl
              .querySelectorAll('.ml-favoritos-efetivar-log-row:not(.is-comparison)')
              .forEach(row => row.remove());
          const temConteudo = !!state.favMlEfetivarLogEl.querySelector('.ml-favoritos-efetivar-log-row');
          state.favMlEfetivarLogEl.classList.toggle('hidden', !temConteudo);
      }, Math.max(0, Number(delayMs) || 0));
  }

  function adicionarStatusEfetivarFavoritos(tipo, titulo, detalhe = '') {
      if (!state.favMlEfetivarLogEl) return;
      internal.limparTimerOcultarStatusEfetivarFavoritos();
      state.favMlEfetivarLogEl.classList.remove('hidden');
      const row = document.createElement('div');
      row.className = `ml-favoritos-efetivar-log-row is-${tipo || 'info'}`;
      const body = document.createElement('div');
      const titleEl = document.createElement('div');
      titleEl.className = 'ml-favoritos-efetivar-log-title';
      titleEl.textContent = titulo || 'Status';
      body.appendChild(titleEl);
      const detalheTxt = internal.normalizarErroEfetivacaoFavoritos(detalhe);
      if (detalheTxt) {
          const detailEl = document.createElement('div');
          detailEl.className = 'ml-favoritos-efetivar-log-detail';
          detailEl.textContent = detalheTxt;
          body.appendChild(detailEl);
      }
      row.appendChild(body);
      state.favMlEfetivarLogEl.appendChild(row);
      state.favMlEfetivarLogEl.scrollTop = state.favMlEfetivarLogEl.scrollHeight;
  }

  function textoPrecoComparacaoFavoritos(valor) {
      const numero = adapters.parsePrecoAnuncioFavoritos(valor);
      return numero !== null ? window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(numero) : '-';
  }

  function resumoPrecosComparacaoFavoritos(anuncio, substitutos = {}) {
      const precos = adapters.obterPrecosAnuncioFavoritos(anuncio);
      const temPrecoSubstituto = Object.prototype.hasOwnProperty.call(substitutos, 'preco');
      const temPromocionalSubstituto = Object.prototype.hasOwnProperty.call(substitutos, 'promocional');
      const precoSubstituto = adapters.parsePrecoAnuncioFavoritos(substitutos.preco);
      const promocionalSubstituto = adapters.parsePrecoAnuncioFavoritos(substitutos.promocional);
      const preco = temPrecoSubstituto
          ? precoSubstituto
          : (precos.preco ?? precos.promocional);
      const promocional = temPromocionalSubstituto
          ? promocionalSubstituto
          : precos.promocional;
      return { preco, promocional };
  }

  function deveMostrarCustoIdealComparacaoFavoritos(item) {
      const registro = item && item.registro || {};
      const sim = registro.sim || {};
      if (!sim.ok || !sim.limiteMargemAplicado || sim.custoIdealAbaixoBase === null || sim.custoIdealAbaixoBase === undefined) {
          return false;
      }
      const data = item && item.data || {};
      const precoBase = adapters.obterPrecoVigenteAnuncioFavoritos(registro.ranking);
      const precoNosso = adapters.parsePrecoAnuncioFavoritos(data.preco_promocional)
          ?? adapters.parsePrecoAnuncioFavoritos(data.preco_anuncio)
          ?? adapters.obterPrecoFinalSimulacaoFavoritos(sim);
      if (precoBase === null || precoNosso === null) return true;
      return precoNosso >= precoBase - 0.0001 || Number(sim.descontoEfetivo || 0) < 0.01;
  }

  function textoCustoIdealComparacaoFavoritos(sim) {
      if (!sim || sim.custoIdealAbaixoBase === null || sim.custoIdealAbaixoBase === undefined) return '-';
      const ideal = adapters.parsePrecoAnuncioFavoritos(sim.custoIdealAbaixoBase);
      const alvo = adapters.parsePrecoAnuncioFavoritos(sim.precoAlvoCustoIdeal);
      if (ideal === null || alvo === null) return '-';
      if (ideal < 0) {
          return `Inviavel: custo teria que ser ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(ideal)} (alvo ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(alvo)})`;
      }
      const reducao = adapters.parsePrecoAnuncioFavoritos(sim.reducaoCustoIdeal);
      const partes = [
          window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(ideal),
          `alvo ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(alvo)}`
      ];
      if (reducao !== null && reducao > 0.009) {
          partes.push(`reduzir ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(reducao)}`);
      }
      return partes.join(' | ');
  }

  function textoPrecoAlvoComparacaoFavoritos(sim) {
      const alvo = adapters.parsePrecoAnuncioFavoritos(sim && sim.precoAlvoCustoIdeal);
      return alvo !== null ? window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(alvo) : '-';
  }

  function textoCustoIdealTabelaComparacaoFavoritos(sim) {
      const ideal = adapters.parsePrecoAnuncioFavoritos(sim && sim.custoIdealAbaixoBase);
      if (ideal === null) return '-';
      const reducao = adapters.parsePrecoAnuncioFavoritos(sim && sim.reducaoCustoIdeal);
      const partes = [
          ideal < 0
              ? `Inviavel: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(ideal)}`
              : window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(ideal)
      ];
      if (reducao !== null && reducao > 0.009) {
          partes.push(`reduzir ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(reducao)}`);
      }
      return partes.join(' | ');
  }

  function descreverTipoEfetivacaoFavoritos(registro, data) {
      const sim = registro && registro.sim || {};
      const update = data && data.listing_type_update || {};
      const atual = String(
          update.current_name
          || update.current_listing_type_name
          || sim.tipoAnuncioAtual
          || ''
      ).trim();
      const alvo = String(
          update.target_name
          || update.target_listing_type_name
          || sim.tipoAnuncioAlvo
          || ''
      ).trim();
      if (!atual && !alvo) return '';
      if (sim.tipoMantidoPorBloqueioMl) {
          const original = String(sim.tipoAnuncioAlvoOriginal || '').trim();
          return original
              ? `tipo mantido: ${atual || alvo} (ML bloqueou ${atual || '-'} -> ${original})`
              : `tipo mantido: ${atual || alvo}`;
      }
      if (atual && alvo && atual !== alvo) {
          const status = update.changed === false ? 'ja estava no tipo alvo' : 'tipo alterado';
          return `${status}: ${atual} -> ${alvo}`;
      }
      return `tipo: ${alvo || atual}`;
  }

  function descreverSucessoEfetivacaoFavoritos(item) {
      const data = item && item.data || {};
      const registro = item && item.registro || {};
      const sim = registro.sim || {};
      const observados = internal.obterPrecosObservadosEfetivacaoFavoritos(data);
      const partes = [];
      const loja = String((data && data.loja) || registro.loja || '').trim();
      if (loja) partes.push(`Loja: ${loja}`);
      const tipo = internal.descreverTipoEfetivacaoFavoritos(registro, data);
      if (tipo) partes.push(tipo);
      if (sim.tipoMantidoPorBloqueioMl && sim.tipoBloqueioMlMotivo) {
          partes.push(`motivo tipo: ${sim.tipoBloqueioMlMotivo}`);
      }
      if (data.fallback_sem_promocao_aplicado) {
          partes.push('campanha nao aplicada');
          if (observados.precoDiretoFallback !== null) {
              partes.push(`preco direto observado: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(observados.precoDiretoFallback)}`);
          } else {
              partes.push('preco direto sem valor observado no retorno');
          }
          const motivo = internal.normalizarErroEfetivacaoFavoritos(data.fallback_motivo || data.message || '');
          if (motivo) partes.push(`motivo: ${motivo}`);
      } else {
          if (observados.precoCheio !== null) {
              partes.push(`preco cheio observado: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(observados.precoCheio)}`);
          } else {
              partes.push(`preco cheio solicitado: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(data.preco_anuncio ?? sim.preco)} (sem valor observado no retorno)`);
          }
          if (observados.precoPromocional !== null) {
              partes.push(`preco final promocional observado: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(observados.precoPromocional)}`);
          } else {
              const precoSolicitado = data.preco_promocional ?? sim.precoPromocionalCalculado ?? sim.precoPromocional ?? sim.precoCompetitivo;
              partes.push(`preco final promocional solicitado: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(precoSolicitado)} (sem valor observado no retorno)`);
          }
          partes.push(`campanha aplicada: ${data.campanha_nome || data.promotion_id || '-'}`);
      }
      const removidas = Array.isArray(data.promocoes_removidas) ? data.promocoes_removidas.length : 0;
      if (removidas) partes.push(`${removidas} promocao(oes) anterior(es) removida(s)`);
      return partes.filter(Boolean).join(' | ');
  }

  function descreverEstadoAtualEfetivacaoFavoritos(data) {
      const estado = data && data.current_state || {};
      const partes = [];
      if (estado.status) partes.push(`status ML: ${estado.status}`);
      const subStatus = Array.isArray(estado.sub_status) ? estado.sub_status.filter(Boolean) : [];
      if (subStatus.length) partes.push(`substatus: ${subStatus.join(', ')}`);
      if (estado.listing_type_name || estado.listing_type_id) {
          partes.push(`tipo atual: ${estado.listing_type_name || estado.listing_type_id}`);
      }
      if (estado.price !== null && estado.price !== undefined) {
          partes.push(`preco atual: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(estado.price)}`);
      }
      return partes;
  }

  function descreverPendenteEfetivacaoFavoritos(item) {
      const data = item && item.data || {};
      const registro = item && item.registro || {};
      const partes = [];
      const loja = String(data.loja || registro.loja || '').trim();
      if (loja) partes.push(`Loja: ${loja}`);
      const tipoUpdate = data.listing_type_update || {};
      if (tipoUpdate.changed) {
          partes.push(`tipo alterado: ${tipoUpdate.current_name || tipoUpdate.current || '-'} -> ${tipoUpdate.target_name || tipoUpdate.target || '-'}`);
      }
      const removidas = Array.isArray(data.promocoes_removidas) ? data.promocoes_removidas.length : 0;
      if (removidas) partes.push(`${removidas} promocao(oes) anterior(es) removida(s)`);
      partes.push(...descreverEstadoAtualEfetivacaoFavoritos(data));
      partes.push('preco e nova campanha nao foram alterados');
      const motivo = internal.normalizarErroEfetivacaoFavoritos(data.message || 'Aguardando o anuncio voltar a ativo no Mercado Livre.');
      if (motivo) partes.push(`proximo passo: ${motivo}`);
      return partes.filter(Boolean).join(' | ');
  }

  function statusEtapaEfetivacaoFavoritos(resultado, etapa) {
      const stages = resultado && resultado.stages;
      const info = stages && typeof stages === 'object' ? stages[etapa] : null;
      return String(info && info.status || '').trim().toLowerCase();
  }

  function resultadoEfetivacaoAposMutacaoFavoritos(resultado) {
      if (!resultado || typeof resultado !== 'object') return false;
      if (['partial_failure', 'partial_unknown'].includes(String(resultado.outcome || '').trim().toLowerCase())) return true;
      if (String(resultado.operation_state || '').trim().toLowerCase().startsWith('partial_')) return true;
      const etapasMutaveis = ['promotion_removal', 'listing_type', 'price', 'promotion', 'verification'];
      return etapasMutaveis.some(etapa => {
          const status = internal.statusEtapaEfetivacaoFavoritos(resultado, etapa);
          return ['completed', 'partial', 'unknown', 'fallback_failed'].includes(status);
      });
  }

  function normalizarSegurancaComercialEfetivacaoFavoritos(resultado) {
      const data = resultado && typeof resultado === 'object' ? resultado : {};
      const recebida = data.commercial_safety && typeof data.commercial_safety === 'object'
          ? data.commercial_safety
          : {};
      const promocoesAtivas = Array.isArray(recebida.active_promotions)
          ? recebida.active_promotions.filter(Boolean)
          : [];
      const mensagem = internal.normalizarErroEfetivacaoFavoritos(data.message || '');
      const salePrice = recebida.sale_price && typeof recebida.sale_price === 'object'
          ? recebida.sale_price
          : {};
      const salePriceComPromocao = recebida.sale_price_has_promotion === true
          || salePrice.has_promotion === true
          || data.sale_price_has_promotion === true;
      const promocaoAtivaInferida = data.promotion_still_active === true
          || data.promotion_active === true
          || salePriceComPromocao
          || promocoesAtivas.length > 0
          || /promo(?:cao|ção)(?:es|ões)?[^.]{0,80}ativ/i.test(mensagem)
          || /sale_price[^.]{0,60}promocional/i.test(mensagem);
      const promocaoAtiva = typeof recebida.promotion_active === 'boolean'
          ? recebida.promotion_active
          : promocaoAtivaInferida;
      const fallbackFalhou = internal.statusEtapaEfetivacaoFavoritos(data, 'promotion') === 'fallback_failed'
          || internal.statusEtapaEfetivacaoFavoritos(data, 'verification') === 'fallback_failed';
      const depoisDeMutacao = internal.resultadoEfetivacaoAposMutacaoFavoritos(data) || fallbackFalhou;
      let estado = String(recebida.state || '').trim().toLowerCase();
      if (!['safe', 'unsafe', 'unknown'].includes(estado)) {
          if (data.success === true && data.completed !== false) {
              estado = 'safe';
          } else if (promocaoAtiva) {
              estado = 'unsafe';
          } else if (fallbackFalhou || String(data.outcome || '').trim().toLowerCase() === 'partial_unknown') {
              estado = 'unknown';
          } else if (depoisDeMutacao) {
              estado = 'unknown';
          } else {
              estado = 'safe';
          }
      }
      const interromperLote = recebida.batch_abort_required === true
          || data.stop_batch === true
          || fallbackFalhou
          || ['unsafe', 'unknown'].includes(estado);
      const motivoPadrao = promocaoAtiva
          ? 'Promoção divergente ainda ativa; o estado comercial não é seguro.'
          : (estado === 'unknown'
              ? 'Não foi possível confirmar um estado comercial remoto seguro depois da alteração.'
              : 'Estado comercial confirmado.');
      return {
          ...recebida,
          state: estado,
          reason: String(recebida.reason || motivoPadrao).trim(),
          batch_abort_required: interromperLote,
          promotion_active: promocaoAtiva,
          active_promotions: promocoesAtivas,
          sale_price_has_promotion: salePriceComPromocao,
          sale_price: recebida.sale_price ?? null,
          observed_discount_pct: recebida.observed_discount_pct ?? null,
          stable_reads: recebida.stable_reads ?? null,
          required_stable_reads: recebida.required_stable_reads ?? null
      };
  }

  function resultadoEfetivacaoRequerInterrupcaoLoteFavoritos(resultado) {
      return internal.normalizarSegurancaComercialEfetivacaoFavoritos(resultado).batch_abort_required === true;
  }

  function textoPercentualEfetivacaoFavoritos(valor) {
      const numero = adapters.parsePrecoAnuncioFavoritos(valor);
      return numero === null ? '' : `${Number(numero).toFixed(2).replace('.', ',')}%`;
  }

  function resultadoEfetivacaoTerminalFavoritos(resultado) {
      if (!resultado || typeof resultado !== 'object' || resultado.success === true) return false;
      if (resultado.terminal === true || resultado.terminal_failure === true || resultado.retryable === false) return true;
      const codigo = String(resultado.error_code || resultado.code || '').trim().toLowerCase();
      return ['price_not_modifiable', 'catalog_price_locked', 'terminal'].includes(codigo);
  }

  function resultadoEfetivacaoIncertoFavoritos(resultado) {
      if (!resultado || typeof resultado !== 'object') return false;
      if (resultado.outcome === 'partial_unknown') return true;
      const seguranca = internal.normalizarSegurancaComercialEfetivacaoFavoritos(resultado);
      return internal.resultadoEfetivacaoAposMutacaoFavoritos(resultado) && seguranca.state === 'unknown';
  }

  function resultadoEfetivacaoParcialFavoritos(resultado) {
      if (!resultado || typeof resultado !== 'object') return false;
      if (['partial_failure', 'partial_unknown'].includes(resultado.outcome)) return true;
      const seguranca = internal.normalizarSegurancaComercialEfetivacaoFavoritos(resultado);
      return internal.resultadoEfetivacaoAposMutacaoFavoritos(resultado)
          && ['unsafe', 'unknown'].includes(seguranca.state);
  }

  function descreverTipoFalhaEfetivacaoFavoritos(registro, resultado, parcial) {
      const sim = registro && registro.sim || {};
      const update = resultado && resultado.listing_type_update || {};
      if (!parcial) {
          const tipoPrevisto = internal.descreverTipoEfetivacaoFavoritos(registro, null)
              .replace(/^tipo alterado:\s*/i, '')
              .replace(/^ja estava no tipo alvo:\s*/i, '')
              .replace(/^tipo:\s*/i, '');
          return tipoPrevisto ? `tipo previsto: ${tipoPrevisto}` : '';
      }
      const atual = String(
          update.current_name
          || update.current_listing_type_name
          || sim.tipoAnuncioAtual
          || ''
      ).trim();
      const alvo = String(
          update.target_name
          || update.target_listing_type_name
          || sim.tipoAnuncioAlvo
          || atual
          || ''
      ).trim();
      if (update.changed === true) {
          return `tipo alterado: ${atual || '-'} -> ${alvo || '-'}`;
      }
      const estado = resultado && resultado.current_state || {};
      const tipoAtual = String(estado.listing_type_name || estado.listing_type_id || atual || alvo || '').trim();
      return tipoAtual ? `tipo inalterado: ${tipoAtual}` : 'tipo inalterado';
  }

  function descreverEtapasFalhaEfetivacaoFavoritos(registro, resultado) {
      const partes = [];
      const sim = registro && registro.sim || {};
      const precoStatus = internal.statusEtapaEfetivacaoFavoritos(resultado, 'price');
      const promocaoStatus = internal.statusEtapaEfetivacaoFavoritos(resultado, 'promotion');
      if (['failed', 'blocked', 'pending', 'not_started'].includes(precoStatus)) {
          const estado = resultado && resultado.current_state || {};
          const precoAtual = adapters.parsePrecoAnuncioFavoritos((resultado && resultado.preco_anuncio_atual) ?? estado.price);
          const precoAlvo = adapters.parsePrecoAnuncioFavoritos((resultado && resultado.preco_anuncio_alvo) ?? sim.preco);
          const precoNoAlvo = precoAtual !== null && precoAlvo !== null && Math.abs(precoAtual - precoAlvo) <= 0.02;
          partes.push(
              precoNoAlvo
                  ? `preco no alvo, mas nao confirmado: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(precoAtual)}`
                  : `preco nao alterado${precoAtual !== null ? `: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(precoAtual)}` : ''}`
          );
      } else if (precoStatus === 'unknown') {
          partes.push('preco enviado, mas o estado remoto ficou incerto');
      }
      if (['not_started', 'pending'].includes(promocaoStatus)) {
          partes.push('campanha nao iniciada');
      } else if (promocaoStatus === 'fallback_failed') {
          const seguranca = internal.normalizarSegurancaComercialEfetivacaoFavoritos(resultado);
          partes.push(
              seguranca.promotion_active
                  ? 'promoção divergente ainda ativa'
                  : 'fallback sem promoção não confirmado; estado comercial remoto inseguro'
          );
      } else if (['failed', 'blocked'].includes(promocaoStatus)) {
          partes.push('campanha nao aplicada');
      } else if (promocaoStatus === 'completed' && internal.statusEtapaEfetivacaoFavoritos(resultado, 'verification') === 'failed') {
          partes.push('campanha enviada, mas a conferencia nao foi concluida');
      } else if (promocaoStatus === 'unknown' || internal.statusEtapaEfetivacaoFavoritos(resultado, 'verification') === 'unknown') {
          partes.push('campanha pode ter sido aplicada; estado remoto incerto');
      }
      return partes;
  }

  function descreverFalhaEfetivacaoFavoritos(item) {
      const registro = item && item.registro || {};
      const sim = registro.sim || {};
      const resultado = item && item.resultado || {};
      const partes = [];
      const loja = String(resultado.loja || registro.loja || '').trim();
      if (loja) partes.push(`Loja: ${loja}`);
      const parcial = internal.resultadoEfetivacaoParcialFavoritos(resultado);
      const incerto = internal.resultadoEfetivacaoIncertoFavoritos(resultado);
      const tipo = internal.descreverTipoFalhaEfetivacaoFavoritos(registro, resultado, parcial);
      if (tipo) partes.push(tipo);
      if (sim && sim.ok) {
          partes.push(`preco cheio previsto: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.preco)}`);
          const precoPromocional = sim.precoPromocionalCalculado ?? sim.precoPromocional ?? sim.precoCompetitivo;
          partes.push(`preco final promocional previsto: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(precoPromocional)}`);
      } else if (sim && sim.status) {
          partes.push(`simulacao invalida: ${internal.textoCurtoStatusSimuladorFavoritos(sim.status)}`);
      }
      const comparacao = internal.obterComparacaoPromocaoEfetivacaoFavoritos(resultado, sim);
      if (comparacao.percentualSolicitado !== null) {
          partes.push(`% solicitada: ${internal.textoPercentualEfetivacaoFavoritos(comparacao.percentualSolicitado)}`);
      }
      if (comparacao.percentualObservado !== null) {
          partes.push(`% observada: ${internal.textoPercentualEfetivacaoFavoritos(comparacao.percentualObservado)}`);
      }
      if (comparacao.precoFinalSolicitado !== null) {
          partes.push(`preço final pretendido: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(comparacao.precoFinalSolicitado)}`);
      }
      if (comparacao.precoFinalObservado !== null) {
          partes.push(`preço final observado: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(comparacao.precoFinalObservado)}`);
      }
      partes.push(...descreverEstadoAtualEfetivacaoFavoritos(resultado));
      if (parcial) partes.push(...descreverEtapasFalhaEfetivacaoFavoritos(registro, resultado));
      const erro = internal.normalizarErroEfetivacaoFavoritos(item && item.erro || '');
      partes.push(`${incerto ? 'estado remoto incerto; confira o anuncio antes de aprovar novamente' : (parcial ? 'concluido parcialmente' : 'nao concluido')}: ${erro || 'O Mercado Livre recusou a alteracao sem detalhar o motivo.'}`);
      if (internal.resultadoEfetivacaoTerminalFavoritos(resultado)) {
          partes.push('bloqueio terminal: corrija a restricao no Mercado Livre antes de aprovar novamente; nao ha repeticao automatica');
      }
      return partes.filter(Boolean).join(' | ');
  }

  Object.assign(internal, {
    textoCurtoStatusSimuladorFavoritos,
    normalizarErroEfetivacaoFavoritos,
    limparLogEfetivarFavoritos,
    limparTimerOcultarStatusEfetivarFavoritos,
    agendarOcultarStatusEfetivarFavoritos,
    adicionarStatusEfetivarFavoritos,
    textoPrecoComparacaoFavoritos,
    resumoPrecosComparacaoFavoritos,
    deveMostrarCustoIdealComparacaoFavoritos,
    textoCustoIdealComparacaoFavoritos,
    textoPrecoAlvoComparacaoFavoritos,
    textoCustoIdealTabelaComparacaoFavoritos,
    descreverTipoEfetivacaoFavoritos,
    descreverSucessoEfetivacaoFavoritos,
    descreverEstadoAtualEfetivacaoFavoritos,
    descreverPendenteEfetivacaoFavoritos,
    statusEtapaEfetivacaoFavoritos,
    resultadoEfetivacaoAposMutacaoFavoritos,
    normalizarSegurancaComercialEfetivacaoFavoritos,
    resultadoEfetivacaoRequerInterrupcaoLoteFavoritos,
    textoPercentualEfetivacaoFavoritos,
    resultadoEfetivacaoTerminalFavoritos,
    resultadoEfetivacaoIncertoFavoritos,
    resultadoEfetivacaoParcialFavoritos,
    descreverTipoFalhaEfetivacaoFavoritos,
    descreverEtapasFalhaEfetivacaoFavoritos,
    descreverFalhaEfetivacaoFavoritos
  });
  internal.components.add('05-outcomes');
})(window);
