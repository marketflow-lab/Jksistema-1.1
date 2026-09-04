'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const staticRoot = path.join(root, 'static');
const lojas = [
  { store_id: 'store-a', nome: 'Loja A' },
  { store_id: 'store-b', nome: 'Loja B' },
];

function json(route, payload, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

function contentType(filePath) {
  if (filePath.endsWith('.css')) return 'text/css; charset=utf-8';
  if (filePath.endsWith('.js')) return 'application/javascript; charset=utf-8';
  return 'application/octet-stream';
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const pageErrors = [];
  const previewRequests = [];
  const applyRequests = [];
  const cancelRequests = [];
  let previewMode = 'delayed';
  let applyMode = 'success';
  let cancelMode = 'cancelled';
  let releaseDelayed;
  let pollRequests = 0;
  let resumePollRequests = 0;
  let productsPayload = [];
  let productLoadFails = false;
  let productLoadRequests = 0;
  let appliedRemotely = false;
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.addInitScript(() => {
      localStorage.setItem('user_data', JSON.stringify({ client_id: 'cliente-catalogo' }));
    });
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname === '/cadastro.html') {
        await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: fs.readFileSync(path.join(staticRoot, 'cadastro.html')) });
        return;
      }
      if (url.pathname === '/cadastro_editar.html') {
        await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: fs.readFileSync(path.join(staticRoot, 'cadastro_editar.html')) });
        return;
      }
      if (url.pathname.startsWith('/cadastro/')) {
        const target = path.resolve(staticRoot, url.pathname.replace(/^\/+/, ''));
        assert(target.startsWith(staticRoot + path.sep));
        await route.fulfill({ status: 200, contentType: contentType(target), body: fs.readFileSync(target) });
        return;
      }
      if (url.pathname === '/auth.js') {
        await route.fulfill({
          status: 200,
          contentType: 'application/javascript; charset=utf-8',
          body: "window.obterAuthHeaders=extra=>Object.assign({Authorization:'Bearer test'},extra||{});window.verificarSessao=()=>true;",
        });
        return;
      }
      if (url.pathname === '/ncm-sync.js') {
        await route.fulfill({
          status: 200,
          contentType: 'application/javascript; charset=utf-8',
          body: 'window.NCM_SYNC={init(){},isRunning(){return false;},async iniciarSincronizacao(){}};',
        });
        return;
      }
      if (url.pathname === '/table_columns.js' || url.pathname === '/ia-sidebar.js') {
        await route.fulfill({ status: 200, contentType: 'application/javascript; charset=utf-8', body: '' });
        return;
      }
      if (url.pathname === '/api/lojas') {
        await json(route, lojas);
        return;
      }
      if (url.pathname === '/api/cadastro/fornecedores') {
        await json(route, []);
        return;
      }
      if (/^\/api\/cadastro\/lojas\/store-[ab]\/produtos$/.test(url.pathname)) {
        productLoadRequests += 1;
        if (productLoadFails) await json(route, { detail: 'falha de recarga simulada' }, 500);
        else await json(route, productsPayload);
        return;
      }
      const previewMatch = url.pathname.match(/^\/api\/cadastro\/lojas\/([^/]+)\/importacoes\/(bling|mercadolivre)\/preview$/);
      if (previewMatch) {
        const [, storeId, source] = previewMatch;
        assert.strictEqual(request.method(), 'POST');
        assert.deepStrictEqual(request.postDataJSON(), {});
        previewRequests.push({ source, storeId });
        if (previewMode === 'delayed') {
          await new Promise(resolve => { releaseDelayed = resolve; });
          await json(route, { job_id: 'job-delayed', source, store_id: storeId, status: 'ready', coverage_complete: true, can_apply: true });
          return;
        }
        if (previewMode.startsWith('incomplete')) {
          const payload = {
            job_id: previewMode === 'incomplete' ? 'job-incomplete' : `job-${previewMode}`,
            source, store_id: storeId, status: 'ready', can_apply: true,
            summary: { novos: 1, preencher: 2, inalterados: 3, conflitos: 4, ignorados: 5 },
          };
          if (previewMode === 'incomplete') payload.coverage_complete = false;
          if (previewMode === 'incomplete-null') payload.coverage_complete = null;
          if (previewMode === 'incomplete-string') payload.coverage_complete = 'false';
          await json(route, payload);
          return;
        }
        if (previewMode === 'sku-incomplete') {
          await json(route, {
            job_id: 'job-sku-incomplete', source, store_id: storeId, status: 'ready',
            sku_coverage_complete: false, coverage_complete: true, can_apply: true,
            summary: { novos: 2, preencher: 0, inalterados: 0, conflitos: 0, ignorados: 3 },
            warnings: ['sku_identity_incomplete'],
          });
          return;
        }
        if (previewMode === 'optional-incomplete') {
          await json(route, {
            job_id: 'job-optional-incomplete', source, store_id: storeId, status: 'ready',
            sku_coverage_complete: true, coverage_complete: false, can_apply: true,
            summary: { novos: 2, preencher: 1, inalterados: 0, conflitos: 0, ignorados: 4 },
            ignored: [{ reason: 'missing_sku', external_ids: { id_bling: 'sem-sku' } }],
            ignored_total: 7,
            warnings: ['stock_balance_incomplete', 'category_detail_missing'],
          });
          return;
        }
        if (previewMode === 'item-warnings') {
          await json(route, {
            job_id: 'job-item-warnings', source, store_id: storeId, status: 'ready',
            sku_coverage_complete: true, coverage_complete: true, can_apply: false,
            summary: { novos: 0, preencher: 0, inalterados: 0, conflitos: 1, ignorados: 0 },
            warnings: ['catalog_optional_warning'],
            items: [{
              sku: 'DUP', status: 'conflito', message: 'duplicata consolidada com dados comuns',
              conflicts: ['sku_duplicado_bling:consolidado'], changes: [],
              warnings: ['sku_duplicado_bling_consolidado:2', 'sku_duplicado_bling_consolidado:2'],
            }],
          });
          return;
        }
        if (previewMode === 'terminal-error') {
          await json(route, {
            job_id: 'job-terminal-error', source, store_id: storeId, status: 'error',
            coverage_complete: false, can_apply: false,
            progress: { stage: 'error', current: 0, total: 0, percent: null },
            error: { message: 'falha terminal simulada' },
          });
          return;
        }
        if (previewMode === 'noop') {
          await json(route, {
            job_id: 'job-noop', source, store_id: storeId, status: 'ready',
            coverage_complete: true, can_apply: false,
            summary: { novos: 0, preencher: 0, inalterados: 12, conflitos: 0, ignorados: 0 },
          });
          return;
        }
        if (previewMode.startsWith('review-')) {
          await json(route, {
            job_id: 'job-review-only', source, store_id: storeId, status: 'ready',
            coverage_complete: true, can_apply: false,
            summary: {
              novos: 0, preencher: 0, inalterados: 10,
              conflitos: previewMode === 'review-conflicts' ? 2 : 0,
              ignorados: previewMode === 'review-ignored' ? 1 : 0,
            },
            warnings: previewMode === 'review-warnings' ? ['aviso revisável'] : [],
          });
          return;
        }
        if (previewMode === 'resume') {
          await json(route, {
            job_id: 'job-resume', source, store_id: storeId, status: 'applying',
            coverage_complete: true, can_apply: false,
            progress: { stage: 'applying', current: 1, total: 1, percent: 100 },
          }, 202);
          return;
        }
        await json(route, {
          job_id: 'job-ready', source, store_id: storeId, status: 'queued',
          coverage_complete: false, can_apply: false,
          progress: { stage: 'queued', current: 0, total: 0, percent: null, message: 'Detalhando User Products <img src=x onerror="window.catalogInjected=true">' },
        }, 202);
        return;
      }
      if (url.pathname === '/api/cadastro/lojas/store-a/importacoes/job-ready') {
        assert.strictEqual(request.method(), 'GET');
        pollRequests += 1;
        if (appliedRemotely) {
          await json(route, {
            job_id: 'job-ready', source: 'bling', store_id: 'store-a', status: 'applied',
            coverage_complete: true, can_apply: false,
          });
          return;
        }
        await json(route, {
          job_id: 'job-ready', source: 'bling', store_id: 'store-a', store_name: 'Loja A', status: 'ready',
          coverage_complete: true, can_apply: true,
          progress: { stage: 'ready', current: 1, total: 1, percent: 100 },
          summary: { novos: 1, preencher: 2, inalterados: 3, conflitos: 1, ignorados: 5 },
          items: [
            {
              sku: '<img src=x onerror="window.catalogInjected=true">', status: 'novo', conflicts: 0,
              changes: Array.from({ length: 501 }, (_item, index) => ({
                field: `produto_bling_${index}`, action: 'fill', current: '',
                incoming: index === 0 ? '<script>window.catalogInjected=true</script>' : `valor-${index}`,
              })),
            },
            { sku: 'SKU-CONFLITO', status: 'conflito', conflicts: ['valor_divergente'], changes: [] },
          ],
          ignored: [{
            reason: 'sku_ambiguous', mlb: 'MLB-AUDIT', variation_id: 'VAR-77',
            candidates: ['<img src=x onerror="window.catalogInjected=true">', 'SKU-B'],
          }],
          ignored_total: 501,
          ignored_truncated: true,
        });
        return;
      }
      if (url.pathname === '/api/cadastro/lojas/store-a/importacoes/job-resume') {
        assert.strictEqual(request.method(), 'GET');
        resumePollRequests += 1;
        await json(route, {
          job_id: 'job-resume', source: 'bling', store_id: 'store-a', status: 'applied',
          coverage_complete: true, can_apply: false,
          progress: { stage: 'applied', current: 1, total: 1, percent: 100 },
        });
        return;
      }
      const cancelMatch = url.pathname.match(/^\/api\/cadastro\/lojas\/store-a\/importacoes\/(job-incomplete|job-ready)\/cancelar$/);
      if (cancelMatch) {
        assert.strictEqual(request.method(), 'POST');
        cancelRequests.push(request.postDataJSON());
        await new Promise(resolve => setTimeout(resolve, 300));
        const applied = cancelMode === 'applied';
        await json(route, {
          job_id: cancelMatch[1], source: cancelMatch[1] === 'job-ready' ? 'bling' : 'mercadolivre',
          store_id: 'store-a', status: applied ? 'applied' : 'cancelled',
          coverage_complete: applied, can_apply: false,
          progress: { stage: applied ? 'applied' : 'cancelled', current: 0, total: 0, percent: applied ? 100 : null },
        });
        return;
      }
      if (url.pathname === '/api/cadastro/lojas/store-a/importacoes/job-ready/aplicar') {
        assert.strictEqual(request.method(), 'POST');
        applyRequests.push(request.postDataJSON());
        if (applyMode === 'stale') {
          await json(route, { detail: { code: 'catalog_preview_stale', message: 'Gere uma nova prévia.' } }, 409);
          return;
        }
        if (applyMode === 'lost-response') {
          appliedRemotely = true;
          await route.abort('failed');
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 500));
        await json(route, { job_id: 'job-ready', source: 'bling', store_id: 'store-a', status: 'applied', can_apply: false });
        return;
      }
      await route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not found"}' });
    });

    await page.goto('http://jk.local/cadastro.html', { waitUntil: 'load' });
    await page.waitForFunction(() => window.JKCadastro && !window.JKCadastro.runtime.state.carregandoProdutos);
    assert.strictEqual(await page.locator('#btnImportarBling').isDisabled(), true, 'Todas as lojas deve bloquear Bling');
    assert.strictEqual(await page.locator('#btnImportarMercadoLivre').isDisabled(), true, 'Todas as lojas deve bloquear Mercado Livre');

    await page.getByRole('button', { name: 'Loja A', exact: true }).click();
    await page.waitForFunction(() => window.JKCadastro.runtime.state.storeIdSelecionado === 'store-a'
      && !window.JKCadastro.runtime.state.carregandoProdutos);
    assert.strictEqual(await page.locator('#btnImportarBling').isEnabled(), true);

    await page.locator('#btnImportarMercadoLivre').click();
    await page.waitForFunction(() => !document.querySelector('#modalImportacaoCatalogo').hidden);
    assert.strictEqual(await page.locator('#importacaoCatalogoTitulo').textContent(), 'Trazer catálogo do Mercado Livre');
    assert.strictEqual(await page.locator('#importacaoCatalogoStatus').textContent(), 'Iniciando consulta somente leitura no Mercado Livre...');
    assert.strictEqual(await page.locator('#btnImportarBling').isDisabled(), true, 'job ativo deve bloquear nova importação Bling');
    assert.strictEqual(await page.locator('#btnImportarMercadoLivre').isDisabled(), true, 'job ativo deve bloquear outra fonte');
    assert.strictEqual(applyRequests.length, 0, 'iniciar prévia não pode aplicar alterações');
    await page.evaluate(() => window.JKCadastro.actions.selecionarLoja('store-b'));
    await page.waitForFunction(() => window.JKCadastro.runtime.state.storeIdSelecionado === 'store-b'
      && document.querySelector('#modalImportacaoCatalogo').hidden);
    releaseDelayed();
    await page.waitForTimeout(80);
    assert.strictEqual(await page.locator('#modalImportacaoCatalogo').isHidden(), true, 'resposta atrasada da loja anterior deve ser descartada');
    assert.strictEqual(applyRequests.length, 0);

    await page.evaluate(() => window.JKCadastro.actions.selecionarLoja('store-a'));
    await page.waitForFunction(() => window.JKCadastro.runtime.state.storeIdSelecionado === 'store-a'
      && !window.JKCadastro.runtime.state.carregandoProdutos);
    previewMode = 'incomplete';
    await page.locator('#btnImportarMercadoLivre').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('sem cobertura completa'));
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isDisabled(), true, 'cobertura incompleta deve bloquear aplicação mesmo com can_apply=true');
    assert.strictEqual(await page.locator('#importacaoCatalogoNovos').textContent(), '1');
    await page.locator('#btnCancelarImportacaoCatalogo').click();
    await page.waitForFunction(() => window.JKCadastro.runtime.state.importacaoCatalogoCancelando === true);
    assert.strictEqual(await page.locator('#cadastroLojaBotoes .loja-btn').first().isDisabled(), true, 'troca de loja deve ser bloqueada durante cancelamento');
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isDisabled(), true, 'Apply deve ser bloqueado durante cancelamento');
    assert.strictEqual(await page.evaluate(() => window.JKCadastro.importacoesCatalogos.aplicar()), false);
    assert.strictEqual(applyRequests.length, 0, 'cancelamento pendente não pode disputar com Apply');
    for (const selector of ['#btnSyncNcm', '#btnEditarCadastro', '#btnIncluirCadastro', '#btnImportarColunas', '#btnAtualizarCustosImpostos']) {
      assert.strictEqual(await page.locator(selector).isDisabled(), true, `${selector} deve permanecer bloqueado durante cancelamento`);
    }
    const cancelSelectionError = await page.evaluate(async () => {
      try { await window.JKCadastro.actions.selecionarLoja('store-b'); return ''; }
      catch (error) { return error.message; }
    });
    assert.match(cancelSelectionError, /Aguarde a operação/);
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Consulta cancelada'));
    assert.strictEqual(await page.locator('#importacaoCatalogoProgressText').textContent(), 'Cancelada');
    assert.strictEqual(await page.locator('#importacaoCatalogoProgress').getAttribute('value'), '0', 'cancelamento não pode manter barra indeterminada');
    assert.deepStrictEqual(cancelRequests, [{}], 'Cancelar deve emitir um único POST de cancelamento');
    assert.strictEqual(applyRequests.length, 0, 'cancelar a consulta não pode aplicar o cadastro');
    assert.strictEqual(await page.locator('#modalImportacaoCatalogo').isVisible(), true, 'resultado do cancelamento deve permanecer visível');
    await page.locator('#btnFecharImportacaoCatalogo').click();

    previewMode = 'terminal-error';
    await page.locator('#btnImportarBling').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('falha terminal simulada'));
    assert.strictEqual(await page.locator('#importacaoCatalogoProgressText').textContent(), 'Interrompida');
    assert.strictEqual(await page.locator('#importacaoCatalogoProgress').getAttribute('value'), '0', 'erro terminal não pode manter barra indeterminada');
    await page.locator('#btnFecharImportacaoCatalogo').click();

    for (const mode of ['incomplete-missing', 'incomplete-null', 'incomplete-string']) {
      previewMode = mode;
      await page.locator('#btnImportarMercadoLivre').click();
      await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('sem cobertura completa'));
      assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isDisabled(), true, `${mode} deve falhar fechado`);
      await page.locator('#btnFecharImportacaoCatalogo').click();
    }

    previewMode = 'sku-incomplete';
    await page.locator('#btnImportarMercadoLivre').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('sem cobertura completa de SKU'));
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isDisabled(), true, 'cobertura de SKU falsa deve bloquear mesmo com can_apply=true');
    assert.match(await page.locator('#importacaoCatalogoStatus').innerText(), /Avisos: 1/);
    assert.match(await page.locator('#importacaoCatalogoStatus').innerText(), /Ignorados: 3/);
    assert.match(await page.locator('#importacaoCatalogoStatus').innerText(), /Itens sem SKU são ignorados/);
    await page.locator('#btnFecharImportacaoCatalogo').click();

    previewMode = 'optional-incomplete';
    await page.locator('#btnImportarBling').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('dados opcionais incompletos'));
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isEnabled(), true, 'dados opcionais incompletos não devem bloquear quando a cobertura de SKU e can_apply permitem');
    assert.match(await page.locator('#importacaoCatalogoStatus').getAttribute('class'), /warning/);
    assert.match(await page.locator('#importacaoCatalogoStatus').innerText(), /Avisos: 2/);
    assert.match(await page.locator('#importacaoCatalogoStatus').innerText(), /Ignorados: 7/);
    assert.match(await page.locator('#importacaoCatalogoStatus').innerText(), /Itens sem SKU são ignorados/);
    assert.strictEqual(await page.locator('#importacaoCatalogoIgnorados').textContent(), '7');
    assert.match(await page.locator('#tBodyImportacaoCatalogo').innerText(), /missing_sku/);
    await page.locator('#btnFecharImportacaoCatalogo').click();

    previewMode = 'item-warnings';
    await page.locator('#btnImportarBling').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Avisos: 2'));
    assert.match(await page.locator('#importacaoCatalogoStatus').innerText(), /para revisão/);
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isDisabled(), true);
    const warningRows = await page.locator('#tBodyImportacaoCatalogo').innerText();
    assert.match(warningRows, /duplicata consolidada com dados comuns/);
    assert.match(warningRows, /sku_duplicado_bling_consolidado:2/);
    assert.strictEqual((warningRows.match(/sku_duplicado_bling_consolidado:2/g) || []).length, 1, 'warning repetido no mesmo item deve aparecer uma vez');
    assert.match(await page.locator('#importacaoCatalogoConflitosLista').innerText(), /sku_duplicado_bling:consolidado/);
    await page.locator('#btnFecharImportacaoCatalogo').click();

    for (const mode of ['review-conflicts', 'review-ignored', 'review-warnings']) {
      previewMode = mode;
      await page.locator('#btnImportarMercadoLivre').click();
      await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('para revisão'));
      assert.match(await page.locator('#importacaoCatalogoStatus').getAttribute('class'), /warning/);
      assert.doesNotMatch(await page.locator('#importacaoCatalogoStatus').innerText(), /já está atualizado/);
      assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isDisabled(), true);
      await page.locator('#btnFecharImportacaoCatalogo').click();
    }

    previewMode = 'noop';
    await page.locator('#btnImportarMercadoLivre').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('já está atualizado'));
    assert.match(await page.locator('#importacaoCatalogoStatus').getAttribute('class'), /success/);
    assert.doesNotMatch(await page.locator('#importacaoCatalogoStatus').getAttribute('class'), /error/);
    await page.locator('#btnFecharImportacaoCatalogo').click();
    assert.deepStrictEqual(cancelRequests, [{}], 'Fechar pelo X não deve cancelar o trabalho');

    previewMode = 'poll';
    cancelMode = 'applied';
    await page.locator('#btnImportarBling').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Detalhando User Products'));
    assert.strictEqual(await page.locator('#importacaoCatalogoStatus img').count(), 0, 'mensagem de progresso deve ser texto literal');
    assert.strictEqual(await page.locator('#importacaoCatalogoProgress').getAttribute('value'), null, 'progresso local sem percentual global deve ser indeterminado');
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Prévia pronta'));
    const loadsBeforeCancelApplied = productLoadRequests;
    await page.locator('#btnCancelarImportacaoCatalogo').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('aplicada com sucesso'));
    assert(productLoadRequests > loadsBeforeCancelApplied, 'cancelamento que encontra applied deve recarregar os produtos');
    assert.strictEqual(await page.evaluate(() => window.JKCadastro.runtime.state.importacaoCatalogoCancelando), false);
    await page.locator('#btnFecharImportacaoCatalogo').click();
    cancelMode = 'cancelled';

    previewMode = 'resume';
    await page.locator('#btnImportarBling').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Importação aplicada com sucesso'));
    assert(resumePollRequests >= 1, 'estado applying deve ser acompanhado até applied');
    assert.strictEqual(await page.locator('#cadastroLojaBotoes .loja-btn').first().isEnabled(), true, 'seletor deve ser liberado depois de applied');
    await page.locator('#btnFecharImportacaoCatalogo').click();

    previewMode = 'poll';
    await page.locator('#btnImportarBling').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Prévia pronta'));
    assert(pollRequests >= 1, 'trabalho em andamento deve ser acompanhado por GET');
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isEnabled(), true);
    assert.match(await page.locator('#tBodyImportacaoCatalogo').innerText(), /MLB-AUDIT/);
    assert.match(await page.locator('#tBodyImportacaoCatalogo').innerText(), /VAR-77/);
    assert.match(await page.locator('#tBodyImportacaoCatalogo').innerText(), /Exibindo 1 de 501 itens ignorados/);
    assert.strictEqual(await page.locator('#importacaoCatalogoIgnorados').textContent(), '501');
    assert.match(await page.locator('#tBodyImportacaoCatalogo').innerText(), /window\.catalogInjected=true/);
    assert.strictEqual(await page.locator('#tBodyImportacaoCatalogo img').count(), 0, 'dados externos devem ser renderizados como texto');
    assert.strictEqual(await page.evaluate(() => window.catalogInjected), undefined);

    await page.evaluate(() => {
      const button = document.querySelector('#btnAplicarImportacaoCatalogo');
      button.click();
      button.click();
    });
    await page.waitForFunction(() => window.JKCadastro.runtime.state.importacaoCatalogoAplicando === true);
    assert.strictEqual(await page.locator('#cadastroLojaBotoes .loja-btn').first().isDisabled(), true, 'troca de loja deve ser bloqueada durante Apply');
    for (const selector of ['#btnSyncNcm', '#btnEditarCadastro', '#btnIncluirCadastro', '#btnImportarColunas', '#btnAtualizarCustosImpostos']) {
      assert.strictEqual(await page.locator(selector).isDisabled(), true, `${selector} deve permanecer bloqueado durante Apply`);
    }
    const selectionError = await page.evaluate(async () => {
      try {
        await window.JKCadastro.actions.selecionarLoja('store-b');
        return '';
      } catch (error) {
        return error.message;
      }
    });
    assert.match(selectionError, /Aguarde a operação/);
    for (let i = 0; i < 4; i += 1) {
      await page.keyboard.press('Tab');
      assert.strictEqual(await page.evaluate(() => document.activeElement.id), 'importacaoCatalogoDialog', 'foco não pode escapar durante Apply');
    }
    assert.strictEqual(await page.evaluate(() => window.JKCadastro.runtime.state.storeIdSelecionado), 'store-a');
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('aplicada com sucesso'));
    assert.deepStrictEqual(applyRequests, [{}], 'Aplicar deve emitir um único POST com JSON vazio');
    assert.strictEqual(await page.locator('#cadastroLojaBotoes .loja-btn').first().isEnabled(), true, 'troca de loja deve ser liberada após Apply');

    await page.locator('#btnFecharImportacaoCatalogo').click();
    applyMode = 'success';
    await page.locator('#btnImportarBling').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Prévia pronta'));
    productLoadFails = true;
    await page.locator('#btnAplicarImportacaoCatalogo').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('lista não pôde ser atualizada'));
    assert.match(await page.locator('#importacaoCatalogoStatus').getAttribute('class'), /warning/);
    assert.doesNotMatch(await page.locator('#importacaoCatalogoStatus').innerText(), /nova prévia/i);
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isDisabled(), true);
    productLoadFails = false;
    await page.locator('#btnFecharImportacaoCatalogo').click();
    applyMode = 'lost-response';
    await page.locator('#btnImportarBling').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Prévia pronta'));
    await page.locator('#btnAplicarImportacaoCatalogo').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('aplicada com sucesso'));
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isDisabled(), true, 'GET deve reconciliar o commit quando a resposta do POST se perde');
    await page.locator('#btnFecharImportacaoCatalogo').click();
    appliedRemotely = false;
    applyMode = 'stale';
    await page.locator('#btnImportarBling').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Prévia pronta'));
    await page.locator('#btnAplicarImportacaoCatalogo').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Gere uma nova prévia'));
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isDisabled(), true, 'falha ao aplicar deve invalidar a prévia no navegador');
    assert.deepStrictEqual(applyRequests, [{}, {}, {}, {}], 'prévia inválida não pode ser reenviada pelo mesmo modal');
    assert.deepStrictEqual(cancelRequests, [{}, {}], 'cada cancelamento deve emitir um único POST');
    assert.deepStrictEqual(previewRequests.map(item => `${item.storeId}:${item.source}`), [
      'store-a:mercadolivre', 'store-a:mercadolivre', 'store-a:bling',
      'store-a:mercadolivre', 'store-a:mercadolivre', 'store-a:mercadolivre',
      'store-a:mercadolivre',
      'store-a:bling', 'store-a:bling', 'store-a:mercadolivre',
      'store-a:mercadolivre', 'store-a:mercadolivre', 'store-a:mercadolivre',
      'store-a:bling', 'store-a:bling', 'store-a:bling',
      'store-a:bling', 'store-a:bling', 'store-a:bling',
    ]);
    productsPayload = [{
      sku: '<img src=x onerror="window.selectorInjected=true">',
      produto_bling: '<script>window.selectorInjected=true</script>',
    }];
    await page.goto('http://jk.local/cadastro_editar.html?store_id=store-a', { waitUntil: 'load' });
    await page.waitForSelector('.sku-item');
    assert.match(await page.locator('.sku-item').innerText(), /window\.selectorInjected=true/);
    assert.strictEqual(await page.locator('.sku-item img, .sku-item script').count(), 0, 'SKU e título importados devem permanecer texto');
    assert.strictEqual(await page.evaluate(() => window.selectorInjected), undefined);
    assert.deepStrictEqual(pageErrors, []);
    console.log('cadastro catalog imports browser: OK');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
