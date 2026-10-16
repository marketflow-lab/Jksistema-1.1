"use strict";

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const repoRoot = path.resolve(__dirname, '..');
const shellSource = fs.readFileSync(path.join(repoRoot, 'electron_shell.html'), 'utf8');

function fixtureHtml(name) {
    return `<!doctype html><html><head><title>${name}</title></head><body>
        <label>Rascunho <input id="draft" value="initial-${name}"></label>
        <script>window.fixtureDocumentId = Math.random().toString(36);</script>
        </body></html>`;
}

async function main() {
    const origin = 'http://127.0.0.1:8001';
    // Route every request in this isolated browser; the real local backend is never contacted.
    async function serveFixture(route) {
        const url = new URL(route.request().url());
        if (url.origin !== origin) return route.abort();
        if (url.pathname === '/dashboard.html' || /^\/fixture-[bcd]\.html$/.test(url.pathname)) {
            const name = url.pathname === '/dashboard.html' ? 'A' : url.pathname.match(/fixture-([bcd])/)[1].toUpperCase();
            return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: fixtureHtml(name) });
        }
        if (url.pathname === '/electron_shell.html') {
            return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: shellSource });
        }
        const relative = url.pathname.replace(/^\/+/, '');
        const target = path.resolve(repoRoot, relative);
        if (!target.startsWith(`${repoRoot}${path.sep}`) || !relative.startsWith('static/')
            || !relative.endsWith('.js') || !fs.existsSync(target)) {
            return route.fulfill({ status: 404, body: 'not found' });
        }
        return route.fulfill({ status: 200, contentType: 'application/javascript; charset=utf-8', body: fs.readFileSync(target) });
    }
    let browser;
    const pages = [];
    try {
        browser = await chromium.launch({ headless: true });
        async function fixture() {
            const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
            pages.push(page);
            await page.route('**/*', serveFixture);
            const errors = [];
            page.on('pageerror', error => errors.push(error.message));
            await page.addInitScript(() => {
                window.tabFixture = { detaches: [], detachSuccess: true };
                window.electronAPI = {
                    async openDetachedInternalBrowser(url, title) {
                        window.tabFixture.detaches.push({ url, title });
                        return { success: window.tabFixture.detachSuccess };
                    }
                };
            });
            await page.goto(`${origin}/electron_shell.html?appUrl=${encodeURIComponent(`${origin}/dashboard.html`)}`, { waitUntil: 'domcontentloaded' });
            await page.waitForFunction(() => tabs.size === 1 && [...tabs.values()][0].webview.contentDocument?.querySelector('#draft'));
            const ids = await page.evaluate(origin => {
                const ids = { A: [...tabs.keys()][0] };
                for (const name of ['B', 'C', 'D']) ids[name] = createTab(`${origin}/fixture-${name.toLowerCase()}.html`, name);
                return ids;
            }, origin);
            await page.waitForFunction(() => [...tabs.values()].every(tab => tab.webview.contentDocument?.querySelector('#draft')));
            await page.evaluate(ids => {
                window.tabFixture.ids = ids;
                window.tabFixture.originals = new Map();
                for (const [name, id] of Object.entries(ids)) {
                    const tab = tabs.get(id), frame = tab.webview, doc = frame.contentDocument;
                    doc.querySelector('#draft').value = `edited-${name}`;
                    window.tabFixture.originals.set(id, { tab, frame, doc, documentId: frame.contentWindow.fixtureDocumentId });
                }
                showTabsToolbar(0);
            }, ids);
            return { page, ids, errors };
        }
        const button = (page, id) => page.locator(`.tab[data-tab-id="${id}"]`);
        async function state(page) {
            return page.evaluate(() => {
                const names = new Map(Object.entries(window.tabFixture.ids).map(([name, id]) => [id, name]));
                return {
                    order: [...tabs.keys()].map(id => names.get(id)),
                    dom: [...document.querySelectorAll('#tabs .tab')].map(node => names.get(node.dataset.tabId)),
                    active: names.get(activeId), dragging: !!tabDragState,
                    decorations: document.querySelectorAll('.is-dragging, .is-drop-before, .is-detach-target').length,
                    hint: document.querySelector('#tab-detach-hint').classList.contains('is-active'),
                    expanded: document.querySelector('.shell').classList.contains('tabs-expanded'),
                    detaches: window.tabFixture.detaches.slice(),
                    views: [...tabs].map(([id, tab]) => {
                        const original = window.tabFixture.originals.get(id);
                        return { name: names.get(id), sameTab: original.tab === tab, sameFrame: original.frame === tab.webview,
                            sameDocument: original.doc === tab.webview.contentDocument,
                            documentId: tab.webview.contentWindow.fixtureDocumentId === original.documentId,
                            draft: tab.webview.contentDocument.querySelector('#draft')?.value };
                    })
                };
            });
        }
        async function checkOrder(page, expected) {
            const current = await state(page);
            assert.deepStrictEqual(current.order, expected, 'the tab Map must use the requested order');
            assert.deepStrictEqual(current.dom, expected, 'the visible tab order must match the Map');
            for (const view of current.views) {
                assert(view.sameTab && view.sameFrame && view.sameDocument && view.documentId, `reordering must preserve ${view.name}'s iframe and loaded document`);
                assert.equal(view.draft, `edited-${view.name}`, 'edited data must survive reordering');
            }
            return current;
        }
        async function startDrag(page, id) {
            await page.evaluate(() => showTabsToolbar(0));
            const rect = await button(page, id).boundingBox();
            const point = { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
            await page.mouse.move(point.x, point.y);
            await page.mouse.down();
            await page.mouse.move(point.x + 10, point.y);
            assert.equal((await state(page)).dragging, true, 'crossing the drag threshold must not lose pointer capture when a button is reinserted');
            return point;
        }
        async function edge(page, side) {
            return page.evaluate(side => {
                const buttons = [...document.querySelectorAll('#tabs .tab')];
                const rect = buttons[side === 'left' ? 0 : buttons.length - 1].getBoundingClientRect();
                return { x: side === 'left' ? rect.left + 2 : rect.right - 2, y: rect.top + rect.height / 2 };
            }, side);
        }
        async function moveToEdge(page, side) {
            const point = await edge(page, side);
            await page.mouse.move(point.x, point.y, { steps: 14 });
        }
        async function finishDrag(page, expectedActive) {
            await page.mouse.up();
            const current = await state(page);
            assert.equal(current.dragging, false);
            assert.equal(current.decorations, 0);
            assert.equal(current.hint, false);
            assert.equal(current.active, expectedActive, 'dropping a tab must activate the dragged tab, not its former neighbor');
            assert.equal(current.detaches.length, 0);
        }

        const reorder = await fixture();
        await startDrag(reorder.page, reorder.ids.A);
        await moveToEdge(reorder.page, 'right');
        await checkOrder(reorder.page, ['B', 'C', 'D', 'A']);
        await finishDrag(reorder.page, 'A');
        await startDrag(reorder.page, reorder.ids.A);
        await moveToEdge(reorder.page, 'left');
        await checkOrder(reorder.page, ['A', 'B', 'C', 'D']);
        await finishDrag(reorder.page, 'A');
        await startDrag(reorder.page, reorder.ids.B);
        await moveToEdge(reorder.page, 'right');
        await checkOrder(reorder.page, ['A', 'C', 'D', 'B']);
        await moveToEdge(reorder.page, 'left');
        await checkOrder(reorder.page, ['B', 'A', 'C', 'D']);
        await moveToEdge(reorder.page, 'right');
        await checkOrder(reorder.page, ['A', 'C', 'D', 'B']);
        await finishDrag(reorder.page, 'B');
        await checkOrder(reorder.page, ['A', 'C', 'D', 'B']);
        await startDrag(reorder.page, reorder.ids.A);
        await moveToEdge(reorder.page, 'right');
        const closeTarget = await button(reorder.page, reorder.ids.B).locator('.tab-close').boundingBox();
        await reorder.page.mouse.move(closeTarget.x + closeTarget.width / 2, closeTarget.y + closeTarget.height / 2);
        await finishDrag(reorder.page, 'A');
        await checkOrder(reorder.page, ['C', 'D', 'B', 'A']);
        assert.equal(await reorder.page.locator('#tabs .tab').count(), 4, 'dropping over another tab close control must not close it');
        await reorder.page.evaluate(() => requestWindowZoom(80));
        assert.equal(await reorder.page.locator('#zoom-reset').textContent(), '80%');
        await startDrag(reorder.page, reorder.ids.A);
        await moveToEdge(reorder.page, 'left');
        await checkOrder(reorder.page, ['A', 'C', 'D', 'B']);
        await moveToEdge(reorder.page, 'right');
        await checkOrder(reorder.page, ['C', 'D', 'B', 'A']);
        await finishDrag(reorder.page, 'A');
        assert.deepStrictEqual(reorder.errors, []);
        await reorder.page.close();

        const controls = await fixture();
        await button(controls.page, controls.ids.A).click();
        assert.equal((await state(controls.page)).active, 'A', 'an ordinary click must still activate its tab');
        await button(controls.page, controls.ids.B).focus();
        await controls.page.keyboard.press('Enter');
        assert.equal((await state(controls.page)).active, 'B', 'keyboard activation must remain available');
        await button(controls.page, controls.ids.C).focus();
        await controls.page.keyboard.press('Space');
        assert.equal((await state(controls.page)).active, 'C');
        await button(controls.page, controls.ids.A).locator('.tab-close').click();
        const closed = await checkOrder(controls.page, ['B', 'C', 'D']);
        assert.equal(closed.active, 'C', 'closing an inactive tab must preserve the active one');
        assert.equal(closed.dragging, false);
        assert.equal(closed.detaches.length, 0);
        await startDrag(controls.page, controls.ids.B);
        await controls.page.evaluate(() => {
            const pointerId = tabDragState.pointerId;
            const owner = [...document.querySelectorAll('#tabs, #tabs .tab')].find(node => node.hasPointerCapture(pointerId));
            assertFixture(owner, 'an active drag must have an owner');
            owner.dispatchEvent(new PointerEvent('pointercancel', { pointerId, bubbles: true }));
            function assertFixture(value, message) { if (!value) throw new Error(message); }
        });
        const cancelled = await state(controls.page);
        assert.equal(cancelled.dragging, false, 'cancellation must clear the gesture');
        assert.equal(cancelled.decorations, 0);
        assert.equal(cancelled.hint, false);
        assert.equal(cancelled.detaches.length, 0);
        await controls.page.mouse.up();
        await button(controls.page, controls.ids.D).click();
        assert.equal((await state(controls.page)).active, 'D', 'a cancelled gesture must not block the next click');
        await startDrag(controls.page, controls.ids.B);
        await controls.page.evaluate(() => {
            const pointerId = tabDragState.pointerId;
            const owner = [...document.querySelectorAll('#tabs, #tabs .tab')].find(node => node.hasPointerCapture(pointerId));
            owner.releasePointerCapture(pointerId);
        });
        const lossPoint = await button(controls.page, controls.ids.B).boundingBox();
        await controls.page.mouse.move(lossPoint.x + 30, lossPoint.y + 15);
        assert.equal((await state(controls.page)).dragging, false, 'real lost capture must cancel the gesture');
        await controls.page.mouse.up();
        await checkOrder(controls.page, ['B', 'C', 'D']);
        await startDrag(controls.page, controls.ids.B);
        await controls.page.evaluate(() => window.dispatchEvent(new Event('blur')));
        assert.equal((await state(controls.page)).dragging, false, 'window blur must release the gesture');
        await controls.page.mouse.up();
        await startDrag(controls.page, controls.ids.B);
        await controls.page.evaluate(id => closeTab(id), controls.ids.B);
        const removed = await checkOrder(controls.page, ['C', 'D']);
        assert.equal(removed.dragging, false, 'closing the dragged tab must release capture');
        assert.equal(removed.decorations, 0);
        await controls.page.mouse.up();
        await startDrag(controls.page, controls.ids.C);
        await moveToEdge(controls.page, 'right');
        await finishDrag(controls.page, 'C');
        await checkOrder(controls.page, ['D', 'C']);
        assert.deepStrictEqual(controls.errors, []);
        await controls.page.close();

        for (const success of [false, true]) {
            const detached = await fixture();
            await detached.page.evaluate(success => { window.tabFixture.detachSuccess = success; }, success);
            const start = await startDrag(detached.page, detached.ids.A);
            const dropY = await detached.page.locator('.toolbar').evaluate(node => node.getBoundingClientRect().bottom + 100);
            await detached.page.mouse.move(start.x, dropY, { steps: 8 });
            await detached.page.waitForTimeout(800);
            const pending = await state(detached.page);
            assert.equal(pending.expanded, true, 'the toolbar must remain expanded while the pointer is held outside it');
            assert.equal(pending.dragging, true);
            assert.equal(pending.hint, true);
            assert.equal(pending.detaches.length, 0, 'a tab must only detach when the user releases the pointer');
            await detached.page.mouse.up();
            await detached.page.waitForFunction(() => window.tabFixture.detaches.length === 1 && !tabDragState);
            const result = await checkOrder(detached.page, success ? ['B', 'C', 'D'] : ['A', 'B', 'C', 'D']);
            assert.equal(result.detaches.length, 1);
            assert.equal(result.detaches[0].url, `${origin}/dashboard.html`);
            assert.equal(result.decorations, 0);
            assert.equal(result.hint, false);
            assert.deepStrictEqual(detached.errors, []);
            await detached.page.close();
        }
        console.log('Electron tab reordering: real pointer capture, bidirectional reorder, controls, cancellation, detach and preserved iframe data OK');
    } finally {
        for (const page of pages) if (!page.isClosed()) await page.close();
        if (browser) await browser.close();
    }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
