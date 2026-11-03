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
        id, nome_lista: 'Pedido ' + id, loja: 'Loja ' + id, store_id: 'store-' + id, dolar_hoje: 5,
        itens: [{
            SKU: '001', Quantidade: quantidade, 'Valor unidade': 10, 'Valor total': quantidade * 10,
            'M3 individual': 0.1, Produto: 'Produto salvo ' + id, NCM: '00000001',
            Link: 'https://produto.test/' + id, II: 10, IPI: 5, PIS: 2, COFINS: 3,
            'ST Minas': 'Não', 'ST Paraíba': 'Não', analise_simulacao_importacao_sku: { margens: {} },
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
        anuncios_loja_store_id: 'store-' + id, anuncios_loja_nome: 'Loja ' + id,
        anuncios_loja_fonte: 'loja_lista_sku',
        concorrentes: { concorrente_1: 'https://produto.mercadolivre.com.br/MLB-1234567890' },
        concorrentes_valores: { concorrente_1: id === 'A' ? 'R$ 200,00' : 'R$ 250,00' },
        concorrentes_mlb: { concorrente_1: id === 'A'
            ? 'https://produto.mercadolivre.com.br/MLB-9876543210'
            : 'https://produto.mercadolivre.com.br/MLB-8765432109' },
        concorrentes_preco: { concorrente_1: id === 'A' ? 'R$ 300,00' : 'R$ 350,00' },
    };
}

function margemSimulada(custo, preco) {
    return ((preco - custo - preco * .1 - preco * .15 - 8) * 100) / preco;
}

function respostaFinanceira(payload, margemPropria = null, storeId = 'store-A') {
    const dados = preco => ({
        preco_venda: preco, item_id_loja: payload.anuncios_loja.concorrente_1, financeiro_exato: true,
        loja: 'Loja ' + storeId.slice(-1), store_id: storeId, seller_id: 'seller-' + storeId, site_id: 'MLB',
        fonte_custo: payload.fonte_custo, custo_unitario: payload.custo_unitario,
        imposto_valor: preco * .1, tarifa_ml: preco * .15, frete_ml: 8,
        valor_liquido: preco - payload.custo_unitario - preco * .1 - preco * .15 - 8,
        margem_percentual: margemSimulada(payload.custo_unitario, preco),
    });
    return { analise_concorrentes: {
        fonte_custo: payload.fonte_custo, custo_unitario: payload.custo_unitario, store_id: storeId,
        margens: { concorrente_1: dados(payload.precos_concorrentes.concorrente_1) },
        margens_anuncios_loja: { concorrente_1: { ...dados(payload.precos_anuncios_loja.concorrente_1),
            ...(margemPropria === null ? {} : { margem_percentual: margemPropria }) } },
    } };
}

function opcoesFinanceiras(overrides = {}) {
    return {
        holdPrices: true,
        complement: ({ pathname, url }) => pathname === '/api/medias-compras/concorrentes-links'
            ? fontesFinanceiras(url.searchParams.get('lista_id')) : undefined,
        patch: ({ payload, pathname }) => respostaFinanceira(payload, null, pathname.includes('/B/') ? 'store-B' : 'store-A'),
        ...overrides,
    };
}

