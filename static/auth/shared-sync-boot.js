(function initIaSecretsProvisioning() {
    if (window.jkCentralManualMode?.()) return;
    if (window.__jkIaSecretsProvisioningInit) return;
    window.__jkIaSecretsProvisioningInit = true;

    const STORAGE_KEY = 'jk-ia-secrets-provision-v1';
    const SUCCESS_INTERVAL_MS = 12 * 60 * 60 * 1000;
    const RETRY_INTERVAL_MS = 15 * 60 * 1000;
    let emExecucao = false;

    function lerEstado() {
        try {
            return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') || {};
        } catch (_err) {
            return {};
        }
    }

    function salvarEstado(patch) {
        try {
            const atual = lerEstado();
            localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.assign({}, atual, patch || {})));
        } catch (_err) {}
    }

    function podeTentar() {
        if (!obterToken()) return false;
        const estado = lerEstado();
        const agora = Date.now();
        if (Number(estado.successAt || 0) && agora - Number(estado.successAt || 0) < SUCCESS_INTERVAL_MS) return false;
        if (Number(estado.lastAttemptAt || 0) && agora - Number(estado.lastAttemptAt || 0) < RETRY_INTERVAL_MS) return false;
        return true;
    }

    async function provisionar() {
        if (emExecucao || !podeTentar()) return;
        const leader = window.jkTabCoordinator && typeof window.jkTabCoordinator.createLeader === 'function'
            ? window.jkTabCoordinator.createLeader('ia-secrets-provisioning', { ttlMs: 60000 })
            : null;
        if (leader && !leader.isLeader()) return;
        emExecucao = true;
        salvarEstado({ lastAttemptAt: Date.now() });
        try {
            const resp = await fetch('/api/ia/secrets/provisionar', {
                method: 'POST',
                headers: obterAuthHeaders({ 'Content-Type': 'application/json' }),
                body: '{}',
                cache: 'no-store'
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data && data.success) {
                salvarEstado({
                    successAt: Date.now(),
                    version: data.version || '',
                    lastError: ''
                });
            } else {
                salvarEstado({
                    lastErrorAt: Date.now(),
                    lastError: String((data && (data.message || data.detail)) || 'Provisionamento indisponivel.')
                });
            }
        } catch (err) {
            salvarEstado({
                lastErrorAt: Date.now(),
                lastError: String((err && err.message) || err || 'Falha no provisionamento.')
            });
        } finally {
            emExecucao = false;
            try { leader?.release?.(); } catch (_err) {}
        }
    }

    function iniciar() {
        setTimeout(provisionar, 1200);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', iniciar, { once: true });
    } else {
        iniciar();
    }
})();

