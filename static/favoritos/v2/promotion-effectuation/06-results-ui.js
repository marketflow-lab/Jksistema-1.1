(function (global) {
  'use strict';

  const feature = global.FavoritosV2.promotionEffectuation;
  const internal = feature.internal;
  const state = feature.runtime.state;
  const adapters = feature.runtime.adapters;
  if (internal.components.has('06-results-ui')) return;

  function criarLinhaResultadoBalaoFavoritos(tipo, titulo, detalhe) {
      const row = document.createElement('div');
      row.className = `ml-favoritos-balloon-result-row is-${tipo || 'info'}`;
      const titleEl = document.createElement('div');
      titleEl.className = 'ml-favoritos-balloon-result-title';
      titleEl.textContent = titulo || 'Resultado';
      row.appendChild(titleEl);
      const detalheEl = document.createElement('div');
      detalheEl.className = 'ml-favoritos-balloon-result-detail';
      detalheEl.textContent = detalhe || '-';
      row.appendChild(detalheEl);
      return row;
  }

  function renderizarResumoResultadoBalaoFavoritos(sucessos, pendentes, falhas) {
      const feitos = Array.isArray(sucessos) ? sucessos : [];
      const aguardando = Array.isArray(pendentes) ? pendentes : [];
      const naoFeitos = Array.isArray(falhas) ? falhas : [];
      const naoEnviados = Array.isArray(arguments[3]) ? arguments[3] : [];
      if (!feitos.length && !aguardando.length && !naoFeitos.length && !naoEnviados.length) return null;
      const wrap = document.createElement('div');
      wrap.className = 'ml-favoritos-balloon-result-list';
      feitos.forEach(item => {
          const itemId = item && (item.itemId || (item.data && item.data.item_id)) || '-';
          const data = item && item.data || {};
          const fallback = !!data.fallback_sem_promocao_aplicado;
          wrap.appendChild(internal.criarLinhaResultadoBalaoFavoritos(
              'success',
              `${itemId} - alteracao feita${fallback ? ' com fallback' : ''}`,
              internal.descreverSucessoEfetivacaoFavoritos(item)
          ));
      });
      aguardando.forEach(item => {
          const itemId = item && (item.itemId || (item.data && item.data.item_id)) || '-';
          wrap.appendChild(internal.criarLinhaResultadoBalaoFavoritos(
              'warning',
              `${itemId} - pendente de revisao`,
              internal.descreverPendenteEfetivacaoFavoritos(item)
          ));
      });
      naoFeitos.forEach(item => {
          const itemId = item && item.itemId || '-';
          const parcial = internal.resultadoEfetivacaoParcialFavoritos(item && item.resultado);
          const incerto = internal.resultadoEfetivacaoIncertoFavoritos(item && item.resultado);
          wrap.appendChild(internal.criarLinhaResultadoBalaoFavoritos(
              'error',
              `${itemId} - ${incerto ? 'estado remoto incerto' : (parcial ? 'alteracao parcial' : 'alteracao nao feita')}`,
              internal.descreverFalhaEfetivacaoFavoritos(item)
          ));
      });
      naoEnviados.forEach(item => {
          const itemId = item && item.itemId || '-';
          const motivo = internal.normalizarErroEfetivacaoFavoritos(item && item.motivo || 'Lote interrompido por segurança.');
          wrap.appendChild(internal.criarLinhaResultadoBalaoFavoritos(
              'warning',
              `${itemId} - não enviado por bloqueio de segurança`,
              `${motivo}${item && item.bloqueadoPor ? ` Anúncio que acionou o bloqueio: ${item.bloqueadoPor}.` : ''}`
          ));
      });
      return wrap;
  }

  function criarBlocoColarPlanilhaResultadoFavoritos(historicoAlteracoes, compacto = false) {
      if (!historicoAlteracoes || !Array.isArray(historicoAlteracoes.vinculos) || !historicoAlteracoes.vinculos.length) {
          return null;
      }
      if (typeof window.favoritosCriarBotaoColarHistoricoPlanilha !== 'function') {
          return null;
      }
      const wrap = document.createElement('div');
      wrap.className = compacto
          ? 'ml-favoritos-resultado-planilha is-compact'
          : 'ml-favoritos-resultado-planilha';
      const statusEl = document.createElement('div');
      statusEl.className = 'ml-favoritos-resultado-planilha-status';
      const botao = window.favoritosCriarBotaoColarHistoricoPlanilha(historicoAlteracoes, { statusEl });
      botao.classList.add('ml-favoritos-resultado-planilha-btn');
      wrap.appendChild(botao);
      wrap.appendChild(statusEl);
      return wrap;
  }

  function prepararComparativoEfetivacaoFavoritos(sucessos, pendentes, falhas, naoEnviados = []) {
      const lista = (Array.isArray(sucessos) ? sucessos : [])
          .filter(item => item && item.registro && item.data);
      const listaPendentes = (Array.isArray(pendentes) ? pendentes : [])
          .filter(item => item && item.registro && item.data);
      const listaFalhas = (Array.isArray(falhas) ? falhas : [])
          .filter(item => item && (item.itemId || item.erro || item.registro));
      const listaNaoEnviados = (Array.isArray(naoEnviados) ? naoEnviados : [])
          .filter(item => item && (item.itemId || item.registro));
      const itensComparacao = [
          ...lista.map(item => ({
              tipo: 'sucesso',
              item,
              registro: item.registro || {},
              data: item.data || {},
              erro: ''
          })),
          ...listaPendentes.map(item => ({
              tipo: 'pendente',
              item,
              registro: item.registro || {},
              data: item.data || {},
              erro: ''
          })),
          ...listaFalhas
              .filter(item => item && item.registro)
              .map(item => ({
                  tipo: 'falha',
                  item,
                  registro: item.registro || {},
                  data: item.resultado || {},
                  erro: internal.normalizarErroEfetivacaoFavoritos(item.erro || '', 220)
              })),
          ...listaNaoEnviados
              .filter(item => item && item.registro)
              .map(item => ({
                  tipo: 'nao_enviado',
                  item,
                  registro: item.registro || {},
                  data: {},
                  erro: internal.normalizarErroEfetivacaoFavoritos(item.motivo || '', 220)
              }))
      ];
      const totalProcessados = lista.length + listaPendentes.length + listaFalhas.length;
      const totalItens = totalProcessados + listaNaoEnviados.length;
      const mostrarCustoIdeal = itensComparacao.some(item => internal.deveMostrarCustoIdealComparacaoFavoritos(item.item));
      const mostrarStatusLinha = listaPendentes.length > 0 || listaFalhas.length > 0 || listaNaoEnviados.length > 0;
      const colunas = [
          'Nosso MLB',
          'Nosso preco',
          'Nosso preco com desconto atual',
          'Base MLB',
          'Base preco',
          'Base preco com desconto atual'
      ];
      if (mostrarCustoIdeal) {
          colunas.push('Preco alvo abaixo da base');
          colunas.push('Custo ideal para vender abaixo da base');
      }
      if (mostrarStatusLinha) {
          colunas.push('Status');
      }
      return { lista, listaPendentes, listaFalhas, listaNaoEnviados, itensComparacao, totalProcessados, totalItens, mostrarCustoIdeal, mostrarStatusLinha, colunas };
  }

  function valoresLinhaComparativoFavoritos(item, contexto) {
      const registro = item.registro || {};
      const data = item.data || {};
      const sim = registro.sim || {};
      const observados = internal.obterPrecosObservadosEfetivacaoFavoritos(data);
      const comparacao = internal.obterComparacaoPromocaoEfetivacaoFavoritos(data, sim);
      const substitutos = item.tipo === 'sucesso'
          ? { preco: observados.precoCheio, promocional: observados.precoPromocional }
          : item.tipo === 'pendente'
              ? { preco: data.current_state && data.current_state.price, promocional: null }
              : item.tipo === 'nao_enviado'
               ? { preco: null, promocional: null }
               : {
                   preco: data.preco_anuncio_atual
                       ?? (data.current_state && data.current_state.price)
                       ?? comparacao.precoCheioObservado,
                   promocional: comparacao.precoFinalObservado
               };
      const nosso = internal.resumoPrecosComparacaoFavoritos(registro.anuncio, substitutos);
      const base = internal.resumoPrecosComparacaoFavoritos(registro.ranking);
      const mostrarIdeal = internal.deveMostrarCustoIdealComparacaoFavoritos(item.item);
      const valores = [
          { valor: registro.itemId || adapters.obterIdAnuncioFavoritos(registro.anuncio) || '-' },
          { valor: internal.textoPrecoComparacaoFavoritos(nosso.preco), money: true },
          { valor: internal.textoPrecoComparacaoFavoritos(nosso.promocional), money: true },
          { valor: adapters.obterIdAnuncioFavoritos(registro.ranking) || '-' },
          { valor: internal.textoPrecoComparacaoFavoritos(base.preco), money: true },
          { valor: internal.textoPrecoComparacaoFavoritos(base.promocional), money: true }
      ];
      if (contexto.mostrarCustoIdeal) {
          valores.push({ valor: mostrarIdeal ? internal.textoPrecoAlvoComparacaoFavoritos(registro.sim) : '-', money: mostrarIdeal });
          valores.push({ valor: mostrarIdeal ? internal.textoCustoIdealTabelaComparacaoFavoritos(registro.sim) : '-' });
      }
      if (contexto.mostrarStatusLinha) {
          valores.push({ valor: item.tipo === 'sucesso'
               ? 'Alterado'
               : item.tipo === 'pendente'
                   ? 'Pendente de revisao no ML; preco/campanha nao enviados'
                   : item.tipo === 'nao_enviado'
                       ? `Não enviado por bloqueio de segurança${item.erro ? `: ${item.erro}` : ''}`
                   : `${internal.resultadoEfetivacaoIncertoFavoritos(data) ? 'Estado remoto incerto' : (internal.resultadoEfetivacaoParcialFavoritos(data) ? 'Alteracao parcial' : 'Nao concluido')}${item.erro ? `: ${item.erro}` : ''}` });
      }
      return valores;
  }

  function criarTabelaComparativoFavoritos(contexto, wrapClass, tableClass) {
      const wrap = document.createElement('div');
      wrap.className = wrapClass;
      const table = document.createElement('table');
      table.className = tableClass;
      const thead = document.createElement('thead');
      const trHead = document.createElement('tr');
      contexto.colunas.forEach(texto => {
          const th = document.createElement('th');
          th.textContent = texto;
          trHead.appendChild(th);
      });
      thead.appendChild(trHead);
      table.appendChild(thead);
      const tbody = document.createElement('tbody');
          contexto.itensComparacao.forEach(item => {
          const tr = document.createElement('tr');
           if (item.tipo === 'falha') tr.className = 'is-error';
           if (item.tipo === 'pendente') tr.className = 'is-warning';
           if (item.tipo === 'nao_enviado') tr.className = 'is-warning';
          internal.valoresLinhaComparativoFavoritos(item, contexto).forEach(itemValor => {
              const td = document.createElement('td');
              td.textContent = itemValor.valor;
              if (itemValor.money) td.className = 'is-money';
              tr.appendChild(td);
          });
          tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      wrap.appendChild(table);
      return wrap;
  }

  function renderizarComparativoLogFavoritos(contexto, historicoAlteracoes) {
      if (contexto.itensComparacao.length && state.favMlEfetivarLogEl) {
          state.favMlEfetivarLogEl.classList.remove('hidden');
          const row = document.createElement('div');
          row.className = 'ml-favoritos-efetivar-log-row is-comparison';
          const titleEl = document.createElement('div');
          titleEl.className = 'ml-favoritos-efetivar-log-title';
          titleEl.textContent = `Comparacao dos anuncios processados: ${contexto.totalProcessados} processado(s), ${contexto.listaNaoEnviados.length} não enviado(s) (${contexto.lista.length} alterado(s), ${contexto.listaPendentes.length} pendente(s), ${contexto.listaFalhas.length} nao concluido(s)).`;
          row.appendChild(titleEl);
          row.appendChild(internal.criarTabelaComparativoFavoritos(contexto, 'ml-favoritos-comparacao-table-wrap', 'ml-favoritos-comparacao-table'));
          const blocoPlanilha = internal.criarBlocoColarPlanilhaResultadoFavoritos(historicoAlteracoes);
          if (blocoPlanilha) row.appendChild(blocoPlanilha);
          state.favMlEfetivarLogEl.appendChild(row);
          state.favMlEfetivarLogEl.scrollTop = state.favMlEfetivarLogEl.scrollHeight;
      }
  }

  function renderizarComparativoBalaoFavoritos(contexto, mensagemStatus, historicoAlteracoes) {
      if (!state.mlFavoritosBalloonEl || !state.mlFavoritosBalloonTextEl) return;
      if (state.mlFavoritosBalloonTimer) {
          clearTimeout(state.mlFavoritosBalloonTimer);
          state.mlFavoritosBalloonTimer = null;
      }
      const titulo = state.mlFavoritosBalloonEl.querySelector('.ml-favoritos-balloon-title');
      if (titulo) titulo.textContent = 'Resultado das alteracoes';
      state.mlFavoritosBalloonTextEl.innerHTML = '';
      if (state.mlFavoritosBalloonActionsEl) state.mlFavoritosBalloonActionsEl.innerHTML = '';
      if (mensagemStatus) {
          const statusResumo = document.createElement('div');
          statusResumo.className = 'ml-favoritos-balloon-status-summary';
          statusResumo.textContent = mensagemStatus;
          state.mlFavoritosBalloonTextEl.appendChild(statusResumo);
      }
      const resumoResultado = internal.renderizarResumoResultadoBalaoFavoritos(
          contexto.lista,
          contexto.listaPendentes,
          contexto.listaFalhas,
          contexto.listaNaoEnviados
      );
      if (resumoResultado) state.mlFavoritosBalloonTextEl.appendChild(resumoResultado);
      if (contexto.itensComparacao.length) {
          state.mlFavoritosBalloonTextEl.appendChild(internal.criarTabelaComparativoFavoritos(contexto, 'ml-favoritos-balloon-comparison-wrap', 'ml-favoritos-balloon-comparison-table'));
      }
      if (state.mlFavoritosBalloonActionsEl) {
          const blocoPlanilha = internal.criarBlocoColarPlanilhaResultadoFavoritos(historicoAlteracoes, true);
          if (blocoPlanilha) state.mlFavoritosBalloonActionsEl.appendChild(blocoPlanilha);
          const fecharBtn = document.createElement('button');
          fecharBtn.type = 'button';
          fecharBtn.textContent = 'Fechar';
          fecharBtn.addEventListener('click', window.FavoritosV2.searchRanking.publicApi.status.esconderBalaoFavoritosStatus);
          state.mlFavoritosBalloonActionsEl.appendChild(fecharBtn);
      }
      state.mlFavoritosBalloonEl.classList.remove('hidden');
      state.mlFavoritosBalloonEl.classList.toggle('is-error', !!contexto.listaFalhas.length);
      state.mlFavoritosBalloonEl.classList.toggle('is-warning', !contexto.listaFalhas.length && !!contexto.listaPendentes.length);
      state.mlFavoritosBalloonEl.classList.add('is-wide', 'is-comparison');
      adapters.posicionarBalaoFavoritosStatus();
  }

  function renderizarComparativoEfetivacaoFavoritos(sucessos, mensagemStatus = '', pendentes = [], falhas = [], historicoAlteracoes = null) {
      const naoEnviados = Array.isArray(arguments[5]) ? arguments[5] : [];
      const contexto = internal.prepararComparativoEfetivacaoFavoritos(sucessos, pendentes, falhas, naoEnviados);
      if (!contexto.lista.length && !contexto.listaPendentes.length && !contexto.listaFalhas.length && !contexto.listaNaoEnviados.length) return;
      internal.renderizarComparativoLogFavoritos(contexto, historicoAlteracoes);
      if (state.mlFavoritosStatusEl) {
          const { totalProcessados, lista, listaPendentes, listaFalhas, listaNaoEnviados } = contexto;
          state.mlFavoritosStatusEl.textContent = `Resultado das alteracoes: ${totalProcessados} processado(s), ${lista.length} feita(s), ${listaPendentes.length} pendente(s), ${listaFalhas.length} nao concluida(s), ${listaNaoEnviados.length} não enviada(s).`;
      }
      internal.renderizarComparativoBalaoFavoritos(contexto, mensagemStatus, historicoAlteracoes);
  }

  Object.assign(internal, {
    criarLinhaResultadoBalaoFavoritos,
    renderizarResumoResultadoBalaoFavoritos,
    criarBlocoColarPlanilhaResultadoFavoritos,
    prepararComparativoEfetivacaoFavoritos,
    valoresLinhaComparativoFavoritos,
    criarTabelaComparativoFavoritos,
    renderizarComparativoLogFavoritos,
    renderizarComparativoBalaoFavoritos,
    renderizarComparativoEfetivacaoFavoritos
  });
  internal.components.add('06-results-ui');
})(window);
