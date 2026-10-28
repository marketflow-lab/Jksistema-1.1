function mostrarCarregamentoSku() {
    if (!skuLoadingOverlay) return;
    skuLoadingOverlay.setAttribute('aria-hidden', 'false');
    skuLoadingOverlay.classList.add('active');
}

function ocultarCarregamentoSku() {
    if (!skuLoadingOverlay) return;
    skuLoadingOverlay.classList.remove('active');
    skuLoadingOverlay.setAttribute('aria-hidden', 'true');
}




window.addEventListener('pageshow', (event) => {
    ocultarCarregamentoSku();
    if (event.persisted && typeof carregarLojas === 'function') {
        void carregarLojas({ silencioso: true });
    }
});


tBody.addEventListener('click', (event) => {
    const tr = event.target && event.target.closest ? event.target.closest('tr[data-sku]') : null;
    if (!tr) return;
    const sku = tr.getAttribute('data-sku') || '';
    if (!sku) return;
    mostrarCarregamentoSku();
    const qs = new URLSearchParams();
    qs.append('sku', sku);
    const iniIso = getDataIniISO();
    const fimIso = getDataFimISO();
    if (iniIso) qs.append('data_inicio', iniIso);
    if (fimIso) qs.append('data_fim', fimIso);
    if (lojaSelecionada && lojaSelecionada !== '__todas') qs.append('loja', lojaSelecionada);
    if (unidadeNegocioSelect.value && unidadeNegocioSelect.value !== '__todos') {
        qs.append('unidade_negocio', unidadeNegocioSelect.value);
    }
    requestAnimationFrame(() => {
        window.location.href = `/vendas_sku.html?${qs.toString()}`;
    });
});

function atualizarPeriodoComRecarregamento(marcarSelecaoManual = false, preferencia = {}) {
    if (preferencia?.origem !== 'atalho' && typeof marcarPeriodoGraficoManual === 'function') {
        marcarPeriodoGraficoManual();
    }
    if (marcarSelecaoManual) {
        periodoSelecionadoPeloUsuario = true;
    }
    atualizarPeriodoTexto();
    if (!getDataIniISO() || !getDataFimISO()) {
        return;
    }
    salvarPreferenciaPeriodoData(preferencia);
    if (periodoApplyTimer) {
        clearTimeout(periodoApplyTimer);
    }
    periodoApplyTimer = setTimeout(() => {
        periodoApplyTimer = null;
        // O gráfico salvo pode aparecer enquanto a lista confere os dados no servidor.
        void carregarGrafico();
        void carregarVendas();
    }, 250);
}

function setSyncButtons(isSyncing) {
    const automacao = vendasSyncMonitor.getLastPayload()?.automacao;
    const verificandoConexoes = automacao?.verificando_conexoes === true;
    btnSync.disabled = isSyncing || verificandoConexoes;
    btnCancel.disabled = !isSyncing && !verificandoConexoes && !automacao?.habilitadas && !automacao?.pendentes;
}

function possuiColetaAgendadaVendas(data) {
    return !!data?.active || [...(data?.automacao?.jobs || []), ...(data?.automacao?.lojas || [])]
        .some(item => ['queued', 'running'].includes(String(item?.status || '').toLowerCase()));
}

const mensagemVerificacaoConexoesVendas = 'Verificando conexões Bling antes da coleta...';

function formatarMomentoSyncVendas(valor) {
    if (!valor) return '';
    const data = new Date(valor);
    return Number.isNaN(data.getTime()) ? String(valor) : data.toLocaleString('pt-BR');
}

function substituirConteudoSyncVendas(painel, html) {
    if (painel.innerHTML === html) return;
    const detalhes = Array.from(painel.querySelectorAll?.('details[data-sync-details]') || []);
    const abertos = new Set(detalhes.filter(item => item.open).map(item => item.getAttribute('data-sync-details')));
    const foco = detalhes.find(item => item.querySelector('summary') === document.activeElement)
        ?.getAttribute('data-sync-details');
    painel.innerHTML = html;
    for (const item of painel.querySelectorAll?.('details[data-sync-details]') || []) {
        const chave = item.getAttribute('data-sync-details');
        item.open = abertos.has(chave);
        if (chave === foco) item.querySelector('summary')?.focus({ preventScroll: true });
    }
}

function estadoSyncLojaVendas(loja) {
    const status = String(loja?.status || '').toLowerCase();
    if (['paused', 'pausado', 'cancelado'].includes(status)) return ['paused', 'Pausada'];
    if (['running', 'executando', 'em_execucao'].includes(status)) return ['running', 'Sincronizando'];
    if (loja?.ultimo_erro) return ['attention', 'Requer atenção'];
    if (['blocked', 'erro'].includes(status)) {
        return loja?.aviso_conexao_verificado
            ? ['pending', 'Pendências preservadas'] : ['attention', 'Requer atenção'];
    }
    if (['complete', 'concluido', 'finalizado'].includes(status) && !(Number(loja?.pendentes) > 0)) {
        return ['complete', 'Atualizada'];
    }
    if (['queued', 'pendente', 'retry', 'aguardando', 'complete', 'concluido', 'finalizado'].includes(status)
        || loja?.habilitado || Number(loja?.pendentes) > 0) return ['queued', 'Aguardando'];
    return ['idle', 'Sem sincronização'];
}

