(function publicarPeriodosCompletos(global) {
    'use strict';

    const mesesPorPeriodo = Object.freeze({
        '3m': 3,
        '6m': 6,
        '1a': 12,
        '2a': 24
    });
    const diasPorPeriodoLegado = Object.freeze({
        '3m': 90,
        '6m': 180,
        '1a': 365,
        '2a': 730
    });

    function formatarDataLocalIso(data) {
        const ano = String(data.getFullYear()).padStart(4, '0');
        const mes = String(data.getMonth() + 1).padStart(2, '0');
        const dia = String(data.getDate()).padStart(2, '0');
        return `${ano}-${mes}-${dia}`;
    }

    function criarDataLocalIso(valor) {
        const partes = String(valor || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
        if (!partes) return null;

        const ano = Number(partes[1]);
        const mes = Number(partes[2]);
        const dia = Number(partes[3]);
        const data = new Date(ano, mes - 1, dia);
        if (
            data.getFullYear() !== ano
            || data.getMonth() !== mes - 1
            || data.getDate() !== dia
        ) {
            return null;
        }
        return data;
    }

    function calcularQuantidadeMesesCompletos(quantidadeMeses, hoje = new Date()) {
        const quantidade = Number(quantidadeMeses);
        if (
            !Number.isInteger(quantidade)
            || quantidade < 1
            || !(hoje instanceof Date)
            || Number.isNaN(hoje.getTime())
        ) {
            return null;
        }

        const fim = new Date(hoje.getFullYear(), hoje.getMonth(), 0);
        const inicio = new Date(fim.getFullYear(), fim.getMonth() - quantidade + 1, 1);
        return {
            inicio: formatarDataLocalIso(inicio),
            fim: formatarDataLocalIso(fim)
        };
    }

    function calcularPeriodoMesesCompletos(periodo, hoje = new Date()) {
        return calcularQuantidadeMesesCompletos(mesesPorPeriodo[periodo], hoje);
    }

    function calcularPeriodoMaximoMesesCompletos(limites, hoje = new Date()) {
        if (!(hoje instanceof Date) || Number.isNaN(hoje.getTime())) return null;

        const menorData = criarDataLocalIso(limites?.inicio);
        const maiorData = criarDataLocalIso(limites?.fim);
        if (!menorData || !maiorData) return null;

        const inicio = new Date(menorData.getFullYear(), menorData.getMonth(), 1);
        const ultimoMesCompleto = new Date(hoje.getFullYear(), hoje.getMonth(), 0);
        const fimDoUltimoMesComDados = new Date(maiorData.getFullYear(), maiorData.getMonth() + 1, 0);
        const fim = fimDoUltimoMesComDados < ultimoMesCompleto
            ? fimDoUltimoMesComDados
            : ultimoMesCompleto;

        if (inicio > fim) return null;
        return {
            inicio: formatarDataLocalIso(inicio),
            fim: formatarDataLocalIso(fim)
        };
    }

    function diferencaDiasIso(inicioIso, fimIso) {
        const inicio = String(inicioIso || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
        const fim = String(fimIso || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
        if (!inicio || !fim) return null;

        const inicioMs = Date.UTC(Number(inicio[1]), Number(inicio[2]) - 1, Number(inicio[3]));
        const fimMs = Date.UTC(Number(fim[1]), Number(fim[2]) - 1, Number(fim[3]));
        const diferenca = (fimMs - inicioMs) / 86400000;
        return Number.isInteger(diferenca) && diferenca >= 0 ? diferenca : null;
    }

    function deveNormalizarPreferenciaAtalho(preferencia, periodoAtivo) {
        const inicio = String(preferencia?.inicio || '').trim();
        const fim = String(preferencia?.fim || '').trim();
        if (!inicio || !fim) return false;

        const origem = String(preferencia?.origem || '').trim();
        const periodoSalvo = String(preferencia?.periodo || '').trim();
        if (origem === 'atalho') {
            return periodoSalvo === periodoAtivo;
        }
        if (origem) return false;

        const diasLegados = diasPorPeriodoLegado[periodoAtivo];
        if (diasLegados) {
            return diferencaDiasIso(inicio, fim) === diasLegados;
        }
        return periodoAtivo === 'max';
    }

    const api = Object.freeze({
        calcularPeriodoMesesCompletos,
        calcularPeriodoMaximoMesesCompletos,
        calcularQuantidadeMesesCompletos,
        deveNormalizarPreferenciaAtalho
    });

    global.JKVendasPeriodosCompletos = api;
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
})(typeof globalThis !== 'undefined' ? globalThis : this);
