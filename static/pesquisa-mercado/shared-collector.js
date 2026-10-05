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
        try {
            const url = new URL(value);
            url.hash = '';
            for (const key of ['tracking_id', 'sid', 'position', 'polycard_client']) url.searchParams.delete(key);
            return url.href;
        } catch (_err) { return String(value || ''); }
    }

    async function collectFirstPage(options) {
        const opts = options || {}, browser = opts.browser;
        if (!browser || typeof browser.executeJavaScript !== 'function' || typeof browser.getURL !== 'function') throw new Error('Navegador da Pesquisa de Mercado indisponivel.');
        const scripts = opts.pageScripts || defaultScripts;
        if (!scripts) throw new Error('Scripts de coleta indisponiveis.');
        const now = typeof opts.now === 'function' ? opts.now : Date.now;
        const wait = opts.wait || ((ms) => delay(ms, opts.signal));
        const start = now(), timeout = Math.max(1000, Number(opts.timeoutMs) || 180000);
        const settleMs = Math.max(0, Number.isFinite(opts.settleMs) ? opts.settleMs : 1500);
        const missingWaitMs = Math.max(settleMs, Number.isFinite(opts.missingWaitMs) ? opts.missingWaitMs : 15000);
        const pollMs = Math.max(10, Number(opts.pollMs) || 450);
        const initialUrl = pageKey(await browser.getURL());
        const items = new Map();
        let lastSignature = '', stableSince = now(), reachedBottom = false, quiet = 0, empty = false;
        let firstBottomAt = null, clicksPending = false;
        let materialized = false, lastLayoutSignature = '', layoutStableSince = now(), layoutQuiet = 0;

        const checkpoint = async () => {
            aborted(opts.signal);
            if (opts.checkpoint) await opts.checkpoint();
            aborted(opts.signal);
        };
        const execute = async code => {
            await checkpoint();
            const value = await browser.executeJavaScript(code);
            await checkpoint();
            return value;
        };
        const finish = (reason, authRequired) => {
            const timestamp = now();
            const anuncios = ranking.rankListings(Array.from(items.values()).map(item => {
                const metric = ranking.calculateMetrics(item, timestamp);
                const hasMetric = Number.isFinite(metric.media) && metric.media >= 0;
                return { ...item, rankable: item.rankable === true && hasMetric,
                    media_vendas_mensal_fonte: metric.meses !== null ? 'calculado_vendas_dias_avantpro' : (item.media_mensal_fonte || ''),
                    erro: item.erro || (!hasMetric ? 'O Avant Pro ainda nao informou ritmo mensal ou vendas com data de criacao.' : '') };
            }), timestamp);
            const missing = anuncios.filter(item => item.rankable !== true).length;
            const resultReason = reason === 'complete' && missing > 0 ? 'avant_data_incomplete' : reason;
            return { anuncios, incomplete: resultReason !== 'complete' || missing > 0, authRequired: !!authRequired, reason: resultReason,
                total: anuncios.length, com_dados_avant: anuncios.length - missing, incompletos: missing,
                primeira_pagina: true, elapsedMs: Math.max(0, now() - start) };
        };
        await execute(scripts.prepareScript());
        await execute(scripts.scrollScript('top'));

        while (now() - start < timeout) {
            const snapshot = await execute(scripts.snapshotScript({ seller: opts.seller || {} }));
            if (!snapshot || typeof snapshot !== 'object') return finish('invalid_page_result');
            if (snapshot.authRequired || snapshot.avantAuthRequired) return finish(snapshot.avantAuthRequired ? 'avant_login_required' : 'ml_login_required', true);
            if (snapshot.url && initialUrl && pageKey(snapshot.url) !== initialUrl) return finish('page_changed');
            empty = !!snapshot.empty;
            for (const incoming of Array.isArray(snapshot.anuncios) ? snapshot.anuncios : []) {
                const metric = ranking.calculateMetrics(incoming, now());
                const item = incoming.rankable === true && !Number.isFinite(metric.media)
                    ? { ...incoming, rankable: false, status: 'sem_dados_avant', erro: 'Aguardando ritmo mensal ou vendas e data de criacao do Avant Pro.' }
                    : incoming;
                const key = item.id || item.key || item.url;
                if (!key) continue;
                const previous = items.get(key);
                const order = previous ? previous.ordem_coleta : items.size + 1;
                // A later empty DOM read may be a virtualized card. Keep confirmed
                // evidence, but never keep a rank after an explicit seller conflict.
                const conflict = ['vendedor_nao_confirmado', 'catalogo_sem_evidencia_vendedor'].includes(item.status);
                items.set(key, previous && previous.rankable && !item.rankable && !conflict
                    ? { ...previous, ordem_coleta: order }
                    : { ...previous, ...item, ordem_coleta: order });
            }
            const list = Array.from(items.values());
            const signature = JSON.stringify([snapshot.height, list.map(item => [item.id || item.url, item.rankable, item.vendas, item.media_mensal, item.data_criacao])]);
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
                firstBottomAt = now();
                stableSince = now();
                quiet = 0;
            }
            const allReady = list.length > 0 && list.every(item => item.rankable);
            // Avant may scroll back to a card. Once the page is materialized, its
            // current viewport must not reset the wait for missing metrics.
            if (quiet >= 2 && now() - stableSince >= settleMs && (!clicksPending || allReady)) {
                if (allReady || empty) return finish('complete');
                if (firstBottomAt !== null && now() - firstBottomAt >= missingWaitMs) return finish(list.length ? 'avant_data_incomplete' : 'no_listings_detected');
            }
            const click = allReady ? null : await execute(scripts.clickAvantScript());
            if (click && click.loginBlocked) return finish('avant_login_required', true);
            clicksPending = !!(click && (click.clicked > 0 || click.eligiblePending > 0 || (Array.isArray(click.pendingKeys) && click.pendingKeys.length)));
            // Pending cards must not turn a failed Avant session into an endless job.
            if (quiet >= 2 && firstBottomAt !== null && now() - firstBottomAt >= missingWaitMs && !(click && click.clicked > 0)) {
                if (!allReady && !empty) return finish(list.length ? 'avant_data_incomplete' : 'no_listings_detected');
            }
            await checkpoint();
            await wait(pollMs);
        }
        return finish('timeout');
    }

    async function collectDetail(options) {
        const opts = options || {};
        if (opts.url) {
            aborted(opts.signal);
            if (!opts.browser || typeof opts.browser.navigate !== 'function') throw new Error('Navegacao indisponivel.');
            await opts.browser.navigate(opts.url);
        }
        return collectFirstPage(opts);
    }

    return Object.freeze({ collectFirstPage, collectDetail, discoveryScript: () => defaultScripts.discoveryScript() });
});
