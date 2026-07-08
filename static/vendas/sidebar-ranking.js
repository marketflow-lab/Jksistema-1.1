// Configurar balão de ranking (abre somente no hover dos botões)
const rankingModal = document.getElementById('rankingModal');
const rankingModalList = document.getElementById('rankingModalList');
const rankingModalTitulo = document.querySelector('#rankingModal .ranking-modal-header span');
const graficoSidebarVendas = document.getElementById('graficoSidebarVendas');
const graficoSidebarResizerVendas = document.getElementById('graficoSidebarResizerVendas');
const SIDEBAR_VENDAS_WIDTH_KEY = 'vendas_sidebar_width_px';
const SIDEBAR_VENDAS_MIN_WIDTH = 280;
const SIDEBAR_VENDAS_MAX_WIDTH = 720;
let sidebarVendasResizeState = null;
let rankingHoverHideTimer = null;

function obterLimitesLarguraSidebarVendas() {
    const larguraJanela = Math.max(0, window.innerWidth || document.documentElement.clientWidth || 0);
    const limiteTela = Math.max(SIDEBAR_VENDAS_MIN_WIDTH, larguraJanela - 64);
    return {
        min: SIDEBAR_VENDAS_MIN_WIDTH,
        max: Math.max(SIDEBAR_VENDAS_MIN_WIDTH, Math.min(SIDEBAR_VENDAS_MAX_WIDTH, limiteTela))
    };
}

function normalizarLarguraSidebarVendas(largura) {
    const limites = obterLimitesLarguraSidebarVendas();
    const valor = Number(largura);
    if (!Number.isFinite(valor)) return 320;
    return Math.max(limites.min, Math.min(limites.max, Math.round(valor)));
}

function aplicarLarguraSidebarVendas(largura, salvar = false) {
    if (!graficosContainerVendas) return;
    const larguraFinal = normalizarLarguraSidebarVendas(largura);
    graficosContainerVendas.style.setProperty('--sidebar-vendas-width', `${larguraFinal}px`);
    graficoSidebarResizerVendas?.setAttribute('aria-valuenow', String(larguraFinal));
    if (salvar) {
        try { localStorage.setItem(SIDEBAR_VENDAS_WIDTH_KEY, String(larguraFinal)); } catch (_) {}
    }
}

function iniciarResizeSidebarVendas(event) {
    if (!graficoSidebarVendas || window.matchMedia('(max-width: 980px)').matches) return;
    const ponto = event.touches?.[0] || event;
    sidebarVendasResizeState = {
        startX: ponto.clientX,
        startWidth: graficoSidebarVendas.getBoundingClientRect().width
    };
    document.body.classList.add('sidebar-vendas-resizing');
    document.addEventListener('mousemove', moverResizeSidebarVendas);
    document.addEventListener('mouseup', finalizarResizeSidebarVendas);
    document.addEventListener('touchmove', moverResizeSidebarVendas, { passive: false });
    document.addEventListener('touchend', finalizarResizeSidebarVendas);
    document.addEventListener('touchcancel', finalizarResizeSidebarVendas);
    event.preventDefault();
}

function moverResizeSidebarVendas(event) {
    if (!sidebarVendasResizeState) return;
    const ponto = event.touches?.[0] || event;
    const delta = sidebarVendasResizeState.startX - ponto.clientX;
    aplicarLarguraSidebarVendas(sidebarVendasResizeState.startWidth + delta);
    event.preventDefault();
}

function finalizarResizeSidebarVendas() {
    if (!sidebarVendasResizeState) return;
    sidebarVendasResizeState = null;
    document.body.classList.remove('sidebar-vendas-resizing');
    document.removeEventListener('mousemove', moverResizeSidebarVendas);
    document.removeEventListener('mouseup', finalizarResizeSidebarVendas);
    document.removeEventListener('touchmove', moverResizeSidebarVendas);
    document.removeEventListener('touchend', finalizarResizeSidebarVendas);
    document.removeEventListener('touchcancel', finalizarResizeSidebarVendas);
    aplicarLarguraSidebarVendas(graficoSidebarVendas?.getBoundingClientRect().width || 320, true);
}

function configurarResizeSidebarVendas() {
    if (!graficoSidebarResizerVendas) return;
    const larguraSalva = localStorage.getItem(SIDEBAR_VENDAS_WIDTH_KEY);
    aplicarLarguraSidebarVendas(larguraSalva ? Number(larguraSalva) : 320);
    graficoSidebarResizerVendas.setAttribute('aria-valuemin', String(SIDEBAR_VENDAS_MIN_WIDTH));
    graficoSidebarResizerVendas.setAttribute('aria-valuemax', String(SIDEBAR_VENDAS_MAX_WIDTH));
    graficoSidebarResizerVendas.addEventListener('mousedown', iniciarResizeSidebarVendas);
    graficoSidebarResizerVendas.addEventListener('touchstart', iniciarResizeSidebarVendas, { passive: false });
    graficoSidebarResizerVendas.addEventListener('keydown', (event) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        const atual = graficoSidebarVendas?.getBoundingClientRect().width || 320;
        let proxima = atual;
        if (event.key === 'ArrowLeft') proxima = atual + 24;
        if (event.key === 'ArrowRight') proxima = atual - 24;
        if (event.key === 'Home') proxima = SIDEBAR_VENDAS_MIN_WIDTH;
        if (event.key === 'End') proxima = SIDEBAR_VENDAS_MAX_WIDTH;
        aplicarLarguraSidebarVendas(proxima, true);
        event.preventDefault();
    });
    window.addEventListener('resize', () => aplicarLarguraSidebarVendas(graficoSidebarVendas?.getBoundingClientRect().width || 320, true));
}

