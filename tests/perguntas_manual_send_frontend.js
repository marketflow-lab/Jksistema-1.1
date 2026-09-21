const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '../static/perguntas_pos_venda/perguntas.js'), 'utf8');
const start = source.indexOf('async function enviarRespostaPerguntaManual(');
const end = source.indexOf('\nfunction carregarLojas()', start);
assert(start >= 0 && end > start);

async function scenario(confirmed, renderFails = false) {
    const question = { id: 'q-fixture', store_id: 'StoreExact', item_id: 'MLB-fixture' };
    const textarea = { value: 'Approved fixture.', dataset: { codexProposalId: 'draft' } };
    const status = {};
    const button = { disabled: false };
    const posts = [];
    const invalidatedDrafts = [];
    const cleanedDrafts = [];
    const context = {
        state: {}, perguntasStatus: {}, window: {
            JKPerguntasLoading: {
                invalidar: (loja, current) => { invalidatedDrafts.push([loja, current.id]); }
            }
        },
        obterPerguntaPorId: () => question,
        chavePerguntaAtendimento: current => `${current.store_id}::${current.id}`,
        lojaOrigemItem: () => 'Fixture store',
        skuRealPergunta: () => 'sku-fixture',
        obterAuthHeaders: () => ({}),
        flushAutosaveRascunhoAtendimentoCodex: async () => {},
        limparEstadoJobAtendimentoCodex: questionKey => { cleanedDrafts.push(questionKey); },
        setStatusRespostaPergunta: (target, text) => { target.textContent = text; },
        mensagemErroApi: () => 'Falha temporária nas lojas.',
        mensagemErro: error => error.message,
        renderizarPerguntas: () => { if (renderFails) throw new Error('fixture rendering failure'); },
        carregarContadoresNotificacoes: () => {},
        fetch: async (_url, options) => {
            posts.push(JSON.parse(options.body));
            return { ok: confirmed, json: async () => confirmed
                ? { resposta: textarea.value, warnings: ['local_state_pending'] }
                : { detail: { code: 'stores_busy' } } };
        },
    };
    vm.createContext(context);
    vm.runInContext(source.slice(start, end), context);
    await context.enviarRespostaPerguntaManual(question.id, 'Fixture store', textarea, button, {}, status);
    assert.strictEqual(posts.length, 1, 'must never automatically repeat publication');
    assert.strictEqual(posts[0].store_id, 'StoreExact');
    assert.strictEqual(textarea.value, 'Approved fixture.');
    if (confirmed) {
        assert.strictEqual(question.status, 'ANSWERED');
        assert.match(context.perguntasStatus.textContent, /[Nn]ão é necessário reenviar/);
        if (renderFails) {
            assert.match(status.textContent, /Resposta enviada ao Mercado Livre/);
            assert.strictEqual(button.disabled, true, 'confirmed POST must not re-enable sending after UI error');
        } else {
            assert.match(context.perguntasStatus.textContent, /envio foi confirmado/);
        }
        assert.strictEqual(textarea.dataset.codexProposalId, undefined);
        assert.deepStrictEqual(invalidatedDrafts, [['Fixture store', 'q-fixture']], 'envio confirmado deve invalidar o rascunho persistido da pergunta');
    } else {
        assert.strictEqual(question.status, undefined);
        assert.strictEqual(button.disabled, false);
        assert.strictEqual(textarea.dataset.codexProposalId, 'draft');
        assert.deepStrictEqual(invalidatedDrafts, [], 'falha de envio deve preservar o rascunho para nova tentativa');
        assert.match(status.textContent, /Erro ao enviar/);
    }
}

Promise.resolve().then(() => scenario(true)).then(() => scenario(false)).then(() => scenario(true, true)).then(() => {
    console.log('Manual send frontend: 3 scenarios passed.');
}).catch(error => { console.error(error); process.exitCode = 1; });
