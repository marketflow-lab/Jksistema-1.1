'use strict';

const fs = require('fs');
const path = require('path');

const componentFiles = [
  '00-runtime.js',
  '01-core.js',
  '02-ui.js',
  '03-public-api.js'
];

function skuSidebarSource(root, options = {}) {
  const componentDir = path.join(root, 'static', 'favoritos', 'v2', 'sku-sidebar');
  const includeRuntime = options.includeRuntime !== false;
  const includePublicApi = options.includePublicApi !== false;
  return componentFiles
    .filter(fileName => includeRuntime || fileName !== '00-runtime.js')
    .filter(fileName => includePublicApi || fileName !== '03-public-api.js')
    .map(fileName => fs.readFileSync(path.join(componentDir, fileName), 'utf8'))
    .join('\n');
}

module.exports = { componentFiles, skuSidebarSource };
