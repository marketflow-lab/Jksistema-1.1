(function (global) {
  'use strict';

  const searchRanking = global.FavoritosV2.searchRanking;
  const internal = searchRanking.internal;
  if (internal.components.has('05-promotions')) return;

  function mostrarBalaoFavoritosStatus(...args) {
    const implementation = internal.mostrarBalaoFavoritosStatus;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: mostrarBalaoFavoritosStatus');
    return implementation(...args);
  }

  function resolverAcaoBalaoFavoritos(...args) {
    const implementation = internal.resolverAcaoBalaoFavoritos;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: resolverAcaoBalaoFavoritos');
    return implementation(...args);
  }

  function nomePromocaoFavoritos(campanha) {
      const id = String(campanha && campanha.id || '').trim();
      const nome = String(campanha && (campanha.name || campanha.title || campanha.nome) || id || 'Promocao sem nome').trim();
      const status = String(campanha && campanha.status || '').trim();
      const quantidade = campanha && (campanha.eligible_count ?? campanha.items_count ?? campanha.item_count ?? campanha.total_items);
      const partes = [nome];
      if (status) partes.push(status);
      if (id) partes.push(id);
      if (quantidade !== null && quantidade !== undefined && quantidade !== '') partes.push(`${quantidade} item(ns)`);
      return partes.join(' - ');
  }

  function grupoPromocaoFavoritos(campanha) {
      const explicito = String(campanha && campanha.selection_group || '').trim().toLowerCase();
      if (explicito) return explicito;
      const tipo = String(campanha && (campanha.type || campanha.promotion_type) || '').trim().toUpperCase();
      const nome = String(campanha && (campanha.name || campanha.title) || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
      if (['SELLER_CAMPAIGN', 'SELLER_COUPON_CAMPAIGN'].includes(tipo)) return 'usuario';
      if (['SMART', 'PRICE_MATCHING', 'PRICE_MATCHING_MELI_ALL', 'MARKETPLACE_CAMPAIGN', 'PRE_NEGOTIATED'].includes(tipo)) return 'mercado_livre';
      if (['aceler', 'tarifa', 'menos tarifa', 'reduzimos', 'aumente suas vendas'].some(chave => nome.includes(chave))) return 'mercado_livre';
      return 'outras';
  }

  function tituloGrupoPromocaoFavoritos(grupo) {
      if (grupo === 'usuario') return 'Promocoes criadas por voce';
      if (grupo === 'mercado_livre') return 'Promocoes do Mercado Livre';
      return 'Outras promocoes ativas';
  }

  async function perguntarSimNaoPromocaoFavoritos() {
      return false;
  }

  async function carregarPromocoesAtivasFavoritos(loja, opcoes = {}) {
      const query = new URLSearchParams({ loja: String(loja || '').trim() });
      const queryString = query.toString();
      const montarUrl = (rota) => `${rota}?${queryString}`;
      const urls = [
          montarUrl('/api/favoritos/ml/promocoes'),
          montarUrl('/api/mercadolivre/promocoes')
      ];
      try {
          const origemAtual = new URL(window.location.href);
          const ehBackendLocal = /^https?:$/i.test(origemAtual.protocol)
              && /^(127\.0\.0\.1|localhost)$/i.test(origemAtual.hostname)
              && String(origemAtual.port || '80') === '8001';
          if (!ehBackendLocal) {
              urls.push(`http://127.0.0.1:8001${montarUrl('/api/favoritos/ml/promocoes')}`);
              urls.push(`http://127.0.0.1:8001${montarUrl('/api/mercadolivre/promocoes')}`);
          }
      } catch (_err) {
          urls.push(`http://127.0.0.1:8001${montarUrl('/api/favoritos/ml/promocoes')}`);
          urls.push(`http://127.0.0.1:8001${montarUrl('/api/mercadolivre/promocoes')}`);
      }
      const urlsUnicas = Array.from(new Set(urls));
      const avisoLentidaoMs = Number(opcoes && opcoes.avisoLentidaoMs) > 0
          ? Number(opcoes.avisoLentidaoMs)
          : 6500;
      const onLento = typeof (opcoes && opcoes.onLento) === 'function' ? opcoes.onLento : null;
      let avisoTimer = null;
      if (onLento) {
          avisoTimer = setTimeout(() => {
              try {
                  onLento();
              } catch (_err) {}
          }, avisoLentidaoMs);
      }
      try {
          const erros = [];
          let erroBloqueante = '';
          for (const url of urlsUnicas) {
              if (erroBloqueante) break;
              try {
                  const response = await fetch(url, {
                      headers: obterAuthHeaders(),
                      cache: 'no-store'
                  });
                  const data = await response.json().catch(() => ({}));
                  if (!response.ok) {
                      const detalhe = data.detail || data.message || `HTTP ${response.status}`;
                      if ([401, 403].includes(Number(response.status))) {
                          erroBloqueante = detalhe;
                          break;
                      }
                      erros.push(`${url}: ${detalhe}`);
                      continue;
                  }
                  return Array.isArray(data.campaigns) ? data.campaigns : [];
              } catch (err) {
                  const mensagem = err && err.message ? err.message : String(err);
                  erros.push(`${url}: ${mensagem}`);
              }
          }
          if (erroBloqueante) {
              throw new Error(erroBloqueante);
          }
          const detalheErro = erros.find(txt => !/Failed to fetch/i.test(txt)) || erros[0] || '';
          throw new Error(
              detalheErro
                  ? `Nao foi possivel conectar ao backend para carregar promocoes. ${detalheErro}`
                  : 'Nao foi possivel conectar ao backend para carregar promocoes.'
          );
      } finally {
          if (avisoTimer) clearTimeout(avisoTimer);
      }
  }

  function perguntarSelecionarPromocaoFavoritos(campanhas) {
      const lista = Array.isArray(campanhas) ? campanhas.filter(campanha => campanha && campanha.id) : [];
      if (!lista.length) return Promise.resolve(null);
      if (!mlFavoritosBalloonEl || !mlFavoritosBalloonActionsEl) {
          const texto = lista.map((campanha, index) => `${index + 1}. ${nomePromocaoFavoritos(campanha)}`).join('\n');
          const resposta = window.prompt(`Escolha a promocao ativa:\n${texto}`, '1');
          if (resposta === null) return Promise.resolve(null);
          const idx = Number(String(resposta).trim()) - 1;
          return Promise.resolve(lista[idx] || null);
      }
      if (mlFavoritosPerguntaResolver) {
          mlFavoritosPerguntaResolver(null);
          mlFavoritosPerguntaResolver = null;
      }
      mlFavoritosBalloonActionsEl.innerHTML = '';
      return new Promise(resolve => {
          mlFavoritosPerguntaResolver = resolve;
          const wrap = document.createElement('div');
          wrap.className = 'ml-favoritos-promo-list';
          const grupos = ['usuario', 'mercado_livre', 'outras'];
          grupos.forEach(grupo => {
              const itens = lista.filter(campanha => grupoPromocaoFavoritos(campanha) === grupo);
              if (!itens.length) return;
              const grupoEl = document.createElement('div');
              grupoEl.className = 'ml-favoritos-promo-group';
              const titulo = document.createElement('div');
              titulo.className = 'ml-favoritos-promo-group-title';
              titulo.textContent = tituloGrupoPromocaoFavoritos(grupo);
              grupoEl.appendChild(titulo);
              itens.forEach(campanha => {
                  const botao = document.createElement('button');
                  botao.type = 'button';
                  botao.className = 'ml-favoritos-promo-button';
                  botao.textContent = nomePromocaoFavoritos(campanha);
                  botao.addEventListener('click', () => {
                      mlFavoritosPerguntaResolver = null;
                      resolverAcaoBalaoFavoritos(resolve, campanha, botao);
                  });
                  grupoEl.appendChild(botao);
              });
              wrap.appendChild(grupoEl);
          });
          mlFavoritosBalloonActionsEl.appendChild(wrap);
          const cancelar = document.createElement('button');
          cancelar.type = 'button';
          cancelar.textContent = 'Cancelar';
          cancelar.addEventListener('click', () => {
              mlFavoritosPerguntaResolver = null;
              resolverAcaoBalaoFavoritos(resolve, null, cancelar, {
                  esconder: true
              });
          });
          mlFavoritosBalloonActionsEl.appendChild(cancelar);
          mostrarBalaoFavoritosStatus('Selecione a promocao ativa que deseja usar.', {
              manterAcoes: true,
              larga: true
          });
      });
  }

  function perguntarContinuarSemPromocaoFavoritos() {
      if (!mlFavoritosBalloonEl || !mlFavoritosBalloonActionsEl) {
          return Promise.resolve(window.confirm('Nenhuma promocao ativa foi encontrada. Deseja continuar sem promocao?') ? { usar_promocao: false } : null);
      }
      if (mlFavoritosPerguntaResolver) {
          mlFavoritosPerguntaResolver(null);
          mlFavoritosPerguntaResolver = null;
      }
      mlFavoritosBalloonActionsEl.innerHTML = '';
      return new Promise(resolve => {
          mlFavoritosPerguntaResolver = resolve;
          const continuar = document.createElement('button');
          continuar.type = 'button';
          continuar.textContent = 'Continuar sem promocao';
          continuar.addEventListener('click', () => {
              mlFavoritosPerguntaResolver = null;
              resolverAcaoBalaoFavoritos(resolve, { usar_promocao: false }, continuar);
          });
          const cancelar = document.createElement('button');
          cancelar.type = 'button';
          cancelar.textContent = 'Cancelar';
          cancelar.addEventListener('click', () => {
              mlFavoritosPerguntaResolver = null;
              resolverAcaoBalaoFavoritos(resolve, null, cancelar, {
                  esconder: true
              });
          });
          mlFavoritosBalloonActionsEl.appendChild(continuar);
          mlFavoritosBalloonActionsEl.appendChild(cancelar);
          mostrarBalaoFavoritosStatus('Nenhuma promocao ativa foi encontrada para essa loja.', {
              manterAcoes: true
          });
      });
  }

  function perguntarModoDescontoPromocaoFavoritos(campanha) {
      if (!mlFavoritosBalloonEl || !mlFavoritosBalloonActionsEl) {
          const percentual = window.prompt('Informe a % fixa de desconto:', '21');
          if (percentual === null) return Promise.resolve(null);
          const numero = Number(String(percentual).replace(',', '.').trim());
          if (!Number.isFinite(numero) || numero <= 0) return Promise.resolve(null);
          return Promise.resolve({ modo: 'percentual_fixo', percentual: numero });
      }
      if (mlFavoritosPerguntaResolver) {
          mlFavoritosPerguntaResolver(null);
          mlFavoritosPerguntaResolver = null;
      }
      mlFavoritosBalloonActionsEl.innerHTML = '';
      return new Promise(resolve => {
          mlFavoritosPerguntaResolver = resolve;
          const linha = document.createElement('div');
          linha.className = 'ml-favoritos-promo-percent';
          const label = document.createElement('label');
          label.textContent = '% fixa';
          const input = document.createElement('input');
          input.type = 'number';
          input.min = '1';
          input.max = '99';
          input.step = '0.1';
          input.value = '21';
          linha.appendChild(label);
          linha.appendChild(input);
          mlFavoritosBalloonActionsEl.appendChild(linha);

          const fixa = document.createElement('button');
          fixa.type = 'button';
          fixa.textContent = 'Usar % fixa';
          fixa.addEventListener('click', () => {
              const numero = Number(String(input.value || '').replace(',', '.').trim());
              if (!Number.isFinite(numero) || numero <= 0) {
                  input.focus();
                  return;
              }
              mlFavoritosPerguntaResolver = null;
              resolverAcaoBalaoFavoritos(resolve, { modo: 'percentual_fixo', percentual: numero }, fixa);
          });
          const cancelar = document.createElement('button');
          cancelar.type = 'button';
          cancelar.textContent = 'Cancelar';
          cancelar.addEventListener('click', () => {
              mlFavoritosPerguntaResolver = null;
              resolverAcaoBalaoFavoritos(resolve, null, cancelar, {
                  esconder: true
              });
          });
          mlFavoritosBalloonActionsEl.appendChild(fixa);
          mlFavoritosBalloonActionsEl.appendChild(cancelar);
          mostrarBalaoFavoritosStatus(`Promocao selecionada: ${nomePromocaoFavoritos(campanha)}. Informe a porcentagem da campanha para calcular o preco cheio do anuncio.`, {
              manterAcoes: true,
              larga: true
          });
          setTimeout(() => input.focus(), 50);
      });
  }

  async function perguntarOpcoesPromocaoFavoritos(opcoes = {}) {
      const exigirPromocao = !!(opcoes && opcoes.exigirPromocao);
      const usarPromocao = exigirPromocao ? true : false;
      if (usarPromocao === null) return null;
      if (!usarPromocao) return { usar_promocao: false };
      const loja = favoritosLojaSelecionadaParaApi(
          (opcoes && opcoes.loja)
          || favMlLojaSelecionada
          || mlSkuLojaSelecionada
          || skuLojaSelecionada
          || ''
      );
      if (!loja) {
          mostrarBalaoFavoritosStatus('Escolha uma loja do Mercado Livre antes de selecionar promocao.', {
              erro: true,
              tempoMs: 4500
          });
          return null;
      }
      mostrarBalaoFavoritosStatus(`Carregando promocoes ativas da loja ${loja}...`);
      const chaveCache = skuNormalizarLoja(loja);
      let campanhas = [];
      try {
          campanhas = favMlPromocoesPorLojaCache.get(chaveCache) || [];
          if (!Array.isArray(campanhas) || !campanhas.length) {
              campanhas = await carregarPromocoesAtivasFavoritos(loja, {
                  onLento: () => mostrarBalaoFavoritosStatus('Mercado Livre demorou para responder, tentando novamente...')
              });
              if (Array.isArray(campanhas) && campanhas.length) {
                  favMlPromocoesPorLojaCache.set(chaveCache, campanhas);
              }
          }
      } catch (err) {
          mostrarBalaoFavoritosStatus(`Erro ao carregar promocoes: ${err && err.message ? err.message : err}`, {
              erro: true
          });
          return null;
      }
      if (!campanhas.length) {
          if (exigirPromocao) {
              mostrarBalaoFavoritosStatus('Nenhuma promocao ativa foi encontrada para escolher campanha e porcentagem.', {
                  erro: true,
                  tempoMs: 6500,
                  larga: true
              });
              return null;
          }
          return perguntarContinuarSemPromocaoFavoritos();
      }
      const campanha = await perguntarSelecionarPromocaoFavoritos(campanhas);
      if (!campanha) return null;
      const desconto = await perguntarModoDescontoPromocaoFavoritos(campanha);
      if (!desconto) return null;
      return {
          usar_promocao: true,
          campanha: {
              id: String(campanha.id || '').trim(),
              nome: String(campanha.name || campanha.title || campanha.id || '').trim(),
              tipo: String(campanha.type || campanha.promotion_type || '').trim(),
              status: String(campanha.status || '').trim()
          },
          desconto
      };
  }

  function resumoOpcoesPromocaoFavoritos(opcoes) {
      if (!opcoes || !opcoes.usar_promocao) return 'sem promocao';
      const nome = opcoes.campanha && (opcoes.campanha.nome || opcoes.campanha.id) || 'promocao selecionada';
      if (opcoes.desconto && opcoes.desconto.modo === 'percentual_fixo') {
          return `${nome}, % fixa ${opcoes.desconto.percentual}%`;
      }
      return `${nome}, sugestao do Mercado Livre`;
  }

  Object.assign(internal, {
    nomePromocaoFavoritos,
    grupoPromocaoFavoritos,
    tituloGrupoPromocaoFavoritos,
    perguntarSimNaoPromocaoFavoritos,
    carregarPromocoesAtivasFavoritos,
    perguntarSelecionarPromocaoFavoritos,
    perguntarContinuarSemPromocaoFavoritos,
    perguntarModoDescontoPromocaoFavoritos,
    perguntarOpcoesPromocaoFavoritos,
    resumoOpcoesPromocaoFavoritos
  });
  internal.components.add('05-promotions');
})(window);
