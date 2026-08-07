'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const GENERATED_ASSETS = 'static/favoritos/v2/browser/generated-assets.json';
const PRELOADED_BROWSER_ASSETS = [
  'static/favoritos/v2/browser/url-utils.js',
  'static/favoritos/v2/browser/worker-controller.js',
  'static/favoritos/v2/browser/worker-pool.js',
  'static/favoritos/v2/browser/shell-bridge.js',
  'static/favoritos/v2/browser/avant-cache.js'
];

function readBrowserAssets(root) {
  return JSON.parse(fs.readFileSync(path.join(root, GENERATED_ASSETS), 'utf8'));
}

function generatedBrowserPaths(root) {
  const assets = readBrowserAssets(root);
  return [
    assets.runtime,
    assets.pageRuntime,
    ...assets.pageScripts,
    ...assets.modules,
    assets.publicApi
  ];
}

function browserSource(root, options = {}) {
  const assets = readBrowserAssets(root);
  const paths = [
    ...(options.preloaded === false ? [] : PRELOADED_BROWSER_ASSETS),
    assets.runtime,
    ...assets.modules,
    assets.pageRuntime,
    ...assets.pageScripts,
    assets.publicApi,
    ...(options.facade === false ? [] : ['static/favoritos/ml-browser.js'])
  ];
  return paths.map(relative => fs.readFileSync(path.join(root, relative), 'utf8')).join('\n');
}

function generatedBrowserSources(root) {
  return generatedBrowserPaths(root).map(relative => ({
    relative,
    content: fs.readFileSync(path.join(root, relative), 'utf8')
  }));
}

function createPageScriptRenderer(root) {
  const assets = readBrowserAssets(root);
  const context = {};
  context.window = context;
  vm.runInNewContext(fs.readFileSync(path.join(root, assets.pageRuntime), 'utf8'), context);
  for (const relative of assets.pageScripts.filter(item => !item.endsWith('/legacy-extract.js'))) {
    vm.runInNewContext(fs.readFileSync(path.join(root, relative), 'utf8'), context, { filename: relative });
  }
  return context.FavoritosV2.browser.pageScripts;
}

module.exports = {
  GENERATED_ASSETS,
  PRELOADED_BROWSER_ASSETS,
  browserSource,
  createPageScriptRenderer,
  generatedBrowserPaths,
  generatedBrowserSources,
  readBrowserAssets
};