(function initMachinePresenceHeartbeat() {
    if (window.jkCentralManualMode?.()) return;
    if (window.__jkMachinePresenceInit) return;
    window.__jkMachinePresenceInit = true;

    const FIREBASE_SDK_VERSION = '10.12.5';
    const FALLBACK_HEARTBEAT_INTERVAL_MS = 5 * 60 * 1000;
    const RTDB_SESSION_ENDPOINT = '/api/firebase/realtime-presence/session';
    let ultimoHeartbeat = 0;
    let timer = null;
    let emExecucao = false;
    let appVersionCache = null;
    let appVersionPromise = null;
    const rtdbState = {
        disabled: false,
        session: null,
        sessionLoadedAt: 0,
        modulesPromise: null,
        startPromise: null,
        modules: null,
        app: null,
        auth: null,
        db: null,
        connectionRef: null,
        lastStateRef: null,
        unsubscribeConnected: null,
        started: false,
        lastUsersPayload: null,
        lastUsersPayloadAt: 0
    };
    const presenceLeader = window.jkTabCoordinator && typeof window.jkTabCoordinator.createLeader === 'function'
        ? window.jkTabCoordinator.createLeader('machine-presence', { ttlMs: 45000 })
        : null;
    const PRESENCE_DATA_CHANNEL = 'machine-presence-data';
    const PRESENCE_REQUEST_CHANNEL = 'machine-presence-request';
    const PRESENCE_CACHE_TTL_MS = 90 * 1000;
    const RTDB_USERS_CACHE_TTL_MS = 15 * 1000;
    let tentativaLiderPresencaTimer = null;
    let cacheMaquinasOnline = null;
    let cacheMaquinasOnlineAt = 0;
    let cacheUsuariosOnline = null;
    let cacheUsuariosOnlineAt = 0;
    let maquinasOnlinePromise = null;
    let usuariosOnlinePromise = null;

    function liderPresenca() {
        return !presenceLeader || presenceLeader.isLeader();
    }

    function cachePresencaValido(ts, ttlMs = PRESENCE_CACHE_TTL_MS) {
        return !!ts && Date.now() - ts < ttlMs;
    }

    function atualizarCachePresenca(tipo, payload) {
        if (!payload || typeof payload !== 'object') return;
        if (tipo === 'machines') {
            cacheMaquinasOnline = payload;
            cacheMaquinasOnlineAt = Date.now();
        } else if (tipo === 'users') {
            cacheUsuariosOnline = payload;
            cacheUsuariosOnlineAt = Date.now();
            rtdbState.lastUsersPayload = payload;
            rtdbState.lastUsersPayloadAt = Date.now();
        }
    }

    function publicarPresenca(tipo, payload) {
        atualizarCachePresenca(tipo, payload);
        try {
            window.jkTabCoordinator?.broadcast(PRESENCE_DATA_CHANNEL, { type: tipo, payload });
        } catch (_err) {}
    }

    function cachePresenca(tipo, permitirStale = false) {
        if (tipo === 'machines') {
            if (permitirStale || cachePresencaValido(cacheMaquinasOnlineAt)) return cacheMaquinasOnline;
        }
        if (tipo === 'users') {
            if (permitirStale || cachePresencaValido(cacheUsuariosOnlineAt)) return cacheUsuariosOnline;
        }
        return null;
    }

    function aguardarPresencaLider(tipo, timeoutMs = 1800) {
        if (!window.jkTabCoordinator || typeof window.jkTabCoordinator.subscribe !== 'function') {
            return Promise.resolve(cachePresenca(tipo, true));
        }
        const cached = cachePresenca(tipo);
        if (cached) return Promise.resolve(cached);
        return new Promise((resolve) => {
            let resolvido = false;
            const encerrar = (payload) => {
                if (resolvido) return;
                resolvido = true;
                try { unsubscribe(); } catch (_err) {}
                resolve(payload || cachePresenca(tipo, true));
            };
            const unsubscribe = window.jkTabCoordinator.subscribe(PRESENCE_DATA_CHANNEL, (evento) => {
                if (!evento || evento.type !== tipo) return;
                atualizarCachePresenca(tipo, evento.payload);
                encerrar(evento.payload);
            });
            try {
                window.jkTabCoordinator.broadcast(PRESENCE_REQUEST_CHANNEL, { type: tipo });
            } catch (_err) {}
            setTimeout(() => encerrar(null), Math.max(600, Number(timeoutMs) || 1800));
        });
    }

    function agendarTentativaLiderPresenca(delayMs = 20000) {
        if (tentativaLiderPresencaTimer) return;
        tentativaLiderPresencaTimer = setTimeout(() => {
            tentativaLiderPresencaTimer = null;
            if (!obterToken() || tokenSessaoExpirado()) return;
            if (liderPresenca()) iniciar();
            else agendarTentativaLiderPresenca(delayMs);
        }, Math.max(5000, Number(delayMs) || 20000));
    }

    try {
        window.jkTabCoordinator?.subscribe(PRESENCE_DATA_CHANNEL, (evento) => {
            if (!evento || !evento.type) return;
            atualizarCachePresenca(evento.type, evento.payload);
        });
        window.jkTabCoordinator?.subscribe(PRESENCE_REQUEST_CHANNEL, (evento) => {
            if (!evento || !liderPresenca()) return;
            if (evento.type === 'machines') {
                buscarMaquinasOnline({ requestedByPeer: true })
                    .then((data) => { if (data) publicarPresenca('machines', data); })
                    .catch(() => {});
            } else if (evento.type === 'users') {
                buscarUsuariosOnline({ requestedByPeer: true })
                    .then((data) => { if (data) publicarPresenca('users', data); })
                    .catch(() => {});
            }
        });
    } catch (_err) {}

    function obterUserDataPresenca() {
        try {
            return JSON.parse(localStorage.getItem('user_data') || 'null') || null;
        } catch (_e) {
            return null;
        }
    }

    function obterMachineIdPresenca() {
        const userData = obterUserDataPresenca();
        return String((userData && userData.machine_id) || '').trim();
    }

    function extrairAppVersionUserAgent() {
        const match = String(navigator.userAgent || '').match(/\bjk-sistema-desktop\/([0-9A-Za-z._+-]+)/i);
        return match ? String(match[1] || '').trim() : '';
    }

    async function obterAppVersionPresenca() {
        if (appVersionCache !== null) return appVersionCache;
        if (appVersionPromise) return appVersionPromise;
        appVersionPromise = (async () => {
            let versao = '';
            try {
                if (window.electronAPI && typeof window.electronAPI.getAppVersion === 'function') {
                    versao = String(await window.electronAPI.getAppVersion() || '').trim();
                }
            } catch (_err) {
                versao = '';
            }
            let versaoLocal = '';
            try {
                versaoLocal = String(localStorage.getItem('jk_app_version') || '').trim();
            } catch (_err) {
                versaoLocal = '';
            }
            appVersionCache = versao || extrairAppVersionUserAgent() || versaoLocal;
            if (appVersionCache) {
                try {
                    localStorage.setItem('jk_app_version', appVersionCache);
                } catch (_err) {}
            }
            appVersionPromise = null;
            return appVersionCache;
        })();
        return appVersionPromise;
    }

    function normalizarTimestampSegundos(valor) {
        const numero = Number(valor || 0);
        if (!Number.isFinite(numero) || numero <= 0) return 0;
        return numero > 100000000000 ? Math.floor(numero / 1000) : Math.floor(numero);
    }

    function dataLocalDeTimestamp(segundos) {
        const ts = normalizarTimestampSegundos(segundos);
        if (!ts) return '';
        const data = new Date(ts * 1000);
        const pad = (v) => String(v).padStart(2, '0');
        return `${data.getFullYear()}-${pad(data.getMonth() + 1)}-${pad(data.getDate())} ${pad(data.getHours())}:${pad(data.getMinutes())}:${pad(data.getSeconds())}`;
    }

    function criarConnectionId() {
        const bytes = new Uint8Array(8);
        try {
            crypto.getRandomValues(bytes);
        } catch (_err) {
            for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
        }
        const aleatorio = Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
        return `${Date.now().toString(36)}-${aleatorio}`;
    }

    function paginaAtualPresenca() {
        return `${window.location.pathname || ''}${window.location.search || ''}`.slice(0, 180);
    }

    async function carregarFirebaseModules() {
        if (!rtdbState.modulesPromise) {
            const base = `https://www.gstatic.com/firebasejs/${FIREBASE_SDK_VERSION}`;
            rtdbState.modulesPromise = Promise.all([
                import(`${base}/firebase-app.js`),
                import(`${base}/firebase-auth.js`),
                import(`${base}/firebase-database.js`)
            ]).then(([app, auth, database]) => ({ app, auth, database }));
        }
        rtdbState.modules = await rtdbState.modulesPromise;
        return rtdbState.modules;
    }

    async function obterSessaoRtdb(force = false) {
        if (rtdbState.disabled && !force) return null;
        if (!force && rtdbState.session && Date.now() - rtdbState.sessionLoadedAt < 45 * 60 * 1000) {
            return rtdbState.session;
        }
        const machineId = obterMachineIdPresenca();
        const appVersion = await obterAppVersionPresenca();
        const params = new URLSearchParams({
            machine_id: machineId,
            app_version: appVersion || ''
        });
        const url = `${RTDB_SESSION_ENDPOINT}?${params.toString()}`;
        const resp = await fetch(url, {
            method: 'GET',
            headers: obterAuthHeaders(),
            cache: 'no-store'
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.success === false) {
            throw new Error(data.detail || data.message || 'Nao foi possivel preparar a presenca em tempo real.');
        }
        if (!data.enabled) {
            rtdbState.disabled = true;
            return null;
        }
        rtdbState.session = data;
        rtdbState.sessionLoadedAt = Date.now();
        return rtdbState.session;
    }

    async function prepararRtdb() {
        const session = await obterSessaoRtdb();
        if (!session) return null;
        const modules = await carregarFirebaseModules();
        const appName = `jk-presence-${String(session.clientKey || 'default').replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 80)}`;
        let app = null;
        try {
            app = modules.app.getApp(appName);
        } catch (_err) {
            app = modules.app.initializeApp(session.firebaseConfig, appName);
        }
        const auth = modules.auth.getAuth(app);
        await modules.auth.signInWithCustomToken(auth, session.customToken);
        rtdbState.app = app;
        rtdbState.auth = auth;
        rtdbState.db = modules.database.getDatabase(app);
        return { session, modules, db: rtdbState.db };
    }

    async function montarPayloadPresencaRtdb(online) {
        const session = rtdbState.session || {};
        const machine = session.machine || {};
        const modules = rtdbState.modules || await carregarFirebaseModules();
        const appVersion = await obterAppVersionPresenca();
        const machineId = String(machine.machine_id || obterMachineIdPresenca() || '').trim();
        const machineKey = chaveFisicaMaquinaPresenca({
            machine_id: machineId,
            machine_key: session.machineKey,
            label: machine.label
        });
        return {
            username: String(session.username || '').trim().toLowerCase(),
            client_id: String(session.client_id || 'default').trim() || 'default',
            machine_id: machineId,
            machine_key: machineKey,
            label: String(machine.label || machineId || 'Maquina').slice(0, 120),
            page: paginaAtualPresenca(),
            app_version: String(appVersion || '').slice(0, 60),
            online: !!online,
            client_updated_at: Date.now(),
            last_seen_ts: modules.database.serverTimestamp()
        };
    }

    function normalizarChaveFisicaPresenca(valor) {
        const texto = String(valor || '').trim();
        if (!texto) return '';
        const macMatch = texto.match(/\bmac\s*:\s*([0-9a-f]{2}(?:[:-][0-9a-f]{2}){5}|[0-9a-f]{12})\b/i);
        if (macMatch) {
            return `mac:${macMatch[1].toLowerCase().replace(/[^0-9a-f]/g, '')}`;
        }
        const pcMatch = texto.match(/\bpc\s*:\s*([^|]+)/i);
        if (pcMatch) {
            const pc = pcMatch[1].trim().toLowerCase().replace(/[^a-z0-9]+/g, '');
            if (pc) return `pc:${pc}`;
        }
        const normalizado = texto.toLowerCase().replace(/[^a-z0-9]+/g, '');
        return normalizado ? `id:${normalizado}` : '';
    }

    function chaveFisicaMaquinaPresenca(machine) {
        if (!machine || typeof machine !== 'object') return '';
        const porMachineId = normalizarChaveFisicaPresenca(machine.machine_id);
        if (porMachineId) return porMachineId;
        const porLabel = normalizarChaveFisicaPresenca(machine.label);
        if (porLabel) return porLabel;
        const key = String(machine.machine_key || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '');
        return key ? `key:${key}` : '';
    }

    function deduplicarMaquinasPresenca(maquinas) {
        const mapa = new Map();
        (Array.isArray(maquinas) ? maquinas : []).forEach((machine) => {
            if (!machine || typeof machine !== 'object') return;
            const key = chaveFisicaMaquinaPresenca(machine);
            if (!key) return;
            const atual = mapa.get(key);
            const item = Object.assign({}, machine, {
                machine_key: key,
                online: machine.online !== false
            });
            if (!atual) {
                mapa.set(key, item);
                return;
            }
            const itemOnline = item.online !== false;
            const atualOnline = atual.online !== false;
            const itemTs = Number(item.last_seen_ts || 0);
            const atualTs = Number(atual.last_seen_ts || 0);
            if ((itemOnline && !atualOnline) || (itemOnline === atualOnline && itemTs >= atualTs)) {
                mapa.set(key, Object.assign({}, atual, item, { current: !!(atual.current || item.current) }));
            } else if (item.current) {
                atual.current = true;
            }
        });
        return Array.from(mapa.values())
            .sort((a, b) => Number(b.last_seen_ts || 0) - Number(a.last_seen_ts || 0));
    }

    function montarUsuarioRtdb(userNode, fallback, currentMachineId) {
        const now = Math.floor(Date.now() / 1000);
        const node = userNode && typeof userNode === 'object' ? userNode : {};
        const connections = node.connections && typeof node.connections === 'object' ? node.connections : {};
        const currentMachineKey = chaveFisicaMaquinaPresenca({ machine_id: currentMachineId });
        const maquinas = deduplicarMaquinasPresenca(Object.values(connections)
            .filter(item => item && typeof item === 'object' && item.online !== false)
            .map((item) => {
                const lastSeen = normalizarTimestampSegundos(item.last_seen_ts || item.client_updated_at);
                const machineId = String(item.machine_id || '').trim();
                const machineKey = chaveFisicaMaquinaPresenca({
                    machine_id: machineId,
                    machine_key: item.machine_key,
                    label: item.label
                });
                return {
                    username: String(item.username || '').trim().toLowerCase(),
                    client_id: String(item.client_id || '').trim(),
                    machine_id: machineId,
                    machine_key: machineKey,
                    label: String(item.label || machineId || 'Maquina'),
                    page: String(item.page || ''),
                    app_version: String(item.app_version || ''),
                    last_seen_at: dataLocalDeTimestamp(lastSeen),
                    last_seen_ts: lastSeen,
                    seconds_since_seen: lastSeen ? Math.max(0, now - lastSeen) : 0,
                    online: true,
                    current: !!(currentMachineKey && machineKey === currentMachineKey)
                };
            }));
        const lastState = node.lastState && typeof node.lastState === 'object' ? node.lastState : {};
        const lastSeen = normalizarTimestampSegundos(lastState.last_seen_ts || node.lastOnline || (maquinas[0] && maquinas[0].last_seen_ts));
        const username = String(
            fallback && fallback.username ||
            lastState.username ||
            (maquinas[0] && maquinas[0].username) ||
            node.username ||
            ''
        ).trim().toLowerCase();
        const clientId = String(
            fallback && fallback.client_id ||
            lastState.client_id ||
            (maquinas[0] && maquinas[0].client_id) ||
            node.client_id ||
            'default'
        ).trim() || 'default';
        return {
            username,
            name: username,
            client_id: clientId,
            online: maquinas.length > 0,
            online_count: maquinas.filter(machine => machine.online !== false).length,
            machines: maquinas.slice(0, 10),
            all_recent_machines: maquinas.slice(0, 20),
            last_seen_at: dataLocalDeTimestamp(lastSeen),
            seconds_since_seen: lastSeen ? Math.max(0, now - lastSeen) : null
        };
    }

    function montarPayloadUsuariosRtdb(rawUsers, session) {
        const usersRoot = rawUsers && typeof rawUsers === 'object' ? rawUsers : {};
        const users = Object.values(usersRoot)
            .map(node => montarUsuarioRtdb(node, null, ''))
            .filter(user => user.username);
        const maquinasUnicas = new Set();
        users.forEach(user => {
            (user.machines || []).forEach(machine => {
                const key = chaveFisicaMaquinaPresenca(machine);
                if (key) maquinasUnicas.add(key);
            });
        });
        users.sort((a, b) => (Number(b.online) - Number(a.online)) || String(a.username).localeCompare(String(b.username)));
        return {
            success: true,
            backend: 'firebase-rtdb',
            realtime: true,
            client_id: session && session.client_id,
            users,
            online_users: users.filter(user => user.online).length,
            online_machines: maquinasUnicas.size || users.reduce((total, user) => total + Number(user.online_count || 0), 0),
            online_timeout_seconds: 0
        };
    }

    async function atualizarConexaoRtdb() {
        if (!rtdbState.connectionRef || !rtdbState.lastStateRef || !rtdbState.modules) return null;
        const payload = await montarPayloadPresencaRtdb(true);
        await rtdbState.modules.database.set(rtdbState.connectionRef, payload);
        await rtdbState.modules.database.set(rtdbState.lastStateRef, payload);
        return { success: true, backend: 'firebase-rtdb' };
    }

    async function iniciarPresencaRtdb() {
        if (!liderPresenca()) {
            agendarTentativaLiderPresenca();
            return null;
        }
        if (rtdbState.disabled) return null;
        if (rtdbState.startPromise) return rtdbState.startPromise;
        rtdbState.startPromise = (async () => {
            const ctx = await prepararRtdb();
            if (!ctx) return null;
            const { session, modules, db } = ctx;
            const userBasePath = `${session.rootPath}/users/${session.userKey}`;
            const connectedRef = modules.database.ref(db, '.info/connected');
            if (rtdbState.unsubscribeConnected) rtdbState.unsubscribeConnected();
            rtdbState.unsubscribeConnected = modules.database.onValue(connectedRef, async (snap) => {
                if (snap.val() !== true) return;
                try {
                    const connectionId = criarConnectionId();
                    const connectionRef = modules.database.ref(db, `${userBasePath}/connections/${connectionId}`);
                    const lastOnlineRef = modules.database.ref(db, `${userBasePath}/lastOnline`);
                    const lastStateRef = modules.database.ref(db, `${userBasePath}/lastState`);
                    rtdbState.connectionRef = connectionRef;
                    rtdbState.lastStateRef = lastStateRef;
                    const onlinePayload = await montarPayloadPresencaRtdb(true);
                    const offlinePayload = Object.assign({}, onlinePayload, {
                        online: false,
                        last_seen_ts: modules.database.serverTimestamp()
                    });
                    await modules.database.onDisconnect(connectionRef).remove();
                    await modules.database.onDisconnect(lastOnlineRef).set(modules.database.serverTimestamp());
                    await modules.database.onDisconnect(lastStateRef).set(offlinePayload);
                    await modules.database.set(lastStateRef, onlinePayload);
                    await modules.database.set(connectionRef, onlinePayload);
                    rtdbState.started = true;
                    iniciarFallbackHeartbeat();
                } catch (_err) {
                    rtdbState.disabled = true;
                    iniciarFallbackHeartbeat();
                }
            }, () => {
                rtdbState.disabled = true;
                iniciarFallbackHeartbeat();
            });
            return { success: true, backend: 'firebase-rtdb' };
        })().catch((err) => {
            rtdbState.disabled = true;
            iniciarFallbackHeartbeat();
            throw err;
        }).finally(() => {
            rtdbState.startPromise = null;
        });
        return rtdbState.startPromise;
    }

    async function desconectarPresencaRtdb() {
        try {
            if (rtdbState.unsubscribeConnected) {
                rtdbState.unsubscribeConnected();
                rtdbState.unsubscribeConnected = null;
            }
            if (rtdbState.connectionRef && rtdbState.modules) {
                await rtdbState.modules.database.remove(rtdbState.connectionRef);
            }
        } catch (_err) {}
        rtdbState.connectionRef = null;
        rtdbState.lastStateRef = null;
        rtdbState.started = false;
    }

    async function enviarHeartbeatBackend(motivo) {
        if (emExecucao || !obterToken() || tokenSessaoExpirado()) return null;
        if (/frontend_index\.html$/i.test(window.location.pathname || '')) return null;
        if (!liderPresenca()) {
            agendarTentativaLiderPresenca();
            return null;
        }
        const agora = Date.now();
        if (agora - ultimoHeartbeat < FALLBACK_HEARTBEAT_INTERVAL_MS) return null;
        emExecucao = true;
        try {
            const payload = {
                machine_id: obterMachineIdPresenca(),
                page: `${window.location.pathname || ''}${window.location.search || ''}`
            };
            const appVersion = await obterAppVersionPresenca();
            if (appVersion) payload.app_version = appVersion;
            const resp = await fetch('/api/user/machines/heartbeat', {
                method: 'POST',
                headers: obterAuthHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(payload),
                keepalive: motivo === 'hidden'
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data && data.success !== false) {
                ultimoHeartbeat = Date.now();
            }
            return data;
        } catch (_err) {
            return null;
        } finally {
            emExecucao = false;
        }
    }

    async function enviarHeartbeat(motivo) {
        if (!obterToken() || tokenSessaoExpirado()) return null;
        if (/frontend_index\.html$/i.test(window.location.pathname || '')) return null;
        if (!liderPresenca()) {
            agendarTentativaLiderPresenca();
            return null;
        }
        if (!rtdbState.disabled) {
            try {
                if (rtdbState.started) return await atualizarConexaoRtdb();
                const result = await iniciarPresencaRtdb();
                if (result) return result;
            } catch (_err) {}
        }
        return enviarHeartbeatBackend(motivo);
    }

    async function buscarMaquinasOnlineBackend() {
        if (!obterToken() || tokenSessaoExpirado()) return null;
        const machineId = obterMachineIdPresenca();
        const url = `/api/user/machines/online?machine_id=${encodeURIComponent(machineId)}`;
        const resp = await fetch(url, {
            method: 'GET',
            headers: obterAuthHeaders(),
            cache: 'no-store'
        });
        return await resp.json().catch(() => ({}));
    }

    async function buscarMaquinasOnlineRtdb() {
        const ctx = await prepararRtdb();
        if (!ctx) return null;
        const { session, modules, db } = ctx;
        const userRef = modules.database.ref(db, `${session.rootPath}/users/${session.userKey}`);
        const snap = await modules.database.get(userRef);
        const machineId = String((session.machine && session.machine.machine_id) || obterMachineIdPresenca() || '').trim();
        const user = montarUsuarioRtdb(snap.val(), session, machineId);
        return {
            success: true,
            backend: 'firebase-rtdb',
            realtime: true,
            username: session.username,
            client_id: session.client_id,
            online_count: Number(user.online_count || 0),
            machines: user.machines || [],
            all_recent_machines: user.all_recent_machines || [],
            online_timeout_seconds: 0
        };
    }

    async function buscarMaquinasOnline(options = {}) {
        if (!obterToken() || tokenSessaoExpirado()) return null;
        if (!liderPresenca()) {
            agendarTentativaLiderPresenca();
            return aguardarPresencaLider('machines');
        }
        if (!options.force && cachePresencaValido(cacheMaquinasOnlineAt)) {
            return cacheMaquinasOnline;
        }
        if (maquinasOnlinePromise) return maquinasOnlinePromise;
        maquinasOnlinePromise = (async () => {
            let data = null;
            if (!rtdbState.disabled) {
                try {
                    data = await buscarMaquinasOnlineRtdb();
                } catch (_err) {}
            }
            if (!data) data = await buscarMaquinasOnlineBackend();
            if (data) publicarPresenca('machines', data);
            return data;
        })().finally(() => {
            maquinasOnlinePromise = null;
        });
        return maquinasOnlinePromise;
    }

    function chaveUsuarioPresenca(user) {
        const username = String(user && user.username || '').trim().toLowerCase();
        const clientId = String(user && user.client_id || 'default').trim() || 'default';
        return username ? `${username}|${clientId}` : '';
    }

    function maquinasUnicasPresenca(users) {
        const maquinas = new Set();
        (Array.isArray(users) ? users : []).forEach((user) => {
            const lista = []
                .concat(Array.isArray(user && user.machines) ? user.machines : [])
                .concat(Array.isArray(user && user.all_recent_machines) ? user.all_recent_machines : []);
            lista.forEach((machine) => {
                if (!machine || machine.online === false) return;
                const key = chaveFisicaMaquinaPresenca(machine);
                if (key) maquinas.add(key);
            });
        });
        return maquinas;
    }

    function mesclarUsuariosOnline(backendPayload, realtimePayload) {
        const backend = backendPayload && typeof backendPayload === 'object' ? backendPayload : {};
        const realtime = realtimePayload && typeof realtimePayload === 'object' ? realtimePayload : {};
        const baseUsers = Array.isArray(backend.users) ? backend.users : [];
        const realtimeUsers = Array.isArray(realtime.users) ? realtime.users : [];
        if (!baseUsers.length && !realtimeUsers.length) return null;

        const realtimePorUsuario = new Map();
        realtimeUsers.forEach((user) => {
            const key = chaveUsuarioPresenca(user);
            if (key) realtimePorUsuario.set(key, user);
        });

        const vistos = new Set();
        const users = baseUsers.map((user) => {
            const key = chaveUsuarioPresenca(user);
            const presenca = realtimePorUsuario.get(key);
            if (key) vistos.add(key);
            if (!presenca) return user;
            const machines = Array.isArray(presenca.machines) && presenca.machines.length
                ? presenca.machines
                : (Array.isArray(user.machines) ? user.machines : []);
            const allRecent = Array.isArray(presenca.all_recent_machines) && presenca.all_recent_machines.length
                ? presenca.all_recent_machines
                : (Array.isArray(user.all_recent_machines) ? user.all_recent_machines : machines);
            const machinesUnicas = deduplicarMaquinasPresenca(machines);
            const recentesUnicas = deduplicarMaquinasPresenca(allRecent);
            return {
                ...user,
                online: !!(presenca.online || user.online),
                online_count: machinesUnicas.filter(machine => machine.online !== false).length || Number(presenca.online_count || user.online_count || 0),
                machines: machinesUnicas,
                all_recent_machines: recentesUnicas.length ? recentesUnicas : machinesUnicas,
                last_seen_at: presenca.last_seen_at || user.last_seen_at || '',
                seconds_since_seen: presenca.seconds_since_seen !== undefined && presenca.seconds_since_seen !== null
                    ? presenca.seconds_since_seen
                    : user.seconds_since_seen
            };
        });

        realtimeUsers.forEach((user) => {
            const key = chaveUsuarioPresenca(user);
            if (!key || vistos.has(key)) return;
            users.push(user);
            vistos.add(key);
        });

        users.sort((a, b) => (Number(b.online) - Number(a.online)) || String(a.name || a.username || '').localeCompare(String(b.name || b.username || '')));
        const onlineUsers = users.filter(user => user && user.online);
        const maquinasOnline = maquinasUnicasPresenca(onlineUsers);
        return {
            ...backend,
            success: true,
            backend: realtimeUsers.length ? `firebase-rtdb+${backend.backend || 'backend'}` : (backend.backend || 'backend'),
            realtime: !!(realtime.realtime || realtimeUsers.length),
            client_id: realtime.client_id || backend.client_id,
            users,
            online_users: onlineUsers.length,
            online_machines: maquinasOnline.size || onlineUsers.reduce((total, user) => total + Number(user.online_count || 0), 0),
            online_timeout_seconds: Number(realtime.online_timeout_seconds || backend.online_timeout_seconds || 0)
        };
    }

    async function buscarUsuariosOnlineBackend() {
        if (!obterToken() || tokenSessaoExpirado()) return null;
        const resp = await fetch('/api/admin/users/online', {
            method: 'GET',
            headers: obterAuthHeaders(),
            cache: 'no-store'
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.success === false) return null;
        return data;
    }

    async function buscarUsuariosOnlineRtdb() {
        const ctx = await prepararRtdb();
        if (!ctx) return null;
        if (
            rtdbState.lastUsersPayload
            && Array.isArray(rtdbState.lastUsersPayload.users)
            && cachePresencaValido(rtdbState.lastUsersPayloadAt, RTDB_USERS_CACHE_TTL_MS)
        ) {
            return rtdbState.lastUsersPayload;
        }
        const usersRef = ctx.modules.database.ref(ctx.db, `${ctx.session.rootPath}/users`);
        const snap = await ctx.modules.database.get(usersRef);
        const payload = montarPayloadUsuariosRtdb(snap.val(), ctx.session);
        rtdbState.lastUsersPayload = payload;
        rtdbState.lastUsersPayloadAt = Date.now();
        return payload;
    }

    async function buscarUsuariosOnline(options = {}) {
        if (!obterToken() || tokenSessaoExpirado()) return null;
        if (!liderPresenca()) {
            agendarTentativaLiderPresenca();
            const payload = await aguardarPresencaLider('users');
            return options.realtimeOnly && !(payload && payload.realtime) ? null : payload;
        }
        if (!options.force && cachePresencaValido(cacheUsuariosOnlineAt) && (!options.realtimeOnly || (cacheUsuariosOnline && cacheUsuariosOnline.realtime))) {
            return cacheUsuariosOnline;
        }
        if (usuariosOnlinePromise) return usuariosOnlinePromise;
        usuariosOnlinePromise = (async () => {
            if (options.realtimeOnly) {
                if (rtdbState.disabled) return null;
                const realtimeData = await buscarUsuariosOnlineRtdb().catch(() => null);
                if (realtimeData) publicarPresenca('users', realtimeData);
                return realtimeData;
            }
            const realtimePromise = !rtdbState.disabled
                ? buscarUsuariosOnlineRtdb().catch(() => null)
                : Promise.resolve(null);
            const backendPromise = buscarUsuariosOnlineBackend().catch(() => null);
            const [realtimeData, backendData] = await Promise.all([realtimePromise, backendPromise]);
            const data = mesclarUsuariosOnline(backendData, realtimeData);
            if (data) publicarPresenca('users', data);
            return data;
        })().finally(() => {
            usuariosOnlinePromise = null;
        });
        return usuariosOnlinePromise;
    }

    async function assinarPresencaUsuarios(onUpdate, onError) {
        if (!liderPresenca()) {
            agendarTentativaLiderPresenca();
            const cached = cachePresenca('users', true);
            if (cached && typeof onUpdate === 'function') {
                setTimeout(() => onUpdate(cached), 0);
            }
            try {
                window.jkTabCoordinator?.broadcast(PRESENCE_REQUEST_CHANNEL, { type: 'users' });
            } catch (_err) {}
            if (window.jkTabCoordinator && typeof window.jkTabCoordinator.subscribe === 'function') {
                return window.jkTabCoordinator.subscribe(PRESENCE_DATA_CHANNEL, (evento) => {
                    if (!evento || evento.type !== 'users') return;
                    atualizarCachePresenca('users', evento.payload);
                    if (typeof onUpdate === 'function') onUpdate(evento.payload);
                });
            }
            throw new Error('Presenca em tempo real indisponivel nesta aba.');
        }
        const ctx = await prepararRtdb();
        if (!ctx) {
            throw new Error('Presenca em tempo real indisponivel.');
        }
        const usersRef = ctx.modules.database.ref(ctx.db, `${ctx.session.rootPath}/users`);
        return ctx.modules.database.onValue(usersRef, (snap) => {
            const payload = montarPayloadUsuariosRtdb(snap.val(), ctx.session);
            rtdbState.lastUsersPayload = payload;
            rtdbState.lastUsersPayloadAt = Date.now();
            publicarPresenca('users', payload);
            if (typeof onUpdate === 'function') onUpdate(payload);
        }, (error) => {
            if (typeof onError === 'function') onError(error);
        });
    }

    async function assinarFavoritosHistoricoRealtime(onUpdate, onError) {
        const ctx = await prepararRtdb();
        if (!ctx || !(ctx.session && ctx.session.rootPath)) {
            throw new Error('Realtime de historico de favoritos indisponivel.');
        }
        const eventRef = ctx.modules.database.ref(ctx.db, `${ctx.session.rootPath}/events/favoritos_historico`);
        return ctx.modules.database.onValue(eventRef, (snap) => {
            const payload = snap.val();
            if (!payload || typeof payload !== 'object' || !payload.event_id) return;
            if (typeof onUpdate === 'function') onUpdate(payload, ctx.session);
        }, (error) => {
            if (typeof onError === 'function') onError(error);
        });
    }

    function iniciarFallbackHeartbeat() {
        if (!obterToken() || tokenSessaoExpirado()) return;
        if (!liderPresenca()) {
            agendarTentativaLiderPresenca();
            return;
        }
        enviarHeartbeatBackend('inicio');
        if (!timer) {
            timer = setInterval(() => enviarHeartbeatBackend('intervalo'), FALLBACK_HEARTBEAT_INTERVAL_MS);
        }
    }

    window.jkEnviarHeartbeatMaquina = enviarHeartbeat;
    window.jkBuscarMaquinasOnline = buscarMaquinasOnline;
    window.jkBuscarUsuariosOnline = buscarUsuariosOnline;
    window.jkAssinarPresencaUsuarios = assinarPresencaUsuarios;
    window.jkAssinarFavoritosHistoricoRealtime = assinarFavoritosHistoricoRealtime;
    window.jkPresencaRealtimeEstado = () => ({
        enabled: !rtdbState.disabled,
        connected: !!rtdbState.started,
        backend: rtdbState.started ? 'firebase-rtdb' : 'fallback'
    });

    function iniciar() {
        if (!obterToken() || tokenSessaoExpirado()) return;
        if (!liderPresenca()) {
            desconectarPresencaRtdb().catch(() => {});
            agendarTentativaLiderPresenca();
            return;
        }
        iniciarPresencaRtdb()
            .then((result) => { if (!result) iniciarFallbackHeartbeat(); })
            .catch(() => iniciarFallbackHeartbeat());
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', iniciar, { once: true });
    } else {
        setTimeout(iniciar, 500);
    }
    setInterval(() => {
        if (!liderPresenca() && (rtdbState.started || timer)) {
            if (timer) clearInterval(timer);
            timer = null;
            desconectarPresencaRtdb().catch(() => {});
            agendarTentativaLiderPresenca();
        }
    }, 15000);
    window.addEventListener('focus', iniciar);
    window.addEventListener('pagehide', () => {
        if (timer) clearInterval(timer);
        timer = null;
        desconectarPresencaRtdb();
        try { presenceLeader?.release(); } catch (_err) {}
    });
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') iniciar();
        if (document.visibilityState === 'hidden' && rtdbState.disabled) enviarHeartbeatBackend('hidden');
    });
})();

(function initDriveBackupAutomatico() {
    if (window.jkCentralManualMode?.()) return;
    if (window.__jkDriveBackupAutoInit) return;
    window.__jkDriveBackupAutoInit = true;

    let executando = false;
    let ultimaExecucao = 0;

    async function executarBackupSeMudou(motivo) {
        if (executando || !obterToken() || tokenSessaoExpirado()) return null;
        if (/frontend_index\.html$/i.test(window.location.pathname || '')) return null;
        if (motivo !== 'manual' && document.visibilityState === 'hidden') return null;
        const agora = Date.now();
        if (motivo !== 'manual' && agora - ultimaExecucao < 30000) return null;
        ultimaExecucao = agora;
        executando = true;
        try {
            const resp = await fetch('/api/drive-sync/backup-if-changed', {
                method: 'POST',
                headers: obterAuthHeaders()
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data && data.action && !['synced', 'not_linked', 'empty'].includes(data.action)) {
                console.info('[Drive Sync]', data.message || data.action);
            }
            return data;
        } catch (err) {
            console.warn('[Drive Sync]', err);
            return null;
        } finally {
            executando = false;
        }
    }

    window.jkDriveBackupNow = () => executarBackupSeMudou('manual');
})();

(function initSharedSyncAutoPull() {
    if (window.jkCentralManualMode?.()) return;
    if (window.__jkSharedSyncAutoPullInit) return;
    window.__jkSharedSyncAutoPullInit = true;

    // Schema 2 e exclusivamente manual. Mantemos somente os nomes publicos
    // para compatibilidade, sem fetch, timer ou listener de sincronizacao.
    window.jkSharedSyncAutoPullNow = async () => ({ success: false, manual_only: true });
    window.jkSharedSyncAutoPushNow = async () => ({ success: false, manual_only: true });
    window.jkFavoritosHistoricoSyncNow = async () => ({ success: false, manual_only: true });
    return;

    let executandoPull = false;
    let executandoPush = false;
    let executandoFavoritosHistorico = false;
    let ultimaPull = 0;
    let ultimaPush = 0;
    let ultimaFavoritosHistorico = Date.now();
    let timerFavoritosHistorico = null;
    let favoritosHistoricoRealtimeUnsubscribe = null;
    let favoritosHistoricoRealtimeUltimoEvento = '';

    const FAVORITOS_HISTORICO_SCOPE = 'favoritos_historico';
    const FAVORITOS_HISTORICO_SYNC_STORAGE_KEY = 'favoritosMlHistoricoSyncEvento';
    const FAVORITOS_HISTORICO_SYNC_CHANNEL = 'jkFavoritosMlHistoricoSync';
    const SHARED_SYNC_AUTO_INTERVAL_MS = 15 * 60 * 1000;
    const SHARED_SYNC_AUTO_START_DELAY_MS = 10 * 60 * 1000;
    const FAVORITOS_HISTORICO_SYNC_INTERVAL_MS = 15 * 60 * 1000;
    const SHARED_SYNC_AUTO_FILES_STORAGE_KEY = 'jk_shared_sync_auto_files_enabled';
    const sharedSyncLeader = window.jkTabCoordinator && typeof window.jkTabCoordinator.createLeader === 'function'
        ? window.jkTabCoordinator.createLeader('shared-sync-auto', { ttlMs: 60000 })
        : null;

    function arquivosSyncAutomaticoAtivo() {
        try {
            return localStorage.getItem(SHARED_SYNC_AUTO_FILES_STORAGE_KEY) === '1';
        } catch (_err) {
            return false;
        }
    }

    function liderSharedSync() {
        return !sharedSyncLeader || sharedSyncLeader.isLeader();
    }

    function telaSeguraParaRestaurar() {
        const path = String(window.location.pathname || '').toLowerCase();
        return !path || path === '/' || /dashboard\.html$|configuracoes\.html$|admin_usuarios\.html$/.test(path);
    }

    function telaPermiteSyncHistoricoFavoritos() {
        const path = String(window.location.pathname || '').toLowerCase();
        return !path || path === '/' || /dashboard\.html$|configuracoes\.html$|admin_usuarios\.html$|favoritos\.html$/.test(path);
    }

    function machineIdAtualSync() {
        try {
            const data = JSON.parse(localStorage.getItem('user_data') || '{}') || {};
            return String(data.machine_id || '').trim();
        } catch (_err) {
            return '';
        }
    }

    function usuarioAtualSync() {
        try {
            return JSON.parse(localStorage.getItem('user_data') || '{}') || {};
        } catch (_err) {
            return {};
        }
    }

    function chaveCacheHistoricoFavoritosSync() {
        const user = usuarioAtualSync();
        const cid = user && user.client_id ? String(user.client_id) : 'default';
        const usuario = user && (user.username || user.email || user.name || user.nome)
            ? String(user.username || user.email || user.name || user.nome)
            : 'usuario';
        const usuarioKey = usuario.trim().toLowerCase().replace(/[^a-z0-9_-]+/gi, '_').slice(0, 60) || 'usuario';
        return `favoritos_ml_historico_${cid}_${usuarioKey}`;
    }

    function resultadosIncluemHistoricoFavoritos(results) {
        return Array.isArray(results) && results.some(item => item && item.scope === FAVORITOS_HISTORICO_SCOPE);
    }

    function resultadosReceberamHistoricoFavoritos(results) {
        return Array.isArray(results) && results.some(item => (
            item &&
            item.scope === FAVORITOS_HISTORICO_SCOPE &&
            item.direction === 'pull' &&
            item.success !== false
        ));
    }

    async function atualizarCacheHistoricoFavoritosSincronizado(results) {
        if (!resultadosReceberamHistoricoFavoritos(results)) return;
        try {
            const resp = await fetch('/api/favoritos/historico', {
                headers: obterAuthHeaders(),
                cache: 'no-store'
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || data.success === false) throw new Error(data.detail || data.message || 'Erro ao recarregar histórico de favoritos.');
            const historico = Array.isArray(data.historico) ? data.historico.slice(0, 30) : [];
            const evento = {
                tipo: 'historico-favoritos-importado',
                ts: Date.now(),
                key: chaveCacheHistoricoFavoritosSync()
            };
            try {
                localStorage.setItem(evento.key, JSON.stringify(historico));
                localStorage.setItem(FAVORITOS_HISTORICO_SYNC_STORAGE_KEY, JSON.stringify(evento));
            } catch (_err) {}
            try {
                if (typeof BroadcastChannel === 'function') {
                    const canal = new BroadcastChannel(FAVORITOS_HISTORICO_SYNC_CHANNEL);
                    canal.postMessage(evento);
                    canal.close();
                }
            } catch (_err) {}
        } catch (error) {
            console.warn('Histórico de favoritos sincronizado, mas o cache local não foi atualizado:', error);
        }
    }

    async function executarAutoPull(motivo) {
        if (!arquivosSyncAutomaticoAtivo()) return null;
        if (executandoPull || !obterToken() || tokenSessaoExpirado()) return null;
        if (!telaSeguraParaRestaurar()) return null;
        if (motivo !== 'manual' && !liderSharedSync()) return null;
        if (motivo !== 'manual' && document.visibilityState === 'hidden') return null;
        const agora = Date.now();
        if (motivo !== 'manual' && agora - ultimaPull < SHARED_SYNC_AUTO_INTERVAL_MS) return null;
        ultimaPull = agora;
        executandoPull = true;
        try {
            const resp = await fetch('/api/shared-sync/auto-pull', {
                method: 'POST',
                headers: obterAuthHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ machine_id: machineIdAtualSync() })
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data && Array.isArray(data.results) && data.results.length) {
                console.info('[Shared Sync] Dados compartilhados restaurados:', data.results);
            }
            return data;
        } catch (err) {
            console.warn('[Shared Sync]', err);
            return null;
        } finally {
            executandoPull = false;
        }
    }

    async function executarAutoPush(motivo) {
        if (!arquivosSyncAutomaticoAtivo()) return null;
        if (executandoPush || !obterToken() || tokenSessaoExpirado()) return null;
        if (!telaSeguraParaRestaurar()) return null;
        if (motivo !== 'manual' && !liderSharedSync()) return null;
        if (motivo !== 'manual' && document.visibilityState === 'hidden') return null;
        const agora = Date.now();
        if (motivo !== 'manual' && agora - ultimaPush < SHARED_SYNC_AUTO_INTERVAL_MS) return null;
        ultimaPush = agora;
        executandoPush = true;
        try {
            const resp = await fetch('/api/shared-sync/user-shares/auto-push', {
                method: 'POST',
                headers: obterAuthHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ machine_id: machineIdAtualSync() })
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data && Array.isArray(data.results) && data.results.length) {
                console.info('[Shared Sync] Dados compartilhados enviados:', data.results);
            }
            return data;
        } catch (err) {
            console.warn('[Shared Sync Push]', err);
            return null;
        } finally {
            executandoPush = false;
        }
    }

    async function executarAutoHistoricoFavoritos(motivo) {
        if (!arquivosSyncAutomaticoAtivo()) return null;
        if (executandoFavoritosHistorico || !obterToken() || tokenSessaoExpirado()) return null;
        if (!telaPermiteSyncHistoricoFavoritos()) return null;
        if (motivo !== 'manual' && !liderSharedSync()) return null;
        if (motivo !== 'manual' && motivo !== 'realtime' && document.visibilityState === 'hidden') return null;
        const agora = Date.now();
        if (motivo !== 'manual' && motivo !== 'realtime' && agora - ultimaFavoritosHistorico < FAVORITOS_HISTORICO_SYNC_INTERVAL_MS) return null;
        ultimaFavoritosHistorico = agora;
        executandoFavoritosHistorico = true;
        try {
            const resp = await fetch('/api/favoritos/historico/realtime-sync', {
                method: 'POST',
                headers: obterAuthHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ machine_id: machineIdAtualSync(), reason: motivo || '' })
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data && Array.isArray(data.results) && data.results.length) {
                if (resultadosIncluemHistoricoFavoritos(data.results)) {
                    console.info('[Favoritos Sync] Histórico sincronizado entre usuários cadastrados:', data.results);
                }
                await atualizarCacheHistoricoFavoritosSincronizado(data.results);
            }
            return data;
        } catch (err) {
            console.warn('[Favoritos Sync]', err);
            return null;
        } finally {
            executandoFavoritosHistorico = false;
        }
    }

    async function iniciarRealtimeHistoricoFavoritos() {
        if (!arquivosSyncAutomaticoAtivo()) return false;
        if (!liderSharedSync()) {
            if (favoritosHistoricoRealtimeUnsubscribe) {
                try { favoritosHistoricoRealtimeUnsubscribe(); } catch (_err) {}
                favoritosHistoricoRealtimeUnsubscribe = null;
            }
            return false;
        }
        if (favoritosHistoricoRealtimeUnsubscribe || typeof window.jkAssinarFavoritosHistoricoRealtime !== 'function') return false;
        if (!obterToken() || tokenSessaoExpirado()) return false;
        try {
            favoritosHistoricoRealtimeUnsubscribe = await window.jkAssinarFavoritosHistoricoRealtime((evento) => {
                const eventId = String(evento && evento.event_id || '');
                if (!eventId || eventId === favoritosHistoricoRealtimeUltimoEvento) return;
                favoritosHistoricoRealtimeUltimoEvento = eventId;
                executarAutoHistoricoFavoritos('realtime');
            }, (error) => {
                favoritosHistoricoRealtimeUnsubscribe = null;
                console.warn('[Favoritos Sync Realtime]', error);
            });
            return true;
        } catch (err) {
            favoritosHistoricoRealtimeUnsubscribe = null;
            console.warn('[Favoritos Sync Realtime]', err);
            return false;
        }
    }

    function agendarAutoHistoricoFavoritos(delayMs) {
        if (!arquivosSyncAutomaticoAtivo()) return;
        if (timerFavoritosHistorico) clearTimeout(timerFavoritosHistorico);
        timerFavoritosHistorico = setTimeout(async () => {
            timerFavoritosHistorico = null;
            await executarAutoHistoricoFavoritos('timer');
            agendarAutoHistoricoFavoritos(FAVORITOS_HISTORICO_SYNC_INTERVAL_MS);
        }, Math.max(5000, Number(delayMs) || FAVORITOS_HISTORICO_SYNC_INTERVAL_MS));
    }

    window.jkSharedSyncAutoPullNow = () => executarAutoPull('manual');
    window.jkSharedSyncAutoPushNow = () => executarAutoPush('manual');
    window.jkFavoritosHistoricoSyncNow = () => executarAutoHistoricoFavoritos('manual');

    if (arquivosSyncAutomaticoAtivo()) {
        agendarAutoHistoricoFavoritos(SHARED_SYNC_AUTO_START_DELAY_MS);
        setTimeout(() => iniciarRealtimeHistoricoFavoritos(), 3500);
    }
    setInterval(() => {
        if (!liderSharedSync() && favoritosHistoricoRealtimeUnsubscribe) {
            try { favoritosHistoricoRealtimeUnsubscribe(); } catch (_err) {}
            favoritosHistoricoRealtimeUnsubscribe = null;
        }
    }, 20000);
    window.addEventListener('pagehide', () => {
        try { sharedSyncLeader?.release(); } catch (_err) {}
    });
    document.addEventListener('visibilitychange', () => {
        if (!arquivosSyncAutomaticoAtivo()) return;
        if (!document.hidden) iniciarRealtimeHistoricoFavoritos();
        if (!document.hidden) setTimeout(() => executarAutoHistoricoFavoritos('visible'), 1200);
    });
})();

