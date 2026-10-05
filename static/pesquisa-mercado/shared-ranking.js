(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.PesquisaMercadoRanking = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    function number(value) {
        if (value === null || value === undefined || value === '') return null;
        if (typeof value === 'number') return Number.isFinite(value) ? value : null;
        let text = String(value).replace(/\s+/g, '').trim();
        const match = text.match(/-?\d[\d.,]*/);
        if (!match) return null;
        text = match[0];
        if (text.includes('.') && text.includes(',')) {
            text = text.lastIndexOf('.') > text.lastIndexOf(',')
                ? text.replace(/,/g, '') : text.replace(/\./g, '').replace(',', '.');
        } else if (text.includes(',')) text = text.replace(/\./g, '').replace(',', '.');
        const result = Number(text);
        return Number.isFinite(result) ? result : null;
    }

    function salesNumber(value) {
        if (value === null || value === undefined) return null;
        if (typeof value === 'number') return Number.isFinite(value) ? value : null;
        const match = String(value).trim().match(/([0-9][0-9.,]*)\s*(k|mil)?\b/i);
        if (!match) return null;
        let text = match[1];
        if (text.includes('.') && text.includes(',')) text = text.lastIndexOf('.') > text.lastIndexOf(',') ? text.replace(/,/g, '') : text.replace(/\./g, '').replace(',', '.');
        else if (text.includes(',')) text = /^\d{1,3}(?:,\d{3})+$/.test(text) ? text.replace(/,/g, '') : text.replace(',', '.');
        else if (text.includes('.')) text = /^\d{1,3}(?:\.\d{3})+$/.test(text) ? text.replace(/\./g, '') : text;
        const total = Number(text) * (/^(?:k|mil)$/i.test(match[2] || '') ? 1000 : 1);
        return Number.isFinite(total) ? Math.round(total) : null;
    }

    // Same local-date interpretation as Favoritos. Callers own source validation.
    function date(value) {
        if (!value) return null;
        if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
        const text = String(value).trim();
        const br = text.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})(?:,?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/);
        let result;
        if (br) result = new Date(Number(br[3].length === 2 ? '20' + br[3] : br[3]), Number(br[2]) - 1, Number(br[1]), Number(br[4] || 0), Number(br[5] || 0), Number(br[6] || 0));
        else {
            const local = text.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2})(?::(\d{2}))?)?/);
            result = local && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(text)
                ? new Date(Number(local[1]), Number(local[2]) - 1, Number(local[3]), Number(local[4] || 0), Number(local[5] || 0), Number(local[6] || 0))
                : new Date(text);
        }
        return Number.isNaN(result.getTime()) ? null : result;
    }

    function calculateMetrics(anuncio, nowMs) {
        const item = anuncio || {};
        const vendas = salesNumber(item.vendas);
        const direct = number(item.media_vendas_mensal ?? item.media_mensal ?? item.ritmo_atual ?? item.ritmo_vendas_mes ?? '');
        const dataCriacao = date(item.data_criacao);
        const elapsed = (nowMs === undefined ? Date.now() : Number(nowMs)) - (dataCriacao ? dataCriacao.getTime() : NaN);
        if (vendas === null || !dataCriacao || !Number.isFinite(elapsed) || elapsed < 0) {
            return { vendas, meses: null, media: direct, dataCriacao };
        }
        const meses = Math.max(1, elapsed / 86400000) / 30.4375;
        const calculated = vendas / meses;
        return { vendas, meses, media: Number.isFinite(calculated) ? calculated : direct, dataCriacao };
    }

    function comparable(value) {
        return value && value.metrica && value.anuncio ? value : {
            anuncio: value || {}, metrica: calculateMetrics(value), index: Number(value && value.ordem_coleta) || 0
        };
    }

    function compareListings(left, right) {
        const a = comparable(left), b = comparable(right);
        const mediaA = Number.isFinite(a.metrica.media) ? a.metrica.media : -1;
        const mediaB = Number.isFinite(b.metrica.media) ? b.metrica.media : -1;
        if (Math.abs(mediaB - mediaA) > 0.0001) return mediaB - mediaA;
        const salesA = Number.isFinite(a.metrica.vendas) ? a.metrica.vendas : -1;
        const salesB = Number.isFinite(b.metrica.vendas) ? b.metrica.vendas : -1;
        if (salesB !== salesA) return salesB - salesA;
        const positionA = Number(a.anuncio.posicao) || 9999;
        const positionB = Number(b.anuncio.posicao) || 9999;
        if (positionA !== positionB) return positionA - positionB;
        return (a.index || 0) - (b.index || 0);
    }

    function rankListings(items, nowMs) {
        const now = nowMs === undefined ? Date.now() : nowMs;
        let rank = 0;
        return (Array.isArray(items) ? items : []).filter(Boolean)
            .map((anuncio, index) => ({ anuncio, index: Number(anuncio.ordem_coleta) || index, metrica: calculateMetrics(anuncio, now) }))
            .sort((a, b) => Number(a.anuncio.rankable === false) - Number(b.anuncio.rankable === false) || compareListings(a, b))
            .map(item => ({
                ...item.anuncio,
                media_vendas_mensal: item.metrica.media,
                meses_desde_criacao: item.metrica.meses,
                rank: item.anuncio.rankable === false ? null : ++rank
            }));
    }

    return Object.freeze({ calculateMetrics, compareListings, rankListings });
});
