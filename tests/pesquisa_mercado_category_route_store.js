'use strict';

const assert = require('assert/strict');
const fs = require('fs/promises');
const os = require('os');
const path = require('path');
const { createCategoryRouteStore, validCategoryRouteUrl } = require('../electron_app/main/modules/pesquisa-mercado-category-route-store');

async function testStore() {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), 'jk-category-route-test-'));
    const filePath = path.join(root, 'first', 'routes.json'), otherPath = path.join(root, 'second', 'routes.json');
    const store = createCategoryRouteStore({ filePath }), other = createCategoryRouteStore({ filePath: otherPath });
    const ids = ['MLB1', 'MLB2'], origin = 'https://lista.mercadolivre.com.br';
    const url = origin + '/seguranca-casa/automacao-portoes/pecas/', replacement = url + '*_NoIndex_True';
    try {
        assert.equal(await store.get(ids), null);
        assert(await store.put(ids, url));
        const reopened = createCategoryRouteStore({ filePath });
        assert.equal(await reopened.get(ids), url);
        const marker = '?original_category_landing=true', marked = url + '_NoIndex_True' + marker;
        assert.equal(validCategoryRouteUrl(marked), true);
        assert(await store.put(['MLB3'], marked));
        assert.equal(await reopened.get(['MLB3']), marked, 'preserve the complete verified first-page address');
        assert.equal(await other.get(ids), null);
        assert(await other.put(ids, replacement));
        assert.equal(await store.get(ids), url);
        assert.equal(await store.get(['MLB3', 'MLB2']), null, 'the full taxonomy path identifies a route');
        for (const bad of [[], ['MLB1', 'MLB1'], ['MLB1', null], ['MLB1/MLB2'], [2], Array(2), 'MLB2']) {
            assert.equal(await store.put(bad, url), false);
            assert.equal(await store.get(bad), null);
        }
        for (const bad of [url + marker, url + '*_NoIndex_True' + marker, url + '*/_NoIndex_True' + marker,
            url + '/_NoIndex_True' + marker, url + '_Desde_49/_NoIndex_True' + marker, marked + '&q=termo',
            marked + '&original_category_landing=true', marked + '#fragment', marked + '&',
            marked.replace('=true', '=TRUE'), marked.replace('=true', '=%74rue'),
            origin + '/p/MLB123/_NoIndex_True' + marker, origin + '/busca/termo/_NoIndex_True' + marker,
            url + 'pecas_OrderId_PRICE/_NoIndex_True' + marker, url + '?q=motor', url + '#fragment', url + '_Desde_49_NoIndex_True', url + 'Produto_OrderId_PRICE',
            url + '*_NoIndex_True?q=texto', origin + '/_CustId_123', origin + '/_CatId_MLB2', origin + '/loja/abc/',
            origin + '/busca/pecas/', origin + '/MLB-123456-peca/', origin + '/p/MLB123/', origin + '/%70/abc',
            origin + '/abc/../pecas/', url + '?', url + '#', url.replace('https://', 'http://'),
            url.replace('https://', 'https://user@'), url.replace('.br/', '.br:444/'), url.replace('lista.', 'produto.'),
            url.replace('.br/', '.br.evil.test/'), url + 'x'.repeat(3000)]) {
            assert.equal(validCategoryRouteUrl(bad), false);
            assert.equal(await reopened.put(ids, bad), false);
        }
        const parallelIds = Array.from({ length: 12 }, (_, i) => ['MLB1', 'MLB' + (i + 100)]);
        assert((await Promise.all(parallelIds.map((key, i) => (i % 2 ? reopened : store).put(key, url)))).every(Boolean));
        assert.deepEqual(await Promise.all(parallelIds.map(key => reopened.get(key))), parallelIds.map(() => url));
        const changed = await Promise.all([store.put(ids, replacement), reopened.forget(ids, url)]);
        assert.deepEqual(changed, [true, false], 'a stale reader cannot remove the newer route');
        assert.equal(await reopened.get(ids), replacement);
        assert(await store.forget(ids, replacement));
        assert.equal(await reopened.get(ids), null);
        assert.equal(await other.get(ids), replacement);
        assert.deepEqual(await fs.readdir(path.dirname(filePath)), ['routes.json']);
        const full = Array.from({ length: 16_384 }, (_, i) => ['MLB1/MLB' + (i + 100), url]);
        await fs.writeFile(filePath, JSON.stringify({ version: 1, routes: full }));
        assert(await store.put(ids, replacement));
        assert.equal(await store.get(['MLB1', 'MLB100']), null, 'adding a new route to a full cache evicts the oldest entry');
        assert.equal(await store.get(ids), replacement);
        await fs.writeFile(filePath, '{truncated');
        assert.equal(await store.get(ids), null);
        assert(await store.put(ids, url), 'a corrupt cache can be rebuilt by a subsequent verified write');
        const unwritable = createCategoryRouteStore({ filePath: path.join(filePath, 'cannot-write') });
        assert.equal(await unwritable.get(ids), null);
        assert.equal(await unwritable.put(ids, url), false);
        assert.equal(await unwritable.forget(ids, url), false);
    } finally {
        for (const file of [filePath, otherPath]) {
            await fs.rm(file, { force: true });
            await fs.rmdir(path.dirname(file)).catch(error => { if (error.code !== 'ENOENT') throw error; });
        }
        await fs.rmdir(root);
    }
}

testStore().then(() => console.log('Pesquisa de Mercado category route store: persistence, isolation, concurrency and validation OK'))
    .catch(error => { console.error(error); process.exitCode = 1; });
