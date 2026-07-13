const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const staticRoot = path.join(root, 'static');

async function waitForCondition(predicate, message, timeoutMs = 4000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error(message || 'Condicao de teste nao foi atendida.');
}

const contentTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
};

function startStaticServer() {
  const server = http.createServer((req, res) => {
    const pathname = decodeURIComponent(String(req.url || '/').split('?')[0]);
    if (pathname === '/' || pathname === '/test.html') {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      res.end('<!doctype html><html><head><meta charset="utf-8"><title>Black Jhon smoke</title></head><body><main>Modulo teste</main><script src="/ia-sidebar.js"></script></body></html>');
      return;
    }
    const relative = pathname.replace(/^\/+/, '');
    const target = path.resolve(staticRoot, relative);
    if (!target.startsWith(staticRoot + path.sep) || !fs.existsSync(target) || !fs.statSync(target).isFile()) {
      res.writeHead(404);
      res.end('not found');
      return;
    }
    res.writeHead(200, {
      'content-type': contentTypes[path.extname(target).toLowerCase()] || 'application/octet-stream',
      'cache-control': 'no-store',
    });
    fs.createReadStream(target).pipe(res);
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      resolve({ server, baseUrl: `http://127.0.0.1:${address.port}` });
    });
  });
}

async function newScenario(browser, baseUrl, options = {}) {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on('dialog', dialog => dialog.type() === 'confirm' ? dialog.accept() : dialog.dismiss());
  if (options.fakeClock === true) {
    await page.clock.install({ time: new Date('2026-07-10T12:00:00Z') });
  }
  const calls = {
    codexTasks: [],
    codexTaskGets: 0,
    codexTaskGetUrls: [],
    codexTaskDetailGets: 0,
    codexStatus: 0,
    codexTaskCancels: 0,
    codexTaskApprovals: 0,
    iaChat: [],
    actionProposals: [],
    attachmentContentTypes: [],
    advancedAdmin: [],
    advancedPayloads: [],
    activeAdvancedAdmin: 0,
    maxActiveAdvancedAdmin: 0,
    reportGets: 0,
    iaModels: 0,
    historyDeletes: [],
    memoryResets: 0,
    restoredTaskGets: 0,
    overlapOldGets: 0,
    reportSettingsGets: 0,
    reportSettingsPuts: [],
  };
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(String(error && error.message || error)));

  await page.addInitScript(({ full, seedLegacy, seedActiveTask, testMode }) => {
    Object.defineProperty(window, 'Worker', { configurable: true, value: undefined });
    window.__JK_CODEX_TEST_MODE__ = testMode === true;
    localStorage.setItem('permissions', JSON.stringify(full ? { full: true } : {}));
    localStorage.setItem('user_data', JSON.stringify({ username: full ? 'admin' : 'operador', client_id: '000002' }));
    localStorage.setItem('access_token', 'black-jhon-browser-test');
    if (seedLegacy) {
      localStorage.setItem('jk_codex_settings_v1', JSON.stringify({
        access: 'full_access',
        model: 'gpt-5.4',
        reasoning_effort: 'low',
        speed: 'fast',
        paths: ['C:/segredo-admin'],
      }));
      localStorage.setItem('jk_codex_thread_id', 'thread-admin-legado');
      localStorage.setItem('jk_codex_history_v1', JSON.stringify([
        { role: 'assistant', text: 'SEGREDO DO HISTORICO ADMIN' },
      ]));
    }
    if (seedActiveTask) {
      const username = full ? 'admin' : 'operador';
      localStorage.setItem(`jk_codex_panel_state_v2_000002_${username}`, JSON.stringify({
        conversation_id: 'conversation-restored',
        task_id: seedActiveTask,
        thread_id: 'thread-restored',
        history_visible: false,
      }));
    }
  }, {
    full: options.full === true,
    seedLegacy: options.seedLegacy === true,
    seedActiveTask: options.seedActiveTask || '',
    testMode: options.fakeClock === true,
  });

  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    const method = request.method().toUpperCase();
    const canonicalConversationId = options.full === true ? 'app-admin-browser' : 'app-operador-browser';
    const json = (status, payload) => route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });
    const trackedAdminJson = async (status, payload) => {
      calls.activeAdvancedAdmin += 1;
      calls.maxActiveAdvancedAdmin = Math.max(calls.maxActiveAdvancedAdmin, calls.activeAdvancedAdmin);
      await new Promise(resolve => setTimeout(resolve, 5));
      calls.activeAdvancedAdmin -= 1;
      return json(status, payload);
    };

    if (pathname === '/api/codex/status') {
      calls.codexStatus += 1;
      return json(200, {
        success: true,
        ready: options.statusReady !== false,
        message: options.statusReady === false ? 'Codex indisponivel no teste.' : 'Black Jhon pronto com Codex como IA principal.',
        defaults: { model: 'gpt-5.5', reasoning_effort: 'xhigh', speed: 'standard' },
        conversation: { conversation_id: canonicalConversationId, channel: 'app', generation: 1, state: 'active', can_reset: true, queue: { running: 0, pending: 0 } },
      });
    }
    if (pathname === '/api/codex/tasks' && method === 'POST') {
      const payload = request.postDataJSON();
      calls.codexTasks.push(payload);
      if (Number(options.taskStatus || 0) > 0) {
        return json(Number(options.taskStatus), {
          detail: options.taskDetail || (options.taskStatus === 503
            ? 'Dependencia openai-codex nao instalada no runtime Python.'
            : 'Sem permissao para usar este recurso.'),
        });
      }
      if (options.overlapTasks === true) {
        const taskNumber = calls.codexTasks.length;
        const taskId = `task-overlap-${taskNumber}`;
        return json(200, {
          success: true,
          task: {
            task_id: taskId,
            conversation_id: canonicalConversationId,
            conversation_generation: 1,
            conversation_state: 'active',
            channel: 'app',
            prompt: payload.prompt,
            sandbox: payload.sandbox,
            status: taskNumber === 1 ? 'running' : 'completed',
            final_response: taskNumber === 1 ? '' : 'RESPOSTA NOVA DEVE PERMANECER',
          },
        });
      }
      return json(200, {
        success: true,
        task: {
          task_id: 'task-browser-1',
          conversation_id: canonicalConversationId,
          conversation_generation: 1,
          conversation_state: 'active',
          channel: 'app',
          prompt: payload.prompt,
          sandbox: payload.sandbox,
          status: options.taskRunning === true ? 'running' : 'completed',
          final_response: options.taskResponse || options.largeTaskResponse || (options.taskRunning === true ? '' : 'Resposta primaria de teste.'),
          paths: payload.paths || [],
        },
      });
    }
    if (pathname === '/api/codex/tasks' && method === 'GET') {
      calls.codexTaskGets += 1;
      calls.codexTaskGetUrls.push(request.url());
      return json(200, { success: true, tasks: options.historyTasks || [] });
    }
    if (pathname === '/api/codex/conversations/current/reset' && method === 'POST') {
      calls.memoryResets += 1;
      return json(200, {
        success: true,
        conversation: { conversation_id: canonicalConversationId, channel: 'app', generation: 2, state: 'active', can_reset: true, queue: { running: 0, pending: 0 } },
      });
    }
    if (pathname.startsWith('/api/codex/conversations/') && method === 'DELETE') {
      calls.historyDeletes.push(pathname);
      return json(200, { success: true, deleted: true, task_ids: ['history-task-1'] });
    }
    if (options.seedActiveTask && pathname === `/api/codex/tasks/${options.seedActiveTask}` && method === 'GET') {
      calls.restoredTaskGets += 1;
      return json(200, {
        success: true,
        task: {
          task_id: options.seedActiveTask,
          conversation_id: canonicalConversationId,
          conversation_generation: 1,
          conversation_state: 'active',
          channel: 'app',
          thread_id: 'thread-restored',
          status: 'running',
          prompt: 'Tarefa restaurada',
          final_response: '',
        },
      });
    }
    if (options.overlapTasks === true && pathname === '/api/codex/tasks/task-overlap-1' && method === 'GET') {
      calls.overlapOldGets += 1;
      await new Promise(resolve => setTimeout(resolve, 180));
      return json(200, {
        success: true,
        task: {
          task_id: 'task-overlap-1',
          conversation_id: canonicalConversationId,
          conversation_generation: 1,
          conversation_state: 'active',
          channel: 'app',
          thread_id: 'thread-antiga-invalida',
          status: 'completed',
          final_response: 'RESPOSTA ANTIGA NAO DEVE SOBRESCREVER',
        },
      });
    }
    if (pathname === '/api/admin/codex/attachments' && method === 'POST') {
      calls.attachmentContentTypes.push(String(request.headers()['content-type'] || ''));
      return json(200, {
        success: true,
        attachments: [{
          id: 'attachment-browser-1',
          name: 'teste.txt',
          mime_type: 'text/plain',
          size: 5,
          relative_path: '.codex-remote-attachments/000002/browser/teste.txt',
        }],
      });
    }
    if (pathname === '/api/codex/tasks/task-browser-1') {
      calls.codexTaskDetailGets += 1;
      const completed = options.taskRunning === true && calls.codexTaskDetailGets > 1;
      return json(200, {
        success: true,
        task: {
          task_id: 'task-browser-1',
          conversation_id: canonicalConversationId,
          conversation_generation: 1,
          conversation_state: 'active',
          channel: 'app',
          thread_id: completed ? 'thread-old-poll' : '',
          status: options.taskRunning === true ? (completed ? 'completed' : 'running') : 'completed',
          final_response: options.taskResponse || options.largeTaskResponse || (completed ? 'RESPOSTA ANTIGA NAO DEVE VOLTAR' : 'Resposta primaria de teste.'),
        },
      });
    }
    if (pathname === '/api/codex/tasks/task-browser-1/cancel' && method === 'POST') {
      calls.codexTaskCancels += 1;
      return json(200, {
        success: true,
        task: { task_id: 'task-browser-1', status: 'canceled', final_response: '' },
      });
    }
    if (pathname === '/api/admin/codex/tasks/task-browser-1/approve' && method === 'POST') {
      calls.codexTaskApprovals += 1;
      return json(200, {
        success: true,
        task: { task_id: 'task-browser-1', status: 'running', final_response: '' },
      });
    }
    if (pathname === '/api/admin/codex/actions/proposals' && method === 'POST') {
      calls.actionProposals.push(request.postDataJSON());
      return json(200, { success: true, matched: false });
    }
    if (pathname === '/api/admin/codex/assistant/report-settings' && method === 'GET') {
      calls.reportSettingsGets += 1;
      return json(200, {
        success: true,
        settings: {
          global: { margin_coverage_min: 0.95, default_lead_time_days: 180, review_cycle_days: 90, target_margin_pct: 20 },
          stores: {}, suppliers: {}, skus: {},
        },
      });
    }
    if (pathname === '/api/admin/codex/assistant/report-settings' && method === 'PUT') {
      const payload = request.postDataJSON();
      calls.reportSettingsPuts.push(payload);
      return json(200, { success: true, settings: payload.settings });
    }
    if (pathname === '/api/admin/codex/assistant/suggestions') {
      calls.advancedAdmin.push(pathname);
      calls.advancedPayloads.push({ path: pathname, payload: null });
      return trackedAdminJson(200, { success: true, suggestions: options.coordinatorSuggestions || [] });
    }
    if (pathname === '/api/admin/codex/assistant/proactive/run') {
      calls.advancedAdmin.push(pathname);
      calls.advancedPayloads.push({ path: pathname, payload: request.postDataJSON() });
      return trackedAdminJson(200, { success: true, suggestions: [] });
    }
    if (pathname === '/api/admin/codex/assistant/daily-analysis/run') {
      calls.advancedAdmin.push(pathname);
      const payload = request.postDataJSON();
      calls.advancedPayloads.push({ path: pathname, payload });
      if (options.largeDailyReport) {
        return trackedAdminJson(200, {
          success: true,
          status: 'completed',
          suggestions: [],
          report: {
            report_id: 'report-browser-large',
            title: 'Relatorio grande de teste',
            chat_text_preview: options.largeDailyReport.slice(0, 600),
            chat_text_length: options.largeDailyReport.length,
            chat_text_truncated: true,
            chat_download_formats: [],
            downloads: {},
            status_steps: ['Relatorio compacto pronto'],
          },
        });
      }
      return trackedAdminJson(200, { success: true, status: 'skipped', suggestions: [] });
    }
    if (pathname === '/api/admin/codex/assistant/weekly-analysis/run') {
      calls.advancedAdmin.push(pathname);
      calls.advancedPayloads.push({ path: pathname, payload: request.postDataJSON() });
      if (options.largeDailyReport) {
        return trackedAdminJson(200, {
          success: true,
          status: 'completed',
          suggestions: [],
          report: {
            report_id: 'report-browser-large',
            title: 'Relatorio grande de teste',
            chat_text_preview: options.largeDailyReport.slice(0, 600),
            chat_text_length: options.largeDailyReport.length,
            chat_text_truncated: true,
            chat_download_formats: [],
            downloads: {},
            status_steps: ['Relatorio compacto pronto'],
          },
        });
      }
      return trackedAdminJson(200, { success: true, status: 'skipped', suggestions: [] });
    }
    if (pathname === '/api/admin/codex/assistant/reports/report-browser-large' && method === 'GET') {
      calls.reportGets += 1;
      return json(200, {
        success: true,
        report: { report_id: 'report-browser-large', chat_text: options.largeDailyReport || '' },
      });
    }
    if (pathname === '/api/admin/codex/assistant/reports' && method === 'POST') {
      calls.advancedAdmin.push(pathname);
      calls.advancedPayloads.push({ path: pathname, payload: request.postDataJSON() });
      return json(200, {
        success: true,
        report: {
          report_id: 'report-browser-manual',
          title: 'Relatorio manual',
          chat_text: options.largeManualReport || 'Relatorio manual curto.',
          chat_download_formats: [],
          downloads: {},
        },
      });
    }
    if (pathname === '/api/admin/codex/capabilities/modules') {
      calls.advancedAdmin.push(pathname);
      return json(200, { success: true, suggestions: [], modules: [] });
    }
    if (pathname === '/api/ia/chat' && method === 'POST') {
      calls.iaChat.push(request.postDataJSON());
      return json(200, { success: true, resposta: 'Resposta da IA secundaria de teste.', model: 'fallback-test' });
    }
    if (pathname === '/api/ia/modelos') {
      calls.iaModels += 1;
      return json(200, { success: true, pode_escolher_modelo_chat: false, defaults: { chat: 'gpt-5.4-mini' } });
    }
    if (pathname === '/api/ia/conversas/listar') return json(200, { success: true, conversas: [] });
    if (pathname === '/api/ia/conversas/salvar') return json(200, { success: true });
    if (pathname.startsWith('/api/ia/conversas/')) return json(200, { success: true, mensagens: [] });
    if (pathname === '/api/mercadolivre/perguntas/aprovacoes') return json(200, { success: true, pendentes: [] });
    return json(200, { success: true, suggestions: [], tasks: [] });
  });

  await page.goto(`${baseUrl}/test.html${options.pageQuery || ''}`, { waitUntil: 'domcontentloaded' });
  await page.locator('#jk-ia-light-fab').click();
  await page.locator('#jk-ia-fab').waitFor({ state: 'visible' });
  return { context, page, calls, pageErrors };
}

