const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, '..', 'static', 'importacoes_sku.html');
const html = fs.readFileSync(htmlPath, 'utf8');

const inlineScripts = [...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map((match) => match[1])
    .filter((source) => source.trim());

for (const source of inlineScripts) {
    new Function(source);
}

const requiredSnippets = [
    'id="btnAprovarCompra"',
    'Desfazer aprovação',
    'const aprovar = !itemComCompraAprovada(item);',
    "'/skus/' + encodeURIComponent(sku) + '/aprovacao'",
    "method: 'PATCH'",
    'body: JSON.stringify({ aprovada: aprovar })',
    'getNumber(cardValor && cardValor.textContent)',
    'dados.financeiro_exato === true',
    'dados.imposto_valor',
    'dados.tarifa_ml',
    'dados.frete_ml',
    'dados.valor_liquido',
    'Custo do cadastro:',
    'anuncios_loja: { ...ultimosAnunciosLojaConcorrentes }',
    '.analysis-comparison-card {',
    '.analysis-price-original {',
    '.analysis-promo-badge {',
    'grid-template-columns: 1.15fr 1fr 0.78fr;',
    'min-height: 88px;',
    '.analysis-comparison-title {',
    '((precoCheio - precoAtual) / precoCheio) * 100',
    'const browserApi = electronApi.getMlBrowserItemInfo;',
    'const publicApi = electronApi.getMlPublicItemInfo;',
    '.analysis-promo-row[hidden] {',
    'fallbackNumero !== null && fallbackNumero >= 0',
    'function manterPrecoPlanilhaComPromocao(valorPlanilha, detalheAnuncio)',
    'await executarConsultasPrecosLimitadas(tarefas, 2);',
    '.analysis-offer.is-clickable {',
    "card.setAttribute('role', 'link');",
    "card.setAttribute('tabindex', '0');",
    "window.open(url, '_blank', 'noopener,noreferrer');",
    "typeof event.preventDefault === 'function'",
    "typeof event.stopPropagation === 'function'",
    'const abrirChrome = window.electronAPI && window.electronAPI.openExternalChrome;',
    'configurarCardOfertaClicavel(lbl, labelTexto, v);',
    'enriquecerPrecosAnalise(linksConc),',
    'await salvarAnaliseConcorrentes(listaId, skuNorm);',
    "const erro = await resp.json().catch(() => ({}));",
    'Não foi possível carregar os anúncios e preços dos concorrentes.',
];

for (const snippet of requiredSnippets) {
    if (!html.includes(snippet)) {
        throw new Error(`Trecho obrigatorio ausente: ${snippet}`);
    }
}

if (!/\.analysis-comparison-title\s*\{\s*display:\s*none;/.test(html)) {
    throw new Error('Titulo da comparacao deve permanecer oculto');
}

const forbiddenSnippets = [
    'Aprovar venda',
    'venda_aprovada',
    'vMargemMlb',
    'escolherValorCardMlb',
    'if (!resp.ok) return vazio;',
    '((precoVenda - custoUnitario) / precoVenda) * 100',
    'custo_unitario: custoValido ? custoUnitario : null',
];

for (const snippet of forbiddenSnippets) {
    if (html.includes(snippet)) {
        throw new Error(`Semantica antiga ainda presente: ${snippet}`);
    }
}

for (let index = 1; index <= 5; index += 1) {
    const comparisonCardPattern = new RegExp(
        `<article class="analysis-comparison-card" data-card-id="analise_comparacao_${index}">[\\s\\S]*?`
        + `id="vMlbOriginal${index}"[\\s\\S]*?`
        + `id="vPromoMlb${index}"[\\s\\S]*?`
        + `id="vConcorrenteOriginal${index}"[\\s\\S]*?`
        + `id="vPromoConcorrente${index}"[\\s\\S]*?`
        + `id="vMargemConcorrente${index}"[\\s\\S]*?<\\/div>`,
    );
    if (!comparisonCardPattern.test(html)) {
        throw new Error(`Comparacao ${index} nao contem precos, promocao e margem no mesmo card`);
    }
}

const mainScript = inlineScripts.find((source) => source.includes('function normalizarDetalhePrecoOferta'));
const pricingStart = mainScript.indexOf('function getNumber(');
const pricingEnd = mainScript.indexOf('function setText(', pricingStart);
const pricingFunctions = mainScript.slice(pricingStart, pricingEnd);
const { normalizarDetalhePrecoOferta, manterPrecoPlanilhaComPromocao } = new Function(
    `${pricingFunctions}; return { normalizarDetalhePrecoOferta, manterPrecoPlanilhaComPromocao };`,
)();

const promotional = normalizarDetalhePrecoOferta('R$ 80,00', {
    price: 80,
    original_price: 100,
});
if (!promotional.temPromocao || promotional.precoCheio !== 100 || promotional.precoAtual !== 80 || promotional.desconto !== 20) {
    throw new Error('Calculo de promocao com preco cheio e promocional esta incorreto');
}

const regular = normalizarDetalhePrecoOferta('R$ 100,00', {
    price: 100,
    original_price: 100,
});
if (regular.temPromocao || regular.precoCheio !== null || regular.precoAtual !== 100) {
    throw new Error('Preco regular foi marcado indevidamente como promocao');
}

const invalidZero = normalizarDetalhePrecoOferta('R$ 0,00', {
    price: 0,
    original_price: 100,
});
if (invalidZero.temPromocao || invalidZero.precoAtual !== 0 || invalidZero.precoCheio !== null) {
    throw new Error('Preco zero da planilha deve ser exibido sem promocao');
}

const sheetPromotion = manterPrecoPlanilhaComPromocao('R$ 80,00', {
    precoAtual: 75,
    precoCheio: 100,
    temPromocao: true,
});
if (sheetPromotion.precoAtual !== 80 || sheetPromotion.precoCheio !== 100 || sheetPromotion.desconto !== 20) {
    throw new Error('A promocao substituiu ou ignorou o preco principal da planilha');
}

const clickableStart = mainScript.indexOf('function configurarCardOfertaClicavel');
const clickableEnd = mainScript.indexOf('function setLinkConcorrente', clickableStart);
const clickableSource = mainScript.slice(clickableStart, clickableEnd);
const configurarCardOfertaClicavel = new Function(
    `${clickableSource}; return configurarCardOfertaClicavel;`,
)();
const activeClasses = new Set();
const attributes = new Map();
const anchor = { clicks: 0, click() { this.clicks += 1; } };
const fallbackUrls = [];
const card = {
    onclick: null,
    onkeydown: null,
    classList: {
        toggle(name, enabled) {
            if (enabled) activeClasses.add(name);
            else activeClasses.delete(name);
        },
    },
    setAttribute(name, value) { attributes.set(name, value); },
    removeAttribute(name) { attributes.delete(name); },
};
const label = {
    closest() { return card; },
    querySelector() { return anchor; },
};
const chromeUrls = [];
global.window = {
    electronAPI: {
        openExternalChrome(url) {
            chromeUrls.push(url);
            return Promise.resolve({ success: true, browser: 'chrome' });
        },
    },
    open(url) {
        fallbackUrls.push(url);
        return { opener: {} };
    },
};
const urlAnuncio = 'https://produto.mercadolivre.com.br/MLB-1234567890';
configurarCardOfertaClicavel(label, 'MLB1234567890', urlAnuncio);
if (!activeClasses.has('is-clickable') || attributes.get('role') !== 'link' || attributes.get('tabindex') !== '0') {
    throw new Error('Card com anuncio nao recebeu comportamento clicavel e acessivel');
}
let prevencoes = 0;
let propagacoesInterrompidas = 0;
const criarEvento = (adicionais = {}) => ({
    target: { closest() { return null; } },
    preventDefault() { prevencoes += 1; },
    stopPropagation() { propagacoesInterrompidas += 1; },
    ...adicionais,
});
card.onclick(criarEvento());
card.onclick(criarEvento({ target: { closest(seletor) { return seletor === 'a[href]' ? anchor : null; } } }));
card.onkeydown(criarEvento({ key: 'Enter' }));
card.onkeydown(criarEvento({ key: ' ' }));
if (chromeUrls.length !== 4 || chromeUrls.some((url) => url !== urlAnuncio) || anchor.clicks !== 0) {
    throw new Error('Clique ou teclado no card nao abriu diretamente o anuncio no Chrome');
}
if (prevencoes !== 4 || propagacoesInterrompidas !== 4 || fallbackUrls.length !== 0) {
    throw new Error('Clique no link interno nao foi tratado exclusivamente pelo card');
}
window.electronAPI.openExternalChrome = () => { throw new Error('Chrome indisponivel'); };
card.onclick(criarEvento());
if (fallbackUrls.length !== 1 || fallbackUrls[0] !== urlAnuncio || anchor.clicks !== 0) {
    throw new Error('Fallback do card nao abriu o anuncio com seguranca');
}
configurarCardOfertaClicavel(label, 'Sem anuncio', '');
if (activeClasses.has('is-clickable') || attributes.has('role') || card.onclick !== null) {
    throw new Error('Card sem link permaneceu clicavel');
}
delete global.window;

console.log('OK: layout comparativo, aprovacao de compra, promocao e margem validados.');
