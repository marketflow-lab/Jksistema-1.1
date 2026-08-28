'use strict';

const COSCO_TRACKING_ORIGIN = 'https://elines.coscoshipping.com';
const COSCO_TRACKING_PATH = '/ebusiness/cargoTracking';
const COSCO_TRACKING_PARTITION = 'jk-importacoes-cosco-public';
const ALLOWED_REFERENCE_TYPES = new Set(['BOOKING', 'BILLOFLADING', 'CONTAINER']);
const DEFAULT_TIMEOUT_MS = 25000;
const POLL_INTERVAL_MS = 700;
const PAGE_SNAPSHOT_SCRIPT = `(() => {
    const text = document.body ? String(document.body.innerText || '').slice(0, 250000) : '';
    const normalized = text.toLowerCase();
    return {
        text,
        title: String(document.title || '').slice(0, 120),
        pathname: String(location.pathname || '').slice(0, 160),
        frameCount: document.querySelectorAll('iframe').length,
        inputValues: Array.from(document.querySelectorAll('input'))
            .map((input) => String(input.value || '').trim().slice(0, 80))
            .filter(Boolean)
            .slice(0, 40),
        hasBookingLabel: /booking\\s*no/i.test(text),
        hasLatestStatus: /latest\\s+status/i.test(text),
        hasSearch: /\\bsearch\\b/i.test(text),
        hasAccessFailure: /access denied|forbidden|captcha|robot|verify you are human/i.test(normalized),
        hasLoading: /loading|please wait/i.test(normalized),
        hasNoResult: /no result(?:\\(s\\)|s)? found|no matching results?/i.test(normalized)
            || /notfoundct/i.test(String(location.pathname || ''))
    };
})()`;

function normalizeVisibleText(value) {
    return String(value || '')
        .replace(/\r/g, '')
        .replace(/[\t\u00a0]+/g, ' ')
        .replace(/[ ]{2,}/g, ' ')
        .trim();
}

function normalizeTrackingRequest(payload = {}) {
    const type = String(payload.type || '').trim().toUpperCase();
    const reference = String(payload.reference || '').trim().toUpperCase();
    if (!ALLOWED_REFERENCE_TYPES.has(type)) {
        throw new Error('Tipo de referencia de rastreamento invalido.');
    }
    if (!/^[A-Z0-9-]{4,40}$/.test(reference)) {
        throw new Error('Referencia de rastreamento invalida.');
    }
    return { type, reference };
}

function buildCoscoTrackingUrl(payload = {}) {
    const request = normalizeTrackingRequest(payload);
    const url = new URL(COSCO_TRACKING_PATH, COSCO_TRACKING_ORIGIN);
    url.searchParams.set('trackingType', request.type);
    url.searchParams.set('number', request.reference);
    return url.toString();
}

function isAllowedCoscoTrackingUrl(value) {
    try {
        const url = new URL(String(value || ''));
        return url.protocol === 'https:' && url.hostname.toLowerCase() === 'elines.coscoshipping.com';
    } catch (_err) {
        return false;
    }
}

function isAllowedCoscoFrameUrl(value) {
    const raw = String(value || '').trim();
    if (!raw || raw === 'about:blank') return true;
    try {
        const url = new URL(raw);
        const host = url.hostname.toLowerCase();
        return url.protocol === 'https:'
            && (host === 'coscoshipping.com' || host.endsWith('.coscoshipping.com'));
    } catch (_err) {
        return false;
    }
}

function collectFrameTree(frame, output = []) {
    if (!frame) return output;
    output.push(frame);
    const children = Array.isArray(frame.frames) ? frame.frames : [];
    for (const child of children) collectFrameTree(child, output);
    return output;
}

function safeFrameLocation(value) {
    try {
        const url = new URL(String(value || ''));
        return `${url.hostname.toLowerCase()}${url.pathname}`.slice(0, 220);
    } catch (_err) {
        return '';
    }
}

function buildCoscoSearchSubmitScript(request) {
    const reference = JSON.stringify(String(request && request.reference || ''));
    return `(() => {
        const reference = ${reference};
        const visible = (element) => {
            if (!element || element.disabled) return false;
            const style = getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
        };
        const inputs = Array.from(document.querySelectorAll('input'))
            .filter((input) => visible(input) && !/^(?:hidden|checkbox|radio|button|submit)$/i.test(input.type || 'text'));
        let input = inputs.find((candidate) => String(candidate.value || '').trim().toUpperCase() === reference.toUpperCase());
        if (!input) {
            input = inputs.find((candidate) => /booking|b\/l|container|number|reference/i.test(
                [candidate.placeholder, candidate.getAttribute('aria-label'), candidate.name].filter(Boolean).join(' ')
            )) || inputs[0] || null;
        }
        if (!input) return { clicked: false, reason: 'input-not-found' };
        if (String(input.value || '').trim().toUpperCase() !== reference.toUpperCase()) {
            const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
            if (descriptor && descriptor.set) descriptor.set.call(input, reference);
            else input.value = reference;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
        }
        const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], input[type="button"]'));
        const search = buttons.find((button) => visible(button) && /^search$/i.test(
            String(button.innerText || button.value || button.getAttribute('aria-label') || '').trim()
        ));
        if (!search) return { clicked: false, reason: 'search-not-found' };
        search.click();
        return { clicked: true };
    })()`;
}