function logsSyncVendas(logs) {
    return (Array.isArray(logs) ? logs : []).slice(-10)
        .map(log => `<div>${escaparHtmlVendas(log)}</div>`).join('');
}

function renderAutomacaoSyncVendas(data, painel = document.getElementById('syncPersistenteStatus')) {
    if (!painel) return;
    const automacao = data?.automacao;
    const verificandoConexoes = automacao?.verificando_conexoes === true;
    const lojas = Array.isArray(automacao?.lojas) ? automacao.lojas : [];
    if (!lojas.length && !verificandoConexoes) {
        painel.innerHTML = '';
        return;
    }
    const pendentes = Math.max(0, Number(automacao?.pendentes || 0));
    const habilitadas = Math.max(0, Number(automacao?.habilitadas || 0));
    const estados = lojas.map(loja => ({ loja, estado: estadoSyncLojaVendas(loja) }));
    const contagens = [
        ['running', 'sincronizando'], ['queued', 'aguardando'], ['complete', 'atualizada', 'atualizadas'],
        ['paused', 'pausada', 'pausadas'], ['pending', 'com pendências'], ['idle', 'sem sincronização']
    ].map(([chave, singular, plural]) => {
        const total = estados.filter(item => item.estado[0] === chave).length;
        return total ? `${total} ${total === 1 ? singular : plural || singular}` : '';
    }).filter(Boolean).join(' · ');
    const lojasComAlerta = estados.filter(item => item.loja?.ultimo_erro || item.estado[0] === 'attention');
    const avisosPausados = lojasComAlerta.filter(item => item.estado[0] === 'paused').length;
    const alertasAtivos = lojasComAlerta.length - avisosPausados;
    const rotuloAlertas = [
        alertasAtivos ? `${alertasAtivos} ${alertasAtivos === 1 ? 'alerta' : 'alertas'}` : '',
        avisosPausados ? `${avisosPausados} ${avisosPausados === 1 ? 'aviso anterior' : 'avisos anteriores'}` : ''
    ].filter(Boolean).join(' · ');
    const indicadorAlertas = lojasComAlerta.length
        ? `<span class="vendas-sync-attention">⚠ ${rotuloAlertas}</span>` : '';
    const alertas = lojasComAlerta
        .map(({ loja, estado }) => `<div class="vendas-sync-alert"><strong>${escaparHtmlVendas(loja?.loja || 'Loja')}:</strong> ${estado[0] === 'paused' ? 'Última tentativa antes da pausa: ' : ''}${escaparHtmlVendas(loja?.ultimo_erro || 'Verifique a integração da loja para continuar.')}</div>`).join('');
    const linhas = estados.map(({ loja, estado: [chave, rotulo] }) => {
        const dia = loja?.dia_atual || loja?.proximo_dia;
        const data = dia && ['running', 'queued'].includes(chave)
            ? `<span class="vendas-sync-muted">${chave === 'running' ? 'Dia' : 'Próximo dia'}: ${escaparHtmlVendas(formatDate(dia))}</span>` : '';
        return `<div class="vendas-sync-store"><strong>${escaparHtmlVendas(loja?.loja || 'Loja')}</strong>${data}<span class="vendas-sync-label" data-sync-state="${chave}">${rotulo}</span></div>`;
    }).join('');
    const detalhesLojas = estados.map(({ loja, estado: [, rotulo] }) => {
        const periodo = loja?.data_inicio && loja?.data_fim
            ? ` · ${formatDate(loja.data_inicio)} a ${formatDate(loja.data_fim)}` : '';
        const dias = Number(loja?.dias_total || 0) > 0
            ? ` · ${Number(loja.dias_concluidos || 0)}/${Number(loja.dias_total)} dia(s) conferido(s)` : '';
        const ultima = loja?.ultima_conferencia
            ? `<br>Última conferência: ${escaparHtmlVendas(formatarMomentoSyncVendas(loja.ultima_conferencia))}` : '';
        const proxima = loja?.habilitado && loja?.proxima_execucao
            ? `<br>Próxima atualização: ${escaparHtmlVendas(formatarMomentoSyncVendas(loja.proxima_execucao))}` : '';
        const checkpoint = loja?.checkpoint || {};
        const tiposDocumento = { pedido: 'Pedido', nfe_entrada: 'NF-e de entrada', nfe_saida: 'NF-e de saída' };
        const documento = checkpoint.ultimo_id
            ? `${tiposDocumento[checkpoint.ultimo_tipo] || 'Documento'} #${checkpoint.ultimo_id}` : '';
        const quantidadeColetada = Math.max(0, Number(checkpoint.documentos_salvos || 0));
        const coletados = quantidadeColetada > 0
            ? ` · ${quantidadeColetada} documento(s) coletado(s) nesta etapa` : '';
        const coletaEm = checkpoint.atualizado_em
            ? ` · ${formatarMomentoSyncVendas(checkpoint.atualizado_em)}` : '';
        const coleta = documento
            ? `<br>Último documento coletado: ${escaparHtmlVendas(documento + coletados + coletaEm)}` : '';
        return `<div class="vendas-sync-technical-store"><strong>${escaparHtmlVendas(loja?.loja || 'Loja')}</strong> · ${escaparHtmlVendas(rotulo + periodo + dias)}${coleta}${ultima}${proxima}</div>`;
    }).join('');
    const logs = logsSyncVendas(data?.logs);
    const mensagemEtapa = verificandoConexoes ? mensagemVerificacaoConexoesVendas : data?.progress?.mensagem;
    const etapa = mensagemEtapa ? `<div>${escaparHtmlVendas(mensagemEtapa)}</div>` : '';
    const resumo = verificandoConexoes ? mensagemVerificacaoConexoesVendas
        : contagens ? `Lojas: ${contagens}` : 'Sincronização das lojas';
    painel.className = 'status-bar vendas-sync-status';
    substituirConteudoSyncVendas(painel, '<details class="vendas-sync-details vendas-sync-disclosure" data-sync-details="sync">'
        + `<summary><span class="vendas-sync-counts">${escaparHtmlVendas(resumo)}</span>${indicadorAlertas}<span class="vendas-sync-toggle">Detalhes</span></summary>`
        + `<div class="vendas-sync-content">${alertas}${linhas}`
        + '<strong class="vendas-sync-technical-title">Detalhes técnicos</strong>'
        + `<div class="vendas-sync-muted">${pendentes} ${pendentes === 1 ? 'tarefa pendente' : 'tarefas pendentes'} · Atualização automática em ${habilitadas} ${habilitadas === 1 ? 'loja' : 'lojas'}.</div>`
        + etapa + detalhesLojas + (logs ? `<div class="vendas-sync-logs"><strong>Registros recentes</strong>${logs}</div>` : '')
        + '<p class="vendas-sync-muted">O progresso é salvo. As atualizações continuam enquanto o serviço do sistema estiver funcionando.</p></div></details>');
}

