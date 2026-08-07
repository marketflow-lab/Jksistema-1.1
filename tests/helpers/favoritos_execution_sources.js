'use strict';

const fs = require('fs');
const path = require('path');

const executionComponentFiles = Object.freeze([
  '00-runtime.js',
  '01-collection-evidence.js',
  '02-collection-controller.js',
  '03-worker-pool.js',
  '04-confirmed-flow.js',
  '05-legacy-renderer.js',
  '06-job-runtime.js',
  '07-job-controls.js',
  '08-ranking-entrypoints.js',
  '09-sidebar-render.js',
  '10-table-layout.js',
  '11-public-api.js'
]);

function executionSource(repoRoot, options = {}) {
  const includeRuntime = options.includeRuntime === true;
  const includePublicApi = options.includePublicApi === true;
  return executionComponentFiles
    .filter(fileName => includeRuntime || fileName !== '00-runtime.js')
    .filter(fileName => includePublicApi || fileName !== '11-public-api.js')
    .map(fileName => fs.readFileSync(
      path.join(repoRoot, 'static', 'favoritos', 'v2', 'execution', fileName),
      'utf8'
    ))
    .join('\n');
}

module.exports = {
  executionComponentFiles,
  executionSource
};
