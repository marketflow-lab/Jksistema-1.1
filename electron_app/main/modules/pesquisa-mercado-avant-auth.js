'use strict';

const { mercadoLivreAuthScript, mercadoLivreRouteAuthReason } = require('../../../static/pesquisa-mercado/page-scripts');
const AUTH_ORIGIN = 'https://auth.avantprocloud.com.br';
function normalizeEmail(value) {
    if (typeof value !== 'string') return '';
    const email = value.trim();
    return email.length <= 254 && /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$/.test(email) ? email : '';
}
function marketPageBlockReason(value) {
    try {
        const url = new URL(value);
        if (url.protocol === 'https:' && /(^|\.)mercadolivre\.com\.br$/i.test(url.hostname)
            && !url.username && !url.password && (!url.port || url.port === '443')) {
            return mercadoLivreRouteAuthReason(url.pathname);
        }
    } catch (_error) {}
    return 'ml_login_required';
}

function allowedPage(value) { return !marketPageBlockReason(value); }

// Only extension-owned controls can be clicked.
function pageSession(action, rankingOnly, mlAuthReason) {
    if (location.protocol !== 'https:' || !/(^|\.)mercadolivre\.com\.br$/i.test(location.hostname)) return { state: 'unavailable' };
    const visible = node => {
        if (!node || !node.isConnected) return false;
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
    };
    const nodes = selector => Array.from(document.querySelectorAll(selector));
    const first = selector => nodes(selector).find(visible);
    if (mlAuthReason) return { state: 'ml_login', reason: mlAuthReason };
    const authFrame = first('#avantpro-login-modal #authMfeIframe');
    let frameAllowed = false;
    if (authFrame) {
        try { frameAllowed = new URL(authFrame.src).origin === 'https://auth.avantprocloud.com.br'; } catch (_error) {}
    }
    // Menu entries are conditionally rendered but hidden until CSS hover.
    const logout = document.querySelector('.avantpro-menu #btnLogoutMenu, #sideMenuLogout');
    const rankingLogin = nodes('.tendencias-extra .logged-out-tendency button.avantpro-btn.avantpro-btn-primary')
        .find(node => visible(node) && !node.disabled && /^login$/i.test(node.textContent.trim()));
    const login = rankingLogin || (!rankingOnly && (document.querySelector('.avantpro-menu #btnLoginMenu')
        || first('#sideMenuLogin, .avantpro-logged-out-combo-cta, .avantpro-logged-out-modern-cta')));
    const menu = first('.avantpro-menu .avantpro-menu-icon, .avantpro-menu .avantpro-menu-icon-html, .avantpro-menu-icon, .avantpro-menu-icon-html');
    if (action === 'close-menu') {
        if (menu && (logout || first('#btnLoginMenu'))) menu.click();
        return { state: 'closed' };
    }
    if (authFrame) return { state: frameAllowed ? 'login_form' : 'untrusted_form' };
    if (logout) return { state: 'authenticated' };
    if (login) {
        if (action === 'open-login') login.click();
        return { state: 'logged_out', clicked: action === 'open-login' };
    }
    if (action === 'open-menu' && menu) { menu.click(); return { state: 'unknown', menuOpened: true }; }
    return { state: 'unknown' };
}
const pageScript = (action, rankingOnly = false) => `(${pageSession.toString()})(${JSON.stringify(action)},${JSON.stringify(rankingOnly)},${mercadoLivreAuthScript()})`;

