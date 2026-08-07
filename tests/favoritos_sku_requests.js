const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { readSkuComponents } = require('./helpers/favoritos_sku_sources');

const root = process.cwd();
const runtime = fs.readFileSync(path.join(root, 'static', 'favoritos', 'runtime.js'), 'utf8');
const sku = readSkuComponents(root);
const endpoints = fs.readFileSync(path.join(root, 'backend', 'services', 'favoritos_endpoints.py'), 'utf8');

assert.match(runtime, /skuDescricaoAutoAbortController/);
assert.match(runtime, /SKU_DESCRICAO_CACHE_TTL_OK_MS\s*=\s*30\s*\*\s*60\s*\*\s*1000/);
assert.match(runtime, /SKU_DESCRICAO_CACHE_TTL_EMPTY_MS\s*=\s*5\s*\*\s*60\s*\*\s*1000/);

assert.match(sku, /function skuCancelarBuscaDescricoesAutomaticas[\s\S]*\.abort\(\)/);
assert.match(sku, /const controller = new AbortController\(\)[\s\S]*signal:\s*controller\.signal/);
assert.match(sku, /linhasVisiveis[\s\S]*SKU_API_PAGE_SIZE[\s\S]*linhasVisiveis\.concat/);
assert.match(sku, /const tamanhoLote = 60/);
assert.match(sku, /force_refresh:\s*false/);
assert.match(sku, /function skuBuscarDescricaoManual[\s\S]*force_refresh:\s*true/);
assert.match(sku, /function skuDescricaoCacheGet[\s\S]*function skuDescricaoCacheSet/);

assert.match(endpoints, /FAVORITOS_DESCRICAO_CACHE_TTL_OK_S\s*=\s*30\s*\*\s*60/);
assert.match(endpoints, /FAVORITOS_DESCRICAO_CACHE_TTL_EMPTY_S\s*=\s*5\s*\*\s*60/);
assert.match(endpoints, /FAVORITOS_DESCRICAO_INFLIGHT/);
assert.match(endpoints, /threading\.BoundedSemaphore\(8\)/);
assert.match(endpoints, /def _favoritos_obter_descricao_item_controlada/);
assert.match(endpoints, /cache_hits/);

console.log('Fluxo controlado de descricoes de SKU validado com sucesso.');