async function aguardarMargens(env) {
    await env.page.waitForFunction(() => {
        const custo = ultimoValorUnitarioProduto;
        const margem = preco => ((preco - custo - preco * .1 - preco * .15 - 8) * 100) / preco;
        const propria = getNumber(document.getElementById('vMlb1').textContent);
        const mercado = getNumber(document.getElementById('vConcorrente1').textContent);
        return custo !== null && document.getElementById('cacheSkuStatus').textContent === 'Dados atualizados.'
            && document.getElementById('vMargemMlb1').textContent === 'Margem: ' + fmtPct(margem(propria))
            && document.getElementById('vMargemConcorrente1').textContent === 'Margem: ' + fmtPct(margem(mercado));
    });
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
            fastRequestTimeout && [5000, 20000, 120000].includes(delay) ? 75 : [1000, 2000].includes(delay) ? 25 : delay, ...args);
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
            if (pathname === '/medias_compras/importacoes-custo-simulado.js') return route.fulfill({
                status: 200, contentType: 'application/javascript',
                body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
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
                assert.strictEqual(call.payload.fonte_custo, 'simulacao_importacao_sku');
                assert(Number.isFinite(call.payload.custo_unitario) && call.payload.custo_unitario > 0,
                    'A simulação exige o custo unitário válido do painel.');
                state.patches.push(call);
                if (options.patch) return json(route, await options.patch({ ...call, state }));
                return json(route, { analise_concorrentes: {
                    fonte_custo: call.payload.fonte_custo, custo_unitario: call.payload.custo_unitario,
                    store_id: pathname.includes('/B/') ? 'store-B' : 'store-A',
                    margens: {}, margens_anuncios_loja: {},
                } });
            }
            if (pathname.startsWith('/api/')) {
                state.complements.push(pathname);
                if (pathname === '/api/medias-compras/concorrentes-links') {
                    assert.strictEqual(url.searchParams.get('anuncios_da_lista'), 'true',
                        'A tela pede anúncios exclusivamente da loja da lista.');
                }
                if (options.complement) {
                    const custom = await options.complement({ pathname, state, url });
                    if (custom !== undefined) return json(route, custom);
                }
                const product = pathname.match(/^\/api\/cadastro\/lojas\/(store-[AB])\/produtos\/001$/);
                if (product) return json(route, { produto: {
                    store_id: product[1], sku: '001', nome: 'Cadastro ' + product[1],
                    ncm: '00000002', 'M3 individual': 0.1, custo: '999', imposto: '10',
                } });
                if (/^\/api\/cadastro\/lojas\/store-[AB]\/produtos$/.test(pathname)) return json(route, []);
                if (pathname === '/api/impostos/produtos') return json(route, { produtos: [] });
                if (pathname === '/api/medias-compras/concorrentes-links') return json(route, {
                    anuncios_loja_store_id: 'store-' + url.searchParams.get('lista_id'),
                    anuncios_loja_nome: 'Loja ' + url.searchParams.get('lista_id'), anuncios_loja_fonte: 'loja_lista_sku',
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

async function seed(env, full = false, snapshotVersion = 3) {
    await env.page.goto(origin + '/seed.html');
    await env.page.evaluate(async ({ data, full, key, snapshotVersion }) => {
        const options = { tags: ['importacoes', 'vendas'] };
        const ok = await JKCachePersistente.salvar(key, { lista: data }, options);
        if (!ok) throw new Error('A fixture não conseguiu salvar o cache canônico.');
        if (!full) return;
        const snapshot = {
            versao: snapshotVersion, anunciosDaLista: snapshotVersion >= 2,
            lista: data, cadastro: { tituloPt: 'Cadastro salvo', ncm: '00000001', m3Individual: 0.1 },
            mapaM3: [['001', 0.1]], impostos: [], cotacao: 5, custoPendente: false,
            concorrentes: { links: {}, valores: { concorrente_1: 'R$ 200,00' }, mlb: {}, preco: {},
                storeId: 'store-A', lojaNome: 'Loja A', fonte: 'loja_lista_sku' },
            precosOfertas: {},
        };
        if (!await JKCachePersistente.salvar('importacoes:SKU:A:001', snapshot, options)) {
            throw new Error('A fixture não conseguiu salvar o snapshot completo.');
        }
    }, { data: lista(), full, key: canonicalKey, snapshotVersion });
}

function verifyCalls(env) {
    assert.strictEqual(env.state.calls.filter(call => call.kind === 'blocking').length, 0,
        'A abertura do SKU não deve executar o GET completo que bloqueia na fila.');
    assert.deepStrictEqual(env.state.errors, [], 'Nenhum erro de página nem acesso externo.');
}

async function run(browser, name, options, check) {
    if (process.env.JK_SKU_TEST_CASE && !name.includes(process.env.JK_SKU_TEST_CASE)) return;
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
            assert.strictEqual(await env.page.locator('.analysis-offer.own .analysis-offer-role').first().textContent(), 'Loja A');
            assert.match(await env.page.locator('#vMargemMlb1').getAttribute('title'), /Custo unitário simulado/);
            assert.strictEqual(await env.page.evaluate(() => contextoDetalheSku.listaPronta.store_id), 'store-A');
            await env.page.waitForFunction(() => window.fixturePrices.length === 2);
            assert.strictEqual(env.state.patches.length, 1);
            assert.strictEqual(env.state.patches[0].payload.precos_anuncios_loja.concorrente_1, 300);
            assert.strictEqual(env.state.patches[0].payload.precos_concorrentes.concorrente_1, 200);
            assert.strictEqual(env.state.patches[0].payload.custo_unitario, await env.page.evaluate(() => ultimoValorUnitarioProduto),
                'O pedido usa o custo arredondado exibido no painel.');
            const salvo = await env.page.evaluate(async () => (await JKCachePersistente.ler('importacoes:SKU:A:001'))?.dados);
            assert.strictEqual(salvo.versao, 3);
            assert.strictEqual(salvo.lista.itens[0].analise_simulacao_importacao_sku.custo_unitario,
                env.state.patches[0].payload.custo_unitario,
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
            for (const change of ['financeiro_exato', 'item_id_loja', 'preco_venda', 'custo_unitario', 'fonte_custo', 'store_id']) {
                await env.page.evaluate(change => {
                    const analise = JSON.parse(JSON.stringify(window.fixtureAnaliseExata));
                    analise.margens_anuncios_loja.concorrente_1[change] = change === 'financeiro_exato' ? false
                        : change === 'item_id_loja' ? 'MLB1111111111'
                            : change === 'store_id' ? 'store-JK'
                                : change === 'fonte_custo' ? 'cadastro_sku_loja' : 999;
                    aplicarAnaliseConcorrentes(analise);
                }, change);
                assert.strictEqual(await env.page.locator('#vMargemMlb1').textContent(), 'Margem: -',
                    'Margem própria exige anúncio, preço, custo simulado e loja correspondentes.');
                assert.strictEqual(await env.page.locator('#vMargemConcorrente1').textContent(), await env.page.evaluate(() =>
                    'Margem: ' + fmtPct(((200 - ultimoValorUnitarioProduto - 20 - 30 - 8) * 100) / 200)));
            }
        });

        await run(browser, 'anúncios de outra loja são rejeitados antes de enviar a simulação', opcoesFinanceiras({
            complement: ({ pathname }) => pathname === '/api/medias-compras/concorrentes-links'
                ? fontesFinanceiras('B') : undefined,
        }), async env => {
            await open(env);
            await env.page.waitForFunction(() => document.getElementById('erro').textContent.includes('complementares'));
            await env.page.waitForFunction(() => controladoresDetalheSku.size === 0);
            assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Cadastro store-A');
            assert.strictEqual(await env.page.locator('#lblMlb1 a').count(), 0);
            assert.strictEqual(await env.page.locator('#vMargemMlb1').textContent(), 'Margem: -');
            assert.strictEqual(env.state.patches.length, 0, 'A loja B não substitui os anúncios da lista A.');
        });

        await run(browser, 'cotação financeira de outra loja não aparece na simulação', opcoesFinanceiras({
            patch: ({ payload }) => respostaFinanceira(payload, null, 'store-B'),
        }), async env => {
            await open(env);
            await waitUntil(() => env.state.patches.length === 1);
            await env.page.evaluate(() => contextoDetalheSku.complementos?.analise);
            await env.page.waitForFunction(() => controladoresDetalheSku.size === 0);
            assert.strictEqual(env.state.patches.length, 1);
            assert.strictEqual(await env.page.locator('#vMargemMlb1').textContent(), 'Margem: -');
            assert.strictEqual(await env.page.locator('#vMargemConcorrente1').textContent(), 'Margem: -');
        });

        await run(browser, 'cache legado de anúncios JK preserva custo e descarta ofertas e margens antigas', opcoesFinanceiras({
            saved: async ({ state }) => {
                const pending = gate(); state.pending.push(pending);
                await pending.promise;
                return { lista: lista() };
            },
            background: () => aguardando(),
        }), async env => {
            await seed(env, true, 1);
            await env.page.evaluate(async () => {
                const key = 'importacoes:SKU:A:001';
                const data = (await JKCachePersistente.ler(key)).dados;
                data.concorrentes = { links: {}, valores: { concorrente_1: 'R$ 200,00' },
                    mlb: { concorrente_1: 'https://produto.mercadolivre.com.br/MLB-1111111111' },
                    preco: { concorrente_1: 'R$ 300,00' }, storeId: 'store-JK', lojaNome: 'JK Peças' };
                data.lista.itens[0].analise_concorrentes = { fonte_custo: 'cadastro_sku_loja', custo_unitario: 50,
                    margens: { concorrente_1: { item_id_loja: 'MLB1111111111', store_id: 'store-JK',
                        financeiro_exato: true, preco_venda: 200, custo_unitario: 50, margem_percentual: 99 } },
                    margens_anuncios_loja: { concorrente_1: { item_id_loja: 'MLB1111111111', store_id: 'store-JK',
                        financeiro_exato: true, preco_venda: 300, custo_unitario: 50, margem_percentual: 99 } } };
                await JKCachePersistente.salvar(key, data, { tags: ['importacoes', 'vendas'] });
            });
            await open(env);
            await env.page.waitForFunction(() => document.getElementById('vCustoTotalProduto').textContent !== '-');
            assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Cadastro salvo');
            assert.strictEqual(await env.page.locator('#lblMlb1 a').count(), 0);
            assert.strictEqual(await env.page.locator('#vMargemMlb1').textContent(), 'Margem: -');
            assert.strictEqual(await env.page.locator('#vMargemConcorrente1').textContent(), 'Margem: -');
            assert.strictEqual(env.state.patches.length, 0, 'O cache anterior não autoriza reaproveitar a análise de JK.');
            env.state.pending.forEach(item => item.resolve());
            await waitUntil(() => env.state.patches.length === 1);
            await env.page.evaluate(() => contextoDetalheSku.complementos?.analise);
            assert.strictEqual(env.state.patches[0].payload.custo_unitario,
                await env.page.evaluate(() => ultimoValorUnitarioProduto));
            assert.strictEqual(await env.page.locator('#lblMlb1').textContent(), 'MLB9876543210');
            assert.notStrictEqual(await env.page.locator('#vMargemMlb1').textContent(), 'Margem: 99,00%');
        });

        await run(browser, 'alterar proporção invalida a margem antiga e usa o novo custo simulado', opcoesFinanceiras({
            patch: async ({ payload, state }) => {
                if (state.patches.length === 1) return respostaFinanceira(payload);
                const pending = gate(); state.pending.push(pending);
                await pending.promise;
                return respostaFinanceira(payload);
            },
        }), async env => {
            await open(env);
            await aguardarMargens(env);
            const custoAnterior = env.state.patches[0].payload.custo_unitario;
            await env.page.locator('.kv.prop').dblclick();
            await env.page.locator('.prop-input').fill('80');
            await env.page.locator('.prop-input').press('Enter');
            await waitUntil(() => env.state.patches.length === 2);
            const custoAtual = await env.page.evaluate(() => ultimoValorUnitarioProduto);
            assert.notStrictEqual(custoAtual, custoAnterior);
            assert.strictEqual(env.state.patches[1].payload.custo_unitario, custoAtual);
            assert.strictEqual(await env.page.locator('#vMargemMlb1').textContent(), 'Margem: -');
            assert.strictEqual(await env.page.locator('#vMargemConcorrente1').textContent(), 'Margem: -');
            env.state.pending.forEach(item => item.resolve());
            await env.page.waitForFunction(() => document.getElementById('vMargemMlb1').textContent !== 'Margem: -'
                && document.getElementById('vMargemConcorrente1').textContent !== 'Margem: -');
            assert.strictEqual(await env.page.locator('#vMargemMlb1').textContent(), await env.page.evaluate(() =>
                'Margem: ' + fmtPct(((300 - ultimoValorUnitarioProduto - 30 - 45 - 8) * 100) / 300)));
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

        await run(browser, 'mudança do preço ignora análise cujo recibo perdeu a revisão', opcoesFinanceiras({
            patch: async ({ payload, state }) => {
                if (state.patches.length === 1) return respostaFinanceira(payload);
                const pending = gate(); state.pending.push(pending);
                await pending.promise;
                return respostaFinanceira(payload, 77);
            },
        }), async env => {
            await open(env);
            await aguardarMargens(env);
            await env.page.evaluate(() => {
                aplicarDetalhePrecoOferta('mlb', 1, { precoAtual: 301, precoCheio: null, temPromocao: false });
                void salvarAnaliseConcorrentes('A', '001').catch(() => {});
            });
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
            await env.page.waitForFunction(() => document.getElementById('vTituloPt').textContent === 'Cadastro store-A'
                && document.getElementById('vCustoTotalProduto').textContent !== '-');
            await waitUntil(() => env.state.patches.length === 1);
            assert(env.state.complements.includes('/api/cadastro/lojas/store-A/produtos/001'),
                'Cadastro individual começa antes do recálculo da lista.');
            assert.strictEqual(await env.page.evaluate(async () =>
                (await JKCachePersistente.ler('importacoes:SKU:A:001')) === null), true,
            'Prévia autorizada não publica um snapshot completo antes do resultado atual.');
            await env.page.locator('.kv.prop').dblclick();
            await env.page.locator('.prop-input').fill('77');
            await env.page.evaluate(() => { window.fixtureEditor = document.querySelector('.prop-input'); });
            const before = env.state.backgroundById.A;
            env.state.phase = 'refreshing';
            await waitUntil(() => env.state.backgroundById.A > before);
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '2', 'Resultado stale não substitui os dados salvos atuais.');
            assert.strictEqual(env.state.patches.length, 1, 'Resultado stale não repete a mesma análise financeira.');
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
            await env.page.waitForFunction(() => document.getElementById('vTituloPt').textContent === 'Cadastro store-A');
            await waitUntil(() => env.state.patches.length === 1);
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '2');
            assert.strictEqual(await env.page.evaluate(async () =>
                (await JKCachePersistente.ler('importacoes:SKU:A:001')) === null), true);
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
            await env.page.evaluate(() => contextoDetalheSku.complementos?.analise);
            assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Cadastro salvo');
            assert.strictEqual(await env.page.locator('#vCustoTotalProduto').textContent(), custo);
            assert.strictEqual(await env.page.evaluate(async key =>
                (await JKCachePersistente.ler(key))?.dados.lista.itens[0].Quantidade, canonicalKey), 2);
            await waitUntil(() => env.state.patches.length === 1);
            assert(env.state.complements.includes('/api/cadastro/lojas/store-A/produtos/001'));
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
            complement: async ({ state }) => {
                const pending = gate(); state.pending.push(pending);
                await pending.promise;
                return undefined;
            },
        }, async env => {
            await seed(env, true);
            await open(env);
            await env.page.waitForFunction(() => document.querySelector('.layout').children.length === 0);
            env.state.pending.forEach(item => item.resolve());
            await env.page.waitForLoadState('networkidle');
            assert.strictEqual(env.state.patches.length, 0);
            assert.strictEqual(await env.page.locator('.layout .card').count(), 0,
                'Complementos iniciados antes do 403 não repovoam o SKU.');
        });

        for (const lateStatus of [200, 403]) {
            await run(browser, 'troca de lista ignora resposta anterior ' + lateStatus, {
                ignoreBackgroundAbort: true,
                complement: async ({ pathname, state }) => {
                    if (pathname !== '/api/cadastro/lojas/store-A/produtos/001') return undefined;
                    const pending = gate(); state.pending.push(pending);
                    await pending.promise;
                    return undefined;
                },
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
                const anteriores = env.state.patches.length;
                await env.page.evaluate(() => {
                    history.replaceState({}, '', '?lista_id=B&sku=001');
                    void carregarDetalheSku();
                });
                await waitUntil(() => env.state.patches.some(item => item.pathname.includes('/B/')));
                env.state.pending.forEach(item => item.resolve());
                await env.page.waitForFunction(() => controladoresDetalheSku.size === 0);
                assert.strictEqual(await env.page.locator('#erro').textContent(), '', 'Resposta antiga não pode invalidar o carregamento atual.');
                await env.page.waitForLoadState('networkidle');
                assert.strictEqual(await env.page.locator('#vQtd').textContent(), '7');
                assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Cadastro store-B');
                assert(env.state.patches.slice(anteriores).every(item => item.pathname.includes('/B/')),
                    'A análise autorizada antes da navegação pode existir; respostas antigas não iniciam outra em A.');
            });
        }

        await run(browser, 'revisão alterada rejeita cálculo iniciado antes da edição', {
            complement: async ({ state }) => {
                const pending = gate(); state.pending.push(pending);
                await pending.promise;
                return undefined;
            },
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
            const anteriores = env.state.patches.length;
            await env.page.evaluate(() => JKCachePersistente.invalidar(['importacoes']));
            env.state.pending.forEach(item => item.resolve());
            await env.page.waitForLoadState('networkidle');
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '2');
            assert.strictEqual(env.state.patches.length, anteriores, 'Resultado anterior à revisão não inicia nova análise.');
        });

        await run(browser, 'troca de cliente limpa dados e ignora a resposta pendente', {
            complement: async ({ state }) => {
                const pending = gate(); state.pending.push(pending);
                await pending.promise;
                return undefined;
            },
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
            const anteriores = env.state.patches.length;
            await env.page.evaluate(() => {
                localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-B', username: 'tester' }));
                window.dispatchEvent(new CustomEvent('jk-tenant-changed'));
            });
            env.state.pending.forEach(item => item.resolve());
            await env.page.waitForLoadState('networkidle');
            assert.strictEqual(await env.page.locator('.layout .card').count(), 0);
            assert.strictEqual(env.state.patches.length, anteriores, 'Resposta do cliente anterior não inicia outra análise.');
        });

        await run(browser, 'resposta pronta de outra lista não substitui os dados autorizados', {
            background: () => completo('B', 88),
        }, async env => {
            await open(env);
            await visibleSaved(env);
            await env.page.waitForLoadState('networkidle');
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '2');
            assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Cadastro store-A');
            assert.strictEqual(env.state.patches.length, 1);
            assert(env.state.patches.every(patch => patch.pathname.includes('/A/')),
                'Resultado inválido de B não desvia a análise autorizada da lista A.');
            assert.strictEqual(await env.page.evaluate(async () =>
                (await JKCachePersistente.ler('importacoes:SKU:A:001')) === null), true);
        });

        for (const failure of ['fiscal 500', 'fiscal timeout', 'concorrentes 500']) {
            await run(browser, 'cache básico encerra a atualização com falha ' + failure, {
                fastRequestTimeout: failure.endsWith('timeout'),
                complement: async ({ pathname, state }) => {
                    if (failure.startsWith('fiscal') && pathname === '/api/medias-compras/concorrentes-links') {
                        return {
                            anuncios_loja_store_id: 'store-A', anuncios_loja_nome: 'Loja A', anuncios_loja_fonte: 'loja_lista_sku',
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
                assert.strictEqual(env.state.patches.length, 0,
                    'Sem custo simulado completo ou anúncios válidos, nenhuma análise financeira é enviada.');
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
            await env.page.evaluate(() => contextoDetalheSku.complementos?.analise);
            assert.doesNotMatch(await env.page.locator('#cacheSkuStatus').textContent(), /Atualizando|Consultando preços/);
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '2');
            assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Cadastro salvo');
            assert.notStrictEqual(await env.page.locator('#vCustoTotalProduto').textContent(), '-');
            assert.strictEqual(env.state.patches.length, 1, 'Uma simulação completa preservada continua sendo a base exibida.');
            assert.strictEqual(env.state.patches[0].payload.fonte_custo, 'simulacao_importacao_sku');
            assert.strictEqual(env.state.patches[0].payload.custo_unitario,
                await env.page.evaluate(() => ultimoValorUnitarioProduto), 'Falha de atualização não troca o custo simulado por cadastro.');
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
            assert.strictEqual(env.state.patches.length, 0, 'Cadastro concluído após a revisão não autoriza análise antiga.');
        });

        await run(browser, 'fiscal indisponível mantém margens pendentes sem recorrer ao cadastro', opcoesFinanceiras({
            background: () => aguardando(),
            complement: ({ pathname, url }) => pathname === '/api/impostos/produtos'
                ? { status: 500, body: { detail: 'Fiscal indisponível na fixture' } }
                : pathname === '/api/medias-compras/concorrentes-links'
                    ? fontesFinanceiras(url.searchParams.get('lista_id')) : undefined,
        }), async env => {
            await open(env);
            await waitUntil(() => env.state.complements.includes('/api/impostos/produtos'));
            await env.page.evaluate(async () => {
                await contextoDetalheSku.complementos?.previa;
                await contextoDetalheSku.complementos?.analise;
            });
            await waitUntil(() => env.state.backgroundById.A > 1);
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '2');
            assert.strictEqual(await env.page.locator('#vTituloPt').textContent(), 'Cadastro store-A');
            assert.strictEqual(await env.page.locator('#vCustoTotalProduto').textContent(), '-');
            assert.strictEqual(env.state.patches.length, 0, 'Cadastro disponível não substitui o custo simulado ausente.');
            assert.strictEqual(await env.page.locator('#vMargemMlb1').textContent(), 'Margem: -');
            assert.strictEqual(await env.page.locator('#vMargemConcorrente1').textContent(), 'Margem: -');
            assert.strictEqual(await env.page.evaluate(async () =>
                (await JKCachePersistente.ler('importacoes:SKU:A:001')) === null), true,
            'O custo fiscal incompleto não gera um snapshot financeiro completo.');
        });

        await run(browser, 'conflito financeiro antecipado permite nova tentativa com a lista pronta', opcoesFinanceiras({
            background: ({ state }) => state.phase === 'waiting' ? aguardando() : completo(),
            patch: ({ payload, state }) => state.patches.length === 1
                ? { status: 409, body: { detail: 'A lista mudou durante o cálculo financeiro.' } }
                : respostaFinanceira(payload),
        }), async env => {
            await open(env);
            await waitUntil(() => env.state.patches.length === 1);
            await env.page.waitForFunction(() => document.getElementById('erro').textContent.includes('mudou'));
            assert.strictEqual(await env.page.locator('#vMargemMlb1').textContent(), 'Margem: -');
            assert.strictEqual(await env.page.evaluate(async () =>
                (await JKCachePersistente.ler('importacoes:SKU:A:001')) === null), true,
            'Conflito não publica uma análise nem um snapshot completo.');
            env.state.phase = 'ready';
            await aguardarMargens(env);
            assert.strictEqual(env.state.patches.length, 2, 'A lista pronta permite uma nova tentativa após o conflito conhecido.');
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '9');
            assert.strictEqual(await env.page.locator('#erro').textContent(), '', 'Nova análise válida encerra o erro transitório.');
            const snapshot = await env.page.evaluate(async () => (await JKCachePersistente.ler('importacoes:SKU:A:001'))?.dados);
            assert.strictEqual(snapshot.lista.itens[0].Quantidade, 9);
            assert.strictEqual(snapshot.lista.itens[0].analise_simulacao_importacao_sku.custo_unitario,
                env.state.patches[1].payload.custo_unitario);
        });

        await run(browser, 'prazo total encerra acompanhamento sem esvaziar o SKU', {
            background: () => aguardando(),
        }, async env => {
            await env.page.clock.install();
            await open(env);
            await visibleSaved(env);
            await waitUntil(() => env.state.backgroundById.A > 0);
            await env.page.evaluate(async () => {
                await contextoDetalheSku.complementos?.analise;
                await contextoDetalheSku.complementos?.previa;
            });
            await env.page.clock.fastForward(121000);
            await env.page.waitForFunction(() => /não foi possível|tempo|pendente|tentar/i.test(
                document.getElementById('cacheSkuStatus').textContent + document.getElementById('erro').textContent));
            const before = env.state.backgroundById.A;
            await env.page.clock.fastForward(20000);
            assert.strictEqual(env.state.backgroundById.A, before, 'Acompanhamento deve parar depois do prazo global.');
            assert.strictEqual(await env.page.locator('#vQtd').textContent(), '2');
            assert.strictEqual(env.state.patches.length, 1, 'O custo simulado válido permite análise durante a fila da lista.');
        });
        console.log('SKU: dados salvos, fila em segundo plano, cache, prazos e isolamento: OK');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
