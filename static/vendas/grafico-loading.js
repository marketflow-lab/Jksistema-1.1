/* Indicador compartilhado pelos dois layouts, seguindo a leitura do escopo vigente. */
(() => {
    'use strict';

    const wrapper = document.getElementById('graficoVendasWrapper');
    const overlay = document.getElementById('graficoVendasLoading');
    const scopeLabel = document.getElementById('graficoVendasLoadingScope');
    const bridge = window.JKVendasLegacyBridge;
    if (!wrapper || !overlay || !scopeLabel || !bridge) return;
    let epoch = window.JKVendasReadState?.epoch;
    let awaitingRead = false;

    function formatDate(value) {
        return String(value || '').replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$3/$2/$1');
    }

    function render(snapshot = bridge.getSnapshot()) {
        const readState = window.JKVendasReadState;
        const filters = snapshot.filters || {};
        const read = readState?.get('grafico');
        if (epoch !== readState?.epoch) {
            epoch = readState?.epoch;
            awaitingRead = true;
        }
        if (read?.state === 'loading') awaitingRead = false;
        const busy = !readState?.isClosed() && !snapshot.graphError
            && Boolean(filters.inicio && filters.fim)
            && (read?.state === 'loading' || (!awaitingRead && (snapshot.graphLoading || snapshot.graphUpdating)));
        const label = [
            !filters.loja || filters.loja === '__todas' ? 'Todas as lojas' : filters.loja,
            `${formatDate(filters.inicio)} a ${formatDate(filters.fim)}`
        ].join(' · ');
        if (scopeLabel.textContent !== label) scopeLabel.textContent = label;
        wrapper.setAttribute('aria-busy', String(Boolean(busy)));
        overlay.hidden = !busy;
    }

    bridge.subscribe(render);
    window.addEventListener('jk:vendas-read-state', () => render());
})();