async function run() {
  const { server, baseUrl } = await startStaticServer();
  const browser = await chromium.launch({ headless: true });
  try {
    {
      const scenario = await newScenario(browser, baseUrl, {
        full: true,
        statusReady: true,
        pageQuery: '?token=segredo-nao-enviar&code=oauth-nao-enviar#hash-secreto',
      });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      const openingMetrics = await page.evaluate(async () => {
        document.getElementById('jk-codex-close')?.click();
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        const before = Number(performance.memory && performance.memory.usedJSHeapSize || 0);
        const started = performance.now();
        document.getElementById('jk-ia-fab')?.click();
        while (!document.getElementById('jk-codex-panel')?.classList.contains('aberto') && performance.now() - started < 1000) {
          await new Promise(resolve => requestAnimationFrame(resolve));
        }
        const after = Number(performance.memory && performance.memory.usedJSHeapSize || 0);
        return { elapsed: performance.now() - started, heapDelta: after - before };
      });
      assert.ok(openingMetrics.elapsed < 500, `painel levou ${openingMetrics.elapsed}ms para ficar visivel`);
      assert.ok(openingMetrics.heapDelta < 100 * 1024 * 1024, `painel cresceu ${openingMetrics.heapDelta} bytes no heap`);
      assert.strictEqual(await page.locator('#jk-ia-fab').count(), 1);
      assert.strictEqual(await page.locator('#jk-ia-light-fab').count(), 0);
      assert.strictEqual(await page.locator('#jk-codex-fab').count(), 0);
      assert.strictEqual(await page.locator('#jk-global-ai-sidebar').count(), 0);
      assert.strictEqual(await page.locator('#jk-ia-fab').getAttribute('data-primary-ai'), 'codex');
      assert.strictEqual(await page.locator('#jk-codex-header h3').textContent(), 'Black Jhon');
      assert.strictEqual(await page.locator('#jk-codex-header span').textContent(), 'Codex principal · IA integrada do sistema');
      assert.strictEqual(calls.codexTaskGets, 0, 'abrir painel nao consulta historico do servidor');
      assert.deepStrictEqual(calls.advancedAdmin, [], 'abrir painel nao dispara rotinas administrativas');
      assert.strictEqual(calls.iaModels, 0, 'abrir Black Jhon nao carrega modelos do painel legado');

      await page.locator('#jk-codex-report-settings').click();
      await page.locator('#jk-codex-report-settings-dialog:not([hidden])').waitFor();
      assert.strictEqual(calls.reportSettingsGets, 1);
      await page.locator('#jk-report-target-margin').fill('24');
      await page.locator('#jk-codex-report-settings-save').click();
      await waitForCondition(() => calls.reportSettingsPuts.length === 1, 'configuracoes do relatorio nao foram salvas');
      assert.strictEqual(calls.reportSettingsPuts[0].settings.global.target_margin_pct, 24);
      await page.locator('#jk-codex-report-settings-dialog').waitFor({ state: 'hidden' });

      await page.locator('#jk-codex-input').fill('Responda apenas com um teste de leitura.');
      await page.locator('#jk-codex-readonly').evaluate(button => button.click());
      await page.waitForFunction(() => document.querySelector('#jk-codex-messages')?.textContent.includes('Resposta primaria de teste.'));
      assert.strictEqual(calls.codexTasks.length, 1);
      assert.strictEqual(calls.codexTasks[0].sandbox, 'read_only');
      assert.strictEqual(calls.codexTasks[0].model, 'gpt-5.5');
      assert.strictEqual(calls.codexTasks[0].screen_context.context_mode, 'manual');
      assert.ok(calls.codexTasks[0].screen_context.dom_scan.scanned_elements <= 300);
      assert.doesNotMatch(calls.codexTasks[0].screen_context.url_completa, /segredo|token|code|hash/i);
      assert.strictEqual(calls.codexTasks[0].screen_context.url_completa, `${baseUrl}/test.html`);
      assert.strictEqual(calls.iaChat.length, 0);

      await page.locator('#jk-codex-input').fill('Segunda consulta para validar cache de contexto.');
      await page.locator('#jk-codex-readonly').evaluate(button => button.click());
      await page.waitForFunction(() => document.querySelectorAll('#jk-codex-messages .jk-codex-msg.user').length >= 2);
      assert.strictEqual(calls.codexTasks.length, 2);
      assert.strictEqual(calls.codexTasks[1].screen_context.dom_scan.sequence, calls.codexTasks[0].screen_context.dom_scan.sequence);
      assert.strictEqual(calls.codexTasks[1].screen_context.dom_scan.cache_hit, true);

      await page.locator('#jk-codex-history-toggle').evaluate(button => button.click());
      await page.waitForFunction(() => document.querySelector('#jk-codex-history-panel')?.classList.contains('ativo'));
      assert.strictEqual(calls.codexTaskGets, 1);
      assert.match(calls.codexTaskGetUrls[0], /[?&]summary=true(?:&|$)/);

      await page.locator('#jk-codex-file-input').setInputFiles({ name: 'teste.txt', mimeType: 'text/plain', buffer: Buffer.from('teste') });
      await page.waitForFunction(() => document.querySelector('#jk-codex-attachment-chips')?.textContent.includes('teste.txt'));
      assert.strictEqual(calls.attachmentContentTypes.length, 1);
      assert.match(calls.attachmentContentTypes[0], /^multipart\/form-data;\s*boundary=/i);

      await page.evaluate(() => window.JKIASidebarNotifyApproval({
        id: 'approval-browser-1',
        tipo: 'pergunta',
        loja: 'Loja teste',
        sku: 'SKU-1',
        titulo: 'Produto teste',
        pergunta: 'Pergunta teste?',
        resposta_sugerida: 'Resposta teste.',
        status: 'pending',
      }, { abrirPainel: false }));
      await page.locator('#jk-codex-messages .black-jhon-approval').waitFor();
      assert.strictEqual(await page.locator('#jk-ia-msgs .jk-ia-approval-card').count(), 0);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.locator('#jk-ia-light-fab').click();
      await page.locator('#jk-codex-panel.aberto').waitFor();
      await page.locator('#jk-codex-messages .black-jhon-approval').waitFor();
      await page.evaluate(() => {
        window.__favoritosLocalCalls = 0;
        window.JKFavoritosPreencherPesquisasIA = async () => {
          window.__favoritosLocalCalls += 1;
          return { success: true, atualizadas: 2, total: 2 };
        };
      });
      await page.locator('#jk-codex-input').fill('Preencha e salve Pesquisa 1 e Pesquisa 2.');
      await page.locator('#jk-codex-readonly').click();
      await page.getByText('Confirmar preenchimento em Favoritos').waitFor();
      assert.strictEqual(await page.evaluate(() => window.__favoritosLocalCalls), 0, 'acao local exige confirmacao explicita');
      await page.getByRole('button', { name: 'Confirmar e executar' }).click();
      await page.waitForFunction(() => window.__favoritosLocalCalls === 1);
      await page.waitForFunction(() => document.querySelector('#jk-codex-messages')?.textContent.includes('Preenchi e salvei Pesquisa 1 e Pesquisa 2'));
      assert.strictEqual(calls.codexTasks.length, 2, 'acao local confirmada nao deve criar tarefa Codex duplicada');
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const scenario = await newScenario(browser, baseUrl, { full: false, statusReady: true, seedLegacy: true });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      assert.strictEqual(await page.locator('#jk-ia-panel.aberto').count(), 0, 'usuario comum nao deve abrir a IA legada');
      assert.strictEqual(await page.locator('#jk-codex-panel').getAttribute('data-access-profile'), 'read_only');
      assert.strictEqual(await page.locator('#jk-codex-toolbar').isHidden(), true);
      assert.strictEqual(await page.locator('#jk-codex-add-menu').isHidden(), true);
      assert.strictEqual(await page.locator('#jk-codex-path-chips').isHidden(), true);
      assert.strictEqual(await page.locator('#jk-codex-attachment-chips').isHidden(), true);
      assert.strictEqual(await page.locator('#jk-codex-approval').isHidden(), true);
      assert.strictEqual(await page.locator('#jk-codex-report-settings').isHidden(), true);
      assert.strictEqual(await page.locator('#jk-codex-header span').textContent(), 'Codex principal · somente leitura');
      assert.strictEqual(calls.iaModels, 0);
      assert.doesNotMatch(await page.locator('#jk-codex-messages').textContent(), /SEGREDO DO HISTORICO ADMIN/);

      await page.evaluate(() => {
        document.getElementById('jk-codex-access').value = 'full_access';
        document.getElementById('jk-codex-model').value = 'gpt-5.4';
      });
      await page.locator('#jk-codex-input').fill('Teste usuario comum em leitura');
      await page.locator('#jk-codex-readonly').click();
      await page.waitForFunction(() => document.querySelector('#jk-codex-messages')?.textContent.includes('Resposta primaria de teste.'));
      assert.strictEqual(calls.codexTasks.length, 1);
      assert.strictEqual(calls.codexTasks[0].sandbox, 'read_only');
      assert.strictEqual(calls.codexTasks[0].approval_mode, 'read_only');
      assert.strictEqual(calls.codexTasks[0].model, 'gpt-5.5');
      assert.strictEqual(calls.codexTasks[0].thread_id, undefined);
      assert.deepStrictEqual(calls.codexTasks[0].paths, []);
      assert.strictEqual(calls.iaChat.length, 0);
      assert.strictEqual(await page.locator('#jk-codex-approve').isHidden(), true, 'aprovacao permanece oculta para usuario comum');
      await page.evaluate(() => document.getElementById('jk-codex-cancel').dispatchEvent(new MouseEvent('click', { bubbles: true })));
      await page.waitForFunction(() => document.querySelector('#jk-codex-status')?.textContent.includes('cancelada'));
      assert.strictEqual(calls.codexTaskCancels, 1, 'usuario comum pode cancelar a propria tarefa pelo alias universal');
      assert.strictEqual(calls.codexTaskApprovals, 0, 'usuario comum nunca deve chamar aprovacao administrativa');

      await page.locator('#jk-codex-file-input').setInputFiles({ name: 'bloqueado.txt', mimeType: 'text/plain', buffer: Buffer.from('teste') });
      await page.waitForTimeout(80);
      assert.strictEqual(calls.attachmentContentTypes.length, 0, 'usuario comum nao pode enviar anexos');

      await page.locator('#jk-codex-input').fill('Atualize o estoque agora.');
      await page.locator('#jk-codex-readonly').click();
      await page.waitForFunction(() => document.querySelector('#jk-codex-messages')?.textContent.includes('somente consultas e analises'));
      assert.strictEqual(calls.codexTasks.length, 1, 'pedido mutavel de usuario comum deve ser bloqueado antes do Codex');
      assert.strictEqual(calls.actionProposals.length, 0);
      assert.deepStrictEqual(calls.advancedAdmin, [], 'usuario comum nao deve disparar recursos administrativos avancados');
      assert.strictEqual(calls.reportSettingsGets, 0);
      assert.strictEqual(calls.reportSettingsPuts.length, 0);

      await page.evaluate(() => window.JKIASidebarNotifyApproval({
        id: 'approval-common-user',
        tipo: 'pergunta',
        pergunta: 'Nao deve aparecer',
        resposta_sugerida: 'Nao deve aparecer',
        status: 'pending',
      }, { abrirPainel: true }));
      await page.waitForTimeout(80);
      assert.strictEqual(await page.locator('#jk-codex-messages .black-jhon-approval').count(), 0);
      assert.strictEqual(await page.locator('#jk-ia-panel.aberto').count(), 0);
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const scenario = await newScenario(browser, baseUrl, { full: false, statusReady: false, taskStatus: 503 });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      assert.match(await page.locator('#jk-codex-status').textContent(), /indisponivel/i);
      await page.locator('#jk-codex-input').fill('Explique este teste em leitura.');
      await page.locator('#jk-codex-readonly').click();
      await page.waitForFunction(() => document.querySelector('#jk-codex-messages')?.textContent.includes('Resposta da IA secundaria de teste.'));
      assert.strictEqual(calls.codexTasks.length, 1);
      assert.strictEqual(calls.iaChat.length, 1);
      assert.strictEqual(calls.iaChat[0].fallback_read_only, true);
      assert.deepStrictEqual(calls.iaChat[0].attachments, []);
      assert.deepStrictEqual(calls.iaChat[0].history, []);
      assert.deepStrictEqual(calls.advancedAdmin, []);
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const scenario = await newScenario(browser, baseUrl, {
        full: false,
        statusReady: true,
        historyTasks: [{
          task_id: 'history-task-1',
          conversation_id: 'app-operador-browser',
          conversation_generation: 1,
          conversation_state: 'active',
          channel: 'app',
          status: 'completed',
          prompt_preview: 'Conversa propria resumida',
          response_preview: 'Resposta resumida',
          created_at: '2026-07-10T10:00:00Z',
          completed_at: '2026-07-10T10:01:00Z',
        }],
      });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      assert.strictEqual(calls.codexTaskGets, 0);
      await page.locator('#jk-codex-history-toggle').click();
      await page.getByText('Conversa propria resumida').waitFor();
      assert.strictEqual(await page.locator('#jk-codex-history-list button.danger').count(), 0, 'conversa ativa nao deve expor exclusao');
      assert.deepStrictEqual(calls.historyDeletes, []);
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const scenario = await newScenario(browser, baseUrl, {
        full: false,
        statusReady: true,
        taskStatus: 403,
        taskDetail: 'Sem permissao para usar este recurso.',
      });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      await page.locator('#jk-codex-input').fill('Explique este teste em leitura.');
      await page.locator('#jk-codex-readonly').click();
      await page.waitForFunction(() => document.querySelector('#jk-codex-messages')?.textContent.includes('Sem permissao'));
      assert.strictEqual(calls.codexTasks.length, 1);
      assert.strictEqual(calls.iaChat.length, 0, '403 generico nunca deve acionar fallback');
      assert.deepStrictEqual(calls.advancedAdmin, []);
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const scenario = await newScenario(browser, baseUrl, { full: true, statusReady: true, taskStatus: 503 });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      await page.locator('#jk-codex-input').fill('Explique os dados visiveis em modo leitura.');
      await page.locator('#jk-codex-readonly').click();
      await page.waitForFunction(() => document.querySelector('#jk-codex-messages')?.textContent.includes('Resposta da IA secundaria de teste.'));
      assert.strictEqual(calls.codexTasks.length, 1);
      assert.strictEqual(calls.iaChat.length, 1, 'consulta de leitura deve usar fallback quando Codex retorna 503');
      assert.strictEqual(calls.iaChat[0].fallback_read_only, true);
      assert.deepStrictEqual(calls.iaChat[0].attachments, []);

      await page.locator('#jk-codex-input').fill('Atualize o estoque agora.');
      await page.locator('#jk-codex-readonly').click();
      await page.waitForTimeout(300);
      assert.strictEqual(calls.actionProposals.length, 1);
      assert.strictEqual(calls.codexTasks.length, 2);
      assert.strictEqual(calls.iaChat.length, 1, 'tarefa mutavel nao pode cair silenciosamente para a IA secundaria');
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const taskResponse = [
        'sales_ranking',
        '',
        '## Indicadores dos Favoritos',
        '',
        '| Indicador | Valor | Detalhe |',
        '| --- | --- | --- |',
        '| Faturamento | R$ 0,00 | Periodo base de vendas dos produtos favoritos selecionados |',
        '| Pedidos | 0 | Pedidos validos encontrados no periodo consultado |',
        '| Lucro estimado | R$ 0,00 | Margem calculada com observacao extensa que precisa quebrar dentro do sidebar sem ficar ilegivel |',
      ].join('\n');
      const scenario = await newScenario(browser, baseUrl, { full: true, statusReady: true, taskResponse });
      const { context, page, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      await page.locator('#jk-codex-input').fill('Mostre os indicadores dos favoritos.');
      await page.locator('#jk-codex-readonly').click();
      const table = page.locator('#jk-codex-messages .jk-ia-table-responsive');
      await table.waitFor();
      assert.match(await page.locator('#jk-codex-messages').textContent(), /Ranking de vendas/);
      assert.doesNotMatch(await page.locator('#jk-codex-messages').textContent(), /sales_ranking/);
      assert.strictEqual(await table.getAttribute('data-columns'), '3');
      assert.deepStrictEqual(await table.locator('tbody tr').first().locator('td').evaluateAll(cells => cells.map(cell => cell.dataset.label)), [
        'Indicador', 'Valor', 'Detalhe',
      ]);
      const responsiveLayout = await page.locator('#jk-codex-messages .jk-ia-table-wrap').evaluate(wrap => {
        const cells = Array.from(wrap.querySelectorAll('tbody td'));
        const message = wrap.closest('.jk-codex-msg');
        return {
          cellDisplays: Array.from(new Set(cells.map(cell => getComputedStyle(cell).display))),
          cellsFit: cells.every(cell => cell.scrollWidth <= cell.clientWidth + 1),
          messageFits: !!message && message.scrollWidth <= message.clientWidth + 1,
          messageWidth: message ? Math.round(message.getBoundingClientRect().width) : 0,
          panelWidth: Math.round(document.getElementById('jk-codex-panel').getBoundingClientRect().width),
        };
      });
      assert.deepStrictEqual(responsiveLayout.cellDisplays, ['grid']);
      assert.strictEqual(responsiveLayout.cellsFit, true, 'conteudo das celulas deve quebrar sem overflow');
      assert.strictEqual(responsiveLayout.messageFits, true, 'tabela nao deve ultrapassar a mensagem');
      assert.ok(responsiveLayout.messageWidth >= responsiveLayout.panelWidth - 40, 'mensagem com tabela deve usar a largura util do sidebar');
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const largeTaskResponse = 'T'.repeat(20000);
      const scenario = await newScenario(browser, baseUrl, { full: true, statusReady: true, largeTaskResponse });
      const { context, page, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      await page.locator('#jk-codex-input').fill('Retorne uma resposta longa para o teste.');
      await page.locator('#jk-codex-readonly').click();
      await page.locator('#jk-codex-messages .jk-codex-open-fulltext').waitFor();
      const panelTextLength = await page.locator('#jk-codex-messages').evaluate(el => el.textContent.length);
      assert.ok(panelTextLength < 12000, 'resposta longa nao deve permanecer integral no DOM principal');
      const stored = await page.evaluate(() => Object.entries(localStorage)
        .filter(([key]) => key.startsWith('jk_codex_history_v2_'))
        .map(([, value]) => value)
        .join('\n'));
      assert.ok(stored.length < 12000, 'historico local deve persistir apenas a previa');
      assert.match(stored, /"truncated":true/);
      assert.match(stored, /"content_id":"task-browser-1"/);
      const popupPromise = page.waitForEvent('popup');
      await page.locator('#jk-codex-messages .jk-codex-open-fulltext').click();
      const popup = await popupPromise;
      await popup.locator('pre').waitFor();
      assert.strictEqual((await popup.locator('pre').textContent()).length, largeTaskResponse.length);
      assert.deepStrictEqual(pageErrors, []);
      await popup.close();
      await context.close();
    }

    {
      const largeDailyReport = 'R'.repeat(200000);
      const scenario = await newScenario(browser, baseUrl, {
        full: true,
        statusReady: true,
        fakeClock: true,
        largeDailyReport,
        coordinatorSuggestions: [{
          id: 'suggestion-coordinator-scroll',
          title: 'Sugestao de fila',
          detail: 'Validar que o scroll interno nao interrompe a propria fila.',
          recommendation: 'Continuar sequencialmente.',
          source: 'browser-test',
        }],
      });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      assert.deepStrictEqual(calls.advancedAdmin, [], 'coordenador nao deve rodar antes do relogio avancar');
      await page.clock.runFor(1);
      if (await page.locator('#jk-codex-panel.aberto').count()) {
        await page.locator('#jk-codex-close').click();
      }
      assert.strictEqual(await page.locator('#jk-codex-panel.aberto').count(), 0);
      await page.clock.runFor(49999);
      await page.keyboard.press('Shift');
      await page.clock.runFor(10000);
      await new Promise(resolve => setTimeout(resolve, 30));
      assert.deepStrictEqual(calls.advancedAdmin, [], 'atividade nos 15 segundos anteriores adia o primeiro ciclo');

      await page.clock.runFor(30001);
      try {
        await waitForCondition(() => calls.advancedAdmin.length === 3, 'coordenador nao concluiu a fila inicial');
      } catch (error) {
        const coordinatorState = await page.evaluate(() => window.__JK_CODEX_COORDINATOR_TEST__?.state());
        error.message += ` Estado: ${JSON.stringify({ calls: calls.advancedAdmin, coordinatorState })}`;
        throw error;
      }
      assert.deepStrictEqual(calls.advancedAdmin, [
        '/api/admin/codex/assistant/suggestions',
        '/api/admin/codex/assistant/proactive/run',
        '/api/admin/codex/assistant/weekly-analysis/run',
      ]);
      assert.strictEqual(calls.maxActiveAdvancedAdmin, 1, 'rotinas administrativas nao podem executar em paralelo');
      const proactivePayload = calls.advancedPayloads.find(item => item.path.endsWith('/proactive/run')).payload;
      const weeklyPayload = calls.advancedPayloads.find(item => item.path.endsWith('/weekly-analysis/run')).payload;
      [proactivePayload, weeklyPayload].forEach(payload => {
        assert.strictEqual(payload.compact, true);
        assert.strictEqual(payload.screen_context.context_mode, 'background');
        assert.strictEqual(Object.prototype.hasOwnProperty.call(payload.screen_context, 'visible_text'), false);
        assert.strictEqual(Object.prototype.hasOwnProperty.call(payload.screen_context, 'controls'), false);
      });

      await page.locator('#jk-codex-messages .codex-report').waitFor();
      const reportCardLength = await page.locator('#jk-codex-messages .codex-report').evaluate(el => el.textContent.length);
      assert.ok(reportCardLength < 3000, 'relatorio de 200k deve virar cartao compacto');
      const persistedReport = await page.evaluate(() => Object.entries(localStorage)
        .filter(([key]) => key.startsWith('jk_codex_history_v2_'))
        .map(([, value]) => value)
        .join('\n'));
      assert.ok(persistedReport.length < 20000);
      assert.match(persistedReport, /report-browser-large/);
      assert.match(persistedReport, /"full_length":200000/);
      assert.doesNotMatch(persistedReport, /R{1000}/);

      await page.evaluate(() => document.getElementById('jk-ia-fab')?.click());
      await page.locator('#jk-codex-panel.aberto').waitFor();
      const popupPromise = page.waitForEvent('popup');
      await page.locator('#jk-codex-messages .codex-report .jk-codex-open-fulltext').click();
      const popup = await popupPromise;
      await popup.locator('pre').waitFor();
      assert.strictEqual((await popup.locator('pre').textContent()).length, largeDailyReport.length);
      assert.strictEqual(calls.reportGets, 1);
      assert.deepStrictEqual(await page.evaluate(() => {
        const state = window.__JK_CODEX_COORDINATOR_TEST__.state();
        return [state.full_text_cache_size, state.full_text_cache_max];
      }), [1, 4]);
      await popup.close();

      await page.evaluate(() => document.getElementById('jk-codex-new')?.click());
      await waitForCondition(() => calls.memoryResets === 1, 'reinicio de memoria nao foi chamado');
      assert.strictEqual(await page.evaluate(() => window.__JK_CODEX_COORDINATOR_TEST__.state().full_text_cache_size), 0);
      await page.locator('#jk-codex-close').click();

      await page.clock.fastForward(29 * 60 * 1000);
      await new Promise(resolve => setTimeout(resolve, 20));
      assert.strictEqual(calls.advancedAdmin.length, 3);
      await page.clock.fastForward((60 * 1000) + 10);
      await waitForCondition(() => calls.advancedAdmin.length === 6, 'recorrencia de 30 minutos nao executou');
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const scenario = await newScenario(browser, baseUrl, {
        full: true,
        statusReady: true,
        overlapTasks: true,
      });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      await page.locator('#jk-codex-input').fill('Primeira tarefa sobreposta');
      await page.locator('#jk-codex-readonly').click();
      await waitForCondition(() => calls.overlapOldGets === 1, 'poll da primeira tarefa nao iniciou');
      await page.locator('#jk-codex-input').fill('Segunda tarefa deve vencer');
      await page.locator('#jk-codex-readonly').click();
      await page.waitForFunction(() => document.querySelector('#jk-codex-messages')?.textContent.includes('RESPOSTA NOVA DEVE PERMANECER'));
      await new Promise(resolve => setTimeout(resolve, 260));
      const overlapText = await page.locator('#jk-codex-messages').textContent();
      assert.doesNotMatch(overlapText, /RESPOSTA ANTIGA NAO DEVE SOBRESCREVER/);
      assert.strictEqual(calls.codexTasks.length, 2);
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const scenario = await newScenario(browser, baseUrl, {
        full: false,
        statusReady: true,
        fakeClock: true,
      });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      await page.locator('#jk-codex-close').click();
      await page.clock.fastForward(5 * 60 * 1000);
      assert.deepStrictEqual(calls.advancedAdmin, [], 'usuario comum nunca inicia coordenador administrativo');
      const state = await page.evaluate(() => window.__JK_CODEX_COORDINATOR_TEST__.state());
      assert.strictEqual(state.started, false);
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const scenario = await newScenario(browser, baseUrl, {
        full: true,
        statusReady: true,
        fakeClock: true,
        seedActiveTask: 'restored-task-1',
      });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      await waitForCondition(() => calls.restoredTaskGets >= 2, 'tarefa ativa nao foi restaurada e retomada');
      assert.strictEqual(calls.codexTaskGets, 0, 'restaurar tarefa ativa nao deve listar historico completo');
      const restoredGets = calls.restoredTaskGets;
      await page.locator('#jk-codex-new').click();
      await waitForCondition(() => calls.memoryResets === 1, 'reinicio de memoria da tarefa restaurada nao foi chamado');
      await page.clock.fastForward(5000);
      await new Promise(resolve => setTimeout(resolve, 30));
      assert.strictEqual(calls.restoredTaskGets, restoredGets, 'reinicio de memoria deve cancelar polls antigos');
      assert.doesNotMatch(await page.locator('#jk-codex-messages').textContent(), /RESPOSTA ANTIGA NAO DEVE VOLTAR/);
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const context = await browser.newContext();
      const page = await context.newPage();
      await page.goto(`${baseUrl}/test.html?embed=share`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(250);
      assert.strictEqual(await page.locator('#jk-ia-light-fab').count(), 0);
      assert.strictEqual(await page.locator('#jk-ia-fab').count(), 0);
      await context.close();
    }

    console.log('BLACK_JHON_SIDEBAR_BROWSER_OK');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}

run().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