function renderProgresso(p, logs, painel = statusEl, lojas = []) {
    if (!p || !painel) return;
    if (['conexões', 'conexoes'].includes(String(p.etapa || '').toLowerCase())) {
        painel.className = 'status-bar loading';
        substituirConteudoSyncVendas(painel, `<div class="vendas-sync-progress">${spinnerHtml}<strong>${mensagemVerificacaoConexoesVendas}</strong></div>`);
        return;
    }
    const confirmados = Number(p.dias_confirmados);
    const total = Number(p.dias_total);
    const possuiCobertura = Number.isFinite(confirmados) && Number.isFinite(total) && total > 0;
    const valor = possuiCobertura ? Math.floor(Math.max(0, confirmados) / total * 100) : Number(p.percentual || 0);
    const limite = possuiCobertura && p.concluido !== true ? 99 : 100;
    const percent = Number.isFinite(valor) ? Math.max(0, Math.min(limite, valor)) : 0;
    const listaLojas = Array.isArray(lojas) ? lojas : [];
    const lojasAtivas = listaLojas.filter(loja =>
        ['running', 'executando', 'em_execucao'].includes(String(loja?.status || '').toLowerCase()));
    const registros = logsSyncVendas(logs);
    const aguardando = (listaLojas.length > 0 && lojasAtivas.length === 0) || String(p.etapa || '').toLowerCase() === 'fila';
    const erro = String(p.etapa || '').toLowerCase() === 'erro'
        ? `<div class="vendas-sync-alert">${escaparHtmlVendas(p.mensagem || 'Falha na sincronização.')}</div>` : '';
    const detalhes = !listaLojas.length && (registros || p.mensagem)
        ? `<details class="vendas-sync-details" data-sync-details="logs"><summary>Ver detalhes técnicos</summary><div>${escaparHtmlVendas(p.mensagem || '')}</div><div class="vendas-sync-logs">${registros}</div></details>` : '';
    const diasAproveitados = possuiCobertura ? Math.max(0, Math.min(total, confirmados)) : Number(p.dias_aproveitados);
    const diasPendentes = possuiCobertura ? Math.max(0, total - diasAproveitados) : Number(p.dias_pendentes);
    const cobertura = Number.isFinite(diasAproveitados) && Number.isFinite(diasPendentes)
        ? `<div class="vendas-sync-muted">${Math.max(0, diasAproveitados)} dia(s) já concluído(s) · ${Math.max(0, diasPendentes)} dia(s) pendente(s)</div>` : '';
    painel.className = 'status-bar loading';
    substituirConteudoSyncVendas(painel, `<div class="vendas-sync-progress">${spinnerHtml}<strong>${aguardando ? 'Aguardando início' : 'Sincronizando'} · ${percent}%</strong>`
        + `<div class="vendas-sync-track" role="progressbar" aria-label="Progresso da sincronização" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><div class="vendas-sync-fill" style="width:${percent}%"></div></div></div>${cobertura}${erro}${detalhes}`);
}

