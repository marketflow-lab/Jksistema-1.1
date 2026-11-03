(function () {
    'use strict';

    const moedaEmCentavos = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2, useGrouping: false });

    function numero(val) {
        if (val === null || val === undefined) return null;
        if (typeof val === 'number') return Number.isFinite(val) ? val : null;
        const txt = String(val).trim();
        if (!txt) return null;
        let s = txt.replace(/\s+/g, '').replace(/R\$/gi, '').replace(/US\$/gi, '').replace(/\$/g, '').replace(/%/g, '');

        // Suporta tanto 1.234,56 quanto 1234.56 sem inflar valores percentuais.
        const hasComma = s.includes(',');
        const hasDot = s.includes('.');
        if (hasComma && hasDot) {
            const lastComma = s.lastIndexOf(',');
            const lastDot = s.lastIndexOf('.');
            if (lastComma > lastDot) {
                s = s.replace(/\./g, '').replace(',', '.');
            } else {
                s = s.replace(/,/g, '');
            }
        } else if (hasComma) {
            s = s.replace(',', '.');
        }

        const n = Number(s);
        return Number.isFinite(n) ? n : null;
    }

    function possuiRespostaSim(valor) {
        return String(valor || '')
            .toUpperCase()
            .split('|')
            .some((parte) => parte.trim() === 'SIM');
    }

    function extrairPrimeiroNumero(valor) {
        const txt = String(valor || '').trim();
        if (!txt || txt === '-') return null;
        const match = txt.replace(/\s+/g, '').match(/-?\d+(?:[.,]\d+)?/);
        if (!match) return null;
        const num = Number(String(match[0]).replace(',', '.'));
        return Number.isFinite(num) ? num : null;
    }

    function calcular({
        totalUsd, freteUsd, cotacao, quantidade, iiPct, ipiPct, pisPct, cofinsPct,
        proporcaoPct, stMg, stPb, mvaMgPct, baseIcmsFallback = null,
        baseCstMgFallback = null, baseCstPbFallback = null, icmsFallback = null,
    }) {
        const fator = Number.isFinite(proporcaoPct) && proporcaoPct > 0 ? proporcaoPct / 100 : 1;
        const produtoBrl = totalUsd > 0 && cotacao > 0 ? totalUsd * cotacao : null;
        const baseUsd = (totalUsd || 0) + (freteUsd || 0);
        const baseTributos = baseUsd > 0 && cotacao > 0 && Number.isFinite(totalUsd) && Number.isFinite(freteUsd)
            ? baseUsd * cotacao * fator : null;
        const produtoProporcional = produtoBrl === null ? null : produtoBrl * fator;
        const tributo = (base, aliquota) => base !== null && Number.isFinite(aliquota)
            ? base * (aliquota / 100) : null;
        const iiBrl = tributo(baseTributos, iiPct);
        const ipiBrl = tributo(produtoProporcional === null ? null : produtoProporcional + (iiBrl || 0), ipiPct);
        const pisBrl = tributo(baseTributos, pisPct);
        const cofinsBrl = tributo(baseTributos, cofinsPct);
        const tributosFederais = (iiBrl || 0) + (ipiBrl || 0) + (pisBrl || 0) + (cofinsBrl || 0);
        const freteBrl = freteUsd > 0 && cotacao > 0 ? freteUsd * cotacao : 0;
        const seguroBrl = baseUsd > 0 && cotacao > 0 ? baseUsd * cotacao * 0.0028 : 0;
        const freteImpostosBrl = produtoProporcional === null ? null
            : produtoProporcional + tributosFederais + freteBrl + seguroBrl;
        const ipiPbMgBrl = freteImpostosBrl !== null && Number.isFinite(ipiPct)
            ? freteImpostosBrl * (ipiPct / 100) - (ipiBrl || 0) : null;
        const icmsRetidoBrl = freteImpostosBrl === null ? null : freteImpostosBrl * 0.04;
        const baseTrade = (produtoProporcional || 0) + freteBrl + seguroBrl;
        const tradeBrl = baseTrade > 0 ? baseTrade * 0.03 : null;

        const mvaAjustadoPct = Number.isFinite(mvaMgPct)
            ? (((1 + mvaMgPct / 100) * ((1 - 0.04) / (1 - 0.18))) - 1) * 100 : null;
        const baseComIpi = freteImpostosBrl !== null && Number.isFinite(ipiPct)
            ? freteImpostosBrl * (1 + ipiPct / 100) : null;
        const baseIcms = freteImpostosBrl ?? baseIcmsFallback;
        const baseCstMg = baseComIpi !== null && Number.isFinite(mvaMgPct)
            ? baseComIpi * (1 + mvaMgPct / 100) : baseCstMgFallback;
        const baseCstPb = baseComIpi !== null && mvaAjustadoPct !== null
            ? baseComIpi * (1 + mvaAjustadoPct / 100) : baseCstPbFallback;
        const baseCalculoIcms = stPb ? baseCstPb : stMg ? (baseCstPb ?? baseCstMg) : baseIcms;
        // As bases do simulador usam o valor exibido nos cards, em centavos.
        const icmsBrl = Number.isFinite(baseCalculoIcms)
            ? Number(moedaEmCentavos.format(baseCalculoIcms)) * 0.18 - Number(moedaEmCentavos.format(icmsRetidoBrl || 0))
            : icmsFallback;
        const completo = [freteUsd, iiPct, ipiPct, pisPct, cofinsPct, icmsBrl].every(Number.isFinite)
            && produtoBrl > 0 && quantidade > 0;
        const custoTotal = completo ? (produtoBrl + tributosFederais + freteBrl + seguroBrl + icmsBrl) / 0.90 : null;
        return {
            custoUnitario: custoTotal === null ? null : Math.round((custoTotal / quantidade) * 100) / 100,
            custoTotal,
            despesasNacionais: custoTotal === null ? null : custoTotal * 0.10,
            iiBrl, ipiBrl, pisBrl, cofinsBrl, freteImpostosBrl, ipiPbMgBrl, icmsRetidoBrl, tradeBrl,
            baseIcms, baseCstMg, baseCstPb, icmsBrl, mvaAjustadoPct,
        };
    }

    window.JKImportacoesCustoSimulado = Object.freeze({ calcular, numero, possuiRespostaSim, extrairPrimeiroNumero });
})();
