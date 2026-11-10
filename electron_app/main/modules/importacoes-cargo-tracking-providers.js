'use strict';

const HMM_TRACKING_URL = 'https://www.hmm21.com/e-service/general/trackNTrace/TrackNTrace.do';
const YANG_MING_TRACKING_URL = 'https://www.yangming.com/en/esolution/tracking/cargo_tracking';
const HMM_INPUTS = {
    BILLOFLADING: 'input[name="srchBlNo1"]',
    BOOKING: 'input[name="srchBkgNo1"]',
    CONTAINER: 'input[name="srchCntrNo1"]',
};

function isTrackingPage(value, trackingUrl) {
    try {
        const actual = new URL(value);
        const expected = new URL(trackingUrl);
        return actual.origin === expected.origin && actual.pathname === expected.pathname
            && !actual.username && !actual.password;
    } catch (_err) {
        return false;
    }
}

function isVisible(element) {
    if (!element || !element.isConnected || element.closest('[hidden], [aria-hidden="true"], [inert]')) {
        return false;
    }
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    if (typeof element.checkVisibility === 'function') {
        return element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
    }
    return element.getClientRects().length > 0 && getComputedStyle(element).visibility === 'visible';
}

function isBlocked(visible) {
    const challenge = document.querySelectorAll(
        '#challenge-stage, #cf-challenge-running, iframe[src*="captcha" i], [data-sitekey], input[type="password"]'
    );
    if (Array.from(challenge).some(visible)) return true;
    const heading = [document.title, ...Array.from(document.querySelectorAll('h1, h2, [role="alert"]'))
        .filter(visible)
        .map((element) => element.innerText)];
    return heading.some((text) => /\b(?:403 Forbidden|Access Denied|Verify (?:that )?you are human|Just a moment|Please (?:log|sign) in to (?:continue|access))\b/i.test(text));
}

function submitTrackingForm(config, allowPage, visible, blocked) {
    if (!allowPage(location.href, config.url) || blocked(visible) || !config.selector) return false;
    const inputs = Array.from(document.querySelectorAll(config.selector)).filter(visible);
    const buttons = Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"]'))
        .filter((button) => visible(button)
            && String(button.innerText || button.value || '').trim() === config.button);
    if (inputs.length !== 1 || buttons.length !== 1) return false;

    const [input] = inputs;
    const [button] = buttons;
    if (input.matches(':disabled, [readonly], [aria-disabled="true"]')
        || (input.maxLength >= 0 && config.reference.length > input.maxLength)) return false;

    if (input.value !== config.reference) {
        const prototype = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input, config.reference);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (input.value !== config.reference || !visible(button)
        || button.matches(':disabled, [aria-disabled="true"]')) return false;

    button.click();
    return true;
}

