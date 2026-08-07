'use strict';

const fs = require('fs');
const path = require('path');

const componentFiles = [
  "00-runtime.js",
  "01-contracts.js",
  "02-promotion-options.js",
  "03-pricing-simulation.js",
  "04-client-single-execution.js",
  "05-outcomes.js",
  "06-results-ui.js",
  "07-history.js",
  "08-simulator-ui.js",
  "09-selection.js",
  "10-confirmation.js",
  "11-preflight.js",
  "12-batch-execution.js",
  "13-listing-ui.js",
  "14-merge-policies.js",
  "15-public-api.js"
];

function promotionEffectuationSource(root, options = {}) {
  const componentDir = path.join(root, 'static', 'favoritos', 'v2', 'promotion-effectuation');
  const includeRuntime = options.includeRuntime !== false;
  const includePublicApi = options.includePublicApi !== false;
  const source = componentFiles
    .filter(fileName => includeRuntime || fileName !== '00-runtime.js')
    .filter(fileName => includePublicApi || fileName !== '15-public-api.js')
    .map(fileName => fs.readFileSync(path.join(componentDir, fileName), 'utf8'))
    .join('\n');
  if (options.installTestGlobals === false) return source;
  return source + `
(function installPromotionEffectuationTestGlobals(global) {
  const internal = global.FavoritosV2.promotionEffectuation.internal;
  Object.keys(internal).forEach(name => {
    if (name !== 'components') global[name] = internal[name];
  });
})(window);
`;
}

module.exports = { componentFiles, promotionEffectuationSource };
