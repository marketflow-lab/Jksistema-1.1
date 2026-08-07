const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static', 'vendas', 'grafico.js'), 'utf8');
const htmlStatic = fs.readFileSync(path.join(root, 'static', 'vendas.html'), 'utf8');
const htmlRoot = fs.readFileSync(path.join(root, 'vendas.html'), 'utf8');

assert.doesNotThrow(
    () => new Function(source),
    'grafico.js deve continuar com JavaScript valido'
);
assert.match(source, /data\.estoque_skus_com_saldo/);
assert.match(source, /label:\s*'Unidades vendidas'/);
assert.match(source, /label:\s*'SKUs com estoque'/);
assert.match(source, /yAxisID:\s*'yComparacao'/);
assert.match(source, /text:\s*'Unidades vendidas'/);
assert.match(source, /function renderizarGraficoSkusComEstoque\(/);
assert.match(source, /text:\s*'Quantidade de SKUs'/);
assert.match(source, /Cobertura \$\{lojasComHistorico\}\/\$\{lojas\} lojas/);
assert.match(source, /estoqueMeta\?\.detail/);

const estoqueStart = source.indexOf('function renderizarGraficoEstoque(');
const skusStart = source.indexOf('function renderizarGraficoSkusComEstoque(');
const nextFunction = source.indexOf('function obterLimitesComVendas(', skusStart);
assert.ok(estoqueStart >= 0 && skusStart > estoqueStart && nextFunction > skusStart);

const estoqueChartSource = source.slice(estoqueStart, skusStart);
const skusChartSource = source.slice(skusStart, nextFunction);
assert.doesNotMatch(estoqueChartSource, /label:\s*'SKUs com estoque'/);
assert.match(estoqueChartSource, /label:\s*'Unidades vendidas'/);
assert.match(skusChartSource, /label:\s*'SKUs com estoque'/);
assert.doesNotMatch(skusChartSource, /label:\s*'Unidades vendidas'/);

assert.match(htmlStatic, /id="graficoSkusEstoqueWrapper"/);
assert.match(htmlStatic, /id="graficoSkusEstoque"/);
assert.ok(
    htmlStatic.indexOf('id="graficoSkusEstoqueWrapper"') > htmlStatic.indexOf('id="graficoEstoqueWrapper"'),
    'o grafico de SKUs deve ficar abaixo do grafico de saldo e vendas'
);
assert.strictEqual(htmlRoot, htmlStatic, 'vendas.html e static/vendas.html devem permanecer espelhados');

console.log('vendas_stock_chart_comparison: ok');
