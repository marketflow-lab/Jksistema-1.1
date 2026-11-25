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

function harness({ directory = profileDir, failFirstLaunch = false } = {}) {
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
                assert(!children.some(child => child.exitCode === null && child.signalCode === null),
                    'an active manual Chrome exclusively owns its profile');
                events.push(options.headless ? 'headless-open' : 'diagnostic-open');
                const context = new EventEmitter();
                contexts.push(context);
                context.serviceWorkers = () => [{ url: () => 'chrome-extension://' + extensionId + '/background.js' }];
                const pages = [];
                context.pages = () => [...pages];
                context.newPage = async () => {
                    const page = new EventEmitter();
                    let closed = false;
                    page.url = () => 'about:blank';
                    page.isClosed = () => closed;
                    page.close = async () => { closed = true; page.emit('close'); };
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
    const page = contexts[0].pages()[0];
    const frame = {};
    const product = 'https://produto.mercadolivre.com.br/MLB-123456789-peca-_JM';
    let href = home, readCount = 0, deliver;
    const navigations = [];
    page.url = () => href;
    page.mainFrame = () => frame;
    page.goto = async (url, options) => {
        href = url;
        page.emit('framenavigated', frame);
        navigations.push(options.waitUntil);
    };
    page.evaluate = async code => {
        assert(code.includes('window !== window.top') && code.includes(JSON.stringify(href)) && code.includes('jk-market-auth:'));
        readCount++;
        return new Promise(resolve => { deliver = resolve; });
    };
    await adapter.navigate(home);
    await adapter.navigate(product, undefined, { waitForAvantPanel: true });
    assert.deepEqual(navigations, ['load', 'domcontentloaded']);

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
    href = 'https://www.mercadolivre.com.br/account-verification/';
    page.emit('framenavigated', frame);
    await assert.rejects(login, error => error.waitingLogin && error.code === 'ml_verification_required');
    href = product;
    page.emit('framenavigated', frame);
    const blocked = adapter.readLoadingPanel('1');
    page.emit('response', { request: () => ({ isNavigationRequest: () => true, frame: () => frame }),
        url: () => href, status: () => 429, headers: () => ({ 'retry-after': '60' }) });
    await assert.rejects(blocked, error => error.code === 'marketplace_http_429' && Number.isSafeInteger(error.retryAt));

    await adapter.navigate(product, undefined, { waitForAvantPanel: true });
    const aborted = new AbortController();
    const cancelled = adapter.readLoadingPanel('1', aborted.signal);
    aborted.abort();
    await assert.rejects(cancelled, error => error.name === 'AbortError');
    assert(page.isClosed());
    await adapter.prepare();
    const replacement = contexts[0].pages().at(-1);
    replacement.url = () => product;
    replacement.evaluate = () => new Promise(() => {});
    const closed = adapter.readLoadingPanel('1');
    await replacement.close();
    await assert.rejects(closed, error => error.code === 'research_browser_closed');
    await session.close();
}

async function run() {
    await testPanelNavigationAndReads();
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

run().catch(error => { console.error(error); process.exitCode = 1; });
