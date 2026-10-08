'use strict';

const https = require('https');
const fs = require('fs/promises');
const path = require('path');
const { randomUUID } = require('crypto');
const { Transform, Writable, pipeline } = require('stream');
const zlib = require('zlib');

const DUMP_URL = 'https://api.mercadolibre.com/sites/MLB/categories/all';
const MAX_BYTES = 64 * 1024 * 1024;
const CACHE_TTL_MS = 24 * 60 * 60 * 1000;
const REQUEST_TIMEOUT_MS = 25000;
const DEADLINE_MS = 40000;
const ID = /^MLB\d+$/;

class TaxonomyError extends Error {
    constructor(message, code = 'taxonomy_unavailable') {
        super(message);
        this.name = 'TaxonomyError';
        this.code = code;
    }
}

function invalid(message) {
    throw new TaxonomyError(message || 'A árvore de categorias recebida está incompleta. Tente atualizar a lista.', 'taxonomy_invalid');
}

function validName(value) {
    if (typeof value !== 'string') invalid();
    const name = value.replace(/\s+/g, ' ').trim();
    if (!name || name.length > 400) invalid();
    return name;
}

function categoryUrl(id, permalink) {
    const fallback = 'https://lista.mercadolivre.com.br/_CatId_' + id;
    if (typeof permalink !== 'string' || !permalink || permalink.length > 4000) return fallback;
    try {
        const url = new URL(permalink);
        if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443')
            || !['www.mercadolivre.com.br', 'mercadolivre.com.br', 'lista.mercadolivre.com.br'].includes(url.hostname)) return fallback;
        const pathname = decodeURIComponent(url.pathname);
        if (url.hostname === 'lista.mercadolivre.com.br' ? !pathname.replace(/\//g, '')
            : !/^\/(?:c|categorias)\/[^/]+/i.test(pathname)) return fallback;
        if (/\/(?:p|up|perfil|loja|stores?|vendedor)(?:\/|$)|\/MLB-\d+|_CustId_|_SellerId_/i.test(pathname)) return fallback;
        // Official category permalinks never need credentials, tracking or seller filters.
        if (url.search) return fallback;
        url.hash = '';
        return url.href;
    } catch (_error) {
        return fallback;
    }
}

function dateIso(value, fallback) {
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? date.toISOString() : new Date(fallback).toISOString();
}

