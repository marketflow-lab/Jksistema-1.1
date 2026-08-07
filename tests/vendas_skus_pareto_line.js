const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static', 'vendas', 'grafico.js'), 'utf8');
const htmlStatic = fs.readFileSync(path.join(root, 'static', 'vendas.html'), 'utf8');
const htmlRoot = fs.readFileSync(path.join(root, 'vendas.html'), 'utf8');

assert.doesNotThrow(() => new Function(source));
assert.match(source, /data\.estoque_skus_pareto_com_saldo/);
assert.match(source, /label:\s*'SKUs Pareto 80% com estoque'/);
assert.match(source, /borderDash:\s*\[7, 5\]/);
assert.match(source, /Pareto 80%: \$\{Math\.round\(paretoAtual\)\}\/\$\{paretoTotal\} com estoque/);
assert.match(source, /context\.dataset\.label/);
const estoqueStart = source.indexOf('function renderizarGraficoEstoque(');
const paretoStart = source.indexOf('function renderizarGraficoSkusComEstoque(');
const nextFunction = source.indexOf('function obterLimitesComVendas(', paretoStart);
assert.ok(estoqueStart >= 0 && paretoStart > estoqueStart && nextFunction > paretoStart);
assert.doesNotMatch(source.slice(estoqueStart, paretoStart), /paretoTotal/);
assert.match(source.slice(paretoStart, nextFunction), /paretoTotal/);
assert.match(htmlStatic, /SKUs com estoque e Pareto 80%/);
assert.match(htmlStatic, /20260731-vendas-skus-pareto-v1/);
assert.strictEqual(htmlRoot, htmlStatic);

console.log('vendas_skus_pareto_line: ok');