function submitLogin(email) {
    if (location.origin !== 'https://auth.avantprocloud.com.br' || location.pathname !== '/') return { blocked: true };
    const root = document.querySelector('#avantauth-root');
    // The auth iframe can commit before its application has rendered the form.
    if (!root) return { ready: false };
    if (root.querySelector('input[name="name"], input[name="phone"]')) return { blocked: true };
    const heading = root.querySelector('h1');
    const title = heading && String(heading.textContent || '').trim();
    if (!title) return { ready: false };
    if (!/^iniciar sess[aã]o$/i.test(title)) return { blocked: true };
    const visible = node => {
        if (!node || !node.isConnected) return false;
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
    };
    if (Array.from(document.querySelectorAll('input[type="password"], input[autocomplete="one-time-code"], iframe[src*="captcha"], [data-testid="captcha"]')).some(visible)) return { manual: true };
    const input = Array.from(root.querySelectorAll('form input#email[type="email"][name="email"][autocomplete="email"]')).find(visible);
    const button = Array.from(root.querySelectorAll('form button[type="submit"]')).find(node => visible(node)
        && /^(?:login|entrar|iniciar sess[aã]o)$/i.test(String(node.textContent || node.value || '').trim()));
    if (!input || !button) return { ready: false };
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(input, email);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    if (button.disabled) return { ready: false };
    button.click();
    return { submitted: true };
}
const loginScript = email => `(${submitLogin.toString()})(${JSON.stringify(email)})`;
function aborted() { const error = new Error('Pesquisa interrompida.'); error.name = 'AbortError'; return error; }
function wait(ms, signal) {
    return new Promise((resolve, reject) => {
        if (signal && signal.aborted) return reject(aborted());
        const onAbort = () => { clearTimeout(timer); reject(aborted()); };
        const timer = setTimeout(() => { if (signal) signal.removeEventListener('abort', onAbort); resolve(); }, ms);
        if (signal) signal.addEventListener('abort', onAbort, { once: true });
    });
}

