'use strict';

// Offline integration regression for the optional Vendas layout entry point.
// Navigation/sidebar visibility rules run unchanged in a real document and iframe.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { chromium } = require('playwright');

const repo = path.resolve(__dirname, '..');
const read = relative => fs.readFileSync(path.join(repo, relative), 'utf8');
const artifactBase = path.resolve(process.env.JK_TEST_ARTIFACTS_DIR || path.join(os.tmpdir(), 'jk-vendas-entry-browser'));
fs.mkdirSync(artifactBase, { recursive: true });
const scratch = fs.mkdtempSync(path.join(artifactBase, 'run-'));
const reportPath = process.env.JK_VENDAS_ENTRY_REPORT ? path.resolve(process.env.JK_VENDAS_ENTRY_REPORT) : path.join(scratch, 'report.json');
const screenshotPath = process.env.JK_VENDAS_ENTRY_SCREENSHOT ? path.resolve(process.env.JK_VENDAS_ENTRY_SCREENSHOT) : null;
const report = { before_reproduction: null, checks: [], errors: [], passing: false };
const inline = source => `<script>${source.replace(/<\/script/gi, '<\\/script')}</script>`;

const auxiliaryIa = path.join(scratch, 'ia-sidebar.js');
fs.writeFileSync(auxiliaryIa, '// Unrelated lazy assistant asset; no navigation or visibility rules.\n');

const fixtureState = `
(() => {
    localStorage.clear();
    sessionStorage.clear();
    localStorage.setItem('user_data', JSON.stringify({client_id: 'entry-offline-fixture'}));
    localStorage.setItem('permissions', '{}');
    window.obterToken = () => '';
    window.tokenSessaoExpirado = () => false;
    window.redirecionarSessaoExpirada = () => { throw new Error('Unexpected fixture login redirect'); };
    window.JKTableColumns = {};
    window.__entryMockFetches = [];
    window.fetch = async input => {
        window.__entryMockFetches.push(String(input));
        return new Response(JSON.stringify({success: false, active: false, messages: []}), {
            status: 200, headers: {'Content-Type': 'application/json'},
        });
    };
    window.__jkVendasSyncMonitor = {
        subscribe: () => () => {}, start() {}, stop() {},
        refresh: async () => ({active: false}), getLastPayload: () => ({active: false}),
    };
    const listeners = new Set();
    const snapshot = {
        clientId: 'entry-offline-fixture', contextKey: 'entry-offline-fixture',
        rows: [], totals: null, summaryCards: [], loading: false, graphLoading: false,
        filters: {inicio: '2026-04-01', fim: '2026-10-15', loja: '__todas', unidade: '__todos', search: 'SKU anterior'},
        stores: [], units: [{value: '__todos', label: 'Todas as lojas virtuais'}],
        graph: {data: {}}, chartOptions: {periodo: '6m', tipo: 'linha', metrica: 'valor', intervalo: 'mes'},
        status: {text: '', automationText: ''},
    };
    window.JKVendasLegacyBridge = {
        getSnapshot: () => structuredClone(snapshot),
        subscribe(listener, options = {}) {
            listeners.add(listener);
            if (options.immediate !== false) listener(structuredClone(snapshot));
            return () => listeners.delete(listener);
        },
        setSearch(value) {
            snapshot.filters.search = String(value);
            document.getElementById('filtroTexto').value = String(value);
            listeners.forEach(listener => listener(structuredClone(snapshot)));
        },
        getChartInstances: () => [], resizeCharts() {},
    };
})();`;

