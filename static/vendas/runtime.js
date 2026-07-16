const tHead = document.getElementById('tHead');
const tBody = document.getElementById('tBody');
const resumo = document.getElementById('resumo');
const statusEl = document.getElementById('status');
const filtroTexto = document.getElementById('filtroTexto');
const lojaBotoes = document.getElementById('lojaBotoes');
const unidadeNegocioSelect = document.getElementById('unidadeNegocioSelect');
const mesAno = document.getElementById('mesAno');
const dataIni = document.getElementById('dataIni');
const dataFim = document.getElementById('dataFim');
const periodoTexto = document.getElementById('periodoTexto');
const btnSync = document.getElementById('btnSync');
const btnCancel = document.getElementById('btnCancel');
const compareAnoPassado = document.getElementById('compareAnoPassado');
const mostrarEstoqueGeralCheck = document.getElementById('mostrarEstoqueGeral');
const mostrarEstoqueSkuCheck = document.getElementById('mostrarEstoqueSku');
const graficoEstoqueSkuInput = document.getElementById('graficoEstoqueSkuInput');
const graficoEstoqueWrapper = document.getElementById('graficoEstoqueWrapper');
const graficoEstoqueResumo = document.getElementById('graficoEstoqueResumo');
const graficoEstoqueEstado = document.getElementById('graficoEstoqueEstado');
const skuLoadingOverlay = document.getElementById('skuLoadingOverlay');
const navActionsVendas = document.querySelector('.nav-actions');
const graficosContainerVendas = document.querySelector('.graficos-container');
const btnSidebarObservacoesVendas = document.getElementById('btnSidebarObservacoesVendas');
const btnSidebarAssistenteVendas = document.getElementById('btnSidebarAssistenteVendas');
const sidebarPainelObservacoesVendas = document.getElementById('sidebarPainelObservacoesVendas');
const sidebarPainelAssistenteVendas = document.getElementById('sidebarPainelAssistenteVendas');
const sidebarAiChatVendas = document.getElementById('sidebarAiChatVendas');
const sidebarAiInputVendas = document.getElementById('sidebarAiInputVendas');
const btnSidebarAiEnviarVendas = document.getElementById('btnSidebarAiEnviarVendas');
const btnSidebarAiAnexoVendas = document.getElementById('btnSidebarAiAnexoVendas');
const sidebarAiFileInputVendas = document.getElementById('sidebarAiFileInputVendas');
const sidebarAiAnexosVendas = document.getElementById('sidebarAiAnexosVendas');
const graficoObservacoesVendas = document.getElementById('graficoObservacoesVendas');
const graficoRankingVendas = document.getElementById('graficoRankingVendas');
const btnRankingVendidosVendas = document.getElementById('btnRankingVendidosVendas');
const btnRankingDevolucoesVendas = document.getElementById('btnRankingDevolucoesVendas');
const graficoOciososVendas = document.getElementById('graficoOciososVendas');
const btnOciosos7d = document.getElementById('btnOciosos7d');
const btnOciosos15d = document.getElementById('btnOciosos15d');
const btnOciosos30d = document.getElementById('btnOciosos30d');
const btnOciosos60d = document.getElementById('btnOciosos60d');
const btnOciosos90d = document.getElementById('btnOciosos90d');
const spinnerHtml = '<span class="spinner"></span>';
const REQUEST_TIMEOUT_MS = 15000;
const VENDAS_VIEW_CACHE_KEY = 'vendas_view_cache_v2';
const VENDAS_VIEW_CACHE_TTL_MS = 10 * 60 * 1000;
const VENDAS_VIEW_CACHE_MAX_ROWS = 5000;
const IA_MAX_ANEXOS = 4;
const IA_MAX_ANEXO_BYTES = 5 * 1024 * 1024;