function expectedReferenceVariants(reference) {
    const normalized = String(reference || '').trim().toUpperCase();
    const variants = [normalized];
    if (/^[A-Z]{4}\d{6,}$/.test(normalized)) variants.push(normalized.slice(4));
    return variants.filter(Boolean);
}

function lineAfter(lines, labelPattern, valuePattern) {
    for (let index = 0; index < lines.length; index += 1) {
        const line = lines[index];
        const inline = line.match(labelPattern);
        if (inline && inline[1] && (!valuePattern || valuePattern.test(inline[1]))) {
            return inline[1].trim();
        }
        if (!labelPattern.test(line)) continue;
        for (let offset = 1; offset <= 4 && index + offset < lines.length; offset += 1) {
            const candidate = lines[index + offset].trim();
            if (candidate && (!valuePattern || valuePattern.test(candidate))) return candidate;
        }
    }
    return '';
}

function lineBeforeMarker(lines, marker) {
    const wanted = String(marker || '').toUpperCase();
    for (let index = 1; index < lines.length; index += 1) {
        if (lines[index].toUpperCase() === wanted) return lines[index - 1].trim();
    }
    return '';
}

function firstMatchingLine(lines, pattern) {
    for (const line of lines) {
        if (pattern.test(line)) return line.trim();
    }
    return '';
}

function dateAfterWrappedLabel(lines, labelText) {
    const wanted = String(labelText || '').toLowerCase();
    const firstWord = wanted.split(/\s+/)[0];
    for (let index = 0; index < lines.length; index += 1) {
        if (!lines[index].toLowerCase().startsWith(firstWord)) continue;
        const joined = lines.slice(index, index + 3).join(' ').toLowerCase();
        if (!joined.includes(wanted)) continue;
        for (let offset = 0; offset <= 5 && index + offset < lines.length; offset += 1) {
            const candidate = lines[index + offset].trim();
            if (/20\d{2}[-/]\d{2}[-/]\d{2}/.test(candidate)) return candidate;
        }
    }
    return '';
}