function createFixture(name, html, extraCss) {
    // Keep the complete production DOM and replace resource loading with the real inline sources.
    let output = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
    const css = [read('static/vendas/styles.css'), extraCss].map(source => source.replace(/@import\s+url\([^)]*\)\s*;/g, '')).join('\n');
    output = output.replace('</head>', `<style>${css}</style></head>`);
    const sources = [
        'static/auth/navigation.js', 'static/auth/global-ui.js',
        'static/vendas/runtime.js', 'static/vendas/assistente.js', 'static/vendas/visual-novo.js',
    ];
    const assets = `<script src="${pathToFileURL(auxiliaryIa).href}"></script>`;
    output = output.replace('</body>', assets + inline(fixtureState) + sources.map(relative => inline(read(relative))).join('')
        + inline('window.__vendasEntryFixtureReady = true;') + '</body>');
    const moduleFile = path.join(scratch, `${name}.html`);
    const shellFile = path.join(scratch, `${name}-shell.html`);
    fs.writeFileSync(moduleFile, output);
    fs.writeFileSync(shellFile, `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Offline Electron tab fixture</title><style>html,body{margin:0;height:100%;}iframe{display:block;width:100%;height:100%;border:0;}</style></head><body><iframe id="moduleFrame" title="Vendas" src="${pathToFileURL(moduleFile).href}"></iframe></body></html>`);
    return { moduleFile, shellFile };
}

async function loadCase(context, fixture, embedded, width, collectErrors = true) {
    const page = await context.newPage();
    await page.setViewportSize({ width, height: 900 });
    if (collectErrors) page.on('pageerror', error => report.errors.push({ mode: embedded ? 'iframe' : 'standalone', width, message: String(error) }));
    await page.goto(pathToFileURL(embedded ? fixture.shellFile : fixture.moduleFile).href, { waitUntil: 'load' });
    const frame = embedded ? page.frames().find(candidate => candidate.parentFrame() === page.mainFrame()) : page.mainFrame();
    assert.ok(frame, 'The module must run in the expected real browser frame');
    await frame.waitForFunction(() => window.__vendasEntryFixtureReady && window.JKVendasNova);
    await frame.waitForFunction(() => document.body.classList.contains('jk-page-ready'));
    return { page, frame };
}

function check(name, actual, details) {
    assert.ok(actual, `${name}: ${JSON.stringify(details)}`);
    report.checks.push({ name, ...details });
}

async function reproduceBefore(context) {
    const reference = process.env.JK_VENDAS_ENTRY_BEFORE_REF;
    if (!reference) return;
    const historical = relative => execFileSync('git', ['show', `${reference}:${relative}`], { cwd: repo, encoding: 'utf8' });
    const fixture = createFixture('before', historical('static/vendas.html'), historical('static/vendas/visual-novo.css'));
    const standalone = await loadCase(context, fixture, false, 1206, false);
    const iframe = await loadCase(context, fixture, true, 1206, false);
    try {
        const standaloneVisible = await standalone.frame.locator('#btnVersaoNovaVendas').isVisible();
        const iframeVisible = await iframe.frame.locator('#btnVersaoNovaVendas').isVisible();
        const navDisplay = await iframe.frame.locator('.nav-actions').evaluate(element => getComputedStyle(element).display);
        await standalone.frame.locator('#btnSidebarObservacoesVendas').click();
        await standalone.frame.waitForFunction(() => document.body.classList.contains('sidebar-observacoes-aberto'));
        const sidebarVisible = await standalone.frame.locator('#btnVersaoNovaVendas').isVisible();
        report.before_reproduction = {
            reference, standalone_visible: standaloneVisible, iframe_visible: iframeVisible,
            iframe_is_real: await iframe.frame.evaluate(() => top !== self),
            nav_actions_display: navDisplay, standalone_sidebar_open_visible: sidebarVisible,
        };
        assert.equal(standaloneVisible, true, 'Published layout should expose the button standalone before opening the sidebar');
        assert.equal(iframeVisible, false, 'Published layout must reproduce the hidden Electron tab entry');
        assert.equal(sidebarVisible, false, 'Published layout must reproduce the sidebar hiding the shared back button class');
    } finally {
        await standalone.page.close();
        await iframe.page.close();
    }
}