let progressMonitorSubscribed = false;
let progressReloadInFlight = false;
let verificandoConexoesMonitor = false;

function criarAtualizadorConfirmadoVendas() {
    let context = null;
    let confirmed = new Map();
    let confirmedRevision = null;
    let timer = null;
    let pending = false;
    let running = false;
    let generation = 0;
    function scope() {
        return JSON.stringify([obterClientId(), getDataIniISO(), getDataFimISO(), lojaSelecionada || '__todas', (typeof unidadeNegocioSelect !== 'undefined' ? unidadeNegocioSelect?.value : '') || '__todos']);
    }
    function cancel() {
        generation += 1;
        if (timer) clearTimeout(timer);
        timer = null; pending = false; context = null; confirmed = new Map(); confirmedRevision = null;
    }
    function request() {
        const next = scope();
        if (context !== next) { cancel(); context = next; }
        pending = true;
        if (timer || running) return;
        const expected = context;
        const revision = generation;
        timer = setTimeout(async () => {
            timer = null;
            if (revision !== generation || expected !== scope()) { pending = false; return; }
            // Keep the pending refresh until the visible monitor observes the same revision again.
            if (document.visibilityState === 'hidden') return;
            pending = false; running = true; progressReloadInFlight = true;
            try {
                await Promise.allSettled([carregarVendas({ retornoRapido: true }), carregarGrafico()]);
            } finally {
                running = false; progressReloadInFlight = false;
                if (pending && context === scope()) request();
            }
        }, 5000);
    }
    function observe(payload) {
        const nextContext = scope();
        const baseline = nextContext !== context;
        if (baseline) { cancel(); context = nextContext; }
        const next = new Map();
        let advanced = false;
        const dataRevision = payload?.data_revision;
        if (Number.isSafeInteger(dataRevision) && dataRevision >= 0) {
            if (!baseline && confirmedRevision !== null && dataRevision !== confirmedRevision) advanced = true;
            confirmedRevision = dataRevision;
        }
        for (const job of payload?.automacao?.jobs || []) {
            if (lojaSelecionada && lojaSelecionada !== '__todas' && !mesmaLoja(job.loja, lojaSelecionada)) continue;
            if (job.data_fim < getDataIniISO() || job.data_inicio > getDataFimISO()) continue;
            const key = String(job.id || job.job_id || `${job.store_id}|${job.data_inicio}|${job.data_fim}`);
            const days = (Array.isArray(job.dias_concluidos) ? job.dias_concluidos : [])
                .filter(day => day >= getDataIniISO() && day <= getDataFimISO());
            const state = { days: new Set(days), complete: ['complete', 'concluido', 'finalizado'].includes(String(job.status || '').toLowerCase()) };
            next.set(key, state);
            const previous = confirmed.get(key);
            if (!baseline && (days.some(day => !previous?.days.has(day)) || (state.complete && !previous?.complete))) advanced = true;
        }
        // A collected document can still be staging. Only committed days or completed jobs advance the view.
        confirmed = next;
        if (advanced || pending) request();
    }
    return { observe, request, cancel };
}
const atualizadorConfirmadoVendas = criarAtualizadorConfirmadoVendas();
function cancelarAtualizacaoConfirmadaVendas() { atualizadorConfirmadoVendas.cancel(); }

