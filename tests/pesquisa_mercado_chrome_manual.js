'use strict';

const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const path = require('node:path');
const { createChromeResearchSession } = require('../electron_app/main/modules/pesquisa-mercado-chrome');
const { launchManualChrome } = require('../electron_app/main/modules/pesquisa-mercado-chrome-manual');
const { allowedUrl } = require('../electron_app/main/modules/pesquisa-mercado-runner');

const profileDir = path.resolve(__dirname, 'synthetic-chrome-profile-no-disk-access');
const home = 'https://www.mercadolivre.com.br/';
const extensionId = 'jdefnfmbnchmnjkcknaadaddgjbgephh';
const store = 'https://chromewebstore.google.com/detail/avantpro-ml/' + extensionId;
const manualOpen = error => error?.code === 'research_chrome_manual_open';

function mockPage(initialUrl = 'about:blank') {
    const page = new EventEmitter(), frame = {};
    let href = initialUrl, closed = false;
    page.url = () => href;
    page.mainFrame = () => frame;
    page.commit = url => { href = url; page.emit('framenavigated', frame); };
    page.navigations = [];
    page.goto = async (url, options) => { page.navigations.push({ url, waitUntil: options.waitUntil }); page.commit(url); };
    page.focusCount = 0;
    page.bringToFront = async () => { page.focusCount++; };
    page.isClosed = () => closed;
    page.close = async () => { if (!closed) { closed = true; page.emit('close'); } };
    return page;
}

function harness({ directory = profileDir, failFirstLaunch = false, initialUrls = [] } = {}) {
    const events = [], children = [], launches = [], contexts = [];
    const session = createChromeResearchSession({
        profileDir: directory, allowedUrl,
        async launchManualChrome({ profileDir: target, url }) {
            assert.equal(target, directory);
            events.push('manual-open');
            if (failFirstLaunch && !launches.length) {
                launches.push(url);
                throw new Error('simulated launch failure');
            }
            launches.push(url);
            const child = Object.assign(new EventEmitter(), { pid: 1000 + children.length, exitCode: null, signalCode: null });
            child.kill = () => assert.fail('closing the JK session must not terminate a manually owned Chrome');
            child.finish = () => { child.exitCode = 0; child.emit('exit', 0, null); };
            children.push(child);
            return child;
        },
        getChromium: () => ({
            async launchPersistentContext(target, options) {
                assert.equal(target, directory, 'the manual and research browsers must use the same dedicated profile');
                assert(options.ignoreDefaultArgs.includes('about:blank'), 'the managed launch must not add a blank beside restored tabs');
                assert(!children.some(child => child.exitCode === null && child.signalCode === null),
                    'an active manual Chrome exclusively owns its profile');
                events.push(options.headless ? 'headless-open' : 'diagnostic-open');
                const context = new EventEmitter();
                contexts.push(context);
                context.serviceWorkers = () => [{ url: () => 'chrome-extension://' + extensionId + '/background.js' }];
                const pages = initialUrls.map(mockPage);
                context.pages = () => pages.filter(page => !page.isClosed());
                context.newPage = async () => {
                    const page = mockPage();
                    pages.push(page);
                    return page;
                };
                context.close = async () => {
                    events.push('managed-close');
                    await Promise.all(pages.map(page => page.close()));
                    context.emit('close');
                };
                return context;
            }
        })
    });
    return { session, events, children, launches, contexts };
}