function corrigirTextoVendas(valor) {
    let texto = String(valor ?? '');
    const trocas = [
        ['\u00c3\u0192\u00c2\u00a7', '\u00e7'],
        ['\u00c3\u0192\u00c2\u00a3', '\u00e3'],
        ['\u00c3\u0192\u00c2\u00b5', '\u00f5'],
        ['\u00c3\u0192\u00c2\u00a1', '\u00e1'],
        ['\u00c3\u0192\u00c2\u00a9', '\u00e9'],
        ['\u00c3\u0192\u00c2\u00ad', '\u00ed'],
        ['\u00c3\u0192\u00c2\u00b3', '\u00f3'],
        ['\u00c3\u0192\u00c2\u00ba', '\u00fa'],
        ['\u00c3\u0192\u00c2\u00aa', '\u00ea'],
        ['\u00c3\u0192\u00c2\u00ba', '\u00fa'],
        ['\u00c3\u00a7', '\u00e7'],
        ['\u00c3\u00a3', '\u00e3'],
        ['\u00c3\u00b5', '\u00f5'],
        ['\u00c3\u00a1', '\u00e1'],
        ['\u00c3\u00a9', '\u00e9'],
        ['\u00c3\u00ad', '\u00ed'],
        ['\u00c3\u00b3', '\u00f3'],
        ['\u00c3\u00ba', '\u00fa'],
        ['\u00c3\u00aa', '\u00ea'],
        ['\u00e2\u0153\u2026', '\u2705'],
        ['\u00e2\u008f\u00b3', '\u23f3'],
        ['\u00e2\u008f\u00b9\u00ef\u00b8\u008f', '\u23f9\ufe0f'],
        ['\u00e2\u009d\u008c', '\u274c'],
        ['\u00f0\u0178\u008f\u00ac', '\ud83c\udfec'],
        ['\u00f0\u0178\u201c\u2020', '\ud83d\udcc6'],
    ];
    for (const [ruim, bom] of trocas) {
        texto = texto.split(ruim).join(bom);
    }
    return texto;
}