class AvantSession {
    constructor(options) {
        this.getContents = options.getContents;
        this.getEmail = options.getEmail;
        this.getContext = options.getContext || (() => 0);
        this.timeoutMs = options.timeoutMs || 40000;
        this.probeMs = options.probeMs || 10000;
        this.pollMs = options.pollMs || 350;
        this.pending = null;
        this.failure = null;
    }
    ensure(options = {}) {
        if (this.pending) return this.pending;
        const task = this.check(options);
        this.pending = task;
        task.finally(() => { if (this.pending === task) this.pending = null; }).catch(() => {});
        return task;
    }
    async check({ signal, rankingOnly = false } = {}) {
        const contents = this.getContents();
        const context = this.getContext();
        const deadline = Date.now() + this.timeoutMs;
        const guard = () => {
            if (signal && signal.aborted || context !== this.getContext()) throw aborted();
            if (!contents || contents.isDestroyed() || contents !== this.getContents()) throw aborted();
        };
        const fail = (reason, attempted = false) => ({ authenticated: false, attempted, reason });
        const execute = async (frame, code) => {
            guard();
            let timer, onAbort;
            const interrupt = new Promise((_resolve, reject) => {
                timer = setTimeout(() => reject(new Error('avant_timeout')), Math.max(1, Math.min(8000, deadline - Date.now())));
                onAbort = () => reject(aborted());
                if (signal) signal.addEventListener('abort', onAbort, { once: true });
            });
            try { const result = await Promise.race([frame.executeJavaScript(code, true), interrupt]); guard(); return result || {}; }
            finally { clearTimeout(timer); if (signal) signal.removeEventListener('abort', onAbort); }
        };
        const executePage = async action => {
            while (true) {
                guard();
                const blockReason = marketPageBlockReason(contents.getURL());
                if (blockReason) return { state: 'ml_login', reason: blockReason };
                if (Date.now() >= deadline) throw new Error('avant_timeout');
                try { return await execute(contents, pageScript(action, rankingOnly)); }
                catch (error) {
                    if (error.name === 'AbortError') throw error;
                    guard();
                    // Reloading after login can destroy the parent document too.
                    // Retry its controls only; submitting the auth frame is never replayed.
                    const navigationInterrupted = /execution context was destroyed|cannot find context with specified id|(?:render )?frame (?:was |has been )?(?:disposed|detached|destroyed)/i.test(String(error.message || ''))
                        || contents.isLoadingMainFrame();
                    if (!navigationInterrupted || Date.now() >= deadline) throw error;
                    await wait(Math.min(this.pollMs, Math.max(1, deadline - Date.now())), signal);
                }
            }
        };
        let attempted = false, openedMenu = false, menuTried = false, loginOpened = false;
        let formDeadline = 0;
        try {
            guard();
            const initialBlock = marketPageBlockReason(contents.getURL());
            if (initialBlock) return fail(initialBlock);
            const initialDeadline = Date.now() + (rankingOnly ? Math.min(this.probeMs, 3000) : this.probeMs);
            while (Date.now() < deadline) {
                guard();
                const blockReason = marketPageBlockReason(contents.getURL());
                if (blockReason) return fail(blockReason, attempted);
                if (contents.isLoadingMainFrame()) { menuTried = false; await wait(this.pollMs, signal); continue; }
                const state = await executePage('probe');
                if (state.state === 'authenticated') {
                    if (openedMenu) {
                        await executePage('close-menu');
                        openedMenu = false;
                        continue; // Confirm the current document after menu cleanup too.
                    }
                    this.failure = null;
                    return { authenticated: true, attempted };
                }
                if (state.state === 'ml_login') return fail(state.reason || 'ml_login_required', attempted);
                if (state.state === 'untrusted_form') return fail('avant_form_not_allowed', attempted);
                if (state.state === 'logged_out' || state.state === 'login_form') {
                    const email = normalizeEmail(this.getEmail());
                    if (!email) return fail('avant_email_missing');
                    if (!attempted && this.failure && this.failure.context === context && this.failure.until > Date.now()) return fail(this.failure.reason, false);
                    if (state.state === 'logged_out') {
                        if (!loginOpened) { await executePage('open-login'); loginOpened = true; }
                    } else if (!attempted) {
                        loginOpened = true; // An existing auth form also gets the full loading deadline.
                        const main = contents.mainFrame;
                        const frames = main && main.framesInSubtree || [];
                        const frame = frames.find(candidate => {
                            try { return candidate !== main && (!candidate.parent || candidate.parent === main)
                                && new URL(candidate.url).origin === AUTH_ORIGIN; } catch (_error) { return false; }
                        });
                        if (frame) {
                            // The frame checks its own origin again at execution time.
                            let result;
                            try { result = await execute(frame, loginScript(email)); }
                            catch (error) {
                                if (error.name === 'AbortError') throw error;
                                // A successful submit can destroy the auth frame before IPC replies.
                                // Confirm the fresh page without sending the account a second time.
                                attempted = true; menuTried = false; formDeadline = Date.now() + 22000;
                                continue;
                            }
                            if (result.manual || result.blocked) return fail('avant_manual_login_required');
                            if (result.submitted) { attempted = true; menuTried = false; formDeadline = Date.now() + 22000; }
                        }
                    }
                } else if (state.state === 'unknown' && !rankingOnly && !menuTried) {
                    const result = await executePage('open-menu');
                    menuTried = !!result.menuOpened;
                    openedMenu = openedMenu || menuTried;
                }
                if (!attempted && !loginOpened && Date.now() >= initialDeadline) {
                    return fail(rankingOnly ? 'avant_ranking_login_not_shown' : 'avant_session_unconfirmed');
                }
                if (attempted && Date.now() >= formDeadline) break;
                await wait(this.pollMs, signal);
            }
            const reason = 'avant_login_not_confirmed';
            if (attempted) this.failure = { context, until: Date.now() + 60000, reason };
            return fail(reason, attempted);
        } catch (error) {
            if (error.name === 'AbortError') throw error;
            const reason = 'avant_login_not_confirmed';
            if (attempted) this.failure = { context, until: Date.now() + 60000, reason };
            return fail(reason, attempted);
        }
    }
}
module.exports = { AvantSession, normalizeEmail, allowedPage, AUTH_ORIGIN, pageScript, loginScript };
