(function (global) {
  'use strict';

  const feature = global.FavoritosV2.promotionEffectuation;
  const internal = feature.internal;
  const state = feature.runtime.state;
  const adapters = feature.runtime.adapters;
  if (internal.components.has('08-simulator-ui')) return;

  function criarCelulaSimuladorPrecoFavoritos(anuncioConta, anuncioRanking, opcoesPromocao = null, simCalculada = null) {
      const td = document.createElement('td');
      td.className = 'ml-favoritos-simulador-cell';
      const sim = simCalculada || internal.calcularSimulacaoPrecoFavoritos(anuncioConta, anuncioRanking, opcoesPromocao);
      if (!sim.ok) {
          const wrap = document.createElement('div');
          wrap.className = 'ml-favoritos-simulador-wrap';
          const badge = document.createElement('span');
          badge.className = 'ml-favoritos-margem-badge is-empty';
          badge.textContent = '-';
          const status = document.createElement('span');
          status.className = 'ml-favoritos-simulador-status';
          status.textContent = internal.textoCurtoStatusSimuladorFavoritos(sim.status || 'Sem anuncio rankeado na mesma linha para simular.');
          td.title = sim.status || 'Sem anuncio rankeado na mesma linha para simular.';
          wrap.appendChild(badge);
          wrap.appendChild(status);
          td.appendChild(wrap);
          return td;
      }

      const wrap = document.createElement('div');
      wrap.className = 'ml-favoritos-simulador-wrap';
      const preco = document.createElement('span');
      preco.className = 'ml-favoritos-simulador-preco';
      preco.textContent = window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.preco);
      const badge = document.createElement('span');
      badge.className = 'ml-favoritos-margem-badge ' + (sim.margem >= 0 ? 'is-positive' : 'is-negative');
      badge.textContent = adapters.formatarMargemAnuncioFavoritos(sim.margem);
      const linhasMeta = [];
      const adicionarLinhaMeta = (texto) => {
          if (!texto) return;
          linhasMeta.push(texto);
      };
      if (sim.percentualPromocao !== null) {
          const pctTxt = `${Number(sim.percentualPromocao).toFixed(1).replace('.', ',').replace(',0', '')}%`;
          const finalPromo = sim.precoPromocionalCalculado ?? sim.precoPromocional ?? sim.precoCompetitivo;
          adicionarLinhaMeta(`${pctTxt} -> ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(finalPromo)}`);
      } else if (sim.limiteMargemAplicado) {
          if (sim.descontoEfetivo >= 0.01) {
              adicionarLinhaMeta(`-${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.descontoEfetivo)}`);
          }
          adicionarLinhaMeta('limite 15%');
      } else {
          adicionarLinhaMeta(`-${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.descontoEfetivo)}`);
      }
      wrap.appendChild(preco);
      wrap.appendChild(badge);
      if (sim.ajustePrecoUnico) {
          adicionarLinhaMeta('preco unico');
      }
      if (sim.trocarTipoAnuncio && sim.tipoAnuncioAlvo) {
          adicionarLinhaMeta(`tipo ${sim.tipoAnuncioAtual || '-'} -> ${sim.tipoAnuncioAlvo}`);
      }
      linhasMeta.forEach((texto) => {
          const meta = document.createElement('span');
          meta.className = 'ml-favoritos-simulador-meta';
          meta.textContent = texto;
          wrap.appendChild(meta);
      });
      td.title = [
          `Preco ranking: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.precoRanking)}`,
          `Desconto sorteado: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.descontoSorteado)}`,
          `Desconto aplicado: ${sim.descontoEfetivo >= 0 ? window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.descontoEfetivo) : 'sem desconto possivel'}`,
          sim.percentualPromocao !== null
              ? `Preco cheio sugerido antes da promocao: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.preco)}`
              : `Preco simulado: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.preco)}`,
          sim.percentualPromocao !== null ? `Promocao fixa: ${Number(sim.percentualPromocao).toFixed(2).replace('.', ',')}%` : '',
          sim.percentualPromocao !== null ? `Preco final alvo apos promocao: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.precoPromocional)}` : '',
          sim.percentualPromocao !== null && sim.precoPromocionalCalculado !== null ? `Preco final calculado apos promocao: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.precoPromocionalCalculado)}` : '',
          sim.ajustePrecoUnico ? `Ajuste para evitar preco igual entre nossos anuncios: +${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.ajustePrecoUnico)}` : '',
          sim.trocarTipoAnuncio && sim.tipoAnuncioAlvo ? `Tipo do anuncio sera alterado para o tipo do ranking: ${sim.tipoAnuncioAtual || '-'} -> ${sim.tipoAnuncioAlvo}` : '',
          sim.taxaTipoAnuncioAplicada !== null && sim.taxaTipoAnuncioAplicada !== undefined ? `Taxa simulada pelo tipo alvo: ${(Number(sim.taxaTipoAnuncioAplicada) * 100).toFixed(2).replace('.', ',')}%` : '',
          `Margem simulada: ${adapters.formatarMargemAnuncioFavoritos(sim.margem)}`,
          sim.limiteMargemAplicado ? 'Limite de margem minima de 15% aplicado' : '',
          sim.limiteMargemAplicado && sim.custoIdealAbaixoBase !== null && sim.custoIdealAbaixoBase !== undefined
              ? `Custo ideal para vender abaixo da base: ${internal.textoCustoIdealComparacaoFavoritos(sim)}`
              : '',
          `Liquido: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.valorLiquido)}`,
          `Custo: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.custo)}`,
          `Frete: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.frete)}`,
          `Tarifa estimada: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.tarifa)}`,
          `Imposto: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(sim.impostoValor)}`
      ].filter(Boolean).join(' | ');
      td.appendChild(wrap);
      return td;
  }

  Object.assign(internal, {
    criarCelulaSimuladorPrecoFavoritos
  });
  internal.components.add('08-simulator-ui');
})(window);
