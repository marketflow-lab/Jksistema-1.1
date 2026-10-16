'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const navigation = fs.readFileSync(path.join(root, 'static/auth/navigation.js'), 'utf8');
const helper = navigation.slice(navigation.indexOf('(function instalarMidiaAutenticadaJK'));
const cache = fs.readFileSync(path.join(root, 'static/auth/media-cache.js'), 'utf8');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 900, height: 600 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.setDefaultTimeout(5000);
  try {
    await page.route('**/*', route => {
      if (route.request().url() === 'https://photo-cache.test/') {
        return route.fulfill({ contentType: 'text/html', body: '<html><body><div id="photos"></div><div style="height:3000px"></div><div id="far"></div></body></html>' });
      }
      throw new Error('Unexpected external request in isolated fixture');
    });
    await page.goto('https://photo-cache.test/');
    await page.evaluate(() => {
      const bytes = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5RkAAAAASUVORK5CYII='), char => char.charCodeAt(0));
      window.photoNow = 0;
      window.photoTimers = new Map();
      let timerId = 0;
      Date.now = () => window.photoNow;
      window.setTimeout = (callback, delay) => { const id = ++timerId; photoTimers.set(id, { callback, at: photoNow + delay }); return id; };
      window.clearTimeout = id => photoTimers.delete(id);
      window.advancePhotos = ms => {
        photoNow += ms;
        for (const [id, timer] of [...photoTimers]) if (timer.at <= photoNow) { photoTimers.delete(id); timer.callback(); }
      };
      window.photoCalls = [];
      window.pendingPhotos = [];
      window.holdPhotos = true;
      window.scope = { tenantId: 'tenant-fixture', storeId: 'store-a' };
      window.obterAuthHeaders = () => ({ Authorization: 'Bearer synthetic-photo-token' });
      window.revokedPhotos = [];
      const nativeRevoke = URL.revokeObjectURL.bind(URL);
      URL.revokeObjectURL = url => { window.revokedPhotos.push(url); nativeRevoke(url); };
      window.fetch = (source, options) => {
        if (!source.startsWith('/api/cadastro/foto/')) throw new Error('Unexpected request');
        if (options.headers.Authorization !== 'Bearer synthetic-photo-token') throw new Error('Missing synthetic auth');
        window.photoCalls.push(source);
        return new Promise((resolve, reject) => {
          const finish = () => resolve({ ok: true, status: 200, blob: async () => new Blob([bytes], { type: 'image/png' }) });
          options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
          if (window.holdPhotos) window.pendingPhotos.push(finish); else finish();
        });
      };
      window.addPhoto = (id, container = 'photos', file = 'same.png') => {
        const link = document.createElement('a');
        link.setAttribute('data-jk-auth-link', '1');
        const img = document.createElement('img');
        img.id = id; img.width = 30; img.height = 30;
        img.setAttribute('data-jk-auth-src', '/api/cadastro/foto/tenant-fixture/' + file);
        link.append(img); document.getElementById(container).append(link);
      };
    });
    await page.addScriptTag({ content: cache });
    await page.addScriptTag({ content: helper });
    await page.evaluate(() => {
      JKAuthenticatedMedia.configurarCacheRaiz(document.body, img => ({ ...window.scope, revision: img.dataset.jkPhotoRevision || '' }));
      addPhoto('first'); addPhoto('second');
    });
    await page.waitForFunction(() => photoCalls.length === 1 && pendingPhotos.length === 1);
    await page.evaluate(() => { holdPhotos = false; pendingPhotos.splice(0).forEach(finish => finish()); });
    await page.waitForFunction(() => Array.from(document.querySelectorAll('#photos img')).every(img => img.complete && img.naturalWidth === 1));
    const first = await page.evaluate(() => Array.from(document.querySelectorAll('#photos img')).map(img => ({ src: img.src, href: img.parentElement.href })));
    assert.notStrictEqual(first[0].src, first[1].src);
    assert.deepStrictEqual(first.map(img => img.href), first.map(img => img.src));
    await page.evaluate(() => { document.getElementById('photos').replaceChildren(); addPhoto('replacement'); });
    await page.waitForFunction(() => document.getElementById('replacement')?.naturalWidth === 1);
    assert.strictEqual(await page.evaluate(() => photoCalls.length), 1, 'DOM rerender must reuse cached bytes');
    assert(await page.evaluate(old => old.every(url => revokedPhotos.includes(url)), first.map(img => img.src)));
    await page.evaluate(() => { scope.storeId = 'store-b'; document.getElementById('photos').replaceChildren(); addPhoto('store-b'); });
    await page.waitForFunction(() => document.getElementById('store-b')?.naturalWidth === 1);
    assert.strictEqual(await page.evaluate(() => photoCalls.length), 2);
    await page.evaluate(() => { scope.storeId = 'store-a'; document.getElementById('photos').replaceChildren(); addPhoto('store-a'); });
    await page.waitForFunction(() => document.getElementById('store-a')?.naturalWidth === 1);
    assert.strictEqual(await page.evaluate(() => photoCalls.length), 2, 'return to a store should reuse its cache');
    await page.evaluate(() => addPhoto('offscreen', 'far', 'far.png'));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.strictEqual(await page.evaluate(() => photoCalls.length), 2, 'offscreen remains lazy');
    await page.locator('#offscreen').scrollIntoViewIfNeeded();
    await page.waitForFunction(() => document.getElementById('offscreen')?.naturalWidth === 1);
    assert.strictEqual(await page.evaluate(() => photoCalls.length), 3);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const oldVisible = await page.locator('#offscreen').getAttribute('src');
    await page.evaluate(() => { holdPhotos = true; advancePhotos(60000); });
    await page.waitForFunction(() => photoCalls.length === 4);
    assert.strictEqual(await page.locator('#offscreen').getAttribute('src'), oldVisible, 'keep visible pixels while refreshing');
    assert.strictEqual(await page.evaluate(() => photoCalls.filter(source => source.endsWith('/same.png')).length), 2, 'offscreen loaded images must not refresh');
    await page.evaluate(() => { holdPhotos = false; pendingPhotos.splice(0).forEach(finish => finish()); });
    await page.waitForFunction(old => document.getElementById('offscreen').src !== old && document.getElementById('offscreen').naturalWidth === 1, oldVisible);
    const beforeRevision = await page.locator('#offscreen').getAttribute('src');
    await page.evaluate(() => { document.getElementById('offscreen').dataset.jkPhotoRevision = 'revision-2'; });
    await page.waitForFunction(old => photoCalls.length === 5 && document.getElementById('offscreen').src !== old && document.getElementById('offscreen').naturalWidth === 1, beforeRevision);
    await page.evaluate(() => dispatchEvent(new Event('pagehide')));
    assert.strictEqual(await page.evaluate(() => photoTimers.size), 0, 'exit clears all photo timers');
    assert.strictEqual(await page.locator('img[src]').count(), 0, 'leaving revokes all rendered URLs');
    assert.deepStrictEqual(errors, []);
    console.log('authenticated photo cache browser: dedup, DOM rerender, URLs, store return, lazy, visible TTL, revision and exit passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
