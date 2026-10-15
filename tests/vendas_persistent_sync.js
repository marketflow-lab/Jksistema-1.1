'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const syncSource = fs.readFileSync(path.join(root, 'static/vendas/sync.js'), 'utf8');
const periodoSource = fs.readFileSync(path.join(root, 'static/vendas/periodo-cache.js'), 'utf8');

function element() {
    return {
        checked: false, disabled: false, innerHTML: '', textContent: '', className: '',
        listeners: {},
        addEventListener(name, handler) { this.listeners[name] = handler; }
    };
}

function setup(options = {}) {
    const elements = Object.fromEntries(['btnSync', 'btnCancel', 'forcarSync', 'syncAutomatico', 'status', 'syncPersistenteStatus'].map(id => [id, element()]));
    elements.syncAutomatico.checked = true;
    const calls = [];
    const events = {};
    let payload = options.payload || { active: false, automacao: { lojas: [], jobs: [], pendentes: 0, habilitadas: 0 } };
    const context = {
        console, AbortController, DOMException, setTimeout, clearTimeout, URLSearchParams,
        document: { getElementById: id => elements[id] || null },
        window: { location: {}, addEventListener: (name, handler) => { events[name] = handler; } },
        localStorage: { getItem: () => JSON.stringify({ client_id: 'cliente-teste' }) },
        statusEl: elements.status, btnSync: elements.btnSync, btnCancel: elements.btnCancel,
        REQUEST_TIMEOUT_MS: 500, spinnerHtml: '', autoSyncTimer: null, clientId: 'cliente-teste',
        syncEmAndamento: false, syncController: null, cancelSolicitado: false,
        periodoSelecionadoPeloUsuario: false, lojaSelecionada: '__todas',
        lojasDisponiveis: [
            { nome: 'Loja A', store_id: 'id-a' },
            { nome: 'Loja B', store_id: 'id-b' }
        ],
        dados: [{ data: '2024-01-01', loja_conta: 'Loja A' }],
        devolucaoItens: [{ data_emissao: '2024-01-01', loja_conta: 'Loja B' }],
        dadosModoResumo: true, vendasResumoMeta: { dias_com_dados: ['2024-01-01'] },
        obterAuthHeaders: extra => ({ ...extra }),
        mesmaLoja: (left, right) => String(left) === String(right),
        corrigirTextoVendas: value => String(value ?? ''),
        escaparHtmlVendas: value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
        formatDate: value => value,
        fecharCalendariosData() {}, carregarLojas: async () => true,
        getDataIniISO: () => options.inicio || '2024-01-01',
        getDataFimISO: () => options.fim || '2026-10-15',
        alert: message => { throw new Error('Unexpected alert: ' + message); },
        carregarVendas: async () => {}, carregarGrafico: async () => {},
        vendasSyncMonitor: {
            getLastPayload: () => payload,
            refresh: async () => payload,
            subscribe() {}, start() {},
            waitForInactive: () => { throw new Error('O navegador não deve administrar a fila de execução.'); }
        },
        fetch: async (url, request = {}) => {
            const call = { url, body: request.body ? JSON.parse(request.body) : null };
            calls.push(call);
            if (options.fetch) return options.fetch(call, calls);
            return { ok: true, json: async () => ({ queued: true, job_id: 'job-' + calls.length }) };
        }
    };
    vm.createContext(context);
    vm.runInContext(syncSource, context, { filename: 'sync.js' });
    vm.runInContext(periodoSource.slice(periodoSource.indexOf('function setSyncButtons('), periodoSource.indexOf('// Funções utilitárias')), context, { filename: 'periodo-cache.js#monitor' });
    context.aguardar = async () => {};
    return { context, elements, calls, events, setPayload(value) { payload = value; } };
}

