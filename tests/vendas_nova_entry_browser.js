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
const report = { before_reproduction: null, checks: [], screenshots: [], errors: [], passing: false };
const inline = source => `<script>${source.replace(/<\/script/gi, '<\\/script')}</script>`;
const periodSource = read('static/vendas/periodo-cache.js');
const syncPresentation = `function formatDate(value) {
    return String(value || '').replace(/^(\\d{4})-(\\d{2})-(\\d{2})$/, '$3/$2/$1');
}\n` + periodSource.slice(periodSource.indexOf('function formatarMomentoSyncVendas('), periodSource.indexOf('let progressMonitorSubscribed'));

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
    window.__entrySetSnapshot = patch => {
        Object.assign(snapshot, patch);
        listeners.forEach(listener => listener(structuredClone(snapshot)));
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
    output = output.replace('</body>', assets + inline(fixtureState) + sources.map(relative => inline(read(relative))
        + (relative === 'static/vendas/runtime.js' ? inline(syncPresentation) : '')).join('')
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

async function verifyPhotoFeedback(context, fixture) {
    const details = { mode: 'iframe', width: 1206, feature: 'async photo feedback' };
    const { page, frame } = await loadCase(context, fixture, true, 1206);
    try {
        // The photo helper's scope, URL and cleanup contract is tested separately.
        // Drive its async completion here without image/network dependencies in the entry fixture.
        await frame.evaluate(() => {
            let epoch = 0;
            const requests = [];
            const clear = root => root.querySelectorAll('img[data-vendas-foto]').forEach(img => {
                img.removeAttribute('data-vendas-foto-ambiguo');
                img.removeAttribute('data-jk-auth-src');
                img.setAttribute('data-vendas-foto-estado', 'idle');
            });
            const release = root => { epoch++; clear(root); };
            window.__entryPhotoRequests = requests;
            window.JKVendasNovaFotos = {
                release,
                hydrate(root, snapshot) {
                    if (snapshot.loading) { release(root); return Promise.resolve(); }
                    return new Promise(resolve => requests.push({ root, key: snapshot.contextKey, epoch, resolve }));
                },
            };
            window.__entryFinishPhotos = (key, ambiguous) => {
                requests.filter(request => request.key === key).forEach(request => {
                    if (request.epoch === epoch) request.root.querySelectorAll('img[data-vendas-foto]').forEach(img => {
                        if (ambiguous) img.setAttribute('data-vendas-foto-ambiguo', 'true');
                        else img.removeAttribute('data-vendas-foto-ambiguo');
                        img.setAttribute('data-vendas-foto-estado', 'missing');
                    });
                    request.resolve();
                });
            };
            window.__entrySetSnapshot({ contextKey: 'photo-absence', rows: [{sku: '001', produto: 'Produto do teste', valor: 1}], loading: false });
            JKVendasNova.show();
        });
        const waitForPhoto = key => frame.waitForFunction(value => __entryPhotoRequests.some(request => request.key === value), key);
        const hint = frame.locator('#vnPhotoHint');
        await waitForPhoto('photo-absence');
        check('pending metadata does not ask the user to select a store', await hint.isHidden(), details);
        await frame.evaluate(() => __entryFinishPhotos('photo-absence', false));
        check('missing photo without explicit ambiguity keeps the hint hidden', await hint.isHidden(), details);

        await frame.evaluate(() => __entrySetSnapshot({ contextKey: 'photo-ambiguous' }));
        await waitForPhoto('photo-ambiguous');
        await frame.evaluate(() => __entryFinishPhotos('photo-ambiguous', true));
        await frame.waitForFunction(() => !document.getElementById('vnPhotoHint').hidden);
        check('explicit ambiguity displays the hint after metadata finishes without a load event', await hint.isVisible(), details);
        check('loading hides the previous hint immediately', await frame.evaluate(() => {
            __entrySetSnapshot({ loading: true });
            return document.getElementById('vnPhotoHint').hidden;
        }), details);

        await frame.evaluate(() => __entrySetSnapshot({ contextKey: 'photo-stale', loading: false }));
        await waitForPhoto('photo-stale');
        await frame.evaluate(() => __entrySetSnapshot({ contextKey: 'photo-current' }));
        await waitForPhoto('photo-current');
        await frame.evaluate(() => { __entryFinishPhotos('photo-current', false); __entryFinishPhotos('photo-stale', true); });
        check('late metadata from another context cannot restore the hint', await hint.isHidden(), details);
        check('late metadata cannot mark the featured photo ambiguous', await frame.locator('.vn-featured-photo img').getAttribute('data-vendas-foto-ambiguo') === null, details);

        await frame.evaluate(() => __entrySetSnapshot({ contextKey: 'photo-hidden' }));
        await waitForPhoto('photo-hidden');
        await frame.locator('#vnBack').click();
        await frame.evaluate(() => __entryFinishPhotos('photo-hidden', true));
        check('completion after leaving the layout keeps the hint hidden', await frame.evaluate(() => !JKVendasNova.isActive() && document.getElementById('vnPhotoHint').hidden), details);
    } finally {
        await page.close();
    }
}

async function verifySyncStatus(context, fixture, width, modern) {
    const details = { mode: 'iframe', width, feature: 'sync status', layout: modern ? 'new' : 'legacy' };
    const { page, frame } = await loadCase(context, fixture, true, width);
    const panel = frame.locator(modern ? '#vnPersistentStatus' : '#syncPersistenteStatus');
    const progress = frame.locator(modern ? '#vnStatus' : '#status');
    try {
        await frame.evaluate(() => {
            const store = (loja, status, extra = {}) => ({
                loja, store_id: loja, status, habilitado: status !== 'paused',
                data_inicio: '2026-10-14', data_fim: '2026-10-20',
                dias_concluidos: status === 'complete' ? 7 : 1, dias_total: 7,
                proxima_execucao: '2026-10-20T12:00:00Z', ultima_conferencia: '2026-10-20T10:00:00Z',
                ...extra,
            });
            window.__entrySyncPayload = {
                active: true, progress: { percentual: 43, etapa: 'Coleta', mensagem: 'Etapa técnica exclusiva do fixture.' },
                logs: ['LOG_TECNICO_RECOLHIDO <coleta>'],
                automacao: {
                    pendentes: 9, habilitadas: 5,
                    lojas: [
                        store('RCL', 'running', { checkpoint: { ultimo_tipo: 'pedido', ultimo_id: 'DOC-123', documentos_salvos: 26, atualizado_em: '2026-10-20T10:16:46Z' } }),
                        store('JK Peças', 'running', { ultimo_erro: 'Falha na tentativa anterior <integracao>.' }),
                        store('Carlos José', 'queued'),
                        store('Aline 2', 'complete'),
                        store('Deckas', 'retry', { ultimo_erro: 'Sessão expirada. Faça login novamente para continuar as pendências.' }),
                        store('Randy', 'paused'),
                    ],
                },
            };
            window.__entryPublishSync = nativeMessage => {
                const payload = window.__entrySyncPayload;
                const native = document.getElementById('status');
                renderAutomacaoSyncVendas(payload);
                if (nativeMessage) {
                    native.className = nativeMessage.className;
                    native.textContent = nativeMessage.text;
                } else {
                    renderProgresso(payload.progress, payload.logs, native, payload.automacao.lojas);
                }
                __entrySetSnapshot({
                    sync: structuredClone(payload),
                    status: { text: native.textContent, className: native.className, automationText: 'FALLBACK_TEXTO_NAO_EXIBIR' },
                });
            };
            __entryPublishSync();
        });
        if (modern) {
            await frame.locator('#btnVersaoNovaVendas').click();
            await frame.waitForFunction(() => JKVendasNova.isActive());
        }
        await panel.locator('.vendas-sync-counts').waitFor({ state: 'visible' });
        const visible = await panel.innerText();
        const bar = progress.locator('..');
        const disclosure = panel.locator('details[data-sync-details="sync"]');
        const summary = disclosure.locator('summary');
        const attention = summary.locator('.vendas-sync-attention');
        check('progress and persistent state share the compact wrapper', await bar.evaluate(element =>
            element.classList.contains('vendas-sync-bar') && element.contains(document.querySelector('#' + (element.classList.contains('vn-status') ? 'vnPersistentStatus' : 'syncPersistenteStatus')))), details);
        check('store counts distinguish activity, completion and pause', [
            '2 sincronizando', '1 aguardando', '1 atualizada', '1 pausada',
        ].every(label => visible.includes(label)), details);
        check('job count is not presented as store count', !visible.includes('9 lojas'), details);
        check('technical content and full alerts start collapsed', !/DOC-123|LOG_TECNICO_RECOLHIDO|Etapa técnica exclusiva|9 tarefas|FALLBACK_TEXTO_NAO_EXIBIR|Sessão expirada|Falha na tentativa anterior/.test(visible), details);
        check('one disclosure starts closed and reveals all details', await panel.locator('details').evaluateAll(nodes => nodes.length === 1 && !nodes[0].open), details);
        check('alert badge stays visible and counts running stores with an earlier error', await attention.isVisible()
            && /\b2 alertas$/.test((await attention.innerText()).trim()), details);
        check('the disclosure has a visible details label', await summary.locator('.vendas-sync-toggle').innerText() === 'Detalhes', details);
        check('the overall panel stays neutral', await panel.evaluate(element => !element.classList.contains('error')), details);
        check('progress stays concise while store names remain in the disclosure', /Sincronizando · 43%/.test(await progress.innerText())
            && !/RCL|JK Peças|em andamento|LOG_TECNICO_RECOLHIDO|Etapa técnica exclusiva/.test(await progress.innerText()), details);
        check('progress retains its accessible percentage', await progress.locator('[role="progressbar"]').evaluate(element =>
            element.getAttribute('aria-valuenow') === '43' && element.getAttribute('aria-valuemin') === '0'
            && element.getAttribute('aria-valuemax') === '100' && Boolean(element.getAttribute('aria-label'))), details);
        const collapsedHeight = await bar.evaluate(element => element.getBoundingClientRect().height);
        check('collapsed status uses at most 60 px on desktop and 150 px on mobile', collapsedHeight > 0
            && collapsedHeight <= (width <= 390 ? 150 : 60), { ...details, height: collapsedHeight });

        await progress.scrollIntoViewIfNeeded();
        await panel.scrollIntoViewIfNeeded();
        const boxes = [await bar.boundingBox()];
        const x = Math.max(0, Math.min(...boxes.map(box => box.x)));
        const y = Math.max(0, Math.min(...boxes.map(box => box.y)));
        const right = Math.min(width, Math.max(...boxes.map(box => box.x + box.width)));
        const bottom = Math.min(900, Math.max(...boxes.map(box => box.y + box.height)));
        const imagePath = path.join(scratch, `sync-${modern ? 'new' : 'legacy'}-${width}.png`);
        await page.screenshot({ path: imagePath, clip: { x, y, width: right - x, height: bottom - y } });
        report.screenshots.push({ ...details, path: path.basename(imagePath) });
        check('collapsed panels do not overflow horizontally', await panel.evaluate(element => {
            const rect = element.getBoundingClientRect();
            return rect.left >= -1 && rect.right <= innerWidth + 1 && element.scrollWidth <= element.clientWidth + 1;
        }), details);

        await summary.click();
        check('one click reveals every store once', await disclosure.locator('.vendas-sync-store').count() === 6
            && await disclosure.locator('.vendas-sync-store').first().isVisible(), details);
        check('the same disclosure reveals checkpoint, stage and escaped log', /Pedido #DOC-123/.test(await disclosure.innerText())
            && /Etapa técnica exclusiva/.test(await disclosure.innerText())
            && /LOG_TECNICO_RECOLHIDO <coleta>/.test(await disclosure.innerText())
            && await disclosure.locator('coleta').count() === 0, details);
        check('expanded alerts preserve login guidance and escape markup', /Sessão expirada\. Faça login novamente/.test(await disclosure.innerText())
            && /Falha na tentativa anterior <integracao>\./.test(await disclosure.innerText())
            && await disclosure.locator('integracao').count() === 0
            && await disclosure.locator('.vendas-sync-alert').count() === 2, details);
        check('expanded panels do not overflow horizontally', await panel.evaluate(element => element.scrollWidth <= element.clientWidth + 1), details);
        if (width === 1206) {
            const expandedPath = path.join(scratch, `sync-${modern ? 'new' : 'legacy'}-${width}-expanded.png`);
            await bar.screenshot({ path: expandedPath });
            report.screenshots.push({ ...details, expanded: true, path: path.basename(expandedPath) });
        }
        await summary.focus();
        await frame.evaluate(() => {
            __entrySyncPayload.progress.percentual = 44;
            __entrySyncPayload.logs.push('Novo registro após atualização.');
            __entryPublishSync();
        });
        await frame.waitForFunction(selector => document.querySelector(selector)?.getAttribute('aria-valuenow') === '44',
            `${modern ? '#vnStatus' : '#status'} [role="progressbar"]`);
        check('polling preserves the open disclosure and focused summary', await panel.evaluate(element => {
            const disclosure = element.querySelector('details[data-sync-details="sync"]');
            return disclosure.open && document.activeElement === disclosure.querySelector('summary');
        }), details);
        await summary.press('Enter');
        check('keyboard activation closes the disclosure', await disclosure.evaluate(element => !element.open)
            && await disclosure.locator('.vendas-sync-store').first().isHidden(), details);
        await frame.evaluate(() => { __entrySyncPayload.logs.push('Mais um registro.'); __entryPublishSync(); });
        await frame.waitForFunction(selector => document.querySelector(selector)?.textContent.includes('Mais um registro.'), modern ? '#vnPersistentStatus' : '#syncPersistenteStatus');
        check('polling preserves the closed disclosure and focused summary', await disclosure.evaluate(element =>
            !element.open && element.querySelector('summary') === document.activeElement), details);
        check('the alert badge remains visible after closing and polling', await attention.isVisible()
            && !/Sessão expirada|Mais um registro/.test(await panel.innerText()), details);
        await summary.press('Space');
        check('keyboard activation can reopen all details', await disclosure.evaluate(element => element.open)
            && await disclosure.locator('.vendas-sync-store').first().isVisible(), details);

        if (modern) {
            await frame.evaluate(() => __entryPublishSync({ className: 'status-bar error', text: 'Erro nativo deve permanecer visível.' }));
            await progress.getByText('Erro nativo deve permanecer visível.', { exact: true }).waitFor();
            check('native errors override stale active progress locally', await progress.evaluate(element =>
                element.classList.contains('error') && !element.querySelector('.vendas-sync-progress')), details);
            check('a native error does not recolor the persistent panel', await panel.evaluate(element => !element.classList.contains('error')), details);
            await frame.evaluate(() => __entryPublishSync({ className: 'status-bar loading', text: 'Pausando sincronização...' }));
            await progress.getByText('Pausando sincronização...', { exact: true }).waitFor();
            check('native action messages override stale active progress', await progress.evaluate(element =>
                !element.classList.contains('error') && !element.querySelector('.vendas-sync-progress')), details);
        }
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
        await verifyPhotoFeedback(context, fixture);
        for (const width of [1206, 390]) {
            await verifySyncStatus(context, fixture, width, false);
            await verifySyncStatus(context, fixture, width, true);
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
