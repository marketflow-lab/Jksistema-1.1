function getApiAutoPrefs() {
    try {
        const parsed = JSON.parse(localStorage.getItem(getApiAutoPrefsKey()) || '{}');
        const intervalUnit = API_AUTO_INTERVAL_UNITS[parsed.intervalUnit] ? parsed.intervalUnit : 'minutes';
        const unitConfig = API_AUTO_INTERVAL_UNITS[intervalUnit];
        const fallbackValue = intervalUnit === 'minutes'
            ? (parsed.intervalMinutes ?? API_AUTO_DEFAULT_INTERVAL_MIN)
            : (parsed.intervalValue ?? API_AUTO_DEFAULT_INTERVAL_MIN / unitConfig.factor);
        const rawValue = Number(String(parsed.intervalValue ?? fallbackValue).replace(',', '.'));
        const intervalValue = Number.isFinite(rawValue)
            ? Math.max(1, Math.min(unitConfig.max, Math.round(rawValue)))
            : Math.max(1, Math.round(API_AUTO_DEFAULT_INTERVAL_MIN / unitConfig.factor));
        const intervalMinutes = Math.max(API_AUTO_MIN_INTERVAL_MIN, Math.round(intervalValue * unitConfig.factor));
        const rawNextRunAt = Number(parsed.nextRunAt || 0);
        const nextRunAt = Number.isFinite(rawNextRunAt) && rawNextRunAt > 0 ? rawNextRunAt : 0;
        return {
            enabled: parsed.enabled === true,
            approvalRequired: parsed.approvalRequired !== false,
            intervalUnit,
            intervalValue,
            intervalMinutes,
            nextRunAt,
            promoBSelectedIds: Array.isArray(parsed.promoBSelectedIds) ? parsed.promoBSelectedIds.map((id) => String(id || '').trim()).filter(Boolean) : [],
        };
    } catch (_e) {
        return {
            enabled: false,
            approvalRequired: true,
            intervalUnit: 'minutes',
            intervalValue: API_AUTO_DEFAULT_INTERVAL_MIN,
            intervalMinutes: API_AUTO_DEFAULT_INTERVAL_MIN,
            nextRunAt: 0,
            promoBSelectedIds: [],
        };
    }
}

function getApiAutoIntervalFromControls() {
    const intervalEl = document.getElementById('apiAutoIntervalMinutes');
    const unitEl = document.getElementById('apiAutoIntervalUnit');
    const intervalUnit = API_AUTO_INTERVAL_UNITS[unitEl?.value] ? unitEl.value : 'minutes';
    const unitConfig = API_AUTO_INTERVAL_UNITS[intervalUnit];
    const defaultValue = unitConfig.factor === 1 ? API_AUTO_DEFAULT_INTERVAL_MIN : 1;
    const rawValue = Number(String(intervalEl?.value || defaultValue).replace(',', '.'));
    const intervalValue = Number.isFinite(rawValue)
        ? Math.max(1, Math.min(unitConfig.max, Math.round(rawValue)))
        : 1;
    const intervalMinutes = Math.max(API_AUTO_MIN_INTERVAL_MIN, Math.round(intervalValue * unitConfig.factor));
    if (intervalEl) {
        intervalEl.min = '1';
        intervalEl.max = String(unitConfig.max);
        intervalEl.value = String(intervalValue);
    }
    if (unitEl) unitEl.value = intervalUnit;
    return { intervalUnit, intervalValue, intervalMinutes };
}

function formatApiAutoInterval(intervalValue, intervalUnit) {
    const unitConfig = API_AUTO_INTERVAL_UNITS[intervalUnit] || API_AUTO_INTERVAL_UNITS.minutes;
    return `${intervalValue} ${unitConfig.label}`;
}

function aplicarApiAutoIntervalPrefs(prefs) {
    const intervalEl = document.getElementById('apiAutoIntervalMinutes');
    const unitEl = document.getElementById('apiAutoIntervalUnit');
    const intervalUnit = API_AUTO_INTERVAL_UNITS[prefs.intervalUnit] ? prefs.intervalUnit : 'minutes';
    const unitConfig = API_AUTO_INTERVAL_UNITS[intervalUnit];
    const intervalValue = Math.max(1, Math.min(unitConfig.max, Math.round(Number(prefs.intervalValue) || 1)));
    if (unitEl) {
        unitEl.value = intervalUnit;
        unitEl.dataset.previousUnit = intervalUnit;
    }
    if (intervalEl) {
        intervalEl.min = '1';
        intervalEl.max = String(unitConfig.max);
        intervalEl.step = '1';
        intervalEl.value = String(intervalValue);
    }
}

