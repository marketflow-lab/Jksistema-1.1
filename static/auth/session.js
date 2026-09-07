// JK Sistema - Helpers de autenticação (JWT)
// Incluir este arquivo em todas as páginas via <script src="/auth.js"></script>

function obterToken() {
    return localStorage.getItem('access_token') || null;
}

function jkCentralManualMode() {
    try {
        return JSON.parse(localStorage.getItem('user_data') || '{}').central?.sync_mode === 'manual';
    } catch (_err) { return false; }
}
window.jkCentralManualMode = jkCentralManualMode;

function jkCentralMigrationMode() {
    try {
        return JSON.parse(localStorage.getItem('user_data') || '{}').central_migration?.available === true;
    } catch (_err) { return false; }
}
window.jkCentralMigrationMode = jkCentralMigrationMode;

function _jwtPayloadLocal(token) {
    try {
        const partes = String(token || '').split('.');
        if (partes.length < 2) return null;
        const base64 = partes[1].replace(/-/g, '+').replace(/_/g, '/');
        const json = decodeURIComponent(Array.from(atob(base64)).map(ch => {
            return '%' + ('00' + ch.charCodeAt(0).toString(16)).slice(-2);
        }).join(''));
        return JSON.parse(json);
    } catch (_e) {
        return null;
    }
}

function tokenSessaoExpirado() {
    return false;
}

function respostaIndicaSessaoExpirada(resp, payload) {
    if (!resp || resp.status !== 401) return false;
    const authHeader = String(resp.headers && resp.headers.get ? resp.headers.get('WWW-Authenticate') || '' : '');
    if (/bearer/i.test(authHeader)) return true;
    const detail = String(
        (payload && (payload.detail || payload.message || payload.error)) || ''
    ).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    return (
        detail.includes('sessao expirada') ||
        detail.includes('token de autenticacao') ||
        detail.includes('faca o login novamente') ||
        detail.includes('login novamente')
    );
}

function redirecionarSessaoExpirada() {
    if (window.__jkSessionRedirecting) return;
    window.__jkSessionRedirecting = true;
    localStorage.removeItem('access_token');
    localStorage.removeItem('user_data');
    localStorage.removeItem('permissions');
    navegarComTransicao('/frontend_index.html');
}

(function initAuthFetchInterceptor() {
    if (window.__jkAuthFetchInterceptorInit || typeof window.fetch !== 'function') return;
    window.__jkAuthFetchInterceptorInit = true;
    const fetchOriginal = window.fetch.bind(window);
    window.fetch = async function jkAuthFetch(input, init) {
        const resp = await fetchOriginal(input, init);
        if (resp && resp.status === 401 && obterToken()) {
            let payload = null;
            try {
                payload = await resp.clone().json();
            } catch (_e) {}
            if (respostaIndicaSessaoExpirada(resp, payload)) {
                redirecionarSessaoExpirada();
            }
        }
        return resp;
    };
})();

