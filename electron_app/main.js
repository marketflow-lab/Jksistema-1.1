const fs = require('fs');
const path = require('path');

const candidates = [
  process.resourcesPath ? path.join(process.resourcesPath, 'local_app', 'main.js') : '',
  path.join(__dirname, '..', 'local_app', 'main.js'),
  path.join(__dirname, '..', 'main.js')
].filter(Boolean);

const entryPoint = candidates.find((candidate) => {
  try {
    return fs.statSync(candidate).isFile();
  } catch (_err) {
    return false;
  }
});

if (!entryPoint) {
  throw new Error(`Nao encontrei o main.js do JK Sistema. Caminhos testados: ${candidates.join(' | ')}`);
}

require(entryPoint);
