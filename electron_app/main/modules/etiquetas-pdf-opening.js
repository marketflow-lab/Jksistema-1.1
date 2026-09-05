const fs = require('fs');
const os = require('os');
const path = require('path');
const { randomUUID } = require('crypto');
const { pathToFileURL } = require('url');

const MAX_PDF_BYTES = 50 * 1024 * 1024;
const DEFAULT_CLEANUP_DELAY_MS = 15 * 60 * 1000;

function normalizePdfBytes(value) {
    if (Buffer.isBuffer(value)) return value;
    if (value instanceof ArrayBuffer) return Buffer.from(new Uint8Array(value));
    if (ArrayBuffer.isView(value)) {
        return Buffer.from(value.buffer, value.byteOffset, value.byteLength);
    }
    if (value && value.type === 'Buffer' && Array.isArray(value.data)) {
        return Buffer.from(value.data);
    }
    return Buffer.alloc(0);
}

function validatePdfBytes(value) {
    const bytes = normalizePdfBytes(value);
    if (!bytes.length) throw new Error('PDF vazio.');
    if (bytes.length > MAX_PDF_BYTES) throw new Error('PDF excede o limite de 50 MB.');
    if (bytes.subarray(0, 5).toString('ascii') !== '%PDF-') {
        throw new Error('Conteudo recebido nao e um PDF valido.');
    }
    return bytes;
}

function safePdfFilename(value) {
    const raw = path.basename(String(value || 'etiquetas-avulsas.pdf'));
    const stem = raw.replace(/\.pdf$/i, '').replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
    return `${stem || 'etiquetas-avulsas'}.pdf`;
}

function scheduleFileCleanup(filePath, delayMs, fsModule = fs) {
    const timer = setTimeout(() => {
        try { fsModule.rmSync(filePath, { force: true }); } catch (_err) {}
    }, Math.max(0, Number(delayMs) || 0));
    if (typeof timer.unref === 'function') timer.unref();
}

async function openPdfBytesInGoogleChrome(pdfBytes, options = {}) {
    if (typeof options.tryOpenChrome !== 'function') {
        throw new Error('Abridor do Google Chrome indisponivel.');
    }

    const bytes = validatePdfBytes(pdfBytes);
    const fsModule = options.fsModule || fs;
    const tempRoot = path.resolve(options.tempRoot || path.join(os.tmpdir(), 'jk-sistema-pdf-preview'));
    fsModule.mkdirSync(tempRoot, { recursive: true });

    const uniqueName = `${randomUUID()}-${safePdfFilename(options.filename)}`;
    const filePath = path.join(tempRoot, uniqueName);
    fsModule.writeFileSync(filePath, bytes, { flag: 'wx' });

    let chromeResult;
    try {
        chromeResult = await options.tryOpenChrome(pathToFileURL(filePath).href);
    } catch (_err) {
        chromeResult = { success: false, reason: 'falha-ao-abrir-chrome' };
    }

    if (!chromeResult || chromeResult.success !== true) {
        try { fsModule.rmSync(filePath, { force: true }); } catch (_err) {}
        return {
            success: false,
            reason: chromeResult && chromeResult.reason ? chromeResult.reason : 'chrome-nao-encontrado'
        };
    }

    scheduleFileCleanup(
        filePath,
        options.cleanupDelayMs === undefined ? DEFAULT_CLEANUP_DELAY_MS : options.cleanupDelayMs,
        fsModule
    );
    return { success: true, browser: 'chrome' };
}

module.exports = {
    MAX_PDF_BYTES,
    normalizePdfBytes,
    openPdfBytesInGoogleChrome,
    safePdfFilename,
    validatePdfBytes,
};
