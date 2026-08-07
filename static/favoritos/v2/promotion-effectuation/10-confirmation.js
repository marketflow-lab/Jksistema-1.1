(function (global) {
  'use strict';

  const feature = global.FavoritosV2.promotionEffectuation;
  const internal = feature.internal;
  const state = feature.runtime.state;
  const adapters = feature.runtime.adapters;
  if (internal.components.has('10-confirmation')) return;

  function criarCardConfirmacaoFavoritos(rotulo, valor) {
      const card = document.createElement('div');
      card.className = 'ml-favoritos-confirm-card';
      const label = document.createElement('span');
      label.className = 'ml-favoritos-confirm-label';
      label.textContent = rotulo || '';
      const value = document.createElement('span');
      value.className = 'ml-favoritos-confirm-value';
      value.textContent = valor || '-';
      card.appendChild(label);
      card.appendChild(value);
      return card;
  }

  function montarDadosConfirmacaoFavoritos(opcoes = {}) {
      const total = Number(opcoes.total || 0);
      const incluirOutrasContas = !!opcoes.incluirOutrasContas;
      const buscouAnunciosAoAprovar = !!opcoes.buscouAnunciosAoAprovar;
      const nomeCampanha = String(opcoes.nomeCampanha || 'campanha selecionada').trim();
      const totalTrocaTipo = Number(opcoes.totalTrocaTipo || 0);
      const totalTipoMantido = Number(opcoes.totalTipoMantido || 0);
      const totalTrocaAposPromocao = Number(opcoes.totalTrocaAposPromocao || 0);
      const escopo = incluirOutrasContas
          ? 'Inclui outras contas com este SKU'
          : (buscouAnunciosAoAprovar ? 'Somente a loja atual apos buscar o SKU' : 'Somente a loja atual');
      const trocaTipo = totalTrocaTipo
          ? `${totalTrocaTipo} anuncio(s) vao mudar Premium/Classico`
          : (totalTipoMantido ? `${totalTipoMantido} manterao o tipo atual por bloqueio do ML` : 'Sem troca de tipo');
      const textoFallback = [
          `Aprovar e alterar ${total} anuncio(s)?`,
          escopo,
          `Campanha: ${nomeCampanha}`,
          totalTrocaTipo ? `${totalTrocaTipo} anuncio(s) tambem terao o tipo alterado para igual ao ranking.` : '',
          totalTrocaAposPromocao ? `${totalTrocaAposPromocao} anuncio(s) so mostraram downgrade bloqueado antes da limpeza; o sistema vai remover promocoes atuais e tentar de novo.` : '',
          totalTipoMantido ? `${totalTipoMantido} anuncio(s) serao recalculados mantendo Premium porque o Mercado Livre nao liberou downgrade para Classico.` : '',
          'Sequencia: remover a promocao atual, ajustar tipo quando necessario, aplicar o preco cheio calculado, aplicar a nova promocao e conferir o resultado.',
          'Se a promocao nao for mantida ou a margem ficar insegura, sera tentado fallback direto com preco seguro e sem promocao.',
          'Quando houver mais de um anuncio nosso, os precos finais e cheios nao serao iguais.'
      ].filter(Boolean).join('\n');
      return { total, escopo, nomeCampanha, trocaTipo, totalTrocaAposPromocao, totalTipoMantido, textoFallback };
  }

  function criarLayoutConfirmacaoFavoritos(dados) {
      const layout = document.createElement('div');
      layout.className = 'ml-favoritos-confirm-layout';
      const hero = document.createElement('div');
      hero.className = 'ml-favoritos-confirm-hero';
      const heroTitle = document.createElement('strong');
      heroTitle.textContent = `Aprovar e alterar ${dados.total} anuncio(s)?`;
      const heroText = document.createElement('span');
      heroText.textContent = 'Revise as acoes antes de enviar. Nada sera enviado ao Mercado Livre se voce cancelar.';
      hero.appendChild(heroTitle);
      hero.appendChild(heroText);
      layout.appendChild(hero);
      const grid = document.createElement('div');
      grid.className = 'ml-favoritos-confirm-grid';
      grid.appendChild(internal.criarCardConfirmacaoFavoritos('Anuncios', `${dados.total} pronto(s)`));
      grid.appendChild(internal.criarCardConfirmacaoFavoritos('Escopo', dados.escopo));
      grid.appendChild(internal.criarCardConfirmacaoFavoritos('Campanha', dados.nomeCampanha));
      grid.appendChild(internal.criarCardConfirmacaoFavoritos('Tipo do anuncio', dados.trocaTipo));
      layout.appendChild(grid);
      const steps = document.createElement('ul');
      steps.className = 'ml-favoritos-confirm-steps';
      [
          'Remover a promocao atual quando necessario.',
          dados.totalTrocaAposPromocao
              ? 'Aguardar o Mercado Livre liberar a troca apos remover promocoes e entao alterar Premium/Classico.'
              : (dados.totalTipoMantido
              ? 'Alterar o tipo quando o Mercado Livre liberar; quando bloquear downgrade, manter o tipo atual e recalcular o preco.'
              : 'Alterar o tipo para ficar igual ao anuncio do ranking.'),
          'Aplicar o preco cheio calculado e confirmar essa base no Mercado Livre.',
          'Aplicar a campanha com o percentual desejado e conferir os precos observados.',
          'Se a promocao falhar ou a margem ficar insegura, remover a campanha e tentar o preco direto seguro.'
      ].forEach(texto => {
          const li = document.createElement('li');
          li.textContent = texto;
          steps.appendChild(li);
      });
      layout.appendChild(steps);
      const warning = document.createElement('div');
      warning.className = 'ml-favoritos-confirm-warning';
      warning.textContent = dados.totalTrocaAposPromocao
          ? 'Alguns anuncios so liberam downgrade depois que a promocao atual sai. O sistema vai limpar as promocoes atuais, esperar a liberacao do Mercado Livre e tentar a troca de modalidade antes de aplicar preco/campanha.'
          : (dados.totalTipoMantido
          ? 'Alguns anuncios ficarao Premium porque o Mercado Livre nao disponibilizou downgrade para eles agora. Os precos foram recalculados nessa condicao para preservar margem e campanha.'
          : 'Quando houver mais de um anuncio nosso, os precos finais e cheios podem ser diferentes para evitar duplicidade e preservar a margem.');
      layout.appendChild(warning);
      return layout;
  }

  function criarBotoesConfirmacaoFavoritos(finalizar) {
      const cancelar = document.createElement('button');
      cancelar.type = 'button';
      cancelar.className = 'is-muted';
      cancelar.textContent = 'Cancelar';
      cancelar.addEventListener('click', () => finalizar(false, cancelar));
      const confirmar = document.createElement('button');
      confirmar.type = 'button';
      confirmar.className = 'is-primary';
      confirmar.textContent = 'Aprovar e alterar';
      confirmar.addEventListener('click', () => finalizar(true, confirmar));
      return { cancelar, confirmar };
  }

  function perguntarConfirmacaoEfetivarFavoritos(opcoes = {}) {
      const dados = internal.montarDadosConfirmacaoFavoritos(opcoes);
      if (!state.mlFavoritosBalloonEl || !state.mlFavoritosBalloonTextEl || !state.mlFavoritosBalloonActionsEl) {
          return Promise.resolve(window.confirm(dados.textoFallback));
      }
      if (state.mlFavoritosPerguntaResolver) {
          state.mlFavoritosPerguntaResolver(false);
          state.mlFavoritosPerguntaResolver = null;
      }
      return new Promise(resolve => {
          let finalizado = false;
          const finalizar = (confirmado, botao) => {
              if (finalizado) return;
              finalizado = true;
              state.mlFavoritosPerguntaResolver = null;
              if (typeof window.favoritosResolverAcaoBalao === 'function') {
                  window.favoritosResolverAcaoBalao(resolve, !!confirmado, botao, {
                      esconder: true
                  });
              } else {
                  window.FavoritosV2.searchRanking.publicApi.status.esconderBalaoFavoritosStatus();
                  setTimeout(() => resolve(!!confirmado), 0);
              }
          };
          state.mlFavoritosPerguntaResolver = finalizar;
          window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('', {
              manterAcoes: true,
              larga: true,
              titulo: 'Confirmar alteracoes no Mercado Livre'
          });
          state.mlFavoritosBalloonTextEl.innerHTML = '';
          state.mlFavoritosBalloonActionsEl.innerHTML = '';
          state.mlFavoritosBalloonTextEl.appendChild(internal.criarLayoutConfirmacaoFavoritos(dados));
          const botoes = internal.criarBotoesConfirmacaoFavoritos(finalizar);
          state.mlFavoritosBalloonActionsEl.appendChild(botoes.cancelar);
          state.mlFavoritosBalloonActionsEl.appendChild(botoes.confirmar);
          state.mlFavoritosBalloonEl.classList.remove('is-error', 'is-comparison');
          state.mlFavoritosBalloonEl.classList.add('is-wide');
          adapters.posicionarBalaoFavoritosStatus();
      });
  }

  Object.assign(internal, {
    criarCardConfirmacaoFavoritos,
    montarDadosConfirmacaoFavoritos,
    criarLayoutConfirmacaoFavoritos,
    criarBotoesConfirmacaoFavoritos,
    perguntarConfirmacaoEfetivarFavoritos
  });
  internal.components.add('10-confirmation');
})(window);
