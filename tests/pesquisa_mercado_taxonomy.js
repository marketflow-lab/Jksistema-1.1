'use strict';

const assert = require('assert/strict');
const fs = require('fs/promises');
const os = require('os');
const path = require('path');
const https = require('https');
const { EventEmitter } = require('events');
const { PassThrough } = require('stream');
const zlib = require('zlib');
const { TaxonomyService, parseTaxonomyDump, fetchTaxonomyDump, downloadDump,
    CACHE_TTL_MS, MAX_BYTES, DUMP_URL } = require('../electron_app/main/modules/pesquisa-mercado-taxonomy');

const clone = value => JSON.parse(JSON.stringify(value));

function fixture() {
    const dump = {};
    const add = (id, name, ancestors = [], permalink = null) => {
        dump[id] = { id, name, permalink, path_from_root: [...ancestors, id].map(key => ({ id: key, name: dump[key] ? dump[key].name : name })), children_categories: [] };
        if (ancestors.length) {
            dump[ancestors[ancestors.length - 1]].children_categories.push({ id, name, total_items_in_this_category: 1 });
        }
    };
    add('MLB100', 'Acessórios', [], 'https://www.mercadolivre.com.br/c/acessorios/');
    const ancestors = ['MLB100'];
    for (let level = 2; level <= 7; level += 1) {
        const id = 'MLB' + (99 + level);
        add(id, 'Nível ' + level, ancestors);
        ancestors.push(id);
    }
    for (let index = 200; index < 350; index += 1) add('MLB' + index, 'Item ' + index, ['MLB100']);
    add('MLB1743', 'Carros, Motos e Outros');
    add('MLB1744', 'Carros e Caminhonetes', ['MLB1743']);
    return dump;
}

function homonymFixture() {
    const dump = {};
    const chains = [
        [['MLB5672', 'Acessórios para Veículos'], ['MLB6005', 'Acessórios Náuticos'],
            ['MLB271613', 'Sistemas de Navegação'], ['MLB455956', 'Anemômetros']],
        [['MLB271599', 'Agro'], ['MLB456927', 'Ferramentas de Trabalho'],
            ['MLB457022', 'Instrumentos de Medição'], ['MLB457024', 'Anemômetros']],
        [['MLB263532', 'Ferramentas'], ['MLB5550', 'Medições e Instrumentação'],
            ['MLB455201', 'Medidores Ambientais'], ['MLB431215', 'Anemômetros']]
    ];
    for (const chain of chains) {
        chain.forEach(([id, name], index) => {
            const child = chain[index + 1];
            dump[id] = { id, name,
                path_from_root: chain.slice(0, index + 1).map(([key, label]) => ({ id: key, name: label })),
                children_categories: child ? [{ id: child[0], name: child[1] }] : [] };
        });
    }
    return dump;
}

async function withDownload(scenario, operation) {
    const original = https.get;
    let request;
    let response;
    https.get = (url, options, callback) => {
        assert.equal(url, DUMP_URL);
        assert.equal(Object.keys(options.headers).some(key => /authorization|cookie|token/i.test(key)), false);
        request = new EventEmitter();
        request.destroy = () => { request.destroyed = true; };
        request.setTimeout = (milliseconds, handler) => {
            assert.ok(milliseconds > 0 && milliseconds <= 25000);
            request.idleTimeout = handler;
        };
        setImmediate(() => {
            if (scenario.requestError) { request.emit('error', new Error('private network details')); return; }
            response = new PassThrough();
            response.statusCode = scenario.statusCode || 200;
            response.headers = scenario.headers || {};
            callback(response);
            if (scenario.idleTimeout) { request.idleTimeout(); return; }
            if (scenario.hang) return;
            response.end(scenario.body || Buffer.from('{}'));
        });
        return request;
    };
    try { return await operation(() => ({ request, response })); }
    finally { https.get = original; }
}

