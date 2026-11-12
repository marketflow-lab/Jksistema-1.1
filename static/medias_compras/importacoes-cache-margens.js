(function (global) {
    'use strict';
    const VERSAO = 1;
    const TAG = 'analises_importacao';
    const FONTE = 'simulacao_importacao_sku';
    const AVISO = 'jk.importacoes.margens.atualizadas.v1';
    const estados = new Map();
    const escopos = new WeakMap();
    const gravacoes = new Map();
    const avisos = new Map();
    const cache = () => global.JKCachePersistente;
    const texto = value => String(value ?? '').trim();
    const clonar = value => value == null ? value : JSON.parse(JSON.stringify(value));
    const canonical = value => Array.isArray(value) ? value.map(canonical)
        : value && typeof value === 'object'
            ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
    const igual = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));

    function opcoes(raw) {
        return { ...raw, contexto: raw.contexto || cache()?.contexto?.(),
            listaId: texto(raw.listaId), storeId: texto(raw.storeId), sku: texto(raw.sku).toUpperCase() };
    }

    function autorizado(options) {
        if (options.vigente && !options.vigente()) return false;
        if (!options.contexto || !cache()) return true;
        if (cache().vigente && !cache().vigente(options.contexto)) return false;
        return cache().podeManterDados ? cache().podeManterDados(options.contexto, [TAG])
            : !cache().dadosVigentes || cache().dadosVigentes(options.contexto, [TAG]);
    }

    function contextoAtual(options) {
        if (!autorizado(options)) return null;
        return cache()?.contexto?.() || options.contexto;
    }

    async function escopo(options) {
        if (!options.contexto || !autorizado(options)) return null;
        if (!escopos.has(options.contexto)) escopos.set(options.contexto, cache()?.chaveEscopo?.(options.contexto));
        return (await escopos.get(options.contexto)) || null;
    }

    function chave(options) {
        return 'importacoes:margens:v1:' + encodeURIComponent(options.storeId) + ':' + encodeURIComponent(options.listaId);
    }

    function normalizarFontes(raw) {
        if (!raw || typeof raw !== 'object') return {};
        if ('anuncios_loja_store_id' in raw || 'concorrentes_valores' in raw) return clonar(raw);
        return {
            anuncios_loja_store_id: texto(raw.storeId), anuncios_loja_seller_id: texto(raw.sellerId),
            anuncios_loja_site_id: texto(raw.siteId), anuncios_loja_nome: texto(raw.lojaNome),
            anuncios_loja_fonte: raw.fonte || '', anuncios_versao: raw.anunciosVersao ?? null,
            anuncios_loja: clonar(raw.anuncios || []), anuncios_loja_status: clonar(raw.statusAnuncios || {}),
            anuncios_loja_variacoes: clonar(raw.variacoes || {}), fontes_versao: raw.fontes_versao ?? null,
            concorrentes: clonar(raw.links || {}), concorrentes_valores: clonar(raw.valores || {}),
            concorrentes_mlb: clonar(raw.mlb || {}), concorrentes_preco: clonar(raw.preco || {}),
            consultado_em_ms: Number(raw.consultadoEm) || null,
        };
    }

    function identidadeFontes(raw) {
        return { storeId: texto(raw?.anuncios_loja_store_id ?? raw?.storeId),
            sellerId: texto(raw?.anuncios_loja_seller_id ?? raw?.sellerId),
            siteId: texto(raw?.anuncios_loja_site_id ?? raw?.siteId) };
    }

    function fontesRecentes(raw, agora = Date.now()) {
        const stamp = Number(raw?.consultado_em_ms ?? raw?.anuncios_consultado_em_ms ?? raw?.consultadoEm);
        return stamp > 0 && stamp <= agora && agora - stamp <= 60000;
    }

    function instanteCalculo(record) {
        const explicit = Number(record?.calculadoEm);
        return explicit > 0 ? explicit : (Date.parse(record?.analise?.atualizado_em || '') || 0);
    }

    function financeiroRecente(record, agora = Date.now()) {
        const stamp = instanteCalculo(record);
        return stamp > 0 && stamp <= agora && agora - stamp <= 900000;
    }

    function payloadCanonico(raw) {
        const payload = clonar(raw || {});
        // Observation time does not change the financial calculation. Source revisions do.
        ['consultadoEm', 'consultado_em_ms', 'anuncios_consultado_em_ms', 'confirmadoEm', 'calculadoEm'].forEach(key => delete payload[key]);
        ['precos_concorrentes', 'precos_anuncios_loja'].forEach(key => {
            payload[key] = Object.fromEntries(Object.entries(payload[key] || {}).map(([k, value]) => [k, Number(value)]));
        });
        payload.anuncios_loja = Object.fromEntries(Object.entries(payload.anuncios_loja || {}).map(([k, value]) => [k, texto(value).toUpperCase()]));
        payload.variacoes_anuncios_loja = Object.fromEntries(Object.entries(payload.variacoes_anuncios_loja || {})
            .map(([k, value]) => [k, value == null || value === '' ? null : String(value)]));
        // Missing variation means the same non-variation offer, on both screens.
        Object.keys(payload.anuncios_loja).forEach(key => { payload.variacoes_anuncios_loja[key] ??= null; });
        payload.fontes_versao ??= null;
        payload.custo_unitario = Number(payload.custo_unitario);
        return payload;
    }

    function analiseCompativel(analise, payload, identidade) {
        if (!analise || !payload || payload.fonte_custo !== FONTE || analise.fonte_custo !== FONTE
            || !/^[a-f0-9]{64}$/.test(payload.assinatura_calculo || '')
            || analise.assinatura_calculo !== payload.assinatura_calculo
            || !texto(payload.revisao_fontes_custo) || analise.revisao_fontes_custo !== payload.revisao_fontes_custo
            || (analise.fontes_versao ?? null) !== (payload.fontes_versao ?? null)
            || !Number.isFinite(payload.custo_unitario) || payload.custo_unitario < 0
            || !Number.isFinite(analise.custo_unitario) || Math.abs(analise.custo_unitario - payload.custo_unitario) > 0.005) return false;
        const id = identidadeFontes(identidade);
        if (!id.storeId || texto(analise.store_id || analise.anuncios_loja_store_id) !== id.storeId) return false;
        if (analise.anuncios_loja_seller_id != null && texto(analise.anuncios_loja_seller_id) !== id.sellerId) return false;
        if (analise.anuncios_loja_site_id != null && texto(analise.anuncios_loja_site_id) !== id.siteId) return false;
        return [[analise.margens || {}, payload.precos_concorrentes || {}],
            [analise.margens_anuncios_loja || {}, payload.precos_anuncios_loja || {}]].every(([margens, precos]) =>
            Object.keys(margens).length === Object.keys(precos).length && Object.entries(precos).every(([key, price]) => {
                const row = margens[key];
                return row && row.financeiro_exato === true && row.fonte_custo === FONTE
                    && Number.isFinite(row.custo_unitario) && Math.abs(row.custo_unitario - payload.custo_unitario) <= 0.005
                    && Number(row.preco_venda) === Number(price) && Number(price) > 0
                    && texto(row.item_id_loja).toUpperCase() === texto(payload.anuncios_loja?.[key]).toUpperCase()
                    && texto(row.variation_id) === texto(payload.variacoes_anuncios_loja?.[key])
                    && texto(row.store_id) === id.storeId && texto(row.seller_id) === id.sellerId && texto(row.site_id) === id.siteId;
            }));
    }

    function mesmosInsumos(record, payload, identidade) {
        return Boolean(record && igual(identidadeFontes(record.fontes), identidadeFontes(identidade))
            && igual(payloadCanonico(record.payload), payloadCanonico(payload))
            && analiseCompativel(record.analise, payload, identidade));
    }

    function registroValido(record, options) {
        return record && record.fontes?.anuncios_loja_fonte === 'loja_lista_sku'
            && identidadeFontes(record.fontes).storeId === options.storeId
            && Number.isFinite(record.confirmadoEm) && record.confirmadoEm > 0
            && record.confirmadoEm <= Date.now() + 60000
            && analiseCompativel(record.analise, record.payload, record.fontes);
    }

    async function obterEstado(options) {
        const scope = await escopo(options);
        if (!scope || !autorizado(options)) return null;
        const key = scope + ':' + chave(options);
        let state = estados.get(key);
        if (!state || !autorizado({ contexto: state.contexto })) {
            state = { scope, key, contexto: options.contexto, registros: {}, pendentes: new Map(), carregado: false, precisaLer: false };
            estados.set(key, state);
        }
        // A reader entering the SKU lock must observe the preceding writer, even if an older read was in flight.
        if (options.reler && state.carregando) await state.carregando;
        if (!state.carregando && (!state.carregado || options.reler || state.precisaLer)) {
            state.precisaLer = false;
            state.carregando = (async () => {
                const saved = await cache()?.ler?.(chave(options), { contexto: contextoAtual(options), permitirDesatualizado: true });
                if (!autorizado(options)) return;
                const data = saved?.dados;
                if (data?.versao === VERSAO && data.listaId === options.listaId && data.storeId === options.storeId) {
                    const records = Object.fromEntries(Object.entries(data.itens || {}).filter(([, record]) => registroValido(record, options)));
                    // Include local writes awaiting their transaction without losing newer records from another tab.
                    state.pendentes.forEach((record, sku) => {
                        if (record === null) delete records[sku]; else records[sku] = record;
                    });
                    state.registros = records;
                }
                state.carregado = true;
            })().finally(() => { state.carregando = null; });
        }
        await state.carregando;
        return autorizado(options) ? state : null;
    }

    async function lerLista(raw) {
        const options = opcoes(raw), state = await obterEstado(options);
        return state && autorizado(options) ? clonar(state.registros) : {};
    }

    async function ler(raw) {
        const options = opcoes(raw), state = await obterEstado(options);
        return state && autorizado(options) ? clonar(state.registros[options.sku] || null) : null;
    }

    function anunciar(options, scope) {
        const detail = { listaId: options.listaId, storeId: options.storeId, sku: options.sku };
        global.dispatchEvent(new global.CustomEvent('jk:importacoes-margens-atualizadas', { detail }));
        const key = scope + ':' + chave(options);
        if (avisos.has(key)) return;
        avisos.set(key, global.setTimeout(() => {
            avisos.delete(key);
            try { global.localStorage.setItem(AVISO, JSON.stringify({ scope, listaId: options.listaId, storeId: options.storeId,
                nonce: global.crypto?.randomUUID?.() || String(Date.now()) + Math.random() })); } catch (_error) {}
        }, 30));
    }

    async function gravar(options, record) {
        const state = await obterEstado(options);
        if (!state || !autorizado(options)) return false;
        if (record === null) delete state.registros[options.sku]; else state.registros[options.sku] = record;
        state.pendentes.set(options.sku, record);
        if (!cache()?.atualizar) return false;
        let batch = gravacoes.get(state.key);
        if (!batch) {
            batch = { alteracoes: {}, options: {}, esperas: [] };
            gravacoes.set(state.key, batch);
            global.setTimeout(async () => {
                gravacoes.delete(state.key);
                let saved = false;
                try {
                    const changes = Object.entries(batch.alteracoes).filter(([sku]) => autorizado(batch.options[sku]));
                    if (changes.length) {
                        const valid = batch.options[changes[changes.length - 1][0]];
                        const atualizar = writeContext => cache().atualizar(chave(valid), previous => {
                            const records = previous?.versao === VERSAO && previous.listaId === valid.listaId
                                && previous.storeId === valid.storeId ? { ...previous.itens } : {};
                            changes.forEach(([sku, value]) => {
                                if (!autorizado(batch.options[sku])) return;
                                if (value === null) delete records[sku]; else records[sku] = value;
                            });
                            return { versao: VERSAO, listaId: valid.listaId, storeId: valid.storeId, itens: records };
                        }, { contexto: writeContext, tags: [TAG], permitirDesatualizado: true });
                        // A successful PATCH of another SKU can advance the family revision during the transaction.
                        // Retry only that proven data race; quota/storage errors must not create an endless retry loop.
                        while (autorizado(valid)) {
                            const writeContext = contextoAtual(valid);
                            saved = await atualizar(writeContext);
                            if (saved || !autorizado(valid) || !cache()?.dadosVigentes
                                || cache().dadosVigentes(writeContext, [TAG])) break;
                            await new Promise(resolve => global.setTimeout(resolve, 10));
                        }
                        if (saved) changes.forEach(([sku, value]) => {
                            if (state.pendentes.get(sku) === value) state.pendentes.delete(sku);
                        });
                    }
                } catch (_error) { saved = false; }
                finally { batch.esperas.forEach(done => done(Boolean(saved))); }
            }, 10);
        }
        batch.alteracoes[options.sku] = record;
        batch.options[options.sku] = options;
        const saved = await new Promise(resolve => batch.esperas.push(resolve));
        if (!autorizado(options) && state.pendentes.get(options.sku) === record) {
            state.pendentes.delete(options.sku);
            if (state.registros[options.sku] === record) delete state.registros[options.sku];
            state.precisaLer = true;
        }
        if (saved && autorizado(options)) anunciar(options, state.scope);
        return saved;
    }

    async function salvar(raw) {
        const options = opcoes(raw);
        const record = { analise: clonar(raw.analise), fontes: normalizarFontes(raw.fontes), payload: clonar(raw.payload),
            confirmadoEm: raw.confirmadoEm ?? Date.now(), calculadoEm: instanteCalculo(raw) };
        if (!options.listaId || !options.storeId || !options.sku || !autorizado(options) || !registroValido(record, options)) return null;
        await gravar(options, record);
        return autorizado(options) ? clonar(record) : null;
    }

    async function remover(raw) {
        const options = opcoes(raw);
        if (!options.listaId || !options.storeId || !options.sku || !autorizado(options)) return false;
        return gravar(options, null);
    }

    global.addEventListener?.('storage', event => {
        if (event.key !== AVISO || !event.newValue) return;
        void (async () => {
            try {
                const detail = JSON.parse(event.newValue);
                const options = opcoes(detail);
                if (!autorizado(options) || await escopo(options) !== detail.scope) return;
                const state = estados.get(detail.scope + ':' + chave(options));
                if (state) state.precisaLer = true;
                global.dispatchEvent(new global.CustomEvent('jk:importacoes-margens-atualizadas', {
                    detail: { listaId: options.listaId, storeId: options.storeId, externa: true },
                }));
            } catch (_error) {}
        })();
    });
    global.addEventListener?.('jk:cache-persistente-invalidado', event => {
        const tags = event.detail?.tags || [];
        if ((tags.includes(TAG) || tags.includes('*')) && event.detail?.revogado) estados.clear();
    });
    global.JKImportacoesCacheMargens = Object.freeze({ versao: VERSAO, ler, lerLista, salvar, remover,
        normalizarFontes, identidadeFontes, mesmosInsumos, analiseCompativel, fontesRecentes, financeiroRecente });
})(window);
