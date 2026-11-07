const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '../static/perguntas_pos_venda/perguntas.js'), 'utf8');
const runtime = fs.readFileSync(path.join(__dirname, '../static/perguntas_pos_venda/runtime.js'), 'utf8');
const runtimeStart = runtime.indexOf('function corrigirTextoQuebrado(');
const runtimeEnd = runtime.indexOf('\nfunction formatarData(', runtimeStart);
assert(runtimeStart >= 0 && runtimeEnd > runtimeStart);
const start = source.indexOf('async function enviarRespostaPerguntaManual(');
const end = source.indexOf('\nfunction carregarLojas()', start);
assert(start >= 0 && end > start);

async function scenario({ failureDetail, expectedError, renderFails = false, replaceCard = false, changeSession = false, renewSession = false } = {}) {
    const confirmed = failureDetail === undefined;
    const question = { id: 'q-fixture', store_id: 'StoreExact', item_id: 'MLB-fixture' };
    const textarea = { value: 'Approved fixture.', dataset: { codexProposalId: 'draft' } };
    const questionKey = `${question.store_id}::${question.id}`;
    const status = {};
    const replacementStatus = {};
    const draftUpdates = [];
    const button = { disabled: false };
    let session = 'fixture-session';
    const posts = [];
    const invalidatedDrafts = [];
    const cleanedDrafts = [];
    const context = {
        state: {}, perguntasStatus: {}, window: {
            JKPerguntasLoading: {
                identidadeSessaoPorHeaders: headers => headers.Authorization.replace(/-renewed$/, ''),
                invalidar: (loja, current) => { invalidatedDrafts.push([loja, current.id]); },
                atualizarRascunhoPorChave: (key, update) => { draftUpdates.push([key, update.motivoFalhaEnvio]); },
            }
        },
        perguntaAtendimentoPorChave: key => key === questionKey ? question : null,
        cardsAtuaisJobAtendimentoCodex: key => replaceCard && key === questionKey
            ? [{ querySelector: () => replacementStatus }] : [],
        lojaOrigemItem: () => 'Fixture store',
        skuRealPergunta: () => 'sku-fixture',
        obterAuthHeaders: () => ({ Authorization: session }),
        flushAutosaveRascunhoAtendimentoCodex: async () => {},
        limparEstadoJobAtendimentoCodex: questionKey => { cleanedDrafts.push(questionKey); },
        setStatusRespostaPergunta: (target, text) => { target.textContent = text; },
        renderizarPerguntas: () => { if (renderFails) throw new Error('fixture rendering failure'); },
        carregarContadoresNotificacoes: () => {},
        fetch: async (_url, options) => {
            posts.push(JSON.parse(options.body));
            if (changeSession) session = 'other-fixture-session';
            if (renewSession) session += '-renewed';
            return { ok: confirmed, json: async () => confirmed
                ? { resposta: textarea.value, warnings: ['local_state_pending'] }
                : { detail: failureDetail } };
        },
    };
    vm.createContext(context);
    vm.runInContext(runtime.slice(runtimeStart, runtimeEnd), context);
    vm.runInContext(source.slice(start, end), context);
    await context.enviarRespostaPerguntaManual(questionKey, textarea, button, {}, status);
    assert.strictEqual(posts.length, 1, 'must never automatically repeat publication');
    assert.strictEqual(posts[0].store_id, 'StoreExact');
    assert.strictEqual(textarea.value, 'Approved fixture.');
    if (changeSession) {
        assert.strictEqual(replacementStatus.textContent, undefined);
        assert.deepStrictEqual(draftUpdates, [[questionKey, '']]);
        return;
    }
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
        assert.strictEqual(status.textContent, expectedError);
        if (replaceCard) {
            assert.strictEqual(replacementStatus.textContent, expectedError);
            assert.deepStrictEqual(draftUpdates, [[questionKey, ''], [questionKey, expectedError]]);
        }
    }
}

async function main() {
    await scenario();
    await scenario({ renderFails: true });
    await scenario({
        failureDetail: 'Falha temporária nas lojas.',
        expectedError: 'Erro ao enviar: Falha temporária nas lojas.',
    });
    await scenario({
        failureDetail: { code: 'stores_busy', message: 'A loja está ocupada.' },
        expectedError: 'Erro ao enviar: A loja está ocupada.',
    });
    await scenario({
        failureDetail: { code: 'ml_item_inactive', message: 'Anúncio pausado por falta de estoque no Mercado Livre.' },
        expectedError: 'Anúncio pausado por falta de estoque no Mercado Livre.',
        replaceCard: true,
        renewSession: true,
    });
    await scenario({
        failureDetail: { code: 'ml_item_inactive', message: 'Anúncio encerrado no Mercado Livre.' },
        replaceCard: true,
        changeSession: true,
    });
    console.log('Manual send frontend: 6 scenarios passed.');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
