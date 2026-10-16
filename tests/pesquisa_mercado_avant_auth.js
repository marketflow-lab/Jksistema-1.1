'use strict';

const assert = require('assert');
const { chromium } = require('playwright');
const { AvantSession, normalizeEmail, allowedPage, pageScript, loginScript } = require('../electron_app/main/modules/pesquisa-mercado-avant-auth');

const MARKET_URL = 'https://lista.mercadolivre.com.br/ferramentas';
const AUTH_URL = 'https://auth.avantprocloud.com.br/?source=extension&extension=avantpro-ml&language=pt-BR';
const FIXTURE_EMAIL = 'researcher@account.example';

function marketHtml(options) {
    const state = options.loggedIn ? 'logged' : 'logout';
    return `<!doctype html><meta charset="UTF-8"><style>
      .avantpro-menu-content { visibility:hidden; opacity:0; transform:scale(0); }
      .avantpro-menu-icon { width:30px; height:30px; }
      iframe { width:440px; height:400px; }
      </style><div id="fixture"></div><script>
      const settings = ${JSON.stringify(options)};
      window.fixture = { submits:0, menuClicks:0, loginClicks:0 };
      function menu(state) {
        document.querySelector('#fixture').innerHTML = '<div class="avantpro-menu"><div class="avantpro-menu-surface"><div class="avantpro-menu-icon">A</div><div class="avantpro-menu-content">' +
          (state === 'logged' ? '<div id="btnLogoutMenu">Sair do Avantpro</div><small class="avantpro-menu-extra-info-version"><span>Assinante | </span>Versão 7.32.0</small>' : '<div id="btnLoginMenu">Fazer Login</div><small class="avantpro-menu-extra-info-version">Versão 7.32.0</small>') + '</div></div></div>';
        document.querySelector('.avantpro-menu-icon').onclick = () => { window.fixture.menuClicks++; };
        const login = document.querySelector('#btnLoginMenu');
        if (login) login.onclick = openLogin;
      }
      function openLogin() {
        if (document.querySelector('#avantpro-login-modal')) return;
        window.fixture.loginClicks++;
        const modal = document.createElement('div'); modal.id = 'avantpro-login-modal';
        const frame = document.createElement('iframe'); frame.id = 'authMfeIframe'; frame.name = 'authMfeIframe';
        frame.src = settings.authUrl || ${JSON.stringify(AUTH_URL)};
        modal.appendChild(frame); document.body.appendChild(modal);
      }
      window.addEventListener('message', event => {
        const frame = document.querySelector('#authMfeIframe');
        if (!frame || event.source !== frame.contentWindow || event.origin !== 'https://auth.avantprocloud.com.br') return;
        if (event.data.kind === 'fixture-submitted') window.fixture.submits++;
        if (event.data.kind === 'fixture-confirmed') { frame.parentElement.remove(); menu('logged'); }
      });
      if (!settings.unknown) menu(${JSON.stringify(state)});
      if (settings.marketCaptcha) { const captcha = document.createElement('div'); captcha.dataset.testid = 'captcha'; captcha.textContent = 'Captcha'; document.body.appendChild(captcha); }
      if (settings.beginPopup) openLogin();
      </script>`;
}

