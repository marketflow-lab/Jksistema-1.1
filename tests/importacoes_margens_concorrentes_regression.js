'use strict';

const fs = require('fs');
const path = require('path');

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
    'item.analise_concorrentes',
    "'concorrente_' + numero",
    'dados.margem_percentual',
    'dados.motivo_indisponivel',
    'dados.tarifa_ml',
    'dados.frete_ml',
    'Custo do cadastro:',
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
    'anuncios_loja: { ...ultimosAnunciosLojaConcorrentes }',
    'precos_anuncios_loja: precosAnunciosLoja',
    'ultimaAnaliseConcorrentes.margens_anuncios_loja',
    'dados.financeiro_exato === true',
    'dados.imposto_valor',
    'dados.tarifa_ml',
    'dados.frete_ml',
    'function aplicarAnaliseConcorrentes(analise)',
    'const promessaDadosImposto = preencherCampos(item, lista, cad, itens, mapaM3Cadastro);',
    'await promessaDadosImposto;',
    'enriquecerPromocoesSkuEmSegundoPlano(contexto, lista, cad, mapaM3Cadastro, linksConc);',
    'salvarAnaliseConcorrentes(contexto.listaId, contexto.sku)',
    'item.analise_concorrentes = resultadoAnalise?.analise_concorrentes || item.analise_concorrentes;',
    'aplicarAnaliseConcorrentes(item.analise_concorrentes);',
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

const inicioPreparacao = skuHtml.indexOf('function prepararComplementosSku(');
const inicioCarregamento = skuHtml.indexOf('async function carregarDetalheSku()');
const salvaIndependente = skuHtml.indexOf('salvarAnaliseConcorrentes(contexto.listaId, contexto.sku)', inicioPreparacao);
const consultaPromocoes = skuHtml.indexOf('enriquecerPromocoesSkuEmSegundoPlano(', inicioCarregamento);
const guardAnalise = skuHtml.slice(skuHtml.indexOf('async function salvarAnaliseConcorrentes'), skuHtml.indexOf('function atualizarCustoTotalProduto'));
if (!(salvaIndependente > inicioPreparacao && salvaIndependente < inicioCarregamento
    && consultaPromocoes > inicioCarregamento) || guardAnalise.includes('custoComplementosPendente')) {
    throw new Error('Margens do cadastro devem iniciar independentemente dos custos de importação e antes das promoções opcionais.');
}

console.log('OK: margens dos cinco concorrentes persistidas e exibidas na lista.');
