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

    const FIXOS_USD = Object.freeze({ blFee: 80, desconsolidacao: 100, handling: 100 });
    const FIXOS_BRL = Object.freeze({ desembaraco: 900, expediente: 300 });
    const PICKUP_USD = Object.freeze({ fixo: 351.04, porM3: 25.47 });
    const ORIGEM_USD = Object.freeze({ fixo: 350.52, porM3: 12.74 });
    const RODOVIARIO_BRL = Object.freeze({ fixo: 1021, porM3: 509.42 });
    const CAPATAZIA_BRL_POR_M3 = 30;

    function cotacaoResumo(lista, cotacaoAtual) {
        const salva = Math.max(0, numero(lista && (lista.exchange_rate || lista.dolar_hoje || lista.cotacao_dolar || lista.dolar)) ?? 0);
        const atual = Math.max(0, numero(cotacaoAtual) ?? 0);
        // Uma lista usa a precisão da cotação exibida na compra. Um embarque agregado usa a cotação atual.
        return { salva, efetiva: Number(salva.toFixed(2)) || (lista ? Number(atual.toFixed(2)) : atual) };
    }

    function converterUsdParaBrl(valorUsd, cotacao) {
        const valor = numero(valorUsd);
        const taxa = numero(cotacao);
        return valor > 0 && taxa > 0 ? valor * taxa : 0;
    }

    function campoResumo(item, aliases) {
        for (const chave of aliases) {
            const valor = item?.[chave];
            if (valor != null && String(valor).trim() !== '') return valor;
        }
        return null;
    }

    function volumeItemResumo(item) {
        const qtd = numero(item?.Quantidade ?? item?.Qtd);
        if (qtd === 0) return 0;
        if (qtd === null || qtd < 0) return null;
        const status = String(item?.['Cubagem status'] || '').trim().toLowerCase();
        if (status === 'pendente') return null;
        if (status === 'historico') {
            const total = numero(campoResumo(item, ['M³', 'M3', 'm3', 'Estimated CBM', 'Estimed CBM']));
            if (total !== null && total >= 0) return total;
        }
        const individual = numero(campoResumo(item, ['M3 individual', 'm3 individual']));
        const total = individual > 0 ? individual * qtd : null;
        return Number.isFinite(total) ? total : null;
    }

    function cubagemResumo(lista, itens, obterM3Item = volumeItemResumo) {
        const totalSalvo = numero(lista?.total_m3);
        // Nos resumos antigos, o total parcial ainda pode ser completado pela cubagem dos itens.
        const usarResumo = lista?.m3_fonte || lista?.m3_completo === true || itens.length === 0;
        if (usarResumo && totalSalvo !== null && totalSalvo >= 0) {
            const pendentes = numero(lista.m3_pendente_itens) ?? 0;
            return { totalM3: totalSalvo, m3Completo: lista.m3_completo !== false && pendentes === 0 };
        }
        let totalM3 = 0;
        let m3Completo = true;
        for (const item of itens) {
            const volume = obterM3Item(item);
            if (!Number.isFinite(volume)) m3Completo = false;
            else totalM3 += volume;
        }
        return { totalM3, m3Completo };
    }

    function somarTributo(imposto, item, aliases, base) {
        const percentual = numero(campoResumo(item, aliases)) ?? 0;
        const brl = base > 0 && percentual > 0 ? base * (percentual / 100) : 0;
        imposto.percentual += percentual;
        imposto.brl += brl;
        return brl;
    }

    function resumirItens(itens, cotacao) {
        const impostos = {
            ii: { percentual: 0, brl: 0 },
            ipi: { percentual: 0, brl: 0 },
            pis: { percentual: 0, brl: 0 },
            cofins: { percentual: 0, brl: 0 },
            icms: { percentual: 0, brl: 0 },
        };
        const ncms = new Set();
        let totalUsdItens = 0;
        let freteUsd = 0;
        for (const item of itens) {
            const total = numero(item?.['Valor total']) ?? 0;
            const quantidade = numero(item?.Quantidade) ?? 0;
            const unitario = numero(item?.['Valor unidade']) ?? 0;
            const valorUsd = item?.valor_total_informado === true || total > 0 ? total : quantidade * unitario;
            const freteItem = numero(item?.['Frete Internacional']) ?? 0;
            totalUsdItens += valorUsd;
            freteUsd += freteItem;

            const ncm = String(campoResumo(item, ['NCM', 'ncm', 'Codigo NCM', 'Código NCM', 'codigo_ncm', 'código_ncm']) || '')
                .replace(/\D/g, '');
            if (ncm) ncms.add(ncm);

            const produtoBrl = converterUsdParaBrl(valorUsd, cotacao);
            const base = valorUsd > 0 && freteItem >= 0 ? converterUsdParaBrl(valorUsd + freteItem, cotacao) : 0;
            const ii = somarTributo(impostos.ii, item, ['II', 'ii'], base);
            somarTributo(impostos.ipi, item, ['IPI', 'ipi'], produtoBrl + ii);
            somarTributo(impostos.pis, item, ['PIS', 'pis'], base);
            somarTributo(impostos.cofins, item, ['COFINS', 'cofins', 'Cofins'], base);
            somarTributo(impostos.icms, item, ['ICMS', 'icms'], base);
        }
        for (const imposto of Object.values(impostos)) imposto.percentual /= itens.length || 1;
        return { totalUsdItens, freteUsd, impostos, totalNcm: ncms.size };
    }

    function taxaSiscomex(totalNcm) {
        if (totalNcm <= 2) return 115.67 + totalNcm * 38.56;
        if (totalNcm <= 5) return 192.79 + (totalNcm - 2) * 30.85;
        if (totalNcm <= 10) return 285.34 + (totalNcm - 5) * 23.14;
        if (totalNcm <= 20) return 401.04 + (totalNcm - 10) * 15.42;
        if (totalNcm <= 50) return 555.24 + (totalNcm - 20) * 7.71;
        return 786.54 + (totalNcm - 50) * 3.86;
    }

    function calcularTaxasResumo(totalM3, cotacao, totalNcm) {
        const taxaSiscomexBrl = taxaSiscomex(totalNcm);
        const fixosUsd = FIXOS_USD.blFee + FIXOS_USD.desconsolidacao + FIXOS_USD.handling + PICKUP_USD.fixo + ORIGEM_USD.fixo;
        const fixosBrl = FIXOS_BRL.desembaraco + FIXOS_BRL.expediente + RODOVIARIO_BRL.fixo + taxaSiscomexBrl;
        const usdPorM3 = PICKUP_USD.porM3 + ORIGEM_USD.porM3;
        const brlPorM3 = RODOVIARIO_BRL.porM3 + CAPATAZIA_BRL_POR_M3;
        return {
            pickupOrigemBrl: (totalM3 * PICKUP_USD.porM3 + PICKUP_USD.fixo) * cotacao,
            taxasOrigemBrl: (totalM3 * ORIGEM_USD.porM3 + ORIGEM_USD.fixo) * cotacao,
            transporteRodoviarioBrl: totalM3 * RODOVIARIO_BRL.porM3 + RODOVIARIO_BRL.fixo,
            capataziaBrl: totalM3 * CAPATAZIA_BRL_POR_M3,
            taxasFixasBrl: cotacao > 0 ? fixosUsd * cotacao + fixosBrl : 0,
            taxasSobreM3Brl: totalM3 > 0 && cotacao > 0 ? totalM3 * usdPorM3 * cotacao + totalM3 * brlPorM3 : 0,
            fixosUsd: FIXOS_USD,
            fixosBrl: FIXOS_BRL,
            taxaSiscomexBrl,
        };
    }

    function normalizarChaveResumo(chave) {
        return String(chave).normalize('NFD').replace(/[\u0300-\u036f]/g, '')
            .toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
    }

    function lerValorInformado(lista, itens, aliases) {
        if (!lista) return '-';
        const nomes = aliases.map(normalizarChaveResumo);
        for (const registro of [lista, ...itens]) {
            if (!registro || typeof registro !== 'object') continue;
            for (const [chave, bruto] of Object.entries(registro)) {
                const nome = normalizarChaveResumo(chave);
                if (nome && nomes.some(alias => nome.includes(alias) || alias.includes(nome))) {
                    const valor = String(bruto ?? '').trim();
                    if (valor) return valor;
                }
            }
        }
        return '-';
    }

    function calcularResumo({ lista = null, itens, cotacaoAtual, cubagem, obterM3Item, valorPorM3Informado }) {
        const dados = Array.isArray(itens) ? itens : [];
        const { salva: cotacaoSalva, efetiva: cotacaoEfetiva } = cotacaoResumo(lista, cotacaoAtual);
        const { totalM3, m3Completo } = cubagem ?? cubagemResumo(lista, dados, obterM3Item);
        const { totalUsdItens, freteUsd, impostos, totalNcm } = resumirItens(dados, cotacaoEfetiva);
        const totalUsd = numero(lista?.total_usd) ?? totalUsdItens;
        const manualPorM3 = numero(valorPorM3Informado);
        const valorPorM3 = manualPorM3 > 0 ? manualPorM3 : (m3Completo && totalM3 > 0 ? totalUsd / totalM3 : null);
        const vmleBrl = converterUsdParaBrl(totalUsd, cotacaoEfetiva);
        const freteBrl = converterUsdParaBrl(freteUsd, cotacaoEfetiva);
        const seguroUsd = totalUsd > 0 && freteUsd > 0 ? (totalUsd + freteUsd) * 0.0028 : 0;
        const seguroCalculadoBrl = converterUsdParaBrl(seguroUsd, cotacaoEfetiva);
        const seguroInformado = seguroCalculadoBrl > 0 ? '-'
            : lerValorInformado(lista, dados, ['Seguro Internacional 0,28%', 'Seguro Internacional']);
        const seguroBrl = seguroCalculadoBrl || (numero(seguroInformado) ?? 0);
        const vmldBrl = vmleBrl + freteBrl + seguroBrl;
        const honorariosTradingBrl = vmldBrl > 0 ? vmldBrl * 0.03 : 0;
        const honorariosTradingInformados = honorariosTradingBrl > 0 ? '-'
            : lerValorInformado(lista, dados, ['Honorários Trading', 'Honorarios Trading']);
        return {
            cotacaoSalva, cotacaoEfetiva, totalM3, m3Completo, totalUsd, valorPorM3, totalNcm,
            vmleBrl, freteUsd, freteBrl, seguroUsd, seguroBrl, seguroInformado, vmldBrl, impostos,
            armazenagemBrl: vmldBrl > 0 ? vmldBrl * 0.035 : 0,
            honorariosTradingBrl, honorariosTradingInformados,
            afrmmBrl: converterUsdParaBrl(freteUsd * 0.08, cotacaoEfetiva),
            ...calcularTaxasResumo(totalM3, cotacaoEfetiva, totalNcm),
        };
    }

    function calcularFreteUsd({ valorPorM3, m3, freteInformado }) {
        if (Number.isFinite(valorPorM3) && valorPorM3 > 0 && Number.isFinite(m3) && m3 >= 0) {
            const frete = valorPorM3 * m3;
            return Number.isFinite(frete) ? frete : null;
        }
        const informado = numero(freteInformado);
        return informado > 0 ? informado : 0;
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

    window.JKImportacoesCustoSimulado = Object.freeze({
        calcular, calcularFreteUsd, numero, possuiRespostaSim, extrairPrimeiroNumero,
        calcularResumo, cotacaoResumo, converterUsdParaBrl,
    });
})();