function inspectTrackingResult(config, allowPage, visible, blocked) {
    if (!allowPage(location.href, config.url) || blocked(visible)) return 'unavailable';
    if (!document.body) return 'waiting';

    const text = (element) => String(element.innerText || '').replace(/\s+/g, ' ').trim();
    const label = (value) => value.toUpperCase().replace(/[.:#]/g, '').replace(/\s+/g, ' ').trim();
    const referenceLabel = {
        BILLOFLADING: /^(?:B\/?L|BILL OF LADING)(?:\s+(?:NO|NUMBER))?$/,
        BOOKING: /^(?:BOOKING|BKG|S\/O)(?:\s+(?:NO|NUMBER))?(?:\s+\(S\/O NO\))?$/,
        CONTAINER: /^(?:CONTAINER|CNTR)(?:\s+(?:NO|NUMBER))?$/,
    }[config.type];
    if (!referenceLabel) return 'unavailable';

    const shipmentLabel = /^(?:VESSEL(?:\s+NAME|\s+VOY(?:AGE)?(?:\s+NO)?|[ /]+VOYAGE)?|VSL[ /]+VOY|(?:PORT|PLACE) OF (?:RECEIPT|LOADING|DISCHARGE|DELIVERY)|RECEIPT|LOADING|DISCHARGE|DELIVERY|POL|POD|ORIGIN|DESTINATION|LATEST (?:EVENT|STATUS)|ON BOARD DATE|ETD|ETA|CONTAINER NO|CNTR NO)$/;
    const hasReference = (key, value) => referenceLabel.test(label(key))
        && config.references.includes(value.toUpperCase());
    const hasShipmentField = (key, value) => {
        const field = label(key);
        if (!shipmentLabel.test(field) || referenceLabel.test(field)
            || !/[A-Z0-9]/i.test(value) || config.references.includes(value.toUpperCase())
            || /^(?:N\/?A|NONE|NULL|UNKNOWN|TBN|INVALID|NOT (?:AVAILABLE|FOUND)|NO (?:DATA|RESULTS?|RECORDS?))(?:\b|$)/i.test(value)) {
            return false;
        }
        if (/^(?:CONTAINER|CNTR) NO$/.test(field)) return /^[A-Z]{4}\d{7}$/i.test(value);
        if (/^(?:ON BOARD DATE|ETD|ETA)$/.test(field)) return /\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}\b/i.test(value);
        return true;
    };
    const isMatchingRecord = (fields) => {
        const identities = fields.filter(([key, value]) => referenceLabel.test(label(key)) && value);
        return identities.length === 1 && hasReference(...identities[0])
            && fields.some(([key, value]) => hasShipmentField(key, value));
    };

    const body = document.body.innerText;
    const invalidReference = {
        BILLOFLADING: /\bB\/L\s+(?:No\.?|number)\s+is\s+invalid\b/i,
        BOOKING: /\b(?:Booking|BKG)\s+(?:No\.?|number)\s+is\s+invalid\b/i,
        CONTAINER: /\b(?:Container|CNTR)\s+(?:No\.?|number)\s+is\s+invalid\b/i,
    }[config.type];
    if (invalidReference.test(body)) return 'not_found';
    if (/\b(?:captcha|verification code)\b.{0,50}\b(?:required|incorrect|failed)\b|\b(?:please|you must) (?:log|sign) in to (?:continue|access|view|track)\b/i.test(body)) {
        return 'unavailable';
    }

    if (config.isYangMing) {
        const reference = config.references[0];
        const tabSuffix = '-tab-empty-' + reference;
        const tab = Array.from(document.querySelectorAll('[role="tablist"] [role="tab"][aria-selected="true"]'))
            .find((element) => visible(element) && element.id.endsWith(tabSuffix)
                && text(element).replace(/\s*[\u00D7\u2715]$/, '').toUpperCase() === reference);
        if (tab) {
            const panelId = tab.id.slice(0, -tabSuffix.length) + '-tabpanel-empty-' + reference;
            const controlledId = tab.getAttribute('aria-controls');
            const panel = document.getElementById(panelId);
            if ((!controlledId || controlledId === panelId) && panel?.getAttribute('role') === 'tabpanel'
                && visible(panel) && /\bSorry,\s*we can['\u2019]t identify your input\./i.test(text(panel))) {
                return 'not_found';
            }
        }
    }

    const resultRoot = config.resultSelector ? document.querySelector(config.resultSelector) : document;
    if (!resultRoot) return 'waiting';
    for (const table of Array.from(resultRoot.querySelectorAll('table, [role="table"], [role="grid"]')).filter(visible)) {
        const rows = Array.from(table.querySelectorAll('tr, [role="row"]'))
            .filter((row) => visible(row) && row.closest('table, [role="table"], [role="grid"]') === table)
            .map((row) => Array.from(row.children).filter((cell) => visible(cell)
                && cell.matches('th, td, [role="columnheader"], [role="rowheader"], [role="cell"], [role="gridcell"]')));

        if (isMatchingRecord(rows.filter((row) => row.length === 2).map((row) => row.map(text)))) {
            return 'found';
        }

        const headerIndex = rows.findIndex((row) => row.length > 1
            && row.every((cell) => cell.matches('th, [role="columnheader"]'))
            && row.some((cell) => referenceLabel.test(label(text(cell)))));
        if (headerIndex === -1) continue;
        const keys = rows[headerIndex].map(text);
        if (rows.slice(headerIndex + 1).some((row) => row.length === keys.length
            && row.every((cell) => cell.matches('td, [role="cell"], [role="gridcell"]'))
            && isMatchingRecord(keys.map((key, index) => [key, text(row[index])])))) {
            return 'found';
        }
    }

    const regions = config.resultSelector ? [resultRoot] : Array.from(resultRoot.querySelectorAll(
        'section, article, [role="tabpanel"], [id*="result" i], [class*="result" i]'
    )).filter((region) => Array.from(region.querySelectorAll('h1, h2, h3, h4, [role="heading"]'))
        .some((element) => visible(element) && /^(?:Result of Tracking\b|Tracking Results?$)/i.test(text(element))));
    for (const region of regions.filter(visible)) {
        const fields = Array.from(region.querySelectorAll('dt, label, th, span, strong'))
            .filter((element) => visible(element) && visible(element.nextElementSibling)
                && !element.nextElementSibling.matches('input, textarea, select, [contenteditable]'))
            .map((element) => [text(element), text(element.nextElementSibling)]);
        const lines = region.innerText.split(/\n/).map((line) => line.trim()).filter(Boolean);
        for (const line of lines) {
            const separator = line.indexOf(':');
            if (separator > 0) fields.push([line.slice(0, separator).trim(), line.slice(separator + 1).trim()]);
        }
        const uniqueFields = fields.filter(([key, value], index) => fields.findIndex(([otherKey, otherValue]) =>
            label(otherKey) === label(key) && otherValue.toUpperCase() === value.toUpperCase()) === index);
        if (isMatchingRecord(uniqueFields)) return 'found';
    }

    if (/\b(?:No (?:matching |search )?(?:results?|records?|data) (?:was |were )?found|there is no (?:tracking )?data)\b/i.test(body)) {
        return 'not_found';
    }
    return 'waiting';
}

function buildBrowserScript(run, config) {
    return '(' + run.toString() + ')(' + JSON.stringify(config) + ', '
        + isTrackingPage.toString() + ', ' + isVisible.toString() + ', ' + isBlocked.toString() + ')';
}

function createProvider(url, inputSelector, button, normalizeReference = (_type, reference) => reference) {
    return {
        url,
        allowUrl: (value) => isTrackingPage(value, url),
        buildSubmitScript({ type, reference }) {
            return buildBrowserScript(submitTrackingForm, {
                url,
                selector: inputSelector(type),
                button,
                reference: normalizeReference(type, reference),
            });
        },
        buildResultScript({ type, reference }) {
            const localReference = normalizeReference(type, reference);
            const references = [reference, localReference];
            if (url === HMM_TRACKING_URL && type === 'BILLOFLADING' && /^[A-Z0-9-]{11,12}$/.test(localReference)) {
                references.push('HDMU' + localReference);
            }
            const resultSelector = url === HMM_TRACKING_URL ? '#trackingInfomationDateResultTable' : null;
            return buildBrowserScript(inspectTrackingResult, {
                url, type, references, resultSelector, isYangMing: url === YANG_MING_TRACKING_URL,
            });
        },
    };
}

function normalizeHmmReference(type, reference) {
    // HMM's form takes the B/L number without its optional HDMU carrier code.
    if (type === 'BILLOFLADING' && /^HDMU[A-Z0-9-]{11,12}$/.test(reference)) {
        return reference.slice(4);
    }
    return reference;
}

const CARGO_FORM_PROVIDERS = {
    hmm: createProvider(HMM_TRACKING_URL, (type) => HMM_INPUTS[type], 'Retrieve', normalizeHmmReference),
    'yang-ming': createProvider(YANG_MING_TRACKING_URL, () => 'textarea', 'Search'),
};

module.exports = { CARGO_FORM_PROVIDERS };