function authHtml(options) {
    const challenge = options.challenge === 'password' ? '<input type="password">'
        : options.challenge === 'otp' ? '<input autocomplete="one-time-code">'
        : options.challenge === 'captcha' ? '<div data-testid="captcha">Captcha</div>' : '';
    return `<!doctype html><meta charset="UTF-8"><div id="avantauth-root"><form>
      <h1>${options.registration ? 'Criar conta' : 'Iniciar Sessão'}</h1>
      ${options.registration ? '<input name="name">' : ''}
      <label for="email">Email</label><input id="email" type="email" name="email" autocomplete="email">
      ${challenge}<button type="submit" ${options.disabled || options.enableDelay ? 'disabled' : ''}>Login</button>
      </form></div><script>
      const settings = ${JSON.stringify(options)};
      const input = document.querySelector('#email'); const button = document.querySelector('button');
      let scheduled = false;
      input.addEventListener('input', () => {
        if (settings.enableDelay && !scheduled) { scheduled = true; setTimeout(() => { button.disabled = false; }, settings.enableDelay); }
      });
      document.querySelector('form').addEventListener('submit', event => {
        event.preventDefault(); button.disabled = true; button.textContent = 'Entrando...';
        parent.postMessage({kind:'fixture-submitted'}, 'https://lista.mercadolivre.com.br');
        if (settings.success) setTimeout(() => parent.postMessage({kind:'fixture-confirmed'}, 'https://lista.mercadolivre.com.br'), settings.confirmDelay || 10);
      });
      </script>`;
}

async function createFixture(browser, options = {}) {
    const context = await browser.newContext();
    await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.hostname === 'lista.mercadolivre.com.br') return route.fulfill({ contentType:'text/html', body:marketHtml(options) });
        if (url.hostname === 'auth.avantprocloud.com.br' || url.hostname === 'auth.avantprocloud.com.br.evil.example') return route.fulfill({ contentType:'text/html', body:authHtml(options) });
        return route.fulfill({ status:404, body:'' });
    });
    const page = await context.newPage();
    await page.goto(options.marketUrl || MARKET_URL);
    let contextVersion = 1;
    let suppliedEmail = options.email === undefined ? FIXTURE_EMAIL : options.email;
    const wrappers = new Map();
    const stats = { frameExecutions:0, pageExecutions:0 };
    function wrap(frame) {
        if (!wrappers.has(frame)) {
            const item = {
                get url() { return frame.url(); },
                get parent() { return frame.parentFrame() ? wrap(frame.parentFrame()) : null; },
                get framesInSubtree() { return page.frames().filter(candidate => { let current = candidate; while (current) { if (current === frame) return true; current = current.parentFrame(); } return false; }).map(wrap); },
                async executeJavaScript(code) {
                    stats.frameExecutions++;
                    const result = await frame.evaluate(source => (0, eval)(source), code);
                    if (options.rejectAfterSubmit && result && result.submitted) throw new Error('Execution context was destroyed during authentication reload.');
                    return result;
                }
            };
            wrappers.set(frame, item);
        }
        return wrappers.get(frame);
    }
    const contents = {
        getURL:() => options.reportedUrl || page.url(),
        isDestroyed:() => page.isClosed(),
        isLoadingMainFrame:() => false,
        get mainFrame() { return wrap(page.mainFrame()); },
        async executeJavaScript(code) { stats.pageExecutions++; return page.evaluate(source => (0, eval)(source), code); }
    };
    const session = new AvantSession({ getContents:() => contents, getEmail:() => suppliedEmail, getContext:() => contextVersion,
        timeoutMs:options.timeoutMs || 260, probeMs:options.probeMs || 40, pollMs:5 });
    return { page, session, contents, stats, close:() => context.close(), switchContext:() => { contextVersion++; }, setEmail:value => { suppliedEmail = value; },
        submits:() => page.evaluate(() => window.fixture.submits), probe:() => contents.executeJavaScript(pageScript('probe')) };
}

async function until(check, timeout = 1000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) { if (await check()) return; await new Promise(resolve => setTimeout(resolve, 5)); }
    throw new Error('Fixture condition did not complete.');
}