(async () => {
    const partial = setup({ inicio: '2024-01-01', fim: '2024-01-01' });
    await partial.context.executarSync();
    assert.equal(partial.calls.length, 2, 'uma linha existente não pode fazer o dia desaparecer da solicitação de nenhuma loja');
    assert.deepEqual(partial.calls.map(call => call.body.store_id), ['id-a', 'id-b']);
    assert(partial.calls.every(call => call.body.automatico === true && call.body.forcar_resync === false));

    const historical = setup();
    await historical.context.executarSync();
    assert.equal(historical.calls.length, 2, 'todo o histórico deve ser persistido por loja em um único pedido');
    assert(historical.calls.every(call => call.body.data_inicio === '2024-01-01' && call.body.data_fim === '2026-10-15'));
    assert.doesNotMatch(historical.elements.status.textContent, /finalizad|concluíd/i, 'registrar não comprova término');

    const failedStore = setup({
        fetch: async call => call.body?.store_id === 'id-a'
            ? { ok: false, json: async () => ({ detail: 'Sem conexão da loja' }) }
            : { ok: true, json: async () => ({ queued: true }) }
    });
    await failedStore.context.executarSync();
    assert.equal(failedStore.calls.length, 2, 'uma falha não deve impedir o registro das outras lojas');
    assert.match(failedStore.elements.status.innerHTML, /registrado em 1 loja/);
    assert.match(failedStore.elements.status.innerHTML, /Loja A: Sem conexão da loja/);

    const quickCompletion = setup({ fetch: async () => ({ ok: true, json: async () => ({ accepted: true, started: false, queued: false, status: 'complete' }) }) });
    await quickCompletion.context.executarSync();
    assert.equal(quickCompletion.calls.length, 2);
    assert.doesNotMatch(quickCompletion.elements.status.innerHTML, /não confirmou|Erro/);

    const notAccepted = setup({ fetch: async () => ({ ok: true, json: async () => ({ already_running: true }) }) });
    await assert.rejects(notAccepted.context.iniciarSyncLoja('Loja A'), /não confirmou o registro/);

    const recovery = setup();
    const pedido = { loja: 'Loja A', store_id: 'id-a', data_inicio: '2026-10-01', data_fim: '2026-10-15', forcar_resync: false, automatico: true };
    const currentJob = { ...pedido, job_id: 'job-correto', status: 'queued' };
    recovery.setPayload({ active: true, automacao: { jobs: [{ ...currentJob, data_inicio: '2026-10-02' }], lojas: [{ loja: 'Loja A', store_id: 'id-a', habilitado: true }] } });
    assert.equal(await recovery.context.recuperarSyncLojaAposFalhaFetch(pedido, new TypeError('Failed to fetch')), null, 'outro intervalo não confirma o pedido perdido');
    recovery.setPayload({ active: false, automacao: { jobs: [currentJob], lojas: [{ loja: 'Loja A', store_id: 'id-a', habilitado: true }] } });
    assert.equal((await recovery.context.recuperarSyncLojaAposFalhaFetch(pedido, new TypeError('Failed to fetch'))).job_id, 'job-correto');
    assert.equal(recovery.context.pedidoSyncVendasConfirmado({ ...currentJob, store_id: 'outra-loja' }, pedido), false);
    assert.equal(recovery.context.pedidoSyncVendasConfirmado({ ...currentJob, status: 'complete' }, pedido), false, 'uma execução antiga concluída não confirma um novo pedido perdido');
    assert.equal(recovery.context.pedidoSyncVendasConfirmado({ ...currentJob, status: 'paused' }, pedido), false);

    const persisted = {
        active: false,
        automacao: {
            pendentes: 1, habilitadas: 1,
            lojas: [{ loja: '<Loja A>', store_id: 'id-a', habilitado: true, status: 'retry', data_inicio: '2026-10-01', data_fim: '2026-10-15', dias_concluidos: 3, dias_total: 15, checkpoint: { documentos_salvos: 2, ultimo_tipo: 'pedido', ultimo_id: '123', atualizado_em: '2026-10-15T13:00:00Z' }, ultimo_erro: '<falha>', ultima_conferencia: '2026-10-14T13:00:00Z', proxima_execucao: '2026-10-15T14:00:00Z' }]
        }
    };
    const reopened = setup({ payload: persisted });
    reopened.context.aplicarEstadoMonitorSyncVendas(persisted);
    assert.equal(reopened.elements.btnSync.disabled, false);
    assert.equal(reopened.elements.btnCancel.disabled, false, 'pausa deve funcionar entre ciclos automáticos');
    assert.match(reopened.elements.syncPersistenteStatus.innerHTML, /1 sincronização.*pendente/);
    assert.match(reopened.elements.syncPersistenteStatus.innerHTML, /3\/15 dia/);
    assert.match(reopened.elements.syncPersistenteStatus.innerHTML, /&lt;falha&gt;/);
    assert.doesNotMatch(reopened.elements.syncPersistenteStatus.innerHTML, /<falha>|Período conferido/);
    assert.match(reopened.elements.syncPersistenteStatus.innerHTML, /Aguardando próxima tentativa/);
    assert.match(reopened.elements.syncPersistenteStatus.innerHTML, /Último documento coletado: Pedido #123/);
    const coletaExibida = reopened.elements.syncPersistenteStatus.innerHTML.split('Último documento coletado: ')[1].split('<br>')[0];
    assert.match(coletaExibida, /2 documento\(s\) coletado\(s\) nesta etapa/);
    assert.doesNotMatch(coletaExibida, /gravado|conferido|concluído/i, 'a preparação do documento não comprova a confirmação do dia');
    for (const [tipo, rotulo] of [['nfe_entrada', 'NF-e de entrada'], ['nfe_saida', 'NF-e de saída']]) {
        const loja = { ...persisted.automacao.lojas[0], checkpoint: { ultimo_tipo: tipo, ultimo_id: '<456>' } };
        reopened.context.renderAutomacaoSyncVendas({ automacao: { ...persisted.automacao, lojas: [loja] } });
        assert(reopened.elements.syncPersistenteStatus.innerHTML.includes('Último documento coletado: ' + rotulo + ' #&lt;456&gt;'));
    }
    reopened.context.renderAutomacaoSyncVendas({ automacao: { ...persisted.automacao, lojas: [{ ...persisted.automacao.lojas[0], status: 'complete' }] } });
    assert.doesNotMatch(reopened.elements.syncPersistenteStatus.innerHTML, /Período conferido/, 'status concluído com erro ou pendência não pode gerar sucesso');
    reopened.context.aplicarEstadoMonitorSyncVendas(persisted);
    reopened.elements.status.textContent = '';
    assert.match(reopened.elements.syncPersistenteStatus.innerHTML, /Última conferência/, 'carregar vendas não deve apagar a situação persistida');
    await reopened.elements.btnCancel.listeners.click();
    assert.equal(reopened.calls[0].url, '/api/vendas/sync/cancel');
    assert.equal(reopened.elements.syncAutomatico.checked, false);

    const pauseFails = setup({ payload: persisted, fetch: async () => ({ ok: false }) });
    await pauseFails.elements.btnCancel.listeners.click();
    assert.match(pauseFails.elements.status.textContent, /não confirmou a pausa/);
    assert.equal(pauseFails.elements.syncAutomatico.checked, true);
    assert.equal(pauseFails.elements.btnCancel.disabled, false);

    let releasePost;
    const pauseInFlight = setup({
        fetch: async call => {
            if (call.url === '/api/vendas/sync') await new Promise(resolve => { releasePost = resolve; });
            return { ok: true, json: async () => ({ queued: true }) };
        }
    });
    const registration = pauseInFlight.context.executarSync();
    await new Promise(resolve => setImmediate(resolve));
    const pause = pauseInFlight.elements.btnCancel.listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(pauseInFlight.calls.length, 1, 'pausa deve aguardar o registro corrente para preservar a ordem no servidor');
    releasePost();
    await Promise.all([registration, pause]);
    assert.deepEqual(pauseInFlight.calls.map(call => call.url), ['/api/vendas/sync', '/api/vendas/sync/cancel']);
    assert.match(pauseInFlight.elements.status.textContent, /pausada/);

    console.log('vendas persistent sync frontend: ok');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