function aplicarEstadoMonitorSyncVendas(data) {
    if (!data) return;
    atualizadorConfirmadoVendas.observe(data);
    renderAutomacaoSyncVendas(data);
    const automacao = data.automacao || {};
    const progresso = data.progress ? { ...data.progress } : null;
    if (progresso && Number.isFinite(automacao.dias_confirmados) && Number.isFinite(automacao.dias_total)) {
        progresso.dias_confirmados = automacao.dias_confirmados;
        progresso.dias_total = automacao.dias_total;
        progresso.dias_pendentes = Math.max(0, automacao.dias_total - automacao.dias_confirmados);
        if (Number(automacao.pendentes) > 0) progresso.concluido = false;
    }
    const etapa = String(progresso?.etapa || '').toLowerCase();
    const loteLocalAtivo = !!syncController;
    const estavaVerificandoConexoes = verificandoConexoesMonitor;
    verificandoConexoesMonitor = data.automacao?.verificando_conexoes === true;

    if (verificandoConexoesMonitor) {
        syncEmAndamento = true;
        setSyncButtons(true);
        renderProgresso({ etapa: 'Conexões', concluido: false }, data.logs, statusEl);
        return;
    }

    if (possuiColetaAgendadaVendas(data)) {
        syncEmAndamento = true;
        setSyncButtons(true);
        const readError = window.JKVendasReadState?.get('resumo')?.error;
        if (readError) {
            exibirErroLeituraVendas(readError);
            return;
        }
        if (progresso) {
            renderProgresso(progresso, data.logs, statusEl, data.automacao?.lojas || []);
        } else {
            statusEl.className = 'status-bar loading';
            statusEl.innerHTML = `${spinnerHtml}<strong>Aguardando resposta do servidor...</strong>`;
        }
        return;
    }

    if (!loteLocalAtivo) {
        syncEmAndamento = false;
        setSyncButtons(false);
    }
    if (etapa === 'erro' && !loteLocalAtivo) {
        statusEl.className = 'status-bar error';
        statusEl.textContent = `Erro ao sincronizar: ${corrigirTextoVendas(progresso?.mensagem || 'Falha na sincronização.')}`;
    } else if (etapa === 'cancelamento' && !loteLocalAtivo) {
        statusEl.className = 'status-bar';
        statusEl.textContent = corrigirTextoVendas(progresso?.mensagem || 'Sincronização pausada.');
    } else if (estavaVerificandoConexoes && !loteLocalAtivo) {
        statusEl.className = 'status-bar';
        statusEl.textContent = corrigirTextoVendas(progresso?.mensagem || 'Verificação encerrada. Acompanhe as pendências das lojas.');
    }
}

function iniciarMonitoramentoProgresso() {
    if (!progressMonitorSubscribed) {
        vendasSyncMonitor.subscribe(aplicarEstadoMonitorSyncVendas);
        progressMonitorSubscribed = true;
    }
    vendasSyncMonitor.start();
    void vendasSyncMonitor.refresh();
}

function pararMonitoramentoProgresso() {
    progressTimer = null;
    progressPollInFlight = false;
}

async function verificarSyncEmAndamentoNaEntrada() {
    clientId = clientId || obterClientId();
    if (!clientId) return false;
    iniciarMonitoramentoProgresso();
    const data = await vendasSyncMonitor.refresh();
    aplicarEstadoMonitorSyncVendas(data);
    return !!data?.active;
}

window.addEventListener('jk:vendas-sync-finished', event => {
    const payload = event?.detail?.payload || {};
    const etapa = String(payload?.progress?.etapa || '').toLowerCase();
    if (syncController || progressReloadInFlight || etapa === 'erro' || etapa === 'cancelamento') return;
    atualizadorConfirmadoVendas.request();
});

// Funções utilitárias
function formatDate(dateStr) {
    if (!dateStr) return '';
    if (/^\d{2}\/\d{2}\/\d{4}$/.test(dateStr)) return dateStr;
    if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
        const [year, month, day] = dateStr.split('-');
        return day + '/' + month + '/' + year;
    }
    return dateStr;
}

function toIsoDateFromInput(value) {
    const v = String(value || '').trim();
    if (!v) return '';
    if (/^\d{4}-\d{2}-\d{2}$/.test(v)) return v;
    if (/^\d{2}\/\d{2}\/\d{4}$/.test(v)) {
        const [d, m, y] = v.split('/');
        return `${y}-${m}-${d}`;
    }
    return '';
}

function normalizarChaveFiltro(valor, removerPrefixoUnidade = false) {
    let txt = String(valor || '').trim().toLowerCase();
    if (!txt) return '';
    txt = txt.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    if (removerPrefixoUnidade) {
        txt = txt.replace(/^\s*unidade\s+/i, '');
    }
    txt = txt.replace(/\s+/g, ' ').trim();
    return txt;
}

function mesmaLoja(a, b) {
    return normalizarChaveFiltro(a) === normalizarChaveFiltro(b);
}

function mesmaUnidade(a, b) {
    return normalizarChaveFiltro(a, true) === normalizarChaveFiltro(b, true);
}

function getDataIniISO() {
    return toIsoDateFromInput(dataIni.value);
}

function getDataFimISO() {
    return toIsoDateFromInput(dataFim.value);
}

function getHojeISO() {
    return new Date().toISOString().split('T')[0];
}

function chavePreferenciaPeriodoData() {
    // Esta preferência é lida antes de sync.js definir obterClientId().
    const cid = clientId || JSON.parse(localStorage.getItem('user_data') || 'null')?.client_id || 'anon';
    return `vendas_periodo_pref_${cid}`;
}