(function initMachineSharedSyncAuto() {
    if (window.jkCentralManualMode?.()) return;
    if (window.__jkMachineSharedSyncAutoInit) return;
    window.__jkMachineSharedSyncAutoInit = true;

    let executando = false;
    let ultimaExecucao = 0;
    let machineSyncInitialTimer = null;
    let machineSyncIntervalTimer = null;
    const MACHINE_SHARED_SYNC_AUTO_INTERVAL_MS = 2 * 60 * 1000;
    const MACHINE_SHARED_SYNC_AUTO_START_DELAY_MS = 4000;
    const machineSyncLeader = telaSeguraParaSincronizar()
        && window.jkTabCoordinator
        && typeof window.jkTabCoordinator.createLeader === 'function'
        ? window.jkTabCoordinator.createLeader('machine-shared-sync-auto', { ttlMs: 60000 })
        : null;

    function liderMachineSync() {
        return !machineSyncLeader || machineSyncLeader.isLeader();
    }

    function telaSeguraParaSincronizar() {
        const path = String(window.location.pathname || '').toLowerCase();
        return !path || path === '/' || /dashboard\.html$|configuracoes\.html$|admin_usuarios\.html$/.test(path);
    }

    function machineIdAtualSync() {
        try {
            const data = JSON.parse(localStorage.getItem('user_data') || '{}') || {};
            return String(data.machine_id || '').trim();
        } catch (_err) {
            return '';
        }
    }

    function resultadosPullAplicados(results) {
        return (Array.isArray(results) ? results : []).filter(item => (
            item
            && item.direction === 'pull'
            && item.success !== false
            && item.skipped !== true
        ));
    }

    function dispararAtualizacaoMachineSync(data, pullResults) {
        const detail = {
            source: 'machine-auto-pull',
            results: Array.isArray(data && data.results) ? data.results : pullResults,
            received_scopes: pullResults.map(item => String(item.scope || '')).filter(Boolean),
            updated_at: new Date().toISOString()
        };
        const targets = [window];
        try {
            if (window.parent && window.parent !== window) targets.push(window.parent);
        } catch (_err) {}
        targets.forEach((target) => {
            try {
                target.dispatchEvent(new target.CustomEvent('jk:machine-sync-updated', { detail }));
            } catch (_err) {}
        });
    }

    async function executarMachineSync(motivo) {
        if (executando || !obterToken() || tokenSessaoExpirado()) return null;
        if (/frontend_index\.html$/i.test(window.location.pathname || '')) return null;
        if (!telaSeguraParaSincronizar()) return null;
        if (motivo !== 'manual' && !liderMachineSync()) return null;
        if (motivo !== 'manual' && document.visibilityState === 'hidden') return null;
        const agora = Date.now();
        if (motivo !== 'manual' && agora - ultimaExecucao < MACHINE_SHARED_SYNC_AUTO_INTERVAL_MS) return null;
        ultimaExecucao = agora;
        executando = true;
        try {
            const resp = await fetch('/api/shared-sync/machine-sync/auto', {
                method: 'POST',
                headers: obterAuthHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ machine_id: machineIdAtualSync() })
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || data.success === false) {
                throw new Error(data.detail || data.message || 'Erro ao receber dados das outras maquinas.');
            }
            const pullResults = resultadosPullAplicados(data.results);
            if (pullResults.length) {
                console.info('[Machine Sync] Dados recebidos das outras maquinas:', pullResults);
                dispararAtualizacaoMachineSync(data, pullResults);
            }
            return data;
        } catch (err) {
            console.warn('[Machine Sync]', err);
            return null;
        } finally {
            executando = false;
        }
    }

    function iniciarAgendamentoMachineSync() {
        if (!machineSyncInitialTimer) {
            machineSyncInitialTimer = setTimeout(() => {
                machineSyncInitialTimer = null;
                void executarMachineSync('inicio');
            }, MACHINE_SHARED_SYNC_AUTO_START_DELAY_MS);
        }
        if (!machineSyncIntervalTimer) {
            machineSyncIntervalTimer = setInterval(() => {
                void executarMachineSync('intervalo');
            }, MACHINE_SHARED_SYNC_AUTO_INTERVAL_MS);
        }
    }

    window.jkMachineSyncNow = () => executarMachineSync('manual');
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', iniciarAgendamentoMachineSync, { once: true });
    } else {
        iniciarAgendamentoMachineSync();
    }
    window.addEventListener('focus', () => {
        if (document.visibilityState !== 'hidden') void executarMachineSync('foco');
    });
    window.addEventListener('pageshow', iniciarAgendamentoMachineSync);
    window.addEventListener('pagehide', () => {
        if (machineSyncInitialTimer) clearTimeout(machineSyncInitialTimer);
        if (machineSyncIntervalTimer) clearInterval(machineSyncIntervalTimer);
        machineSyncInitialTimer = null;
        machineSyncIntervalTimer = null;
        try { machineSyncLeader?.release(); } catch (_err) {}
    });
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'hidden') void executarMachineSync('visivel');
    });
})();

/** Remove dados de sessão e redireciona para login. */
