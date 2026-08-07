'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const FACADE_PATH = 'static/favoritos/styles.css';
const IMPORT_PATTERN = /@import\s+url\(["']([^"']+)["']\)\s*;/g;

function read(root, relativePath) {
  return fs.readFileSync(path.join(root, relativePath), 'utf8');
}

function normalizeNewlines(source) {
  return String(source || '').replace(/\r\n/g, '\n');
}

function importedStylePaths(root) {
  const facade = read(root, FACADE_PATH);
  const imports = Array.from(facade.matchAll(IMPORT_PATTERN), match => match[1]);
  return imports.map(source => {
    const withoutQuery = source.split('?')[0];
    const relative = path.posix.normalize(path.posix.join('static/favoritos', withoutQuery));
    if (!relative.startsWith('static/favoritos/styles/')) {
      throw new Error(`Import CSS fora da pasta permitida: ${source}`);
    }
    return relative;
  });
}

function readFavoritosStyles(root) {
  return importedStylePaths(root).map(relativePath => read(root, relativePath)).join('');
}

function normalizedSha256(source) {
  return crypto.createHash('sha256').update(normalizeNewlines(source)).digest('hex');
}

function cssRules(source) {
  const css = normalizeNewlines(source);
  const rules = [];
  const stack = [];
  let lastBoundary = 0;
  for (let index = 0; index < css.length; index += 1) {
    const char = css[index];
    if (char === ';') {
      lastBoundary = index + 1;
      continue;
    }
    if (char === '{') {
      const prelude = css.slice(lastBoundary, index).trim().replace(/\s+/g, ' ');
      const context = stack.filter(item => item.atRule).map(item => item.prelude).join(' > ') || 'root';
      const atRule = prelude.startsWith('@');
      if (prelude && !atRule && !context.includes('@keyframes')) {
        rules.push({ selector: prelude, context });
      }
      stack.push({ prelude, atRule });
      lastBoundary = index + 1;
      continue;
    }
    if (char === '}') {
      stack.pop();
      lastBoundary = index + 1;
    }
  }
  return rules;
}

function styleStats(source) {
  const normalized = normalizeNewlines(source);
  const rules = cssRules(normalized);
  const selectors = new Set(rules.map(rule => rule.selector));
  const classes = new Set();
  const ids = new Set();
  for (const selector of selectors) {
    for (const match of selector.matchAll(/\.([A-Za-z_][\w-]*)/g)) classes.add(match[1]);
    for (const match of selector.matchAll(/#([A-Za-z_][\w-]*)/g)) ids.add(match[1]);
  }
  return {
    normalized_sha256: normalizedSha256(normalized),
    physical_lines: normalized.endsWith('\n') ? normalized.split('\n').length - 1 : normalized.split('\n').length,
    rules: rules.length,
    unique_selectors: selectors.size,
    class_tokens: classes.size,
    id_tokens: ids.size,
    media_queries: Array.from(normalized.matchAll(/@media\s*([^\{]+)\{/g), match => match[1].trim()),
    keyframes: Array.from(normalized.matchAll(/@keyframes\s+([\w-]+)/g), match => match[1]),
    important_count: (normalized.match(/!important/g) || []).length,
    custom_property_declarations: (normalized.match(/--[A-Za-z0-9_-]+\s*:/g) || []).length,
  };
}

module.exports = {
  FACADE_PATH,
  importedStylePaths,
  normalizeNewlines,
  normalizedSha256,
  readFavoritosStyles,
  styleStats,
};