function escaparHtmlVendas(valor) {
    return corrigirTextoVendas(valor)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function usuarioLocalEhAdminChatVendas() {
    try {
        const permissoes = JSON.parse(localStorage.getItem('permissions') || '{}');
        return !!(permissoes && (permissoes.full === true || permissoes.admin_usuarios === true));
    } catch (_error) {
        return false;
    }
}

function aplicarPermissaoModeloChatVendas(selectEl, podeEscolher) {
    if (!selectEl) return;
    const permitido = !!podeEscolher;
    selectEl.dataset.podeEscolherModelo = permitido ? 'true' : 'false';
    selectEl.disabled = !permitido;
    selectEl.style.display = permitido ? '' : 'none';
}

let dados = [];
let dadosFiltrados = [];
let notasEntrada = [];
let devolucaoItens = [];
let dadosModoResumo = false;
let resumoTotaisVendas = null;
let vendasResumoMeta = null;
let clientId = null;
let lojaSelecionada = '__todas';
let lojasDisponiveis = [];
let missingPromptShown = false;
let missingPromptKey = '';
let syncController = null;
let syncEmAndamento = false;
let cancelSolicitado = false;
let progressTimer = null;
let progressPollInFlight = false;
let mapeamentoUnidades = {}; // Armazenar mapeamento de ID -> Nome
let autoSyncAoTrocarLoja = false;
let autoSyncTimer = null;
let dateRangePicker = null;
let dateFimPicker = null;
let anexosAssistenteVendas = [];
    // ── Persistência de conversas IA (Vendas) — Servidor + localStorage fallback ──
    const IA_MODULO_KEY = 'vendas';
    const IA_MAX_MSGS_STORE = 80;
    const IA_MAX_CONVS_STORE = 20;
    let iaConvAtualId = null;
    let iaMensagensAtuais = [];

    function _iaClientId() {
        try { return (JSON.parse(localStorage.getItem('user_data') || '{}')).client_id || 'default'; } catch(_) { return 'default'; }
    }
    function _iaIndexKey() { return 'ia_convs_' + _iaClientId() + '_' + IA_MODULO_KEY; }
    function _iaStoreKey(id) { return 'ia_hist_' + _iaClientId() + '_' + IA_MODULO_KEY + '_' + id; }
    function _iaNovoId() { return Date.now().toString(36) + Math.random().toString(36).slice(2,6); }
    function _iaListarConversasLocal() { try { return JSON.parse(localStorage.getItem(_iaIndexKey()) || '[]'); } catch(_) { return []; } }
    function _iaSalvarIndexLocal(lista) { try { localStorage.setItem(_iaIndexKey(), JSON.stringify(lista.slice(0, IA_MAX_CONVS_STORE))); } catch(_) {} }
    function _iaCarregarMsgsLocal(id) { try { return JSON.parse(localStorage.getItem(_iaStoreKey(id)) || '[]'); } catch(_) { return []; } }
    function _iaSalvarMsgsLocal(id, msgs) { try { localStorage.setItem(_iaStoreKey(id), JSON.stringify(msgs.slice(-IA_MAX_MSGS_STORE))); } catch(_) {} }
    function _iaRemoverConversaLocal(id) { try { localStorage.removeItem(_iaStoreKey(id)); } catch(_) {} }
    
    async function _iaListarConversasServidor() {
        try {
            const r = await fetch(`/api/ia/conversas/listar?modulo=${encodeURIComponent(IA_MODULO_KEY)}`, {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + (localStorage.getItem('access_token') || ''),
                    'X-Client-ID': _iaClientId()
                }
            });
            if (r.ok) {
                const d = await r.json();
                if (d && d.success === true && Array.isArray(d.conversas)) {
                    return d.conversas.map(c => ({ id: c.id, data: String(c.data_atualizacao || '').slice(0, 16), preview: c.titulo }));
                }
            }
        } catch(_) {}
        return _iaListarConversasLocal();
    }
    
    async function _iaCarregarConversaServidor(convId) {
        try {
            const r = await fetch(`/api/ia/conversas/${encodeURIComponent(convId)}`, {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + (localStorage.getItem('access_token') || ''),
                    'X-Client-ID': _iaClientId()
                }
            });
            if (r.ok) {
                const d = await r.json();
                if (d && d.success === true && d.conversa && Array.isArray(d.conversa.mensagens)) {
                    return d.conversa.mensagens.map(m => ({ role: m.role, text: m.text }));
                }
            }
        } catch(_) {}
        return _iaCarregarMsgsLocal(convId);
    }
    
    async function _iaSalvarConversaServidor(convId, titulo, msgs) {
        _iaSalvarMsgsLocal(convId, msgs);
        try {
            const r = await fetch('/api/ia/conversas/salvar', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + (localStorage.getItem('access_token') || ''),
                    'X-Client-ID': _iaClientId()
                },
                body: JSON.stringify({
                    conversa_id: convId,
                    modulo: IA_MODULO_KEY,
                    titulo: titulo,
                    mensagens: msgs.map(m => ({ role: m.role, text: m.text }))
                })
            });
            if (r.ok) {
                const d = await r.json().catch(() => null);
                return !!(d && d.success === true);
            }
        } catch(_) {}
        return false;
    }
    
    async function _iaDeletarConversaServidor(convId) {
        try {
            const r = await fetch(`/api/ia/conversas/${encodeURIComponent(convId)}`, {
                method: 'DELETE',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + (localStorage.getItem('access_token') || ''),
                    'X-Client-ID': _iaClientId()
                }
            });
            if (r.ok) {
                const d = await r.json().catch(() => null);
                if (d && d.success === true) return true;
            }
        } catch(_) {}
        _iaRemoverConversaLocal(convId);
        return false;
    }
    
    function _iaSalvarMsgsAtuais() {
        if (!iaConvAtualId || iaMensagensAtuais.length === 0) return;
        _iaSalvarMsgsLocal(iaConvAtualId, iaMensagensAtuais);
        const titulo = (iaMensagensAtuais.find(m => m.role === 'user')?.text || 'Conversa').slice(0, 60);
        _iaSalvarConversaServidor(iaConvAtualId, titulo, iaMensagensAtuais).catch(() => {});
    }

    async function iaRestaurarOuCriarConversa() {
        const lista = await _iaListarConversasServidor();
        if (!sidebarAiChatVendas) return;
        if (lista.length > 0) {
            iaConvAtualId = lista[0].id;
            iaMensagensAtuais = await _iaCarregarConversaServidor(iaConvAtualId);
        } else {
            iaConvAtualId = _iaNovoId();
            iaMensagensAtuais = [];
        }
        if (iaMensagensAtuais.length > 0) {
            sidebarAiChatVendas.innerHTML = '';
            iaMensagensAtuais.forEach(m => {
                const d = document.createElement('div');
                d.className = 'sidebar-ai-msg ' + m.role;
                if (m.role === 'assistant') {
                    d.innerHTML = formatarMarkdownBasicoAssistenteVendas(m.text || '');
                } else {
                    d.textContent = m.text || '';
                }
                sidebarAiChatVendas.appendChild(d);
            });
            sidebarAiChatVendas.scrollTop = sidebarAiChatVendas.scrollHeight;
        }
    }

    function iaNovaConversa() {
        _iaSalvarMsgsAtuais();
        iaConvAtualId = _iaNovoId();
        iaMensagensAtuais = [];
        if (sidebarAiChatVendas) {
            sidebarAiChatVendas.innerHTML = '';
            const msg = document.createElement('div');
            msg.className = 'sidebar-ai-msg assistant';
            msg.textContent = 'Olá! Nova conversa iniciada. O que você quer analisar?';
            sidebarAiChatVendas.appendChild(msg);
        }
        _iaSalvarMsgsAtuais();
    }

    async function iaMostrarHistorico() {
        const lista = await _iaListarConversasServidor();
        if (!sidebarAiChatVendas) return;
        sidebarAiChatVendas.innerHTML = '';
        if (lista.length === 0) {
            const d = document.createElement('div');
            d.className = 'sidebar-ai-msg assistant';
            d.textContent = 'Nenhuma conversa salva ainda.';
            sidebarAiChatVendas.appendChild(d);
            return;
        }
        const titulo = document.createElement('div');
        titulo.className = 'sidebar-ai-msg assistant';
        titulo.innerHTML = '<strong>Conversas salvas:</strong>';
        sidebarAiChatVendas.appendChild(titulo);
        lista.forEach(conv => {
            const d = document.createElement('div');
            d.style.cssText = 'display:flex;align-items:center;gap:6px;padding:6px 8px;border:1px solid rgba(120,227,212,.22);border-radius:10px;margin-bottom:4px;cursor:pointer;background:rgba(15,60,80,.3);';
            d.innerHTML = `<div style="flex:1;overflow:hidden;"><div style="color:#8ee9de;font-size:.7rem;">${conv.data}</div><div style="color:#cde;font-size:.76rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${(conv.preview||'').replace(/</g,'&lt;')}</div></div><button style="border:0;background:transparent;color:#f87171;cursor:pointer;font-size:.85rem;padding:2px 6px;" data-del="${conv.id}">🗑</button>`;
            d.querySelector('div').addEventListener('click', async () => {
                _iaSalvarMsgsAtuais();
                iaConvAtualId = conv.id;
                iaMensagensAtuais = await _iaCarregarConversaServidor(conv.id);
                sidebarAiChatVendas.innerHTML = '';
                iaMensagensAtuais.forEach(m => {
                    const el = document.createElement('div');
                    el.className = 'sidebar-ai-msg ' + m.role;
                    if (m.role === 'assistant') el.innerHTML = formatarMarkdownBasicoAssistenteVendas(m.text || '');
                    else el.textContent = m.text || '';
                    sidebarAiChatVendas.appendChild(el);
                });
                sidebarAiChatVendas.scrollTop = sidebarAiChatVendas.scrollHeight;
            });
            d.querySelector('[data-del]').addEventListener('click', e => {
                e.stopPropagation();
                _iaDeletarConversaServidor(conv.id).catch(() => {});
                if (conv.id === iaConvAtualId) iaNovaConversa();
                else iaMostrarHistorico();
            });
            sidebarAiChatVendas.appendChild(d);
        });
    }