function saveApiAutoPrefs() {
    const enabledEl = document.getElementById('apiAutoWorkEnabled');
    const approvalEl = document.getElementById('apiAutoApprovalRequired');
    const interval = getApiAutoIntervalFromControls();
    try {
        const atual = JSON.parse(localStorage.getItem(getApiAutoPrefsKey()) || '{}');
        const rawNextRunAt = Number(atual.nextRunAt || 0);
        const nextRunAt = enabledEl?.checked && Number.isFinite(rawNextRunAt) && rawNextRunAt > 0 ? rawNextRunAt : 0;
        localStorage.setItem(getApiAutoPrefsKey(), JSON.stringify({
            enabled: !!enabledEl?.checked,
            approvalRequired: approvalEl ? !!approvalEl.checked : true,
            intervalUnit: interval.intervalUnit,
            intervalValue: interval.intervalValue,
            intervalMinutes: interval.intervalMinutes,
            nextRunAt,
            promoBSelectedIds: getApiPromoBSelections().map((promo) => promo.value),
        }));
    } catch (_e) {}
}

function persistApiAutoPrefsObject(prefs) {
    try {
        localStorage.setItem(getApiAutoPrefsKey(), JSON.stringify({
            enabled: !!prefs.enabled,
            approvalRequired: prefs.approvalRequired !== false,
            intervalUnit: prefs.intervalUnit || 'minutes',
            intervalValue: Number(prefs.intervalValue || API_AUTO_DEFAULT_INTERVAL_MIN),
            intervalMinutes: Number(prefs.intervalMinutes || API_AUTO_DEFAULT_INTERVAL_MIN),
            nextRunAt: Number(prefs.nextRunAt || 0),
            promoBSelectedIds: Array.isArray(prefs.promoBSelectedIds) ? prefs.promoBSelectedIds : getApiPromoBSelections().map((promo) => promo.value),
        }));
    } catch (_e) {}
}

function salvarApiAutoNextRunAt(nextRunAt) {
    try {
        const atual = JSON.parse(localStorage.getItem(getApiAutoPrefsKey()) || '{}');
        const valor = Number(nextRunAt || 0);
        atual.nextRunAt = Number.isFinite(valor) && valor > 0 ? valor : 0;
        localStorage.setItem(getApiAutoPrefsKey(), JSON.stringify(atual));
    } catch (_e) {}
}

function apiAutoPrefsFromServerConfig(config) {
    const cfg = config && typeof config === 'object' ? config : {};
    const intervalUnit = API_AUTO_INTERVAL_UNITS[cfg.interval_unit] ? cfg.interval_unit : 'minutes';
    const unitConfig = API_AUTO_INTERVAL_UNITS[intervalUnit];
    const intervalValue = Math.max(1, Math.min(unitConfig.max, Math.round(Number(cfg.interval_value || 1))));
    const intervalMinutes = Math.max(API_AUTO_MIN_INTERVAL_MIN, Math.round(Number(cfg.interval_minutes || intervalValue * unitConfig.factor)));
    const nextRunAtSec = Number(cfg.next_run_at || 0);
    return {
        enabled: cfg.enabled === true,
        approvalRequired: cfg.approval_required !== false,
        intervalUnit,
        intervalValue,
        intervalMinutes,
        nextRunAt: Number.isFinite(nextRunAtSec) && nextRunAtSec > 0 ? nextRunAtSec * 1000 : 0,
        lastJobId: String(cfg.last_job_id || ''),
        lastJobStatus: String(cfg.last_job_status || ''),
        lastJobMessage: String(cfg.last_job_message || ''),
        lastError: String(cfg.last_error || ''),
        updatedAt: Number(cfg.updated_at || 0),
        due: cfg.due === true,
        promoBSelectedIds: extractApiPromoBIdsFromMeta(cfg.promocoes_b_meta),
    };
}

