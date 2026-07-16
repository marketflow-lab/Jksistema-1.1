const fs = require('fs');
const path = require('path');

const sourcePath = path.join(__dirname, '..', 'electron_app', 'main', 'modules', 'window.js');
const source = fs.readFileSync(sourcePath, 'utf8');

const requiredSnippets = [
    'function parseMlMoneyValue(value)',
    'function normalizeMlBrowserPriceInfo(source = {})',
    "'.ui-pdp-price__second-line .andes-money-amount'",
    "'.ui-pdp-price__original-value .andes-money-amount'",
    "htmlInfo.source = pagePrices.preco !== null ? 'browser_page'",
    'preco_original: prices.preco_original',
    'discount_pct: prices.discount_pct',
];

for (const snippet of requiredSnippets) {
    if (!source.includes(snippet)) {
        throw new Error(`Trecho obrigatorio ausente na coleta pelo navegador: ${snippet}`);
    }
}

const start = source.indexOf('function parseMlMoneyValue(value)');
const end = source.indexOf('function findMlItemInfoInObject', start);
if (start < 0 || end < 0) throw new Error('Nao foi possivel isolar os normalizadores de preco.');

const functionsSource = source.slice(start, end);
const { parseMlMoneyValue, normalizeMlBrowserPriceInfo } = new Function(
    `${functionsSource}; return { parseMlMoneyValue, normalizeMlBrowserPriceInfo };`,
)();

if (parseMlMoneyValue('R$ 1.299,90') !== 1299.9) {
    throw new Error('Falha ao ler preco brasileiro do DOM.');
}
if (parseMlMoneyValue('112 reais e 50 centavos') !== 112.5) {
    throw new Error('Falha ao ler aria-label de preco.');
}
if (parseMlMoneyValue('R$ 0,00') !== null) {
    throw new Error('Preco zero foi aceito pela coleta do navegador.');
}

const promotional = normalizeMlBrowserPriceInfo({ price: 80, original_price: 100 });
if (
    promotional.preco !== 80
    || promotional.preco_original !== 100
    || promotional.preco_promocional !== 80
    || promotional.discount_pct !== 20
) {
    throw new Error('Preco cheio, promocional ou percentual incorreto.');
}

const regular = normalizeMlBrowserPriceInfo({ price: 100, original_price: 100 });
if (regular.preco !== 100 || regular.preco_original !== '' || regular.preco_promocional !== '') {
    throw new Error('Preco sem promocao foi marcado como promocional.');
}

console.log('OK: coleta de preco pelo navegador validada.');