function makeSnapshot(records, updatedAt) {
    if (!Array.isArray(records) || !records.length) invalid();
    const nodes = new Map();
    const relations = new Map();
    const paths = new Map();
    for (const record of records) {
        if (!Array.isArray(record) || record.length !== 5) invalid();
        const [key, rawName, rawUrl, childIds, pathIds] = record;
        if (typeof key !== 'string' || !ID.test(key) || nodes.has(key)) invalid();
        if (!Array.isArray(childIds) || !Array.isArray(pathIds) || !pathIds.length) invalid();
        const uniqueChildren = new Set(childIds);
        if (uniqueChildren.size !== childIds.length || childIds.some(id => typeof id !== 'string' || !ID.test(id))) invalid();
        if (pathIds.some(id => typeof id !== 'string' || !ID.test(id)) || new Set(pathIds).size !== pathIds.length
            || pathIds[pathIds.length - 1] !== key) invalid();
        nodes.set(key, Object.freeze({ key, nome: validName(rawName), url: categoryUrl(key, rawUrl) }));
        relations.set(key, childIds.slice());
        paths.set(key, pathIds.slice());
    }

    const parents = new Map();
    for (const [key, childIds] of relations) {
        for (const child of childIds) {
            if (!nodes.has(child)) invalid('A árvore oficial contém uma subcategoria ausente. Tente atualizar a lista.');
            if (parents.has(child)) invalid();
            parents.set(child, key);
        }
    }
    // Iterative traversal avoids a depth limit or recursion failure on future trees.
    const colors = new Map();
    for (const key of nodes.keys()) {
        if (colors.get(key) === 2) continue;
        const stack = [[key, false]];
        while (stack.length) {
            const [current, exit] = stack.pop();
            if (exit) { colors.set(current, 2); continue; }
            if (colors.get(current) === 1) invalid('A árvore oficial contém um ciclo inválido. Tente atualizar a lista.');
            if (colors.get(current) === 2) continue;
            colors.set(current, 1);
            stack.push([current, true]);
            const children = relations.get(current);
            for (let index = children.length - 1; index >= 0; index -= 1) stack.push([children[index], false]);
        }
    }

    const roots = [];
    let maxDepth = 0;
    for (const [key, pathIds] of paths) {
        if (pathIds.some(id => !nodes.has(id))) invalid();
        const parent = parents.get(key);
        if (!parent) {
            if (pathIds.length !== 1) invalid();
            roots.push(key);
        } else {
            const parentPath = paths.get(parent);
            if (pathIds.length !== parentPath.length + 1 || parentPath.some((id, index) => pathIds[index] !== id)) invalid();
        }
        maxDepth = Math.max(maxDepth, pathIds.length);
    }
    if (!roots.length) invalid();
    roots.sort((left, right) => nodes.get(left).nome.localeCompare(nodes.get(right).nome, 'pt-BR'));
    const requireKey = key => {
        if (!nodes.has(key)) throw new TaxonomyError('Categoria não encontrada na árvore oficial. Atualize a lista.', 'taxonomy_unknown_category');
        return key;
    };
    const summary = Object.freeze({ total: nodes.size, rootCount: roots.length, subcategoryCount: nodes.size - roots.length, maxDepth });
    return Object.freeze({
        complete: true,
        updatedAt,
        summary,
        // Unknown get() is deliberately null; traversal methods reject unknown IDs.
        get(key) { return nodes.get(key) || null; },
        children(key = null) { return (key === null ? roots : relations.get(requireKey(key))).map(id => nodes.get(id)); },
        pathFromRoot(key) { return paths.get(requireKey(key)).map(id => ({ ...nodes.get(id) })); },
        childCounts(keys = nodes.keys()) {
            const result = {};
            for (const key of keys) result[requireKey(key)] = relations.get(key).length;
            return result;
        },
        // Compact public-only persistence; callers receive copies of every array.
        cacheRecords() {
            return [...nodes.values()].map(node => [node.key, node.nome, node.url, relations.get(node.key).slice(), paths.get(node.key).slice()]);
        }
    });
}

function parseTaxonomyDump(dump, updatedAt = new Date().toISOString()) {
    if (!dump || typeof dump !== 'object' || Array.isArray(dump)) invalid();
    const records = [];
    for (const [key, value] of Object.entries(dump)) {
        if (!value || typeof value !== 'object' || Array.isArray(value) || key !== value.id || !ID.test(key)
            || !Array.isArray(value.path_from_root) || !Array.isArray(value.children_categories)) invalid();
        const readReference = reference => {
            if (!reference || typeof reference !== 'object' || Array.isArray(reference)
                || typeof reference.id !== 'string' || !ID.test(reference.id)) invalid();
            validName(reference.name);
            return reference.id;
        };
        records.push([key, validName(value.name), categoryUrl(key, value.permalink),
            value.children_categories.map(readReference), value.path_from_root.map(readReference)]);
    }
    return makeSnapshot(records, dateIso(updatedAt, Date.now()));
}

function byteLimit() {
    let total = 0;
    return new Transform({
        transform(chunk, _encoding, callback) {
            total += chunk.length;
            callback(total > MAX_BYTES ? new TaxonomyError('A lista de categorias excedeu o tamanho permitido. Tente novamente mais tarde.', 'taxonomy_too_large') : null, chunk);
        }
    });
}