async function verifyCase(context, fixture, embedded, width) {
    const details = { mode: embedded ? 'iframe' : 'standalone', width };
    const { page, frame } = await loadCase(context, fixture, embedded, width);
    const entry = frame.locator('#btnVersaoNovaVendas');
    try {
        check('frame mode is real', await frame.evaluate(expected => (top !== self) === expected, embedded), details);
        check('legacy layout is the default', await frame.evaluate(() => !JKVendasNova.isActive()), details);
        check('production shell rules are active', await frame.evaluate(expected => document.documentElement.classList.contains('jk-electron-tab-shell') === expected, embedded), details);
        check('entry is visible', await entry.isVisible(), details);
        check('entry is independent from shell/back navigation', await entry.evaluate(element => !element.closest('.nav-actions') && !element.classList.contains('back-main-btn')), details);
        check('entry label is correct', /Versão nova/.test(await entry.innerText()), details);
        check('entry is inside the viewport', await entry.evaluate(element => {
            const rect = element.getBoundingClientRect();
            return rect.left >= 0 && rect.right <= innerWidth && rect.top >= 0 && rect.bottom <= innerHeight;
        }), details);
        if (embedded) check('native iframe navigation remains hidden', await frame.locator('.nav-actions').evaluate(element => getComputedStyle(element).display === 'none'), details);
        if (embedded && width === 1206 && screenshotPath) {
            fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
            await frame.locator('.header').screenshot({ path: screenshotPath });
        }

        const sidebar = frame.locator('#btnSidebarObservacoesVendas');
        await sidebar.click();
        await frame.waitForFunction(() => document.body.classList.contains('sidebar-observacoes-aberto'));
        check('real sidebar hides the original back buttons', await frame.evaluate(() => Array.from(document.querySelectorAll('.back-main-btn')).some(element => element.style.display === 'none')), details);
        check('entry stays visible with the sidebar open', await entry.isVisible(), details);

        // A normal Playwright click checks hit testing and clickability, without force or DOM click().
        await entry.click();
        await frame.waitForFunction(() => JKVendasNova.isActive());
        check('new layout opens through the visible entry', await frame.locator('#vendasNova').isVisible(), details);
        check('expanded accessibility state is true', await entry.getAttribute('aria-expanded') === 'true', details);
        check('legacy container is hidden only while the new layout is open', await frame.locator('body > .container').evaluate(element => element.hidden), details);
        await frame.locator('#vnBack').click();
        await frame.waitForFunction(() => !JKVendasNova.isActive());
        check('legacy entry returns while the sidebar is still open', await entry.isVisible(), details);
        check('expanded accessibility state resets', await entry.getAttribute('aria-expanded') === 'false', details);
        check('shared search is restored', await frame.locator('#filtroTexto').inputValue() === 'SKU anterior', details);

        await sidebar.click();
        await frame.waitForFunction(() => !document.body.classList.contains('sidebar-observacoes-aberto'));
        check('entry stays visible after closing the sidebar', await entry.isVisible(), details);
        await entry.click();
        await frame.waitForFunction(() => JKVendasNova.isActive());
        await frame.locator('#vnBack').click();
        await frame.waitForFunction(() => !JKVendasNova.isActive());
        check('entry remains clickable after a second round trip', await entry.isVisible(), details);
    } finally {
        await page.close();
    }
}

(async () => {
    let context;
    try {
        const canonical = read('static/vendas.html');
        assert.equal(canonical, read('vendas.html'), 'Sales HTML mirrors must stay identical');
        const fixture = createFixture('current', canonical, read('static/vendas/visual-novo.css'));
        context = await chromium.launchPersistentContext(path.join(scratch, 'chrome-profile'), {
            channel: process.env.JK_TEST_BROWSER_CHANNEL || 'chrome', headless: true,
            viewport: {width: 1366, height: 900}, args: ['--disable-gpu', '--no-first-run'],
        });
        await context.route(/^https?:/, route => {
            report.errors.push({ message: 'Unexpected external browser request in an offline fixture' });
            return route.abort();
        });
        await reproduceBefore(context);
        for (const width of [1206, 1366, 390]) {
            await verifyCase(context, fixture, false, width);
            await verifyCase(context, fixture, true, width);
        }
        assert.equal(report.errors.length, 0, 'The entry fixture must have no page errors or external browser requests');
        report.passing = true;
        console.log(`vendas nova entry browser: ok (${report.checks.length} checks; standalone/iframe; 1206/1366/390 px)`);
    } catch (error) {
        report.errors.push({ message: String(error.stack || error) });
        console.error(error);
        process.exitCode = 1;
    } finally {
        if (context) await context.close();
        fs.mkdirSync(path.dirname(reportPath), { recursive: true });
        fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
    }
})();
