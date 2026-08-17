'use strict';

const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, '..', 'static', 'importacoes.html');
const html = fs.readFileSync(htmlPath, 'utf8');

const buttonId = 'id="btnOrganizarEmbarque"';
const buttonCount = html.split(buttonId).length - 1;
if (buttonCount !== 1) {
    throw new Error(`Organizar Embarque deve existir uma unica vez; encontrado: ${buttonCount}`);
}

const toolbarStart = html.indexOf('<div class="toolbar">');
const toolbarEnd = html.indexOf('</div>', toolbarStart);
const toolbarHtml = html.slice(toolbarStart, toolbarEnd);
if (toolbarHtml.includes(buttonId)) {
    throw new Error('Organizar Embarque nao deve ficar na barra geral da pagina');
}

const titleIndex = html.indexOf('<span class="secao-titulo">Embarques</span>');
const headerStart = html.lastIndexOf('<div class="secao-header">', titleIndex);
const headerEnd = html.indexOf('</div>', titleIndex);
const headerHtml = html.slice(headerStart, headerEnd);
if (headerStart < 0 || headerEnd < 0 || !headerHtml.includes(buttonId)) {
    throw new Error('Organizar Embarque deve ficar no cabecalho da secao Embarques');
}
const titlePosition = headerHtml.indexOf('<span class="secao-titulo">Embarques</span>');
const buttonPosition = headerHtml.indexOf(buttonId);
const badgePosition = headerHtml.indexOf('id="badgeEmbarques"');
if (!(titlePosition < buttonPosition && buttonPosition < badgePosition)) {
    throw new Error('Organizar Embarque deve ficar imediatamente depois do titulo');
}

if (!/\.secao-header\s*\{[^}]*flex-wrap:\s*wrap;/.test(html)) {
    throw new Error('Cabecalho de Embarques deve permitir quebra responsiva');
}
if (!/\.btn-embarque-org\s*\{[^}]*padding:\s*6px 9px;[^}]*font-size:\s*0\.82rem;/.test(html)) {
    throw new Error('Organizar Embarque deve permanecer compacto');
}
if (/\.secao-header \.btn-embarque-org\s*\{[^}]*margin-left:\s*auto;/.test(html)) {
    throw new Error('Organizar Embarque nao deve ser empurrado para a direita');
}

const inlineScripts = [...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map((match) => match[1])
    .filter((source) => source.trim());
for (const source of inlineScripts) {
    new Function(source);
}

console.log('OK: Organizar Embarque compacto e logo depois do titulo da secao.');
