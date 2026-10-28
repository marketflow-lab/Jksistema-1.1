'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'renovacao.html'), 'utf8');
assert.strictEqual(html, fs.readFileSync(path.join(__dirname, '..', 'static', 'renovacao.html'), 'utf8'));
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(match => match[1]).join('\n');
assert.doesNotThrow(() => new Function(script));

function setup() {
    const ids = ['renovacao-loja', 'renovacao-sync-origem', 'renovacao-campanha-destino',
        'btn-sincronizar-renovacao', 'btn-conferir-renovacao', 'btn-corrigir-renovacao',
        'btn-baixar-conferencia-renovacao', 'renovacao-api-status', 'renovacao-sync-progress',
        'renovacao-sync-progress-text', 'renovacao-sync-progress-percent', 'renovacao-sync-progress-fill',
        'renovacao-sync-result-stats', 'renovacao-sync-details', 'renovacao-sync-details-text'];
    const elements = new Map(ids.map(id => [id, { id, value: '', disabled: false, textContent: '', className: '', style: {} }]));
    elements.get('renovacao-loja').value = 'Loja';
    elements.get('renovacao-sync-origem').value = 'C-MLB1';
    elements.get('renovacao-campanha-destino').value = 'C-MLB2';
    const requests = [], confirmations = [], progressStyles = [];
    const progress = elements.get('renovacao-sync-progress');
    Object.defineProperty(progress, 'className', { get() { return progressStyles.at(-1) || ''; }, set(value) { progressStyles.push(value); } });
    const sandbox = {
        console, Date, setInterval() { return 1; }, clearInterval() {},
        document: { addEventListener() {}, getElementById(id) { return elements.get(id) || null; } },
        window: {},
        obterAuthHeaders() { return { 'X-Test-Session': 'authenticated' }; },
        confirm(message) { confirmations.push(message); return true; },
        async fetch(url, options) {
            requests.push({ url, options });
            assert(['btn-sincronizar-renovacao', 'btn-conferir-renovacao', 'btn-corrigir-renovacao'].every(id => elements.get(id).disabled));
            return { ok: true, async json() { return { job_id: 'job-safe' }; } };
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(script + '\nthis.setCampaignsForTest = value => { campanhasRenovacao = value; };', sandbox);
    sandbox.setCampaignsForTest([{ id: 'C-MLB1', name: 'Origem', active_count: 908, programmed_count: 0 }, { id: 'C-MLB2', name: 'Destino' }]);
    const poller = sandbox.acompanharSincronizacaoRenovacao;
    sandbox.acompanharSincronizacaoRenovacao = async (_job, button) => sandbox.resetarBotaoSincronizacaoRenovacao(button);
    return { sandbox, elements, requests, confirmations, progressStyles, poller };
}

(async () => {
    const audit = setup();
    await audit.sandbox.sincronizarPromocaoRenovacao(true);
    assert.strictEqual(audit.requests[0].url, '/api/renovacao/conferir-promocao/iniciar');
    const auditBody = JSON.parse(audit.requests[0].options.body);
    assert.strictEqual(auditBody.somente_conferir, true);
    assert.strictEqual(auditBody.corrigir_divergencias, false);
    assert.strictEqual(audit.confirmations.length, 0);

    const repair = setup();
    await repair.sandbox.sincronizarPromocaoRenovacao(false, true);
    assert.strictEqual(repair.requests[0].url, '/api/renovacao/corrigir-divergencias/iniciar');
    const repairBody = JSON.parse(repair.requests[0].options.body);
    assert.strictEqual(repairBody.somente_conferir, false);
    assert.strictEqual(repairBody.corrigir_divergencias, true);
    assert.strictEqual(repairBody.quantidade_origem_esperada, 908);
    assert.match(repair.confirmations[0], /precos podem aumentar ou diminuir/);
    assert.match(repair.confirmations[0], /anuncios faltantes.*incluidos/);
    for (const id of ['btn-sincronizar-renovacao', 'btn-conferir-renovacao', 'btn-corrigir-renovacao']) assert.strictEqual(repair.elements.get(id).disabled, false);
    assert.strictEqual(repair.elements.get('btn-corrigir-renovacao').textContent, 'Conferir e corrigir');
    assert.strictEqual(repair.elements.get('btn-baixar-conferencia-renovacao').style.display, 'none');

    const canceled = setup();
    canceled.sandbox.confirm = () => false;
    await canceled.sandbox.sincronizarPromocaoRenovacao(false, true);
    assert.strictEqual(canceled.requests.length, 0);

    const ambiguous = setup();
    await ambiguous.sandbox.sincronizarPromocaoRenovacao(true, true);
    assert.strictEqual(ambiguous.requests.length, 0);
    assert.match(ambiguous.elements.get('renovacao-api-status').className, /error/);

    const oldBackend = setup();
    oldBackend.sandbox.fetch = async () => ({ ok: false, async json() { return { detail: 'Not Found' }; } });
    await oldBackend.sandbox.sincronizarPromocaoRenovacao(false, true);
    assert.match(oldBackend.elements.get('renovacao-sync-progress').className, /error/);
    assert.strictEqual(oldBackend.elements.get('btn-corrigir-renovacao').disabled, false);

    const partial = setup();
    partial.sandbox.carregarCampanhasRenovacao = async silencioso => assert.strictEqual(silencioso, true);
    partial.sandbox.fetch = async () => ({ ok: true, async json() { return { status: 'done', result: {
        success: false, corrigir_divergencias: true, atualizados: 46, incluidos: 2, falhas_total: 68,
        correcao: { corrigidos: 48, pendentes: 26 },
        conferencia: { completa: false, confirmados: 1047, total_origem: 1062, esperado: 908,
            contagem_coerente: false, pendentes: 15, inconclusivos: Array(53).fill({}) },
    } }; } });
    await partial.poller('job-safe', partial.elements.get('btn-corrigir-renovacao'), 'Destino');
    assert(partial.progressStyles.every(value => !/\bok\b/.test(value)), 'Partial correction must never flash green');
    const resumo = partial.elements.get('renovacao-sync-result-stats').innerHTML;
    assert.match(resumo, />1\.047<.*descontos confirmados/);
    assert.match(resumo, />48<.*corrigidos/);
    assert.match(resumo, />68<.*anúncios pendentes/);
    assert(!resumo.includes('26'), 'The correction subset must not replace total pending items');
    assert.match(partial.elements.get('renovacao-sync-details-text').textContent, /908 esperados; 1\.062 encontrados/);
    assert.match(partial.elements.get('renovacao-sync-details-text').textContent, /53 consultas sem confirmação/);
    assert.strictEqual(partial.elements.get('renovacao-sync-details').open, false);
    assert.match(partial.elements.get('renovacao-sync-progress').className, /warning.*finished/);
    assert.strictEqual(partial.elements.get('renovacao-api-status').style.display, 'none');
    assert.strictEqual(partial.elements.get('btn-baixar-conferencia-renovacao').style.display, '');
    partial.sandbox.mostrarResultadoSincronizacaoRenovacao({ success: false, somente_conferir: true,
        falhas_total: 0, conferencia: { completa: false, confirmados: 1098, total_origem: 1098,
            esperado: 908, contagem_coerente: false } }, 'Destino');
    assert.match(partial.elements.get('renovacao-sync-progress-text').textContent, /Confira a contagem da origem/);
    assert(partial.progressStyles.every(value => !/\bok\b/.test(value)), 'A count mismatch must not appear successful even with no pending items');
    console.log('Renovacao: modos de consulta/correcao, cancelamento, version skew e resultado parcial aprovados.');
})().catch(error => { console.error(error); process.exitCode = 1; });
