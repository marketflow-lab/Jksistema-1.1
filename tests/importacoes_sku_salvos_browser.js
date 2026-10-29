'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(process.env.JK_SKU_TEST_SOURCE_ROOT || path.join(__dirname, '..'));
const origin = 'http://127.0.0.1:8137';
const canonicalKey = 'importacoes:GET:/api/medias-compras/listas-pedidos/A';

function gate() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
}

function lista(id = 'A', quantidade = 2) {
    return {
        id, nome_lista: 'Pedido ' + id, store_id: 'store-' + id, dolar_hoje: 5,
        itens: [{
            SKU: '001', Quantidade: quantidade, 'Valor unidade': 10, 'Valor total': quantidade * 10,
            'M3 individual': 0.1, Produto: 'Produto salvo ' + id, NCM: '00000001',
            Link: 'https://produto.test/' + id, II: 10, IPI: 5, PIS: 2, COFINS: 3,
            'ST Minas': 'Não', 'ST Paraíba': 'Não', analise_concorrentes: { margens: {} },
        }],
    };
}

function completo(id = 'A', quantidade = 9, overrides = {}) {
    return { lista: lista(id, quantidade), _cache: {
        estado: 'ready', stale: false, atualizando: false, completo: true, retry_after_ms: 0, ...overrides,
    } };
}

function aguardando() {
    return { status: 202, body: { success: true, dados: null, _cache: {
        estado: 'waiting', stale: true, atualizando: true, completo: false, retry_after_ms: 1000,
    } } };
}

async function waitUntil(predicate, message) {
    for (let attempt = 0; attempt < 500; attempt++) {
        if (predicate()) return;
        await new Promise(resolve => setTimeout(resolve, 10));
    }
    throw new Error(message || 'Condição da fixture não atingida.');
}