function salvarPreferenciaPeriodoData(preferencia = {}) {
    const iniIso = getDataIniISO();
    const fimIso = getDataFimISO();
    if (!iniIso || !fimIso) return;

    const origem = preferencia?.origem === 'atalho' ? 'atalho' : 'manual';
    const periodo = origem === 'atalho' ? String(preferencia?.periodo || '').trim() : '';

    try {
        localStorage.setItem(chavePreferenciaPeriodoData(), JSON.stringify({
            data_inicio: iniIso,
            data_fim: fimIso,
            origem,
            periodo,
            versao: 2
        }));
    } catch (_e) {
        // Ignora erro de localStorage.
    }
}

function carregarPreferenciaPeriodoData() {
    try {
        const raw = localStorage.getItem(chavePreferenciaPeriodoData());
        if (!raw) return null;
        const pref = JSON.parse(raw);
        let inicio = pref?.data_inicio;
        let fim = pref?.data_fim;
        if (!inicio || !fim) return null;

        const hojeIso = getHojeISO();
        if (fim > hojeIso) fim = hojeIso;
        if (inicio > fim) inicio = fim;

        return {
            inicio,
            fim,
            origem: String(pref?.origem || '').trim(),
            periodo: String(pref?.periodo || '').trim(),
            versao: Number(pref?.versao || 0)
        };
    } catch (_e) {
        return null;
    }
}

function limitarDataFinalAoHoje() {
    const fimIso = getDataFimISO();
    if (!fimIso) return false;

    const hojeIso = getHojeISO();
    if (fimIso <= hojeIso) return false;

    const iniIso = getDataIniISO() || hojeIso;
    setDataRangeISO(iniIso, hojeIso);
    return true;
}

function garantirOrdemPeriodo() {
    const iniIso = getDataIniISO();
    const fimIso = getDataFimISO();
    if (!iniIso || !fimIso) return false;
    if (fimIso >= iniIso) return false;

    setDataRangeISO(iniIso, iniIso);
    return true;
}

function setDataRangeISO(inicioIso, fimIso) {
    const hojeIso = getHojeISO();
    const fimSeguroIso = fimIso && fimIso > hojeIso ? hojeIso : fimIso;
    const inicioFmt = formatDate(inicioIso);
    const fimFmt = formatDate(fimSeguroIso);

    if (dateRangePicker) {
        dateRangePicker.setDate(inicioIso, false);
    }

    if (dateFimPicker) {
        if (fimSeguroIso) {
            dateFimPicker.setDate(fimSeguroIso, false);
        } else {
            dateFimPicker.clear(false);
        }
    }

    // Garante que os dois inputs fiquem sempre separados (início/fim), sem intervalo combinado no primeiro campo.
    dataIni.value = inicioFmt;
    dataFim.value = fimFmt;

    if (dateRangePicker) {
        setTimeout(() => {
            dataIni.value = inicioFmt;
            dataFim.value = fimFmt;
        }, 0);
    }
}

function atualizarPeriodoTexto() {
    const iniIso = getDataIniISO();
    const fimIso = getDataFimISO();
    if (iniIso && fimIso) {
        periodoTexto.textContent = 'Per\u00edodo: ' + formatDate(iniIso) + ' at\u00e9 ' + formatDate(fimIso);
    } else {
        periodoTexto.textContent = 'Per\u00edodo: não selecionado';
    }
}

function chaveCacheTelaVendas() {
    return JSON.stringify(['/api/vendas/resumo', getDataIniISO(), getDataFimISO(),
        lojaSelecionada || '__todas', unidadeNegocioSelect.value || '__todos']);
}

async function salvarCacheTelaVendas(expectedScope = window.JKVendasReadState?.scope('resumo'), contexto = contextoDadosVendas) {
    try {
        if (clientId !== obterClientId() || expectedScope !== window.JKVendasReadState?.scope('resumo')) return;
        if (dadosVendasDeCache || !window.JKVendasCache?.dadosVigentes(contexto)) return;
        if (window.JKVendasReadState && !window.JKVendasReadState.canCache()) return;
        const dadosParaCache = Array.isArray(dados) ? dados : [];
        const devolucoesParaCache = Array.isArray(devolucaoItens) ? devolucaoItens : [];

        const payload = {
            ts: Date.now(),
            client_id: obterClientId(),
            data_inicio: getDataIniISO(),
            data_fim: getDataFimISO(),
            lojaSelecionada,
            unidadeSelecionada: unidadeNegocioSelect.value || '__todos',
            filtroTexto: filtroTexto.value || '',
            dadosModoResumo,
            resumoTotaisVendas,
            vendasResumoMeta,
            dados: dadosParaCache.map(r => ({
                ...r,
                data: r.data || '',
                data_inicio: r.data_inicio || '',
                data_fim: r.data_fim || '',
                __resumo: r.__resumo === true,
                loja_conta: r.loja_conta || '',
                unidade_negocio: r.unidade_negocio || '',
                sku: r.sku || '',
                produto: r.produto || '',
                quantidade: Number(r.quantidade || 0),
                pedidos: Number(r.pedidos || 0),
                itens: Number(r.itens || r.quantidade || 0),
                devolucoes: Number(r.devolucoes || 0),
                itensDevolucao: Number(r.itensDevolucao || 0),
                valorDevolucao: Number(r.valorDevolucao || 0),
                valor: Number(r.valor || 0)
            })),
            devolucaoItens: devolucoesParaCache.map(d => ({
                ...d,
                sku: d.sku || '',
                descricao: d.descricao || '',
                quantidade: Number(d.quantidade || 0),
                valor_total: Number(d.valor_total || 0),
                loja_conta: d.loja_conta || '',
                unidade_negocio: d.unidade_negocio || '',
                unidade_negocio_virtual: d.unidade_negocio_virtual || ''
            })),
            mapeamentoUnidades
        };
        return await window.JKVendasCache.salvar(chaveCacheTelaVendas(), payload, contexto);
    } catch (_e) {
        // Cache de tela é best-effort.
    }
}

