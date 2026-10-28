(function () {
    'use strict';

    function start() {
        const button = document.getElementById('buscarMotivos');
        const status = document.getElementById('buscaMotivosStatus');
        const progress = document.getElementById('buscaMotivosProgresso');
        if (!button || !status || !progress) return;
        let timer = null;
        let controller = null;
        let generation = 0;
        let posting = false;
        let stopped = false;
        let previous = null;
        let refreshedAt = 0;
        let refreshPending = false;
        let refreshRevision = 0;
        let refreshing = false;
        let context = tenant();

        function tenant() {
            try { return String(JSON.parse(localStorage.getItem('user_data') || '{}').client_id || ''); }
            catch (_) { return ''; }
        }
        function count(value) { const n = Number(value); return Number.isFinite(n) ? Math.max(0, Math.floor(n)) : 0; }
        function number(value) { return count(value).toLocaleString('pt-BR'); }
        function resetContext() {
            if (context === tenant()) return false;
            context = tenant();
            generation += 1;
            controller?.abort();
            previous = null;
            refreshPending = false;
            refreshRevision += 1;
            posting = false;
            button.disabled = false;
            button.textContent = 'Buscar motivos de todas as devoluções';
            status.textContent = '';
            progress.hidden = true;
            return true;
        }
        function schedule(running) {
            clearTimeout(timer);
            if (!stopped && !document.hidden) timer = setTimeout(poll, running ? 5000 : 30000);
        }
        async function request(method, signal) {
            const path = '/api/notas-entrada/motivos/' + (method === 'POST' ? 'buscar' : 'status');
            const headers = typeof obterAuthHeaders === 'function' ? obterAuthHeaders() : {};
            const options = {method, headers, signal, cache: 'no-store'};
            if (method === 'POST') {
                options.headers = Object.assign({}, headers, {'Content-Type': 'application/json'});
                options.body = '{}';
            }
            const response = await fetch(path, options);
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const data = await response.json();
            if (!data || typeof data !== 'object' || Array.isArray(data)
                || typeof data.running !== 'boolean') throw new Error('invalid_status');
            return data;
        }
        function render(data) {
            const total = count(data.total);
            const processed = Math.min(total, count(data.processed));
            const identified = count(data.identified);
            const reconnect = count(data.reconnect_required);
            button.disabled = posting || data.running;
            button.textContent = data.running ? 'Buscando motivos…' : 'Buscar motivos de todas as devoluções';
            progress.hidden = !data.running;
            progress.max = Math.max(total, 1);
            progress.value = processed;
            let text;
            if (data.phase === 'preparing') {
                text = 'Preparando a busca no histórico das lojas autorizadas. ' + number(total) + ' registros encontrados até agora.';
                progress.removeAttribute('value');
            } else if (data.running) {
                text = 'Busca em andamento: ' + number(processed) + ' de ' + number(total)
                    + ' devoluções consultadas. ' + number(identified) + ' com motivo identificado.';
            } else if (data.phase === 'blocked') {
                text = 'A busca precisa de atenção. ' + number(processed) + ' de ' + number(total)
                    + ' devoluções consultadas; ' + number(identified) + ' com motivo identificado. Verifique as pendências e tente novamente.';
            } else if (data.phase === 'completed') {
                text = 'Busca concluída: ' + number(identified) + ' de ' + number(total)
                    + ' devoluções com motivo identificado.';
                if (total > identified) text += ' ' + number(total - identified) + ' ainda sem motivo confirmado.';
            } else {
                text = 'Busca em todo o histórico com loja identificada e autorizada. Novas devoluções são consultadas automaticamente.';
            }
            if (reconnect) text += ' ' + number(reconnect) + ' dependem de reconectar a integração da loja.';
            if (count(data.retry)) text += ' ' + number(data.retry) + ' aguardam uma nova tentativa automática.';
            if (count(data.skipped)) text += ' ' + number(data.skipped) + ' registros antigos não puderam ser consultados por falta de identificação confirmada.';
            status.textContent = text;
            const changed = previous ? (processed !== count(previous.processed)
                || identified !== count(previous.identified) || previous.running !== data.running)
                : data.phase !== 'idle';
            if (changed) { refreshPending = true; refreshRevision += 1; }
            const finished = previous?.running && !data.running;
            if (refreshPending && !refreshing && (finished || Date.now() - refreshedAt >= 15000)) {
                refreshedAt = Date.now();
                if (typeof window.atualizarMotivosDoRelatorio === 'function') {
                    refreshing = true;
                    const refreshingTenant = context;
                    const refreshingRevision = refreshRevision;
                    Promise.resolve(window.atualizarMotivosDoRelatorio()).then(done => {
                        if (refreshingTenant === context && refreshingRevision === refreshRevision && done) refreshPending = false;
                    }).catch(() => {}).finally(() => { refreshing = false; });
                }
            }
            previous = data;
        }
        async function run(method) {
            resetContext();
            const currentTenant = context;
            const currentGeneration = ++generation;
            controller?.abort();
            const active = new AbortController();
            controller = active;
            const timeout = setTimeout(() => active.abort(), 20000);
            try {
                const data = await request(method, active.signal);
                if (stopped || generation !== currentGeneration || currentTenant !== tenant()) return;
                render(data);
            } catch (_) {
                if (stopped || generation !== currentGeneration || currentTenant !== tenant()) return;
                status.textContent = method === 'POST'
                    ? 'Não foi possível confirmar o início da busca. Verificando o andamento…'
                    : 'Não foi possível consultar o andamento. A busca salva continuará quando o serviço estiver disponível.';
            } finally {
                clearTimeout(timeout);
                if (generation === currentGeneration) {
                    controller = null;
                    if (method === 'POST') posting = false;
                    button.disabled = posting || Boolean(previous?.running);
                    if (!button.disabled) button.textContent = 'Buscar motivos de todas as devoluções';
                    schedule(Boolean(previous?.running));
                }
            }
        }
        async function poll() {
            if (!posting && !stopped && !document.hidden) await run('GET');
        }
        button.addEventListener('click', async () => {
            resetContext();
            if (posting || previous?.running) return;
            posting = true;
            button.disabled = true;
            button.textContent = 'Iniciando busca…';
            clearTimeout(timer);
            await run('POST');
            if (!stopped) await poll();
        });
        document.addEventListener('visibilitychange', () => {
            clearTimeout(timer);
            if (!document.hidden) void poll();
        });
        window.addEventListener('storage', event => {
            if (event.key === 'user_data') { resetContext(); void poll(); }
        });
        window.addEventListener('pagehide', () => {
            stopped = true;
            generation += 1;
            clearTimeout(timer);
            controller?.abort();
        });
        window.addEventListener('pageshow', event => {
            if (event.persisted) { stopped = false; resetContext(); void poll(); }
        });
        void poll();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
    else start();
})();