async function environment(browser, options = {}) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const state = {
        calls: [], complements: [], patches: [], errors: [], pending: [],
        backgroundById: {}, phase: 'waiting',
    };
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => state.errors.push(error.message));
    await context.addInitScript(({ testOrigin, fastRequestTimeout, ignoreBackgroundAbort }) => {
        if (location.origin !== testOrigin) return;
        if (!localStorage.getItem('user_data')) {
            localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-A', username: 'tester' }));
            localStorage.setItem('permissions', JSON.stringify({ medias_compras: true, vendas: true }));
            localStorage.setItem('access_token', 'synthetic');
        }
        if (ignoreBackgroundAbort) {
            const originalFetch = window.fetch.bind(window);
            window.fetch = (input, init) => {
                const url = new URL(typeof input === 'string' ? input : input.url, location.href);
                if (url.pathname.endsWith('/listas-pedidos/A') && url.searchParams.get('cache_mode') === 'stale') {
                    // Reproduce a response that wins the race with cancellation.
                    const { signal: _signal, ...request } = init || {};
                    return originalFetch(input, request);
                }
                return originalFetch(input, init);
            };
        }
        const original = window.setTimeout.bind(window);
        window.setTimeout = (callback, delay, ...args) => original(callback,
            fastRequestTimeout && delay === 5000 ? 75 : [1000, 2000].includes(delay) ? 25 : delay, ...args);
    }, { testOrigin: origin, fastRequestTimeout: Boolean(options.fastRequestTimeout), ignoreBackgroundAbort: Boolean(options.ignoreBackgroundAbort) });
    const json = (route, result) => route.fulfill({
        status: result?.status || 200, contentType: 'application/json',
        body: JSON.stringify(result?.body ?? result),
    }).catch(() => {});
    await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        const pathname = url.pathname;
        try {
            if (url.hostname === 'economia.awesomeapi.com.br') return json(route, { USDBRL: { bid: '5', timestamp: '1' } });
            if (url.hostname === 'fonts.googleapis.com') return route.fulfill({ contentType: 'text/css', body: '' });
            assert.strictEqual(url.origin, origin, 'Nenhuma requisição pode sair das fixtures.');
            if (pathname === '/seed.html') return route.fulfill({
                contentType: 'text/html', body: '<!doctype html><script src="/cache-persistente.js"></script>',
            });
            if (pathname === '/importacoes_sku.html' || pathname === '/cache-persistente.js') return route.fulfill({
                contentType: pathname.endsWith('.html') ? 'text/html' : 'application/javascript',
                body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))),
            });
            if (pathname === '/auth.js') return route.fulfill({
                contentType: 'application/javascript',
                body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic"});',
            });
            const match = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([AB])$/);
            if (match) {
                const id = match[1];
                const kind = url.searchParams.get('somente_salvos') === 'true' ? 'saved'
                    : url.searchParams.get('cache_mode') === 'stale' ? 'background' : 'blocking';
                const call = { id, kind, method: route.request().method() };
                state.calls.push(call);
                assert.strictEqual(call.method, 'GET');
                if (kind === 'blocking') return json(route, { status: 500, body: { detail: 'GET pesado sem query proibido nesta regressão' } });
                if (kind === 'background') state.backgroundById[id] = (state.backgroundById[id] || 0) + 1;
                const handler = options[kind];
                const result = handler ? await handler({ id, state, index: state.backgroundById[id], page })
                    : kind === 'saved' ? { lista: lista(id) } : completo(id);
                return json(route, result);
            }
            if (pathname.endsWith('/analise-concorrentes')) {
                assert.strictEqual(route.request().method(), 'PATCH');
                state.patches.push({ pathname, payload: route.request().postDataJSON() });
                return json(route, { analise_concorrentes: { margens: {} } });
            }
            if (pathname.startsWith('/api/')) {
                state.complements.push(pathname);
                if (options.complement) {
                    const custom = await options.complement({ pathname, state });
                    if (custom !== undefined) return json(route, custom);
                }
                const product = pathname.match(/^\/api\/cadastro\/lojas\/(store-[AB])\/produtos\/001$/);
                if (product) return json(route, { produto: {
                    store_id: product[1], sku: '001', nome: 'Cadastro ' + product[1],
                    ncm: '00000002', 'M3 individual': 0.1,
                } });
                if (/^\/api\/cadastro\/lojas\/store-[AB]\/produtos$/.test(pathname)) return json(route, []);
                if (pathname === '/api/impostos/produtos') return json(route, { produtos: [] });
                if (pathname === '/api/medias-compras/concorrentes-links') return json(route, {
                    concorrentes: {}, concorrentes_mlb: {}, concorrentes_preco: {},
                    concorrentes_valores: { concorrente_1: 'R$ 200,00' },
                });
                throw new Error('Endpoint fora da fixture: ' + pathname);
            }
            return route.fulfill({ contentType: 'application/javascript', body: '' });
        } catch (error) {
            state.errors.push(error.message);
            return route.abort().catch(() => {});
        }
    });
    return {
        page, context, state,
        async close() {
            state.pending.forEach(item => item.resolve());
            await context.close();
        },
    };
}

async function open(env, id = 'A') {
    await env.page.goto(origin + '/importacoes_sku.html?lista_id=' + id + '&sku=001');
}

async function visibleSaved(env, quantity = '2') {
    await env.page.waitForFunction(expected =>
        document.getElementById('vSku').textContent === '001'
        && document.getElementById('vQtd').textContent === expected, quantity);
}

async function seed(env, full = false) {
    await env.page.goto(origin + '/seed.html');
    await env.page.evaluate(async ({ data, full, key }) => {
        const options = { tags: ['importacoes', 'vendas'] };
        const ok = await JKCachePersistente.salvar(key, { lista: data }, options);
        if (!ok) throw new Error('A fixture não conseguiu salvar o cache canônico.');
        if (!full) return;
        const snapshot = {
            versao: 1, lista: data, cadastro: { tituloPt: 'Cadastro salvo', ncm: '00000001', m3Individual: 0.1 },
            mapaM3: [['001', 0.1]], impostos: [], cotacao: 5, custoPendente: false,
            concorrentes: { links: {}, valores: { concorrente_1: 'R$ 200,00' }, mlb: {}, preco: {} },
            precosOfertas: {},
        };
        if (!await JKCachePersistente.salvar('importacoes:SKU:A:001', snapshot, options)) {
            throw new Error('A fixture não conseguiu salvar o snapshot completo.');
        }
    }, { data: lista(), full, key: canonicalKey });
}

