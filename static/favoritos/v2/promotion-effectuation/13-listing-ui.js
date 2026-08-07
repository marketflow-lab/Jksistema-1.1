(function (global) {
  'use strict';

  const feature = global.FavoritosV2.promotionEffectuation;
  const internal = feature.internal;
  const state = feature.runtime.state;
  const adapters = feature.runtime.adapters;
  if (internal.components.has('13-listing-ui')) return;

  function criarCelulaMargemAnuncioFavoritos(anuncio) {
      const td = document.createElement('td');
      td.className = 'ml-favoritos-margem-cell';
      const margem = adapters.parseMargemAnuncioFavoritos(anuncio && (anuncio.margem_percentual ?? anuncio.margem));
      const badge = document.createElement('span');
      badge.className = 'ml-favoritos-margem-badge';
      if (margem === null) {
          badge.classList.add('is-empty');
          badge.textContent = '-';
          const status = anuncio && anuncio.margem_status && anuncio.margem_status !== 'ok' ? anuncio.margem_status : 'Margem nao calculada para este anuncio.';
          td.title = status;
      } else {
          badge.classList.add(margem >= 0 ? 'is-positive' : 'is-negative');
          badge.textContent = adapters.formatarMargemAnuncioFavoritos(margem);
          const detalhes = [
              anuncio && anuncio.valor_liquido_text ? `Liquido: ${anuncio.valor_liquido_text}` : '',
              anuncio && anuncio.custo_text ? `Custo: ${anuncio.custo_text}` : '',
              anuncio && anuncio.frete_ml_text ? `Frete: ${anuncio.frete_ml_text}` : '',
              anuncio && anuncio.promotion_fee_base_text ? `Tarifa campanha usuario: ${anuncio.promotion_fee_base_text}` : '',
              anuncio && anuncio.promotion_fee_ml_text ? `Tarifa campanha ML: ${anuncio.promotion_fee_ml_text}` : '',
              anuncio && anuncio.ad_cost_original_text ? `Tarifa original: ${anuncio.ad_cost_original_text}` : '',
              anuncio && anuncio.promotion_fee_discount_applied && anuncio.promotion_fee_discount_text ? `Desc. tarifa promo: ${anuncio.promotion_fee_discount_text}` : '',
              anuncio && anuncio.tarifa_ml_text ? `Tarifa: ${anuncio.tarifa_ml_text}` : '',
              anuncio && anuncio.imposto_valor_text ? `Imposto: ${anuncio.imposto_valor_text}` : '',
          ].filter(Boolean);
          td.title = detalhes.join(' | ');
      }
      td.appendChild(badge);
      return td;
  }

  function normalizarTipoAnuncioFavoritos(valor) {
      if (valor === null || valor === undefined || valor === '') return '';
      let bruto = valor;
      if (typeof bruto === 'object') {
          bruto = bruto.name || bruto.label || bruto.title || bruto.id || '';
      }
      const original = String(bruto || '').trim();
      if (!original) return '';
      if (original === '-') return '';
      const texto = adapters.normalizarNomeVendedorParaBusca(original);
      if (!texto) return original;
      const compacto = texto.replace(/\s+/g, '');
      if (texto.includes('premium') || texto.includes('gold pro') || texto === 'pro') return 'Premium';
      if (compacto.includes('classico') || compacto.includes('classic') || texto.includes('gold special') || texto === 'gold') return 'Classico';
      if (texto === 'free' || compacto.includes('gratis') || compacto.includes('gratuito')) return 'Gratis';
      return original;
  }

  function obterParcelamentoSemJurosFavoritos(anuncio) {
      if (!anuncio) return null;
      const camposBooleanos = [
          anuncio.parcelamento_sem_juros,
          anuncio.parcelamentoSemJuros,
          anuncio.installments_sem_juros,
          anuncio.installmentsSemJuros,
          anuncio.sem_juros,
          anuncio.semJuros,
          anuncio.juros_zero
      ];
      for (const valor of camposBooleanos) {
          if (valor === true || valor === false) return valor;
          if (typeof valor === 'number' && Number.isFinite(valor)) return valor !== 0;
          if (typeof valor === 'string') {
              const texto = adapters.normalizarNomeVendedorParaBusca(valor);
              if (['true', 'sim', 'yes', '1', 'sem juros', 'semjuros'].includes(texto)) return true;
              if (['false', 'nao', 'não', 'no', '0', 'com juros'].includes(texto)) return false;
          }
      }
      const installments = anuncio.installments || anuncio.parcelamento || anuncio.installment || null;
      if (installments && typeof installments === 'object') {
          const rate = adapters.parsePrecoAnuncioFavoritos(installments.rate ?? installments.interest_rate ?? installments.interestRate ?? installments.juros ?? '');
          const quantity = Number(installments.quantity ?? installments.installments ?? installments.parcelas ?? 0);
          if (rate !== null) return rate === 0 && (!Number.isFinite(quantity) || quantity > 1);
          if (installments.no_interest === true || installments.noInterest === true || installments.sem_juros === true) return true;
      }
      const textoParcelamento = [
          anuncio.parcelamento_texto,
          anuncio.parcelamentoTexto,
          anuncio.installments_text,
          anuncio.installmentsText,
          anuncio.installments && anuncio.installments.text,
          anuncio.installments && anuncio.installments.description
      ].filter(Boolean).join(' ');
      if (textoParcelamento) {
          const texto = adapters.normalizarNomeVendedorParaBusca(textoParcelamento);
          if (texto.includes('sem juros')) return true;
          if (texto.includes('com juros')) return false;
      }
      return null;
  }

  function inferirTipoPorParcelamentoFavoritos(anuncio) {
      const semJuros = internal.obterParcelamentoSemJurosFavoritos(anuncio);
      if (semJuros === true) return 'Premium';
      if (semJuros === false) return 'Classico';
      return '';
  }

  function obterTipoAnuncioFavoritos(anuncio) {
      if (!anuncio) return '';
      const tipoParcelamento = internal.inferirTipoPorParcelamentoFavoritos(anuncio);
      if (tipoParcelamento) return tipoParcelamento;
      const candidatos = [
          anuncio.listing_type_id,
          anuncio.listingTypeId,
          anuncio.listing_type,
          anuncio.listingType,
          anuncio.tipo_anuncio,
          anuncio.tipoAnuncio,
          anuncio.tipo,
          anuncio.listing_type_name,
          anuncio.listingTypeName
      ];
      for (const candidato of candidatos) {
          const tipo = internal.normalizarTipoAnuncioFavoritos(candidato);
          if (tipo) return tipo;
      }
      return '';
  }

  function listingTypeIdFavoritos(valor) {
      if (!valor) return '';
      let bruto = valor;
      if (typeof bruto === 'object') {
          bruto = bruto.id || bruto.name || bruto.label || bruto.title || '';
      }
      const texto = adapters.normalizarNomeVendedorParaBusca(bruto);
      const compacto = texto.replace(/\s+/g, '');
      if (!texto) return '';
      if (texto.includes('gold pro') || texto.includes('gold_pro') || compacto.includes('goldpro') || texto.includes('premium') || texto === 'pro') return 'gold_pro';
      if (texto.includes('gold special') || texto.includes('gold_special') || compacto.includes('goldspecial') || compacto.includes('classico') || compacto.includes('classic') || texto === 'gold') return 'gold_special';
      if (texto === 'free' || compacto.includes('gratis') || compacto.includes('gratuito')) return 'free';
      return '';
  }

  function nomeTipoPorListingTypeFavoritos(listingTypeId) {
      const id = internal.listingTypeIdFavoritos(listingTypeId);
      if (id === 'gold_pro') return 'Premium';
      if (id === 'gold_special') return 'Classico';
      if (id === 'free') return 'Gratis';
      return '';
  }

  function taxaPadraoListingTypeFavoritos(listingTypeId) {
      const id = internal.listingTypeIdFavoritos(listingTypeId);
      if (id === 'gold_pro') return 0.17;
      if (id === 'gold_special') return 0.12;
      if (id === 'free') return 0;
      return null;
  }

  function obterListingTypeIdAnuncioFavoritos(anuncio) {
      if (!anuncio) return '';
      const candidatos = [
          anuncio.listing_type_id,
          anuncio.listingTypeId,
          anuncio.listing_type && anuncio.listing_type.id,
          anuncio.listing_type,
          anuncio.listingType,
          anuncio.tipo_anuncio,
          anuncio.tipoAnuncio,
          anuncio.tipo,
          anuncio.listing_type_name,
          anuncio.listingTypeName,
          internal.obterTipoAnuncioFavoritos(anuncio)
      ];
      for (const candidato of candidatos) {
          const id = internal.listingTypeIdFavoritos(candidato);
          if (id) return id;
      }
      return '';
  }

  function resolverTrocaTipoAnuncioFavoritos(anuncioConta, anuncioRanking, opcoes = {}) {
      const listingTypeIdAlvoRaw = internal.obterListingTypeIdAnuncioFavoritos(anuncioRanking);
      const listingTypeIdAlvo = ['gold_pro', 'gold_special'].includes(listingTypeIdAlvoRaw) ? listingTypeIdAlvoRaw : '';
      const listingTypeIdAtual = internal.obterListingTypeIdAnuncioFavoritos(anuncioConta);
      const tipoAtual = internal.nomeTipoPorListingTypeFavoritos(listingTypeIdAtual) || internal.obterTipoAnuncioFavoritos(anuncioConta);
      if (opcoes && opcoes.manterTipoAtual) {
          return {
              tipoAtual,
              tipoAlvo: tipoAtual,
              listingTypeIdAtual,
              listingTypeIdAlvo: listingTypeIdAtual,
              trocar: false,
              taxaPadraoAlvo: null,
              usarTaxaPadraoAlvo: false
          };
      }
      const tipoAlvo = internal.nomeTipoPorListingTypeFavoritos(listingTypeIdAlvo) || internal.obterTipoAnuncioFavoritos(anuncioRanking);
      const taxaPadraoAlvo = internal.taxaPadraoListingTypeFavoritos(listingTypeIdAlvo);
      const trocar = !!listingTypeIdAlvo && (!listingTypeIdAtual || listingTypeIdAtual !== listingTypeIdAlvo);
      return {
          tipoAtual,
          tipoAlvo,
          listingTypeIdAtual,
          listingTypeIdAlvo,
          trocar,
          taxaPadraoAlvo,
          usarTaxaPadraoAlvo: !!listingTypeIdAlvo && taxaPadraoAlvo !== null && (!listingTypeIdAtual || listingTypeIdAtual !== listingTypeIdAlvo)
      };
  }

  function valorIndicaFullFavoritos(valor) {
      if (valor === true) return true;
      if (valor === false || valor === null || valor === undefined) return false;
      if (Array.isArray(valor)) return valor.some(item => internal.valorIndicaFullFavoritos(item));
      if (typeof valor === 'object') {
          return internal.valorIndicaFullFavoritos(valor.logistic_type)
              || internal.valorIndicaFullFavoritos(valor.logisticType)
              || internal.valorIndicaFullFavoritos(valor.tipo_logistica)
              || internal.valorIndicaFullFavoritos(valor.tags)
              || valor.full === true
              || valor.is_full === true
              || valor.fulfillment === true;
      }
      const texto = adapters.normalizarNomeVendedorParaBusca(valor);
      return texto === 'full'
          || texto === 'mercado livre full'
          || texto === 'mercadolivre full'
          || texto === 'fulfillment'
          || texto.includes(' logistic type fulfillment ')
          || texto.includes(' mercado livre full ');
  }

  function temIndicadorFullFavoritos(anuncio) {
      if (!anuncio) return false;
      const campos = [
          'full', 'is_full', 'isFull', 'meli_full', 'mercado_livre_full',
          'fulfillment', 'envio_full', 'logistic_type', 'logisticType',
          'shipping_logistic_type', 'shippingLogisticType', 'tipo_logistica'
      ];
      if (campos.some(campo => anuncio[campo] !== null && anuncio[campo] !== undefined && anuncio[campo] !== '')) return true;
      const shipping = anuncio.shipping || anuncio.shipping_info || anuncio.shippingInfo || anuncio.envio || null;
      if (shipping && typeof shipping === 'object') {
          return ['logistic_type', 'logisticType', 'tipo_logistica', 'tags', 'full', 'is_full', 'fulfillment']
              .some(campo => shipping[campo] !== null && shipping[campo] !== undefined && shipping[campo] !== '');
      }
      return false;
  }

  function obterFullAnuncioFavoritos(anuncio) {
      if (!anuncio) return false;
      return internal.valorIndicaFullFavoritos([
          anuncio.full,
          anuncio.is_full,
          anuncio.isFull,
          anuncio.meli_full,
          anuncio.mercado_livre_full,
          anuncio.fulfillment,
          anuncio.envio_full,
          anuncio.logistic_type,
          anuncio.logisticType,
          anuncio.shipping_logistic_type,
          anuncio.shippingLogisticType,
          anuncio.tipo_logistica,
          anuncio.shipping,
          anuncio.shipping_info,
          anuncio.shippingInfo,
          anuncio.envio
      ]);
  }

  function fullAnuncioDesconhecidoFavoritos(anuncio) {
      return !!(anuncio && !internal.temIndicadorFullFavoritos(anuncio) && !anuncio._fullAnuncioVerificado);
  }

  function preencherFullAnuncioFavoritos(alvo, fonte) {
      if (!alvo || !fonte) return false;
      let alterou = false;
      if (internal.obterFullAnuncioFavoritos(fonte) && !internal.obterFullAnuncioFavoritos(alvo)) {
          alvo.is_full = true;
          alvo.full = true;
          alterou = true;
      }
      const campos = ['full', 'is_full', 'isFull', 'meli_full', 'mercado_livre_full', 'fulfillment', 'envio_full', 'logistic_type', 'logisticType', 'shipping_logistic_type', 'shippingLogisticType', 'tipo_logistica', 'shipping', 'shipping_info', 'shippingInfo', 'envio'];
      campos.forEach(campo => {
          if ((alvo[campo] === null || alvo[campo] === undefined || alvo[campo] === '') && fonte[campo] !== null && fonte[campo] !== undefined && fonte[campo] !== '') {
              alvo[campo] = fonte[campo];
              alterou = true;
          }
      });
      if (internal.temIndicadorFullFavoritos(fonte)) alvo._fullAnuncioVerificado = true;
      return alterou;
  }

  function obterTipoCompletoAnuncioFavoritos(anuncio) {
      const tipo = internal.obterTipoAnuncioFavoritos(anuncio);
      const full = internal.obterFullAnuncioFavoritos(anuncio);
      return [tipo, full ? 'Full' : ''].filter(Boolean).join(' ');
  }

  function preencherTipoAnuncioFavoritos(alvo, fonte) {
      if (!alvo || !fonte) return false;
      let alterou = false;
      if (internal.preencherFullAnuncioFavoritos(alvo, fonte)) alterou = true;
      const semJuros = internal.obterParcelamentoSemJurosFavoritos(fonte);
      if (semJuros !== null && internal.obterParcelamentoSemJurosFavoritos(alvo) === null) {
          alvo.parcelamento_sem_juros = semJuros;
          alterou = true;
      }
      const tipo = internal.obterTipoAnuncioFavoritos(fonte);
      if (tipo && !internal.obterTipoAnuncioFavoritos(alvo)) {
          alvo.tipo_anuncio = tipo;
          alvo.listing_type_name = tipo;
          alterou = true;
      }
      const tipoId = fonte.listing_type_id || fonte.listingTypeId || (fonte.listing_type && fonte.listing_type.id) || '';
      if (tipoId && !alvo.listing_type_id) {
          alvo.listing_type_id = tipoId;
          alterou = true;
      }
      return alterou;
  }

  function criarCelulaTipoAnuncioFavoritos(anuncio, vendedor = '') {
      const td = document.createElement('td');
      td.className = 'ml-favoritos-type-cell';
      const tipo = document.createElement('span');
      tipo.className = 'ml-favoritos-type-value';
      tipo.textContent = internal.obterTipoCompletoAnuncioFavoritos(anuncio);
      td.appendChild(tipo);
      const botaoVendedor = adapters.criarBotaoIgnorarVendedorFavoritos(vendedor);
      if (botaoVendedor) {
          const action = document.createElement('div');
          action.className = 'ml-favoritos-seller-action';
          action.appendChild(botaoVendedor);
          td.appendChild(action);
      }
      return td;
  }

  function criarCelulaMediaHistoricoFavoritos(anuncio) {
      const td = document.createElement('td');
      td.className = 'ml-favoritos-media-cell';

      const media = document.createElement('span');
      media.className = 'ml-favoritos-media-main';
      media.textContent = adapters.formatarMediaVendas(anuncio) || '-';
      td.appendChild(media);

      const vendas = internal.formatarQuantidadeVendidaHistoricoFavoritos(anuncio);
      if (vendas) {
          const vendasEl = document.createElement('span');
          vendasEl.className = 'ml-favoritos-media-detail';
          vendasEl.textContent = `Vendas: ${vendas}`;
          td.appendChild(vendasEl);
      }

      const dias = adapters.formatarDiasAnuncio(anuncio);
      if (dias) {
          const diasEl = document.createElement('span');
          diasEl.className = 'ml-favoritos-media-detail';
          diasEl.textContent = `Dias: ${dias}`;
          td.appendChild(diasEl);
      }

      const precos = adapters.obterPrecosAnuncioFavoritos(anuncio);
      if (precos.preco !== null || precos.promocional !== null) {
          const temPromocional = precos.promocional !== null && precos.preco !== null;
          const precoBase = precos.preco !== null ? precos.preco : precos.promocional;
          const precoEl = document.createElement('span');
          precoEl.className = `ml-favoritos-media-price${temPromocional ? ' is-original' : ''}`;
          precoEl.textContent = `Preco: ${window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(precoBase)}`;
          td.appendChild(precoEl);

          if (temPromocional) {
              const promoEl = document.createElement('span');
              promoEl.className = 'ml-favoritos-media-price';
              promoEl.textContent = window.FavoritosV2.execution.publicApi.formatarPrecoFavoritosMl(precos.promocional);
              const descontoTexto = adapters.formatarDescontoPrecoFavoritos(precos.desconto);
              if (descontoTexto) {
                  const descontoEl = document.createElement('span');
                  descontoEl.className = 'ml-favoritos-media-discount';
                  descontoEl.textContent = descontoTexto;
                  promoEl.appendChild(descontoEl);
              }
              td.appendChild(promoEl);
          }
      }

      return td;
  }

  function formatarQuantidadeVendidaHistoricoFavoritos(anuncio) {
      const vendasAvant = adapters.parseVendasAvantPro(anuncio);
      if (vendasAvant !== null) return String(vendasAvant);
      const vendas = adapters.parseNumeroVendas(anuncio && anuncio.vendas);
      return Number.isFinite(vendas) ? String(vendas) : '';
  }

  Object.assign(internal, {
    criarCelulaMargemAnuncioFavoritos,
    normalizarTipoAnuncioFavoritos,
    obterParcelamentoSemJurosFavoritos,
    inferirTipoPorParcelamentoFavoritos,
    obterTipoAnuncioFavoritos,
    listingTypeIdFavoritos,
    nomeTipoPorListingTypeFavoritos,
    taxaPadraoListingTypeFavoritos,
    obterListingTypeIdAnuncioFavoritos,
    resolverTrocaTipoAnuncioFavoritos,
    valorIndicaFullFavoritos,
    temIndicadorFullFavoritos,
    obterFullAnuncioFavoritos,
    fullAnuncioDesconhecidoFavoritos,
    preencherFullAnuncioFavoritos,
    obterTipoCompletoAnuncioFavoritos,
    preencherTipoAnuncioFavoritos,
    criarCelulaTipoAnuncioFavoritos,
    criarCelulaMediaHistoricoFavoritos,
    formatarQuantidadeVendidaHistoricoFavoritos
  });
  internal.components.add('13-listing-ui');
})(window);
