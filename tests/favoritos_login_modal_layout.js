'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const repoRoot = path.resolve(__dirname, '..');
const styles = fs.readFileSync(path.join(repoRoot, 'static', 'favoritos', 'styles.css'), 'utf8');

async function checkLayout(page, viewport) {
  await page.setViewportSize(viewport);
  await page.setContent(`<!doctype html>
    <html class="jk-electron-tab-shell">
      <body class="ml-work-modal-open">
        <div class="jk-nav-card-group"><button type="button">Voltar</button></div>
        <section class="ml-work-modal">
          <div class="ml-work-modal-dialog">
            <header class="ml-work-modal-header">
              <div><h2 class="ml-work-modal-title">Login Avant Pro</h2></div>
              <div class="ml-work-modal-live-status">Faça o login e continue.</div>
              <div class="ml-work-modal-actions">
                <button class="ml-work-modal-control">Continuar favoritos</button>
                <button id="ml-work-modal-close" class="ml-work-modal-close">Fechar</button>
              </div>
            </header>
          </div>
        </section>
      </body>
    </html>`);
  await page.addStyleTag({ content: styles });
  return page.evaluate(() => {
    const dialog = document.querySelector('.ml-work-modal-dialog').getBoundingClientRect();
    const close = document.getElementById('ml-work-modal-close').getBoundingClientRect();
    const actions = document.querySelector('.ml-work-modal-actions').getBoundingClientRect();
    return {
      navDisplay: getComputedStyle(document.querySelector('.jk-nav-card-group')).display,
      dialog: { top: dialog.top, right: dialog.right, bottom: dialog.bottom },
      close: { top: close.top, right: close.right, bottom: close.bottom },
      actions: { top: actions.top, right: actions.right, bottom: actions.bottom },
      viewport: { width: innerWidth, height: innerHeight }
    };
  });
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    for (const viewport of [{ width: 1440, height: 850 }, { width: 720, height: 800 }]) {
      const layout = await checkLayout(page, viewport);
      assert.strictEqual(layout.navDisplay, 'none', 'Voltar deve ficar oculto enquanto o modal estiver aberto');
      assert.ok(layout.dialog.top >= 51, `modal deve iniciar abaixo da barra do shell: ${JSON.stringify(layout)}`);
      assert.ok(layout.dialog.right <= layout.viewport.width, `modal nao pode ultrapassar a direita: ${JSON.stringify(layout)}`);
      assert.ok(layout.dialog.bottom <= layout.viewport.height, `modal nao pode ultrapassar a base: ${JSON.stringify(layout)}`);
      assert.ok(layout.close.top >= layout.dialog.top, `Fechar deve permanecer dentro do modal: ${JSON.stringify(layout)}`);
      assert.ok(layout.close.right <= layout.dialog.right, `Fechar deve permanecer clicavel dentro do modal: ${JSON.stringify(layout)}`);
      assert.ok(layout.actions.bottom <= layout.dialog.bottom, `acoes devem caber no modal: ${JSON.stringify(layout)}`);
    }
    console.log('Favoritos login modal layout checks passed');
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