async function main() {
    let scenarios = 0;
    const scenario = async operation => { await operation(); scenarios += 1; };
    const dump = fixture();
    await scenario(() => {
        const snapshot = parseTaxonomyDump(dump, '2026-10-06T10:07:07.000Z');
        assert.equal(snapshot.complete, true);
        assert.deepEqual(snapshot.summary, { total: 159, rootCount: 2, subcategoryCount: 157, maxDepth: 7 });
        assert.equal(snapshot.children('MLB100').length, 151);
        assert.equal(snapshot.get('MLB106').nome, 'Nível 7');
        assert.deepEqual(snapshot.children('MLB1744'), []);
        assert.equal(snapshot.get('MLB999999'), null);
        assert.throws(() => snapshot.children('MLB999999'), error => error.code === 'taxonomy_unknown_category');
        assert.throws(() => snapshot.childCounts(['MLB999999']), error => error.code === 'taxonomy_unknown_category');
        assert.throws(() => snapshot.pathFromRoot('MLB999999'), error => error.code === 'taxonomy_unknown_category');
        assert.deepEqual(snapshot.pathFromRoot('MLB100'), [snapshot.get('MLB100')]);
        assert.deepEqual(snapshot.pathFromRoot('MLB106').map(node => node.key),
            ['MLB100', 'MLB101', 'MLB102', 'MLB103', 'MLB104', 'MLB105', 'MLB106']);
        assert.deepEqual(snapshot.pathFromRoot('MLB106').map(node => node.nome),
            ['Acessórios', 'Nível 2', 'Nível 3', 'Nível 4', 'Nível 5', 'Nível 6', 'Nível 7']);
        assert.deepEqual(snapshot.childCounts(['MLB100', 'MLB1744']), { MLB100: 151, MLB1744: 0 });
        assert.deepEqual(Object.keys(snapshot.get('MLB100')).sort(), ['key', 'nome', 'url']);
        assert.equal(snapshot.get('MLB100').url, 'https://www.mercadolivre.com.br/c/acessorios/');
        assert.equal(snapshot.get('MLB1744').url, 'https://lista.mercadolivre.com.br/_CatId_MLB1744');
        const children = snapshot.children('MLB100');
        children.pop();
        assert.equal(snapshot.children('MLB100').length, 151);
        const records = snapshot.cacheRecords();
        records.find(record => record[0] === 'MLB100')[3].length = 0;
        assert.equal(snapshot.children('MLB100').length, 151);
    });
    await scenario(() => {
        const snapshot = parseTaxonomyDump(dump);
        const original = snapshot.pathFromRoot('MLB106');
        const changed = snapshot.pathFromRoot('MLB106');
        const cacheBefore = snapshot.cacheRecords();
        assert.notEqual(changed, original);
        for (let index = 0; index < changed.length; index += 1) {
            assert.deepEqual(Object.keys(changed[index]).sort(), ['key', 'nome', 'url']);
            assert.notEqual(changed[index], original[index]);
            assert.notEqual(changed[index], snapshot.get(changed[index].key));
        }
        changed[0].key = 'MLB1743';
        changed[0].nome = 'Outra categoria';
        changed[0].url = 'https://example.com/';
        changed.reverse();
        changed.pop();
        const records = snapshot.cacheRecords();
        records.find(record => record[0] === 'MLB106')[4][0] = 'MLB1743';
        assert.deepEqual(snapshot.pathFromRoot('MLB106'), original);
        assert.deepEqual(snapshot.cacheRecords(), cacheBefore);
        assert.equal(snapshot.get('MLB100').nome, 'Acessórios');
    });
    await scenario(() => {
        const snapshot = parseTaxonomyDump(homonymFixture());
        const expected = [
            ['MLB455956', ['MLB5672', 'MLB6005', 'MLB271613', 'MLB455956'],
                ['Acessórios para Veículos', 'Acessórios Náuticos', 'Sistemas de Navegação', 'Anemômetros']],
            ['MLB457024', ['MLB271599', 'MLB456927', 'MLB457022', 'MLB457024'],
                ['Agro', 'Ferramentas de Trabalho', 'Instrumentos de Medição', 'Anemômetros']],
            ['MLB431215', ['MLB263532', 'MLB5550', 'MLB455201', 'MLB431215'],
                ['Ferramentas', 'Medições e Instrumentação', 'Medidores Ambientais', 'Anemômetros']]
        ];
        for (const [key, ids, names] of expected) {
            const chain = snapshot.pathFromRoot(key);
            assert.deepEqual(chain.map(node => node.key), ids);
            assert.deepEqual(chain.map(node => node.nome), names);
            assert.deepEqual(chain.map(node => node.url), ids.map(id => 'https://lista.mercadolivre.com.br/_CatId_' + id));
            assert.deepEqual(snapshot.children(key), []);
        }
    });
    await scenario(() => {
        for (const permalink of ['https://evil.example/c/carros', 'https://www.mercadolivre.com.br.evil.example/c/carros',
            'http://lista.mercadolivre.com.br/carros/', 'https://user:password@lista.mercadolivre.com.br/carros/',
            'https://www.mercadolivre.com.br/p/MLB123', 'https://lista.mercadolivre.com.br/_CustId_123',
            'https://lista.mercadolivre.com.br/carros/?seller_id=123', 'https://www.mercadolivre.com.br/c/carros?access_token=abc',
            'https://lista.mercadolivre.com.br:444/carros/', 'https://lista.mercadolivre.com.br/%ZZ']) {
            const changed = clone(dump);
            changed.MLB1744.permalink = permalink;
            assert.equal(parseTaxonomyDump(changed).get('MLB1744').url, 'https://lista.mercadolivre.com.br/_CatId_MLB1744');
        }
        const changed = clone(dump);
        changed.MLB1744.permalink = 'https://lista.mercadolivre.com.br/veiculos/carros/#tracking';
        assert.equal(parseTaxonomyDump(changed).get('MLB1744').url, 'https://lista.mercadolivre.com.br/veiculos/carros/');
    });
    await scenario(() => {
        const mutations = [
            value => { delete value.MLB200; },
            value => { value.MLB100.children_categories.push({ id: 'MLB100', name: 'Acessórios' }); },
            value => { value.MLB1743.children_categories.push({ id: 'MLB200', name: 'Item 200' }); },
            value => { value.MLB200.path_from_root[0].id = 'MLB999999'; },
            value => { value.MLB200.path_from_root[0].id = 'MLB1743'; },
            value => { value.MLB200.id = 'MLB201'; },
            value => { value.MLB200.name = ''; },
            value => { value.MLB200.path_from_root = []; },
            value => { value.MLB200.children_categories = null; },
            value => { value.MLB100.children_categories.push(value.MLB100.children_categories[0]); },
            value => { value.invalid = { id: 'invalid', name: 'Invalid', path_from_root: [], children_categories: [] }; }
        ];
        for (const mutate of mutations) {
            const changed = clone(dump);
            mutate(changed);
            assert.throws(() => parseTaxonomyDump(changed), error => error.code === 'taxonomy_invalid');
        }
        assert.throws(() => parseTaxonomyDump({}), error => error.code === 'taxonomy_invalid');
    });
    await scenario(async () => {
        let currentTime = Date.parse('2026-10-06T12:00:00Z');
        let calls = 0;
        let release;
        const service = new TaxonomyService({ now: () => currentTime, fetchDump: async () => {
            calls += 1;
            if (calls === 1) await new Promise(resolve => { release = resolve; });
            return { data: dump, updatedAt: '2026-10-06T10:07:07.000Z' };
        } });
        const first = service.load();
        const second = service.load();
        assert.equal(first, second);
        await new Promise(resolve => setImmediate(resolve));
        release();
        const loaded = await first;
        assert.equal(await service.load(), loaded);
        assert.equal(calls, 1);
        const refreshed = await service.load({ forceRefresh: true });
        assert.notEqual(refreshed, loaded);
        assert.equal(calls, 2);
        currentTime += CACHE_TTL_MS;
        await service.load();
        assert.equal(calls, 3);
    });
    await scenario(async () => {
        let fail = false;
        const service = new TaxonomyService({ fetchDump: async () => {
            if (fail) throw new Error('private network details');
            return dump;
        } });
        const original = await service.load();
        fail = true;
        await assert.rejects(service.load({ forceRefresh: true }), error => error.code === 'taxonomy_unavailable' && !error.message.includes('private'));
        assert.equal(service.snapshot, original);
        assert.equal(await service.load(), original);
        fail = false;
        assert.equal((await service.load({ forceRefresh: true })).complete, true);
    });
    await scenario(async () => {
        let aborted = false;
        const service = new TaxonomyService({ deadlineMs: 20, fetchDump: ({ signal }) => new Promise(() => {
            signal.addEventListener('abort', () => { aborted = true; });
        }) });
        await assert.rejects(service.load(), error => error.code === 'taxonomy_timeout');
        assert.equal(aborted, true);
        assert.equal(service.snapshot, null);
    });

    const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'jk-public-taxonomy-'));
    try {
        await scenario(async () => {
            const cachePath = path.join(temporary, 'snapshot.json');
            const now = Date.parse('2026-10-06T12:00:00Z');
            const service = new TaxonomyService({ cachePath, now: () => now, fetchDump: async () => dump });
            const snapshot = await service.load();
            let saved = await fs.readFile(cachePath, 'utf8');
            const cache = JSON.parse(saved);
            assert.deepEqual(Object.keys(cache).sort(), ['fetchedAt', 'records', 'updatedAt', 'version']);
            assert.equal(cache.records.length, snapshot.summary.total);
            let calls = 0;
            const restored = new TaxonomyService({ cachePath, now: () => now + 1000, fetchDump: async () => { calls += 1; throw new Error('network should not run'); } });
            const restoredSnapshot = await restored.load();
            assert.deepEqual(restoredSnapshot.summary, snapshot.summary);
            assert.deepEqual(restoredSnapshot.pathFromRoot('MLB106'), snapshot.pathFromRoot('MLB106'));
            assert.equal(calls, 0);
            const duringCache = new TaxonomyService({ cachePath, now: () => now + 1000, fetchDump: async () => { calls += 1; return dump; } });
            const cachedLoad = duringCache.load();
            const forcedLoad = duringCache.load({ forceRefresh: true });
            await cachedLoad;
            await forcedLoad;
            assert.equal(calls, 1);
            saved = await fs.readFile(cachePath, 'utf8');
            await assert.rejects(restored.load({ forceRefresh: true }), error => error.code === 'taxonomy_unavailable');
            assert.equal(await fs.readFile(cachePath, 'utf8'), saved);
            assert.deepEqual((await restored.load()).summary, snapshot.summary);
            assert.equal((await fs.readdir(temporary)).some(name => name.endsWith('.tmp')), false);
        });
        await scenario(async () => {
            const cachePath = path.join(temporary, 'homonyms.json');
            const now = Date.parse('2026-10-08T12:00:00Z');
            const service = new TaxonomyService({ cachePath, now: () => now, fetchDump: async () => homonymFixture() });
            const snapshot = await service.load();
            const saved = await fs.readFile(cachePath, 'utf8');
            assert.deepEqual(Object.keys(JSON.parse(saved)).sort(), ['fetchedAt', 'records', 'updatedAt', 'version']);
            const restored = new TaxonomyService({ cachePath, now: () => now + 1000,
                fetchDump: async () => { throw new Error('network should not run'); } });
            const restoredSnapshot = await restored.load();
            for (const key of ['MLB455956', 'MLB457024', 'MLB431215']) {
                const expected = snapshot.pathFromRoot(key);
                const chain = restoredSnapshot.pathFromRoot(key);
                assert.deepEqual(chain, expected);
                chain[0].nome = 'Alterado pelo consumidor';
                chain.splice(0, chain.length);
                assert.deepEqual(restoredSnapshot.pathFromRoot(key), expected);
            }
            assert.equal(await fs.readFile(cachePath, 'utf8'), saved);
        });
        await scenario(async () => {
            const cachePath = path.join(temporary, 'corrupt.json');
            await fs.writeFile(cachePath, '{"version":1,"records":[');
            let calls = 0;
            const service = new TaxonomyService({ cachePath, fetchDump: async () => { calls += 1; return dump; } });
            assert.equal((await service.load()).complete, true);
            assert.equal(calls, 1);
            const cache = JSON.parse(await fs.readFile(cachePath, 'utf8'));
            cache.records.find(record => record[0] === 'MLB100')[3].push('MLB999999');
            await fs.writeFile(cachePath, JSON.stringify(cache));
            const bad = new TaxonomyService({ cachePath, fetchDump: async () => { throw new Error('unavailable'); } });
            await assert.rejects(bad.load(), error => error.code === 'taxonomy_unavailable');
            assert.equal(bad.snapshot, null);
        });
    } finally {
        await fs.rm(temporary, { recursive: true, force: true });
    }

    for (const encoding of ['identity', 'gzip', 'deflate']) {
        await scenario(async () => {
            const json = Buffer.from(JSON.stringify(dump));
            const body = encoding === 'gzip' ? zlib.gzipSync(json) : encoding === 'deflate' ? zlib.deflateSync(json) : json;
            const fetched = await withDownload({ body, headers: { 'content-encoding': encoding, 'content-length': String(body.length), 'x-content-created': '2026-10-06T10:07:07.000Z' } },
                () => fetchTaxonomyDump({ deadlineMs: 1000 }));
            assert.equal(parseTaxonomyDump(fetched.data).summary.maxDepth, 7);
            assert.equal(fetched.updatedAt, '2026-10-06T10:07:07.000Z');
        });
    }
    await scenario(async () => {
        await withDownload({ statusCode: 403 }, () => assert.rejects(downloadDump(), error => error.code === 'taxonomy_http_error'));
        await withDownload({ headers: { 'content-encoding': 'br' } }, () => assert.rejects(downloadDump(), error => error.code === 'taxonomy_encoding_invalid'));
        await withDownload({ headers: { 'content-length': String(MAX_BYTES + 1) } }, () => assert.rejects(downloadDump(), error => error.code === 'taxonomy_too_large'));
        await withDownload({ body: Buffer.from('{broken') }, () => assert.rejects(fetchTaxonomyDump(), error => error.code === 'taxonomy_invalid'));
        await withDownload({ requestError: true }, () => assert.rejects(downloadDump(), error => error.code === 'taxonomy_unavailable' && !error.message.includes('private')));
        await withDownload({ idleTimeout: true }, () => assert.rejects(downloadDump({ requestTimeoutMs: 20 }), error => error.code === 'taxonomy_timeout'));
        await withDownload({ hang: true }, state => assert.rejects(downloadDump({ deadlineMs: 20 }), error => {
            assert.equal(state().request.destroyed, true);
            assert.equal(state().response.destroyed, true);
            return error.code === 'taxonomy_timeout';
        }));
        const controller = new AbortController();
        controller.abort();
        await assert.rejects(downloadDump({ signal: controller.signal }), error => error.code === 'taxonomy_aborted');
    });
    await scenario(async () => {
        const oversized = Buffer.alloc(MAX_BYTES + 1, 32);
        const compressed = zlib.gzipSync(oversized);
        await withDownload({ body: compressed, headers: { 'content-encoding': 'gzip' } },
            () => assert.rejects(downloadDump({ deadlineMs: 5000 }), error => error.code === 'taxonomy_too_large'));
    });
    console.log('Pesquisa Mercado: taxonomia oficial validada (' + scenarios + ' cenários).');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
