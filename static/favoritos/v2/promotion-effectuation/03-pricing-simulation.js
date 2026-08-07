(function (global) {
  'use strict';

  const feature = global.FavoritosV2.promotionEffectuation;
  const internal = feature.internal;
  const state = feature.runtime.state;
  const adapters = feature.runtime.adapters;
  if (internal.components.has('03-pricing-simulation')) return;

  function calcularPrecoCheioPromocaoFavoritos(precoFinalAlvo, percentual) {
      const alvo = Number(precoFinalAlvo);
      const pct = Number(percentual);
      const fator = 1 - (pct / 100);
      if (!Number.isFinite(alvo) || alvo <= 0 || !Number.isFinite(fator) || fator <= 0) return null;
      const alvoCentavos = Math.round(alvo * 100);
      const centro = Math.max(1, Math.round((alvo / fator) * 100));
      let melhor = null;
      for (let delta = -300; delta <= 300; delta += 1) {
          const baseCentavos = centro + delta;
          if (baseCentavos <= 0) continue;
          const precoCheio = baseCentavos / 100;
          const finalCentavos = Math.round(precoCheio * fator * 100);
          const diffAbs = Math.abs(finalCentavos - alvoCentavos);
          const prioridade = finalCentavos === alvoCentavos ? 0 : (finalCentavos > alvoCentavos ? 1 : 2);
          const candidato = {
              precoCheio,
              precoFinalCalculado: finalCentavos / 100,
              diferenca: (finalCentavos - alvoCentavos) / 100,
              diffAbs,
              prioridade,
              distanciaBase: Math.abs(baseCentavos - centro)
          };
          if (
              !melhor
              || candidato.diffAbs < melhor.diffAbs
              || (candidato.diffAbs === melhor.diffAbs && candidato.prioridade < melhor.prioridade)
              || (candidato.diffAbs === melhor.diffAbs && candidato.prioridade === melhor.prioridade && candidato.distanciaBase < melhor.distanciaBase)
          ) {
              melhor = candidato;
              if (melhor.diffAbs === 0) break;
          }
      }
      return melhor;
  }

  function coletarEntradasSimulacaoFavoritos(anuncioConta, anuncioRanking, opcoes) {
      const ajusteTipoAnuncio = internal.resolverTrocaTipoAnuncioFavoritos(anuncioConta, anuncioRanking, opcoes);
      const precoRanking = adapters.obterPrecoVigenteAnuncioFavoritos(anuncioRanking);
      if (precoRanking === null || precoRanking <= 0.01) {
          return { erro: 'Sem preco do ranking nesta linha.' };
      }
      const custo = adapters.parsePrecoAnuncioFavoritos(anuncioConta && (anuncioConta.custo ?? anuncioConta.custo_unitario ?? ''));
      const frete = adapters.parsePrecoAnuncioFavoritos(anuncioConta && (
          anuncioConta.frete_ml ??
          anuncioConta.shipping_cost ??
          anuncioConta.shipping_seller_cost ??
          anuncioConta.shipping_list_cost ??
          anuncioConta.shipping_base_cost ??
          anuncioConta.frete ??
          anuncioConta.custo_frete ??
          ''
      ));
      let impostoRate = adapters.parseTaxaSimuladorFavoritos(anuncioConta && (
          anuncioConta.imposto_percentual ??
          anuncioConta.imposto_rate ??
          anuncioConta.aliquota_imposto ??
          anuncioConta.imposto ??
          ''
      ));

      const faltando = [];
      if (custo === null) faltando.push('custo');
      if (impostoRate === null) faltando.push('imposto');
      if (frete === null && (anuncioConta && anuncioConta.free_shipping === true)) faltando.push('frete');
      if (faltando.length) {
          return { erro: `Faltando ${faltando.join(', ')}` };
      }
      if (impostoRate === null) impostoRate = 0;
      const precoAtualConta = adapters.obterPrecoVigenteAnuncioFavoritos(anuncioConta);
      const tarifaAtual = adapters.parsePrecoAnuncioFavoritos(anuncioConta && (
          anuncioConta.tarifa_ml ??
          anuncioConta.ad_cost ??
          anuncioConta.fee_per_sale ??
          anuncioConta.sale_fee_amount ??
          ''
      ));
      const taxaFixa = adapters.parsePrecoAnuncioFavoritos(anuncioConta && (anuncioConta.fixed_fee_amount ?? '')) || 0;
      let taxaVariavel = null;
      if (tarifaAtual !== null && precoAtualConta !== null && precoAtualConta > 0) {
          taxaVariavel = Math.max(0, tarifaAtual - taxaFixa) / precoAtualConta;
      }
      if (taxaVariavel === null || !Number.isFinite(taxaVariavel)) {
          taxaVariavel = adapters.parseTaxaSimuladorFavoritos(anuncioConta && (
              anuncioConta.taxa_ml_percentual ??
              anuncioConta.sale_fee_pct ??
              anuncioConta.meli_fee_pct ??
              ''
          ));
      }
      if (taxaVariavel === null || !Number.isFinite(taxaVariavel)) taxaVariavel = 0;
      if (ajusteTipoAnuncio.usarTaxaPadraoAlvo && ajusteTipoAnuncio.taxaPadraoAlvo !== null) {
          taxaVariavel = ajusteTipoAnuncio.taxaPadraoAlvo;
      }
      return {
          ajusteTipoAnuncio,
          precoRanking,
          custo,
          freteCalc: frete !== null ? frete : 0,
          impostoRate,
          taxaFixa,
          taxaVariavel
      };
  }

  function calcularValoresMargemFavoritos(preco, entradas) {
      const tarifa = Math.max(0, entradas.taxaFixa + (preco * entradas.taxaVariavel));
      const imposto = preco * entradas.impostoRate;
      const liquido = preco - entradas.custo - entradas.freteCalc - imposto - tarifa;
      return { tarifa, imposto, liquido, margemCalc: (liquido * 100) / preco };
  }

  function prepararBaseSimulacaoFavoritos(entradas) {
      const margemMinima = 0.15;
      const descontoSorteado = Math.round((Math.floor(Math.random() * 150) + 1)) / 100;
      let precoSimulado = Math.max(0.01, Math.round((entradas.precoRanking - descontoSorteado) * 100) / 100);
      const custoFixo = entradas.custo + entradas.freteCalc + entradas.taxaFixa;
      const denominadorPrecoMinimo = 1 - entradas.impostoRate - entradas.taxaVariavel - margemMinima;
      let limiteMargemAplicado = false;
      if (denominadorPrecoMinimo <= 0) {
          return { erro: 'Nao ha preco que mantenha margem minima de 15% com estes custos.' };
      }
      const precoMinimoMargem = Math.ceil((custoFixo / denominadorPrecoMinimo) * 100) / 100;
      if (Number.isFinite(precoMinimoMargem) && precoSimulado < precoMinimoMargem) {
          precoSimulado = Math.max(0.01, precoMinimoMargem);
          limiteMargemAplicado = true;
      }
      let calculado = internal.calcularValoresMargemFavoritos(precoSimulado, entradas);
      for (let ajuste = 0; calculado.margemCalc < 15 && ajuste < 200; ajuste += 1) {
          precoSimulado = Math.round((precoSimulado + 0.01) * 100) / 100;
          limiteMargemAplicado = true;
          calculado = internal.calcularValoresMargemFavoritos(precoSimulado, entradas);
      }
      if (!Number.isFinite(calculado.margemCalc)) {
          return { erro: 'Nao foi possivel simular a margem.', preco: precoSimulado };
      }
      if (calculado.margemCalc < 15) {
          return { erro: 'Simulacao bloqueada: margem ficaria abaixo de 15%.', preco: precoSimulado };
      }
      return {
          precoSimulado,
          calculado,
          limiteMargemAplicado,
          precoMinimoMargem,
          descontoSorteado,
          denominadorPrecoMinimo
      };
  }

  function recalcularPromocaoSimulacaoFavoritos(simulacao, percentualPromocao) {
      simulacao.precoAnuncioSugerido = simulacao.precoSimulado;
      simulacao.precoPromocionalCalculado = null;
      simulacao.diferencaPromocao = null;
      if (percentualPromocao === null) return;
      const info = internal.calcularPrecoCheioPromocaoFavoritos(simulacao.precoSimulado, percentualPromocao);
      if (!info) return;
      simulacao.precoAnuncioSugerido = info.precoCheio;
      simulacao.precoPromocionalCalculado = info.precoFinalCalculado;
      simulacao.diferencaPromocao = info.diferenca;
  }

  function reservarPrecoUnicoSimulacaoFavoritos(simulacao, entradas, percentualPromocao, opcoes) {
      const precosFinaisReservados = opcoes && opcoes.precosFinaisReservados instanceof Set ? opcoes.precosFinaisReservados : null;
      const precosCheiosReservados = opcoes && opcoes.precosCheiosReservados instanceof Set ? opcoes.precosCheiosReservados : null;
      simulacao.ajustePrecoUnico = 0;
      const precoFinalAtual = () => percentualPromocao !== null
          ? (simulacao.precoPromocionalCalculado ?? simulacao.precoSimulado)
          : simulacao.precoSimulado;
      const chaveRanking = adapters.chavePrecoCentavosFavoritos(entradas.precoRanking);
      const temConflitoPreco = () => {
          const chaveFinal = adapters.chavePrecoCentavosFavoritos(precoFinalAtual());
          const chaveCheio = adapters.chavePrecoCentavosFavoritos(simulacao.precoAnuncioSugerido);
          return !!(
              (chaveRanking && chaveFinal === chaveRanking)
              || (chaveRanking && chaveCheio === chaveRanking)
              || (chaveFinal && precosFinaisReservados && precosFinaisReservados.has(chaveFinal))
              || (chaveCheio && precosCheiosReservados && precosCheiosReservados.has(chaveCheio))
          );
      };
      for (let tentativa = 0; temConflitoPreco() && tentativa < 500; tentativa += 1) {
          simulacao.precoSimulado = Math.round((simulacao.precoSimulado + 0.01) * 100) / 100;
          simulacao.ajustePrecoUnico = Math.round((simulacao.ajustePrecoUnico + 0.01) * 100) / 100;
          simulacao.calculado = internal.calcularValoresMargemFavoritos(simulacao.precoSimulado, entradas);
          internal.recalcularPromocaoSimulacaoFavoritos(simulacao, percentualPromocao);
      }
      if (temConflitoPreco()) return false;
      if (precosFinaisReservados) {
          const chaveFinal = adapters.chavePrecoCentavosFavoritos(precoFinalAtual());
          if (chaveFinal) precosFinaisReservados.add(chaveFinal);
      }
      if (precosCheiosReservados) {
          const chaveCheio = adapters.chavePrecoCentavosFavoritos(simulacao.precoAnuncioSugerido);
          if (chaveCheio) precosCheiosReservados.add(chaveCheio);
      }
      return true;
  }

  function calcularSimulacaoPrecoFavoritos(anuncioConta, anuncioRanking, opcoesPromocao = null, opcoes = {}) {
      if (internal.promocaoFavoritosSemPercentualFixo(opcoesPromocao)) {
          return { ok: false, status: 'Promocao selecionada sem % fixa. Refaca o favorito informando a porcentagem da campanha.' };
      }
      const entradas = internal.coletarEntradasSimulacaoFavoritos(anuncioConta, anuncioRanking, opcoes);
      if (entradas.erro) return { ok: false, status: entradas.erro };
      const base = internal.prepararBaseSimulacaoFavoritos(entradas);
      if (base.erro) return { ok: false, preco: base.preco, status: base.erro };
      const percentualPromocao = internal.obterPercentualPromocaoFixaFavoritos(opcoesPromocao);
      const simulacao = { ...base, precoAnuncioSugerido: base.precoSimulado, precoPromocionalCalculado: null, diferencaPromocao: null };
      internal.recalcularPromocaoSimulacaoFavoritos(simulacao, percentualPromocao);
      if (!internal.reservarPrecoUnicoSimulacaoFavoritos(simulacao, entradas, percentualPromocao, opcoes)) {
          return { ok: false, preco: simulacao.precoAnuncioSugerido, status: 'Simulacao bloqueada: nao foi possivel gerar preco unico para este anuncio.' };
      }
      const precoAlvoCustoIdeal = Math.max(0.01, (Math.round(entradas.precoRanking * 100) - 1) / 100);
      const custoIdealAbaixoBase = Math.floor(((precoAlvoCustoIdeal * base.denominadorPrecoMinimo) - entradas.freteCalc - entradas.taxaFixa) * 100) / 100;
      const reducaoCustoIdeal = custoIdealAbaixoBase !== null
          ? Math.max(0, Math.ceil((entradas.custo - custoIdealAbaixoBase) * 100) / 100)
          : null;
      return {
          ok: true,
          preco: simulacao.precoAnuncioSugerido,
          precoCompetitivo: simulacao.precoSimulado,
          precoPromocional: percentualPromocao !== null ? simulacao.precoSimulado : null,
          precoPromocionalCalculado: simulacao.precoPromocionalCalculado,
          percentualPromocao,
          assinaturaPromocao: internal.assinaturaOpcoesPromocaoFavoritos(opcoesPromocao),
          tipoAnuncioAtual: entradas.ajusteTipoAnuncio.tipoAtual,
          tipoAnuncioAlvo: entradas.ajusteTipoAnuncio.tipoAlvo,
          listingTypeIdAtual: entradas.ajusteTipoAnuncio.listingTypeIdAtual,
          listingTypeIdAlvo: entradas.ajusteTipoAnuncio.listingTypeIdAlvo,
          trocarTipoAnuncio: entradas.ajusteTipoAnuncio.trocar,
          taxaTipoAnuncioAplicada: entradas.ajusteTipoAnuncio.usarTaxaPadraoAlvo ? entradas.ajusteTipoAnuncio.taxaPadraoAlvo : null,
          diferencaPromocao: simulacao.diferencaPromocao,
          margem: simulacao.calculado.margemCalc,
          valorLiquido: simulacao.calculado.liquido,
          tarifa: simulacao.calculado.tarifa,
          impostoValor: simulacao.calculado.imposto,
          frete: entradas.freteCalc,
          custo: entradas.custo,
          precoRanking: entradas.precoRanking,
          descontoSorteado: base.descontoSorteado,
          descontoEfetivo: Math.round((entradas.precoRanking - simulacao.precoSimulado) * 100) / 100,
          ajustePrecoUnico: simulacao.ajustePrecoUnico,
          limiteMargemAplicado: base.limiteMargemAplicado,
          precoMinimoMargem: base.precoMinimoMargem,
          precoAlvoCustoIdeal,
          custoIdealAbaixoBase,
          reducaoCustoIdeal
      };
  }

  function obterLojaEfetivarFavoritos(anuncioConta) {
      return adapters.favoritosLojaSelecionadaParaApi(
          (anuncioConta && (anuncioConta.loja || anuncioConta.loja_sync || anuncioConta.loja_conta))
          || state.favMlLojaSelecionada
          || state.mlSkuLojaSelecionada
          || state.skuLojaSelecionada
          || ''
      );
  }

  function escolherPrecoObservadoEfetivacaoFavoritos(alvo, valores = []) {
      const alvoNumero = adapters.parsePrecoAnuncioFavoritos(alvo);
      const observados = (Array.isArray(valores) ? valores : [])
          .map(adapters.parsePrecoAnuncioFavoritos)
          .filter(valor => valor !== null && Number.isFinite(valor) && valor > 0);
      if (!observados.length) return null;
      if (alvoNumero === null) return observados[0];
      return observados.sort((a, b) => Math.abs(a - alvoNumero) - Math.abs(b - alvoNumero))[0];
  }

  function obterPrecosObservadosEfetivacaoFavoritos(data = {}) {
      const fallbackSemPromocao = !!data.fallback_sem_promocao_aplicado;
      const segurancaComercial = data.commercial_safety && typeof data.commercial_safety === 'object'
          ? data.commercial_safety
          : {};
      const salePriceSeguranca = segurancaComercial.sale_price && typeof segurancaComercial.sale_price === 'object'
          ? segurancaComercial.sale_price
          : {};
      const autoritativos = data.observados_autoritativos && typeof data.observados_autoritativos === 'object'
          ? data.observados_autoritativos
          : {};
      const verificacao = data.verificacao && typeof data.verificacao === 'object' ? data.verificacao : {};
      const priceInfo = verificacao.price_info && typeof verificacao.price_info === 'object' ? verificacao.price_info : {};
      const confirmacao = (fallbackSemPromocao ? data.preco_confirmacao_fallback : data.preco_confirmacao) || {};
      const confirmacaoInfo = confirmacao.price_info && typeof confirmacao.price_info === 'object' ? confirmacao.price_info : {};
      const candidatosBase = [
          verificacao.standard_price,
          priceInfo.standard_price,
          priceInfo.original_price,
          confirmacao.standard_price,
          confirmacao.original_price,
          confirmacao.base_price,
          confirmacaoInfo.standard_price,
          confirmacaoInfo.original_price
      ];
      const candidatosDiretos = [
          confirmacao.standard_price,
          confirmacao.original_price,
          confirmacao.item_price,
          confirmacao.base_price,
          confirmacaoInfo.standard_price,
          confirmacaoInfo.original_price,
          confirmacaoInfo.price,
          confirmacaoInfo.sale_price
      ];
      const precoDiretoFallback = fallbackSemPromocao
          ? (adapters.parsePrecoAnuncioFavoritos(salePriceSeguranca.amount)
              ?? adapters.parsePrecoAnuncioFavoritos(salePriceSeguranca.price)
              ?? adapters.parsePrecoAnuncioFavoritos(typeof segurancaComercial.sale_price !== 'object' ? segurancaComercial.sale_price : null)
              ?? adapters.parsePrecoAnuncioFavoritos(autoritativos.final_price)
              ?? internal.escolherPrecoObservadoEfetivacaoFavoritos(data.preco_anuncio, candidatosDiretos))
          : null;
      return {
          fallbackSemPromocao,
          precoCheio: fallbackSemPromocao
              ? precoDiretoFallback
              : (adapters.parsePrecoAnuncioFavoritos(autoritativos.base_price)
                  ?? internal.escolherPrecoObservadoEfetivacaoFavoritos(data.preco_anuncio, candidatosBase)),
          precoPromocional: fallbackSemPromocao
              ? null
              : (adapters.parsePrecoAnuncioFavoritos(salePriceSeguranca.amount)
                  ?? adapters.parsePrecoAnuncioFavoritos(salePriceSeguranca.price)
                  ?? adapters.parsePrecoAnuncioFavoritos(typeof segurancaComercial.sale_price !== 'object' ? segurancaComercial.sale_price : null)
                  ?? adapters.parsePrecoAnuncioFavoritos(autoritativos.final_price)
                  ?? internal.escolherPrecoObservadoEfetivacaoFavoritos(data.preco_promocional, [
                  verificacao.promotion_price_raw,
                  priceInfo.price,
                  priceInfo.sale_price,
                  priceInfo.promotional_price,
                  verificacao.item_price
              ])),
          precoDiretoFallback
      };
  }

  function primeiroNumeroEfetivacaoFavoritos(...valores) {
      for (const valor of valores) {
          const numero = adapters.parsePrecoAnuncioFavoritos(valor);
          if (numero !== null && Number.isFinite(numero)) return numero;
      }
      return null;
  }

  function percentualEntrePrecosEfetivacaoFavoritos(precoBase, precoFinal) {
      const base = adapters.parsePrecoAnuncioFavoritos(precoBase);
      const final = adapters.parsePrecoAnuncioFavoritos(precoFinal);
      if (base === null || final === null || base <= 0 || final < 0 || final > base) return null;
      return Math.round((((base - final) / base) * 100) * 100) / 100;
  }

  function obterComparacaoPromocaoEfetivacaoFavoritos(data = {}, sim = {}) {
      const seguranca = data.commercial_safety && typeof data.commercial_safety === 'object'
          ? data.commercial_safety
          : {};
      const salePriceSeguranca = seguranca.sale_price && typeof seguranca.sale_price === 'object'
          ? seguranca.sale_price
          : {};
      const autoritativos = data.observados_autoritativos && typeof data.observados_autoritativos === 'object'
          ? data.observados_autoritativos
          : {};
      const verificacao = data.verificacao && typeof data.verificacao === 'object' ? data.verificacao : {};
      const priceInfo = verificacao.price_info && typeof verificacao.price_info === 'object' ? verificacao.price_info : {};
      const estado = data.current_state && typeof data.current_state === 'object' ? data.current_state : {};
      const salePriceEstado = estado.sale_price && typeof estado.sale_price === 'object' ? estado.sale_price : {};
      const precoCheioSolicitado = internal.primeiroNumeroEfetivacaoFavoritos(
          data.preco_anuncio_alvo,
          data.preco_anuncio,
          sim.preco
      );
      const precoFinalSolicitado = internal.primeiroNumeroEfetivacaoFavoritos(
          data.preco_promocional_alvo,
          data.preco_ideal,
          data.preco_promocional,
          sim.precoPromocionalCalculado,
          sim.precoPromocional,
          sim.precoCompetitivo
      );
      const precoCheioObservado = internal.primeiroNumeroEfetivacaoFavoritos(
          autoritativos.base_price,
          verificacao.base_price,
          verificacao.standard_price,
          priceInfo.standard_price,
          salePriceSeguranca.regular_amount,
          salePriceEstado.regular_amount,
          estado.regular_amount,
          estado.price,
          data.preco_anuncio_atual
      );
      const precoFinalObservado = internal.primeiroNumeroEfetivacaoFavoritos(
          salePriceSeguranca.amount,
          salePriceSeguranca.price,
          typeof seguranca.sale_price !== 'object' ? seguranca.sale_price : null,
          autoritativos.final_price,
          verificacao.final_price,
          verificacao.promotion_price_raw,
          priceInfo.sale_price,
          priceInfo.promotional_price,
          priceInfo.price,
          salePriceEstado.amount,
          salePriceEstado.price,
          estado.promotional_price,
          typeof estado.sale_price !== 'object' ? estado.sale_price : null
      );
      const percentualSolicitado = internal.primeiroNumeroEfetivacaoFavoritos(
          data.percentual_promocao,
          verificacao.desconto_esperado,
          sim.percentualPromocao,
          internal.percentualEntrePrecosEfetivacaoFavoritos(precoCheioSolicitado, precoFinalSolicitado)
      );
      const percentualObservado = internal.primeiroNumeroEfetivacaoFavoritos(
          seguranca.observed_discount_pct,
          autoritativos.discount_pct,
          verificacao.desconto_info,
          priceInfo.discount_pct,
          internal.percentualEntrePrecosEfetivacaoFavoritos(precoCheioObservado, precoFinalObservado)
      );
      return {
          precoCheioSolicitado,
          precoFinalSolicitado,
          percentualSolicitado,
          precoCheioObservado,
          precoFinalObservado,
          percentualObservado
      };
  }

  Object.assign(internal, {
    calcularPrecoCheioPromocaoFavoritos,
    coletarEntradasSimulacaoFavoritos,
    calcularValoresMargemFavoritos,
    prepararBaseSimulacaoFavoritos,
    recalcularPromocaoSimulacaoFavoritos,
    reservarPrecoUnicoSimulacaoFavoritos,
    calcularSimulacaoPrecoFavoritos,
    obterLojaEfetivarFavoritos,
    escolherPrecoObservadoEfetivacaoFavoritos,
    obterPrecosObservadosEfetivacaoFavoritos,
    primeiroNumeroEfetivacaoFavoritos,
    percentualEntrePrecosEfetivacaoFavoritos,
    obterComparacaoPromocaoEfetivacaoFavoritos
  });
  internal.components.add('03-pricing-simulation');
})(window);