function agendarSalvarCacheTelaVendas() {
    const expectedScope = window.JKVendasReadState?.scope('resumo');
    const contexto = contextoDadosVendas;
    if (cacheSaveTimer) {
        clearTimeout(cacheSaveTimer);
    }
    cacheSaveTimer = setTimeout(() => {
        cacheSaveTimer = null;
        agendarSegundoPlano(() => salvarCacheTelaVendas(expectedScope, contexto), 60);
    }, 180);
}

async function restaurarCacheTelaVendasSeValido(opcoes = {}) {
    const contexto = opcoes.contexto || window.JKVendasCache?.contexto();
    const scope = window.JKVendasReadState?.scope('resumo');
    const vigente = () => (!window.JKVendasCache || window.JKVendasCache.dadosVigentes(contexto))
        && scope === window.JKVendasReadState?.scope('resumo')
        && (!opcoes.leituraAtual || opcoes.leituraAtual());
    try {
        const salvo = await window.JKVendasCache?.ler(chaveCacheTelaVendas(), contexto);
        if (!salvo || !vigente()) return false;
        const payload = salvo.dados;
        if (payload?.client_id !== obterClientId()) return false;

        const iniAtual = getDataIniISO() || '';
        const fimAtual = getDataFimISO() || '';
        if ((payload.data_inicio || '') !== iniAtual || (payload.data_fim || '') !== fimAtual) {
            return false;
        }

        if (!Array.isArray(payload.dados)) return false;

        clientId = obterClientId();
        dadosVendasDeCache = true;
        contextoDadosVendas = contexto;
        dadosModoResumo = payload.dadosModoResumo === true;
        resumoTotaisVendas = payload.resumoTotaisVendas || null;
        vendasResumoMeta = payload.vendasResumoMeta || null;
        dados = payload.dados;
        devolucaoItens = Array.isArray(payload.devolucaoItens) ? payload.devolucaoItens : [];
        mapeamentoUnidades = payload.mapeamentoUnidades || {};

        if (opcoes.restaurarFiltro && payload.filtroTexto != null) {
            filtroTexto.value = String(payload.filtroTexto);
        }

        atualizarUnidadesNegocio(dados, mapeamentoUnidades || {});
        if (payload.unidadeSelecionada) {
            unidadeNegocioSelect.value = payload.unidadeSelecionada;
        }
        window.JKVendasReadState?.captureData();
        renderBotoesLojas(lojasDisponiveis || []);
        await filtrar();
        if (!vigente()) return false;
        if (typeof atualizarObservacoesGraficoVigente === 'function') atualizarObservacoesGraficoVigente();
        window.JKVendasCache?.estado('Lista', 'atualizando', salvo.salvoEm);
        if (!syncEmAndamento) {
            statusEl.className = 'status-bar';
            statusEl.textContent = '';
        }
        return true;
    } catch (_e) {
        return false;
    }
}

function preencherMeses() {
    const hoje = new Date();
    for (let i = 0; i < 12; i++) {
        const data = new Date(hoje.getFullYear(), hoje.getMonth() - i, 1);
        const mes = (data.getMonth() + 1).toString().padStart(2, '0');
        const ano = data.getFullYear();
        const opcao = document.createElement('option');
        opcao.value = ano + '-' + mes;
        const meses = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 
                      'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];
        opcao.textContent = meses[data.getMonth()] + ' de ' + ano;
        mesAno.appendChild(opcao);
    }
}