configurarResizeSidebarVendas();

const esconderRankingModal = () => {
    if (rankingHoverHideTimer) clearTimeout(rankingHoverHideTimer);
    rankingHoverHideTimer = setTimeout(() => {
        if (!rankingModal?.matches(':hover')
            && !btnRankingVendidosVendas?.matches(':hover')
            && !btnRankingDevolucoesVendas?.matches(':hover')) {
            rankingModal?.classList.remove('ativo');
        }
    }, 120);
};

const posicionarRankingModalAoLado = (botaoAlvo, modo) => {
    if (!rankingModal) return;

    const sidebarRect = graficoSidebarVendas?.getBoundingClientRect();
    const botaoRect = botaoAlvo?.getBoundingClientRect();
    const btnVendidosRect = btnRankingVendidosVendas?.getBoundingClientRect();
    const btnDevolRect = btnRankingDevolucoesVendas?.getBoundingClientRect();
    const larguraModal = rankingModal.offsetWidth || 380;
    const alturaModal = rankingModal.offsetHeight || 420;
    const gap = 12;

    let left;
    let top;

    if (modo === 'devolucoes' && btnDevolRect) {
        // No modo devolucoes, ancora ao lado do botao "Maior devolucao"
        left = btnDevolRect.left - larguraModal - gap;
        // E sobe o balao para ficar por cima da faixa do botao "Mais vendidos"
        top = (btnVendidosRect ? btnVendidosRect.top : btnDevolRect.top) - 18;
    } else {
        left = (sidebarRect ? sidebarRect.left : window.innerWidth) - larguraModal - gap;
        top = botaoRect ? (botaoRect.top - 8) : 120;
    }

    left = Math.max(12, Math.min(left, window.innerWidth - larguraModal - 12));
    top = Math.max(12, Math.min(top, window.innerHeight - alturaModal - 12));

    rankingModal.style.left = `${left}px`;
    rankingModal.style.top = `${top}px`;
};

const mostrarRankingModal = (modo, botaoAlvo) => {
    if (!rankingModal || !rankingModalList) return;
    if (rankingHoverHideTimer) clearTimeout(rankingHoverHideTimer);

    rankingSidebarModo = modo;
    renderRankingSidebarVendas();
    rankingModalList.innerHTML = modo === 'devolucoes'
        ? renderizarRankingModalVendas(rankingDevolTodos, 'devolucoes')
        : renderizarRankingModalVendas(rankingVendidosComDevolTodos, 'vendidos');
    if (rankingModalTitulo) {
        rankingModalTitulo.textContent = modo === 'devolucoes'
            ? '📊 Todos SKUs com devolução - ordem Maior devolução'
            : '📊 Todos SKUs com devolução - ordem Mais vendidos';
    }

    rankingModal.classList.toggle('ranking-modal-devolucoes', modo === 'devolucoes');

    rankingModal.classList.add('ativo');
    posicionarRankingModalAoLado(botaoAlvo, modo);
};

if (btnRankingVendidosVendas) {
    btnRankingVendidosVendas.addEventListener('mouseenter', () => mostrarRankingModal('vendidos', btnRankingVendidosVendas));
    btnRankingVendidosVendas.addEventListener('mouseleave', esconderRankingModal);
    btnRankingVendidosVendas.addEventListener('click', () => {
        rankingSidebarModo = 'vendidos';
        renderRankingSidebarVendas();
    });
}

if (btnRankingDevolucoesVendas) {
    btnRankingDevolucoesVendas.addEventListener('mouseenter', () => mostrarRankingModal('devolucoes', btnRankingDevolucoesVendas));
    btnRankingDevolucoesVendas.addEventListener('mouseleave', esconderRankingModal);
    btnRankingDevolucoesVendas.addEventListener('click', () => {
        rankingSidebarModo = 'devolucoes';
        renderRankingSidebarVendas();
    });
}

if (rankingModal) {
    rankingModal.addEventListener('mouseenter', () => {
        if (rankingHoverHideTimer) clearTimeout(rankingHoverHideTimer);
    });
    rankingModal.addEventListener('mouseleave', esconderRankingModal);
    window.addEventListener('resize', () => {
        if (rankingModal.classList.contains('ativo')) {
            const botaoAtivo = rankingSidebarModo === 'devolucoes' ? btnRankingDevolucoesVendas : btnRankingVendidosVendas;
            posicionarRankingModalAoLado(botaoAtivo, rankingSidebarModo);
        }
    });
}

if (btnOciosos7d) {
    btnOciosos7d.addEventListener('click', () => {
        ociososSidebarModo = '7dias';
        renderOciososSidebarVendas();
    });
}

if (btnOciosos15d) {
    btnOciosos15d.addEventListener('click', () => {
        ociososSidebarModo = '15dias';
        renderOciososSidebarVendas();
    });
}

if (btnOciosos30d) {
    btnOciosos30d.addEventListener('click', () => {
        ociososSidebarModo = '30dias';
        renderOciososSidebarVendas();
    });
}

if (btnOciosos60d) {
    btnOciosos60d.addEventListener('click', () => {
        ociososSidebarModo = '60dias';
        renderOciososSidebarVendas();
    });
}

if (btnOciosos90d) {
    btnOciosos90d.addEventListener('click', () => {
        ociososSidebarModo = '90dias';
        renderOciososSidebarVendas();
    });
}