async function testPanelNavigationAndReads() {
    const { session, contexts } = harness();
    const adapter = session.createPage(); // The primary tab also takes product work.
    await adapter.prepare();
    assert.equal(contexts[0].pages().length, 0, 'preparing a primary adapter does not need a blank tab');
    await adapter.navigate(home);
    const page = contexts[0].pages()[0];
    const frame = page.mainFrame();
    const product = 'https://produto.mercadolivre.com.br/MLB-123456789-peca-_JM';
    let readCount = 0, deliver;
    page.evaluate = async code => {
        assert(code.includes('window !== window.top') && code.includes(JSON.stringify(page.url())) && code.includes('jk-market-auth:'));
        readCount++;
        return new Promise(resolve => { deliver = resolve; });
    };
    await adapter.navigate(product, undefined, { waitForAvantPanel: true });
    assert.deepEqual(page.navigations.map(entry => entry.waitUntil), ['load', 'domcontentloaded']);

    const originalTimer = global.setTimeout;
    global.setTimeout = (fn, ms, ...args) => originalTimer(fn, ms === 12000 ? 1 : ms, ...args);
    try {
        await assert.rejects(adapter.executeJavaScript('1'), error => error.code === 'category_load_timeout');
        await assert.rejects(adapter.readLoadingPanel('1'), /leitura do painel/);
        deliver(null); // The renderer finishes the ordinary read after the caller's deadline.
        await new Promise(resolve => setImmediate(resolve));
        const pending = adapter.readLoadingPanel('1');
        const firstCount = readCount;
        await assert.rejects(adapter.readLoadingPanel('1'), /leitura do painel/);
        await assert.rejects(adapter.executeJavaScript('2'), /leitura do painel/);
        assert.equal(readCount, firstCount);
        page.emit('framenavigated', {}); // A child frame can finish while the panel loads.
        await new Promise(resolve => originalTimer(resolve, 10));
        deliver({ vendas: 12 });
        assert.deepEqual(await pending, { vendas: 12 });
    } finally { global.setTimeout = originalTimer; }

    const login = adapter.readLoadingPanel('1');
    page.commit('https://www.mercadolivre.com.br/account-verification/');
    await assert.rejects(login, error => error.waitingLogin && error.code === 'ml_verification_required');
    page.commit(product);
    const blocked = adapter.readLoadingPanel('1');
    page.emit('response', { request: () => ({ isNavigationRequest: () => true, frame: () => frame }),
        url: page.url, status: () => 429, headers: () => ({ 'retry-after': '60' }) });
    await assert.rejects(blocked, error => error.code === 'marketplace_http_429' && Number.isSafeInteger(error.retryAt));

    await adapter.navigate(product, undefined, { waitForAvantPanel: true });
    const aborted = new AbortController();
    const cancelled = adapter.readLoadingPanel('1', aborted.signal);
    aborted.abort();
    await assert.rejects(cancelled, error => error.name === 'AbortError');
    assert(page.isClosed());
    await adapter.prepare();
    await adapter.navigate(product);
    const replacement = contexts[0].pages().at(-1);
    replacement.evaluate = () => new Promise(() => {});
    const closed = adapter.readLoadingPanel('1');
    await replacement.close();
    await assert.rejects(closed, error => error.code === 'research_browser_closed');
    await session.close();
}

