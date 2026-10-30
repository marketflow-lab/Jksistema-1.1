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

function fontesFinanceiras(id = 'A') {
    return {
        concorrentes: { concorrente_1: 'https://produto.mercadolivre.com.br/MLB-1234567890' },
        concorrentes_valores: { concorrente_1: id === 'A' ? 'R$ 200,00' : 'R$ 250,00' },
        concorrentes_mlb: { concorrente_1: id === 'A'
            ? 'https://produto.mercadolivre.com.br/MLB-9876543210'
            : 'https://produto.mercadolivre.com.br/MLB-8765432109' },
        concorrentes_preco: { concorrente_1: id === 'A' ? 'R$ 300,00' : 'R$ 350,00' },
    };
}

function respostaFinanceira(payload, margemPropria = 33) {
    const dados = preco => ({
        preco_venda: preco, item_id_loja: payload.anuncios_loja.concorrente_1, financeiro_exato: true,
        custo_unitario: 50, imposto_valor: 20, tarifa_ml: 30, frete_ml: 10, valor_liquido: 190,
    });
    return { analise_concorrentes: {
        margens: { concorrente_1: { ...dados(payload.precos_concorrentes.concorrente_1), margem_percentual: 12 } },
        margens_anuncios_loja: { concorrente_1: { ...dados(payload.precos_anuncios_loja.concorrente_1), margem_percentual: margemPropria } },
    } };
}

function opcoesFinanceiras(overrides = {}) {
    return {
        holdPrices: true,
        complement: ({ pathname, url }) => pathname === '/api/medias-compras/concorrentes-links'
            ? fontesFinanceiras(url.searchParams.get('lista_id')) : undefined,
        patch: ({ payload }) => respostaFinanceira(payload),
        ...overrides,
    };
}

async function aguardarMargens(env) {
    await env.page.waitForFunction(() => document.getElementById('cacheSkuStatus').textContent === 'Dados atualizados.'
        && document.getElementById('vMargemMlb1').textContent === 'Margem: 33,00%'
        && document.getElementById('vMargemConcorrente1').textContent === 'Margem: 12,00%');
}

async function waitUntil(predicate, message) {
    for (let attempt = 0; attempt < 500; attempt++) {
        if (predicate()) return;
        await new Promise(resolve => setTimeout(resolve, 10));
    }
    throw new Error(message || 'Condição da fixture não atingida.');
}