function downloadDump({ signal, requestTimeoutMs = REQUEST_TIMEOUT_MS, deadlineMs = DEADLINE_MS } = {}) {
    return new Promise((resolve, reject) => {
        let settled = false;
        let request;
        let response;
        const streams = [];
        const finish = (error, value) => {
            if (settled) return;
            settled = true;
            clearTimeout(deadline);
            if (signal) signal.removeEventListener('abort', abort);
            if (error) {
                if (request) request.destroy();
                if (response) response.destroy();
                for (const stream of streams) stream.destroy();
                reject(error instanceof TaxonomyError ? error : new TaxonomyError('Não foi possível carregar as categorias oficiais. Verifique a conexão e tente atualizar a lista.'));
            } else resolve(value);
        };
        const timeout = () => finish(new TaxonomyError('O Mercado Livre demorou para responder. Tente atualizar a lista.', 'taxonomy_timeout'));
        const abort = () => finish(new TaxonomyError('A consulta de categorias foi interrompida. Tente novamente.', 'taxonomy_aborted'));
        const deadline = setTimeout(timeout, Math.min(DEADLINE_MS, Math.max(1, deadlineMs)));
        if (signal && signal.aborted) { abort(); return; }
        if (signal) signal.addEventListener('abort', abort, { once: true });
        request = https.get(DUMP_URL, { headers: { Accept: 'application/json', 'Accept-Encoding': 'gzip, deflate' } }, incoming => {
            response = incoming;
            if (incoming.statusCode !== 200) {
                finish(new TaxonomyError('O Mercado Livre não disponibilizou a lista de categorias. Tente atualizar a lista.', 'taxonomy_http_error'));
                return;
            }
            const declaredLength = Number(incoming.headers['content-length']);
            if (Number.isFinite(declaredLength) && declaredLength > MAX_BYTES) {
                finish(new TaxonomyError('A lista de categorias excedeu o tamanho permitido.', 'taxonomy_too_large'));
                return;
            }
            const encoding = String(incoming.headers['content-encoding'] || 'identity').trim().toLowerCase();
            if (!['identity', 'gzip', 'deflate'].includes(encoding)) {
                finish(new TaxonomyError('O Mercado Livre retornou um formato de categorias indisponível.', 'taxonomy_encoding_invalid'));
                return;
            }
            const chunks = [];
            let inflatedSize = 0;
            const collect = new Writable({
                write(chunk, _encoding, callback) {
                    inflatedSize += chunk.length;
                    if (inflatedSize > MAX_BYTES) { callback(new TaxonomyError('A lista de categorias excedeu o tamanho permitido.', 'taxonomy_too_large')); return; }
                    chunks.push(chunk);
                    callback();
                }
            });
            streams.push(byteLimit());
            if (encoding === 'gzip') streams.push(zlib.createGunzip());
            if (encoding === 'deflate') streams.push(zlib.createInflate());
            streams.push(collect);
            pipeline(incoming, ...streams, error => {
                if (error) { finish(error); return; }
                finish(null, { bytes: Buffer.concat(chunks, inflatedSize), updatedAt: incoming.headers['x-content-created'] });
            });
        });
        request.setTimeout(Math.min(REQUEST_TIMEOUT_MS, Math.max(1, requestTimeoutMs)), timeout);
        request.on('error', error => finish(error));
    });
}

async function fetchTaxonomyDump(options) {
    const downloaded = await downloadDump(options);
    try {
        return { data: JSON.parse(downloaded.bytes.toString('utf8')), updatedAt: downloaded.updatedAt };
    } catch (_error) {
        throw new TaxonomyError('O Mercado Livre retornou uma lista de categorias inválida. Tente atualizar a lista.', 'taxonomy_invalid');
    }
}

class TaxonomyService {
    constructor({ cachePath = null, fetchDump = fetchTaxonomyDump, now = Date.now,
        requestTimeoutMs = REQUEST_TIMEOUT_MS, deadlineMs = DEADLINE_MS } = {}) {
        this.cachePath = cachePath;
        this.fetchDump = fetchDump;
        this.now = now;
        this.requestTimeoutMs = Math.min(REQUEST_TIMEOUT_MS, Math.max(1, requestTimeoutMs));
        this.deadlineMs = Math.min(DEADLINE_MS, Math.max(1, deadlineMs));
        this.snapshot = null;
        this.fetchedAt = 0;
        this.inflight = null;
        this.inflightState = null;
        this.cacheRead = false;
    }