function selecionarMes(mesAnoValue) {
    if (!mesAnoValue) return;
    const [ano, mes] = mesAnoValue.split('-');
    const data = new Date(ano, parseInt(mes) - 1, 1);
    const ultimoDia = new Date(ano, parseInt(mes), 0);

    const ini = ano + '-' + mes + '-01';
    const fim = ano + '-' + mes + '-' + ultimoDia.getDate().toString().padStart(2, '0');

    setDataRangeISO(ini, fim);
    atualizarPeriodoComRecarregamento(true);
}

function sincronizarPeriodoTopo(inicioIso, fimIso) {
    if (!inicioIso || !fimIso) return;
    setDataRangeISO(inicioIso, fimIso);

    // Se o intervalo for o mês fechado, reflete no seletor de mês.
    const mIni = inicioIso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    const mFim = fimIso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    let valorMes = '';
    if (mIni && mFim && mIni[1] === mFim[1] && mIni[2] === mFim[2] && mIni[3] === '01') {
        const ultimoDiaMes = new Date(Number(mIni[1]), Number(mIni[2]), 0).getDate();
        if (Number(mFim[3]) === ultimoDiaMes) {
            valorMes = `${mIni[1]}-${mIni[2]}`;
        }
    }
    mesAno.value = valorMes;
}

function initDateRangePicker() {
    if (typeof flatpickr !== 'function') {
        return;
    }

    if (flatpickr?.localize && flatpickr?.l10ns?.pt) {
        flatpickr.localize(flatpickr.l10ns.pt);
    }

    dateRangePicker = flatpickr(dataIni, {
        dateFormat: 'd/m/Y',
        disableMobile: true,
        allowInput: true,
        clickOpens: true,
        showMonths: 1,
        maxDate: 'today',
        locale: flatpickr.l10ns.pt,
        onChange: (selectedDates) => {
            if (selectedDates.length === 1) {
                dataFim.value = '';
                if (dateFimPicker) {
                    dateFimPicker.clear(false);
                    dateFimPicker.open();
                } else {
                    const secondInput = document.getElementById('dataFim');
                    if (secondInput) secondInput.focus();
                }
            }
        },
        onValueUpdate: () => {
            atualizarPeriodoTexto();
        },
        onClose: () => {
            limitarDataFinalAoHoje();
            if (getDataIniISO() && getDataFimISO()) {
                atualizarPeriodoComRecarregamento(true);
            }
        }
    });

    dateFimPicker = flatpickr(dataFim, {
        dateFormat: 'd/m/Y',
        disableMobile: true,
        allowInput: true,
        clickOpens: true,
        showMonths: 1,
        maxDate: 'today',
        locale: flatpickr.l10ns.pt,
        onChange: (selectedDates, _dateStr, instance) => {
            if (selectedDates.length === 1 && instance?.isOpen) {
                instance.close();
            }
        },
        onValueUpdate: () => {
            limitarDataFinalAoHoje();
            garantirOrdemPeriodo();
            atualizarPeriodoTexto();
        },
        onClose: () => {
            limitarDataFinalAoHoje();
            garantirOrdemPeriodo();
            if (getDataIniISO() && getDataFimISO()) {
                atualizarPeriodoComRecarregamento(true);
            }
        }
    });
}

function fecharCalendariosData() {
    if (dateRangePicker?.isOpen) {
        dateRangePicker.close();
    }
    if (dateFimPicker?.isOpen) {
        dateFimPicker.close();
    }
    dataIni.blur();
    dataFim.blur();
}

// Ler parâmetros de URL e preencher datas
const params_url = new URLSearchParams(window.location.search);
if (params_url.has('loja')) {
    lojaSelecionada = params_url.get('loja') || '__todas';
}
const unidadeUrl = params_url.get('unidade_negocio');
if (unidadeUrl && unidadeUrl !== '__todos') {
    unidadeNegocioSelect.add(new Option(unidadeUrl, unidadeUrl));
    unidadeNegocioSelect.value = unidadeUrl;
}
const data_inicio_param = params_url.get('data_inicio');
const data_fim_param = params_url.get('data_fim');
const retornoDoSku = params_url.get('from_sku') === '1';
const periodoPrefSalvo = carregarPreferenciaPeriodoData();

if (data_inicio_param && data_fim_param) {
    setDataRangeISO(data_inicio_param, data_fim_param);
} else if (periodoPrefSalvo?.inicio && periodoPrefSalvo?.fim) {
    setDataRangeISO(periodoPrefSalvo.inicio, periodoPrefSalvo.fim);
} else {
    dataIni.value = '';
    dataFim.value = '';
}

initDateRangePicker();

dataFim.addEventListener('change', () => {
    if (limitarDataFinalAoHoje()) {
        atualizarPeriodoTexto();
    }
});
dataFim.addEventListener('blur', () => {
    if (limitarDataFinalAoHoje()) {
        atualizarPeriodoTexto();
    }
});

preencherMeses();
atualizarPeriodoTexto();
mesAno.addEventListener('change', (e) => selecionarMes(e.target.value));