function montarPayloadApiAutoServidor() {
    const enabledEl = document.getElementById('apiAutoWorkEnabled');
    const approvalEl = document.getElementById('apiAutoApprovalRequired');
    const loja = document.getElementById('apiLojaSelect')?.value || '';
    const selectPromoA = document.getElementById('apiPromoASelect');
    const interval = getApiAutoIntervalFromControls();
    const prefs = getApiAutoPrefs();
    return {
        enabled: !!enabledEl?.checked,
        approval_required: approvalEl ? !!approvalEl.checked : true,
        interval_unit: interval.intervalUnit,
        interval_value: interval.intervalValue,
        interval_minutes: interval.intervalMinutes,
        next_run_at: prefs.nextRunAt ? Math.floor(Number(prefs.nextRunAt) / 1000) : null,
        loja,
        promocao_a_id: selectPromoA?.value || '',
        promocao_a_type: selectPromoA?.selectedOptions?.[0]?.dataset?.promoType || '',
        margem_minima: Number(document.getElementById('apiMargemMinima')?.value || 15),
        promocoes_b_meta: getApiPromoBSelections().map((promo) => ({
            promo_b_id: promo.value,
            promo_b_type: promo.promoType || '',
            promo_texto: promo.text || promo.matchName || promo.value,
            active_count: promo.activeCount,
            eligible_count: promo.eligibleCount,
        })),
    };
}

function aplicarApiAutoPrefsTela(prefs) {
    const enabledEl = document.getElementById('apiAutoWorkEnabled');
    const approvalEl = document.getElementById('apiAutoApprovalRequired');
    if (enabledEl) enabledEl.checked = !!prefs.enabled;
    if (approvalEl) approvalEl.checked = prefs.approvalRequired !== false;
    aplicarApiAutoIntervalPrefs(prefs);
    if (Array.isArray(prefs.promoBSelectedIds) && prefs.promoBSelectedIds.length) {
        apiPromoBPendingSelectedIds = new Set(prefs.promoBSelectedIds.map((id) => String(id || '').trim()).filter(Boolean));
        if (Array.isArray(apiPromoBCampaigns) && apiPromoBCampaigns.length) {
            apiPromoBSelectionReady = false;
            renderApiPromoBButtons(apiPromoBCampaigns);
        }
    }
    persistApiAutoPrefsObject(prefs);
    if (prefs.enabled) {
        apiAutoNextRunAt = Number(prefs.nextRunAt || 0);
        salvarApiAutoNextRunAt(apiAutoNextRunAt);
        if (apiAutoNextRunAt > Date.now()) {
            iniciarTimerProximaVerificacaoPromo();
        } else {
            pararTimerProximaVerificacaoPromo();
            atualizarStatusAutomacaoPromo('Automatico ligado. Servidor verificando o horario...');
        }
    } else {
        apiAutoNextRunAt = 0;
        pararTimerProximaVerificacaoPromo();
        salvarApiAutoNextRunAt(0);
        atualizarStatusAutomacaoPromo('Automatico desligado.');
    }
}

async function carregarApiAutoPrefsServidor() {
    const resp = await fetch('/api/promo/automacao', { headers: getAuthHeadersWithClient() });
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(buildApiErrorMessage(resp, payload, 'Erro ao carregar automacao'));
    return payload;
}

