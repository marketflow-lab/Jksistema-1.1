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
  let applyPollStep = 0;
  const retryRequests = [], photoApplyRequests = [];
  let retryMode = 'success', releaseRetry;
  const confirmedPreview = (jobId, source, storeId) => ({
    job_id: jobId, source, store_id: storeId, status: 'ready', can_apply: true,
    coverage_complete: false, sku_coverage_complete: false, partial_application: true,
    partial_apply_scope: 'confirmed_sku_catalog_fields',
    summary: { encontrados: 5, novos: 1, preencher: 2, aplicaveis: 3, conflitos: 2, ignorados: 6, fotos_planejadas: 2 },
    items: [
      { sku: 'SAFE-001', status: 'novo', changes: [{ field: 'mlb', action: 'fill', incoming: 'MLB-SAFE' }, { field: 'link', action: 'fill', incoming: 'https://example.invalid/item' }] },
      { sku: 'LEGACY-003', status: 'preencher', changes: [{ field: 'descricao', action: 'update', incoming: 'Descrição confirmada' }, { field: 'foto', action: 'fill', incoming: 'Foto planejada' }] },
      { sku: 'DUP-NO-PHOTO', status: 'conflito', conflicts: ['sku_duplicado_ml'], changes: [] },
      { sku: 'DUP-WITH-PHOTO', status: 'conflito', conflicts: ['sku_duplicado_ml'], changes: [{ field: 'foto', action: 'fill', incoming: 'Foto confirmada' }] },
    ],
    ignored: [{ sku: 'UNCONFIRMED', reason: 'sku_identity_not_confirmed' }, { sku: '<img src=x onerror="window.catalogInjected=true">', reason: 'partial_non_store_sku_not_applied' }],
    ignored_total: 6, ignored_truncated: true,
    ignored_reason_counts: { sku_identity_not_confirmed: 1, partial_new_sku_not_applied: 2, partial_non_store_sku_not_applied: 3 },
  });
  const photoResult = (jobId, source, storeId) => ({
    job_id: jobId, source, store_id: storeId, status: 'applied', can_apply: false,
    progress: { stage: 'applied', current: 1, total: 1, percent: 100 },
    apply_result: { incluidos: 1, atualizados: 2, total: 3, fotos_planejadas: 2, fotos_salvas: 1, fotos_ignoradas: 1,
      photos_failed: [{ sku: 'LEGACY-003', reason: 'photo_download_failed' }], can_retry_photos: true },
  });
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
        if (previewMode === 'ml-confirmed-fields') {
          await json(route, confirmedPreview('job-ml-confirmed', source, storeId)); return;
        }
        if (previewMode === 'ml-no-photo') {
          const payload = confirmedPreview('job-ml-no-photo', source, storeId);
          payload.summary.fotos_planejadas = 0;
          payload.items.forEach(item => { item.changes = item.changes.filter(change => change.field !== 'foto'); });
          await json(route, payload); return;
        }
        if (previewMode === 'delayed') {
          await new Promise(resolve => { releaseDelayed = resolve; });
          await json(route, { job_id: 'job-delayed', source, store_id: storeId, status: 'ready', coverage_complete: true, can_apply: true });
          return;
        }
        if (previewMode.startsWith('incomplete')) {
          const payload = {
            job_id: previewMode === 'incomplete' ? 'job-incomplete' : `job-${previewMode}`,
            source, store_id: storeId, status: 'ready', can_apply: true,
            partial_application: true,
            partial_reasons: ['catalog_listing_details_incomplete'],
            summary: { encontrados: 6, novos: 1, preencher: 2, aplicaveis: 3, fotos_planejadas: 3, inalterados: 3, conflitos: 2, ignorados: 5 },
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
            partial_application: true, partial_reasons: ['sku_identity_incomplete'],
            summary: { encontrados: 5, novos: 2, preencher: 0, aplicaveis: 2, fotos_planejadas: 2, inalterados: 0, conflitos: 0, ignorados: 3 },
            warnings: ['sku_identity_incomplete'],
          });
          return;
        }
        if (previewMode === 'optional-incomplete') {
          await json(route, {
            job_id: 'job-optional-incomplete', source, store_id: storeId, status: 'ready',
            sku_coverage_complete: true, coverage_complete: false, can_apply: true,
            partial_application: true, partial_reasons: ['stock_balance_incomplete', 'category_detail_missing'],
            summary: { encontrados: 7, novos: 2, preencher: 1, aplicaveis: 3, inalterados: 0, conflitos: 0, ignorados: 4 },
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
      if (url.pathname === '/api/cadastro/lojas/store-a/importacoes/job-ml-confirmed/retentar-fotos') {
        assert.strictEqual(request.method(), 'POST'); assert.deepStrictEqual(request.postDataJSON(), {});
        retryRequests.push(request.postDataJSON());
        if (retryMode === 'failure') return json(route, { detail: 'Consulta de fotos indisponível' }, 503);
        const nextJobId = `job-photo-retry-${retryRequests.length}`;
        if (retryMode === 'delayed') await new Promise(resolve => { releaseRetry = resolve; });
        const payload = {
          job_id: nextJobId, source: 'mercadolivre', store_id: retryMode === 'foreign' ? 'store-b' : 'store-a',
          status: 'ready', coverage_complete: true, can_apply: true,
          summary: { encontrados: 1, preencher: 1, aplicaveis: 1, fotos_planejadas: 1 },
          items: [{ sku: 'LEGACY-003', status: 'preencher', changes: [{ field: 'foto', action: 'fill', incoming: 'Foto pendente confirmada' }] }],
        };
        if (retryMode === 'noop') {
          payload.can_apply = false; payload.summary = { encontrados: 2, inalterados: 1, ignorados: 2, aplicaveis: 0, fotos_planejadas: 0 }; payload.items = [];
          payload.ignored = [{ sku: 'LEGACY-003', reason: 'photo_sku_binding_changed' }, { sku: 'DUP-WITH-PHOTO', reason: 'photo_already_saved' }];
          payload.ignored_total = 2; payload.ignored_reason_counts = { photo_sku_binding_changed: 1, photo_already_saved: 1 };
        }
        return json(route, payload);
      }
      const photoApply = url.pathname.match(/^\/api\/cadastro\/lojas\/store-a\/importacoes\/(job-ml-confirmed|job-photo-retry-\d+)\/aplicar$/);
      if (photoApply) {
        assert.strictEqual(request.method(), 'POST'); assert.deepStrictEqual(request.postDataJSON(), {});
        photoApplyRequests.push(photoApply[1]);
        const payload = photoResult(photoApply[1], 'mercadolivre', 'store-a');
        if (photoApply[1].startsWith('job-photo-retry-')) payload.apply_result = { incluidos: 0, atualizados: 1, total: 1, fotos_planejadas: 1, fotos_salvas: 1, fotos_ignoradas: 0, photos_failed: [], can_retry_photos: false };
        return json(route, payload);
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
        if (applyMode === 'progress' && applyRequests.length > 0) {
          applyPollStep += 1;
          await json(route, {
            job_id: 'job-ready', source: 'bling', store_id: 'store-a', status: 'applying', can_apply: false,
            progress: applyPollStep === 1
              ? { stage: 'photos', current: 1, total: 2, percent: 45, message: 'Baixando capas.' }
              : { stage: 'saving', current: 2, total: 2, percent: 100, message: 'Salvando.' },
          });
          return;
        }
        await json(route, {
          job_id: 'job-ready', source: 'bling', store_id: 'store-a', store_name: 'Loja A', status: 'ready',
          coverage_complete: true, can_apply: true,
          progress: { stage: 'ready', current: 1, total: 1, percent: 100 },
          summary: { encontrados: 7, novos: 1, preencher: 2, aplicaveis: 3, inalterados: 3, conflitos: 1, ignorados: 5 },
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
        await new Promise(resolve => setTimeout(resolve, applyMode === 'progress' ? 1900 : 500));
        await json(route, {
          job_id: 'job-ready', source: 'bling', store_id: 'store-a', status: 'applied', can_apply: false,
          progress: { stage: 'applied', current: 1, total: 1, percent: 100 },
          apply_result: { fotos_planejadas: 2, fotos_salvas: 2, fotos_ignoradas: 0 },
        });
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
    const principalUrl = page.url();
    await page.locator('#btnEditarCadastro').click();
    assert.strictEqual(page.url(), principalUrl, 'Editar deve manter o usuário na tabela principal');
    assert.strictEqual(await page.evaluate(() => document.activeElement.id), 'filtro');
    assert.strictEqual(await page.locator('#painelProdutos').isVisible(), true);
    assert.match(await page.locator('#status').textContent(), /Clique no SKU ou na linha do produto/);

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
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Prévia parcial pronta para aplicar'));
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isEnabled(), true, 'prévia parcial aplicável deve habilitar a aprovação');
    assert.strictEqual(await page.locator('#importacaoCatalogoNovos').textContent(), '6');
    assert.strictEqual(await page.locator('#importacaoCatalogoPreencher').textContent(), '3');
    const partialStatus = await page.locator('#importacaoCatalogoStatus').innerText();
    assert.match(partialStatus, /as fotos ainda não foram baixadas/);
    assert.match(partialStatus, /somente as alterações previstas para cada SKU confirmado serão aplicadas/);
    assert.match(partialStatus, /serão tentadas 3 fotos previstas/);
    assert.match(partialStatus, /2 produtos têm diferenças que serão mantidas/);
    assert.match(partialStatus, /5 anúncios ou variações foram ignorados/);
    assert.doesNotMatch(partialStatus, /atualizará as descrições|MLBs.*serão|links.*serão/);
    assert.doesNotMatch(partialStatus, /catalog_listing_details_incomplete|Itens sem SKU são ignorados/);
    assert.strictEqual(await page.locator('#importacaoCatalogoStatus').getAttribute('title'), '');
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
      await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Prévia parcial pronta para aplicar'));
      assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isEnabled(), true, `${mode} deve respeitar can_apply=true`);
      await page.locator('#btnFecharImportacaoCatalogo').click();
    }

    previewMode = 'sku-incomplete';
    await page.locator('#btnImportarMercadoLivre').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Prévia parcial pronta para aplicar'));
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isEnabled(), true, 'can_apply=true deve decidir a aplicação mesmo com cobertura parcial de SKU');
    assert.match(await page.locator('#importacaoCatalogoStatus').innerText(), /3 anúncios ou variações foram ignorados/);
    assert.doesNotMatch(await page.locator('#importacaoCatalogoStatus').innerText(), /Itens sem SKU são ignorados|sku_identity_incomplete/);
    await page.locator('#btnFecharImportacaoCatalogo').click();

    previewMode = 'optional-incomplete';
    await page.locator('#btnImportarBling').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Prévia parcial pronta para aplicar'));
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isEnabled(), true, 'prévia parcial não deve bloquear quando can_apply permite');
    assert.match(await page.locator('#importacaoCatalogoStatus').getAttribute('class'), /warning/);
    assert.match(await page.locator('#importacaoCatalogoStatus').innerText(), /7 anúncios ou variações foram ignorados/);
    assert.doesNotMatch(await page.locator('#importacaoCatalogoStatus').innerText(), /Avisos:|Itens sem SKU são ignorados|stock_balance_incomplete/);
    assert.strictEqual(await page.locator('#importacaoCatalogoIgnorados').textContent(), '7');
    assert.match(await page.locator('#tBodyImportacaoCatalogo').textContent(), /SKU não informado no anúncio/);
    assert.doesNotMatch(await page.locator('#tBodyImportacaoCatalogo').textContent(), /missing_sku/);
    await page.locator('#btnFecharImportacaoCatalogo').click();

    previewMode = 'item-warnings';
    await page.locator('#btnImportarBling').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('para revisão'));
    assert.match(await page.locator('#importacaoCatalogoStatus').innerText(), /para revisão/);
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isDisabled(), true);
    const warningRows = await page.locator('#tBodyImportacaoCatalogo').textContent();
    assert.match(warningRows, /duplicata consolidada com dados comuns/);
    assert.match(warningRows, /sku_duplicado_bling_consolidado:2/);
    assert.strictEqual((warningRows.match(/sku_duplicado_bling_consolidado:2/g) || []).length, 1, 'warning repetido no mesmo item deve aparecer uma vez');
    const conflictList = page.locator('#importacaoCatalogoConflitosLista');
    assert.match(await conflictList.textContent(), /SKU DUP: há mais de um anúncio para este SKU/);
    assert.doesNotMatch(await conflictList.textContent(), /sku_duplicado_bling:consolidado|capa|foto/);
    assert.match(await conflictList.locator('li').getAttribute('title'), /sku_duplicado_bling:consolidado/);
    assert.strictEqual(await page.locator('#importacaoCatalogoConflitos').getAttribute('open'), null, 'lista extensa de diferenças deve iniciar recolhida');
    assert.match(await page.locator('#importacaoCatalogoConflitosTitulo').innerText(), /1 produto tem diferenças/);
    assert.deepStrictEqual(await page.locator('#modalImportacaoCatalogo details').evaluateAll(nodes => nodes.map(node => node.open)), [false, false], 'diferenças e detalhes técnicos devem iniciar recolhidos');
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
    applyMode = 'progress';
    applyPollStep = 0;
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
    assert.match(await page.locator('#tBodyImportacaoCatalogo').textContent(), /MLB-AUDIT/);
    assert.match(await page.locator('#tBodyImportacaoCatalogo').textContent(), /VAR-77/);
    assert.match(await page.locator('#tBodyImportacaoCatalogo').textContent(), /Exibindo 1 de 501 itens ignorados/);
    assert.strictEqual(await page.locator('#importacaoCatalogoIgnorados').textContent(), '501');
    assert.match(await page.locator('#tBodyImportacaoCatalogo').textContent(), /window\.catalogInjected=true/);
    assert.strictEqual(await page.locator('#tBodyImportacaoCatalogo img').count(), 0, 'dados externos devem ser renderizados como texto');
    assert.strictEqual(await page.evaluate(() => window.catalogInjected), undefined);

    await page.evaluate(() => {
      const button = document.querySelector('#btnAplicarImportacaoCatalogo');
      button.click();
      button.click();
    });
    await page.waitForFunction(() => window.JKCadastro.runtime.state.importacaoCatalogoAplicando === true);
    assert.strictEqual(await page.locator('#importacaoCatalogoProgressText').textContent(), '0%', 'Apply deve sair dos 100% da prévia imediatamente');
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
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('1 de 2 processadas'));
    assert.strictEqual(await page.locator('#importacaoCatalogoProgressText').textContent(), '45%');
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Salvando as informações'));
    assert.strictEqual(await page.locator('#importacaoCatalogoProgressText').textContent(), '99%', '100% deve ficar reservado para aplicação concluída');
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Aplicação concluída'));
    assert.deepStrictEqual(applyRequests, [{}], 'Aplicar deve emitir um único POST com JSON vazio');
    assert.match(await page.locator('#importacaoCatalogoStatus').innerText(), /Fotos do Mercado Livre: 2 de 2 foram baixadas e salvas; 0 não puderam ser baixadas e salvas/);
    assert.strictEqual(await page.locator('#importacaoCatalogoProgressText').textContent(), '100%');
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
    await page.locator('#btnFecharImportacaoCatalogo').click();
    previewMode = 'ml-no-photo';
    await page.locator('#btnImportarMercadoLivre').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Prévia parcial pronta'));
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isEnabled(), true, 'Confirmed fields still apply when no photo is applicable');
    assert.match(await page.locator('#importacaoCatalogoStatus').innerText(), /nenhuma foto está prevista/);
    assert.doesNotMatch(await page.locator('#importacaoCatalogoStatus').innerText(), /fotos ainda não foram baixadas|serão tentadas|atualizará as descrições/);
    assert.doesNotMatch(await conflictList.textContent(), /capa|foto/);
    await page.locator('#btnFecharImportacaoCatalogo').click();
    previewMode = 'ml-confirmed-fields';
    await page.locator('#btnImportarMercadoLivre').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Prévia parcial pronta'));
    const confirmedStatus = await page.locator('#importacaoCatalogoStatus').innerText();
    assert.match(confirmedStatus, /cadastros legados identificados/);
    assert.match(confirmedStatus, /2 fotos previstas/);
    assert.match(confirmedStatus, /1: identificação do sku não confirmada/);
    assert.match(confirmedStatus, /2: novo sku ficou de fora desta consulta parcial/);
    assert.match(confirmedStatus, /3: sku sem cadastro confirmado nesta loja ou no cadastro legado/);
    assert.doesNotMatch(confirmedStatus, /sku_identity_not_confirmed|partial_new_sku_not_applied|partial_non_store_sku_not_applied/);
    const noPhotoConflict = conflictList.locator('li').filter({ hasText: 'SKU DUP-NO-PHOTO:' });
    const photoConflict = conflictList.locator('li').filter({ hasText: 'SKU DUP-WITH-PHOTO:' });
    assert.doesNotMatch(await noPhotoConflict.textContent(), /capa|foto/);
    assert.match(await photoConflict.textContent(), /A foto prevista para este SKU será tentada/);
    assert.match(await page.locator('#tBodyImportacaoCatalogo').textContent(), /SAFE-001.*mlb.*MLB-SAFE/);
    assert.match(await page.locator('#tBodyImportacaoCatalogo').textContent(), /LEGACY-003.*Descrição confirmada/);
    assert.strictEqual(await page.locator('#tBodyImportacaoCatalogo img').count(), 0);
    assert.strictEqual(await page.evaluate(() => window.catalogInjected), undefined);
    await page.locator('#btnAplicarImportacaoCatalogo').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Fotos pendentes: SKU LEGACY-003'));
    const appliedPhotos = await page.locator('#importacaoCatalogoStatus').innerText();
    assert.match(appliedPhotos, /Cadastros: 1 incluído e 2 atualizados/);
    assert.match(appliedPhotos, /1 de 2 foram baixadas e salvas/);
    assert.match(appliedPhotos, /SKU LEGACY-003: não foi possível baixar a foto/);
    assert.doesNotMatch(appliedPhotos, /photo_download_failed|https?:/);
    const retry = page.locator('#btnRetentarFotosImportacaoCatalogo');
    assert.strictEqual(await retry.isVisible(), true); assert.strictEqual(await retry.isEnabled(), true);
    for (const width of [360, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await retry.scrollIntoViewIfNeeded();
      const layout = await page.evaluate(() => {
        const dialog = document.querySelector('.catalog-import-dialog'), footer = document.querySelector('.catalog-import-actions');
        return { dialogOverflow: dialog.scrollWidth - dialog.clientWidth, footerOverflow: footer.scrollWidth - footer.clientWidth,
          buttons: Array.from(footer.querySelectorAll('button')).filter(button => !button.hidden).map(button => {
            const rect = button.getBoundingClientRect(); return { x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom };
          }) };
      });
      assert(layout.dialogOverflow <= 1 && layout.footerOverflow <= 1, `Retry footer must fit the ${width}px viewport`);
      assert.strictEqual(layout.buttons.length, 3);
      for (let i = 0; i < layout.buttons.length; i++) {
        const box = layout.buttons[i];
        assert(box.x >= 0 && box.right <= width, `Every retry action must remain visible at ${width}px`);
        for (const other of layout.buttons.slice(i + 1)) {
          assert(box.right <= other.x || other.right <= box.x || box.bottom <= other.y || other.bottom <= box.y,
            `Retry actions must not overlap at ${width}px`);
        }
      }
      if (process.env.JK_CADASTRO_PREVIEW_DIR) {
        fs.mkdirSync(process.env.JK_CADASTRO_PREVIEW_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.JK_CADASTRO_PREVIEW_DIR, `cadastro-retry-${width}.png`) });
      }
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    const beforePhotoApplyCount = photoApplyRequests.length;
    retryMode = 'failure';
    await retry.click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('Consulta de fotos indisponível'));
    assert.strictEqual(await retry.isEnabled(), true, 'Failed new preview preserves retryable parent result');
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isDisabled(), true);
    assert.strictEqual(photoApplyRequests.length, beforePhotoApplyCount, 'Retry failure must never reapply the old job');
    retryMode = 'foreign';
    await retry.click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('outra loja'));
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isDisabled(), true);
    assert.strictEqual(photoApplyRequests.length, beforePhotoApplyCount);
    retryMode = 'delayed';
    await page.evaluate(() => { document.querySelector('#btnRetentarFotosImportacaoCatalogo').click(); document.querySelector('#btnRetentarFotosImportacaoCatalogo').click(); });
    await page.waitForFunction(() => window.JKCadastro.runtime.state.importacaoCatalogoAplicando === true);
    assert.strictEqual(await retry.isDisabled(), true); assert.strictEqual(retryRequests.length, 3, 'Double retry click emits one request');
    assert.strictEqual(await page.locator('#btnFecharImportacaoCatalogo').isDisabled(), true);
    assert.strictEqual(await page.evaluate(() => window.JKCadastro.importacoesCatalogos.aplicar()), false);
    const retryStoreError = await page.evaluate(async () => { try { await window.JKCadastro.actions.selecionarLoja('store-b'); return ''; } catch (error) { return error.message; } });
    assert.match(retryStoreError, /Aguarde a operação/);
    await page.keyboard.press('Escape'); assert.strictEqual(await page.locator('#modalImportacaoCatalogo').isVisible(), true);
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('nova prévia das fotos'));
    while (!releaseRetry) await new Promise(resolve => setTimeout(resolve, 10));
    releaseRetry();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoTitulo').textContent.includes('Revisar fotos pendentes') && document.querySelector('#btnAplicarImportacaoCatalogo').disabled === false);
    assert.strictEqual(await retry.isHidden(), true);
    assert.strictEqual(photoApplyRequests.length, beforePhotoApplyCount, 'New photo preview never applies automatically');
    assert.match(await page.locator('#tBodyImportacaoCatalogo').textContent(), /LEGACY-003.*foto/);
    assert.doesNotMatch(await page.locator('#tBodyImportacaoCatalogo').textContent(), /Descrição confirmada|SAFE-001|mlb|link/);
    await page.locator('#btnAplicarImportacaoCatalogo').click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoStatus').textContent.includes('1 de 1 foram baixadas e salvas'));
    assert.strictEqual(photoApplyRequests.at(-1), 'job-photo-retry-3', 'Apply binds to the new job, not the parent');
    assert.strictEqual(await retry.isHidden(), true, 'No retry offered after every pending photo succeeds');
    await page.locator('#btnFecharImportacaoCatalogo').click();
    previewMode = 'ml-confirmed-fields';
    await page.locator('#btnImportarMercadoLivre').click();
    await page.waitForFunction(() => document.querySelector('#btnAplicarImportacaoCatalogo').disabled === false);
    await page.evaluate(() => {
      const originalTimer = window.setTimeout;
      window.setTimeout = (callback, delay, ...args) => {
        if (delay === 750) { window.pendingPhotoApplyCleanup = () => callback(...args); return 0; }
        return originalTimer(callback, delay, ...args);
      };
      window.pendingPhotoApply = window.JKCadastro.importacoesCatalogos.aplicar();
      window.setTimeout = originalTimer;
    });
    await page.waitForFunction(() => document.querySelector('#btnRetentarFotosImportacaoCatalogo').hidden === false);
    retryMode = 'delayed'; releaseRetry = undefined;
    const applyCountBeforeCleanup = photoApplyRequests.length;
    await page.evaluate(() => { window.pendingPhotoRetry = window.JKCadastro.importacoesCatalogos.retentarFotos(); });
    await page.waitForFunction(() => window.JKCadastro.runtime.state.importacaoCatalogoAplicando === true);
    await page.evaluate(async () => { window.pendingPhotoApplyCleanup(); await window.pendingPhotoApply; });
    assert.strictEqual(await page.evaluate(() => window.JKCadastro.runtime.state.importacaoCatalogoAplicando), true,
      'Late cleanup from previous Apply must preserve the new retry busy state');
    assert.strictEqual(await page.locator('#cadastroLojaBotoes .loja-btn').first().isDisabled(), true);
    const lateCleanupStoreError = await page.evaluate(async () => { try { await window.JKCadastro.actions.selecionarLoja('store-b'); return ''; } catch (error) { return error.message; } });
    assert.match(lateCleanupStoreError, /Aguarde a operação/);
    assert.strictEqual(await page.evaluate(() => window.JKCadastro.runtime.state.storeIdSelecionado), 'store-a');
    assert.strictEqual(photoApplyRequests.length, applyCountBeforeCleanup, 'Previous cleanup must not apply the pending photo preview');
    while (!releaseRetry) await new Promise(resolve => setTimeout(resolve, 10));
    releaseRetry();
    assert.strictEqual(await page.evaluate(() => window.pendingPhotoRetry), true);
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isEnabled(), true);
    assert.strictEqual(photoApplyRequests.length, applyCountBeforeCleanup, 'The new photo preview still requires explicit Apply');
    await page.locator('#btnFecharImportacaoCatalogo').click();
    await page.locator('#btnImportarMercadoLivre').click();
    await page.waitForFunction(() => document.querySelector('#btnAplicarImportacaoCatalogo').disabled === false);
    await page.locator('#btnAplicarImportacaoCatalogo').click();
    await page.waitForFunction(() => document.querySelector('#btnRetentarFotosImportacaoCatalogo').hidden === false);
    retryMode = 'noop';
    await retry.click();
    await page.waitForFunction(() => document.querySelector('#importacaoCatalogoTitulo').textContent.includes('Revisar fotos pendentes') && document.querySelector('#importacaoCatalogoStatus').textContent.includes('Prévia de fotos concluída'));
    assert.strictEqual(await page.locator('#btnAplicarImportacaoCatalogo').isDisabled(), true, 'Changed SKU binding or saved photo never becomes an applicable retry');
    assert.match(await page.locator('#importacaoCatalogoStatus').textContent(), /consulte o catálogo novamente/);
    assert.match(await page.locator('#tBodyImportacaoCatalogo').textContent(), /Vínculo do SKU mudou|A foto deste SKU já foi salva/);
    assert.doesNotMatch(await page.locator('#importacaoCatalogoStatus').textContent(), /cadastro já está atualizado|photo_sku_binding_changed|photo_already_saved/);
    assert.strictEqual(await retry.isHidden(), true);
    await page.locator('#btnFecharImportacaoCatalogo').click();
    console.log('cadastro catalog imports: confirmed/legacy SKU fields, readable ignored reasons, independent photo results and safe new photo preview retry passed');

    productsPayload = [{
      sku: '<img src=x onerror="window.selectorInjected=true">',
      produto_bling: '<script>window.selectorInjected=true</script>',
    }];
    await page.goto('http://jk.local/cadastro_editar.html?store_id=store-a', { waitUntil: 'load' });
    await page.waitForURL('http://jk.local/cadastro.html?store_id=store-a');
    await page.waitForSelector('#tBody tr[data-sku]');
    assert.strictEqual(await page.locator('.sku-item, #listaSkus, #buscaSku').count(), 0, 'endereço antigo deve encaminhar à tabela principal sem a tela intermediária');
    assert.strictEqual(await page.evaluate(() => window.JKCadastro.runtime.state.storeIdSelecionado), 'store-a');
    assert.match(await page.locator('#tBody tr[data-sku]').innerText(), /window\.selectorInjected=true/);
    assert.strictEqual(await page.locator('#tBody tr[data-sku] img, #tBody tr[data-sku] script').count(), 0, 'SKU e título importados devem permanecer texto na tabela principal');
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
