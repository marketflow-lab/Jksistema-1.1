function parametrosVendasFull(dataInicio, dataFim, conta = '') {
    const params = new URLSearchParams();
    params.set('data_inicio', dataInicio);
    params.set('data_fim', dataFim);
    params.set('resolver_nf', 'false');
    params.set('unidade_negocio', '__ml_loja_full');
    const contaSelecionada = conta || (fullSalesAccountEl ? String(fullSalesAccountEl.value || '') : '');
    if (contaSelecionada && contaSelecionada !== CONTA_TODAS_FULL) params.set('loja', contaSelecionada);
    return params;
}

async function buscarVendasFull(dataInicio, dataFim, conta = '', force = false) {
    const params = parametrosVendasFull(dataInicio, dataFim, conta);
    const hoje = hojeISO();
    const ttlMs = dataInicio <= hoje && dataFim >= hoje ? FULL_CACHE_TTL_VENDAS_HOJE_MS : FULL_CACHE_TTL_HISTORICO_MS;
    const payload = await fetchJsonFullCached(
        chaveCacheFull('vendas', [params.toString()]),
        `/api/vendas?${params.toString()}`,
        { ttlMs, force }
    );
    return Array.isArray(payload) ? payload : [];
}

function totalVendido(lista) {
    return lista.reduce((acc, row) => acc + numero(row.valor), 0);
}

function totalPedidos(lista) {
    const ids = new Set();
    lista.forEach(row => {
        const id = String(primeiro(row, ['numero', 'pedido', 'id_pedido', 'id']) || '').trim();
        if (id) ids.add(id);
    });
    return ids.size || lista.length;
}

function horaVenda(row) {
    const dataRaw = String(primeiro(row, ['data', 'data_venda', 'created_at']) || '').trim();
    const matchHora = dataRaw.match(/\b(\d{1,2}):\d{2}/);
    if (matchHora) {
        const h = Number(matchHora[1]);
        return Number.isFinite(h) ? Math.max(0, Math.min(23, h)) : 0;
    }
    const d = new Date(dataRaw);
    if (!Number.isNaN(d.getTime())) return d.getHours();
    return 0;
}

function serieHoraria(lista) {
    const valores = Array.from({ length: 24 }, () => 0);
    lista.forEach(row => {
        valores[horaVenda(row)] += numero(row.valor);
    });
    return valores;
}

function percentualDiaDecorrido() {
    const agora = new Date();
    const minutos = (agora.getHours() * 60) + agora.getMinutes() + (agora.getSeconds() / 60);
    return Math.max(1, Math.min(100, (minutos / 1440) * 100));
}

function variacaoPercentual(atual, anterior) {
    if (!Number(anterior)) return null;
    return ((Number(atual || 0) - Number(anterior || 0)) / Math.abs(Number(anterior))) * 100;
}

function renderPercentual(el, valor) {
    if (!el) return;
    if (valor === null || !Number.isFinite(valor)) {
        el.textContent = 'Sem base';
        el.classList.remove('up', 'down');
        return;
    }
    const classe = valor >= 0 ? 'up' : 'down';
    el.textContent = `${valor >= 0 ? '+' : ''}${valor.toFixed(1)}%`;
    el.classList.toggle('up', classe === 'up');
    el.classList.toggle('down', classe === 'down');
}

function renderGraficoVendasFull(hojeValores, ontemValores) {
    const canvas = document.getElementById('fullSalesChart');
    if (!canvas || typeof Chart === 'undefined') return;
    const ctx = canvas.getContext('2d');
    if (vendasFullChart) vendasFullChart.destroy();
    vendasFullChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, '0')}:00`),
            datasets: [
                {
                    label: 'Hoje',
                    data: hojeValores,
                    borderColor: '#45c7c9',
                    backgroundColor: 'rgba(69, 199, 201, 0.12)',
                    tension: 0.35,
                    fill: true,
                    pointRadius: 3,
                    pointHoverRadius: 5
                },
                {
                    label: 'Ontem',
                    data: ontemValores,
                    borderColor: '#b79cff',
                    backgroundColor: 'rgba(183, 156, 255, 0.1)',
                    borderDash: [5, 5],
                    tension: 0.35,
                    fill: false,
                    pointRadius: 3,
                    pointHoverRadius: 5
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { labels: { color: '#405276', usePointStyle: true } },
                tooltip: {
                    callbacks: {
                        label: context => `${context.dataset.label}: ${formatarMoeda(context.parsed.y || 0)}`
                    }
                }
            },
            scales: {
                x: { ticks: { color: '#5d6885', maxRotation: 50 }, grid: { color: '#e1e5ef' } },
                y: {
                    beginAtZero: true,
                    ticks: { color: '#5d6885', callback: value => formatarMoeda(value) },
                    grid: { color: '#e1e5ef' }
                }
            }
        }
    });
}