    async readCache() {
        this.cacheRead = true;
        if (!this.cachePath) return;
        try {
            const stats = await fs.stat(this.cachePath);
            if (!stats.isFile() || stats.size > MAX_BYTES) return;
            const raw = await fs.readFile(this.cachePath);
            if (raw.length > MAX_BYTES) return;
            const value = JSON.parse(raw.toString('utf8'));
            if (!value || value.version !== 1 || Object.keys(value).some(key => !['version', 'updatedAt', 'fetchedAt', 'records'].includes(key))) return;
            const fetchedAt = Date.parse(value.fetchedAt);
            const updatedAt = Date.parse(value.updatedAt);
            if (!Number.isFinite(fetchedAt) || !Number.isFinite(updatedAt) || fetchedAt > this.now()) return;
            const snapshot = makeSnapshot(value.records, new Date(updatedAt).toISOString());
            this.snapshot = snapshot;
            this.fetchedAt = fetchedAt;
        } catch (_error) {
            // A missing, corrupt or truncated public cache is revalidated by the network.
        }
    }

    async persist(snapshot, fetchedAt) {
        if (!this.cachePath) return;
        const temporary = path.join(path.dirname(this.cachePath), '.' + path.basename(this.cachePath) + '.' + randomUUID() + '.tmp');
        try {
            const text = JSON.stringify({ version: 1, updatedAt: snapshot.updatedAt, fetchedAt: new Date(fetchedAt).toISOString(), records: snapshot.cacheRecords() });
            if (Buffer.byteLength(text) > MAX_BYTES) return;
            await fs.mkdir(path.dirname(this.cachePath), { recursive: true });
            await fs.writeFile(temporary, text, { flag: 'wx', mode: 0o600 });
            await fs.rename(temporary, this.cachePath);
        } catch (_error) {
            // The validated in-memory snapshot remains usable if disk caching is unavailable.
        } finally {
            await fs.unlink(temporary).catch(() => {});
        }
    }

    load({ forceRefresh = false } = {}) {
        if (this.inflight) {
            if (!forceRefresh || this.inflightState.refreshing) return this.inflight;
            const pending = this.inflight;
            const state = this.inflightState;
            // A refresh requested while disk cache is loading must still revalidate it.
            return pending.then(snapshot => state.refreshing ? snapshot : this.load({ forceRefresh: true }));
        }
        const state = { refreshing: false };
        const operation = (async () => {
            if (!this.cacheRead) await this.readCache();
            const age = this.now() - this.fetchedAt;
            if (!forceRefresh && this.snapshot && age >= 0 && age < CACHE_TTL_MS) return this.snapshot;
            state.refreshing = true;
            const controller = new AbortController();
            let timer;
            try {
                const deadline = new Promise((_resolve, reject) => {
                    timer = setTimeout(() => {
                        reject(new TaxonomyError('O Mercado Livre demorou para responder. Tente atualizar a lista.', 'taxonomy_timeout'));
                        controller.abort();
                    }, this.deadlineMs);
                });
                const fetched = await Promise.race([
                    Promise.resolve().then(() => this.fetchDump({ signal: controller.signal, requestTimeoutMs: this.requestTimeoutMs, deadlineMs: this.deadlineMs })),
                    deadline
                ]);
                const wrapped = fetched && typeof fetched === 'object' && Object.prototype.hasOwnProperty.call(fetched, 'data');
                const snapshot = parseTaxonomyDump(wrapped ? fetched.data : fetched, wrapped ? fetched.updatedAt : this.now());
                const fetchedAt = this.now();
                this.snapshot = snapshot;
                this.fetchedAt = fetchedAt;
                await this.persist(snapshot, fetchedAt);
                return snapshot;
            } catch (error) {
                if (error instanceof TaxonomyError) throw error;
                throw new TaxonomyError('Não foi possível carregar as categorias oficiais. Verifique a conexão e tente atualizar a lista.');
            } finally {
                clearTimeout(timer);
            }
        })();
        this.inflight = operation;
        this.inflightState = state;
        operation.then(() => { if (this.inflight === operation) this.inflight = null; }, () => { if (this.inflight === operation) this.inflight = null; });
        return operation;
    }
}

module.exports = { TaxonomyService, TaxonomyError, parseTaxonomyDump, fetchTaxonomyDump, downloadDump,
    DUMP_URL, MAX_BYTES, CACHE_TTL_MS, REQUEST_TIMEOUT_MS, DEADLINE_MS };
