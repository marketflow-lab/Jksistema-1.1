'use strict';

const fs = require('fs/promises');
const path = require('path');
const { randomUUID } = require('crypto');

const MAX_ENTRIES = 16_384;
const MAX_URL_LENGTH = 2048;
const MAX_DEPTH = 16;
const ID = /^MLB\d{1,12}$/;
const MAX_BYTES = MAX_ENTRIES * (MAX_URL_LENGTH + MAX_DEPTH * 16 + 16) + 128;
const writes = new Map();

function pathKey(ids) {
    if (!Array.isArray(ids) || !ids.length || ids.length > MAX_DEPTH || new Set(ids).size !== ids.length) return '';
    for (const id of ids) if (typeof id !== 'string' || !ID.test(id)) return '';
    return ids.join('/');
}

const ORIGINAL_LANDING_SUFFIX = '/_NoIndex_True?original_category_landing=true';
function originalCategoryLandingBase(value) {
    return typeof value === 'string' && value.endsWith(ORIGINAL_LANDING_SUFFIX) ? value.slice(0, -ORIGINAL_LANDING_SUFFIX.length) : '';
}

function validCategoryRouteUrl(value) {
    if (typeof value !== 'string' || value.length > MAX_URL_LENGTH) return false;
    const landingBase = originalCategoryLandingBase(value);
    if (landingBase.endsWith('/')) return false;
    const match = /^https:\/\/(?:lista|imoveis|servicos)\.mercadolivre\.com\.br(\/[^\s?#%\\]*)$/.exec(landingBase || value);
    if (!match) return false;
    const pathname = landingBase ? match[1] : match[1].replace(/\/(?:\*_NoIndex_True|_NoIndex_True|\*)\/?$/, '/');
    if (!/^\/(?:[a-z0-9]+(?:-[a-z0-9]+)*\/)*[a-z0-9]+(?:-[a-z0-9]+)*\/?$/.test(pathname)) return false;
    return !/(?:^|\/)(?:mlb-?\d+[^/]*|p|up|perfil|loja|pagina|stores?|vendedor|busca|buscar|search|categorias|ofertas|q)(?:\/|$)/i.test(pathname);
}

async function readRoutes(filePath) {
    let file;
    try {
        file = await fs.open(filePath, 'r');
        if ((await file.stat()).size > MAX_BYTES) return new Map();
        const data = JSON.parse(await file.readFile('utf8'));
        const routes = new Map();
        if (data?.version === 1 && Array.isArray(data.routes) && data.routes.length <= MAX_ENTRIES) {
            for (const row of data.routes) {
                if (!Array.isArray(row) || row.length !== 2 || typeof row[0] !== 'string') continue;
                if (pathKey(row[0].split('/')) && validCategoryRouteUrl(row[1])) routes.set(row[0], row[1]);
            }
        }
        return routes;
    } catch (error) {
        if (error.code === 'ENOENT' || error instanceof SyntaxError) return new Map();
        throw error;
    } finally { await file?.close(); }
}

async function saveRoutes(filePath, routes) {
    await fs.mkdir(path.dirname(filePath), { recursive: true });
    const temporary = filePath + '.' + randomUUID() + '.tmp';
    const file = await fs.open(temporary, 'wx', 0o600);
    try {
        try { await file.writeFile(JSON.stringify({ version: 1, routes: [...routes] })); }
        finally { await file.close(); }
        await fs.rename(temporary, filePath);
    } finally { await fs.rm(temporary, { force: true }); }
}

function createCategoryRouteStore({ filePath }) {
    if (typeof filePath !== 'string' || !path.isAbsolute(filePath)) throw new TypeError('Invalid category cache location.');
    filePath = path.resolve(filePath);
    const fileKey = process.platform === 'win32' ? filePath.toLowerCase() : filePath;

    // All coordinators in the Electron main process share the same per-file write queue.
    async function change(ids, url, remove) {
        const key = pathKey(ids);
        if (!key || !validCategoryRouteUrl(url)) return false;
        const operation = (writes.get(fileKey) || Promise.resolve()).then(async () => {
            const routes = await readRoutes(filePath);
            if (remove) {
                if (routes.get(key) !== url) return false;
                routes.delete(key);
            } else {
                if (routes.get(key) === url) return true;
                routes.delete(key);
                routes.set(key, url);
                if (routes.size > MAX_ENTRIES) routes.delete(routes.keys().next().value);
            }
            await saveRoutes(filePath, routes);
            return true;
        }).catch(() => false);
        writes.set(fileKey, operation);
        try { return await operation; }
        finally { if (writes.get(fileKey) === operation) writes.delete(fileKey); }
    }

    return {
        async get(ids) {
            const key = pathKey(ids);
            if (!key) return null;
            try {
                await writes.get(fileKey);
                return (await readRoutes(filePath)).get(key) || null;
            } catch (_error) { return null; }
        },
        put: (ids, url) => change(ids, url, false),
        forget: (ids, url) => change(ids, url, true)
    };
}

module.exports = { createCategoryRouteStore, validCategoryRouteUrl, originalCategoryLandingBase };
