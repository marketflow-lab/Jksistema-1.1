'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const html = fs.readFileSync(path.join(__dirname, '..', 'static', 'importacoes.html'), 'utf8');

const buttonId = 'id="btnOrganizarEmbarque"';
assert.strictEqual(html.split(buttonId).length - 1, 1,
  'Organizar Embarque deve existir uma única vez.');
const toolbarStart = html.indexOf('<div class="toolbar">');
const toolbarEnd = html.indexOf('</div>', toolbarStart);
assert(!html.slice(toolbarStart, toolbarEnd).includes(buttonId),
  'Organizar Embarque deve permanecer associado à sua seção.');
const sectionStart = html.indexOf('<section class="embarques-principal"');
const sectionEnd = html.indexOf('</section>', sectionStart);
assert(sectionStart >= 0 && sectionEnd > sectionStart, 'Embarques devem ter uma seção própria.');
const section = html.slice(sectionStart, sectionEnd);
for (const marker of ['aria-label="Embarques"', '<span class="secao-titulo">Embarques</span>',
  buttonId, 'id="badgeEmbarques"', 'id="embarquesWrap"', 'Nenhum embarque criado.']) {
  assert(section.includes(marker), 'Elemento original ausente na seção Embarques: ' + marker);
}
const titleIndex = html.indexOf('<span class="secao-titulo">Embarques</span>', sectionStart);
const headerStart = html.lastIndexOf('<div class="secao-header">', titleIndex);
const headerEnd = html.indexOf('</div>', titleIndex);
assert(headerStart >= sectionStart && html.slice(headerStart, headerEnd).includes(buttonId),
  'Organizar Embarque deve estar junto ao título e à contagem.');
assert(/\.secao-header\s*\{[^}]*flex-wrap:\s*wrap;/.test(html),
  'Cabeçalho deve permitir reorganização em telas menores.');
assert(html.includes('#embarquesWrap:has(> .empty)'),
  'A seção deve compactar o espaço quando não há embarques.');
assert(html.includes('function renderEmbarques(todasListas)'),
  'Renderização dos embarques deve preservar sua fonte de dados própria.');

const inlineScripts = [...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(match => match[1]).filter(source => source.trim());
for (const source of inlineScripts) new Function(source);
console.log('OK: Embarques mantém ação, contagem e conteúdo em uma seção compacta e responsiva.');