function extractCoscoTrackingFromText(rawText, expectedReference = '') {
    const text = normalizeVisibleText(rawText);
    if (!text) return null;
    const lines = text.split('\n').map((line) => line.trim()).filter(Boolean);
    const upperText = text.toUpperCase();
    const variants = expectedReferenceVariants(expectedReference);
    if (variants.length && !variants.some((variant) => upperText.includes(variant))) return null;

    const bookingMatch = text.match(/Booking\s*No\.?\s*[:#]?\s*"?([A-Z0-9-]{6,40})/i);
    const bookingNumber = bookingMatch ? bookingMatch[1].trim() : '';
    if (bookingNumber && variants.length && !variants.includes(bookingNumber.toUpperCase())) return null;
    const latestStatus = lineAfter(
        lines,
        /^Latest\s+Status(?:\s*[:\-]\s*(.+))?$/i,
        /[A-Za-z]/,
    );
    const trafficTerm = lineAfter(
        lines,
        /^Traffic\s+Term\s*:\s*(.+)$/i,
        /[A-Za-z0-9]/,
    );
    const etd = lineAfter(
        lines,
        /^ETD(?:\s*[:\-]\s*(.+))?$/i,
        /20\d{2}[-/]\d{2}[-/]\d{2}/,
    );
    const eta = lineAfter(
        lines,
        /^ETA(?:\s*[:\-]\s*(.+))?$/i,
        /20\d{2}[-/]\d{2}[-/]\d{2}/,
    );
    const cargoAvailableAt = dateAfterWrappedLabel(
        lines,
        'estimated cargo available for pickup at destination',
    );
    const bookingStatus = firstMatchingLine(lines, /^Booking\s+(?:Confirmed|Cancelled|Pending|Rejected)$/i);
    const blStatus = firstMatchingLine(lines, /^B\/?L\s+(?:Not\s+Ready|Ready|Issued|Pending)$/i);
    const equipment = firstMatchingLine(lines, /^\d{2}(?:GP|HC|HQ|RF|OT|FR)(?:\s*[x*]\s*\d+)?$/i);

    const meaningfulCount = [
        latestStatus,
        bookingStatus,
        blStatus,
        lineBeforeMarker(lines, 'POR'),
        lineBeforeMarker(lines, 'FND'),
        trafficTerm,
        equipment,
        etd,
        eta,
        cargoAvailableAt,
    ].filter(Boolean).length;
    if (meaningfulCount < 2) return null;
    return {
        bookingNumber,
        bookingStatus,
        blStatus,
        latestStatus,
        origin: lineBeforeMarker(lines, 'POR'),
        destination: lineBeforeMarker(lines, 'FND'),
        trafficTerm,
        equipment,
        etd,
        eta,
        cargoAvailableAt,
    };
}

function wait(delayMs) {
    return new Promise((resolve) => setTimeout(resolve, delayMs));
}

function safeClose(win) {
    if (!win) return;
    try {
        if (!win.isDestroyed()) win.destroy();
    } catch (_err) {}
}

async function trackCoscoShipment(payload = {}, dependencies = {}) {
    let request = normalizeTrackingRequest(payload);
    const requestedReferenceType = request.type;
    let fallbackUsed = false;
    const BrowserWindow = dependencies.BrowserWindow;
    const session = dependencies.session;
    if (!BrowserWindow || !session || typeof session.fromPartition !== 'function') {
        throw new Error('Navegador seguro de rastreamento indisponivel.');
    }
    const timeoutMs = Math.max(3000, Math.min(Number(dependencies.timeoutMs) || DEFAULT_TIMEOUT_MS, 60000));
    let portalUrl = buildCoscoTrackingUrl(request);
    const trackingSession = session.fromPartition(COSCO_TRACKING_PARTITION, { cache: true });
    const denyDownload = (event) => event.preventDefault();
    if (typeof trackingSession.setPermissionRequestHandler === 'function') {
        trackingSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
    }
    if (typeof trackingSession.setPermissionCheckHandler === 'function') {
        trackingSession.setPermissionCheckHandler(() => false);
    }
    if (typeof trackingSession.on === 'function') trackingSession.on('will-download', denyDownload);

    let win = null;
    let lastDiagnostics = null;
    let searchSubmitted = false;
    try {
        win = new BrowserWindow({
            show: false,
            skipTaskbar: true,
            width: 1180,
            height: 820,
            webPreferences: {
                session: trackingSession,
                contextIsolation: true,
                nodeIntegration: false,
                sandbox: true,
                webSecurity: true,
                allowRunningInsecureContent: false,
                backgroundThrottling: false,
                webviewTag: false,
            },
        });
        const contents = win.webContents;
        if (typeof contents.setWindowOpenHandler === 'function') {
            contents.setWindowOpenHandler(() => ({ action: 'deny' }));
        }
        const guardNavigation = (event, targetUrl) => {
            if (!isAllowedCoscoTrackingUrl(targetUrl)) event.preventDefault();
        };
        contents.on('will-navigate', guardNavigation);
        contents.on('will-redirect', guardNavigation);

        const loadCurrentPortal = async () => {
            try {
                await win.loadURL(portalUrl, {
                    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
                });
            } catch (err) {
                const message = String(err && err.message || err || '');
                if (!/ERR_ABORTED|\(-3\)/i.test(message)) throw err;
            }
        };
        await loadCurrentPortal();

        let deadline = Date.now() + timeoutMs;
        polling: while (Date.now() < deadline) {
            if (!win || win.isDestroyed() || contents.isDestroyed()) break;
            const frames = contents.mainFrame
                ? collectFrameTree(contents.mainFrame)
                : [contents];
            const diagnostics = [];
            for (const frame of frames) {
                let frameUrl = '';
                try { frameUrl = String(frame.url || ''); } catch (_err) {}
                if (!isAllowedCoscoFrameUrl(frameUrl)) continue;
                let pageSnapshot = null;
                try {
                    pageSnapshot = await frame.executeJavaScript(PAGE_SNAPSHOT_SCRIPT, true);
                } catch (_err) {}
                const bodyText = pageSnapshot && typeof pageSnapshot === 'object'
                    ? String(pageSnapshot.text || '')
                    : '';
                const referenceVariants = expectedReferenceVariants(request.reference);
                const inputValues = pageSnapshot && Array.isArray(pageSnapshot.inputValues)
                    ? pageSnapshot.inputValues.map((value) => String(value || '').trim().toUpperCase())
                    : [];
                const inputReferencePresent = referenceVariants.some((variant) => inputValues.includes(variant));
                const bodyReferencePresent = referenceVariants
                    .some((variant) => bodyText.toUpperCase().includes(variant));
                const parserCandidate = extractCoscoTrackingFromText(bodyText, '');
                if (pageSnapshot && typeof pageSnapshot === 'object') {
                    diagnostics.push({
                        location: safeFrameLocation(frameUrl),
                        title: String(pageSnapshot.title || ''),
                        pathname: String(pageSnapshot.pathname || ''),
                        bodyLength: bodyText.length,
                        frameCount: Number(pageSnapshot.frameCount) || 0,
                        hasBookingLabel: !!pageSnapshot.hasBookingLabel,
                        hasLatestStatus: !!pageSnapshot.hasLatestStatus,
                        hasSearch: !!pageSnapshot.hasSearch,
                        hasAccessFailure: !!pageSnapshot.hasAccessFailure,
                        hasLoading: !!pageSnapshot.hasLoading,
                        hasNoResult: !!pageSnapshot.hasNoResult,
                        referencePresent: bodyReferencePresent || inputReferencePresent,
                        inputReferencePresent,
                        parserFieldCount: parserCandidate
                            ? Object.values(parserCandidate).filter(Boolean).length
                            : 0,
                    });
                }
                if (pageSnapshot && pageSnapshot.hasNoResult && inputReferencePresent) {
                    if (request.type === 'BILLOFLADING' && !fallbackUsed) {
                        fallbackUsed = true;
                        request = { ...request, type: 'BOOKING' };
                        portalUrl = buildCoscoTrackingUrl(request);
                        searchSubmitted = false;
                        lastDiagnostics = null;
                        await wait(POLL_INTERVAL_MS);
                        await loadCurrentPortal();
                        deadline = Date.now() + timeoutMs;
                        continue polling;
                    }
                    return {
                        success: false,
                        code: 'TRACKING_REFERENCE_NOT_FOUND',
                        message: 'A COSCO não encontrou resultado para esta referência no tipo consultado.',
                        portalUrl,
                        referenceType: request.type,
                        requestedReferenceType,
                        resolvedReferenceType: request.type,
                        fallbackUsed: false,
                        fallbackAttempted: fallbackUsed,
                        ...(dependencies.includeDiagnostics ? { diagnostics: { searchSubmitted, frames: diagnostics } } : {}),
                    };
                }
                const tracking = bodyReferencePresent
                    ? extractCoscoTrackingFromText(bodyText, request.reference)
                    : (inputReferencePresent ? parserCandidate : null);
                if (tracking) {
                    return {
                        success: true,
                        carrier: 'COSCO Shipping',
                        source: 'Página pública oficial da COSCO Shipping',
                        retrievedAt: new Date().toISOString(),
                        portalUrl,
                        tracking,
                        requestedReferenceType,
                        resolvedReferenceType: request.type,
                        fallbackUsed,
                    };
                }
                if (!searchSubmitted && pageSnapshot && pageSnapshot.hasBookingLabel && pageSnapshot.hasSearch) {
                    try {
                        const submitResult = await frame.executeJavaScript(
                            buildCoscoSearchSubmitScript(request),
                            true,
                        );
                        searchSubmitted = !!(submitResult && submitResult.clicked);
                    } catch (_err) {}
                }
            }
            lastDiagnostics = dependencies.includeDiagnostics
                ? { searchSubmitted, frames: diagnostics }
                : null;
            await wait(POLL_INTERVAL_MS);
        }
        return {
            success: false,
            code: 'TRACKING_RESULT_UNAVAILABLE',
            message: 'A COSCO não apresentou um resultado público dentro do tempo esperado.',
            portalUrl,
            requestedReferenceType,
            resolvedReferenceType: request.type,
            fallbackUsed: false,
            fallbackAttempted: fallbackUsed,
            ...(dependencies.includeDiagnostics ? { diagnostics: lastDiagnostics } : {}),
        };
    } catch (_err) {
        return {
            success: false,
            code: 'TRACKING_QUERY_FAILED',
            message: 'Não foi possível consultar a página pública da COSCO neste momento.',
            portalUrl,
            requestedReferenceType,
            resolvedReferenceType: request.type,
            fallbackUsed: false,
            fallbackAttempted: fallbackUsed,
            ...(dependencies.includeDiagnostics ? {
                diagnostics: {
                    errorName: String(_err && _err.name || ''),
                    errorMessage: String(_err && _err.message || _err || '').slice(0, 500),
                },
            } : {}),
        };
    } finally {
        if (trackingSession && typeof trackingSession.removeListener === 'function') {
            trackingSession.removeListener('will-download', denyDownload);
        }
        safeClose(win);
    }
}

module.exports = {
    ALLOWED_REFERENCE_TYPES,
    COSCO_TRACKING_ORIGIN,
    COSCO_TRACKING_PARTITION,
    buildCoscoTrackingUrl,
    extractCoscoTrackingFromText,
    isAllowedCoscoFrameUrl,
    isAllowedCoscoTrackingUrl,
    normalizeTrackingRequest,
    trackCoscoShipment,
};
