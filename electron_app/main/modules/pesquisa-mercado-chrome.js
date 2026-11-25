'use strict';

const path = require('node:path');
const { mercadoLivreAuthScript, mercadoLivreRouteAuthReason } = require('../../../static/pesquisa-mercado/page-scripts');
const { pageScript } = require('./pesquisa-mercado-avant-auth');
const { isManualChromeRunning } = require('./pesquisa-mercado-chrome-manual');

const EXTENSION_ID = 'jdefnfmbnchmnjkcknaadaddgjbgephh';
const STORE = 'https://chromewebstore.google.com/detail/avantpro-ml/' + EXTENSION_ID;
const HOME = 'https://www.mercadolivre.com.br/';
const RELEASE_MEDIA = 'jk-pesquisa-mercado:release-recommendation-media';
const manualBrowsers = new Map();

function retryAfterMilliseconds(value) {
    if (typeof value !== 'string') return;
    let text = value.trim();
    if (/^\d+$/.test(text)) {
        const milliseconds = Number(text) * 1000;
        return Number.isSafeInteger(milliseconds) ? milliseconds : undefined;
    }
    const now = Date.now();
    // Normalize the two historical HTTP-date formats to IMF-fixdate, always in UTC.
    const legacy = /^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), (\d{2})-([A-Z][a-z]{2})-(\d{2}) (\d{2}:\d{2}:\d{2}) GMT$/.exec(text);
    if (legacy) {
        const [, weekday, day, month, shortYear, clock] = legacy;
        const horizon = new Date(now);
        horizon.setUTCFullYear(horizon.getUTCFullYear() + 50);
        let year = Math.floor(horizon.getUTCFullYear() / 100) * 100 + Number(shortYear);
        if (Date.parse(`${day} ${month} ${year} ${clock} GMT`) > horizon.getTime()) year -= 100;
        text = `${weekday.slice(0, 3)}, ${day} ${month} ${year} ${clock} GMT`;
    } else {
        const asctime = /^([A-Z][a-z]{2}) ([A-Z][a-z]{2}) ( [1-9]|\d{2}) (\d{2}:\d{2}:\d{2}) (\d{4})$/.exec(text);
        if (asctime) {
            const [, weekday, month, day, clock, year] = asctime;
            text = `${weekday}, ${day.trim().padStart(2, '0')} ${month} ${year} ${clock} GMT`;
        }
    }
    // Date.parse also accepts fractions and malformed dates. Require an actual HTTP date.
    if (text.length !== 29) return;
    const date = new Date(text);
    if (!Number.isFinite(date.getTime()) || date.toUTCString() !== text) return;
    return Math.max(0, date.getTime() - now);
}

function interrupted() {
    return Object.assign(new Error('Pesquisa interrompida.'), { name: 'AbortError' });
}

function browserClosed() {
    return Object.assign(new Error('O Chrome da pesquisa foi fechado. Retome para continuar.'), {
        code: 'research_browser_closed', waitingLogin: true
    });
}

function chromeFailure(error) {
    if (error?.waitingLogin || error?.name === 'AbortError' || ['research_browser_closed', 'research_chrome_unavailable', 'research_chrome_manual_open', 'category_load_timeout'].includes(error?.code)) return error;
    if (/target.*closed|browser.*closed|page crashed/i.test(String(error?.message))) return browserClosed();
    return Object.assign(new Error('Não foi possível acessar o Google Chrome da pesquisa.'), { code: 'research_chrome_unavailable' });
}

function loginRequired(reason) {
    const message = reason === 'avant_extension_missing' ? 'Instale o Avantpro ML no Chrome da pesquisa e retome.'
        : reason === 'ml_verification_required' ? 'Conclua a verificação do Mercado Livre no Chrome da pesquisa.'
        : 'Conclua o login no Chrome da pesquisa.';
    return Object.assign(new Error(message), { code: reason, waitingLogin: true });
}