async function run() {
    await testPanelNavigationAndReads();
    await testLazyPagesAndCancellation();
    await testOwnedPreview();
    await testFailedPreviews();
    {
        const calls = [], started = new EventEmitter();
        let detached = false;
        started.unref = () => { detached = true; };
        function spawnProcess(executable, args, options) {
            calls.push({ executable, args, options });
            if (executable === 'installed-chrome') {
                queueMicrotask(() => started.emit('spawn'));
                return started;
            }
            const missing = new EventEmitter();
            queueMicrotask(() => missing.emit('error', Object.assign(new Error('private path from operating system'), { code: 'ENOENT' })));
            return missing;
        }
        const options = { profileDir, url: store, spawnProcess, executableCandidates: ['missing-chrome', 'installed-chrome'] };
        assert.equal(await launchManualChrome(options), started);
        assert(detached);
        assert.deepEqual(calls.map(call => call.executable), options.executableCandidates);
        for (const call of calls) {
            assert.deepEqual(call.args, ['--user-data-dir=' + profileDir, '--new-window', store]);
            assert.equal(call.options.shell, false);
        }
        await assert.rejects(() => launchManualChrome({ ...options, executableCandidates: ['missing-chrome'] }), {
            code: 'research_chrome_unavailable', message: 'Não foi possível abrir o Google Chrome da pesquisa.'
        });
    }
    {
        const { session, children, launches, events } = harness();
        await assert.rejects(async () => session.showManual('https://chromewebstore.google.com/detail/unrelated/extension'));
        await assert.rejects(async () => session.showManual('https://www.mercadolivre.com.br.untrusted.invalid/'));
        assert.deepEqual(events, [], 'invalid targets must not open either browser');
        await session.showManual(store);
        assert.deepEqual(events, ['manual-open'], 'the native setup opens without requesting an automation context');
        await Promise.allSettled([home, store].map(async url => session.showManual(url)));
        assert.deepEqual(launches, [store], 'repeated setup cannot navigate or relaunch the manual window');
        await assert.rejects(() => session.prepare(), manualOpen);
        await assert.rejects(() => session.createPage({ detail: true }).prepare(), manualOpen);
        await session.close();
        assert(children[0].exitCode === null && children[0].signalCode === null);
        const recreated = harness(), other = harness({ directory: profileDir + '-second' });
        await recreated.session.showManual(home);
        assert.deepEqual(recreated.launches, [], 'a new session for the same profile must reuse the user-owned Chrome');
        await assert.rejects(() => recreated.session.prepare(), manualOpen);
        await other.session.prepare();
        await other.session.close();
        assert.deepEqual(other.events, ['headless-open', 'managed-close'], 'other dedicated profiles can still collect');
        children[0].finish();
        await recreated.session.prepare();
        await recreated.session.close();
        assert.deepEqual(recreated.events, ['headless-open', 'managed-close']);
        assert.deepEqual(events, ['manual-open']);
    }
    {
        const { session, children, events, launches } = harness();
        await session.prepare();
        await session.showManual(home);
        assert.deepEqual(events, ['headless-open', 'managed-close', 'manual-open'],
            'the managed collector must release the profile before opening manual login');
        assert.deepEqual(launches, [home]);
        await assert.rejects(() => session.prepare(), manualOpen);
        children[0].finish();
        await session.prepare();
        await session.close();
        assert.deepEqual(events, ['headless-open', 'managed-close', 'manual-open', 'headless-open', 'managed-close']);
    }
    {
        const { session, events, children, contexts } = harness();
        const page = session.createPage();
        await session.showManual(home);
        await assert.rejects(() => page.prepare({ collectVisible: true }), manualOpen);
        children[0].finish();
        await page.prepare({ collectVisible: true });
        await contexts[0].close(); // User closes the managed window.
        assert.deepEqual(events, ['manual-open', 'diagnostic-open', 'managed-close']);
        await page.prepare({ collectVisible: true });
        await page.prepare({ collectVisible: false }); // Another job defaults to headless.
        await session.close();
        assert.deepEqual(events.slice(3), ['diagnostic-open', 'managed-close', 'headless-open', 'managed-close']);
    }
    {
        const { session, events } = harness({ failFirstLaunch: true });
        await assert.rejects(() => session.showManual(home));
        await session.prepare();
        await session.close();
        assert.deepEqual(events, ['manual-open', 'headless-open', 'managed-close'],
            'a failed native launch must not leave a phantom profile lock');
    }
    console.log('Pesquisa de Mercado Chrome manual: isolated profile, user-owned window, wait and resume OK');
}