(function initTabCoordinator() {
    if (window.__jkTabCoordinatorInit && window.jkTabCoordinator) return;
    window.__jkTabCoordinatorInit = true;

    const TAB_ID_KEY = 'jk-tab-instance-id-v1';
    const LEASE_PREFIX = 'jk-tab-leader:';
    const BUS_PREFIX = 'jk-tab-bus:';
    const BUS_CHANNEL_PREFIX = 'jk-tab-bus-channel:';
    const DEFAULT_TTL_MS = 45000;
    const ownedLeases = new Set();
    const channelCache = new Map();

    function gerarId() {
        try {
            if (crypto && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
        } catch (_err) {}
        return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
    }

    let tabId = gerarId();
    try {
        const stored = sessionStorage.getItem(TAB_ID_KEY);
        if (stored) {
            tabId = stored;
        } else {
            sessionStorage.setItem(TAB_ID_KEY, tabId);
        }
    } catch (_err) {}

    function leaseKey(name) {
        return LEASE_PREFIX + String(name || 'default');
    }

    function busKey(name) {
        return BUS_PREFIX + String(name || 'default');
    }

    function lerJsonStorage(key) {
        try {
            const raw = localStorage.getItem(key);
            return raw ? JSON.parse(raw) : null;
        } catch (_err) {
            return null;
        }
    }

    function escreverJsonStorage(key, value) {
        try {
            localStorage.setItem(key, JSON.stringify(value));
            return true;
        } catch (_err) {
            return false;
        }
    }

    function lerLease(name) {
        return lerJsonStorage(leaseKey(name));
    }

    function liberar(name) {
        const key = leaseKey(name);
        try {
            const atual = lerLease(name);
            if (atual && atual.id === tabId) localStorage.removeItem(key);
        } catch (_err) {}
        ownedLeases.delete(String(name || 'default'));
    }

    function isLeader(name, ttlMs) {
        const leaseName = String(name || 'default');
        const ttl = Math.max(10000, Number(ttlMs) || DEFAULT_TTL_MS);
        const agora = Date.now();
        let atual = lerLease(leaseName);
        if (!atual || !atual.id || Number(atual.expiresAt || 0) <= agora || atual.id === tabId) {
            const next = { id: tabId, updatedAt: agora, expiresAt: agora + ttl };
            if (!escreverJsonStorage(leaseKey(leaseName), next)) {
                ownedLeases.add(leaseName);
                return true;
            }
            atual = lerLease(leaseName);
        }
        const lider = !!(atual && atual.id === tabId && Number(atual.expiresAt || 0) > agora);
        if (lider) ownedLeases.add(leaseName);
        else ownedLeases.delete(leaseName);
        return lider;
    }

    function createLeader(name, options = {}) {
        const leaseName = String(name || 'default');
        const ttl = Math.max(10000, Number(options.ttlMs) || DEFAULT_TTL_MS);
        let stopped = false;
        const intervalMs = Math.max(5000, Math.floor(ttl / 3));
        const timer = setInterval(() => {
            if (!stopped) isLeader(leaseName, ttl);
        }, intervalMs);
        const controller = {
            id: tabId,
            isLeader: () => !stopped && isLeader(leaseName, ttl),
            release: () => liberar(leaseName),
            stop: () => {
                stopped = true;
                clearInterval(timer);
                liberar(leaseName);
            }
        };
        controller.isLeader();
        return controller;
    }

    function obterCanal(name) {
        if (typeof BroadcastChannel !== 'function') return null;
        const channelName = BUS_CHANNEL_PREFIX + String(name || 'default');
        if (!channelCache.has(channelName)) {
            channelCache.set(channelName, new BroadcastChannel(channelName));
        }
        return channelCache.get(channelName);
    }

    function normalizarEnvelope(payload) {
        return {
            id: gerarId(),
            from: tabId,
            ts: Date.now(),
            payload
        };
    }

    function broadcast(name, payload) {
        const envelope = normalizarEnvelope(payload);
        try {
            const canal = obterCanal(name);
            if (canal) canal.postMessage(envelope);
        } catch (_err) {}
        try {
            localStorage.setItem(busKey(name), JSON.stringify(envelope));
        } catch (_err) {}
    }

    function tratarEnvelope(envelope, handler) {
        if (!envelope || envelope.from === tabId) return;
        try {
            handler(envelope.payload, envelope);
        } catch (_err) {}
    }

    function subscribe(name, handler) {
        let canal = null;
        const onMessage = (event) => tratarEnvelope(event && event.data, handler);
        try {
            canal = obterCanal(name);
            if (canal) canal.addEventListener('message', onMessage);
        } catch (_err) {
            canal = null;
        }
        const onStorage = (event) => {
            if (!event || event.key !== busKey(name) || !event.newValue) return;
            try {
                tratarEnvelope(JSON.parse(event.newValue), handler);
            } catch (_err) {}
        };
        window.addEventListener('storage', onStorage);
        return () => {
            try {
                if (canal) canal.removeEventListener('message', onMessage);
            } catch (_err) {}
            window.removeEventListener('storage', onStorage);
        };
    }

    window.addEventListener('pagehide', () => {
        Array.from(ownedLeases).forEach(liberar);
    });

    window.jkTabCoordinator = {
        tabId,
        isLeader,
        createLeader,
        broadcast,
        subscribe,
        release: liberar
    };
})();