function createChromeResearchSession({ profileDir, allowedUrl, getChromium, createMediaScript, launchManualChrome }) {
    if (!profileDir || !path.isAbsolute(profileDir) || typeof allowedUrl !== 'function') {
        throw new Error('Configuração do perfil de pesquisa inválida.');
    }
    let context = null, manual = false, collectVisible = false, manualPage = null;
    let previewUrl = '', epoch = 0, nextMediaToken = 0;
    let startPage = null, checkedContext = null, lostContext = false;
    const closing = new WeakSet();
    let transition = Promise.resolve();
    const serialize = action => {
        const work = transition.then(action);
        transition = work.catch(() => {});
        return work;
    };

    async function checkExtension(active) {
        const origin = 'chrome-extension://' + EXTENSION_ID + '/';
        if (active.serviceWorkers().some(worker => worker.url() === origin + 'background.js')) return;
        // An installed MV3 worker can be idle. Confirm its packaged manifest independently.
        const probe = await active.newPage();
        try {
            const response = await probe.goto(origin + 'manifest.json', { waitUntil: 'commit', timeout: 7000 });
            if (response && response.ok() && response.url() === origin + 'manifest.json') {
                const manifest = await response.json();
                if (manifest.manifest_version === 3 && manifest.version && manifest.background?.service_worker) return;
            }
        } catch (error) {
            if (active !== context || probe.isClosed()) throw closing.has(active) ? interrupted() : browserClosed();
        } finally { await probe.close().catch(() => {}); }
        throw loginRequired('avant_extension_missing');
    }

    async function closeContext() {
        const old = context;
        if (old) {
            closing.add(old);
            try { await old.close(); } catch (error) { throw chromeFailure(error); }
        }
        if (context === old) { context = null; manualPage = null; }
    }

    async function open(visible, forResearch = true) {
        if (isManualChromeRunning(manualBrowsers.get(profileDir))) {
            throw Object.assign(new Error('Feche a janela do Chrome dedicada ao login e clique em Retomar.'), {
                code: 'research_chrome_manual_open'
            });
        }
        if (lostContext) throw browserClosed();
        if (context && manual === visible) {
            if (forResearch && checkedContext !== context) {
                await checkExtension(context);
                checkedContext = context;
            }
            return context;
        }
        await closeContext();
        let active;
        try {
            const chromium = await (getChromium ? getChromium() : require('playwright-core').chromium);
            active = await chromium.launchPersistentContext(profileDir, {
                channel: 'chrome', headless: !visible, viewport: null, acceptDownloads: false,
                ignoreDefaultArgs: ['--disable-extensions']
            });
        } catch (error) {
            const busy = /processsingleton|profile.{0,30}(?:in use|em uso)|singletonlock/i.test(error.message);
            throw Object.assign(new Error(busy ? 'O perfil da pesquisa está aberto em outro processo do Chrome.'
                : 'Não foi possível iniciar o Google Chrome da pesquisa. Verifique se o Chrome está instalado.'),
            { code: 'research_chrome_unavailable' });
        }
        context = active;
        manual = visible;
        // A newly started Chrome usually supplies exactly one blank tab.
        const initial = active.pages();
        startPage = initial.length === 1 && initial[0].url() === 'about:blank' ? initial[0] : null;
        active.on('close', () => {
            if (active !== context) return;
            if (!closing.has(active)) { lostContext = true; collectVisible = false; }
            context = null; manualPage = null; startPage = null; checkedContext = null;
        });
        try {
            if (forResearch) { await checkExtension(active); checkedContext = active; }
            return active;
        } catch (error) {
            if (context === active) await closeContext();
            throw error;
        }
    }

    function prepare() {
        return serialize(async () => { lostContext = false; await open(collectVisible); }).catch(error => { throw chromeFailure(error); });
    }

    function takeStartPage() {
        const first = startPage;
        startPage = null;
        return first && !first.isClosed() && first.url() === 'about:blank' ? first : null;
    }

    function show(url = HOME, options = {}) {
        if (url !== STORE && !allowedUrl(url)) return Promise.reject(new Error('Endereço de configuração inválido.'));
        epoch++;
        return serialize(async () => {
            lostContext = false;
            const active = await open(true, false);
            const useForJob = options.collectVisible === true && url !== STORE;
            if (!useForJob && !collectVisible && manualPage && !manualPage.isClosed() && url !== STORE) {
                await manualPage.bringToFront(); return true;
            }
            collectVisible = useForJob;
            // Only tabs opened by this adapter can later be assigned to the collector.
            manualPage = takeStartPage() || await active.newPage();
            await manualPage.goto(url, { waitUntil: 'commit', timeout: 35000 });
            previewUrl = manualPage.url();
            await manualPage.bringToFront();
            return true;
        }).catch(error => { throw chromeFailure(error); });
    }

    function showManual(url = HOME) {
        if (url !== STORE && !allowedUrl(url)) return Promise.reject(new Error('Endereço de configuração inválido.'));
        epoch++;
        return serialize(async () => {
            if (isManualChromeRunning(manualBrowsers.get(profileDir))) return true;
            await closeContext();
            collectVisible = false;
            lostContext = false;
            const child = await launchManualChrome({ profileDir, url });
            manualBrowsers.set(profileDir, child);
            child.once('exit', () => {
                if (manualBrowsers.get(profileDir) === child) manualBrowsers.delete(profileDir);
            });
            return true;
        }).catch(error => { throw chromeFailure(error); });
    }

    function close() {
        epoch++;
        return serialize(async () => { collectVisible = false; lostContext = false; await closeContext(); });
    }

    function createPage({ detail = false } = {}) {
        let tab = null, status = null, loading = false, revision = 0, documentVersion = 0, moving = null;
        let lastUrl = HOME, media = null, lostPage = false;
        const live = () => tab && !tab.isClosed() ? tab : null;
        const getURL = () => live()?.url() || '';
        const verify = (owner, generation) => {
            if (owner !== revision || generation !== epoch) throw interrupted();
            if (lostContext || lostPage) throw browserClosed();
        };
        function discard() {
            revision++;
            const old = tab;
            tab = null; moving = null; status = null; loading = false; lostPage = false;
            if (media) media.release();
            return old && !old.isClosed() ? old.close().catch(() => {}) : Promise.resolve();
        }

        async function getPage(owner, generation) {
            verify(owner, generation);
            if (live() && (!manual || collectVisible)) return tab;
            return serialize(async () => {
                verify(owner, generation);
                if (live() && (!manual || collectVisible)) return tab;
                const active = await open(collectVisible);
                verify(owner, generation);
                let page;
                if (collectVisible && !detail && manualPage && !manualPage.isClosed() && manualPage.url() === previewUrl
                    && allowedUrl(previewUrl) && !routeReason(previewUrl)) {
                    page = manualPage;
                    manualPage = null;
                } else page = takeStartPage() || await active.newPage();
                if (owner !== revision || generation !== epoch) {
                    await page.close().catch(() => {}); throw interrupted();
                }
                tab = page;
                const mainNavigation = request => {
                    try { return request.isNavigationRequest() && request.frame() === page.mainFrame(); }
                    catch (_error) { return false; }
                };
                page.on('request', request => {
                    if (tab === page && mainNavigation(request)) {
                        loading = true; status = null;
                    }
                });
                page.on('response', response => {
                    const request = response.request();
                    if (tab === page && mainNavigation(request)) {
                        status = { url: response.url(), statusCode: response.status() };
                        if (status.statusCode === 429 && allowedUrl(status.url)) {
                            const delay = retryAfterMilliseconds(response.headers()['retry-after']);
                            if (delay !== undefined) status.retryAfterMs = delay;
                        }
                    }
                });
                page.on('framenavigated', frame => { if (tab === page && frame === page.mainFrame()) documentVersion++; });
                page.on('load', () => { if (tab === page) loading = false; });
                const onLose = () => {
                    if (tab !== page) return;
                    if (!closing.has(active)) lostPage = true;
                    tab = null; status = null; loading = false;
                };
                page.on('close', onLose);
                page.on('crash', onLose);
                return page;
            }).catch(error => { throw chromeFailure(error); });
        }

        function routeReason(url) {
            if (!allowedUrl(url)) throw new Error('Página de coleta fora do Mercado Livre.');
            return mercadoLivreRouteAuthReason(new URL(url).pathname);
        }

        async function navigate(url, signal) {
            if (!allowedUrl(url)) throw new Error('Link fora do Mercado Livre.');
            if (signal?.aborted) throw interrupted();
            if (moving) void discard();
            if (!routeReason(url)) lastUrl = url;
            const owner = revision, generation = epoch, ticket = {};
            moving = ticket;
            const stop = () => { if (moving === ticket) void discard(); };
            signal?.addEventListener('abort', stop, { once: true });
            try {
                const page = await getPage(owner, generation);
                verify(owner, generation);
                const pending = media;
                if (pending && createMediaScript && pending.url === url) {
                    const script = await page.addInitScript({ content: createMediaScript(pending.state) });
                    if (pending === media) pending.script = script;
                    else await script.dispose();
                }
                verify(owner, generation);
                loading = true; status = null;
                await page.goto(url, { timeout: 35000, waitUntil: 'load' });
                verify(owner, generation);
                const actual = page.url();
                const reason = routeReason(actual);
                if (reason) throw loginRequired(reason);
            } catch (error) {
                verify(owner, generation);
                // Chromium rejects an empty 403/429 body after reporting its HTTP response.
                // Close the old document and retain only the confirmed top-level response.
                if (moving === ticket && /net::ERR_HTTP_RESPONSE_CODE_FAILURE\b/.test(error.message)
                    && status && [403, 429].includes(status.statusCode) && allowedUrl(status.url)) {
                    const blocked = { ...status, didNotCommit: true };
                    const closed = discard();
                    status = blocked;
                    await closed;
                    return;
                }
                if (!error.waitingLogin && moving === ticket) await discard();
                if (error.name === 'TimeoutError') throw Object.assign(new Error('Tempo esgotado ao abrir a página.'), {
                    name: 'TimeoutError', code: 'category_load_timeout'
                });
                if (error.waitingLogin || ['research_chrome_unavailable', 'research_browser_closed'].includes(error.code)) throw error;
                if (/target.*closed|browser.*closed|page crashed/i.test(error.message)) throw browserClosed();
                throw new Error('Não foi possível abrir a página da pesquisa.');
            } finally {
                signal?.removeEventListener('abort', stop);
                if (moving === ticket) { moving = null; loading = false; }
            }
        }

        async function evaluate(code, inspectLogin = false) {
            const page = live(), owner = revision, generation = epoch, document = documentVersion;
            verify(owner, generation);
            if (!page || (manual && !collectVisible)) throw interrupted();
            const href = page.url(), reason = routeReason(href);
            if (reason) throw loginRequired(reason);
            // Test the document again inside the target frame. A redirect can commit between
            // the URL check above and Chromium scheduling the expression.
            const guard = 'if(window !== window.top || location.href !== ' + JSON.stringify(href)
                + ') throw new Error("Documento da pesquisa alterado.");\n';
            const gate = inspectLogin ? '' : 'const __jkAuth = ' + mercadoLivreAuthScript()
                + '; if (__jkAuth) throw new Error("jk-market-auth:" + __jkAuth);\n';
            let timeout;
            try {
                const read = page.evaluate('{\n' + guard + gate + String(code) + '\n}');
                const value = await Promise.race([read, new Promise((_resolve, reject) => {
                    timeout = setTimeout(() => reject(Object.assign(new Error('Tempo esgotado ao ler a página.'),
                        { name: 'TimeoutError', code: 'category_load_timeout' })), 12000);
                })]);
                verify(owner, generation);
                if (page !== live() || page.url() !== href || document !== documentVersion) throw new Error('Documento da pesquisa alterado.');
                return value;
            } catch (error) {
                verify(owner, generation);
                const found = String(error.message).match(/jk-market-auth:(ml_login_required|ml_verification_required)/);
                if (found) throw loginRequired(found[1]);
                if (error.code === 'category_load_timeout') throw error;
                if (/target.*closed|browser.*closed|page crashed/i.test(error.message)) throw browserClosed();
                throw new Error('Não foi possível ler o documento da pesquisa.');
            } finally { clearTimeout(timeout); }
        }

        async function ensureAvantSession({ signal, rankingOnly = false, force = false } = {}) {
            const end = Date.now() + (rankingOnly ? 900 : 4500);
            do {
                if (signal?.aborted) throw interrupted();
                const probe = await evaluate(pageScript('probe', rankingOnly), true);
                if (probe?.state === 'authenticated') return { authenticated: true, attempted: false };
                if (probe?.state === 'ml_login') return { authenticated: false, attempted: false, reason: probe.reason };
                if (['logged_out', 'login_form', 'untrusted_form'].includes(probe?.state)) {
                    return { authenticated: false, attempted: false, reason: 'avant_login_required' };
                }
                if (!force && !rankingOnly) return { collectionAllowed: true, attempted: false };
                await new Promise(resolve => setTimeout(resolve, 200));
            } while (Date.now() < end);
            return { authenticated: false, attempted: false,
                reason: force ? 'avant_login_required' : 'avant_ranking_login_not_shown' };
        }

        function deferRecommendationMedia(url, itemId) {
            if (!createMediaScript || !allowedUrl(url)) return;
            if (media) media.release();
            const token = ++nextMediaToken;
            const pending = { url, state: { url, itemId, token }, script: null, release() {
                if (media !== pending) return;
                media = null;
                if (pending.script) void pending.script.dispose().catch(() => {});
                const page = live();
                const href = page?.url();
                if (page && (!manual || collectVisible) && allowedUrl(href) && !routeReason(href)) {
                    void page.evaluate(({ event, token, href }) => {
                        if (window === window.top && location.href === href) window.dispatchEvent(new CustomEvent(event, { detail: token }));
                    }, { event: RELEASE_MEDIA, token, href }).catch(() => {});
                }
            } };
            media = pending;
            return pending.release;
        }

        return {
            prepare: async (options = {}) => {
                if (!detail) {
                    lostContext = false;
                    lostPage = false;
                    if (typeof options.collectVisible === 'boolean') {
                        collectVisible = options.collectVisible;
                        await prepare();
                    }
                }
                await getPage(revision, epoch);
            },
            navigate, executeJavaScript: evaluate, ensureAvantSession, deferRecommendationMedia, getURL, getResumeURL: () => lastUrl,
            isLoading: () => loading,
            getNavigationStatus: () => status && (status.didNotCommit || status.url === getURL()) ? { ...status } : null,
            stop: () => { void discard(); }, destroy: discard, close: discard,
            finish: async state => { if (state !== 'waiting_login' || detail) await discard(); },
            show: () => show(lastUrl, { collectVisible: true })
        };
    }

    return { prepare, createPage, show, showManual, close };
}

module.exports = { createChromeResearchSession };
