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
    let refreshCount = 0;
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
            refresh: async () => { refreshCount += 1; return payload; },
            subscribe() {}, start() {},
            waitForInactive: () => { throw new Error('O navegador não deve administrar a fila de execução.'); }
        },
        fetch: async (url, request = {}) => {
            const call = { url, method: request.method || 'GET', body: request.body ? JSON.parse(request.body) : null };
            calls.push(call);
            if (options.fetch) return options.fetch(call, calls);
            return { ok: true, json: async () => ({ queued: true, job_id: 'job-' + calls.length }) };
        }
    };
    vm.createContext(context);
    vm.runInContext(syncSource, context, { filename: 'sync.js' });
    vm.runInContext(periodoSource.slice(periodoSource.indexOf('function setSyncButtons('), periodoSource.indexOf('// Funções utilitárias')), context, { filename: 'periodo-cache.js#monitor' });
    context.aguardar = async () => {};
    return { context, elements, calls, events, getRefreshCount: () => refreshCount, setPayload(value) { payload = value; } };
}

function freezeState(value) {
    if (value && typeof value === 'object') {
        Object.values(value).forEach(freezeState);
        Object.freeze(value);
    }
    return value;
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

    const allStores = Array.from({ length: 11 }, (_, index) => ({ nome: `Loja ${index}`, store_id: `id-${index}` }));
    for (const [status, detail] of [[503, 'connection_failed'], [503, 'server_setup_required'], [401, 'session_expired'],
        [503, { code: 'connection_failed', message: 'Falha técnica interna' }]]) {
        const savedQueue = freezeState({
            active: false,
            automacao: {
                habilitadas: 1, pendentes: 1,
                lojas: [{ loja: 'Loja anterior', store_id: 'anterior', status: 'blocked', pendentes: 1,
                    dias_concluidos: 3, dias_total: 15, ultimo_erro: 'Aviso anterior preservado' }],
                jobs: [{ id: 'job-anterior', store_id: 'anterior', status: 'blocked',
                    data_inicio: '2026-10-01', data_fim: '2026-10-15', dias_concluidos: ['2026-10-01'],
                    last_error: 'Aviso anterior preservado' }]
            }
        });
        const savedState = JSON.stringify(savedQueue);
        const globalFailure = setup({ payload: savedQueue,
            fetch: async () => ({ ok: false, status, json: async () => ({ detail }) }) });
        globalFailure.context.lojasDisponiveis = allStores;

        await globalFailure.context.executarSync();

        assert.equal(globalFailure.calls.length, 1, 'uma falha global não deve gerar onze POSTs nem tentar novamente sem autorização');
        assert.equal(globalFailure.calls[0].url, '/api/vendas/sync');
        assert.equal(globalFailure.calls[0].method, 'POST');
        assert.equal(globalFailure.getRefreshCount(), 1, 'não deve consultar novamente o monitor após a falha global de autorização');
        assert.equal(JSON.stringify(savedQueue), savedState, 'falha de autorização preserva integralmente o estado e os avisos anteriores');
        assert.equal(globalFailure.context.syncEmAndamento, false);
        assert.equal(globalFailure.context.syncController, null);
        assert.equal(globalFailure.elements.btnSync.disabled, false, 'uma nova tentativa deve poder ser iniciada pelo usuário');
        assert.equal(globalFailure.elements.btnCancel.disabled, false, 'as pendências anteriores continuam disponíveis para pausa explícita');
        assert.match(globalFailure.elements.status.innerHTML, /Registro do período interrompido/);
        assert.match(globalFailure.elements.status.innerHTML, /sessão do sistema/);
        assert.match(globalFailure.elements.status.innerHTML, /pendências e os registros já confirmados foram preservados/);
        const expectedAction = status === 401 ? /login novamente/
            : detail === 'server_setup_required' ? /Firebase.*configuração.*administrador/ : /Firebase.*Atualizar/;
        assert.match(globalFailure.elements.status.innerHTML, expectedAction);
        if (detail === 'server_setup_required') assert.doesNotMatch(globalFailure.elements.status.innerHTML, /Atualizar|login novamente/);
        assert.doesNotMatch(globalFailure.elements.status.innerHTML, /Bling|connection_failed|server_setup_required|session_expired|Loja 0:/);
    }

    for (const [status, detail] of [[502, 'connection_failed'], [401, 'connection_failed'],
        [503, 'session_expired'], [400, 'server_setup_required'], [503, 'connection_failed extra'],
        [503, 'CONNECTION_FAILED'], [503, { message: 'connection_failed' }]]) {
        const individualFailure = setup({ fetch: async () => ({ ok: false, status, json: async () => ({ detail }) }) });
        await individualFailure.context.executarSync();
        assert.equal(individualFailure.calls.length, 2, 'somente a combinação HTTP/código exata pode interromper as demais lojas');
        assert.match(individualFailure.elements.status.innerHTML, /Loja A:/);
        assert.match(individualFailure.elements.status.innerHTML, /Loja B:/);
    }

    let recoveredSession = false;
    const serverJobs = new Map();
    const jobKey = body => JSON.stringify([body.store_id, body.data_inicio, body.data_fim, body.forcar_resync]);
    const partialAuthFailure = setup({ fetch: async call => {
        if (!recoveredSession && call.body.store_id === 'id-1') {
            return { ok: false, status: 503, json: async () => ({ detail: 'connection_failed' }) };
        }
        const key = jobKey(call.body);
        if (!serverJobs.has(key)) serverJobs.set(key, { id: `persisted-${serverJobs.size}`, ...call.body });
        return { ok: true, status: 200, json: async () => ({ accepted: true, job_id: serverJobs.get(key).id }) };
    } });
    partialAuthFailure.context.lojasDisponiveis = allStores;
    await partialAuthFailure.context.executarSync();
    assert.deepEqual(partialAuthFailure.calls.map(call => call.body.store_id), ['id-0', 'id-1']);
    assert.equal(serverJobs.size, 1);
    const originalKey = jobKey(partialAuthFailure.calls[0].body);
    const originalJob = serverJobs.get(originalKey);
    assert.match(partialAuthFailure.elements.status.innerHTML, /registrado em 1 loja/);
    assert.match(partialAuthFailure.elements.status.innerHTML, /sessão do sistema no Firebase/);

    recoveredSession = true;
    await partialAuthFailure.context.executarSync();
    assert.equal(partialAuthFailure.calls.length, 13, 'a nova tentativa só ocorre no segundo clique e envia uma vez por loja');
    assert(partialAuthFailure.calls.every(call => call.url === '/api/vendas/sync' && call.method === 'POST'));
    assert.equal(serverJobs.size, 11, 'o pedido idêntico reutiliza o job pendente aceito antes da falha');
    assert.strictEqual(serverJobs.get(originalKey), originalJob, 'a retentativa mantém a identidade e o conteúdo do registro aceito');
    assert.deepEqual(partialAuthFailure.calls[0].body, partialAuthFailure.calls[2].body);
    assert.match(partialAuthFailure.elements.status.textContent, /registrado em 11 loja/);
    assert.equal(partialAuthFailure.context.syncEmAndamento, false);

    const mixedAuthFailure = setup({ fetch: async call => {
        if (call.body.store_id === 'id-0') {
            return { ok: false, status: 503, json: async () => ({ detail: 'Falha individual <Loja 0>' }) };
        }
        if (call.body.store_id === 'id-2') {
            return { ok: false, status: 503, json: async () => ({ detail: 'connection_failed' }) };
        }
        return { ok: true, status: 200, json: async () => ({ accepted: true, job_id: 'registro-confirmado' }) };
    } });
    mixedAuthFailure.context.lojasDisponiveis = allStores;
    await mixedAuthFailure.context.executarSync();
    assert.deepEqual(mixedAuthFailure.calls.map(call => call.body.store_id), ['id-0', 'id-1', 'id-2']);
    assert.match(mixedAuthFailure.elements.status.innerHTML, /registrado em 1 loja/);
    assert.match(mixedAuthFailure.elements.status.innerHTML, /sessão do sistema no Firebase/);
    assert.match(mixedAuthFailure.elements.status.innerHTML, /Loja 0: Falha individual &lt;Loja 0&gt;/);
    assert.doesNotMatch(mixedAuthFailure.elements.status.innerHTML, /<Loja 0>|Loja 2:|connection_failed/);

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
    assert.match(reopened.elements.syncPersistenteStatus.innerHTML, /1 tarefa pendente/);
    assert.match(reopened.elements.syncPersistenteStatus.innerHTML, /3\/15 dia/);
    assert.match(reopened.elements.syncPersistenteStatus.innerHTML, /&lt;falha&gt;/);
    assert.doesNotMatch(reopened.elements.syncPersistenteStatus.innerHTML, /<falha>|Atualizada/);
    assert.match(reopened.elements.syncPersistenteStatus.innerHTML, /Requer atenção/);
    assert.doesNotMatch(reopened.elements.syncPersistenteStatus.className, /\berror\b/, 'a pendência de uma loja não deve marcar todo o painel como erro');
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
    assert.doesNotMatch(reopened.elements.syncPersistenteStatus.innerHTML, /Atualizada/, 'status concluído com erro ou pendência não pode gerar sucesso');
    reopened.context.renderAutomacaoSyncVendas({ automacao: { lojas: [
        { loja: 'Conferência parcial', status: 'complete', pendentes: 2 },
        { loja: 'Sem histórico', status: 'idle', habilitado: true }
    ] } });
    assert.doesNotMatch(reopened.elements.syncPersistenteStatus.innerHTML, /Atualizada/, 'tarefas restantes ou automação habilitada não comprovam atualização');
    assert.doesNotMatch(reopened.elements.syncPersistenteStatus.innerHTML, /vendas-sync-attention/, 'o indicador de alerta só deve aparecer quando há uma pendência de integração');
    reopened.context.renderAutomacaoSyncVendas({ automacao: { lojas: [{ loja: 'Loja bloqueada', status: 'blocked' }] } });
    const attentionOnly = reopened.elements.syncPersistenteStatus.innerHTML;
    const attentionSummary = attentionOnly.match(/<summary\b[^>]*>([\s\S]*?)<\/summary>/)?.[1] || '';
    assert.match(attentionSummary, /vendas-sync-attention[^>]*>[^<]*1 alerta</,
        'um estado de atenção sem último erro também deve produzir um indicador visível');
    assert.match(attentionSummary, /Detalhes/);
    assert.doesNotMatch(attentionSummary, /atualizada|sincronizando|aguardando/,
        'uma lista contendo apenas lojas bloqueadas não pode informar atividade ou sucesso');
    assert.match(attentionOnly, /Verifique a integração da loja para continuar/);
    reopened.context.renderAutomacaoSyncVendas({ automacao: { lojas: [
        { loja: 'Coleta anterior bloqueada', status: 'blocked', aviso_conexao_verificado: true, ultimo_erro: null }
    ] } });
    const confirmedConnection = reopened.elements.syncPersistenteStatus.innerHTML;
    assert.doesNotMatch(confirmedConnection, /vendas-sync-attention|Verifique a integração|Atualizada/,
        'conexão confirmada retira o alerta antigo e preserva a pendência sem declarar coleta concluída');
    assert.match(confirmedConnection, /Pendências preservadas/);
    reopened.context.renderAutomacaoSyncVendas({ automacao: { lojas: [
        { loja: 'Falha nova', status: 'retry', aviso_conexao_verificado: true, ultimo_erro: 'Falha na coleta atual' }
    ] } });
    assert.match(reopened.elements.syncPersistenteStatus.innerHTML, /vendas-sync-attention/,
        'uma nova falha continua visível mesmo se a conexão havia sido confirmada');

    reopened.context.renderAutomacaoSyncVendas({ automacao: { lojas: [
        { loja: 'Loja pausada', status: 'paused', ultimo_erro: '<falha antiga>' },
        { loja: 'Loja com acesso pendente', status: 'paused', ultimo_erro: 'Reconecte o Bling da loja.' }
    ] } });
    const pausedWarnings = reopened.elements.syncPersistenteStatus.innerHTML;
    assert.match(pausedWarnings, /2 avisos anteriores/);
    assert.doesNotMatch(pausedWarnings, /2 alertas/);
    assert.match(pausedWarnings, /Última tentativa antes da pausa: &lt;falha antiga&gt;/);
    assert.match(pausedWarnings, /Última tentativa antes da pausa: Reconecte o Bling da loja/);
    assert.equal((pausedWarnings.match(/data-sync-state="paused">Pausada/g) || []).length, 2);

    for (const active of [false, true]) {
        const checkingPayload = {
            active, active_count: 0,
            progress: { etapa: 'Finalizado', percentual: 100, concluido: true, mensagem: 'Período anterior conferido.' },
            automacao: {
                verificando_conexoes: true, pendentes: 1, habilitadas: 0, jobs: [],
                lojas: [
                    { ...persisted.automacao.lojas[0], status: 'paused', ultimo_erro: '<falha anterior>' },
                    { loja: 'Loja com período anterior', status: 'complete', pendentes: 0 }
                ]
            }
        };
        const unchanged = JSON.stringify(checkingPayload);
        const checking = setup({ payload: checkingPayload });
        checking.context.window.JKVendasReadState = { get: () => ({ error: new Error('Falha de leitura anterior') }) };
        checking.context.exibirErroLeituraVendas = () => { throw new Error('O fallback não deve substituir a etapa de verificação.'); };
        checking.context.aplicarEstadoMonitorSyncVendas(checkingPayload);
        assert.equal(checking.context.syncEmAndamento, true);
        assert.equal(checking.elements.btnSync.disabled, true);
        assert.equal(checking.elements.btnCancel.disabled, false, 'a verificação global deve poder ser pausada sem coleta ativa');
        assert.match(checking.elements.status.innerHTML, /Verificando conexões Bling antes da coleta\.\.\./);
        assert.doesNotMatch(checking.elements.status.innerHTML, /Sincronizando|Aguardando|100%|concluído|conferido/i,
            'a verificação tem prioridade sobre progresso antigo e não comprova coleta');
        const checkingHtml = checking.elements.syncPersistenteStatus.innerHTML;
        const checkingSummary = checkingHtml.match(/<summary\b[^>]*>([\s\S]*?)<\/summary>/)?.[1] || '';
        assert.match(checkingSummary, /Verificando conexões Bling antes da coleta/);
        assert.doesNotMatch(checkingSummary, /sincronizando|atualizada|concluída|conferida/i);
        assert.match(checkingHtml, /&lt;falha anterior&gt;/, 'a verificação não apaga o erro histórico');
        assert.match(checkingHtml, /1 tarefa pendente/);
        assert.match(checkingHtml, /3\/15 dia/);
        assert.match(checkingHtml, /Último documento coletado: Pedido #123/);
        assert.equal(JSON.stringify(checkingPayload), unchanged, 'a apresentação não altera o estado persistido');

        checking.setPayload({
            active: false,
            progress: { etapa: 'Pausado', concluido: false, mensagem: 'Sincronização pausada; progresso preservado.' },
            automacao: { ...checkingPayload.automacao, verificando_conexoes: false, pendentes: 0 }
        });
        await checking.elements.btnCancel.listeners.click();
        assert.equal(checking.calls[0].url, '/api/vendas/sync/cancel');
        assert.equal(checking.elements.syncAutomatico.checked, false);
        assert.equal(checking.context.syncEmAndamento, false);
        assert.match(checking.elements.status.textContent, /pausada/);
    }

    const checkingWithoutStores = setup({ payload: {
        active: false, active_count: 0,
        automacao: { verificando_conexoes: true, lojas: [], jobs: [], pendentes: 0, habilitadas: 0 }
    } });
    checkingWithoutStores.context.aplicarEstadoMonitorSyncVendas(checkingWithoutStores.context.vendasSyncMonitor.getLastPayload());
    checkingWithoutStores.context.setSyncButtons(false);
    assert.equal(checkingWithoutStores.elements.btnSync.disabled, true);
    assert.equal(checkingWithoutStores.elements.btnCancel.disabled, false);
    assert.match(checkingWithoutStores.elements.status.innerHTML, /Verificando conexões Bling antes da coleta/);
    assert.match(checkingWithoutStores.elements.syncPersistenteStatus.innerHTML, /Verificando conexões Bling antes da coleta/);
    const verificationFailed = {
        active: false, progress: { etapa: 'Pendente', concluido: false, mensagem: 'Conexão Bling requer nova autorização.' },
        automacao: { verificando_conexoes: false, lojas: [{ loja: 'Loja A', status: 'retry', ultimo_erro: 'Reconecte a loja.' }], jobs: [], pendentes: 1, habilitadas: 0 }
    };
    checkingWithoutStores.setPayload(verificationFailed);
    checkingWithoutStores.context.aplicarEstadoMonitorSyncVendas(verificationFailed);
    assert.equal(checkingWithoutStores.elements.btnSync.disabled, false);
    assert.equal(checkingWithoutStores.context.syncEmAndamento, false);
    assert.match(checkingWithoutStores.elements.status.textContent, /requer nova autorização/);
    assert.doesNotMatch(checkingWithoutStores.elements.status.className, /loading/);
    assert.match(checkingWithoutStores.elements.syncPersistenteStatus.innerHTML, /Reconecte a loja/);

    const mixed = {
        active: true, progress: { percentual: 43, mensagem: 'Coletando página 2' }, logs: ['<registro técnico>'],
        automacao: { pendentes: 9, habilitadas: 6, lojas: [
            { loja: 'Loja A', status: 'running' },
            { loja: 'Loja B', status: 'running', ultimo_erro: 'Falha na tentativa anterior' },
            { loja: 'Loja C', status: 'queued', pendentes: 4 },
            { loja: 'Loja D', status: 'complete', pendentes: 0 },
            { loja: 'Loja E', status: 'retry', ultimo_erro: 'Sessão expirada. Faça login novamente.' },
            { loja: 'Loja F', status: 'paused' }
        ] }
    };
    reopened.context.aplicarEstadoMonitorSyncVendas(mixed);
    const mixedHtml = reopened.elements.syncPersistenteStatus.innerHTML;
    const mixedSummary = mixedHtml.match(/<summary\b[^>]*>([\s\S]*?)<\/summary>/)?.[1] || '';
    assert.match(mixedSummary, /2 sincronizando · 1 aguardando · 1 atualizada · 1 pausada/,
        'o resumo deve contar lojas, mesmo quando há várias tarefas por loja');
    assert.equal((mixedHtml.match(/<details\b/g) || []).length, 1, 'um único controle deve revelar todas as informações');
    assert.match(mixedHtml, /data-sync-details="sync"/);
    assert.match(mixedSummary, /vendas-sync-attention[^>]*>[^<]*2 alertas</,
        'o indicador deve contar também a loja em execução com erro da tentativa anterior');
    assert.match(mixedSummary, /vendas-sync-toggle[^>]*>Detalhes</);
    assert.doesNotMatch(mixedSummary, /Loja [A-F]|Sessão expirada|Falha na tentativa anterior|registro técnico|9 tarefas|requer atenção/,
        'o resumo compacto deve reservar mensagens e detalhes para a expansão');
    assert.match(mixedHtml, /Loja E.*Sessão expirada\. Faça login novamente/,
        'a orientação de login deve permanecer disponível nos detalhes');
    assert.match(mixedHtml, /data-sync-state="paused">Pausada/);
    assert.match(mixedHtml, /&lt;registro técnico&gt;/);
    assert.match(mixedHtml, /Coletando página 2/, 'a mensagem da etapa deve continuar disponível nos detalhes');
    assert.match(mixedHtml, /Loja B.*Falha na tentativa anterior/);
    assert.match(reopened.elements.status.innerHTML, /Sincronizando · 43%/);
    assert.doesNotMatch(reopened.elements.status.innerHTML, /registro técnico|Coletando página|Loja [A-F]|em andamento/);
    assert.match(reopened.elements.status.innerHTML, /role="progressbar"[^>]*aria-valuenow="43"/);
    reopened.context.renderProgresso({ etapa: 'Fila', percentual: 0 }, [], reopened.elements.status, [{ status: 'queued' }]);
    assert.match(reopened.elements.status.innerHTML, /Aguardando início/);
    assert.doesNotMatch(reopened.elements.status.innerHTML, />Sincronizando/, 'a fila ativa não comprova coleta em andamento');
    reopened.context.renderProgresso({ etapa: 'Erro', mensagem: '<Falha na consulta>' }, []);
    assert.match(reopened.elements.status.innerHTML.split('<details')[0], /vendas-sync-alert.*&lt;Falha na consulta&gt;/,
        'falhas da etapa devem continuar visíveis mesmo sem uma loja identificada');
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
