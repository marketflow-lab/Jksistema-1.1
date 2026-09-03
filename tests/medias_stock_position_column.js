'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const canonicalHtml = fs.readFileSync(path.join(root, 'medias_compras.html'), 'utf8');
const served = fs.readFileSync(path.join(root, 'static', 'medias_compras.html'), 'utf8');
const moduleDirectory = path.join(root, 'static', 'medias_compras');
const extractedSources = fs.readdirSync(moduleDirectory)
  .sort()
  .map(name => fs.readFileSync(path.join(moduleDirectory, name), 'utf8'));
const canonical = [canonicalHtml, ...extractedSources].join('\n');

assert.strictEqual(served, canonicalHtml, 'os espelhos de Médias e Pedidos divergiram');
assert(
  /\*\s*\{[^}]*scrollbar-width:\s*thin;[^}]*scrollbar-color:\s*rgba\(100, 116, 139, 0\.42\) transparent;/s.test(canonical),
  'o módulo deve usar uma barra de rolagem fina e discreta',
);
assert(
  /\*::\-webkit-scrollbar\s*\{[^}]*width:\s*6px;[^}]*height:\s*6px;/s.test(canonical),
  'as barras vertical e horizontal devem ter 6px',
);
assert(
  /\*::\-webkit-scrollbar-track,\s*\*::\-webkit-scrollbar-corner\s*\{[^}]*background:\s*transparent;/s.test(canonical),
  'o trilho e o canto da rolagem devem permanecer transparentes',
);
assert(
  /\*::\-webkit-scrollbar-thumb\s*\{[^}]*background-color:\s*rgba\(100, 116, 139, 0\.42\);[^}]*border-radius:\s*999px;/s.test(canonical),
  'o indicador da rolagem deve ser arredondado e discreto',
);
assert(
  /\*::\-webkit-scrollbar-thumb:hover\s*\{[^}]*background-color:\s*rgba\(96, 165, 250, 0\.58\);/s.test(canonical),
  'a barra deve ganhar um azul suave apenas no hover',
);
assert(canonical.includes("if (colKey === 'posicao_estoque') col.classList.add('col-posicao-estoque')"));
assert(canonical.includes("if (th.dataset.colKey === 'posicao_estoque') th.classList.add('col-posicao-estoque')"));
assert(canonical.includes('<td class="num col-posicao-estoque"><strong>'));
assert(canonical.includes("if (colKey === 'cobertura_meses') col.classList.add('col-cobertura-estoque')"));
assert(canonical.includes("if (th.dataset.colKey === 'cobertura_meses') th.classList.add('col-cobertura-estoque')"));
assert(canonical.includes("'Cobertura (meses)'"));
assert(canonical.includes('<td class="num col-cobertura-estoque"'));
assert(canonical.includes('Posição de estoque dividida pela VMM'));
assert(canonical.includes("col.classList.add('col-venda-mes', (idx - 3) % 2 === 0 ? 'col-venda-mes-a' : 'col-venda-mes-b')"));
assert(canonical.includes("if (idx === 3) col.classList.add('col-venda-mes-primeira')"));
assert(canonical.includes("th.classList.add('col-venda-mes', (idx - inicioMeses) % 2 === 0 ? 'col-venda-mes-a' : 'col-venda-mes-b')"));
assert(canonical.includes("if (idx === inicioMeses) th.classList.add('col-venda-mes-primeira')"));
assert(canonical.includes("const classeMes = idxMes % 2 === 0 ? 'col-venda-mes-a' : 'col-venda-mes-b'"));
assert(canonical.includes("const classePrimeiroMes = idxMes === 0 ? ' col-venda-mes-primeira' : ''"));
assert(canonical.includes('<td class="num col-venda-mes '));
assert(canonical.includes("if (colKey === 'total_periodo') col.classList.add('col-total-periodo')"));
assert(canonical.includes("if (th.dataset.colKey === 'total_periodo') th.classList.add('col-total-periodo')"));
assert(canonical.includes('<td class="num col-total-periodo"><strong>'));
assert(
  /#tblResultado th,\s*#tblResultado td\s*\{[^}]*border-right:\s*1px[^}]*border-bottom:\s*1px/s.test(canonical),
  'toda a tabela principal deve ter delimitadores verticais e horizontais',
);
assert(
  /#tblResultado th:first-child,\s*#tblResultado td:first-child\s*\{[^}]*border-left:\s*1px/s.test(canonical),
  'a grade principal deve fechar a borda esquerda',
);
assert(
  /#tblResultado thead th\.col-venda-mes,\s*#tblResultado tbody td\.col-venda-mes\s*\{[^}]*border-right:\s*1px solid #60a5fa;/s.test(canonical),
  'todas as divisórias das vendas devem usar azul fino',
);
assert(
  /#tblResultado thead th\.col-venda-mes-primeira,\s*#tblResultado tbody td\.col-venda-mes-primeira\s*\{[^}]*border-left:\s*1px solid #60a5fa;/s.test(canonical),
  'a grade de vendas deve começar na borda esquerda do primeiro mês',
);
assert(
  /#tblResultado tbody td\.col-venda-mes\.col-venda-mes-a,\s*#tblResultado tbody td\.col-venda-mes\.col-venda-mes-b\s*\{[^}]*background-image:\s*linear-gradient\(rgba\(125, 211, 252, 0\.06\), rgba\(125, 211, 252, 0\.06\)\) !important;/s.test(canonical),
  'as células de vendas devem receber somente um filtro azul-claro sutil',
);
assert(
  /#tblResultado tbody tr td\.col-total-periodo\s*\{[^}]*background-image:\s*linear-gradient\(rgba\(96, 165, 250, 0\.15\), rgba\(96, 165, 250, 0\.15\)\) !important;/s.test(canonical),
  'a coluna Total período deve ter um filtro azul-claro de 15%',
);
assert(!canonical.includes('#7c3aed'), 'a paleta violeta antiga não deve permanecer nas vendas mensais');
for (const color of [
  '#172b4d', '#1b365d', '#244d7a', '#443044', '#3d442f',
  '#1e4165', '#255078', '#30638e', '#473642', '#3f4933',
]) {
  assert(!canonical.includes(color), `o preenchimento mensal antigo não deve permanecer: ${color}`);
}
assert(canonical.includes("if (colKey === 'saldo_estoque') col.classList.add('col-estoque-fisico')"));
assert(canonical.includes("if (th.dataset.colKey === 'saldo_estoque') th.classList.add('col-estoque-fisico')"));
assert(canonical.includes('<td class="num col-estoque-fisico"><strong>'));
assert(
  /th\s*\{[^}]*position:\s*sticky;[^}]*top:\s*0;/s.test(canonical),
  'o cabeçalho principal deve permanecer fixo no topo, sem deslocamento vertical',
);
assert(!canonical.includes('top: 82px;'), 'o deslocamento antigo do cabeçalho não pode permanecer');

