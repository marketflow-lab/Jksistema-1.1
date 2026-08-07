(function (global) {
  'use strict';

  const feature = global.FavoritosV2.promotionEffectuation;
  const internal = feature.internal;
  const state = feature.runtime.state;
  const adapters = feature.runtime.adapters;
  if (internal.components.has('14-merge-policies')) return;

  const normalizarChaveVendedor = (valor) => adapters.normalizarNomeVendedorParaBusca(valor);

  const deveAtualizarVendedor = (atual, fonteAtual, novo, fonteNova) => {
      if (!adapters.hasTexto(novo)) {
          return false;
      }
      if (!adapters.vendedorValido(novo)) {
          return false;
      }
      if (!adapters.hasTexto(atual)) {
          return true;
      }
      if (!adapters.vendedorValido(atual)) {
          return true;
      }
      const atualTexto = internal.normalizarChaveVendedor(atual);
      const novoTexto = internal.normalizarChaveVendedor(novo);
      if (atualTexto === novoTexto) {
          return false;
      }
      const pesoAtual = adapters.pesoFonteVendedor(fonteAtual);
      const pesoNovo = adapters.pesoFonteVendedor(fonteNova);
      if (pesoNovo > pesoAtual) return true;
      if (pesoNovo < pesoAtual) return false;
      const scoreAtual = adapters.scoreNomeVendedor(atual);
      const scoreNovo = adapters.scoreNomeVendedor(novo);
      return scoreNovo > scoreAtual;
  };

  const deveAtualizarVendas = (atual, fonteAtual, novo, fonteNova) => {
      const atualNumero = adapters.parseNumeroVendas(atual);
      const novoNumero = adapters.parseNumeroVendas(novo);
      if (!adapters.hasNumeroVendas(novoNumero)) return false;
      if (!adapters.fonteVendasConfiavel(fonteNova)) return false;
      if (!adapters.hasNumeroVendas(atualNumero)) return true;
      if (adapters.fonteVendasAvantPro(fonteAtual) && !adapters.fonteVendasAvantPro(fonteNova)) return false;
      const pesoAtual = adapters.pesoFonteVendas(fonteAtual);
      const pesoNovo = adapters.pesoFonteVendas(fonteNova);
      return pesoNovo > pesoAtual || (pesoNovo === pesoAtual && atualNumero !== novoNumero);
  };

  Object.assign(internal, {
    normalizarChaveVendedor,
    deveAtualizarVendedor,
    deveAtualizarVendas
  });
  internal.components.add('14-merge-policies');
})(window);
