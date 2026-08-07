(function (global) {
  'use strict';

  const feature = global.FavoritosV2.promotionEffectuation;
  const internal = feature.internal;
  const state = feature.runtime.state;
  const adapters = feature.runtime.adapters;
  if (internal.components.has('11-preflight')) return;

  function normalizarNomePromocaoFavoritos(valor) {
      return String(valor || '')
          .normalize('NFD')
          .replace(/[\u0300-\u036f]/g, '')
          .replace(/\s+/g, ' ')
          .trim()
          .toLowerCase();
  }

  async function carregarFavoritosAnunciosSkuTodasContas(sku) {
      const skuSelecionado = String(sku || '').trim();
      if (!skuSelecionado) return [];
      const params = new URLSearchParams({ sku: skuSelecionado });
      params.set('compartilhar_sku', '1');
      params.set('todas_contas', '1');
      const response = await fetch(`/api/favoritos/ml/anuncios-sku?${params.toString()}`, {
          headers: adapters.obterAuthHeaders(),
          cache: 'no-store'
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
          throw new Error(data.detail || `HTTP ${response.status}`);
      }
      return Array.isArray(data.anuncios) ? data.anuncios : [];
  }

  function registroFavoritosExigeTrocaTipoAnuncio(registro) {
      const sim = registro && registro.sim;
      if (!sim) return false;
      const alvo = String(sim.listingTypeIdAlvo || '').trim();
      if (!alvo) return false;
      const atual = String(sim.listingTypeIdAtual || '').trim();
      return !!sim.trocarTipoAnuncio || !atual || atual !== alvo;
  }

  function validacaoPermiteTentativaAposPromocoesFavoritos(registro, validacao) {
      const sim = registro && registro.sim || {};
      const atual = internal.listingTypeIdFavoritos((validacao && validacao.current) || sim.listingTypeIdAtual);
      const alvo = internal.listingTypeIdFavoritos((validacao && validacao.target) || sim.listingTypeIdAlvo);
      return atual
          && alvo
          && atual !== alvo
          && ['gold_pro', 'gold_special'].includes(atual)
          && ['gold_pro', 'gold_special'].includes(alvo);
  }

  function textoTipoEnvioFavoritos(registro) {
      const sim = registro && registro.sim;
      if (!sim) return 'sem troca';
      if (sim.trocaTipoAposPromocaoMl) {
          return `${sim.tipoAnuncioAtual || '-'} -> ${sim.tipoAnuncioAlvo || '-'} via API ML`;
      }
      if (sim.tipoMantidoPorBloqueioMl) {
          const atual = sim.tipoAnuncioAtual || internal.nomeTipoPorListingTypeFavoritos(sim.listingTypeIdAtual) || '-';
          const original = sim.tipoAnuncioAlvoOriginal || sim.tipoAnuncioAlvo || '';
          return original ? `mantendo ${atual}; ML bloqueou ${atual} -> ${original}` : `mantendo ${atual}`;
      }
      return internal.registroFavoritosExigeTrocaTipoAnuncio(registro)
          ? `${sim.tipoAnuncioAtual || '-'} -> ${sim.tipoAnuncioAlvo || '-'}`
          : 'sem troca';
  }

  async function validarRegistrosEfetivaveisFavoritosMercadoLivre(registros) {
      const lista = Array.isArray(registros) ? registros : [];
      const paraValidar = lista.filter(registro => {
          return !!(registro && registro.itemId && registro.loja);
      });
      if (!paraValidar.length) {
          return { validos: lista, bloqueados: [] };
      }
      window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Validando no Mercado Livre o estado atual dos anuncios...', {
          larga: true
      });
      const response = await fetch('/api/favoritos/ml/validar-efetivacao', {
          method: 'POST',
          headers: {
              'Content-Type': 'application/json',
              ...obterAuthHeaders()
          },
          body: JSON.stringify({
              itens: paraValidar.map(registro => ({
                  loja: registro.loja || '',
                  item_id: registro.itemId || '',
                  preco_anuncio_alvo: (registro.sim && registro.sim.preco) ?? null,
                  listing_type_id_alvo: registro.sim && registro.sim.listingTypeIdAlvo || '',
                  tipo_anuncio_alvo: registro.sim && registro.sim.tipoAnuncioAlvo || ''
              }))
          })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
          throw new Error(data.detail || `HTTP ${response.status}`);
      }
      const respostas = Array.isArray(data.itens) ? data.itens : [];
      const mapa = new Map();
      respostas.forEach(item => {
          const chave = `${adapters.skuNormalizarLoja(item && item.loja)}|${String(item && item.item_id || '').trim().toUpperCase()}`;
          if (chave) mapa.set(chave, item);
      });
      const validos = [];
      const bloqueados = [];
      lista.forEach(registro => {
          const sim = registro && registro.sim;
          const chave = `${adapters.skuNormalizarLoja(registro.loja)}|${String(registro.itemId || '').trim().toUpperCase()}`;
          const validacao = mapa.get(chave);
          if (!validacao) {
              bloqueados.push({
                  itemId: registro.itemId,
                  erro: 'O sistema nao conseguiu confirmar o estado atual deste anuncio no Mercado Livre.',
                  registro,
                  validacao: null
              });
              return;
          }
          if (validacao.ok !== false) {
              validos.push(registro);
              return;
          }
          if (
              validacao.outcome !== 'blocked_preflight'
              && sim
              && internal.registroFavoritosExigeTrocaTipoAnuncio(registro)
              && internal.validacaoPermiteTentativaAposPromocoesFavoritos(registro, validacao)
          ) {
              sim.trocaTipoAposPromocaoMl = true;
              sim.trocaTipoAposPromocaoMotivo = validacao.message || 'Mercado Livre pode liberar downgrade depois de remover as promocoes atuais.';
              validos.push(registro);
              return;
          }
          const motivo = validacao.message || 'Mercado Livre nao disponibiliza essa troca Premium/Classico para o anuncio agora.';
          bloqueados.push({
              itemId: registro.itemId,
              erro: motivo,
              registro,
              validacao
          });
      });
      return { validos, bloqueados };
  }

  function criarReservasPrecosEfetivacaoFavoritos(registros) {
      const precosFinaisReservados = new Set();
      const precosCheiosReservados = new Set();
      (Array.isArray(registros) ? registros : []).forEach(registro => {
          const sim = registro && registro.sim;
          if (!sim || !sim.ok) return;
          const chaveFinal = adapters.chavePrecoCentavosFavoritos(adapters.obterPrecoFinalSimulacaoFavoritos(sim));
          const chaveCheio = adapters.chavePrecoCentavosFavoritos(sim.preco);
          if (chaveFinal) precosFinaisReservados.add(chaveFinal);
          if (chaveCheio) precosCheiosReservados.add(chaveCheio);
      });
      return { precosFinaisReservados, precosCheiosReservados };
  }

  function bloqueioPermiteFallbackTipoAtualFavoritos(bloqueio) {
      const validacao = bloqueio && bloqueio.validacao || {};
      const registro = bloqueio && bloqueio.registro || {};
      const sim = registro.sim || {};
      const atual = internal.listingTypeIdFavoritos(validacao.current || sim.listingTypeIdAtual);
      const alvo = internal.listingTypeIdFavoritos(validacao.target || sim.listingTypeIdAlvo);
      return atual === 'gold_pro' && alvo === 'gold_special';
  }

  function recalcularRegistroMantendoTipoAtualFavoritos(bloqueio, opcoesPromocao, reservas) {
      const registro = bloqueio && bloqueio.registro;
      if (!registro || !registro.anuncio || !registro.ranking) return null;
      const simOriginal = registro.sim || {};
      const sim = internal.calcularSimulacaoPrecoFavoritos(registro.anuncio, registro.ranking, opcoesPromocao, {
          ...(reservas || {}),
          manterTipoAtual: true
      });
      if (!sim || !sim.ok) {
          return {
              ok: false,
              erro: sim && sim.status
                  ? `Nao foi possivel recalcular mantendo Premium: ${internal.textoCurtoStatusSimuladorFavoritos(sim.status)}`
                  : 'Nao foi possivel recalcular mantendo Premium.'
          };
      }
      const motivo = bloqueio && bloqueio.erro
          ? bloqueio.erro
          : 'Mercado Livre nao liberou a troca Premium -> Classico para este anuncio agora.';
      sim.tipoMantidoPorBloqueioMl = true;
      sim.tipoBloqueioMlMotivo = motivo;
      sim.tipoAnuncioAlvoOriginal = simOriginal.tipoAnuncioAlvo || '';
      sim.listingTypeIdAlvoOriginal = simOriginal.listingTypeIdAlvo || '';
      sim.tipoAnuncioAlvo = sim.tipoAnuncioAtual || internal.nomeTipoPorListingTypeFavoritos(sim.listingTypeIdAtual) || 'Premium';
      sim.listingTypeIdAlvo = sim.listingTypeIdAtual || 'gold_pro';
      sim.trocarTipoAnuncio = false;
      return {
          ok: true,
          registro: {
              ...registro,
              sim,
              fallbackTipoAtualMl: true,
              fallbackTipoAtualMotivo: motivo
          }
      };
  }

  function resolverFallbackTipoAtualFavoritos(bloqueados, validos, opcoesPromocao) {
      const saidaValidos = Array.isArray(validos) ? [...validos] : [];
      const aindaBloqueados = [];
      const fallbacks = [];
      const reservas = internal.criarReservasPrecosEfetivacaoFavoritos(saidaValidos);
      (Array.isArray(bloqueados) ? bloqueados : []).forEach(bloqueio => {
          if (!internal.bloqueioPermiteFallbackTipoAtualFavoritos(bloqueio)) {
              aindaBloqueados.push(bloqueio);
              return;
          }
          const fallback = internal.recalcularRegistroMantendoTipoAtualFavoritos(bloqueio, opcoesPromocao, reservas);
          if (!fallback || !fallback.ok || !fallback.registro) {
              aindaBloqueados.push({
                  ...bloqueio,
                  erro: fallback && fallback.erro || bloqueio.erro || 'Mercado Livre bloqueou a troca de tipo e nao foi possivel recalcular mantendo Premium.'
              });
              return;
          }
          saidaValidos.push(fallback.registro);
          fallbacks.push({
              itemId: fallback.registro.itemId,
              motivo: fallback.registro.fallbackTipoAtualMotivo,
              registro: fallback.registro
          });
      });
      return { validos: saidaValidos, bloqueados: aindaBloqueados, fallbacks };
  }

  async function resolverOpcoesPromocaoEfetivacaoParaLoja(opcoesPromocao, loja) {
      if (!opcoesPromocao || !opcoesPromocao.usar_promocao) return opcoesPromocao;
      const lojaApi = adapters.favoritosLojaSelecionadaParaApi(loja || '');
      const lojaAtual = adapters.favoritosLojaSelecionadaParaApi(state.favMlLojaSelecionada || state.mlSkuLojaSelecionada || state.skuLojaSelecionada || '');
      if (!lojaApi || (lojaAtual && adapters.skuNormalizarLoja(lojaApi) === adapters.skuNormalizarLoja(lojaAtual))) {
          return opcoesPromocao;
      }

      const campanhaOriginal = opcoesPromocao.campanha || {};
      const idOriginal = String(campanhaOriginal.id || campanhaOriginal.campaign_id || '').trim();
      const nomeOriginal = internal.normalizarNomePromocaoFavoritos(campanhaOriginal.nome || campanhaOriginal.name || campanhaOriginal.title || '');
      const tipoOriginal = String(campanhaOriginal.tipo || campanhaOriginal.type || campanhaOriginal.promotion_type || '').trim().toUpperCase();
      const chaveCache = adapters.skuNormalizarLoja(lojaApi);
      let campanhas = state.favMlPromocoesPorLojaCache.get(chaveCache);
      if (!campanhas) {
          campanhas = await window.FavoritosV2.searchRanking.publicApi.promotions.carregarPromocoesAtivasFavoritos(lojaApi);
          state.favMlPromocoesPorLojaCache.set(chaveCache, campanhas);
      }

      const encontrada = campanhas.find(campanha => String(campanha && campanha.id || '').trim() === idOriginal)
          || campanhas.find(campanha => internal.normalizarNomePromocaoFavoritos(campanha && (campanha.name || campanha.title || campanha.nome)) === nomeOriginal)
          || campanhas.find(campanha => {
              const tipo = String(campanha && (campanha.type || campanha.promotion_type) || '').trim().toUpperCase();
              const nome = internal.normalizarNomePromocaoFavoritos(campanha && (campanha.name || campanha.title || campanha.nome));
              return tipoOriginal && tipo === tipoOriginal && nomeOriginal && (nome.includes(nomeOriginal) || nomeOriginal.includes(nome));
          });
      if (!encontrada) {
          throw new Error(`Promocao ${campanhaOriginal.nome || campanhaOriginal.id || ''} nao encontrada na conta ${lojaApi}.`);
      }

      const ajustada = internal.clonarOpcoesPromocaoFavoritos(opcoesPromocao) || {};
      ajustada.campanha = {
          id: String(encontrada.id || '').trim(),
          nome: String(encontrada.name || encontrada.title || encontrada.id || '').trim(),
          tipo: String(encontrada.type || encontrada.promotion_type || '').trim(),
          status: String(encontrada.status || '').trim()
      };
      return ajustada;
  }

  Object.assign(internal, {
    normalizarNomePromocaoFavoritos,
    carregarFavoritosAnunciosSkuTodasContas,
    registroFavoritosExigeTrocaTipoAnuncio,
    validacaoPermiteTentativaAposPromocoesFavoritos,
    textoTipoEnvioFavoritos,
    validarRegistrosEfetivaveisFavoritosMercadoLivre,
    criarReservasPrecosEfetivacaoFavoritos,
    bloqueioPermiteFallbackTipoAtualFavoritos,
    recalcularRegistroMantendoTipoAtualFavoritos,
    resolverFallbackTipoAtualFavoritos,
    resolverOpcoesPromocaoEfetivacaoParaLoja
  });
  internal.components.add('11-preflight');
})(window);