let periodoApplyTimer = null;
let periodoSelecionadoPeloUsuario = false;
let carregarVendasPromise = null;
let carregarVendasRequestKey = '';
let carregarVendasController = null;
let tabelaRenderToken = 0;
let filtrarToken = 0;
let cacheSaveTimer = null;

function criarMonitorSyncVendas() {
    const listeners = new Set();
    const activeIntervalMs = 3000;
    const idleIntervalMs = 15000;
    const retryDelaysMs = [3000, 6000, 12000, 30000];
    let timer = null;
    let inFlight = null;
    let started = false;
    let failures = 0;
    let lastPayload = null;
    let completionSequence = 0;

    function clearTimer() {
        if (timer) {
            clearTimeout(timer);
            timer = null;
        }
    }

    function schedule(delayMs) {
        clearTimer();
        if (!started || document.visibilityState === 'hidden') return;
        timer = setTimeout(() => {
            void refresh();
        }, Math.max(0, delayMs));
    }

    function notify(payload) {
        listeners.forEach(listener => {
            try { listener(payload); } catch (error) { console.warn('[VENDAS] Falha em listener de sync:', error); }
        });
        window.dispatchEvent(new CustomEvent('jk:vendas-sync-state', { detail: payload }));
    }

    function publish(payload) {
        const wasActive = !!lastPayload?.active;
        lastPayload = payload;
        failures = 0;
        notify(payload);
        if (wasActive && !payload?.active) {
            completionSequence += 1;
            window.dispatchEvent(new CustomEvent('jk:vendas-sync-finished', {
                detail: { id: completionSequence, payload }
            }));
        }
        schedule(payload?.active ? activeIntervalMs : idleIntervalMs);
        return payload;
    }

    async function refresh(options = {}) {
        if (document.visibilityState === 'hidden') return lastPayload;
        if (inFlight) return inFlight;
        clearTimer();
        inFlight = (async () => {
            try {
                const response = await fetch('/api/vendas/sync/progress', {
                    headers: obterAuthHeaders(),
                    cache: 'no-store'
                });
                if (!response.ok) {
                    throw new Error(`Falha ao consultar progresso: HTTP ${response.status}`);
                }
                return publish(await response.json());
            } catch (error) {
                failures += 1;
                const retryIndex = Math.min(failures - 1, retryDelaysMs.length - 1);
                schedule(retryDelaysMs[retryIndex]);
                if (options.propagateError) throw error;
                return lastPayload;
            } finally {
                inFlight = null;
            }
        })();
        return inFlight;
    }

    function subscribe(listener, options = {}) {
        if (typeof listener !== 'function') return () => {};
        listeners.add(listener);
        if (options.immediate !== false && lastPayload) listener(lastPayload);
        return () => listeners.delete(listener);
    }

    function start() {
        if (!started) started = true;
        if (document.visibilityState !== 'hidden') void refresh();
    }

    function stop() {
        started = false;
        clearTimer();
    }

    function waitForInactive(options = {}) {
        let sawActive = !!lastPayload?.active;
        return new Promise((resolve, reject) => {
            let unsubscribe = () => {};
            let settled = false;
            const finish = (callback, value) => {
                if (settled) return;
                settled = true;
                unsubscribe();
                if (options.signal) options.signal.removeEventListener('abort', onAbort);
                callback(value);
            };
            const onAbort = () => finish(reject, new DOMException('Operação cancelada.', 'AbortError'));
            const onState = (payload, fresh = false) => {
                if (payload?.active) {
                    sawActive = true;
                    return;
                }
                if (!sawActive && !fresh) return;
                const etapa = String(payload?.progress?.etapa || '').toLowerCase();
                if (etapa === 'erro') {
                    finish(reject, new Error(corrigirTextoVendas(payload?.progress?.mensagem || 'Erro na sincronização.')));
                    return;
                }
                finish(resolve, payload);
            };
            unsubscribe = subscribe(payload => onState(payload, false), { immediate: false });
            if (options.signal) {
                if (options.signal.aborted) {
                    onAbort();
                    return;
                }
                options.signal.addEventListener('abort', onAbort, { once: true });
            }
            void refresh({ propagateError: false }).then(payload => {
                if (payload) onState(payload, true);
            });
        });
    }

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') {
            clearTimer();
            return;
        }
        if (started) void refresh();
    });

    return {
        start,
        stop,
        refresh,
        subscribe,
        waitForInactive,
        getLastPayload: () => lastPayload,
        isRequestInFlight: () => !!inFlight
    };
}

const vendasSyncMonitor = window.__jkVendasSyncMonitor || criarMonitorSyncVendas();
window.__jkVendasSyncMonitor = vendasSyncMonitor;
