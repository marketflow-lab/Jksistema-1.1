'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {
  cadastroScriptPaths,
  inlineScripts,
  read,
  root,
} = require('./helpers/cadastro_frontend_sources');

const fixture = JSON.parse(read('tests/fixtures/cadastro_frontend_contract.json'));
const budgets = fixture.budgets;

function lineCount(source) {
  return source.split(/\r?\n/).length - (source.endsWith('\n') ? 1 : 0);
}

function topLevelFunctionSpans(source) {
  const lines = source.split(/\r?\n/);
  const starts = [];
  lines.forEach((line, index) => {
    const match = /^    (?:async\s+)?function\s+([A-Za-z0-9_$]+)/.exec(line);
    if (match) starts.push({ name: match[1], index });
  });
  return starts.map(start => {
    let end = start.index + 1;
    while (end < lines.length && !/^    }\s*$/.test(lines[end])) end += 1;
    assert(end < lines.length, `fim da função não encontrado: ${start.name}`);
    return { name: start.name, span: end - start.index + 1 };
  });
}

for (const [htmlPath, expectedScripts] of Object.entries(fixture.scripts_by_page)) {
  const html = read(htmlPath);
  assert(lineCount(html) <= budgets.html_lines, `${htmlPath} excede ${budgets.html_lines} linhas`);
  assert.strictEqual(inlineScripts(html).length, 0, `${htmlPath} não pode conter JavaScript inline`);
  assert.doesNotMatch(html, /<style(?:\s|>)/i, `${htmlPath} não pode conter bloco CSS inline`);
  assert.doesNotMatch(html, /\sstyle\s*=/i, `${htmlPath} não pode conter atributo CSS inline`);
  assert.deepStrictEqual(cadastroScriptPaths(html), expectedScripts, `${htmlPath}: ordem de componentes divergente`);
}

const componentRoot = path.join(root, 'static', 'cadastro');
const componentFiles = [];
function collect(directory) {
  fs.readdirSync(directory, { withFileTypes: true }).forEach(entry => {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) collect(absolute);
    else if (entry.isFile() && entry.name.endsWith('.js')) componentFiles.push(absolute);
  });
}
collect(componentRoot);

componentFiles.forEach(absolute => {
  const relative = path.relative(root, absolute).replace(/\\/g, '/');
  const source = fs.readFileSync(absolute, 'utf8');
  const budget = relative.endsWith('/00-runtime.js') ? budgets.runtime_lines : budgets.component_lines;
  assert(lineCount(source) <= budget, `${relative} excede ${budget} linhas`);
  assert.doesNotMatch(source, /document\.write|eval\s*\(/, `${relative}: operação insegura`);
  assert.doesNotMatch(source, /\sstyle\s*=/i, `${relative}: apresentação deve permanecer nos componentes CSS`);
  assert.doesNotThrow(() => new Function(source), `${relative}: JavaScript inválido`);
  topLevelFunctionSpans(source).forEach(fn => {
    assert(fn.span <= budgets.function_lines, `${relative}:${fn.name} ocupa ${fn.span} linhas`);
  });
});

const cssFiles = [];
function collectCss(directory) {
  fs.readdirSync(directory, { withFileTypes: true }).forEach(entry => {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) collectCss(absolute);
    else if (entry.isFile() && entry.name.endsWith('.css')) cssFiles.push(absolute);
  });
}
collectCss(componentRoot);
cssFiles.forEach(absolute => {
  const relative = path.relative(root, absolute).replace(/\\/g, '/');
  assert(lineCount(fs.readFileSync(absolute, 'utf8')) <= budgets.css_lines, `${relative} excede ${budgets.css_lines} linhas`);
});

const manifest = read('electron_app/installer-required-resources.json');
[...componentFiles, ...cssFiles].forEach(absolute => {
  const relative = path.relative(root, absolute).replace(/\\/g, '/');
  assert(manifest.includes(`"${relative}"`), `${relative} ausente dos recursos-fonte do instalador`);
  assert(manifest.includes(`"local_app/${relative}"`), `${relative} ausente dos recursos empacotados`);
});

const mainInit = read('static/cadastro/main/04-init.js');
componentFiles.filter(file => !file.endsWith(path.join('main', '04-init.js'))).forEach(file => {
  assert(!fs.readFileSync(file, 'utf8').includes('main/04-init.js'), 'componentes não podem depender do inicializador');
});
assert.match(mainInit, /actions\.carregarProdutos\(\)/, 'inicializador deve carregar os produtos');
assert.match(read('static/cadastro/main/03-actions.js'), /global\.carregarProdutos = carregarProdutos/, 'fachada global de compatibilidade ausente');

console.log(`cadastro frontend architecture: ${componentFiles.length} JS e ${cssFiles.length} CSS dentro dos limites`);
