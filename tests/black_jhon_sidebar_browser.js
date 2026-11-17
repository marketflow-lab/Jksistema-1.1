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
    codexModels: 0,
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

  await page.addInitScript(({ full, seedLegacy, seedCodexModel, seedAutomaticAlert, seedActiveTask, testMode }) => {
    Object.defineProperty(window, 'Worker', { configurable: true, value: undefined });
    window.__JK_CODEX_TEST_MODE__ = testMode === true;
    localStorage.setItem('permissions', JSON.stringify(full ? { full: true } : {}));
    localStorage.setItem('user_data', JSON.stringify({ username: full ? 'admin' : 'operador', client_id: '000002' }));
    localStorage.setItem('access_token', 'black-jhon-browser-test');
    if (seedCodexModel) {
      localStorage.setItem('jk_codex_settings_v1', JSON.stringify({ model: seedCodexModel }));
    }
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
    if (seedAutomaticAlert) {
      localStorage.setItem('jk_codex_history_v2_legacy_automatic_test', JSON.stringify([
        { role: 'assistant', text: 'MENSAGEM AUTOMATICA LEGADA', classExtra: 'codex-alert', task_id: 'codex_assistant|legacy' },
        { role: 'assistant', text: 'MENSAGEM MANUAL PRESERVADA', classExtra: '', task_id: 'manual-legacy' },
      ]));
      localStorage.setItem('jk_codex_assistant_seen_v1', JSON.stringify({ legacy: '2026-07-10T12:00:00Z' }));
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
    seedCodexModel: options.seedCodexModel || '',
    seedAutomaticAlert: options.seedAutomaticAlert === true,
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

    if (pathname === '/api/codex/models') {
      calls.codexModels += 1;
      if (options.catalogFallbackOnce && calls.codexModels === 1) return json(503, { detail: 'Catalogo temporariamente indisponivel.' });
      return json(200, { success: true, default_model: options.defaultCodexModel || 'gpt-6.1-sol', default_reasoning_effort: options.defaultCodexModel === 'gpt-6-luna' ? 'low' : 'xhigh', models: [
        { id: 'gpt-6.1-sol', name: 'codex:gpt-6.1-sol', display_name: 'GPT-6.1 Sol', reasoning_efforts: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'], default_reasoning_effort: 'high', available: true },
        { id: 'gpt-6-luna', name: 'codex:gpt-6-luna', display_name: 'GPT-6 Luna', reasoning_efforts: ['minimal', 'low', 'medium'], default_reasoning_effort: 'low', available: true },
      ] });
    }
    if (pathname === '/api/codex/status') {
      calls.codexStatus += 1;
      if (options.authAlwaysChecking === true) {
        return json(200, {
          success: true,
          ready: false,
          authentication_state: 'checking',
          authentication_check_pending: true,
          message: 'Verificando a sessao local do Codex...',
        });
      }
      if (options.authOutOfOrder === true && calls.codexStatus === 1) {
        await new Promise(resolve => setTimeout(resolve, 250));
        return json(200, {
          success: true,
          ready: false,
          authentication_state: 'checking',
          authentication_check_pending: true,
          message: 'Verificando a sessao local do Codex...',
        });
      }
      if (options.authCheckingOnce === true && calls.codexStatus === 1) {
        return json(200, {
          success: true,
          ready: false,
          authentication_state: 'checking',
          authentication_check_pending: true,
          message: 'Verificando a sessao local do Codex...',
          defaults: { model: 'gpt-5.5', reasoning_effort: 'xhigh', speed: 'standard' },
          conversation: { conversation_id: canonicalConversationId, channel: 'app', generation: 1, state: 'active', can_reset: true, queue: { running: 0, pending: 0 } },
        });
      }
      return json(200, {
        success: true,
        ready: options.statusReady !== false,
        authentication_state: options.statusReady === false ? 'unauthenticated' : 'authenticated',
        authentication_check_pending: false,
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
          task_id: options.distinctTaskIds === true ? `task-browser-${calls.codexTasks.length}` : 'task-browser-1',
          conversation_id: canonicalConversationId,
          conversation_generation: 1,
          conversation_state: 'active',
          channel: 'app',
          prompt: payload.prompt,
          sandbox: payload.sandbox,
          status: options.taskState || (options.taskRunning === true ? 'running' : 'completed'),
          error: options.taskError || '',
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
    if (pathname === '/api/codex/tasks/task-browser-1' || (options.distinctTaskIds === true && /^\/api\/codex\/tasks\/task-browser-\d+$/.test(pathname))) {
      calls.codexTaskDetailGets += 1;
      const completed = options.taskRunning === true && calls.codexTaskDetailGets > 1;
      return json(200, {
        success: true,
        task: {
          task_id: pathname.split('/').pop(),
          conversation_id: canonicalConversationId,
          conversation_generation: 1,
          conversation_state: 'active',
          channel: 'app',
          thread_id: completed ? 'thread-old-poll' : '',
          status: options.taskState || (options.taskRunning === true ? (completed ? 'completed' : 'running') : 'completed'),
          error: options.taskError || '',
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
    if (pathname === '/api/admin/codex/assistant/reports/report-browser-manual' && method === 'GET') {
      calls.reportGets += 1;
      return json(200, {
        success: true,
        report: { report_id: 'report-browser-manual', chat_text: options.largeManualReport || '' },
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
    if (pathname === '/api/mercadolivre/perguntas/aprovacoes') {
      return json(200, { success: true, pendentes: Array.isArray(options.pendingApprovals) ? options.pendingApprovals : [] });
    }
    return json(200, { success: true, suggestions: [], tasks: [] });
  });

  await page.goto(`${baseUrl}/test.html${options.pageQuery || ''}`, { waitUntil: 'domcontentloaded' });
  await page.locator('#jk-ia-light-fab').click();
  await page.locator('#jk-ia-fab').waitFor({ state: 'visible' });
  return { context, page, calls, pageErrors };
}

async function readTaskHistory(page, taskId = 'task-browser-1') {
  const history = await page.evaluate((id) => {
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (!key.startsWith('jk_codex_history_v2_')) continue;
      const messages = JSON.parse(localStorage.getItem(key) || '[]');
      if (Array.isArray(messages) && messages.some(item => item.task_id === id)) return { key, messages };
    }
    return null;
  }, taskId);
  assert.ok(history, 'historico persistido da tarefa deve existir');
  return history;
}

async function reloadCodexPanel(page, taskId = 'task-browser-1') {
  const restored = page.waitForResponse(response => new URL(response.url()).pathname === `/api/codex/tasks/${taskId}` && response.request().method() === 'GET');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.locator('#jk-ia-light-fab').click();
  await page.locator('#jk-codex-panel.aberto').waitFor();
  await restored;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function runReplyRestorationRegressions(browser, baseUrl) {
  for (const taskState of ['completed', 'failed']) {
    const reply = taskState === 'failed' ? 'Falha sintetica sem fallback.' : 'Resposta sintetica preservada.';
    const { context, page, calls, pageErrors } = await newScenario(browser, baseUrl, {
      full: true,
      statusReady: true,
      taskState,
      taskResponse: reply,
      taskError: taskState === 'failed' ? reply : '',
    });
    try {
      await page.locator('#jk-codex-panel.aberto').waitFor();
      await page.locator('#jk-codex-input').fill('Consulta sintetica para restauracao.');
      await page.locator('#jk-codex-readonly').click();
      await page.waitForFunction(text => document.querySelector('#jk-codex-messages')?.textContent.includes(text), reply);
      for (let reload = 0; reload <= 2; reload += 1) {
        if (reload) await reloadCodexPanel(page);
        assert.strictEqual(calls.codexTasks.length, 1, `${taskState}: recarregar nao deve criar outra tarefa`);
        assert.strictEqual(await page.locator('#jk-codex-messages .jk-codex-msg.user').count(), 1);
        assert.strictEqual(await page.locator('#jk-codex-messages .jk-codex-msg.assistant').count(), 1, `${taskState}: a resposta deve aparecer uma vez apos ${reload} recargas`);
        const { messages } = await readTaskHistory(page);
        assert.strictEqual(messages.filter(item => item.role === 'assistant' && item.task_id === 'task-browser-1').length, 1, `${taskState}: historico persistido nao pode acumular copias`);
      }
      assert.strictEqual(calls.iaChat.length, 0, 'restauracao nao deve gerar resposta secundaria');
      assert.deepStrictEqual(pageErrors, []);
    } finally {
      await context.close();
    }
  }

  {
    const reply = 'Resposta igual para tarefas diferentes.';
    const { context, page, calls, pageErrors } = await newScenario(browser, baseUrl, {
      full: true, statusReady: true, distinctTaskIds: true, taskResponse: reply,
    });
    try {
      await page.locator('#jk-codex-panel.aberto').waitFor();
      for (let task = 1; task <= 2; task += 1) {
        await page.locator('#jk-codex-input').fill(`Consulta sintetica ${task}.`);
        await page.locator('#jk-codex-readonly').click();
        await page.waitForFunction(count => document.querySelectorAll('#jk-codex-messages .jk-codex-msg.assistant').length === count, task);
      }
      const history = await readTaskHistory(page);
      assert.deepStrictEqual(history.messages.filter(item => item.role === 'assistant').map(item => item.task_id), ['task-browser-1', 'task-browser-2']);
      await page.evaluate(({ key, messages }) => {
        const firstReply = messages.find(item => item.role === 'assistant' && item.task_id === 'task-browser-1');
        const secondReply = messages.find(item => item.role === 'assistant' && item.task_id === 'task-browser-2');
        localStorage.setItem(key, JSON.stringify(messages.concat(
          { ...firstReply, at: '2026-07-10T12:00:01Z' },
          { ...secondReply, at: '2026-07-10T12:00:02Z' },
          { role: 'assistant', text: 'Mensagem legada sem identificador.' },
          { role: 'assistant', text: 'Mensagem legada sem identificador.' },
        )));
      }, history);
      for (let reload = 1; reload <= 2; reload += 1) {
        await reloadCodexPanel(page, 'task-browser-2');
        assert.strictEqual(await page.locator('#jk-codex-messages .jk-codex-msg.assistant').count(), 4, 'saneamento deve remover apenas copias identificadas, preservando tarefas diferentes e mensagens legadas');
        assert.strictEqual(await page.locator('#jk-codex-messages .jk-codex-msg.user').count(), 2);
        const persisted = await readTaskHistory(page);
        assert.deepStrictEqual(persisted.messages.filter(item => item.role === 'assistant' && item.task_id).map(item => item.task_id), ['task-browser-1', 'task-browser-2']);
        assert.strictEqual(persisted.messages.filter(item => item.role === 'assistant' && !item.task_id && item.text === 'Mensagem legada sem identificador.').length, 2);
      }
      assert.strictEqual(calls.codexTasks.length, 2);
      assert.deepStrictEqual(pageErrors, []);
    } finally {
      await context.close();
    }
  }

  for (const hasPriorAssistant of [false, true]) {
    const reply = 'Conclusao ausente no historico pendente.';
    const { context, page, calls, pageErrors } = await newScenario(browser, baseUrl, {
      full: true, statusReady: true, taskResponse: reply,
    });
    try {
      await page.locator('#jk-codex-panel.aberto').waitFor();
      await page.locator('#jk-codex-input').fill('Consulta sintetica pendente.');
      await page.locator('#jk-codex-readonly').click();
      await page.waitForFunction(text => document.querySelector('#jk-codex-messages')?.textContent.includes(text), reply);
      const history = await readTaskHistory(page);
      await page.evaluate(({ key, messages, hasPriorAssistant }) => {
        const pending = messages.filter(item => item.role !== 'assistant').map(item => ({ ...item, kind: 'pending' }));
        if (hasPriorAssistant) {
          const prior = messages.find(item => item.role === 'assistant');
          const text = 'Preciso de mais dados para continuar.';
          pending.push({ ...prior, text, full_length: text.length, truncated: false });
        }
        localStorage.setItem(key, JSON.stringify(pending));
      }, { ...history, hasPriorAssistant });
      await reloadCodexPanel(page);
      const expectedReplies = hasPriorAssistant ? 2 : 1;
      assert.strictEqual(await page.locator('#jk-codex-messages .jk-codex-msg.assistant').count(), expectedReplies, 'task_id do usuario ou de um pedido anterior de dados nao deve suprimir a conclusao ainda nao exibida');
      assert.match(await page.locator('#jk-codex-messages .jk-codex-msg.assistant').last().textContent(), /Conclusao ausente no historico pendente/);
      if (hasPriorAssistant) assert.strictEqual(await page.locator('#jk-codex-messages .jk-codex-msg.assistant').first().textContent(), 'Preciso de mais dados para continuar.');
      assert.strictEqual((await readTaskHistory(page)).messages.filter(item => item.role === 'assistant').length, expectedReplies);
      assert.strictEqual(calls.codexTasks.length, 1);
      assert.deepStrictEqual(pageErrors, []);
    } finally {
      await context.close();
    }
  }

  {
    const { context, page, calls, pageErrors } = await newScenario(browser, baseUrl, {
      full: true,
      statusReady: true,
      distinctTaskIds: true,
      taskState: 'failed',
      taskError: 'Backend do Codex temporariamente indisponivel.',
    });
    try {
      await page.locator('#jk-codex-panel.aberto').waitFor();
      await page.locator('#jk-codex-input').fill('Explique os dados sinteticos visiveis.');
      await page.locator('#jk-codex-readonly').click();
      await page.waitForFunction(() => document.querySelector('#jk-codex-messages')?.textContent.includes('Backend do Codex temporariamente indisponivel.'));
      await page.locator('#jk-codex-input').fill('Explique novamente os dados sinteticos visiveis.');
      await page.locator('#jk-codex-readonly').click();
      await page.waitForFunction(() => document.querySelector('#jk-codex-messages')?.textContent.includes('Resposta da IA secundaria de teste.'));
      for (let reload = 0; reload <= 2; reload += 1) {
        if (reload) await reloadCodexPanel(page, 'task-browser-2');
        assert.strictEqual(calls.iaChat.length, 1, 'restaurar fallback persistido nao deve gerar nova resposta secundaria');
        assert.strictEqual(calls.codexTasks.length, 2);
        assert.strictEqual(await page.locator('#jk-codex-messages .jk-codex-msg.assistant').count(), 2, 'restaurar fallback nao deve acrescentar o erro primario da mesma tarefa');
        const { messages } = await readTaskHistory(page, 'task-browser-2');
        const replies = messages.filter(item => item.role === 'assistant' && item.task_id === 'task-browser-2');
        assert.strictEqual(replies.length, 1);
        assert.strictEqual(replies[0].kind, 'fallback');
        assert.strictEqual(replies[0].text, 'Resposta da IA secundaria de teste.');
      }
      assert.deepStrictEqual(pageErrors, []);
    } finally {
      await context.close();
    }
  }
}

async function run() {
  const { server, baseUrl } = await startStaticServer();
  const browser = await chromium.launch({ headless: true });
  try {
    await runReplyRestorationRegressions(browser, baseUrl);
    {
      const scenario = await newScenario(browser, baseUrl, {
        full: true,
        statusReady: true,
        seedCodexModel: 'gpt-5.5',
        pageQuery: '?token=segredo-nao-enviar&code=oauth-nao-enviar#hash-secreto',
        pendingApprovals: [{
          id: 'approval-browser-1',
          tipo: 'pergunta',
          loja: 'Loja teste',
          sku: 'SKU-1',
          titulo: 'Produto teste',
          pergunta: 'Pergunta teste?',
          resposta_sugerida: 'Resposta teste.',
          status: 'pending',
        }],
      });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      assert.strictEqual(await page.locator('#jk-codex-model').inputValue(), 'gpt-6.1-sol', 'modelo antigo salvo deve migrar para o padrao suportado');
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
      assert.strictEqual(await page.locator('#jk-questions-fab .jk-questions-ml-symbol').getAttribute('href'), '/assets/mercado-livre-symbol.svg?v=20260815-official-v1');
      assert.strictEqual(await page.locator('.jk-questions-header-logo img').evaluate(img => img.complete && img.naturalWidth > 0), true);
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

      assert.strictEqual(await page.locator('#jk-codex-reasoning').count(), 1, 'seletor de raciocinio possui ID unico');
      await page.locator('#jk-codex-reasoning').selectOption('max');
      await page.locator('#jk-codex-input').fill('Responda apenas com um teste de leitura.');
      await page.locator('#jk-codex-readonly').evaluate(button => button.click());
      await page.waitForFunction(() => document.querySelector('#jk-codex-messages')?.textContent.includes('Resposta primaria de teste.'));
      assert.strictEqual(calls.codexTasks.length, 1);
      assert.strictEqual(calls.codexTasks[0].sandbox, 'read_only');
      assert.strictEqual(calls.codexTasks[0].model, 'gpt-6.1-sol');
      assert.strictEqual(calls.codexTasks[0].reasoning_effort, 'max', 'raciocinio max selecionado deve chegar ao backend');
      assert.strictEqual(calls.codexTasks[0].screen_context.context_mode, 'manual');
      assert.ok(calls.codexTasks[0].screen_context.dom_scan.scanned_elements <= 300);
      assert.doesNotMatch(calls.codexTasks[0].screen_context.url_completa, /segredo|token|code|hash/i);
      assert.strictEqual(calls.codexTasks[0].screen_context.url_completa, `${baseUrl}/test.html`);
      assert.strictEqual(calls.iaChat.length, 0);

      await page.locator('#jk-codex-reasoning').selectOption('ultra');
      await page.locator('#jk-codex-input').fill('Segunda consulta para validar cache de contexto.');
      await page.locator('#jk-codex-readonly').evaluate(button => button.click());
      await page.waitForFunction(() => document.querySelectorAll('#jk-codex-messages .jk-codex-msg.user').length >= 2);
      assert.strictEqual(calls.codexTasks.length, 2);
      assert.strictEqual(calls.codexTasks[1].reasoning_effort, 'ultra', 'raciocinio ultra selecionado deve chegar ao backend');
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
      await page.locator('#jk-questions-list .jk-ia-approval-card').waitFor();
      assert.strictEqual(await page.locator('#jk-codex-messages .jk-ia-approval-card').count(), 0);
      assert.strictEqual(await page.locator('#jk-ia-msgs .jk-ia-approval-card').count(), 0);
      assert.strictEqual(await page.locator('#jk-questions-badge').textContent(), '1');
      await page.locator('#jk-questions-fab').evaluate(button => button.click());
      await page.locator('#jk-questions-panel.aberto').waitFor();
      await page.locator('#jk-questions-list .jk-ia-approval-card').waitFor();
      assert.strictEqual(await page.locator('#jk-codex-panel.aberto').count(), 0);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.locator('#jk-ia-light-fab').click();
      await page.locator('#jk-codex-panel.aberto').waitFor();
      assert.strictEqual(await page.locator('#jk-codex-messages .jk-ia-approval-card').count(), 0, 'perguntas nao voltam ao historico do Black Jhon');
      await page.locator('#jk-questions-fab').evaluate(button => button.click());
      await page.locator('#jk-questions-panel.aberto').waitFor();
      await page.locator('#jk-questions-list .jk-ia-approval-card').waitFor();
      await page.locator('#jk-questions-close').click();
      await page.locator('#jk-ia-fab').click();
      await page.locator('#jk-codex-panel.aberto').waitFor();
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
      const scenario = await newScenario(browser, baseUrl, { full: true, statusReady: true, defaultCodexModel: 'gpt-6-luna' });
      const { context, page, calls, pageErrors } = scenario;
      await page.waitForFunction(() => document.getElementById('jk-codex-model')?.value === 'gpt-6-luna');
      assert.deepStrictEqual(await page.locator('#jk-codex-reasoning option').evaluateAll(items => items.map(item => item.value)), ['minimal', 'low', 'medium']);
      assert.strictEqual(await page.locator('#jk-codex-reasoning option[value="ultra"]').count(), 0, 'default Luna deve usar suas capacidades reais');
      await page.locator('#jk-codex-input').fill('Confirme o modelo padrao.');
      await page.locator('#jk-codex-readonly').click();
      await waitForCondition(() => calls.codexTasks.length === 1, 'consulta com default do catalogo nao foi enviada');
      assert.strictEqual(calls.codexTasks[0].model, 'gpt-6-luna');
      assert(['minimal', 'low', 'medium'].includes(calls.codexTasks[0].reasoning_effort));
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const scenario = await newScenario(browser, baseUrl, { full: true, statusReady: true, defaultCodexModel: 'gpt-6-luna', seedCodexModel: 'gpt-6.1-sol' });
      const { context, page, pageErrors } = scenario;
      await page.waitForFunction(() => document.querySelector('#jk-codex-model option[value="gpt-6-luna"]'));
      assert.strictEqual(await page.locator('#jk-codex-model').inputValue(), 'gpt-6.1-sol', 'preferencia valida salva deve prevalecer sobre o default do servidor');
      assert.strictEqual(await page.locator('#jk-codex-reasoning option[value="ultra"]').count(), 1);
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const scenario = await newScenario(browser, baseUrl, { full: true, statusReady: true, catalogFallbackOnce: true });
      const { context, page, calls, pageErrors } = scenario;
      await page.waitForFunction(() => document.querySelector('#jk-codex-status')?.textContent.includes('Black Jhon pronto'));
      assert.strictEqual(await page.locator('#jk-codex-reasoning option[value="ultra"]').count(), 0, 'fallback conservador nao deve inventar ultra');
      await page.locator('#jk-codex-refresh').click();
      await waitForCondition(() => calls.codexModels === 2, 'atualizar deve buscar novamente o catalogo apos fallback');
      await page.locator('#jk-codex-reasoning option[value="ultra"]').waitFor({ state: 'attached' });
      await page.locator('#jk-codex-reasoning').selectOption('ultra');
      await page.locator('#jk-codex-input').fill('Consulta apos atualizacao do catalogo.');
      await page.locator('#jk-codex-readonly').click();
      await waitForCondition(() => calls.codexTasks.length === 1, 'consulta apos atualizacao nao foi enviada');
      assert.strictEqual(calls.codexTasks[0].reasoning_effort, 'ultra');
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
      assert.strictEqual(await page.locator('#jk-questions-fab').isHidden(), true);
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
      assert.strictEqual(calls.codexTasks[0].model, 'gpt-6.1-sol');
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
      assert.strictEqual(await page.locator('#jk-codex-messages .jk-ia-approval-card').count(), 0);
      assert.strictEqual(await page.locator('#jk-questions-list .jk-ia-approval-card').count(), 0);
      assert.strictEqual(await page.locator('#jk-questions-panel.aberto').count(), 0);
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
      await page.waitForTimeout(200);
      if (calls.iaChat.length === 0) {
        await page.locator('#jk-codex-input').fill('Explique este teste em leitura.');
        await page.locator('#jk-codex-readonly').click();
      }
      await page.waitForFunction(() => document.querySelector('#jk-codex-messages')?.textContent.includes('Resposta da IA secundaria de teste.'));
      assert.ok(calls.codexTasks.length >= 1 && calls.codexTasks.length <= 2);
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
      await page.waitForTimeout(200);
      assert.strictEqual(calls.codexTasks.length, 1);
      assert.strictEqual(calls.iaChat.length, 0, 'uma unica falha operacional nao pode liberar fallback');

      await page.locator('#jk-codex-input').fill('Explique os dados visiveis em modo leitura.');
      await page.locator('#jk-codex-readonly').click();
      await page.waitForFunction(() => document.querySelector('#jk-codex-messages')?.textContent.includes('Resposta da IA secundaria de teste.'));
      assert.strictEqual(calls.codexTasks.length, 2);
      assert.strictEqual(calls.iaChat.length, 1, 'fallback exige duas falhas operacionais do Codex');
      assert.strictEqual(calls.iaChat[0].fallback_read_only, true);
      assert.deepStrictEqual(calls.iaChat[0].attachments, []);

      await page.locator('#jk-codex-input').fill('Atualize o estoque agora.');
      await page.locator('#jk-codex-readonly').click();
      await page.waitForTimeout(300);
      assert.strictEqual(calls.actionProposals.length, 1);
      assert.strictEqual(calls.codexTasks.length, 3);
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
      const { context, page, calls, pageErrors } = scenario;
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
      await popup.close();
      for (let reload = 1; reload <= 2; reload += 1) {
        await reloadCodexPanel(page);
        assert.strictEqual(await page.locator('#jk-codex-messages .jk-codex-msg.assistant').count(), 1, 'previa truncada restaurada deve ser reconhecida como a mesma resposta integral da tarefa');
        assert.strictEqual(await page.locator('#jk-codex-messages .jk-codex-open-fulltext').count(), 1);
        const { messages } = await readTaskHistory(page);
        const replies = messages.filter(item => item.role === 'assistant');
        assert.strictEqual(replies.length, 1);
        assert.strictEqual(replies[0].truncated, true);
        assert.strictEqual(replies[0].content_id, 'task-browser-1');
      }
      assert.strictEqual(calls.codexTasks.length, 1);
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const largeManualReport = 'R'.repeat(200000);
      const scenario = await newScenario(browser, baseUrl, {
        full: true,
        statusReady: true,
        fakeClock: true,
        seedAutomaticAlert: true,
        largeManualReport,
      });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();

      await page.clock.fastForward((31 * 60 * 1000) + 10);
      await new Promise(resolve => setTimeout(resolve, 20));
      assert.deepStrictEqual(calls.advancedAdmin, [], 'Black Jhon nao deve disparar consultas ou relatorios automaticos');
      assert.strictEqual(await page.evaluate(() => Object.prototype.hasOwnProperty.call(window, '__JK_CODEX_COORDINATOR_TEST__')), false);
      const legacyHistory = await page.evaluate(() => ({
        history: JSON.parse(localStorage.getItem('jk_codex_history_v2_legacy_automatic_test') || '[]'),
        seen: localStorage.getItem('jk_codex_assistant_seen_v1'),
      }));
      assert.deepStrictEqual(legacyHistory.history.map(item => item.text), ['MENSAGEM MANUAL PRESERVADA']);
      assert.strictEqual(legacyHistory.seen, null);
      assert.doesNotMatch(await page.locator('#jk-codex-messages').textContent(), /MENSAGEM AUTOMATICA LEGADA/);

      await page.locator('#jk-codex-report').evaluate(button => button.click());
      await page.locator('#jk-codex-messages .codex-report').waitFor();
      assert.deepStrictEqual(calls.advancedAdmin, ['/api/admin/codex/assistant/reports']);
      const reportCardLength = await page.locator('#jk-codex-messages .codex-report').evaluate(el => el.textContent.length);
      assert.ok(reportCardLength < 3000, 'relatorio manual de 200k deve virar cartao compacto');
      const persistedReport = await page.evaluate(() => Object.entries(localStorage)
        .filter(([key]) => key.startsWith('jk_codex_history_v2_'))
        .map(([, value]) => value)
        .join('\n'));
      assert.ok(persistedReport.length < 20000);
      assert.match(persistedReport, /report-browser-manual/);
      assert.match(persistedReport, /"full_length":200000/);
      assert.doesNotMatch(persistedReport, /R{1000}/);

      const popupPromise = page.waitForEvent('popup');
      await page.locator('#jk-codex-messages .codex-report .jk-codex-open-fulltext').click();
      const popup = await popupPromise;
      await popup.locator('pre').waitFor();
      assert.strictEqual((await popup.locator('pre').textContent()).length, largeManualReport.length);
      assert.strictEqual(calls.reportGets, 1);
      await popup.close();
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
      assert.strictEqual(await page.evaluate(() => Object.prototype.hasOwnProperty.call(window, '__JK_CODEX_COORDINATOR_TEST__')), false);
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
      const scenario = await newScenario(browser, baseUrl, {
        full: true,
        authCheckingOnce: true,
      });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      await waitForCondition(() => calls.codexStatus === 1, 'consulta checking nao iniciou');
      await page.locator('#jk-codex-close').click();
      await page.waitForTimeout(700);
      assert.strictEqual(calls.codexStatus, 1, 'painel fechado deve cancelar o polling de autenticacao');
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const scenario = await newScenario(browser, baseUrl, {
        full: true,
        authAlwaysChecking: true,
      });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      await waitForCondition(() => calls.codexStatus >= 3, 'polling checking nao iniciou');
      await page.waitForTimeout(10500);
      const callsAtDeadline = calls.codexStatus;
      await page.waitForTimeout(1000);
      assert.strictEqual(calls.codexStatus, callsAtDeadline, 'polling checking deve parar no deadline');
      assert.match(await page.locator('#jk-codex-status').textContent(), /Nao foi possivel confirmar/);
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const scenario = await newScenario(browser, baseUrl, {
        full: true,
        statusReady: true,
        authOutOfOrder: true,
      });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      await waitForCondition(() => calls.codexStatus === 1, 'primeira consulta de status nao iniciou');
      await page.locator('#jk-codex-refresh').click();
      await page.waitForFunction(() => document.querySelector('#jk-codex-status')?.textContent.includes('Black Jhon pronto'));
      await page.waitForTimeout(700);
      assert.strictEqual(calls.codexStatus, 2, 'resposta checking antiga nao deve reiniciar polling');
      assert.match(await page.locator('#jk-codex-status').textContent(), /Black Jhon pronto/);
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const scenario = await newScenario(browser, baseUrl, {
        full: true,
        statusReady: true,
        authCheckingOnce: true,
      });
      const { context, page, calls, pageErrors } = scenario;
      await page.locator('#jk-codex-panel.aberto').waitFor();
      await waitForCondition(() => calls.codexStatus >= 2, 'status de autenticacao nao foi atualizado');
      await page.waitForFunction(() => document.querySelector('#jk-codex-status')?.textContent.includes('Black Jhon pronto'));
      assert.strictEqual(calls.codexStatus, 2, 'estado checking deve iniciar somente uma nova consulta');
      assert.deepStrictEqual(pageErrors, []);
      await context.close();
    }

    {
      const context = await browser.newContext();
      const page = await context.newPage();
      const chunkRequests = [];
      page.on('request', request => {
        if (new URL(request.url()).pathname.startsWith('/ia-sidebar/')) chunkRequests.push(request.url());
      });
      await page.addInitScript(() => {
        Object.defineProperty(window, 'Worker', { configurable: true, value: undefined });
        sessionStorage.setItem('jk_ia_sidebar_full_loaded_session_v1', '1');
      });
      await page.goto(`${baseUrl}/test.html`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(900);
      assert.strictEqual(await page.locator('#jk-ia-light-fab').count(), 1, 'inicio deve manter somente o acionador leve da IA');
      assert.strictEqual(await page.locator('#jk-ia-fab').count(), 0, 'sidebar completa nao deve executar automaticamente apos navegacao');
      assert.strictEqual(await page.evaluate(() => window.__JK_IA_SIDEBAR_BOOTSTRAPPED__ === true), false);
      assert.deepStrictEqual(chunkRequests, [], 'inicio nao deve baixar os nove chunks pesados da sidebar');
      await page.locator('#jk-ia-light-fab').click();
      await page.locator('#jk-ia-fab').waitFor({ state: 'visible' });
      assert.strictEqual(chunkRequests.length, 9, 'primeiro clique deve carregar cada chunk uma unica vez');
      assert.strictEqual(await page.locator('#jk-codex-panel.aberto').count(), 1, 'Black Jhon deve continuar abrindo sob demanda');
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
