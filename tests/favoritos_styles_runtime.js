'use strict';

const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { chromium } = require('playwright');
const { importedStylePaths } = require('./helpers/favoritos_styles_sources');

const root = path.resolve(__dirname, '..');
const staticRoot = path.join(root, 'static');

function fixtureHtml() {
  return `<!doctype html>
    <html><head><link rel="stylesheet" href="/favoritos/styles.css?v=runtime-test"></head>
    <body>
      <main class="container">
        <header class="header"></header>
        <section class="panel"></section>
        <div class="sku-pagination"><button>Anterior</button></div>
        <aside class="ml-ranking-sidebar"></aside>
        <div class="ml-favoritos-balloon"></div>
        <div class="ml-work-modal"></div>
        <div class="favoritos-ml-tables"></div>
        <article class="ml-favoritos-sku-card"></article>
        <div class="ml-favoritos-historico-list"></div>
        <div class="ml-seller-cell"></div>
        <div class="favoritos-sheets-panel"></div>
        <div class="browser-frame-wrap"></div>
      </main>
    </body></html>`;
}

function startServer(requestedPaths) {
  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
    requestedPaths.add(pathname);
    if (pathname === '/fixture') {
      response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      response.end(fixtureHtml());
      return;
    }
    const relative = pathname.replace(/^\//, '');
    const absolute = path.resolve(staticRoot, relative);
    if (!absolute.startsWith(`${staticRoot}${path.sep}`) || !fs.existsSync(absolute)) {
      response.writeHead(404);
      response.end('not found');
      return;
    }
    response.writeHead(200, { 'content-type': 'text/css; charset=utf-8' });
    response.end(fs.readFileSync(absolute));
  });
  return new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server)));
}

async function main() {
  const requestedPaths = new Set();
  const server = await startServer(requestedPaths);
  const address = server.address();
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 720, height: 800 } });
    await page.goto(`http://127.0.0.1:${address.port}/fixture`, { waitUntil: 'networkidle' });
    const computed = await page.evaluate(() => {
      const style = selector => getComputedStyle(document.querySelector(selector));
      return {
        panelBackground: style('.panel').backgroundColor,
        paginationRadius: style('.sku-pagination button').borderRadius,
        sidebarBorder: style('.ml-ranking-sidebar').borderLeftWidth,
        balloonPosition: style('.ml-favoritos-balloon').position,
        modalPosition: style('.ml-work-modal').position,
        tablesDisplay: style('.favoritos-ml-tables').display,
        cardBackground: style('.ml-favoritos-sku-card').backgroundColor,
        historyDisplay: style('.ml-favoritos-historico-list').display,
        sellerDisplay: style('.ml-seller-cell').display,
        sheetsDisplay: style('.favoritos-sheets-panel').display,
        browserPosition: style('.browser-frame-wrap').position,
        responsiveHeaderDirection: style('.header').flexDirection,
      };
    });
    assert.deepStrictEqual(computed, {
      panelBackground: 'rgb(255, 255, 255)',
      paginationRadius: '6px',
      sidebarBorder: '4px',
      balloonPosition: 'fixed',
      modalPosition: 'fixed',
      tablesDisplay: 'grid',
      cardBackground: 'rgb(255, 255, 255)',
      historyDisplay: 'flex',
      sellerDisplay: 'flex',
      sheetsDisplay: 'grid',
      browserPosition: 'relative',
      responsiveHeaderDirection: 'column',
    });

    const expectedRequests = [
      '/favoritos/styles.css',
      ...importedStylePaths(root).map(relative => `/${relative.replace(/^static\//, '')}`),
    ];
    for (const pathname of expectedRequests) {
      assert.ok(requestedPaths.has(pathname), `asset CSS nao foi requisitado: ${pathname}`);
    }
    console.log(`OK: fachada e ${expectedRequests.length - 1} componentes CSS carregados pelo navegador.`);
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