const coberturaFnSource = canonical.match(/function calcularCoberturaMeses\(posicaoEstoque, mediaMensal\) \{[\s\S]*?\n    \}/);
assert(coberturaFnSource, 'função de cálculo da cobertura não encontrada');
const calcularCoberturaMeses = new Function(coberturaFnSource[0] + '; return calcularCoberturaMeses;')();
assert.strictEqual(calcularCoberturaMeses(12, 4), 3, '12 unidades com VMM 4 devem durar 3 meses');
assert.strictEqual(calcularCoberturaMeses(0, 4), 0, 'estoque zerado deve ter cobertura zero');
assert.strictEqual(calcularCoberturaMeses(-2, 4), 0, 'estoque negativo deve ter cobertura zero');
assert.strictEqual(calcularCoberturaMeses(12, 0), null, 'sem VMM a cobertura deve ser indeterminada');
assert.strictEqual(calcularCoberturaMeses(12, 'inválido'), null, 'VMM inválida não deve gerar cobertura');

for (const selector of [
  '#tblResultado thead th.col-venda-mes',
  '#tblResultado thead th.col-venda-mes-a',
  '#tblResultado thead th.col-venda-mes-b',
  '#tblResultado thead th.col-estoque-fisico',
  '#tblResultado tbody tr:nth-child(odd) td.col-estoque-fisico',
  '#tblResultado tbody tr:nth-child(even) td.col-estoque-fisico',
  '#tblResultado tbody tr.risco-critico td.col-estoque-fisico',
  '#tblResultado tbody tr.risco-atencao td.col-estoque-fisico',
  '#tblResultado thead th.col-posicao-estoque',
  '#tblResultado tbody tr:nth-child(odd) td.col-posicao-estoque',
  '#tblResultado tbody tr:nth-child(even) td.col-posicao-estoque',
  '#tblResultado tbody tr:hover td.col-posicao-estoque',
  '#tblResultado tbody tr.risco-critico td.col-posicao-estoque',
  '#tblResultado tbody tr.risco-atencao td.col-posicao-estoque',
  '#tblResultado thead th.col-cobertura-estoque',
  '#tblResultado tbody td.col-cobertura-estoque strong',
  '#tblResultado tbody td.col-cobertura-estoque .cobertura-sem-venda',
]) {
  assert(canonical.includes(selector), `estilo ausente para a coluna: ${selector}`);
}

for (const color of [
  '#1d4ed8', '#1e3a8a', '#60a5fa',
  '#3b82f6', '#2563eb', '#93c5fd', '#e0f2fe',
  '#166534', '#14532d', '#173c2d', '#1c4936', '#4ade80', '#86efac',
  '#0f766e', '#164e63', '#103645', '#124252', '#2dd4bf', '#99f6e4',
  '#334155', '#1e3a5f', '#38bdf8', '#bae6fd', '#94a3b8',
]) {
  assert(canonical.includes(color), `cor da paleta da coluna ausente: ${color}`);
}

const scripts = [...canonical.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(match => match[1])
  .filter(source => source.trim());
scripts.forEach((source, index) => {
  assert.doesNotThrow(
    () => new Function(source),
    `script inline ${index + 1} de Médias e Pedidos possui erro de sintaxe`,
  );
});

console.log('medias stock position column: OK');