async function salvarApiAutoPrefsServidor() {
    const resp = await fetch('/api/promo/automacao', {
        method: 'PUT',
        headers: getAuthHeadersWithClient({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(montarPayloadApiAutoServidor()),
    });
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(buildApiErrorMessage(resp, payload, 'Erro ao salvar automacao'));
    const prefs = apiAutoPrefsFromServerConfig(payload.config || {});
    aplicarApiAutoPrefsTela(prefs);
    atualizarStatusAutomacaoPromoPorPrefs(prefs);
    iniciarPollingAutomacaoPromoServidor();
    return prefs;
}

function atualizarStatusAutomacaoPromoPorPrefs(prefs) {
    if (!prefs.enabled) {
        atualizarStatusAutomacaoPromo('Automatico desligado.');
        return;
    }
    if (prefs.lastJobStatus === 'running' || prefs.lastJobStatus === 'queued') {
        atualizarStatusAutomacaoPromo(`Servidor rodando analise automatica${prefs.lastJobMessage ? `: ${prefs.lastJobMessage}` : '...'}`);
        return;
    }
    const restante = Number(prefs.nextRunAt || 0) - Date.now();
    if (prefs.due || restante <= 0) {
        atualizarStatusAutomacaoPromo('Automatico ligado. Servidor verificando o horario...');
        return;
    }
    if (prefs.lastError) {
        atualizarStatusAutomacaoPromo(`Automatico ligado. Ultimo erro: ${prefs.lastError}`);
        return;
    }
    atualizarStatusAutomacaoPromo(`Automatico ligado. Proxima verificacao em ${formatarTempoProximaVerificacao(restante)}.`);
}

function pararPollingAutomacaoPromoServidor() {
    if (apiAutoServerPollTimer) {
        clearInterval(apiAutoServerPollTimer);
        apiAutoServerPollTimer = null;
    }
}

function iniciarPollingAutomacaoPromoServidor() {
    pararPollingAutomacaoPromoServidor();
    if (!getApiAutoPrefs().enabled) return;
    apiAutoServerPollTimer = setInterval(() => {
        sincronizarAutomacaoPromoServidor(true);
    }, 30000);
}

async function sincronizarAutomacaoPromoServidor(silencioso = false) {
    try {
        const payload = await carregarApiAutoPrefsServidor();
        const prefs = apiAutoPrefsFromServerConfig(payload.config || {});
        aplicarApiAutoPrefsTela(prefs);
        atualizarStatusAutomacaoPromoPorPrefs(prefs);
        return prefs;
    } catch (e) {
        if (!silencioso) atualizarStatusAutomacaoPromo(e.message || 'Erro ao sincronizar automacao.');
        return null;
    }
}

function getApiAutoIntervalMs() {
    const prefs = getApiAutoPrefs();
    return Math.max(API_AUTO_MIN_INTERVAL_MIN, prefs.intervalMinutes || API_AUTO_DEFAULT_INTERVAL_MIN) * 60 * 1000;
}

function atualizarStatusAutomacaoPromo(texto) {
    const el = document.getElementById('apiAutoStatus');
    if (el) el.textContent = String(texto || '');
}

function pararTimerProximaVerificacaoPromo() {
    if (apiAutoCountdownTimer) {
        clearInterval(apiAutoCountdownTimer);
        apiAutoCountdownTimer = null;
    }
}

function formatarTempoProximaVerificacao(ms) {
    let totalSegundos = Math.max(0, Math.ceil(Number(ms || 0) / 1000));
    const semanas = Math.floor(totalSegundos / 604800);
    totalSegundos -= semanas * 604800;
    const dias = Math.floor(totalSegundos / 86400);
    totalSegundos -= dias * 86400;
    const horas = Math.floor(totalSegundos / 3600);
    totalSegundos -= horas * 3600;
    const minutos = Math.floor(totalSegundos / 60);
    const segundos = totalSegundos - minutos * 60;
    const partes = [];
    if (semanas) partes.push(`${semanas}sem`);
    if (dias || semanas) partes.push(`${dias}d`);
    if (horas || dias || semanas) partes.push(`${horas}h`);
    if (minutos || horas || dias || semanas) partes.push(`${minutos}min`);
    partes.push(`${segundos}s`);
    return partes.join(' ');
}

function atualizarTimerProximaVerificacaoPromo() {
    const restante = apiAutoNextRunAt - Date.now();
    if (restante <= 0) {
        atualizarStatusAutomacaoPromo('Automatico ligado. Servidor verificando o horario...');
        pararTimerProximaVerificacaoPromo();
        if (apiAutoTimer) clearTimeout(apiAutoTimer);
        apiAutoTimer = setTimeout(() => sincronizarAutomacaoPromoServidor(true), 3000);
        return;
    }
    atualizarStatusAutomacaoPromo(`Automatico ligado. Proxima verificacao em ${formatarTempoProximaVerificacao(restante)}.`);
}

function iniciarTimerProximaVerificacaoPromo() {
    pararTimerProximaVerificacaoPromo();
    atualizarTimerProximaVerificacaoPromo();
    apiAutoCountdownTimer = setInterval(atualizarTimerProximaVerificacaoPromo, 1000);
}

function inicializarAutomacaoPromoApi() {
    const enabledEl = document.getElementById('apiAutoWorkEnabled');
    const approvalEl = document.getElementById('apiAutoApprovalRequired');
    const intervalEl = document.getElementById('apiAutoIntervalMinutes');
    const unitEl = document.getElementById('apiAutoIntervalUnit');
    if (!enabledEl || !approvalEl || !intervalEl || !unitEl) return;
    const prefsLocal = getApiAutoPrefs();
    enabledEl.checked = !!prefsLocal.enabled;
    approvalEl.checked = !!prefsLocal.approvalRequired;
    aplicarApiAutoIntervalPrefs(prefsLocal);

    const salvarServidor = () => {
        saveApiAutoPrefs();
        return salvarApiAutoPrefsServidor().catch((e) => {
            atualizarStatusAutomacaoPromo(e.message || 'Erro ao salvar automacao no servidor.');
        });
    };
    enabledEl.addEventListener('change', () => {
        salvarServidor();
    });
    approvalEl.addEventListener('change', salvarServidor);
    intervalEl.addEventListener('change', () => {
        salvarServidor();
    });
    unitEl.addEventListener('change', () => {
        const previousUnit = API_AUTO_INTERVAL_UNITS[unitEl.dataset.previousUnit] ? unitEl.dataset.previousUnit : 'minutes';
        const previousConfig = API_AUTO_INTERVAL_UNITS[previousUnit];
        const nextConfig = API_AUTO_INTERVAL_UNITS[unitEl.value] || API_AUTO_INTERVAL_UNITS.minutes;
        const rawValue = Number(String(intervalEl.value || 1).replace(',', '.'));
        const previousMinutes = Math.max(API_AUTO_MIN_INTERVAL_MIN, Math.round((Number.isFinite(rawValue) ? rawValue : 1) * previousConfig.factor));
        intervalEl.max = String(nextConfig.max);
        intervalEl.value = String(Math.max(1, Math.min(nextConfig.max, Math.round(previousMinutes / nextConfig.factor))));
        unitEl.dataset.previousUnit = unitEl.value;
        salvarServidor();
    });
    document.getElementById('apiPromoASelect')?.addEventListener('change', () => {
        if (enabledEl.checked) salvarServidor();
    });

    atualizarStatusAutomacaoPromo('Sincronizando automacao com o servidor...');
    carregarApiAutoPrefsServidor().then(async (payload) => {
        const prefsServidor = apiAutoPrefsFromServerConfig(payload.config || {});
        if (!prefsServidor.updatedAt && prefsLocal.enabled) {
            aplicarApiAutoPrefsTela(prefsLocal);
            await salvarApiAutoPrefsServidor();
            apiAutoInicializada = true;
            return;
        }
        aplicarApiAutoPrefsTela(prefsServidor);
        atualizarStatusAutomacaoPromoPorPrefs(prefsServidor);
        iniciarPollingAutomacaoPromoServidor();
        apiAutoInicializada = true;
    }).catch(() => {
        aplicarApiAutoPrefsTela(prefsLocal);
        atualizarStatusAutomacaoPromoPorPrefs(prefsLocal);
        iniciarPollingAutomacaoPromoServidor();
        apiAutoInicializada = true;
    });
}

function agendarAutomacaoPromoApi(delayMs = null) {
    if (apiAutoTimer) {
        clearTimeout(apiAutoTimer);
        apiAutoTimer = null;
    }
    pararTimerProximaVerificacaoPromo();
    const prefs = getApiAutoPrefs();
    if (!prefs.enabled) {
        apiAutoNextRunAt = 0;
        salvarApiAutoNextRunAt(0);
        atualizarStatusAutomacaoPromo('Automatico desligado.');
        return;
    }
    const delayInformado = Number(delayMs);
    if (Number.isFinite(delayInformado) && delayInformado > 0 && !prefs.nextRunAt) {
        apiAutoNextRunAt = Date.now() + delayInformado;
    } else {
        apiAutoNextRunAt = Number(prefs.nextRunAt || 0);
    }
    salvarApiAutoNextRunAt(apiAutoNextRunAt);
    if (!apiAutoNextRunAt) {
        atualizarStatusAutomacaoPromo('Automatico ligado. Aguardando horario definido pelo servidor...');
        sincronizarAutomacaoPromoServidor(true);
        return;
    }
    const restante = apiAutoNextRunAt - Date.now();
    if (restante <= 0) {
        atualizarStatusAutomacaoPromo('Automatico ligado. Servidor verificando o horario...');
        apiAutoTimer = setTimeout(() => sincronizarAutomacaoPromoServidor(true), 3000);
        return;
    }
    atualizarStatusAutomacaoPromo(`Automatico ligado. Proxima verificacao em ${formatarTempoProximaVerificacao(restante)}.`);
    iniciarTimerProximaVerificacaoPromo();
    apiAutoTimer = setTimeout(() => sincronizarAutomacaoPromoServidor(true), Math.min(restante, API_AUTO_MAX_TIMEOUT_MS));
}

async function executarAutomacaoPromoApi() {
    await sincronizarAutomacaoPromoServidor(true);
}

function limparPollingAnaliseApi() {
    if (apiAnaliseJobPolling) {
        clearInterval(apiAnaliseJobPolling);
        apiAnaliseJobPolling = null;
    }
}

function resolverAcompanhamentoAnaliseApi(resultado) {
    limparPollingAnaliseApi();
    if (apiAnaliseResolveAtual) {
        const resolve = apiAnaliseResolveAtual;
        apiAnaliseResolveAtual = null;
        resolve(resultado);
    }
}

function setApiCancelButtonVisible(visible, loading = false) {
    const btn = document.getElementById('apiCancelJobBtn');
    if (!btn) return;
    btn.style.display = visible ? 'inline-flex' : 'none';
    btn.disabled = !!loading;
    btn.textContent = loading ? 'CANCELANDO...' : 'CANCELAR VERIFICACAO';
}

async function cancelarJobAnaliseApi(jobId) {
    const id = String(jobId || '').trim();
    if (!id) return { success: true, status: 'canceled', message: 'Verificacao cancelada.' };
    const resp = await fetch(`/api/promo/analise-via-api-arquivos/cancelar/${encodeURIComponent(id)}`, {
        method: 'POST',
        headers: getAuthHeadersWithClient(),
    });
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok || payload.success === false) {
        throw new Error(buildApiErrorMessage(resp, payload, 'Erro ao cancelar verificacao'));
    }
    return payload;
}

async function cancelarAnaliseApi() {
    apiAnaliseCancelada = true;
    setApiCancelButtonVisible(true, true);
    const loading = document.getElementById('apiLoading');
    const loadingDetails = document.getElementById('apiLoadingDetails');
    const errorMsg = document.getElementById('apiErrorMsg');
    try {
        const payload = await cancelarJobAnaliseApi(apiAnaliseJobId);
        limparPollingAnaliseApi();
        if (loading) loading.style.display = 'none';
        if (loadingDetails) {
            loadingDetails.style.display = 'none';
            loadingDetails.innerHTML = '';
        }
        if (errorMsg) errorMsg.style.display = 'none';
        atualizarApiStatusBar({
            status: 'canceled',
            progress: 100,
            message: payload.message || 'Verificacao cancelada pelo usuario.',
        });
        ocultarApiStatusBarDepois(2500);
        atualizarStatusAutomacaoPromo('Verificacao cancelada.');
        resolverAcompanhamentoAnaliseApi({ status: 'canceled', payload });
    } catch (e) {
        limparPollingAnaliseApi();
        if (loading) loading.style.display = 'none';
        if (loadingDetails) {
            loadingDetails.style.display = 'none';
            loadingDetails.innerHTML = '';
        }
        if (errorMsg) errorMsg.style.display = 'none';
        atualizarApiStatusBar({
            status: 'canceled',
            progress: 100,
            message: 'Verificacao cancelada na tela. Reinicie o sistema para parar um worker antigo.',
        });
        ocultarApiStatusBarDepois(2500);
        atualizarStatusAutomacaoPromo('Verificacao cancelada na tela.');
        resolverAcompanhamentoAnaliseApi({ status: 'canceled', error: e.message || 'Cancelamento local.' });
    } finally {
        setApiCancelButtonVisible(false);
    }
}
