'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/ia-sidebar/03-init-shell-codex.part.js'), 'utf8');
const start = source.indexOf('    function _codexReportQueueActionId(');
const end = source.indexOf('    function _codexAppendReportDecisionCards(', start);
assert(start >= 0 && end > start, 'As ações de relatório devem permanecer acessíveis para verificação');
const code = source.slice(start, end) + '\nglobalThis.propor = _codexProporFilaRelatorio;';

function scenario(options = {}) {
  const state = {
    selected: Object.hasOwn(options, 'selected') ? options.selected : { id: 'supplier-b', nome_empresa: 'Fornecedor B' },
    loadMode: options.loadMode || 'ok',
    selectionError: options.selectionError,
    scripts: [], selections: [], proposals: [], renders: [], statuses: [], requests: [],
  };
  const button = { disabled: false, textContent: 'Criar fila' };
  const helper = {
    async selecionar(config) {
      assert.strictEqual(button.disabled, true, 'O botão deve permanecer bloqueado durante a seleção');
      assert.strictEqual(config.titulo, 'Fornecedor da lista de reposição');
      assert.deepStrictEqual(config.headers(), { Authorization: 'Bearer test' });
      await config.fetch('/api/cadastro/fornecedores', { headers: config.headers() });
      state.selections.push(config);
      if (state.selectionError) throw new Error(state.selectionError);
      return state.selected;
    },
  };
  const sandbox = {
    window: {
      __JK_IA_SIDEBAR_FETCH__: async (input, init) => {
        state.requests.push({ input, init });
        return { ok: true, json: async () => [] };
      },
    },
    document: {
      createElement: tag => ({ tag, removed: false, remove() { this.removed = true; } }),
      head: { appendChild(script) {
        assert.strictEqual(script.tag, 'script');
        assert.strictEqual(script.src, '/medias_compras/fornecedores.js?v=20261002-fornecedor-listas-v1');
        state.scripts.push(script);
        queueMicrotask(() => {
          if (state.loadMode === 'error') script.onerror();
          else {
            if (state.loadMode === 'ok') sandbox.window.JKFornecedorListas = helper;
            script.onload();
          }
        });
      } },
    },
    _authHeaders: () => ({ Authorization: 'Bearer test' }),
    _usuarioLocalEhFull: () => options.full !== false,
    _codexSetStatus: (text, error) => state.statuses.push({ text, error: !!error }),
    _codexErroCurto: (error, fallback) => error.message || fallback,
    _codexGetActiveConversationId: create => { assert.strictEqual(create, true); return 'conversation-test'; },
    _codexObterContextoTelaAtual: () => ({ page: 'medias_compras' }),
    _codexFetchJson: async (url, config) => {
      assert.strictEqual(url, '/api/admin/codex/actions/proposals');
      assert.strictEqual(config.method, 'POST');
      const proposal = JSON.parse(config.body);
      state.proposals.push(proposal);
      return { proposal: { id: 'proposal-test' } };
    },
    _codexRenderActionProposal: proposal => state.renders.push(proposal),
  };
  if (options.installed !== false) sandbox.window.JKFornecedorListas = helper;
  vm.runInNewContext(code, sandbox);
  const action = { action_type: 'replenishment', recommendation: 'Repor estoque' };
  return { state, button, run: reportAction => sandbox.propor('report-test', reportAction || action, button) };
}

(async () => {
  const selected = scenario();
  await selected.run();
  assert.strictEqual(selected.state.scripts.length, 0, 'O helper já instalado deve ser reutilizado');
  assert.strictEqual(selected.state.selections.length, 1);
  assert.deepStrictEqual(selected.state.proposals[0].params, {
    report_id: 'report-test', report_action: { action_type: 'replenishment', recommendation: 'Repor estoque' }, fornecedor_id: 'supplier-b',
  });
  assert.strictEqual(selected.state.proposals[0].conversation_id, 'conversation-test');
  assert.strictEqual(selected.state.renders.length, 1, 'A proposta deve continuar exigindo aprovação');
  assert.strictEqual(selected.button.textContent, 'Aguardando aprovacao');
  assert.strictEqual(selected.button.disabled, true);
  assert.deepStrictEqual(selected.state.requests[0].init.headers, { Authorization: 'Bearer test' });

  const cancelled = scenario({ selected: null });
  await cancelled.run();
  assert.strictEqual(cancelled.state.proposals.length, 0, 'Cancelar a seleção não pode criar proposta');
  assert.strictEqual(cancelled.button.disabled, false);
  assert.strictEqual(cancelled.button.textContent, 'Criar fila');

  const selectionError = scenario({ selectionError: 'Cadastro indisponível' });
  await selectionError.run();
  assert.strictEqual(selectionError.state.proposals.length, 0);
  assert.strictEqual(selectionError.button.disabled, false);
  assert.deepStrictEqual(selectionError.state.statuses.at(-1), { text: 'Cadastro indisponível', error: true });

  const dynamic = scenario({ installed: false });
  await dynamic.run();
  assert.strictEqual(dynamic.state.scripts.length, 1);
  assert.strictEqual(dynamic.state.proposals[0].params.fornecedor_id, 'supplier-b');

  for (const loadMode of ['error', 'missing']) {
    const failed = scenario({ installed: false, loadMode });
    await failed.run();
    assert.strictEqual(failed.state.proposals.length, 0, 'Falha ao carregar helper não pode criar proposta');
    assert.strictEqual(failed.state.scripts[0].removed, true);
    assert.strictEqual(failed.button.disabled, false);
    assert.strictEqual(failed.state.statuses.at(-1).error, true);
    failed.state.loadMode = 'ok';
    await failed.run();
    assert.strictEqual(failed.state.scripts.length, 2, 'Falha no script deve permitir nova tentativa');
    assert.strictEqual(failed.state.proposals[0].params.fornecedor_id, 'supplier-b');
  }

  for (const actionType of ['price_review', 'liquidation']) {
    const other = scenario({ installed: false, loadMode: 'error' });
    await other.run({ action_type: actionType });
    assert.strictEqual(other.state.scripts.length, 0, 'Outras filas não devem carregar seleção de fornecedor');
    assert.strictEqual(other.state.selections.length, 0);
    assert.strictEqual(other.state.proposals[0].action_id, 'reports.queue_' + actionType);
    assert.strictEqual(Object.hasOwn(other.state.proposals[0].params, 'fornecedor_id'), false);
    assert.strictEqual(other.state.renders.length, 1);
  }

  const malformed = scenario({ selected: { id: '' } });
  await malformed.run();
  assert.strictEqual(malformed.state.proposals.length, 0);
  assert.strictEqual(malformed.button.disabled, false);

  const restricted = scenario({ full: false });
  await restricted.run();
  assert.strictEqual(restricted.state.selections.length, 0);
  assert.strictEqual(restricted.state.proposals.length, 0);
  console.log('Fornecedor em fila de reposição: seleção, cancelamento, falhas, carga dinâmica, autenticação e aprovação preservada: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
