'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '..');
const navigation = fs.readFileSync(path.join(root, 'static/auth/navigation.js'), 'utf8');
const cachePath = path.join(root, 'static/auth/media-cache.js');
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const photo = (size = 16) => ({ type: 'image/png', size });
const response = (blob = photo(), status = 200) => ({ ok: status === 200, status, blob: async () => blob });
function harness() {
  let now = 0, timerId = 0;
  const timers = new Map();
  const calls = [], urls = [], revoked = [], observed = new Set(), listeners = {};
  const headers = { Authorization: 'Bearer synthetic-a' };
  const scope = { tenantId: 'tenant-a', storeId: 'StoreA', revision: '', policyRevision: '', sessionId: '' };
  const images = [];
  const roots = [];
  const document = { readyState: 'loading', addEventListener(name, fn) { (listeners['document:' + name] ||= []).push(fn); }, querySelectorAll: () => images, documentElement: {} };
  class MediaURL extends URL {
    static createObjectURL(blob) { const url = `blob:test-${urls.length}`; urls.push({ url, blob }); return url; }
    static revokeObjectURL(url) { revoked.push(url); }
  }
  class Clock extends Date { static now() { return now; } }
  const context = {
    AbortController, URL: MediaURL, Date: Clock, console, document,
    location: { href: 'https://jk.test/medias_compras.html', origin: 'https://jk.test' },
    obterAuthHeaders: () => ({ ...headers }),
    addEventListener(name, fn) { (listeners[name] ||= []).push(fn); },
    setTimeout(callback, delay) { const id = ++timerId; timers.set(id, { callback, at: now + delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    IntersectionObserver: class {
      constructor(callback, options) {
        this.callback = callback; this.visibility = options?.rootMargin === '0px';
        if (this.visibility) context.visibility = callback; else context.intersection = callback;
      }
      observe(img) {
        if (this.visibility) this.callback([{ target: img, isIntersecting: true }]);
        else observed.add(img);
      }
      unobserve(img) { observed.delete(img); }
      disconnect() { observed.clear(); }
    },
    fetch(url, options) {
      let resolve, reject;
      const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
      calls.push({ url, options, resolve, reject });
      return promise;
    },
  };
  context.window = context;
  vm.createContext(context);
  if (fs.existsSync(cachePath)) vm.runInContext(fs.readFileSync(cachePath, 'utf8'), context);
  vm.runInContext(navigation.slice(navigation.indexOf('(function instalarMidiaAutenticadaJK')), context);
  const api = context.JKAuthenticatedMedia;
  function createRoot(enabled = true) {
    const container = {
      children: [],
      contains(img) { return this.children.includes(img); },
      querySelectorAll() { return this.children; },
    };
    roots.push(container);
    if (enabled) api.configurarCacheRaiz(container, () => ({ ...scope }));
    return container;
  }
  function image(container, source = '/api/cadastro/foto/tenant-a/001.png') {
    const attrs = new Map([['data-jk-auth-src', source]]);
    const img = {
      classList: { add() {}, remove() {} },
      closest() { return null; },
      getAttribute: name => attrs.get(name) || null,
      setAttribute: (name, value) => attrs.set(name, String(value)),
      removeAttribute: name => attrs.delete(name),
      matches: selector => selector === 'img[data-jk-auth-src]',
      querySelectorAll: () => [],
    };
    images.push(img);
    if (container) container.children.push(img);
    return img;
  }
  return { context, api, calls, urls, revoked, observed, scope, headers, image, createRoot,
    timers, advance: ms => { now += ms; },
    async fireDue() {
      for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.callback(); }
      await flush();
    },
    emit: (name, event = {}) => (listeners[name] || []).forEach(fn => fn(event)) };
}
(async () => {
  let scenarios = 0;
  {
    const h = harness();
    assert.strictEqual(typeof h.api.configurarCacheRaiz, 'function', 'cache must be opt-in by root');
    const a = h.image(null), b = h.image(null);
    const pending = [h.api.hidratarImagem(a), h.api.hidratarImagem(b)];
    assert.strictEqual(h.calls.length, 2, 'unconfigured consumers retain existing behavior');
    h.calls.forEach(call => call.resolve(response())); await Promise.all(pending);
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot();
    const a = h.image(container), b = h.image(container);
    const first = h.api.hidratarImagem(a), second = h.api.hidratarImagem(b);
    assert.strictEqual(h.calls.length, 1, 'same scope shares one fetch');
    const blob = photo(); h.calls[0].resolve(response(blob)); await Promise.all([first, second]);
    assert.strictEqual(h.urls[0].blob, h.urls[1].blob);
    assert.notStrictEqual(a.getAttribute('src'), b.getAttribute('src'), 'each img owns its URL');
    h.api.liberar(a); assert.deepStrictEqual(h.revoked, [h.urls[0].url]);
    const c = h.image(container); await h.api.hidratarImagem(c);
    assert.strictEqual(h.calls.length, 1, 'rerender reuses retained Blob');
    h.api.liberar(container); assert.strictEqual(h.revoked.length, 3);
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot();
    const a = h.image(container), b = h.image(container);
    const pending = [h.api.hidratarImagem(a), h.api.hidratarImagem(b)];
    h.api.liberar(a); assert.strictEqual(h.calls[0].options.signal.aborted, false);
    h.calls[0].resolve(response()); await Promise.all(pending);
    assert.strictEqual(a.getAttribute('src'), null); assert(b.getAttribute('src'));
    const c = h.image(container, '/api/cadastro/foto/tenant-a/002.png');
    const abandoned = h.api.hidratarImagem(c); h.api.liberar(c);
    assert.strictEqual(h.calls[1].options.signal.aborted, true, 'last consumer aborts');
    h.calls[1].resolve(response()); await abandoned; await flush();
    const again = h.api.hidratarImagem(c); assert.strictEqual(h.calls.length, 3, 'aborted result is not retained');
    h.calls[2].resolve(response()); await again;
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot();
    const img = h.image(container);
    h.api.hidratar(container); assert.strictEqual(h.calls.length, 0); assert(h.observed.has(img));
    h.context.intersection([{ target: img, isIntersecting: true }]);
    assert.strictEqual(h.calls.length, 1); h.calls[0].resolve(response()); await flush();
    h.api.liberar(img); h.advance(59999);
    await h.api.hidratarImagem(img); assert.strictEqual(h.calls.length, 1);
    h.api.liberar(img); h.advance(1);
    const expired = h.api.hidratarImagem(img); assert.strictEqual(h.calls.length, 2, 'TTL is fixed at 60s');
    h.calls[1].resolve(response()); await expired;
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot(), img = h.image(container);
    async function load(expectFetch) {
      const count = h.calls.length, pending = h.api.hidratarImagem(img);
      assert.strictEqual(h.calls.length, count + Number(expectFetch));
      if (expectFetch) h.calls.at(-1).resolve(response());
      await pending;
    }
    await load(true); h.api.liberar(img);
    h.scope.storeId = 'storea'; await load(true); h.api.liberar(img);
    h.scope.storeId = 'StoreA'; await load(false);
    h.scope.revision = '2'; await load(true);
    h.scope.policyRevision = 'policy-2'; await load(true);
    h.scope.tenantId = 'tenant-b'; await load(true);
    h.scope.sessionId = 'session-2'; await load(true);
    h.headers.Authorization = 'Bearer synthetic-b'; await load(true);
    assert(h.revoked.length >= 6, 'scope/auth changes release earlier URLs');
    h.emit('storage', { key: 'access_token' }); assert.strictEqual(img.getAttribute('src'), null);
    await load(true); h.emit('pagehide'); assert.strictEqual(img.getAttribute('src'), null);
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot();
    const imgs = Array.from({ length: 7 }, (_, n) => h.image(container, `/api/cadastro/foto/tenant-a/${n}.png`));
    const pending = imgs.map(img => h.api.hidratarImagem(img));
    assert.strictEqual(h.calls.length, 4, 'only four downloads run concurrently');
    h.api.liberar(imgs[5]);
    h.calls[0].resolve(response()); await flush(); assert.strictEqual(h.calls.length, 5);
    assert(!h.calls.some(call => call.url.endsWith('/5.png')), 'cancelled queued consumer never downloads');
    h.calls.slice(1).forEach(call => call.resolve(response())); await flush();
    h.calls.slice(5).forEach(call => call.resolve(response())); await Promise.all(pending);
    assert.strictEqual(h.calls.length, 6);
    scenarios++;
  }
  for (const status of [401, 403]) {
    const h = harness(), container = h.createRoot(), a = h.image(container);
    let pending = h.api.hidratarImagem(a); h.calls[0].resolve(response()); await pending;
    const b = h.image(container, '/api/cadastro/foto/tenant-a/denied.png');
    pending = h.api.hidratarImagem(b); h.calls[1].resolve(response(photo(), status)); await pending;
    assert.strictEqual(a.getAttribute('src'), null, 'authorization denial revokes displayed cached images');
    pending = h.api.hidratarImagem(a); assert.strictEqual(h.calls.length, 3);
    h.calls[2].resolve(response()); await pending;
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot(), a = h.image(container), b = h.image(container);
    let body;
    const pending = h.api.hidratarImagem(a);
    h.calls[0].resolve({ ok: true, blob: () => new Promise(resolve => { body = resolve; }) });
    await flush(); h.api.invalidarCache(); body(photo()); await pending; await flush();
    assert.strictEqual(a.getAttribute('src'), null, 'late Blob after invalidation never renders');
    const newer = h.api.hidratarImagem(b); assert.strictEqual(h.calls.length, 2);
    h.calls[1].resolve(response()); await newer;
    scenarios++;
  }
  for (const [count, bytes, expected] of [[129, 16, true], [9, 4 * 1024 * 1024, true], [1, 4 * 1024 * 1024 + 1, true], [1, 4 * 1024 * 1024, false]]) {
    const h = harness(), container = h.createRoot();
    for (let i = 0; i < count; i++) {
      const img = h.image(container, `/api/cadastro/foto/tenant-a/${i}.png`);
      const pending = h.api.hidratarImagem(img); h.calls.at(-1).resolve(response(photo(bytes))); await pending;
      h.api.liberar(img);
    }
    const before = h.calls.length, img = h.image(container, '/api/cadastro/foto/tenant-a/0.png');
    const pending = h.api.hidratarImagem(img);
    assert.strictEqual(h.calls.length, before + Number(expected), `cache bounds count=${count} bytes=${bytes}`);
    if (expected) h.calls.at(-1).resolve(response()); await pending;
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot();
    async function get(i, expected) {
      const img = h.image(container, `/api/cadastro/foto/tenant-a/${i}.png`), before = h.calls.length;
      const pending = h.api.hidratarImagem(img);
      assert.strictEqual(h.calls.length, before + Number(expected));
      if (expected) h.calls.at(-1).resolve(response()); await pending; h.api.liberar(img);
    }
    for (let i = 0; i < 128; i++) await get(i, true);
    await get(0, false); await get(128, true); await get(0, false); await get(1, true);
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot(), img = h.image(container);
    let pending = h.api.hidratarImagem(img); h.calls[0].resolve(response()); await pending;
    h.advance(60000);
    pending = h.api.hidratarImagem(img);
    assert.strictEqual(h.calls.length, 2, 'rehydrating the same img also respects TTL');
    assert.strictEqual(img.getAttribute('src'), null);
    h.calls[1].resolve(response()); await pending;
    scenarios++;
  }
  {
    const h = harness(), left = h.createRoot(), right = h.createRoot();
    const a = h.image(left), b = h.image(right);
    const pending = [h.api.hidratarImagem(a), h.api.hidratarImagem(b)];
    h.api.invalidarCache(left);
    assert.strictEqual(h.calls[0].options.signal.aborted, false, 'other root retains a consumer');
    h.calls[0].resolve(response()); await Promise.all(pending);
    assert.strictEqual(a.getAttribute('src'), null); assert(b.getAttribute('src'));
    scenarios++;
  }
  for (const invalid of [response(photo(), 500), response({ type: 'text/html', size: 10 })]) {
    const h = harness(), container = h.createRoot(), img = h.image(container);
    let pending = h.api.hidratarImagem(img); h.calls[0].resolve(invalid); await pending;
    assert.strictEqual(img.getAttribute('src'), null);
    pending = h.api.hidratarImagem(img); assert.strictEqual(h.calls.length, 2, 'errors are not cached');
    h.calls[1].resolve(response()); await pending;
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot(), img = h.image(container);
    let finishBody;
    const pending = h.api.hidratarImagem(img);
    h.calls[0].resolve({ ok: true, blob: () => new Promise(resolve => { finishBody = resolve; }) });
    await flush(); h.headers.Authorization = 'Bearer new-session';
    finishBody(photo()); await pending;
    assert.strictEqual(img.getAttribute('src'), null, 'auth must be rechecked after Blob download');
    const fresh = h.api.hidratarImagem(img); assert.strictEqual(h.calls.length, 2);
    h.calls[1].resolve(response()); await fresh;
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot(); h.scope.storeId = '';
    const pending = [h.api.hidratarImagem(h.image(container)), h.api.hidratarImagem(h.image(container))];
    assert.strictEqual(h.calls.length, 2, 'unresolved store never enables cache');
    h.calls.forEach(call => call.resolve(response())); await Promise.all(pending);
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot(), a = h.image(container), b = h.image(container);
    const pending = [h.api.hidratarImagem(a), h.api.hidratarImagem(b)];
    h.calls[0].resolve(response()); await Promise.all(pending);
    const old = [a.getAttribute('src'), b.getAttribute('src')];
    h.advance(59999); await h.fireDue(); assert.strictEqual(h.calls.length, 1);
    h.advance(1); await h.fireDue(); assert.strictEqual(h.calls.length, 2, 'visible images revalidate automatically at TTL');
    assert.deepStrictEqual([a.getAttribute('src'), b.getAttribute('src')], old, 'keep current pixels during refresh');
    h.calls[1].resolve(response()); await flush();
    assert.notStrictEqual(a.getAttribute('src'), old[0]); assert.notStrictEqual(b.getAttribute('src'), old[1]);
    assert(old.every(url => h.revoked.includes(url)));
    h.emit('pagehide'); assert.strictEqual(h.timers.size, 0, 'exit cancels refresh timers');
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot(), img = h.image(container);
    const pending = h.api.hidratarImagem(img); h.calls[0].resolve(response()); await pending;
    h.context.visibility([{ target: img, isIntersecting: false }]);
    h.advance(60000); await h.fireDue(); assert.strictEqual(h.calls.length, 1, 'offscreen never refreshes');
    h.context.visibility([{ target: img, isIntersecting: true }]); await h.fireDue();
    assert.strictEqual(h.calls.length, 2, 'expired visible image refreshes when it returns');
    h.calls[1].resolve(response()); await flush(); h.api.liberar(img);
    assert.strictEqual(h.timers.size, 0);
    scenarios++;
  }
  {
    const h = harness(), img = h.image(null);
    const pending = h.api.hidratarImagem(img); h.calls[0].resolve(response()); await pending;
    h.advance(60000); await h.fireDue();
    assert.strictEqual(h.calls.length, 1); assert.strictEqual(h.timers.size, 0, 'unconfigured pages have no refresh timers');
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot(), img = h.image(container);
    const pending = h.api.hidratarImagem(img); h.calls[0].resolve(response()); await pending;
    const old = img.getAttribute('src');
    h.advance(60000); await h.fireDue(); h.calls[1].resolve(response(photo(), 500)); await flush();
    assert.strictEqual(img.getAttribute('src'), old, 'temporary error preserves rendered image');
    h.advance(60000); await h.fireDue(); assert.strictEqual(h.calls.length, 3, 'failed refresh retries at next TTL');
    h.calls[2].resolve(response(photo(), 403)); await flush();
    assert.strictEqual(img.getAttribute('src'), null, 'denied refresh revokes previous pixels');
    assert.strictEqual(h.timers.size, 0);
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot(), img = h.image(container);
    const pending = h.api.hidratarImagem(img); h.calls[0].resolve(response()); await pending;
    h.context.document.hidden = true; h.emit('document:visibilitychange');
    h.advance(60000); await h.fireDue(); assert.strictEqual(h.calls.length, 1);
    assert.strictEqual(h.timers.size, 0, 'hidden document pauses timers');
    h.context.document.hidden = false; h.emit('document:visibilitychange'); await h.fireDue();
    assert.strictEqual(h.calls.length, 2, 'visible document resumes expired images');
    h.calls[1].resolve(response()); await flush(); h.api.invalidarCache();
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot();
    const imgs = Array.from({ length: 5 }, (_, i) => h.image(container, `/api/cadastro/foto/tenant-a/${i}.png`));
    const pending = imgs.map(img => h.api.hidratarImagem(img));
    h.calls.slice().forEach(call => call.resolve(response())); await flush(); h.calls[4].resolve(response()); await Promise.all(pending);
    h.advance(60000); await h.fireDue(); assert.strictEqual(h.calls.length, 9, 'automatic refresh still has four concurrent downloads');
    h.calls[5].resolve(response()); await flush(); assert.strictEqual(h.calls.length, 10);
    h.calls.slice(6).forEach(call => call.resolve(response())); await flush(); h.api.invalidarCache();
    scenarios++;
  }
  {
    const h = harness(), left = h.createRoot(), right = h.createRoot();
    h.api.configurarCacheRaiz(right, () => ({ ...h.scope, storeId: 'StoreB' }));
    const a = h.image(left), b = h.image(right);
    const pending = [h.api.hidratarImagem(a), h.api.hidratarImagem(b)];
    h.calls.forEach(call => call.resolve(response())); await Promise.all(pending);
    const oldB = b.getAttribute('src');
    h.advance(60000); await h.fireDue(); assert.strictEqual(h.calls.length, 4);
    h.calls[2].resolve(response(photo(), 404)); await flush();
    assert.strictEqual(a.getAttribute('src'), null, '404 revokes a formerly allowed image');
    assert.strictEqual(b.getAttribute('src'), oldB, 'another store keeps its rendered image');
    assert.strictEqual(h.calls[3].options.signal.aborted, false, 'another store keeps its download');
    h.calls[3].resolve(response()); await flush(); h.api.liberar(b);
    await h.api.hidratarImagem(h.image(right));
    assert.strictEqual(h.calls.length, 4, 'another store retains its cache');
    h.api.invalidarCache(); assert.strictEqual(h.timers.size, 0);
    scenarios++;
  }
  {
    const h = harness(), container = h.createRoot(), img = h.image(container);
    let pending = h.api.hidratarImagem(img); h.calls[0].resolve(response()); await pending;
    assert.strictEqual(h.api.atualizarPoliticaRaiz(container, 'policy-1'), true);
    assert.strictEqual(img.getAttribute('src'), null, 'new policy immediately revokes rendered pixels');
    assert(h.observed.has(img), 'policy rehydration remains lazy');
    h.context.intersection([{ target: img, isIntersecting: true }]);
    h.calls[1].resolve(response()); await flush();
    const same = img.getAttribute('src');
    assert.strictEqual(h.api.atualizarPoliticaRaiz(container, 'policy-1'), false);
    assert.strictEqual(img.getAttribute('src'), same, 'same policy retains pixels and cache');
    h.api.liberar(img); h.scope.storeId = 'StoreB';
    pending = h.api.hidratarImagem(img); h.calls[2].resolve(response()); await pending;
    h.api.liberar(img); h.scope.storeId = 'StoreA'; await h.api.hidratarImagem(img);
    assert.strictEqual(h.calls.length, 3, 'stable policy allows store return cache hits');
    assert.strictEqual(h.api.atualizarPoliticaRaiz(container, 'policy-2'), true);
    h.context.intersection([{ target: img, isIntersecting: true }]);
    assert.strictEqual(h.calls.length, 4, 'new policy cannot reuse old-policy Blob');
    h.calls[3].resolve(response()); await flush(); h.api.invalidarCache();
    scenarios++;
  }
  console.log(`authenticated photo cache: ${scenarios} scenarios passed`);
})().catch(error => { console.error(error); process.exitCode = 1; });