async function environment(browser, options = {}) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await context.newPage();
    const state = {
        calls: [], complements: [], patches: [], errors: [], pending: [],
        backgroundById: {}, phase: 'waiting',
    };
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => state.errors.push(error.message));
    await context.addInitScript(({ testOrigin, fastRequestTimeout, ignoreBackgroundAbort, holdPrices }) => {
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
        if (holdPrices) {
            window.fixturePrices = [];
            window.releasePrices = count => window.fixturePrices.splice(0, count ?? window.fixturePrices.length)
                .forEach(item => item.resolve({ price: 999, original_price: 1000 }));
            window.electronAPI = { getMlBrowserItemInfo: (itemId, url) => new Promise(resolve => {
                window.fixturePrices.push({ itemId, url, resolve });
            }) };
        }
        const original = window.setTimeout.bind(window);
        window.setTimeout = (callback, delay, ...args) => original(callback,
            fastRequestTimeout && [5000, 20000].includes(delay) ? 75 : [1000, 2000].includes(delay) ? 25 : delay, ...args);
    }, { testOrigin: origin, fastRequestTimeout: Boolean(options.fastRequestTimeout),
        ignoreBackgroundAbort: Boolean(options.ignoreBackgroundAbort), holdPrices: Boolean(options.holdPrices) });
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
                const call = { pathname, payload: route.request().postDataJSON() };
                state.patches.push(call);
                if (options.patch) return json(route, await options.patch({ ...call, state }));
                return json(route, { analise_concorrentes: { margens: {} } });
            }
            if (pathname.startsWith('/api/')) {
                state.complements.push(pathname);
                if (options.complement) {
                    const custom = await options.complement({ pathname, state, url });
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
        await run(browser, 'margens próprias e Mercado ficam prontas antes das promoções opcionais', opcoesFinanceiras(), async env => {
            await open(env);
            await aguardarMargens(env);
            await env.page.waitForFunction(() => window.fixturePrices.length === 2);
            assert.strictEqual(env.state.patches.length, 1);
            assert.strictEqual(env.state.patches[0].payload.precos_anuncios_loja.concorrente_1, 300);
            assert.strictEqual(env.state.patches[0].payload.precos_concorrentes.concorrente_1, 200);
            const salvo = await env.page.evaluate(async () => (await JKCachePersistente.ler('importacoes:SKU:A:001'))?.dados);
            assert.strictEqual(salvo.lista.itens[0].analise_concorrentes.margens_anuncios_loja.concorrente_1.margem_percentual, 33,
                'Snapshot financeiro completo não aguarda o IPC de promoções.');
            if (process.env.JK_SKU_MARGIN_SCREENSHOT) {
                await env.page.setViewportSize({ width: 1680, height: 1050 });
                await env.page.locator('.analysis-comparison-card').first().scrollIntoViewIfNeeded();
                await env.page.screenshot({ path: process.env.JK_SKU_MARGIN_SCREENSHOT });
            }
            await env.page.evaluate(() => window.releasePrices());
            await env.page.waitForFunction(() => !document.getElementById('vPromoMlb1').hidden
                && !document.getElementById('vPromoConcorrente1').hidden);
            assert.strictEqual(await env.page.locator('#vMlb1').textContent(), await env.page.evaluate(() => fmtBRL(300)));
            assert.strictEqual(await env.page.locator('#vConcorrente1').textContent(), await env.page.evaluate(() => fmtBRL(200)));
            await env.page.waitForFunction(async () => (await JKCachePersistente.ler('importacoes:SKU:A:001'))
                ?.dados.precosOfertas['mlb:1'].temPromocao === true);
            assert.strictEqual(env.state.patches.length, 1, 'Metadados promocionais não causam uma segunda mutação financeira.');
            await aguardarMargens(env);
            await env.page.evaluate(() => { window.fixtureAnaliseExata = JSON.parse(JSON.stringify(ultimaAnaliseConcorrentes)); });
            for (const change of ['financeiro_exato', 'item_id_loja', 'preco_venda']) {
                await env.page.evaluate(change => {
                    const analise = JSON.parse(JSON.stringify(window.fixtureAnaliseExata));
                    analise.margens_anuncios_loja.concorrente_1[change] = change === 'financeiro_exato' ? false
                        : change === 'item_id_loja' ? 'MLB1111111111' : 999;
                    aplicarAnaliseConcorrentes(analise);
                }, change);
                assert.strictEqual(await env.page.locator('#vMargemMlb1').textContent(), 'Margem: -',
                    'Margem própria exige financeiro exato e o mesmo anúncio/preço exibidos.');
                assert.strictEqual(await env.page.locator('#vMargemConcorrente1').textContent(), 'Margem: 12,00%');
            }
        });

        await run(browser, 'promoção antiga não altera o SKU depois de navegar para outra lista', opcoesFinanceiras(), async env => {
            await open(env);
            await aguardarMargens(env);
            await env.page.waitForFunction(() => window.fixturePrices.length === 2);
            await env.page.evaluate(() => { history.replaceState({}, '', '?lista_id=B&sku=001'); return carregarDetalheSku(); });
            await aguardarMargens(env);
            await env.page.waitForFunction(() => window.fixturePrices.length === 4);
            await env.page.evaluate(() => window.releasePrices(2));
            await env.page.waitForTimeout(80);
            assert.strictEqual(await env.page.locator('#vMlb1').textContent(), await env.page.evaluate(() => fmtBRL(350)));
            assert.strictEqual(await env.page.locator('#vConcorrente1').textContent(), await env.page.evaluate(() => fmtBRL(250)));
            assert.strictEqual(await env.page.locator('#vPromoMlb1').isHidden(), true);
            assert.strictEqual(env.state.patches.length, 2);
        });

        for (const change of ['auth', 'revision']) {
            await run(browser, 'promoção tardia respeita mudança de ' + change, opcoesFinanceiras(), async env => {
                await open(env);
                await aguardarMargens(env);
                await env.page.waitForFunction(() => window.fixturePrices.length === 2);
                await env.page.evaluate(async change => {
                    if (change === 'auth') window.dispatchEvent(new CustomEvent('jk-auth-changed'));
                    else await JKCachePersistente.invalidar(['importacoes']);
                    window.releasePrices();
                }, change);
                await env.page.waitForTimeout(100);
                if (change === 'auth') assert.strictEqual(await env.page.locator('.layout > *').count(), 0);
                else {
                    assert.strictEqual(await env.page.locator('#vPromoMlb1').isHidden(), true);
                    assert.strictEqual(await env.page.evaluate(async () => (await JKCachePersistente.ler('importacoes:SKU:A:001')) === null), true);
                }
                assert.strictEqual(env.state.patches.length, 1);
            });
        }

        for (const failure of ['receipt', 'timeout']) {
            await run(browser, 'análise com ' + failure + ' não publica margem nem snapshot completo', opcoesFinanceiras({
                fastRequestTimeout: failure === 'timeout',
                patch: async ({ payload, state }) => {
                    const pending = gate(); state.pending.push(pending);
                    await pending.promise;
                    return respostaFinanceira(payload);
                },
            }), async env => {
                await open(env);
                await waitUntil(() => env.state.patches.length === 1);
                if (failure === 'receipt') {
                    await env.page.evaluate(() => JKCachePersistente.invalidar(['importacoes']));
                    env.state.pending[0].resolve();
                }
                await env.page.waitForFunction(() => contextoDetalheSku.podeSalvar === false
                    && !/Calculando|Consultando/.test(document.getElementById('cacheSkuStatus').textContent));
                assert.strictEqual(await env.page.locator('#vMargemMlb1').textContent(), 'Margem: -');
                assert.strictEqual(await env.page.evaluate(() => window.fixturePrices.length), 0);
                assert.strictEqual(await env.page.evaluate(async () => (await JKCachePersistente.ler('importacoes:SKU:A:001')) === null), true);
            });
        }

        await run(browser, 'edição da proporção ignora análise cujo recibo perdeu a revisão', opcoesFinanceiras({
            patch: async ({ payload, state }) => {
                if (state.patches.length === 1) return respostaFinanceira(payload);
                const pending = gate(); state.pending.push(pending);
                await pending.promise;
                return respostaFinanceira(payload, 77);
            },
        }), async env => {
            await open(env);
            await aguardarMargens(env);
            await env.page.locator('.kv.prop').dblclick();
            await env.page.locator('.prop-input').fill('80');
            await env.page.locator('.prop-input').press('Enter');
            await waitUntil(() => env.state.patches.length === 2);
            await env.page.evaluate(() => JKCachePersistente.invalidar(['importacoes']));
            env.state.pending[0].resolve();
            await env.page.waitForFunction(() => contextoDetalheSku.podeSalvar === false);
            assert.notStrictEqual(await env.page.locator('#vMargemMlb1').textContent(), 'Margem: 77,00%');
            assert.strictEqual(await env.page.evaluate(async () => (await JKCachePersistente.ler('importacoes:SKU:A:001')) === null), true);
        });

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

        for (const failure of ['fiscal 500', 'fiscal timeout', 'concorrentes 500']) {
            await run(browser, 'cache básico encerra a atualização com falha ' + failure, {
                fastRequestTimeout: failure.endsWith('timeout'),
                complement: async ({ pathname, state }) => {
                    if (failure.startsWith('fiscal') && pathname === '/api/medias-compras/concorrentes-links') {
                        return {
                            concorrentes: { concorrente_1: 'https://produto.mercadolivre.com.br/MLB-1234567890' },
                            concorrentes_valores: { concorrente_1: 'R$ 200,00' },
                        };
                    }
                    const target = failure.startsWith('fiscal') ? '/api/impostos/produtos' : '/api/medias-compras/concorrentes-links';
                    if (pathname !== target) return undefined;
                    if (failure.endsWith('timeout')) {
                        const pending = gate();
                        state.pending.push(pending);
                        await pending.promise;
                    }
                    return { status: 500, body: { detail: 'Complemento indisponível' } };
                },
            }, async env => {
                await seed(env);
                await open(env);
                await env.page.waitForFunction(() => document.getElementById('erro').textContent.includes('complementares'));
                await env.page.waitForFunction(() => controladoresDetalheSku.size === 0
                    && document.getElementById('cacheSkuStatus').textContent.includes('Não foi possível'));
                assert.doesNotMatch(await env.page.locator('#cacheSkuStatus').textContent(), /Atualizando|Consultando preços/);
                assert.strictEqual(await env.page.locator('#vQtd').textContent(), '9');
                assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Cadastro store-A');
                if (failure.startsWith('fiscal')) {
                    assert.strictEqual(await env.page.locator('#vCustoTotalProduto').textContent(), '-');
                    assert.strictEqual(await env.page.locator('#lblConcorrente1 a').getAttribute('href'),
                        'https://produto.mercadolivre.com.br/MLB-1234567890');
                    assert.strictEqual(await env.page.locator('#vConcorrente1').textContent(),
                        await env.page.evaluate(() => fmtBRL(200)),
                    'Falha fiscal preserva links e preços válidos dos concorrentes.');
                }
                assert.strictEqual(await env.page.evaluate(() => analiseComplementosPronta), false);
                assert.strictEqual(env.state.patches.length, 0);
                assert.strictEqual(await env.page.evaluate(async () =>
                    (await JKCachePersistente.ler('importacoes:SKU:A:001')) === null), true,
                'Falha complementar não publica um snapshot completo parcial.');
            });
        }

        await run(browser, 'falha complementar encerra a atualização e preserva o snapshot completo', {
            complement: ({ pathname }) => pathname === '/api/impostos/produtos'
                ? { status: 500, body: { detail: 'Fiscal indisponível' } } : undefined,
        }, async env => {
            await seed(env, true);
            await open(env);
            await env.page.waitForFunction(() => document.getElementById('cacheSkuStatus').textContent.includes('Algumas informações não puderam'));
            assert.doesNotMatch(await env.page.locator('#cacheSkuStatus').textContent(), /Atualizando|Consultando preços/);
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '2');
            assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Cadastro salvo');
            assert.notStrictEqual(await env.page.locator('#vCustoTotalProduto').textContent(), '-');
            assert.strictEqual(env.state.patches.length, 0);
            assert.strictEqual(await env.page.evaluate(async () =>
                (await JKCachePersistente.ler('importacoes:SKU:A:001'))?.dados.lista.itens[0].Quantidade), 2);
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