async function testLazyPagesAndCancellation() {
    const restoredUrls = [home, 'about:blank'];
    const { session, contexts } = harness({ initialUrls: restoredUrls });
    const primary = session.createPage(), details = Array.from({ length: 7 }, () => session.createPage({ detail: true }));
    await primary.prepare({ collectVisible: true });
    const context = contexts[0], restored = context.pages();
    await Promise.all(details.map(detail => detail.prepare()));
    assert.deepEqual(context.pages(), restored, 'eight prepared workers leave restored tabs alone and allocate no tabs');
    await details[0].destroy();
    const create = context.newPage;
    let started, release;
    const creating = new Promise(resolve => { started = resolve; });
    const hold = new Promise(resolve => { release = resolve; });
    context.newPage = async () => { const page = await create(); started(page); await hold; return page; };
    const controller = new AbortController();
    const failed = assert.rejects(details[1].navigate(home, controller.signal), error => error.name === 'AbortError');
    const interrupted = await creating;
    controller.abort(); release(); await failed;
    assert(interrupted.isClosed());
    assert.deepEqual(interrupted.navigations, [], 'an aborted worker must never navigate the newly allocated page');
    context.newPage = create;
    await details[2].navigate(home);
    assert.equal(context.pages().length, restored.length + 1);
    await Promise.all(details.map(detail => detail.finish('cancelled')));
    assert.deepEqual(context.pages(), restored);
    assert.deepEqual(restored.map(page => page.url()), restoredUrls);
    assert(restored.every(page => page.navigations.length === 0));
    await session.close();

    const single = harness({ initialUrls: ['chrome://new-tab-page/'] });
    const main = single.session.createPage();
    await main.prepare();
    const initial = single.contexts[0].pages()[0];
    await main.navigate(home);
    assert.deepEqual(single.contexts[0].pages(), [initial], 'the lone startup tab is available for the requested URL');
    assert.equal(initial.url(), home);
    await single.session.close();
}

async function testOwnedPreview() {
    const requested = 'https://lista.mercadolivre.com.br/pecas/', finalUrl = requested + '*_NoIndex_True';
    const manualUrl = 'https://www.mercadolivre.com.br/account-verification/';
    const visible = { collectVisible: true };
    const { session, contexts } = harness({ initialUrls: [home, 'about:blank'] });
    const main = session.createPage();
    await main.prepare(visible);
    const context = contexts[0], restored = context.pages(), create = context.newPage;
    context.newPage = async () => {
        const page = await create(), goto = page.goto;
        page.goto = async (url, options) => { await goto(url, options); if (url === requested) page.commit(finalUrl); };
        return page;
    };
    await session.show(requested, visible);
    const preview = context.pages().at(-1);
    await session.show(requested, visible);
    await session.show(finalUrl, visible);
    assert.equal(context.pages().length, restored.length + 1);
    assert.deepEqual(preview.navigations.map(entry => entry.url), [requested], 'the displayed redirect destination remains in place');
    assert.equal(preview.focusCount, 3);
    preview.commit(manualUrl);
    await session.show(requested, visible);
    const nextPreview = context.pages().at(-1);
    assert.notEqual(nextPreview, preview);
    const detail = session.createPage({ detail: true });
    await detail.prepare(); await detail.navigate(home);
    assert.equal(nextPreview.url(), finalUrl, 'a detail worker cannot adopt the preview');
    await main.navigate(home);
    assert.deepEqual(nextPreview.navigations.map(entry => entry.url), [requested, home], 'the primary worker adopts the current preview');
    await main.finish('completed'); await detail.finish('completed');
    assert.deepEqual(context.pages(), [...restored, preview]);
    assert.equal(preview.url(), manualUrl, 'the tab the user navigated remains untouched');
    await session.close();
}

async function testFailedPreviews() {
    const target = 'https://lista.mercadolivre.com.br/pecas/';
    const { session, contexts } = harness({ initialUrls: [home, 'about:blank'] });
    await session.createPage().prepare({ collectVisible: true });
    const context = contexts[0], restored = context.pages(), create = context.newPage;
    for (const committed of [false, true]) {
        let opened;
        context.newPage = async () => {
            opened = await create();
            opened.goto = async () => {
                if (committed) {
                    opened.commit(target);
                    opened.emit('response', { url: () => target, status: () => 403 });
                }
                throw new Error('net::ERR_HTTP_RESPONSE_CODE_FAILURE');
            };
            return opened;
        };
        await assert.rejects(session.show(target, { collectVisible: true }));
        assert.equal(opened.isClosed(), !committed, 'close only a failed blank preview; preserve the committed document');
        assert.deepEqual(context.pages(), committed ? [...restored, opened] : restored);
    }
    await session.close();
}

run().catch(error => { console.error(error); process.exitCode = 1; });
