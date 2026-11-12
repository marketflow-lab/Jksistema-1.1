(function (global) {
    'use strict';
    const VERSAO = 1;
    const TAG_CALCULOS = 'custos_importacao';
    const TAG_CADASTRO = 'cadastro_custos';
    let memorias = new WeakMap();
    const consultas = new WeakMap();
    const gravacoes = new WeakMap();
    const filasAnalise = new Map();
    const CHAVE_AVISO = 'jk.importacoes.custos.atualizados.v1';
    let avisoPendente;
    const clonar = value => JSON.parse(JSON.stringify(value));
    const texto = value => String(value ?? '').trim();
    const skuExato = value => texto(value).toUpperCase();
    const cache = () => global.JKCachePersistente;
    const canonical = value => Array.isArray(value) ? value.map(canonical)
        : value && typeof value === 'object'
            ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;

    function opcoes(raw, familia = TAG_CALCULOS) {
        const contexto = raw.contexto || cache()?.contexto?.();
        return { ...raw, contexto, familia, listaId: texto(raw.listaId), storeId: texto(raw.storeId) };
    }

    function vigente(options) {
        return (!options.vigente || options.vigente())
            && (!options.contexto || !cache()?.vigente || cache().vigente(options.contexto))
            && (!options.contexto || !cache()?.dadosVigentes
                || cache().dadosVigentes(options.contexto, [options.familia || TAG_CALCULOS]));
    }

    function chave(tipo, options) {
        return 'importacoes:calculados:v1:' + tipo + ':' + encodeURIComponent(options.storeId)
            + (tipo === 'cadastro' ? '' : ':' + encodeURIComponent(options.listaId));
    }

    function normalizarEntradas(entradas) {
        const result = Object.fromEntries(['totalUsd', 'freteUsd', 'cotacao', 'quantidade', 'iiPct', 'ipiPct',
            'pisPct', 'cofinsPct', 'mvaMgPct', 'baseIcmsFallback', 'baseCstMgFallback', 'baseCstPbFallback',
            'icmsFallback'].map(key => [key, entradas[key] ?? null]));
        result.proporcaoPct = Number.isFinite(entradas.proporcaoPct) && entradas.proporcaoPct > 0
            ? entradas.proporcaoPct : 100;
        result.stMg = Boolean(entradas.stMg);
        result.stPb = Boolean(entradas.stPb);
        return result;
    }

    function sha256(bytes) {
        // Non-secure development origins lack SubtleCrypto. Keep the same signature without disabling calculations.
        const constants = [0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
            0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
            0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
            0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
            0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
            0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
            0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2];
        const hash = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
        const padded = new Uint8Array(Math.ceil((bytes.length + 9) / 64) * 64);
        padded.set(bytes); padded[bytes.length] = 0x80;
        const data = new DataView(padded.buffer);
        data.setUint32(padded.length - 8, Math.floor(bytes.length / 0x20000000));
        data.setUint32(padded.length - 4, bytes.length * 8);
        const rotate = (value, n) => (value >>> n) | (value << (32 - n));
        const words = new Int32Array(64);
        for (let offset = 0; offset < padded.length; offset += 64) {
            for (let i = 0; i < 16; i++) words[i] = data.getInt32(offset + i * 4);
            for (let i = 16; i < 64; i++) {
                const a = words[i - 15], b = words[i - 2];
                words[i] = words[i - 16] + (rotate(a, 7) ^ rotate(a, 18) ^ (a >>> 3))
                    + words[i - 7] + (rotate(b, 17) ^ rotate(b, 19) ^ (b >>> 10));
            }
            let [a, b, c, d, e, f, g, h] = hash;
            for (let i = 0; i < 64; i++) {
                const t1 = (h + (rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25))
                    + ((e & f) ^ (~e & g)) + constants[i] + words[i]) | 0;
                const t2 = ((rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
                h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
            }
            [a, b, c, d, e, f, g, h].forEach((word, i) => { hash[i] = (hash[i] + word) | 0; });
        }
        return hash.map(value => (value >>> 0).toString(16).padStart(8, '0')).join('');
    }

    async function assinatura(entradas) {
        const source = JSON.stringify(canonical({ versao: VERSAO, entradas: normalizarEntradas(entradas) }));
        const bytes = new TextEncoder().encode(source);
        if (!global.crypto?.subtle) return sha256(bytes);
        const hash = await global.crypto.subtle.digest('SHA-256', bytes);
        return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
    }

    function memoria(options) {
        if (!options.contexto) return null;
        let state = memorias.get(options.contexto);
        if (!state) { state = new Map(); memorias.set(options.contexto, state); }
        return state;
    }

    function registroValido(record) {
        return record && /^[a-f0-9]{64}$/.test(record.assinatura || '')
            && texto(record.revisao) && record.entradas && record.calculo
            && typeof record.calculo.custoUnitario === 'number' && Number.isFinite(record.calculo.custoUnitario)
            && record.calculo.custoUnitario >= 0;
    }

    async function obterRegistros(options) {
        const key = chave('resultados', options);
        const state = memoria(options);
        if (!vigente(options)) return {};
        if (state?.has(key)) return state.get(key);
        const pending = (async () => {
            const saved = await cache()?.ler?.(key, { contexto: options.contexto });
            if (!vigente(options)) return {};
            const data = saved?.dados;
            const records = data?.versao === VERSAO && data.listaId === options.listaId && data.storeId === options.storeId
                ? Object.fromEntries(Object.entries(data.itens || {}).filter(([_sku, record]) => registroValido(record))) : {};
            state?.set(key, records);
            return records;
        })();
        state?.set(key, pending);
        return pending;
    }

    async function restaurarCalculos(raw) { return clonar(await obterRegistros(opcoes(raw))); }

    async function salvarCalculo(raw) {
        const options = opcoes(raw);
        const sku = skuExato(raw.sku);
        const record = { assinatura: raw.assinatura, calculo: raw.calculo, revisao: raw.revisao, entradas: raw.entradas };
        if (!options.listaId || !options.storeId || !sku || !registroValido(record) || !vigente(options)) return false;
        const key = chave('resultados', options);
        const records = await obterRegistros(options);
        if (!vigente(options)) return false;
        records[sku] = clonar(record);
        if (!options.contexto || !cache()?.atualizar) return false;
        let scoped = gravacoes.get(options.contexto);
        if (!scoped) { scoped = new Map(); gravacoes.set(options.contexto, scoped); }
        let batch = scoped.get(key);
        if (!batch) {
            batch = { records: {}, contexts: {}, waiters: [] };
            scoped.set(key, batch);
            // A table calculation batches its writes, while every caller still awaits durable completion.
            global.setTimeout(async () => {
                scoped.delete(key);
                let saved = false;
                try {
                    const records = Object.fromEntries(Object.entries(batch.records)
                        .filter(([sku]) => vigente(batch.contexts[sku])));
                    if (Object.keys(records).length) saved = await cache().atualizar(key, previous => ({
                        versao: VERSAO, listaId: options.listaId, storeId: options.storeId,
                        itens: { ...(previous?.versao === VERSAO && previous.listaId === options.listaId
                            && previous.storeId === options.storeId ? previous.itens : {}), ...records },
                    }), { contexto: options.contexto, tags: [TAG_CALCULOS] });
                } catch (_error) { saved = false; }
                finally { batch.waiters.forEach(done => done(Boolean(saved))); }
            }, 10);
        }
        batch.records[sku] = record;
        batch.contexts[sku] = options;
        const saved = await new Promise(resolve => batch.waiters.push(resolve));
        if (saved && vigente(options)) {
            global.dispatchEvent(new CustomEvent('jk:importacoes-custo-salvo', {
                detail: { listaId: options.listaId, storeId: options.storeId, sku },
            }));
            if (!avisoPendente) avisoPendente = global.setTimeout(() => {
                avisoPendente = null;
                try { global.localStorage.setItem(CHAVE_AVISO, global.crypto.randomUUID()); } catch (_error) {}
            }, 100);
        }
        return Boolean(saved);
    }

    async function calcular(raw) {
        const options = opcoes(raw);
        const sku = skuExato(raw.sku);
        const entradas = normalizarEntradas(raw.entradas);
        const hash = await assinatura(entradas);
        if (!vigente(options)) return null;
        const records = await obterRegistros(options);
        if (!vigente(options)) return null;
        const anterior = records[sku];
        if (registroValido(anterior) && anterior.assinatura === hash) {
            const record = { ...clonar(anterior), revisao: raw.revisao };
            if (anterior.revisao !== raw.revisao) await salvarCalculo({ ...options, sku, ...record });
            return vigente(options) ? record : null;
        }
        const calculo = raw.calcular(entradas);
        const record = { assinatura: hash, entradas, calculo, revisao: raw.revisao };
        // An incomplete calculation is useful to the UI, but never replaces a complete saved result.
        if (registroValido(record)) await salvarCalculo({ ...options, sku, ...record });
        return vigente(options) ? record : null;
    }

    function validarBase(data, options) {
        return data?.versao === VERSAO && data.lista?.id === options.listaId
            && data.lista?.store_id === options.storeId && texto(data.revisao)
            && Array.isArray(data.itens) && data.rateio && data.revisoes_sku;
    }

    function validarCadastro(data, options) {
        return data?.store_id === options.storeId && texto(data.revisao) && Array.isArray(data.produtos);
    }

    async function lerRecurso(tipo, options) {
        const saved = await cache()?.ler?.(chave(tipo, options), { contexto: options.contexto });
        if (!vigente(options)) return null;
        const valid = tipo === 'base' ? validarBase : validarCadastro;
        return valid(saved?.dados, options) ? saved.dados : null;
    }

    function lerBase(raw) { return lerRecurso('base', opcoes(raw)); }
    function lerCadastro(raw) { return lerRecurso('cadastro', opcoes(raw, TAG_CADASTRO)); }

    async function comTravaAnalise(raw, executar) {
        const options = opcoes(raw);
        const scope = options.contexto && await cache()?.chaveEscopo?.(options.contexto);
        if (!vigente(options)) throw new Error('Análise substituída.');
        const key = 'jk:importacoes:analise:' + (scope || 'sem-cache') + ':'
            + JSON.stringify([options.storeId, options.listaId, skuExato(raw.sku)]);
        const rodar = () => {
            if (!vigente(options)) throw new Error('Análise substituída.');
            return executar();
        };
        if (global.navigator?.locks?.request && scope) return global.navigator.locks.request(key, rodar);
        const anterior = filasAnalise.get(key) || Promise.resolve();
        const atual = anterior.catch(() => {}).then(rodar);
        filasAnalise.set(key, atual);
        try { return await atual; }
        finally { if (filasAnalise.get(key) === atual) filasAnalise.delete(key); }
    }

    async function pedirUmaVez(url, options, tag) {
        const controller = new AbortController();
        const timeout = global.setTimeout(() => controller.abort(), 20000);
        try {
            const response = await global.fetch(url, { method: 'GET', cache: 'no-store', signal: controller.signal,
                headers: options.headers || global.obterAuthHeaders?.() || {} });
            if (!vigente(options)) throw new Error('Consulta substituída.');
            if ([401, 403].includes(response.status)) {
                await cache()?.invalidar?.([tag]);
                const error = new Error('Acesso indisponível.');
                error.status = response.status;
                throw error;
            }
            if (!response.ok) throw Object.assign(new Error('Não foi possível atualizar os custos agora.'), { status: response.status });
            const data = await response.json();
            if (!vigente(options)) throw new Error('Consulta substituída.');
            return { data, response };
        } finally { global.clearTimeout(timeout); }
    }

    async function pedir(url, options, tag) {
        for (let tentativa = 0; tentativa < 2; tentativa++) {
            if (!vigente(options)) throw new Error('Consulta substituída.');
            try { return await pedirUmaVez(url, options, tag); }
            catch (error) {
                const transitorio = [409, 500, 502, 503, 504].includes(error.status)
                    || error.name === 'AbortError' || error instanceof TypeError;
                if (tentativa || !transitorio || !vigente(options)) throw error;
                await new Promise(resolve => global.setTimeout(resolve, 500));
            }
        }
    }

    async function consultarRecurso(tipo, raw) {
        const tag = tipo === 'base' ? TAG_CALCULOS : TAG_CADASTRO;
        const options = opcoes(raw, tag);
        const key = chave(tipo, options);
        const saved = await lerRecurso(tipo, options);
        if (!vigente(options)) return null;
        if (saved && options.aoRestaurar) await options.aoRestaurar(clonar(saved));
        if (!vigente(options)) return null;
        let running = options.contexto && consultas.get(options.contexto);
        if (!running && options.contexto) { running = new Map(); consultas.set(options.contexto, running); }
        if (running?.has(key)) return running.get(key);
        const promise = (async () => {
            try {
                const url = tipo === 'base'
                    ? '/api/medias-compras/listas-pedidos/' + encodeURIComponent(options.listaId) + '/base-custos'
                    : '/api/cadastro/lojas/' + encodeURIComponent(options.storeId) + '/produtos?view=costs';
                const { data: metadata } = await pedir(url + (url.includes('?') ? '&' : '?') + 'somente_revisao=true', options, tag);
                const store = tipo === 'base' ? metadata?.lista?.store_id : metadata?.store_id;
                if (store !== options.storeId || !texto(metadata?.revisao)
                    || (tipo === 'base' && metadata?.lista?.id !== options.listaId)) throw new Error('Revisão de custos inválida.');
                if (saved && saved.revisao === metadata.revisao) return clonar(saved);
                const { data, response } = await pedir(url, options, tag);
                const payload = tipo === 'base' ? data : { store_id: options.storeId,
                    revisao: response.headers.get('X-JK-Custos-Revisao'), produtos: data };
                const valid = tipo === 'base' ? validarBase : validarCadastro;
                if (!valid(payload, options)) throw new Error('Dados de custos inválidos.');
                if (!vigente(options)) return null;
                await cache()?.salvar?.(key, payload, { contexto: options.contexto, tags: [tag] });
                return vigente(options) ? payload : null;
            } catch (error) {
                if ([401, 403].includes(error.status)) throw error;
                if (!vigente(options)) return null;
                if (saved) return { ...clonar(saved), desatualizado: true, erro: error.message };
                throw error;
            } finally { running?.delete(key); }
        })();
        running?.set(key, promise);
        return promise;
    }

    // Other tabs retain independent in-memory maps; the next read must see their atomic merge.
    global.addEventListener?.('storage', event => {
        if (event.key === CHAVE_AVISO) {
            memorias = new WeakMap();
            global.dispatchEvent(new CustomEvent('jk:importacoes-custos-atualizados'));
        }
    });

    global.JKImportacoesCacheCustos = Object.freeze({ versao: VERSAO, assinatura, normalizarEntradas, calcular,
        restaurarCalculos, salvarCalculo, lerBase, lerCadastro, comTravaAnalise,
        consultarBase: options => consultarRecurso('base', options),
        consultarCadastro: options => consultarRecurso('cadastro', options),
    });
})(window);