async function main() {
    assert.equal(normalizeEmail(` ${FIXTURE_EMAIL} `), FIXTURE_EMAIL);
    for (const value of ['invalid', 'a@b', 'bad\n@account.example', 'a@evil.example/path', {}, 'a'.repeat(255) + '@account.example']) assert.equal(normalizeEmail(value), '');
    assert(allowedPage(MARKET_URL));
    for (const url of ['http://lista.mercadolivre.com.br/a', 'https://lista.mercadolivre.com.br.evil.example/a', 'https://u:p@lista.mercadolivre.com.br/a', 'https://lista.mercadolivre.com.br:8443/a', 'https://www.mercadolivre.com.br/jms/mlb/lgz/login', 'https://www.mercadolivre.com.br/account-verification', 'https://www.mercadolivre.com.br/challenge']) assert.equal(allowedPage(url), false);

    const browser = await chromium.launch({ headless:true });
    let cases = 0;
    async function test(options, operation) {
        const fixture = await createFixture(browser, options);
        try { await operation(fixture); cases++; } finally { await fixture.close(); }
    }
    try {
        await test({ loggedIn:true }, async f => {
            assert.equal((await f.probe()).state, 'authenticated', 'Hidden authenticated menu must be detected.');
            assert.deepEqual(await f.session.ensure(), { authenticated:true, attempted:false });
            assert.equal(await f.submits(), 0); assert.equal(f.stats.frameExecutions, 0);
        });
        await test({ loggedIn:true, timeoutMs:1000 }, async f => {
            const execute = f.contents.executeJavaScript;
            let interrupted = false;
            f.contents.executeJavaScript = async code => {
                if (!interrupted && code === pageScript('probe')) {
                    interrupted = true;
                    await f.page.reload();
                    throw new Error('Execution context was destroyed during page reload.');
                }
                return execute(code);
            };
            assert.deepEqual(await f.session.ensure(), { authenticated:true, attempted:false });
            assert(interrupted, 'The parent document must actually reload during confirmation.');
            assert.equal(await f.submits(), 0, 'A confirmed session does not require another login.');
        });
        await test({ success:true, beginPopup:true, confirmDelay:30, timeoutMs:1000 }, async f => {
            const execute = f.contents.executeJavaScript;
            let interrupted = false;
            f.contents.executeJavaScript = async code => {
                if (!interrupted && f.stats.frameExecutions > 0 && code === pageScript('probe')) {
                    interrupted = true;
                    throw new Error('Render frame was disposed before WebFrameMain could be accessed');
                }
                return execute(code);
            };
            assert.deepEqual(await f.session.ensure(), { authenticated:true, attempted:true });
            assert(interrupted);
            assert.equal(await f.submits(), 1, 'Lost parent confirmation must never resubmit the login.');
            assert.equal(f.stats.frameExecutions, 1);
        });
        await test({ loggedIn:true, timeoutMs:80 }, async f => {
            let probes = 0;
            f.contents.executeJavaScript = async () => {
                probes++;
                throw new Error('Execution context was destroyed');
            };
            const started = Date.now();
            const result = await f.session.ensure();
            assert.equal(result.authenticated, false);
            assert.equal(result.reason, 'avant_login_not_confirmed');
            assert(probes > 1, 'A temporary document failure must be checked again.');
            assert(Date.now() - started < 1000, 'Persistent navigation errors must keep the existing deadline.');
            assert.equal(f.stats.frameExecutions, 0);
        });
        await test({ loggedIn:true }, async f => {
            let probes = 0;
            f.contents.executeJavaScript = async () => { probes++; throw new Error('Unexpected script failure'); };
            assert.equal((await f.session.ensure()).authenticated, false);
            assert.equal(probes, 1, 'Unknown script failures must not be retried as a navigation event.');
        });
        for (const interruption of ['abort', 'context', 'contents']) {
            await test({ loggedIn:true, timeoutMs:500 }, async f => {
                const controller = new AbortController();
                let probes = 0;
                f.contents.executeJavaScript = async () => {
                    probes++;
                    if (interruption === 'abort') controller.abort();
                    else if (interruption === 'context') f.switchContext();
                    else f.session.getContents = () => null;
                    throw new Error('Execution context was destroyed');
                };
                await assert.rejects(f.session.ensure({ signal:controller.signal }), error => error.name === 'AbortError');
                assert.equal(probes, 1, 'Navigation recovery must stop when its original context is no longer valid.');
                assert.equal(f.stats.frameExecutions, 0);
            });
        }
        await test({ loggedIn:true }, async f => {
            let probes = 0;
            f.contents.executeJavaScript = async () => {
                probes++;
                f.contents.getURL = () => 'https://www.mercadolivre.com.br/account-verification';
                throw new Error('Execution context was destroyed');
            };
            const result = await f.session.ensure();
            assert.equal(result.reason, 'ml_login_required');
            assert.equal(probes, 1, 'Navigation recovery must validate the new destination before executing.');
            assert.equal(f.stats.frameExecutions, 0);
        });
        for (const redirect of [true, false]) {
            await test({ loggedIn:true, timeoutMs:500 }, async f => {
                const execute = f.contents.executeJavaScript;
                await f.page.evaluate(() => document.querySelector('#btnLogoutMenu').remove());
                let interrupted = false;
                f.contents.executeJavaScript = async code => {
                    if (code === pageScript('open-menu')) {
                        const result = await execute(code);
                        await f.page.evaluate(() => {
                            const logout = document.createElement('div');
                            logout.id = 'btnLogoutMenu';
                            document.querySelector('.avantpro-menu-content').appendChild(logout);
                        });
                        return result;
                    }
                    if (!interrupted && code === pageScript('close-menu')) {
                        interrupted = true;
                        if (redirect) f.contents.getURL = () => 'https://www.mercadolivre.com.br/account-verification';
                        else {
                            f.setEmail('');
                            await f.page.evaluate(() => {
                                const logout = document.querySelector('#btnLogoutMenu');
                                logout.id = 'btnLoginMenu';
                                logout.textContent = 'Fazer Login';
                            });
                        }
                        throw new Error('Execution context was destroyed');
                    }
                    return execute(code);
                };
                const result = await f.session.ensure();
                assert(interrupted, 'The confirmation must encounter navigation during menu cleanup.');
                assert.equal(result.authenticated, false, 'A previous document cannot confirm the new session.');
                assert.equal(result.reason, redirect ? 'ml_login_required' : 'avant_email_missing');
                assert.equal(f.stats.frameExecutions, 0);
            });
        }
        await test({ success:true }, async f => {
            assert.equal((await f.probe()).state, 'logged_out', 'Hidden logout menu must be detected.');
            assert.deepEqual(await f.session.ensure(), { authenticated:true, attempted:true });
            assert.equal(await f.submits(), 1); assert.equal((await f.probe()).state, 'authenticated');
        });
        await test({ success:true, enableDelay:25 }, async f => {
            assert.deepEqual(await f.session.ensure(), { authenticated:true, attempted:true });
            assert.equal(await f.submits(), 1); assert(f.stats.frameExecutions > 1, 'Validation must be polled before clicking disabled button.');
        });
        await test({ success:true, beginPopup:true, confirmDelay:20, rejectAfterSubmit:true }, async f => {
            assert.deepEqual(await f.session.ensure(), { authenticated:true, attempted:true });
            assert.equal(await f.submits(), 1, 'A destroyed submission context must be confirmed using the fresh parent document.');
            assert.equal(f.stats.frameExecutions, 1, 'Reload rejection must never cause another credential submission.');
        });
        await test({ success:true, beginPopup:true, confirmDelay:50 }, async f => {
            const first = f.session.ensure(); const concurrent = f.session.ensure();
            assert.strictEqual(first, concurrent, 'Concurrent callers must share one attempt.');
            assert.deepEqual(await first, { authenticated:true, attempted:true }); assert.equal(await f.submits(), 1);
        });
        await test({ beginPopup:true, authUrl:'https://auth.avantprocloud.com.br.evil.example/' }, async f => {
            const result = await f.session.ensure();
            assert.equal(result.authenticated, false); assert.equal(result.reason, 'avant_form_not_allowed');
            assert.equal(f.stats.frameExecutions, 0, 'An evil suffix must never receive the account identifier.');
        });
        await test({ beginPopup:true, authUrl:'https://auth.avantprocloud.com.br/register', registration:true }, async f => {
            const result = await f.session.ensure(); assert.equal(result.authenticated, false); assert.equal(result.reason, 'avant_manual_login_required');
            const value = await f.page.frames().find(frame => frame.url().includes('/register')).locator('#email').inputValue(); assert.equal(value, '');
        });
        await test({ beginPopup:true, registration:true }, async f => {
            const result = await f.session.ensure(); assert.equal(result.reason, 'avant_manual_login_required'); assert.equal(await f.submits(), 0);
        });
        for (const challenge of ['password','otp','captcha']) {
            await test({ beginPopup:true, challenge }, async f => {
                const result = await f.session.ensure(); assert.equal(result.reason, 'avant_manual_login_required'); assert.equal(await f.submits(), 0);
            });
        }
        await test({ marketCaptcha:true }, async f => {
            const result = await f.session.ensure(); assert.equal(result.reason, 'ml_login_required'); assert.equal(await f.submits(), 0); assert.equal(f.stats.frameExecutions, 0);
        });
        await test({ reportedUrl:'https://lista.mercadolivre.com.br.evil.example/' }, async f => {
            const result = await f.session.ensure(); assert.equal(result.authenticated, false); assert.equal(f.stats.pageExecutions, 0);
        });
        await test({ email:'' }, async f => {
            const result = await f.session.ensure(); assert.equal(result.reason, 'avant_email_missing'); assert.equal(await f.submits(), 0);
        });
        await test({ beginPopup:true, disabled:true, timeoutMs:85 }, async f => {
            const result = await f.session.ensure(); assert.equal(result.authenticated, false); assert.equal(result.attempted, false);
            assert.equal(await f.submits(), 0, 'Filling alone must never authenticate.');
            assert.equal(await f.page.frames().find(frame => frame.url().startsWith(AUTH_URL)).locator('#email').inputValue(), FIXTURE_EMAIL);
        });
        await test({ beginPopup:true, timeoutMs:85 }, async f => {
            const result = await f.session.ensure(); assert.equal(result.authenticated, false); assert.equal(result.attempted, true);
            assert.equal(result.reason, 'avant_login_not_confirmed'); assert.equal(await f.submits(), 1);
            const second = await f.session.ensure(); assert.equal(second.authenticated, false); assert.equal(second.attempted, false);
            assert.equal(await f.submits(), 1, 'Cooldown must prevent repeated submissions after unconfirmed login.');
        });
        await test({ beginPopup:true, timeoutMs:300 }, async f => {
            const controller = new AbortController(); const pending = f.session.ensure({ signal:controller.signal });
            await until(async () => await f.submits() === 1); controller.abort();
            await assert.rejects(pending, error => error.name === 'AbortError'); assert.equal(await f.submits(), 1);
        });
        await test({ beginPopup:true, timeoutMs:300 }, async f => {
            const pending = f.session.ensure(); await until(async () => await f.submits() === 1); f.switchContext();
            await assert.rejects(pending, error => error.name === 'AbortError'); assert.equal(await f.submits(), 1);
        });
        await test({ unknown:true, probeMs:20 }, async f => {
            const result = await f.session.ensure(); assert.equal(result.authenticated, false); assert.equal(result.reason, 'avant_session_unconfirmed'); assert.equal(f.stats.frameExecutions, 0);
        });
        await test({ beginPopup:true, authUrl:'https://auth.avantprocloud.com.br.evil.example/' }, async f => {
            const frame = f.page.frames().find(candidate => candidate !== f.page.mainFrame());
            const result = await frame.evaluate(source => (0, eval)(source), loginScript(FIXTURE_EMAIL));
            assert.equal(result.blocked, true, 'Execution-time origin checks must also refuse attacker frames.');
            assert.equal(await frame.locator('#email').inputValue(), '');
        });
        console.log(`Pesquisa de Mercado Avant automatic login: ${cases} DOM/lifecycle cases passed.`);
    } finally { await browser.close(); }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
