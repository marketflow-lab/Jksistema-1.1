'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'renovacao.html'), 'utf8');
assert.strictEqual(html, fs.readFileSync(path.join(root, 'static', 'renovacao.html'), 'utf8'));
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(match => match[1]).join('\n');
assert.doesNotThrow(() => new Function(script));

const originalCampaign = {
    id: 'C-TESTE', name: 'Promo Novembro', status: 'pending',
    start_date: '2026-11-03T03:00:00Z', finish_date: '2026-12-01T02:59:59Z',
    active_count: 0, programmed_count: 228,
};
const updatedCampaign = { ...originalCampaign, start_date: '2026-11-01T03:00:00Z' };

function setup({ status = 409, refreshStatus = 200 } = {}) {
    let now = '2026-10-29T15:00:00Z';
    class FixedDate extends Date {
        constructor(...args) { super(...(args.length ? args : [now])); }
        static now() { return new Date(now).getTime(); }
    }
    const ids = ['renovacao-loja', 'renovacao-campanha', 'renovacao-sync-origem',
        'renovacao-campanha-destino', 'renovacao-nome', 'renovacao-campanha-info',
        'renovacao-campanhas-lista', 'renovacao-lista-resumo', 'renovacao-api-status',
        'renovacao-periodo-popover', 'renovacao-periodo-titulo', 'renovacao-periodo-subtitulo',
        'renovacao-periodo-valor-inicio', 'renovacao-periodo-valor-fim',
        'renovacao-periodo-modo-inicio', 'renovacao-periodo-modo-fim',
        'renovacao-periodo-hint', 'renovacao-periodo-mes', 'renovacao-periodo-dias',
        'btn-salvar-periodo-renovacao'];
    const elements = new Map(ids.map(id => [id, {
        value: '', textContent: '', innerHTML: '', className: '', style: {},
        hidden: false, disabled: false, classList: { toggle() {} },
        querySelectorAll() { return []; },
    }]));
    elements.get('renovacao-loja').value = 'Loja Teste';
    elements.get('renovacao-campanha').value = originalCampaign.id;
    elements.get('btn-salvar-periodo-renovacao').textContent = 'Salvar periodo';
    const requests = [];
    const errorMessage = 'O período confirmado pelo Mercado Livre difere do solicitado.';
    const sandbox = {
        console, Date: FixedDate, Intl,
        document: {
            addEventListener() {},
            getElementById(id) { return elements.get(id) || null; },
            querySelectorAll() { return []; },
        },
        window: {},
        obterAuthHeaders() { return {}; },
        confirm() { return true; },
        async fetch(url, options) {
            requests.push({ url, options });
            if (url === '/api/renovacao/campanha-periodo') {
                return { ok: status < 400, status, async json() { return { detail: errorMessage }; } };
            }
            assert.strictEqual(url, '/api/renovacao/campanhas-usuario?loja=Loja%20Teste');
            return {
                ok: refreshStatus < 400, status: refreshStatus,
                async json() { return { campaigns: [updatedCampaign], detail: 'Falha ao consultar campanhas.' }; },
            };
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(script + `
        this.setCampaignsForTest = value => { campanhasRenovacao = value; };
        this.setPeriodForTest = value => { renovacaoPeriodoState = value; };
        this.getPeriodForTest = () => renovacaoPeriodoState;
    `, sandbox);
    sandbox.setCampaignsForTest([originalCampaign]);
    sandbox.setPeriodForTest({ campanhaId: originalCampaign.id, campo: 'fim', inicio: '2026-11-01', fim: '2026-11-30' });
    return { sandbox, elements, requests, errorMessage, setNow(value) { now = value; } };
}

function civilDate(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function checkCalendarAndNames() {
    const { sandbox, elements, setNow } = setup();
    for (const [value, expected] of [
        ['2026-11-30', '2026-11-30'],
        ['2026-11-30T23:59:59', '2026-11-30'],
        ['2026-12-01T02:59:59Z', '2026-11-30'],
        ['2026-12-01T02:59:59.000Z', '2026-11-30'],
        ['2026-11-30T23:59:59-03:00', '2026-11-30'],
        ['2026-12-01T00:59:59+01:00', '2026-11-30'],
        ['2026-11-01T03:00:00Z', '2026-11-01'],
        ['2027-01-01T02:59:59Z', '2026-12-31'],
        ['2027-01-01T03:00:00Z', '2027-01-01'],
    ]) {
        assert.strictEqual(sandbox.dataInputRenovacao(value), expected, `${process.env.TZ}: ${value}`);
        assert.strictEqual(sandbox.formatarDataCurta(value), expected.split('-').reverse().join('/'));
        assert.strictEqual(civilDate(sandbox.parseDataRenovacao(value)), expected);
    }
    assert.strictEqual(sandbox.dataInputRenovacao('invalid'), '');
    assert.strictEqual(sandbox.parseDataRenovacao('invalid'), null);
    sandbox.renderizarCampanhasRenovacao([updatedCampaign]);
    assert.match(elements.get('renovacao-campanhas-lista').innerHTML, /01\/11\/2026 ate 30\/11\/2026/);
    sandbox.abrirPopoverPeriodoRenovacao(null, originalCampaign.id);
    assert.strictEqual(sandbox.getPeriodForTest().fim, '2026-11-30');
    assert.strictEqual(elements.get('renovacao-periodo-valor-fim').textContent, '30/11/2026');
    assert.strictEqual(elements.get('renovacao-periodo-mes').textContent, 'Novembro 2026');

    for (const [startDate, expected, expectedName] of [
        ['2026-10-03T00:00:00', '2026-11-01', 'Promo Novembro'],
        ['2026-11-03T00:00:00', '2026-12-01', 'Promo Dezembro'],
        ['2026-05-18', '2026-11-01', 'Promo Novembro'],
        [undefined, '2026-11-01', 'Promo Novembro'],
        ['2026-11-01T01:30:00Z', '2026-11-01', 'Promo Novembro'],
        ['2026-12-31T23:59:59-03:00', '2027-01-01', 'Promo Janeiro'],
    ]) {
        const campaign = { name: 'Promo Outubro', start_date: startDate };
        assert.strictEqual(civilDate(sandbox.inicioProximaCampanhaRenovacao(campaign)), expected);
        assert.strictEqual(sandbox.gerarNomeCampanhaProximoMes(campaign), expectedName);
    }
    // A data atual do Brasil ainda pertence ao mês anterior nestes instantes UTC.
    setNow('2026-11-01T01:30:00Z');
    assert.strictEqual(civilDate(sandbox.inicioProximaCampanhaRenovacao({})), '2026-11-01');
    setNow('2027-01-01T01:30:00Z');
    assert.strictEqual(civilDate(sandbox.inicioProximaCampanhaRenovacao({})), '2027-01-01');
    setNow('2027-01-01T03:30:00Z');
    assert.strictEqual(civilDate(sandbox.inicioProximaCampanhaRenovacao({})), '2027-02-01');
}

async function checkPeriodRefresh() {
    const conflict = setup();
    conflict.sandbox.renderizarCampanhasRenovacao([originalCampaign]);
    await conflict.sandbox.alterarPeriodoCampanhaRenovacao();
    assert.strictEqual(conflict.requests.length, 2, 'Conflito exige uma única consulta após o PUT.');
    assert.strictEqual(conflict.requests[0].options.method, 'PUT');
    assert.deepStrictEqual(JSON.parse(conflict.requests[0].options.body), {
        loja: 'Loja Teste', campanha_id: originalCampaign.id,
        start_date: '2026-11-01', finish_date: '2026-11-30',
        promotion_type: 'SELLER_CAMPAIGN', quantidade_origem_esperada: 228,
    });
    assert.match(conflict.elements.get('renovacao-campanhas-lista').innerHTML, /01\/11\/2026 ate 30\/11\/2026/);
    assert.strictEqual(conflict.elements.get('renovacao-api-status').textContent, conflict.errorMessage);
    assert.match(conflict.elements.get('renovacao-api-status').className, /error/);
    assert.strictEqual(conflict.elements.get('renovacao-periodo-popover').hidden, true);
    assert.strictEqual(conflict.elements.get('btn-salvar-periodo-renovacao').disabled, false);

    for (const status of [400, 422]) {
        const invalid = setup({ status });
        await invalid.sandbox.alterarPeriodoCampanhaRenovacao();
        assert.strictEqual(invalid.requests.length, 1, 'Validação rejeitada não deve consultar campanhas.');
        assert.strictEqual(invalid.elements.get('renovacao-api-status').textContent, invalid.errorMessage);
        assert.strictEqual(invalid.elements.get('renovacao-periodo-popover').hidden, false);
    }
    const localInvalid = setup();
    localInvalid.sandbox.setPeriodForTest({ campanhaId: originalCampaign.id, inicio: '2026-11-30', fim: '2026-11-01' });
    await localInvalid.sandbox.alterarPeriodoCampanhaRenovacao();
    assert.strictEqual(localInvalid.requests.length, 0);

    const refreshFailure = setup({ refreshStatus: 503 });
    await refreshFailure.sandbox.alterarPeriodoCampanhaRenovacao();
    assert.strictEqual(refreshFailure.requests.length, 2);
    assert.strictEqual(refreshFailure.elements.get('renovacao-api-status').textContent, refreshFailure.errorMessage,
        'Falha na recarga não deve substituir o motivo original da alteração.');
}

(async () => {
    const originalTimezone = process.env.TZ;
    try {
        for (const timezone of ['UTC', 'America/Sao_Paulo', 'Asia/Tokyo']) {
            process.env.TZ = timezone;
            checkCalendarAndNames();
        }
        await checkPeriodRefresh();
    } finally {
        if (originalTimezone === undefined) delete process.env.TZ;
        else process.env.TZ = originalTimezone;
    }
    console.log('Renovacao: fuso brasileiro, mes completo, nomes e recarga apos conflito aprovados.');
})().catch(error => { console.error(error); process.exitCode = 1; });
