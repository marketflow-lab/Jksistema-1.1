'use strict';

const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), 'utf8');
}

function inlineScripts(html) {
  return [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map(match => match[1])
    .filter(source => source.trim());
}

function cadastroScriptPaths(html) {
  return [...html.matchAll(/<script\s+[^>]*src=["']([^"']+)["'][^>]*><\/script>/gi)]
    .map(match => match[1].split('?')[0])
    .filter(source => source.startsWith('/cadastro/'))
    .map(source => `static${source}`);
}

function pageSources(relativePath) {
  const html = read(relativePath);
  const scripts = [
    ...inlineScripts(html).map((source, index) => ({ name: `${relativePath}:inline-${index + 1}`, source })),
    ...cadastroScriptPaths(html).map(scriptPath => ({ name: scriptPath, source: read(scriptPath) })),
  ];
  return { html, scripts, combined: `${html}\n${scripts.map(item => item.source).join('\n')}` };
}

module.exports = {
  cadastroScriptPaths,
  inlineScripts,
  pageSources,
  read,
  root,
};
