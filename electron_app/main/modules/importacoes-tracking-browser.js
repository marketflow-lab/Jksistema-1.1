'use strict';

const { CARGO_FORM_PROVIDERS } = require('./importacoes-cargo-tracking-providers');

const CMA_CGM_ORIGIN = 'https://www.cma-cgm.com';
const CMA_CGM_TRACKING_PATH = '/ebusiness/tracking';
const CMA_CGM_SEARCH_PATH = CMA_CGM_TRACKING_PATH + '/search';
const CARGO_CARRIERS = ['cosco', 'cma-cgm', 'hmm', 'yang-ming'];
const CARGO_TIMEOUT_MS = 20000;
const CARGO_PORTAL_NAMES = { cosco: 'COSCO Shipping', 'cma-cgm': 'CMA CGM', hmm: 'HMM', 'yang-ming': 'Yang Ming' };

let shownCargoPortal = null;

const COSCO_TRACKING_ORIGIN = 'https://elines.coscoshipping.com';
const COSCO_TRACKING_PATH = '/ebusiness/cargoTracking';
const COSCO_TRACKING_PARTITION = 'jk-importacoes-cosco-public';
const ALLOWED_REFERENCE_TYPES = new Set(['BOOKING', 'BILLOFLADING', 'CONTAINER']);
const DEFAULT_TIMEOUT_MS = 25000;
const POLL_INTERVAL_MS = 700;
const PUBLIC_VESSEL_PARTITION = 'jk-importacoes-ais-public';
const PUBLIC_VESSEL_TIMEOUT_MS = 25000;
const PUBLIC_VESSEL_PAGE_TIMEOUT_MS = 10000;
const MARITIME_OPTIMA_ORIGIN = 'https://maritimeoptima.com';
const MY_SHIP_TRACKING_ORIGIN = 'https://www.myshiptracking.com';
const VESSEL_FINDER_ORIGIN = 'https://www.vesselfinder.com';
const WINGO_MARINE_ORIGIN = 'https://wingo-marine.com';

