(function (global) {
  'use strict';

  const feature = global.FavoritosV2.promotionEffectuation;
  if (feature.internal.components.has('01-contracts')) return;
  feature.contracts = Object.freeze({
    schema: 'jk.favoritos.promotion-effectuation.contracts.v1',
    outcomes: Object.freeze(['success', 'pending_review', 'partial_failure', 'partial_unknown', 'failed']),
    endpoints: Object.freeze({
      listingsBySku: '/api/favoritos/ml/anuncios-sku',
      preflight: '/api/favoritos/ml/validar-efetivacao',
      effectuation: '/api/favoritos/ml/efetivar-promocao'
    })
  });
  feature.internal.components.add('01-contracts');
})(window);
