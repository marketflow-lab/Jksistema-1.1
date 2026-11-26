(function (root, factory) {
    'use strict';
    if (typeof module === 'object' && module.exports) module.exports = factory(require('./page-scripts'), require('./shared-ranking'));
    else root.PesquisaMercadoCollector = factory(root.PesquisaMercadoPageScripts, root.PesquisaMercadoRanking);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (defaultScripts, ranking) {
    'use strict';

    function aborted(signal) {
        if (!signal || !signal.aborted) return;
        const error = new Error('Pesquisa de Mercado cancelada.');
        error.name = 'AbortError';
        throw error;
    }

    function delay(ms, signal) {
        return new Promise((resolve, reject) => {
            let timer;
            const onAbort = () => {
                clearTimeout(timer);
                signal.removeEventListener('abort', onAbort);
                const error = new Error('Pesquisa de Mercado cancelada.');
                error.name = 'AbortError';
                reject(error);
            };
            if (signal && signal.aborted) return onAbort();
            if (signal) signal.addEventListener('abort', onAbort, { once: true });
            timer = setTimeout(() => {
                if (signal) signal.removeEventListener('abort', onAbort);
                resolve();
            }, ms);
        });
    }

    function pageKey(value) {
        try { return defaultScripts.normalizeMarketplaceLink(new URL(value)); }
        catch (_err) { return String(value || ''); }
    }

    function isComplete(item, nowMs) {
        const metric = ranking.calculateMetrics(item, nowMs);
        return !!item && item.rankable === true && Number.isFinite(metric.vendas) && metric.vendas >= 0
            && Number.isFinite(metric.media) && metric.media >= 0;
    }

    function hasAvantDetailData(item) {
        const monthly = item.media_vendas_mensal ?? item.media_mensal;
        const sales = Number.isFinite(item.vendas) && item.vendas >= 0 || /^\+\s*\d/.test(item.vendas_limite_inferior || '');
        return item.avant_metrics_version === 3 && sales && !!item.data_criacao
            && Number.isFinite(monthly) && monthly >= 0;
    }

    function finalizeListings(items, nowMs = Date.now()) {
        return ranking.rankListings((Array.isArray(items) ? items : []).map(item => {
            const metric = ranking.calculateMetrics(item, nowMs);
            const hasMetric = Number.isFinite(metric.media) && metric.media >= 0;
            const hasSales = Number.isFinite(metric.vendas) && metric.vendas >= 0;
            const source = metric.meses !== null ? 'calculado_vendas_dias_avantpro' : (item.media_mensal_fonte || '');
            return { ...item, rankable: isComplete(item, nowMs),
                media_mensal_fonte: source, media_vendas_mensal_fonte: source,
                erro: item.erro || (!hasSales ? 'O Avant Pro ainda nao informou a quantidade de vendas.'
                    : (!hasMetric ? 'O Avant Pro ainda nao informou a metrica mensal deste anuncio.' : '')) };
        }), nowMs);
    }

    function matchesExpected(item, expected) {
        if (!expected) return true;
        if (expected.id) return String(item.id || '').replace('-', '').toUpperCase() === String(expected.id).replace('-', '').toUpperCase();
        return !!expected.url && pageKey(item.url) === pageKey(expected.url);
    }

    function compatiblePartialPanel(previous, item) {
        if (previous.avant_metrics_version !== 3 || !item.seller_confirmed || !previous.seller_confirmed
            || Boolean(item.catalogo) !== Boolean(previous.catalogo)) return false;
        const bothHaveSellerId = (previous.seller_id || previous.avant_seller_id) && (item.seller_id || item.avant_seller_id);
        const bothHaveProfile = ['seller_perfil_url', 'avant_seller_perfil_url'].some(field => previous[field] && item[field]);
        if (!bothHaveSellerId && !bothHaveProfile) return false;

        // A row that disappears while the panel renders differs from a visible
        // lower bound or a monthly row explicitly showing an unavailable value.
        if (item.vendas == null && (item.vendas_rotulo || item.vendas_exibicao || item.vendas_limite_inferior)) return false;
        if ((item.media_mensal ?? item.media_vendas_mensal) == null && (item.media_mensal_rotulo || item.media_vendas_mensal_exibicao)) return false;
        return ['vendas', 'vendas_rotulo', 'vendas_exibicao', 'vendas_limite_inferior', 'vendas_estimadas',
            'media_mensal', 'media_vendas_mensal', 'media_mensal_rotulo', 'media_vendas_mensal_exibicao',
            'ritmo_atual', 'data_criacao'].every(field =>
            item[field] == null || item[field] === '' || item[field] === previous[field]);
    }

    function selectObservation(previous, item) {
        if (!previous || item.seller_confirmed === false || (item.catalogo && !previous.catalogo)
            || ['vendedor_nao_confirmado', 'catalogo_sem_evidencia_vendedor'].includes(item.status)) return item;
        const sellerIds = [previous.seller_id, previous.avant_seller_id, item.seller_id, item.avant_seller_id].filter(Boolean).map(String);
        if (new Set(sellerIds).size > 1 || ['seller_perfil_url', 'avant_seller_perfil_url'].some(field =>
            previous[field] && item[field] && pageKey(previous[field]) !== pageKey(item[field]))) return item;
        if (previous.rankable && !item.rankable && (item.avant_metrics_version !== 3 || compatiblePartialPanel(previous, item))) return previous;
        if (item.avant_metrics_version === 3 || previous.avant_metrics_version === 3) return item;
        return { ...previous, ...item };
    }

    async function collectFirstPage(options) {
        const opts = options || {}, browser = opts.browser;
        if (!browser || typeof browser.executeJavaScript !== 'function' || typeof browser.getURL !== 'function') throw new Error('Navegador da Pesquisa de Mercado indisponivel.');
        const scripts = opts.pageScripts || defaultScripts;
        if (!scripts) throw new Error('Scripts de coleta indisponiveis.');
        const now = typeof opts.now === 'function' ? opts.now : Date.now;
        const wait = opts.wait || ((ms) => delay(ms, opts.signal));
        const start = now(), timeout = Math.max(1000, Number(opts.timeoutMs) || 180000);
        const waitForLoadingPanel = opts.waitForLoadingPanel === true && opts.detail === true && !!(opts.expectedItem?.id || opts.expectedItem?.url);
        const waitForAvantData = waitForLoadingPanel && opts.waitForAvantData === true;
        let deadline = start + timeout;
        const settleMs = Math.max(0, Number.isFinite(opts.settleMs) ? opts.settleMs : 1500);
        const missingWaitMs = Math.max(settleMs, Number.isFinite(opts.missingWaitMs) ? opts.missingWaitMs : 15000);
        const pollMs = Math.max(10, Number(opts.pollMs) || 450);
        const initialUrl = pageKey(await browser.getURL());
        const items = new Map();
        let lastSignature = '', stableSince = now(), reachedBottom = false, quiet = 0, empty = false;
        let missingSince = null, clicksPending = false, avantPanelLoading = false, avantDataPending = waitForAvantData;
        let loadingOffer = null;
        let materialized = !!opts.detail, lastLayoutSignature = '', layoutStableSince = now(), layoutQuiet = 0;

        const checkpoint = async () => {
            aborted(opts.signal);
            if (opts.checkpoint) await opts.checkpoint();
            aborted(opts.signal);
        };
        const execute = async (code, readingLoadingPanel = false) => {
            await checkpoint();
            const value = readingLoadingPanel && typeof browser.readLoadingPanel === 'function'
                ? await browser.readLoadingPanel(code, opts.signal) : await browser.executeJavaScript(code);
            await checkpoint();
            return value;
        };
        const finish = (reason, authRequired) => {
            const timestamp = now();
            const anuncios = finalizeListings(Array.from(items.values()), timestamp);
            const missing = anuncios.filter(item => item.rankable !== true).length;
            const resultReason = reason === 'complete' && missing > 0 ? 'avant_data_incomplete' : reason;
            return { anuncios, incomplete: resultReason !== 'complete' || missing > 0, authRequired: !!authRequired, reason: resultReason,
                total: anuncios.length, com_dados_avant: anuncios.length - missing, incompletos: missing,
                ...(waitForAvantData && (reason === 'partial' ? { avantDataPending }
                    : { avantDataLoaded: !avantDataPending && ['complete', 'avant_data_incomplete'].includes(reason) })),
                ...(avantPanelLoading && (waitForLoadingPanel || missing > 0) && ['partial', 'timeout'].includes(reason)
                    ? { avantPanelLoading: true, ...(loadingOffer ? { avantLoadingOffer: loadingOffer } : {}) } : {}),
                primeira_pagina: !opts.detail, elapsedMs: Math.max(0, now() - start) };
        };
        // Internal observations also invalidate cached evidence when the page becomes blocked.
        const observe = async result => {
            aborted(opts.signal);
            if (typeof opts.onPartial === 'function') await opts.onPartial(result);
            aborted(opts.signal);
            return result;
        };
        if (waitForAvantData && typeof opts.onPartial === 'function') await observe(finish('partial'));
        await execute(scripts.prepareScript());
        await execute(scripts.scrollScript('top'));
        // An individual offer is not a lazy result list. Carousels and images
        // can keep moving the page bottom even after the Avant panel is ready.
        if (opts.detail) missingSince = now();

        while (waitForAvantData || now() < deadline || (waitForLoadingPanel && avantPanelLoading)) {
            const snapshot = await execute(scripts.snapshotScript({ seller: opts.seller || {}, detail: !!opts.detail, expectedItem: opts.expectedItem }),
                waitForAvantData || waitForLoadingPanel && avantPanelLoading);
            if (!snapshot || typeof snapshot !== 'object') return observe(finish('invalid_page_result'));
            if (snapshot.authRequired || snapshot.avantAuthRequired) {
                const reason = snapshot.authReason || (snapshot.authRequired ? 'ml_login_required' : 'avant_login_required');
                return observe(finish(reason, true));
            }
            if (snapshot.url && initialUrl && pageKey(snapshot.url) !== initialUrl) return observe(finish('page_changed'));
            if (waitForAvantData && snapshot.avantDetailIdentityConflict) return observe(finish('offer_identity_mismatch'));
            // A PDP can contain an empty recommendations block. Only listing
            // pages may use that page-wide signal to finish before Avant loads.
            empty = !opts.detail && !!snapshot.empty;
            let matchedOffer = false, observedDetailData = false;
            for (const incoming of Array.isArray(snapshot.anuncios) ? snapshot.anuncios : []) {
                if (!matchesExpected(incoming, opts.expectedItem)) continue;
                matchedOffer = true;
                const freshlyLoaded = waitForAvantData && snapshot.avantDetailDataRead !== false && hasAvantDetailData(incoming);
                observedDetailData ||= freshlyLoaded;
                const metric = ranking.calculateMetrics(incoming, now());
                const hasSales = Number.isFinite(metric.vendas) && metric.vendas >= 0;
                const item = incoming.rankable === true && !isComplete(incoming, now())
                    ? { ...incoming, rankable: false, status: hasSales ? 'sem_dados_avant' : 'sem_quantidade_avant',
                        erro: hasSales ? 'Aguardando a metrica mensal do Avant Pro.' : 'Aguardando a quantidade de vendas do Avant Pro.' }
                    : incoming;
                const key = item.id || item.key || item.url;
                if (!key) continue;
                const previous = items.get(key);
                const order = previous ? previous.ordem_coleta : items.size + 1;
                const observation = freshlyLoaded ? item : selectObservation(previous, item);
                items.set(key, { ...observation, ordem_coleta: order });
            }
            const wasLoading = avantPanelLoading;
            // The panel can mount before the product title that supplies the first item row.
            const panelOffer = snapshot.avantLoadingOffer;
            loadingOffer = waitForLoadingPanel && snapshot.avantDetailLoading === true && !snapshot.anuncios?.length
                && panelOffer && panelOffer.url === snapshot.url && matchesExpected(panelOffer, opts.expectedItem) ? panelOffer : null;
            avantPanelLoading = !!opts.detail && (matchedOffer || !!loadingOffer) && snapshot.avantDetailLoading === true;
            if (waitForLoadingPanel && wasLoading && !avantPanelLoading) {
                // The rows may arrive just after the loading label disappears.
                missingSince = now();
                deadline = missingSince + timeout;
            }
            const list = Array.from(items.values());
            // Data about the catalog can settle the panel without qualifying the
            // seller's offer for the ranking.
            avantDataPending = waitForAvantData && (avantPanelLoading || !observedDetailData);
            if (typeof opts.onPartial === 'function') await observe(finish('partial'));
            const signature = JSON.stringify(opts.detail
                ? [avantPanelLoading, avantDataPending, list.map(item => [item.id || item.url, item.rankable, item.vendas, item.media_mensal,
                    item.media_vendas_mensal, item.data_criacao, item.status, item.seller_confirmed,
                    item.seller_id, item.seller_perfil_url, item.avant_seller_id, item.avant_seller_perfil_url,
                    item.vendas_rotulo, item.vendas_exibicao, item.media_mensal_rotulo, item.media_vendas_mensal_exibicao])]
                : [snapshot.height, list.map(item => [item.id || item.url, item.rankable, item.vendas, item.media_mensal, item.data_criacao])]);
            if (signature !== lastSignature) { lastSignature = signature; stableSince = now(); quiet = 0; }
            else quiet += 1;
            reachedBottom = !!snapshot.atBottom;
            if (opts.onProgress) await opts.onProgress({ phase: 'coletando_primeira_pagina', total: items.size, com_dados_avant: list.filter(item => item.rankable).length, atBottom: reachedBottom });
            // Materialize every card before spending the bounded job time waiting
            // for individual Avant clicks. Already visible Avant data is kept above.
            if (!materialized) {
                const layoutSignature = JSON.stringify([snapshot.height, list.map(item => item.id || item.key || item.url)]);
                if (layoutSignature !== lastLayoutSignature) { lastLayoutSignature = layoutSignature; layoutStableSince = now(); layoutQuiet = 0; }
                else layoutQuiet += 1;
                if (!reachedBottom || layoutQuiet < 2 || now() - layoutStableSince < settleMs) {
                    if (!reachedBottom) await execute(scripts.scrollScript('next'));
                    await checkpoint();
                    await wait(pollMs);
                    continue;
                }
                materialized = true;
                missingSince = now();
                stableSince = now();
                quiet = 0;
            }
            const allReady = !avantDataPending && !(waitForLoadingPanel && avantPanelLoading)
                && list.length > 0 && list.every(item => isComplete(item, now()));
            const loadedData = waitForAvantData && !avantDataPending;
            const overdue = !waitForAvantData && !avantPanelLoading && missingSince !== null && now() - missingSince >= missingWaitMs;
            // Avant may scroll back to a card. Once the page is materialized, its
            // current viewport must not reset the wait for missing metrics.
            if (quiet >= 2 && now() - stableSince >= settleMs && (!clicksPending || allReady || loadedData)) {
                if (allReady || empty) return finish('complete');
                if (loadedData || overdue) return finish(list.length ? 'avant_data_incomplete' : 'no_listings_detected');
            }
            const click = allReady || loadedData || avantPanelLoading ? null : await execute(scripts.clickAvantScript({
                pending: list.filter(item => waitForAvantData || !isComplete(item, now())), detail: !!opts.detail
            }));
            if (click && click.loginBlocked) return observe(finish('avant_login_required', true));
            clicksPending = !!(click && (click.clicked > 0 || click.eligiblePending > 0 || (Array.isArray(click.pendingKeys) && click.pendingKeys.length)));
            // Pending cards must not turn a failed Avant session into an endless job.
            if (quiet >= 2 && overdue && !(click && click.clicked > 0)) {
                if (!allReady && !empty) return finish(list.length ? 'avant_data_incomplete' : 'no_listings_detected');
            }
            await checkpoint();
            await wait(pollMs);
        }
        return finish('timeout');
    }

    async function collectDetail(options) {
        const opts = options || {};
        aborted(opts.signal);
        if (opts.url) {
            if (!opts.browser || typeof opts.browser.navigate !== 'function') throw new Error('Navegacao indisponivel.');
            if (typeof opts.onNavigationStart === 'function') await opts.onNavigationStart();
            aborted(opts.signal);
            if (opts.waitForLoadingPanel === true) await opts.browser.navigate(opts.url, opts.signal, { waitForAvantPanel: true });
            else await opts.browser.navigate(opts.url, opts.signal);
            aborted(opts.signal);
        }
        if (typeof opts.onNavigationComplete === 'function') await opts.onNavigationComplete();
        aborted(opts.signal);
        return collectFirstPage({ ...opts, detail: true });
    }

    return Object.freeze({ AVANT_METRICS_VERSION: 3, collectFirstPage, collectDetail, hasAvantDetailData, isComplete, finalizeListings, discoveryScript: () => defaultScripts.discoveryScript() });
});