function verifyCalls(env) {
    assert.strictEqual(env.state.calls.filter(call => call.kind === 'blocking').length, 0,
        'A abertura do SKU não deve executar o GET completo que bloqueia na fila.');
    assert.deepStrictEqual(env.state.errors, [], 'Nenhum erro de página nem acesso externo.');
}

async function run(browser, name, options, check) {
    const env = await environment(browser, options);
    try {
        await check(env);
        verifyCalls(env);
        console.log('OK: ' + name);
    } finally {
        await env.close();
    }
}

(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        await run(browser, 'dados salvos antes da fila; waiting/refreshing/ready; edição preservada', {
            background: ({ state }) => state.phase === 'waiting' ? aguardando()
                : state.phase === 'refreshing' ? completo('A', 88, { estado: 'refreshing', stale: true, atualizando: true, retry_after_ms: 1000 })
                    : completo(),
        }, async env => {
            await open(env);
            await waitUntil(() => env.state.calls.some(call => call.kind === 'saved'),
                'A tela não consultou somente_salvos=true antes de aguardar a fila.');
            await visibleSaved(env);
            await waitUntil(() => env.state.backgroundById.A > 0);
            assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Produto salvo A');
            assert.strictEqual(await env.page.locator('#vCustoTotalProduto').textContent(), '-');
            assert.deepStrictEqual(env.state.complements, []);
            assert.strictEqual(env.state.patches.length, 0);
            await env.page.locator('.kv.prop').dblclick();
            await env.page.locator('.prop-input').fill('77');
            await env.page.evaluate(() => { window.fixtureEditor = document.querySelector('.prop-input'); });
            const before = env.state.backgroundById.A;
            env.state.phase = 'refreshing';
            await waitUntil(() => env.state.backgroundById.A > before);
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '2', 'Resultado stale não substitui os dados salvos atuais.');
            assert.deepStrictEqual(env.state.complements, []);
            assert.strictEqual(env.state.patches.length, 0);
            env.state.phase = 'ready';
            await waitUntil(() => env.state.patches.length === 1);
            await env.page.waitForFunction(() => controladoresDetalheSku.size === 0);
            assert.strictEqual(await env.page.locator('#erro').textContent(), '', 'O PATCH confirmado não pode invalidar o próprio carregamento.');
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '9');
            assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Cadastro store-A');
            assert.notStrictEqual(await env.page.locator('#vCustoTotalProduto').textContent(), '-');
            assert.strictEqual(await env.page.evaluate(() => document.querySelector('.prop-input') === window.fixtureEditor
                && document.activeElement === window.fixtureEditor), true);
            assert.strictEqual(await env.page.locator('.prop-input').inputValue(), '77');
            await env.page.locator('.prop-input').press('Escape');
        });

        await run(browser, 'falha do cálculo preserva os dados salvos', {
            background: () => ({ status: 500, body: { detail: 'Cálculo indisponível na fixture' } }),
        }, async env => {
            await open(env);
            await visibleSaved(env);
            await env.page.waitForFunction(() => /não foi possível|indispon|tentar/i.test(
                document.getElementById('cacheSkuStatus').textContent + document.getElementById('erro').textContent));
            assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Produto salvo A');
            assert.strictEqual(env.state.patches.length, 0);
            assert.deepStrictEqual(env.state.complements, []);
        });

        await run(browser, 'leitura salva falha, resultado pronto recupera a abertura', {
            saved: () => ({ status: 500, body: { detail: 'Snapshot indisponível na fixture' } }),
        }, async env => {
            await open(env);
            await waitUntil(() => env.state.patches.length === 1);
            await env.page.waitForFunction(() => controladoresDetalheSku.size === 0);
            assert.strictEqual(await env.page.locator('#erro').textContent(), '', 'O PATCH confirmado não pode invalidar o próprio carregamento.');
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '9');
            assert.strictEqual(await env.page.locator('#erro').textContent(), '');
        });

        await run(browser, 'prazo da leitura salva não impede o resultado pronto', {
            fastRequestTimeout: true,
            saved: async ({ state }) => {
                const pending = gate();
                state.pending.push(pending);
                await pending.promise;
                return { lista: lista('A', 88) };
            },
        }, async env => {
            await open(env);
            await waitUntil(() => env.state.patches.length === 1);
            await env.page.waitForFunction(() => controladoresDetalheSku.size === 0);
            assert.strictEqual(await env.page.locator('#erro').textContent(), '', 'O PATCH confirmado não pode invalidar o próprio carregamento.');
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '9');
        });

        await run(browser, '202 e falha preservam custo completo e cache anterior', {
            background: ({ state }) => state.phase === 'waiting' ? aguardando()
                : ({ status: 500, body: { detail: 'Atualização indisponível' } }),
        }, async env => {
            await seed(env, true);
            await open(env);
            await env.page.waitForFunction(() => document.getElementById('vCustoTotalProduto').textContent !== '-');
            const custo = await env.page.locator('#vCustoTotalProduto').textContent();
            await waitUntil(() => env.state.backgroundById.A > 0);
            assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Cadastro salvo');
            assert.strictEqual(await env.page.locator('#vCustoTotalProduto').textContent(), custo);
            assert.strictEqual(await env.page.evaluate(async key =>
                (await JKCachePersistente.ler(key))?.dados.lista.itens[0].Quantidade, canonicalKey), 2);
            assert.strictEqual(env.state.patches.length, 0);
            assert.deepStrictEqual(env.state.complements, []);
            env.state.phase = 'fail';
            await env.page.waitForFunction(() => /não foi possível|indispon/i.test(
                document.getElementById('cacheSkuStatus').textContent + document.getElementById('erro').textContent));
            assert.strictEqual(await env.page.locator('#vCustoTotalProduto').textContent(), custo);
        });

        for (const status of [401, 403]) {
            await run(browser, status + ' na consulta salva remove cache visível', {
                saved: () => ({ status, body: { detail: 'Acesso negado na fixture' } }),
                background: () => completo('A', 88),
            }, async env => {
                await seed(env, true);
                await open(env);
                await env.page.waitForFunction(() => document.querySelector('.layout').children.length === 0);
                assert.strictEqual(env.state.patches.length, 0);
            });
        }

        await run(browser, '403 no acompanhamento impede repovoamento por cache', {
            background: () => ({ status: 403, body: { detail: 'Permissão revogada' } }),
        }, async env => {
            await seed(env, true);
            await open(env);
            await env.page.waitForFunction(() => document.querySelector('.layout').children.length === 0);
            assert.strictEqual(env.state.patches.length, 0);
        });

        for (const lateStatus of [200, 403]) {
            await run(browser, 'troca de lista ignora resposta anterior ' + lateStatus, {
                ignoreBackgroundAbort: true,
                background: async ({ id, state }) => {
                    if (id !== 'A') return completo(id, 7);
                    const pending = gate();
                    state.pending.push(pending);
                    await pending.promise;
                    return lateStatus === 403 ? { status: 403, body: { detail: 'Negação antiga de A' } } : completo('A', 88);
                },
            }, async env => {
                await open(env);
                await visibleSaved(env);
                await waitUntil(() => env.state.backgroundById.A > 0);
                await env.page.evaluate(() => {
                    history.replaceState({}, '', '?lista_id=B&sku=001');
                    void carregarDetalheSku();
                });
                await waitUntil(() => env.state.patches.length === 1);
                env.state.pending.forEach(item => item.resolve());
                await env.page.waitForFunction(() => controladoresDetalheSku.size === 0);
                assert.strictEqual(await env.page.locator('#erro').textContent(), '', 'Resposta antiga não pode invalidar o carregamento atual.');
                await env.page.waitForLoadState('networkidle');
                assert.strictEqual(await env.page.locator('#vQtd').textContent(), '7');
                assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Cadastro store-B');
                assert(env.state.patches.every(item => item.pathname.includes('/B/')));
            });
        }

        await run(browser, 'revisão alterada rejeita cálculo iniciado antes da edição', {
            background: async ({ state }) => {
                const pending = gate();
                state.pending.push(pending);
                await pending.promise;
                return completo('A', 88);
            },
        }, async env => {
            await open(env);
            await visibleSaved(env);
            await waitUntil(() => env.state.backgroundById.A > 0);
            await env.page.evaluate(() => JKCachePersistente.invalidar(['importacoes']));
            env.state.pending.forEach(item => item.resolve());
            await env.page.waitForLoadState('networkidle');
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '2');
            assert.deepStrictEqual(env.state.complements, []);
            assert.strictEqual(env.state.patches.length, 0);
        });

        await run(browser, 'troca de cliente limpa dados e ignora a resposta pendente', {
            background: async ({ state }) => {
                const pending = gate();
                state.pending.push(pending);
                await pending.promise;
                return completo('A', 88);
            },
        }, async env => {
            await open(env);
            await visibleSaved(env);
            await waitUntil(() => env.state.backgroundById.A > 0);
            await env.page.evaluate(() => {
                localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-B', username: 'tester' }));
                window.dispatchEvent(new CustomEvent('jk-tenant-changed'));
            });
            env.state.pending.forEach(item => item.resolve());
            await env.page.waitForLoadState('networkidle');
            assert.strictEqual(await env.page.locator('.layout .card').count(), 0);
            assert.strictEqual(env.state.patches.length, 0);
        });

        await run(browser, 'resposta pronta de outra lista não substitui os dados autorizados', {
            background: () => completo('B', 88),
        }, async env => {
            await open(env);
            await visibleSaved(env);
            await env.page.waitForLoadState('networkidle');
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '2');
            assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Produto salvo A');
            assert.strictEqual(env.state.patches.length, 0);
            assert.deepStrictEqual(env.state.complements, []);
        });

        await run(browser, 'cache básico não retém quantidade antiga quando um complemento falha', {
            complement: ({ pathname }) => pathname === '/api/impostos/produtos'
                ? { status: 500, body: { detail: 'Fiscal indisponível' } } : undefined,
        }, async env => {
            await seed(env);
            await open(env);
            await env.page.waitForFunction(() => document.getElementById('erro').textContent.includes('complementares'));
            await env.page.waitForFunction(() => controladoresDetalheSku.size === 0);
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '9');
            assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Cadastro store-A');
            assert.strictEqual(await env.page.locator('#vCustoTotalProduto').textContent(), '-');
            assert.strictEqual(env.state.patches.length, 0);
        });

        await run(browser, 'revisão alterada durante complementos impede cadastro e análise antigos', {
            complement: async ({ pathname, state }) => {
                if (pathname !== '/api/cadastro/lojas/store-A/produtos/001') return undefined;
                const pending = gate();
                state.pending.push(pending);
                await pending.promise;
                return { produto: { store_id: 'store-A', sku: '001', nome: 'Cadastro antigo indevido',
                    ncm: '00000002', 'M3 individual': 0.1 } };
            },
        }, async env => {
            await open(env);
            await visibleSaved(env, '9');
            await waitUntil(() => env.state.pending.length > 0);
            await env.page.evaluate(() => JKCachePersistente.invalidar(['importacoes']));
            env.state.pending.forEach(item => item.resolve());
            await env.page.waitForLoadState('networkidle');
            assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Produto salvo A');
            assert.strictEqual(await env.page.locator('#vCustoTotalProduto').textContent(), '-');
            assert.strictEqual(env.state.patches.length, 0);
        });

        await run(browser, 'prazo total encerra acompanhamento sem esvaziar o SKU', {
            background: () => aguardando(),
        }, async env => {
            await env.page.clock.install();
            await open(env);
            await visibleSaved(env);
            await waitUntil(() => env.state.backgroundById.A > 0);
            await env.page.clock.fastForward(121000);
            await env.page.waitForFunction(() => /não foi possível|tempo|pendente|tentar/i.test(
                document.getElementById('cacheSkuStatus').textContent + document.getElementById('erro').textContent));
            const before = env.state.backgroundById.A;
            await env.page.clock.fastForward(20000);
            assert.strictEqual(env.state.backgroundById.A, before, 'Acompanhamento deve parar depois do prazo global.');
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '2');
            assert.strictEqual(env.state.patches.length, 0);
        });
        console.log('SKU: dados salvos, fila em segundo plano, cache, prazos e isolamento: OK');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
