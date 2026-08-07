'use strict';

const fs = require('fs');
const path = require('path');

const searchRankingComponentFiles = Object.freeze([
  '00-runtime.js',
  '01-status-errors.js',
  '02-avant-connection.js',
  '03-login-auth.js',
  '04-avant-login-prompt.js',
  '05-promotions.js',
  '06-execution-control.js',
  '07-sku-queries.js',
  '08-legacy-search.js',
  '09-search-entrypoints.js',
  '10-listing-normalization.js',
  '11-enrichment-runtime.js',
  '12-enrichment-sources.js',
  '13-ranking.js',
  '14-ai-filter.js',
  '15-public-api.js'
]);

function searchRankingSource(repoRoot, options = {}) {
  const includeRuntime = options.includeRuntime !== false;
  const includePublicApi = options.includePublicApi !== false;
  return searchRankingComponentFiles
    .filter(fileName => includeRuntime || fileName !== '00-runtime.js')
    .filter(fileName => includePublicApi || fileName !== '15-public-api.js')
    .map(fileName => fs.readFileSync(
      path.join(repoRoot, 'static', 'favoritos', 'v2', 'search-ranking', fileName),
      'utf8'
    ))
    .join('\n');
}

module.exports = {
  searchRankingComponentFiles,
  searchRankingSource
};
