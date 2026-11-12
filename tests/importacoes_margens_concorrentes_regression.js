'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const vm = require('node:vm');

function readHtml(name) {
    return fs.readFileSync(path.join(__dirname, '..', 'static', name), 'utf8');
}

function validarScriptsInline(html, name) {
    const scripts = [...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/gi)]
        .map((match) => match[1])
        .filter((source) => source.trim());
    for (const source of scripts) {
        try {
            new Function(source);
        } catch (error) {
            throw new Error(`JavaScript inline inválido em ${name}: ${error.message}`);
        }
    }
}

const listaHtml = readHtml('importacoes_lista.html');
const skuHtml = readHtml('importacoes_sku.html');
validarScriptsInline(listaHtml, 'importacoes_lista.html');
validarScriptsInline(skuHtml, 'importacoes_sku.html');

const trechosLista = [
    "{ key: 'margens_concorrentes', label: 'Margem concorrentes'",
    'function formatarMargensConcorrentes(item)',
    'item.analise_simulacao_importacao_sku',
    "'concorrente_' + numero",
    'dados.margem_percentual',
    'dados.motivo_indisponivel',
    'dados.tarifa_ml',
    'dados.frete_ml',
    'Custo da importação:',
    "margens_concorrentes: formatarMargensConcorrentes(item)",
    "margens_concorrentes: 'num-center'",
    'class="competitor-margin ',
    'Não analisado',
    'Sem margem',
];

for (const trecho of trechosLista) {
    if (!listaHtml.includes(trecho)) {
        throw new Error(`Trecho obrigatório ausente na lista: ${trecho}`);
    }
}

const colunaMargens = listaHtml.indexOf("{ key: 'margens_concorrentes'");
const colunaAcao = listaHtml.indexOf("{ key: 'acao'", colunaMargens);
if (!(colunaMargens >= 0 && colunaAcao > colunaMargens)) {
    throw new Error('A coluna de margens deve aparecer antes da coluna Ação.');
}

const trechosSku = [
    'function coletarAnaliseConcorrentes()',
    'async function salvarAnaliseConcorrentes(listaId, sku)',
    "'/analise-concorrentes'",
    "method: 'PATCH'",
    'const payload = coletarAnaliseConcorrentes();',
    'body: JSON.stringify(payload)',
    'timeoutMs: 120000',
    'precos_anuncios_loja: precosAnunciosLoja',
    'propria ? analise?.margens_anuncios_loja : analise?.margens',
    'dados?.financeiro_exato === true',
    'dados.imposto_valor',
    'dados.tarifa_ml',
    'dados.frete_ml',
    'function aplicarAnaliseConcorrentes(analise,',
    'const promessaDadosImposto = preencherCampos(item, lista, cad, itens, mapaM3Cadastro);',
    'await promessaDadosImposto;',
    'enriquecerPromocoesSkuEmSegundoPlano(contexto, lista, cad, mapaM3Cadastro, linksConc);',
    'salvarAnaliseConcorrentes(contexto.listaId, contexto.sku)',
    'item.analise_simulacao_importacao_sku',
    "fonte_custo: 'simulacao_importacao_sku'",
    'custo_unitario: ultimoValorUnitarioProduto',
    'void salvarAnaliseConcorrentes(listaId, normSku(sku))',
    'if (dadosSkuVigentes(contextoPersistencia) && contextoPersistencia.podeSalvar !== false) aplicarAnaliseConcorrentes(resultado && resultado.analise_concorrentes);',
];

for (const trecho of trechosSku) {
    if (!skuHtml.includes(trecho)) {
        throw new Error(`Trecho obrigatório ausente no SKU: ${trecho}`);
    }
}

if (skuHtml.includes('((precoVenda - custoUnitario) / precoVenda) * 100')) {
    throw new Error('A tela ainda contém a fórmula simplificada que ignora imposto, tarifa e frete.');
}

const inicioColeta = skuHtml.indexOf('function coletarAnaliseConcorrentes()');
const fimColeta = skuHtml.indexOf('async function salvarAnaliseConcorrentes', inicioColeta);
const valores = {vMlb1: 590, vConcorrente1: 779.9, vConcorrente2: 550, vConcorrente3: 628, vConcorrente4: 620};
const referencia = 'MLB1000000001';
const payloadPausado = vm.runInNewContext(skuHtml.slice(inicioColeta, fimColeta) + '\ncoletarAnaliseConcorrentes();', {
    document: {getElementById: id => valores[id] == null ? null : {textContent: valores[id]}},
    getNumber: valor => typeof valor === 'number' ? valor : null,
    ultimosAnunciosLojaConcorrentes: {concorrente_1: referencia},
    ultimoAnuncioPausadoReferencia: referencia,
    ultimoValorUnitarioProduto: 280.86,
    ultimaVersaoFontesConcorrentes: 1,
    contextoDetalheSku: null,
});
assert.equal(Object.keys(payloadPausado.precos_anuncios_loja).length, 1);
assert.equal(Object.keys(payloadPausado.precos_concorrentes).length, 4);
assert.deepEqual(Object.values(payloadPausado.anuncios_loja), Array(4).fill(referencia));

const inicioPreparacao = skuHtml.indexOf('function prepararComplementosSku(');
const inicioCarregamento = skuHtml.indexOf('async function carregarDetalheSku(');
const salvaIndependente = skuHtml.indexOf('salvarAnaliseConcorrentes(contexto.listaId, contexto.sku)', inicioPreparacao);
const consultaPromocoes = skuHtml.indexOf('enriquecerPromocoesSkuEmSegundoPlano(', inicioCarregamento);
const guardAnalise = skuHtml.slice(skuHtml.indexOf('async function salvarAnaliseConcorrentes'), skuHtml.indexOf('function atualizarCustoTotalProduto'));
if (!(salvaIndependente > inicioPreparacao && salvaIndependente < inicioCarregamento
    && consultaPromocoes > inicioCarregamento) || !guardAnalise.includes('custoSimuladoDisponivel()')) {
    throw new Error('A simulação do SKU exige custo unitário completo e precede as promoções opcionais.');
}

console.log('OK: margens dos cinco concorrentes persistidas e exibidas na lista.');