let publicVesselWindow = null;

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
    const ladenReturnTime = lineAfter(
        lines,
        /^Laden\s+Return\s+Time(?:\s*[:\-]\s*(.+))?$/i,
        /20\d{2}[-/]\d{2}[-/]\d{2}/,
    );
    const atd = lineAfter(
        lines,
        /^ATD(?:\s*[:\-]\s*(.+))?$/i,
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
        ladenReturnTime,
        atd,
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
        ladenReturnTime,
        atd,
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
    if (dependencies.strictResult === true) {
        return queryOfficialCargoPortal('cosco', {
            url: buildCoscoTrackingUrl,
            allowUrl: isAllowedCoscoCargoUrl,
            readResult: readStrictCoscoResult,
        }, request, dependencies);
    }
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

const CARGO_REFERENCE_LABELS = {
    BOOKING: 'booking(?:\\s*(?:no\\.?|number|#))?',
    BILLOFLADING: '(?:b/?l|bill of lading)(?:\\s*(?:no\\.?|number|#))?',
    CONTAINER: 'container(?:\\s*(?:no\\.?|number|#))?',
};

function isAllowedCoscoCargoUrl(value) {
    try {
        const url = new URL(value);
        return url.origin === COSCO_TRACKING_ORIGIN && !url.username && !url.password
            && (url.pathname === COSCO_TRACKING_PATH || /^\/scct\/public\/ct\//i.test(url.pathname));
    } catch (_err) {
        return false;
    }
}

function isAllowedCmaCargoUrl(value) {
    try {
        const url = new URL(value);
        return url.origin === CMA_CGM_ORIGIN && !url.username && !url.password
            && (url.pathname === CMA_CGM_TRACKING_PATH || url.pathname === CMA_CGM_SEARCH_PATH);
    } catch (_err) {
        return false;
    }
}

function buildCmaCargoSubmitScript(request) {
    return `(() => {
        if (location.origin !== ${JSON.stringify(CMA_CGM_ORIGIN)}
            || location.pathname !== ${JSON.stringify(CMA_CGM_TRACKING_PATH)}) return false;
        const reference = ${JSON.stringify(request.reference)};
        const visible = (element) => element?.getClientRects().length
            && (!element.checkVisibility || element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }));
        const screenText = document.title + '\\n' + (document.body?.innerText || '').slice(0, 3000);
        if (/verifying the device|checking your browser|just a moment|access denied|forbidden|verify you are human/i.test(screenText)
            || Array.from(document.querySelectorAll('iframe[src*="captcha" i], iframe[src*="datadome" i], [data-sitekey]')).some(visible)) {
            return false;
        }
        const input = document.querySelector('#Reference');
        const button = document.querySelector('#btnTracking');
        if (!visible(input) || !visible(button) || input.disabled || input.readOnly || button.disabled
            || (input.maxLength >= 0 && reference.length > input.maxLength)) return false;
        if (input.form && button.form && input.form !== button.form) return false;
        const actions = [input.form?.action, button.getAttribute('formaction')].filter(Boolean);
        if (actions.some((action) => {
            const url = new URL(action, location.href);
            return url.origin !== location.origin || url.username || url.password
                || ![${JSON.stringify(CMA_CGM_TRACKING_PATH)}, ${JSON.stringify(CMA_CGM_SEARCH_PATH)}].includes(url.pathname);
        })) return false;
        input.value = reference;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        if (input.value !== reference || button.disabled || !visible(button)) return false;
        button.click();
        return true;
    })()`;
}

function buildCmaCargoResultScript(request) {
    const reference = JSON.stringify(request.reference);
    const referenceLabel = JSON.stringify('^' + CARGO_REFERENCE_LABELS[request.type] + '$');
    return `(() => {
        const reference = ${reference};
        if (location.origin !== ${JSON.stringify(CMA_CGM_ORIGIN)}
            || location.pathname !== ${JSON.stringify(CMA_CGM_SEARCH_PATH)}) return 'waiting';
        const referenceLabel = new RegExp(${referenceLabel}, 'i');
        const shipmentLabel = /\\b(?:status|vessel|voyage|origin|destination|load|discharge|last move|date)\\b/i;
        const screenText = document.title + '\\n' + (document.body?.innerText || '').slice(0, 3000);
        if (/verifying the device|checking your browser|just a moment/i.test(screenText)) return 'waiting';
        if (/access denied|forbidden|captcha|verify you are human/i.test(screenText)) return 'unavailable';
        const visible = (element) => element?.getClientRects().length
            && (!element.checkVisibility || element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }));
        const text = (element) => visible(element) ? String(element.innerText || '').trim() : '';
        const alert = document.querySelector('#trackingAlertError');
        if (/Your shipment was not found, please modify your search/i.test(text(alert))) {
            const input = document.querySelector('#Reference');
            return visible(input) && String(input.value || '').trim().toUpperCase() === reference
                ? 'not_found' : 'waiting';
        }
        const shipmentValue = (value) => /[\\p{L}\\d]/u.test(value)
            && !/^(?:N\\/?A|none|null|unknown|invalid|(?:shipment )?not found|no (?:shipment|data|results?|records?))\\b/i.test(value);
        for (const table of document.querySelectorAll('table')) {
            if (!visible(table)) continue;
            const headers = Array.from(table.querySelectorAll('thead tr:last-child th'), text);
            const index = headers.findIndex((label) => referenceLabel.test(label));
            if (index < 0) continue;
            for (const row of table.querySelectorAll('tbody tr')) {
                if (!visible(row)) continue;
                const cells = Array.from(row.cells, text);
                if (cells[index]?.toUpperCase() !== reference) continue;
                if (headers.some((label, column) => column !== index && shipmentLabel.test(label)
                    && shipmentValue(cells[column] || ''))) return 'found';
            }
        }
        return 'waiting';
    })()`;
}

async function readStrictCoscoResult(contents, request) {
    const referenceLabel = new RegExp('^' + CARGO_REFERENCE_LABELS[request.type]
        + '(?:(?:\\s*[:#]\\s*|\\s+)([A-Z0-9-]{4,40}))?$', 'i');
    for (const frame of collectFrameTree(contents.mainFrame || contents)) {
        const frameUrl = frame.url || (frame === contents ? contents.getURL() : '');
        if (!frameUrl || !isAllowedCoscoFrameUrl(frameUrl)) continue;
        const url = new URL(frameUrl);
        const outcome = /^\/scct\/public\/ct\/(detailCT|notFoundCT)\/?$/i.exec(url.pathname)?.[1];
        if (!outcome || url.port || url.username || url.password) continue;
        let snapshot;
        try {
            snapshot = await frame.executeJavaScript(PAGE_SNAPSHOT_SCRIPT);
        } catch (_err) {
            continue;
        }
        if (snapshot?.hasAccessFailure) return 'unavailable';
        const text = normalizeVisibleText(snapshot?.text);
        if (!text) continue;
        if (outcome.toLowerCase() === 'notfoundct') {
            if (/no result(?:\(s\)|s)? found|no matching results?/i.test(text)) return 'not_found';
            continue;
        }
        if (snapshot?.hasNoResult) continue;
        const lines = text.split('\n').map((line) => line.trim()).filter(Boolean);
        const matchesReference = lines.some((line, index) => {
            const match = line.match(referenceLabel);
            return match && (match[1] || lines[index + 1] || '').toUpperCase() === request.reference;
        });
        if (matchesReference && extractCoscoTrackingFromText(text)) return 'found';
    }
    return 'waiting';
}

async function withCargoDeadline(run, deadline) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new Error('Consulta fora do prazo.');
    let timeout;
    try {
        return await Promise.race([
            run(),
            new Promise((_, reject) => {
                timeout = setTimeout(() => reject(new Error('Consulta fora do prazo.')), remaining);
            }),
        ]);
    } finally {
        clearTimeout(timeout);
    }
}

async function loadCargoPortal(win, url, allowUrl, deadline, waitForAutomaticAccess) {
    const contents = win.webContents;
    let onNavigate;
    let waitingForAccess = false;
    try {
        const committed = new Promise((resolve) => {
            onNavigate = (_event, loadedUrl, status) => {
                if (waitForAutomaticAccess && status === 403 && allowUrl(loadedUrl)) {
                    // The official page may navigate again after its own verification.
                    waitingForAccess = true;
                    return;
                }
                resolve(status >= 200 && status < 400 && allowUrl(loadedUrl));
            };
            contents.on('did-navigate', onNavigate);
        });
        const afterInitialLoad = () => waitingForAccess ? committed : false;
        return await withCargoDeadline(() => Promise.race([
            committed,
            win.loadURL(url).then(afterInitialLoad, afterInitialLoad),
        ]), deadline);
    } finally {
        if (!contents.isDestroyed()) contents.removeListener('did-navigate', onNavigate);
    }
}

async function readCargoPortalResult(contents, provider, request, deadline) {
    const resultScript = provider.buildResultScript?.(request);
    const submitScript = provider.buildSubmitScript?.(request);
    const readStatus = () => withCargoDeadline(() => provider.readResult
        ? provider.readResult(contents, request)
        : contents.executeJavaScript(resultScript), deadline);
    let submitted = !submitScript;
    let previousStatus = 'waiting';
    while (!contents.isDestroyed() && Date.now() < deadline) {
        if (!provider.allowUrl(contents.getURL())) return 'unavailable';
        if (!submitted) {
            previousStatus = await readStatus();
            if (previousStatus === 'unavailable') return previousStatus;
            submitted = await withCargoDeadline(() => contents.executeJavaScript(submitScript, true), deadline) === true;
        }
        if (submitted) {
            const status = await readStatus();
            if (status === 'unavailable') return status;
            // Ignore a result already present before the form was submitted.
            if (status !== previousStatus && (status === 'found' || status === 'not_found')) return status;
            if (status === 'waiting') previousStatus = status;
        }
        await wait(Math.min(POLL_INTERVAL_MS, Math.max(0, deadline - Date.now())));
    }
    return 'unavailable';
}

async function queryOfficialCargoPortal(carrier, provider, request, dependencies) {
    const { BrowserWindow, session, parent, showPortal } = dependencies;
    const timeoutMs = Math.max(1, Math.min(Number(dependencies.timeoutMs) || CARGO_TIMEOUT_MS, 30000));
    const deadline = Date.now() + timeoutMs;
    let win;
    let loaded = false;
    let opened = false;
    let status = 'unavailable';
    try {
        const url = typeof provider.url === 'function' ? provider.url(request) : provider.url;
        if (!provider.allowUrl(url)) return { status, opened };
        const trackingSession = session.fromPartition('jk-importacoes-carga-' + carrier, { cache: false });
        trackingSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
        trackingSession.setPermissionCheckHandler(() => false);
        win = new BrowserWindow({
            parent: parent && !parent.isDestroyed() ? parent : undefined,
            title: 'Rastreamento de carga · ' + CARGO_PORTAL_NAMES[carrier],
            show: false,
            skipTaskbar: true,
            width: 1180,
            height: 820,
            autoHideMenuBar: true,
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
        const denyDownload = (event) => event.preventDefault();
        trackingSession.on('will-download', denyDownload);
        win.once('closed', () => {
            trackingSession.removeListener('will-download', denyDownload);
            if (shownCargoPortal === win) shownCargoPortal = null;
        });
        win.on('page-title-updated', (event) => event.preventDefault());
        const contents = win.webContents;
        contents.setWindowOpenHandler(() => ({ action: 'deny' }));
        const guardNavigation = (event, target) => {
            const targetUrl = typeof target === 'string' ? target : target?.url || event.url;
            if (!provider.allowUrl(targetUrl)) event.preventDefault();
        };
        contents.on('will-navigate', guardNavigation);
        contents.on('will-redirect', (event, target, _inPlace, isMainFrame) => {
            if (isMainFrame !== false && event.isMainFrame !== false) guardNavigation(event, target);
        });
        loaded = await loadCargoPortal(win, url, provider.allowUrl, deadline, provider.waitForAutomaticAccess);
        if (loaded && !win.isDestroyed()) {
            status = await readCargoPortalResult(contents, provider, request, deadline);
        }
    } catch (_err) {
        // Never expose a portal URL or tracking reference in an error.
    } finally {
        if (showPortal && loaded && win && !win.isDestroyed()) {
            try {
                if (provider.allowUrl(win.webContents.getURL())) {
                    safeClose(shownCargoPortal);
                    shownCargoPortal = win;
                    win.show();
                    opened = true;
                }
            } catch (_err) {}
        }
        if (!opened) safeClose(win);
    }
    return { status, opened };
}

async function searchPublicCargoTracking(payload = {}, dependencies = {}) {
    let request;
    try {
        request = normalizeTrackingRequest(payload);
    } catch (_err) {
        return { success: false, message: 'Informe uma referência válida para rastrear a carga.' };
    }
    const carrier = payload.carrier;
    if (carrier != null && !CARGO_CARRIERS.includes(carrier)) {
        return { success: false, message: 'Armador não disponível para consulta pública.' };
    }
    if (!dependencies.BrowserWindow || typeof dependencies.session?.fromPartition !== 'function') {
        return { success: false, message: 'O navegador interno do JK está indisponível.' };
    }
    const carriers = carrier ? [carrier] : CARGO_CARRIERS;
    const options = { ...dependencies, showPortal: !!carrier };
    const result = await Promise.all(carriers.map(async (code) => {
        if (code === 'cosco') {
            return trackCoscoShipment(request, { ...options, strictResult: true });
        }
        const provider = code === 'cma-cgm'
            ? {
                url: CMA_CGM_ORIGIN + CMA_CGM_TRACKING_PATH,
                allowUrl: isAllowedCmaCargoUrl,
                buildSubmitScript: buildCmaCargoSubmitScript,
                buildResultScript: buildCmaCargoResultScript,
                waitForAutomaticAccess: true,
            }
            : CARGO_FORM_PROVIDERS[code];
        return queryOfficialCargoPortal(code, provider, request, options);
    }));
    return {
        success: true,
        results: carriers.map((code, index) => ({ carrier: code, status: result[index].status })),
        opened: result.some((item) => item.opened),
    };
}

function isValidPublicVesselImo(imo) {
    if (!/^\d{7}$/.test(imo)) return false;
    const checksum = Array.from(imo.slice(0, 6))
        .reduce((sum, digit, index) => sum + Number(digit) * (7 - index), 0);
    return checksum % 10 === Number(imo[6]);
}

function wingoVesselMmsi(pathname) {
    return /^\/ship\/(?:[a-z0-9-]+-)?(\d{9})\/?$/i.exec(pathname)?.[1] || '';
}

function isAllowedPublicVesselUrl(value, origin, mmsi) {
    try {
        const url = new URL(value);
        if (url.origin !== origin || url.username || url.password) return false;
        if (origin === WINGO_MARINE_ORIGIN) {
            return !!mmsi && wingoVesselMmsi(url.pathname) === mmsi;
        }
        if (url.origin === MARITIME_OPTIMA_ORIGIN) {
            return /^\/public\/vessels\/pages\/imo:\d{7}\/mmsi:\d{9}\/[^/]+\.html$/
                .test(decodeURIComponent(url.pathname));
        }
        if (url.origin === VESSEL_FINDER_ORIGIN) {
            return url.pathname === '/'
                && (isValidPublicVesselImo(url.searchParams.get('imo') || '')
                    || /^\d{9}$/.test(url.searchParams.get('mmsi') || ''));
        }
        if (url.origin !== MY_SHIP_TRACKING_ORIGIN) return false;
        if (url.pathname === '/') return /^\d{9}$/.test(url.searchParams.get('mmsi') || '');
        return /^\/vessels(?:\/[^/]+-mmsi-\d{9}-imo-\d{1,7})?\/?$/.test(url.pathname);
    } catch (_err) {
        return false;
    }
}

async function loadPublicVesselPage(win, url, timeoutMs) {
    const contents = win.webContents;
    const expectedUrl = new URL(url);
    const wingoMmsi = expectedUrl.origin === WINGO_MARINE_ORIGIN ? wingoVesselMmsi(expectedUrl.pathname) : '';
    let onNavigate;
    let timeout;
    let opened = false;
    try {
        const committed = new Promise((resolve) => {
            onNavigate = (_event, pageUrl, status) => {
                if (!(status >= 200 && status < 400)
                    || !isAllowedPublicVesselUrl(pageUrl, expectedUrl.origin, wingoMmsi)) {
                    resolve(false);
                    return;
                }
                const currentUrl = new URL(pageUrl);
                resolve((!!wingoMmsi || currentUrl.pathname === expectedUrl.pathname)
                    && currentUrl.search === expectedUrl.search);
            };
            contents.once('did-navigate', onNavigate);
        });
        // The map can keep fetching tiles after the main document has opened.
        opened = await Promise.race([
            committed,
            win.loadURL(url).then(() => false),
            new Promise((resolve) => { timeout = setTimeout(() => resolve(false), timeoutMs); }),
        ]);
        return opened && !win.isDestroyed();
    } catch (_err) {
        return false;
    } finally {
        clearTimeout(timeout);
        if (!contents.isDestroyed()) {
            contents.removeListener('did-navigate', onNavigate);
            if (!opened) contents.stop();
        }
    }
}

function normalizePublicVesselNumber(value) {
    return typeof value === 'string' || Number.isSafeInteger(value) ? String(value).trim() : '';
}

async function openPublicVesselPosition(payload = {}, dependencies = {}) {
    const imo = normalizePublicVesselNumber(payload?.imo);
    const mmsi = normalizePublicVesselNumber(payload?.mmsi);
    if ((!imo && !mmsi) || (imo && !isValidPublicVesselImo(imo)) || (mmsi && !/^\d{9}$/.test(mmsi))) {
        return { success: false, message: 'Informe um IMO ou MMSI válido para consultar o navio.' };
    }

    const { BrowserWindow, session, parent, timeoutMs = PUBLIC_VESSEL_TIMEOUT_MS } = dependencies;
    if (!BrowserWindow || typeof session?.fromPartition !== 'function') {
        return { success: false, message: 'O navegador interno do JK está indisponível.' };
    }
    const targets = [];
    if (payload?.mapa === true) {
        const map = new URL('/', VESSEL_FINDER_ORIGIN);
        map.searchParams.set(imo ? 'imo' : 'mmsi', imo || mmsi);
        targets.push({ source: 'VesselFinder', view: 'mapa', url: map.href });
        if (mmsi) {
            targets.push({ source: 'Wingo Marine', view: 'mapa', url: WINGO_MARINE_ORIGIN + '/ship/' + mmsi });
        }
    }
    if (imo && mmsi) {
        targets.push({
            source: 'Maritime Optima',
            view: 'ficha',
            url: MARITIME_OPTIMA_ORIGIN + '/public/vessels/pages/imo:' + imo + '/mmsi:' + mmsi + '/vessel.html',
        });
    }
    const search = new URL('/vessels', MY_SHIP_TRACKING_ORIGIN);
    search.searchParams.set('name', imo || mmsi);
    targets.push({ source: 'MyShipTracking', view: 'busca', url: search.href });

    safeClose(publicVesselWindow);
    let win;
    let shown = false;
    try {
        const trackingSession = session.fromPartition(PUBLIC_VESSEL_PARTITION, { cache: false });
        trackingSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
        trackingSession.setPermissionCheckHandler(() => false);

        win = new BrowserWindow({
            parent: parent && !parent.isDestroyed() ? parent : undefined,
            title: 'Posição AIS do navio',
            show: false,
            skipTaskbar: true,
            width: 1120,
            height: 760,
            autoHideMenuBar: true,
            webPreferences: {
                session: trackingSession,
                sandbox: true,
                contextIsolation: true,
                nodeIntegration: false,
                webSecurity: true,
                allowRunningInsecureContent: false,
                webviewTag: false,
            },
        });
        publicVesselWindow = win;
        const denyDownload = (event) => event.preventDefault();
        trackingSession.on('will-download', denyDownload);
        win.once('closed', () => {
            trackingSession.removeListener('will-download', denyDownload);
            if (publicVesselWindow === win) publicVesselWindow = null;
        });
        win.on('page-title-updated', (event) => event.preventDefault());
        win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
        let activeOrigin;
        const guardNavigation = (event, url) => {
            if (!isAllowedPublicVesselUrl(url ?? event.url, activeOrigin, mmsi)) event.preventDefault();
        };
        win.webContents.on('will-navigate', guardNavigation);
        win.webContents.on('will-redirect', (event, url, _inPlace, isMainFrame) => {
            if (isMainFrame !== false && event.isMainFrame !== false) guardNavigation(event, url);
        });

        const deadline = Date.now() + timeoutMs;
        for (const target of targets) {
            const remaining = deadline - Date.now();
            if (win.isDestroyed() || remaining <= 0) break;
            activeOrigin = new URL(target.url).origin;
            if (await loadPublicVesselPage(win, target.url, Math.min(remaining, PUBLIC_VESSEL_PAGE_TIMEOUT_MS))) {
                const title = target.view === 'mapa' ? 'Mapa AIS do navio' : 'Posição AIS do navio';
                win.setTitle(title + ' · ' + target.source);
                win.show();
                shown = true;
                return { success: true, source: target.source, view: target.view };
            }
        }
    } catch (_err) {
        // Falhas do Chromium não devem expor endereços ou argumentos da consulta.
    } finally {
        if (!shown) safeClose(win);
    }
    return { success: false, message: 'Não foi possível abrir a consulta pública do navio. Tente novamente.' };
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
    openPublicVesselPosition,
    searchPublicCargoTracking,
};
