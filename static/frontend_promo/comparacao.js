// Apresentação da análise: usa os valores e os controles canônicos, sem recalcular decisões.
let promoViewMode = 'comparacao';
const promoComparisonCards = new Map();

function comparacaoPromocoesAtiva() {
    return !!document.getElementById('promoComparison') && promoViewMode === 'comparacao';
}

function promoElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
}

function promoDisplayValue(row, key) {
    let value = getTableCellValue(row, key);
    if (key === '%' && (value === null || value === undefined || String(value).trim() === '')) {
        value = resolvePromoPercentDisplay(row);
    }
    const text = String(value ?? '').trim();
    return text || 'Não informado';
}

function promoProductPhotoUrl(row) {
    const value = row?.thumbnail || row?.secure_thumbnail;
    if (typeof value !== 'string' || !value.trim() || value.length > 2048) return '';
    try {
        const url = new URL(value.trim());
        if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.port) return '';
        if (!/(^|\.)mlstatic\.com$/i.test(url.hostname)) return '';
        url.protocol = 'https:';
        return url.href;
    } catch (_error) { return ''; }
}

function criarFotoComparacaoPromocoes(row) {
    const photo = promoElement('div', 'promo-ad-photo');
    const placeholder = promoElement('span', 'promo-photo-placeholder', 'Sem foto');
    const url = promoProductPhotoUrl(row);
    if (!url) {
        photo.classList.add('is-missing');
        photo.appendChild(placeholder);
        return photo;
    }
    const image = promoElement('img', 'promo-ad-image');
    image.alt = 'Foto de ' + promoDisplayValue(row, 'Título');
    image.width = 64;
    image.height = 64;
    image.loading = 'lazy';
    image.decoding = 'async';
    image.referrerPolicy = 'no-referrer';
    placeholder.hidden = true;
    image.addEventListener('error', () => {
        image.remove();
        placeholder.hidden = false;
        photo.classList.add('is-missing');
    }, { once: true });
    image.src = url;
    photo.append(image, placeholder);
    return photo;
}

function promoNumericValue(raw) {
    if (typeof raw === 'number') return Number.isFinite(raw) ? raw : null;
    const original = String(raw ?? '').trim();
    const money = /^R\$/.test(original);
    let value = original.replace(/^R\$\s*/, '').replace(/%$/, '').replace(/\s/g, '');
    if (!/^[+-]?\d[\d.,]*$/.test(value)) return null;
    if (value.includes(',')) {
        if (!/^[+-]?(?:\d+|\d{1,3}(?:\.\d{3})+),\d+$/.test(value)) return null;
        value = value.replace(/\./g, '').replace(',', '.');
    } else if (money && /^[+-]?\d{1,3}(?:\.\d{3})+$/.test(value)) {
        value = value.replace(/\./g, '');
    } else if (!/^[+-]?\d+(?:\.\d+)?$/.test(value)) return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

function preencherValorComparacaoPromocoes(element, row, key) {
    const value = promoDisplayValue(row, key);
    if (key === 'Margem' || key === 'Margem ML') {
        element.classList.remove('margin-low', 'margin-mid', 'margin-high', 'margin-top');
        const percent = promoNumericValue(value);
        if (percent !== null) {
            element.classList.add(percent < 10 ? 'margin-low' : percent < 13 ? 'margin-mid' : percent < 15 ? 'margin-high' : 'margin-top');
        }
    }
    const reason = obterEstimativaCelulaPromocoes(row, key);
    element.replaceChildren();
    element.appendChild(document.createTextNode(value));
    element.title = reason;
    if (reason && value !== 'Não informado') {
        const badge = promoElement('span', 'promo-approx', '≈');
        badge.tabIndex = 0;
        badge.title = reason;
        badge.setAttribute('aria-label', `${key}: ${value}. Estimado. ${reason}`);
        element.prepend(badge);
    }
}

function promoBoundValue(row, key, className = 'promo-value-number') {
    const value = promoElement('strong', className);
    value.dataset.promoDisplayKey = key;
    preencherValorComparacaoPromocoes(value, row, key);
    return value;
}

function preencherDiferencaComparacaoPromocoes(element, row, key, baseKey) {
    const before = promoNumericValue(getTableCellValue(row, baseKey));
    const after = promoNumericValue(getTableCellValue(row, key));
    element.className = 'promo-delta';
    if (before === null || after === null) {
        element.textContent = '—';
        element.title = 'Diferença indisponível: os dois valores precisam estar informados.';
        return;
    }
    const difference = Math.round((after - before) * 100) / 100;
    const estimated = !!(obterEstimativaCelulaPromocoes(row, key) || obterEstimativaCelulaPromocoes(row, baseKey));
    const absolute = Math.abs(difference).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const sign = difference > 0 ? '+' : difference < 0 ? '−' : '';
    const formatted = key === 'Margem ML' ? `${sign}${absolute} p.p.` : `${sign}R$ ${absolute}`;
    element.textContent = `${estimated ? '≈ ' : ''}${formatted}`;
    element.title = `Variação em relação à Promoção 1${estimated ? ' calculada com valor estimado' : ''}.`;
    if (key !== 'Preço Final ML' && difference !== 0) element.classList.add(difference > 0 ? 'is-positive' : 'is-negative');
    if (estimated) element.classList.add('is-estimated');
}

function promoCampaignMetrics(row, second) {
    const section = promoElement('div', `promo-campaign ${second ? 'promo-two' : 'promo-one'}`);
    section.appendChild(promoElement('div', 'promo-mobile-campaign', second ? 'Promoção 2 · comparação' : 'Promoção 1 · referência'));
    const main = promoElement('div', 'promo-values');
    const fields = second
        ? [['Preço Final ML', 'Preço final', 'Preço Final'], ['Valor líquido ML', 'Valor líquido', 'Valor Líquido'], ['Margem ML', 'Margem', 'Margem']]
        : [['Preço Final', 'Preço final'], ['Valor Líquido', 'Valor líquido'], ['Margem', 'Margem']];
    fields.forEach(([key, label, baseKey]) => {
        const cell = promoElement('div', 'promo-value');
        cell.appendChild(promoElement('span', 'promo-value-label', label));
        cell.appendChild(promoBoundValue(row, key));
        const delta = promoElement('small', 'promo-delta');
        if (second) {
            delta.dataset.promoDeltaKey = key;
            delta.dataset.promoBaseKey = baseKey;
            preencherDiferencaComparacaoPromocoes(delta, row, key, baseKey);
        } else {
            delta.textContent = '\u00a0';
            delta.setAttribute('aria-hidden', 'true');
        }
        cell.appendChild(delta);
        main.appendChild(cell);
    });
    section.appendChild(main);
    const full = promoElement('div', 'promo-full');
    const expenses = promoElement('div', 'promo-expenses');
    const expenseFields = second ? [['Tarifa ML', 'Tarifa'], ['Frete ML', 'Frete'], ['Imposto ML', 'Imposto']]
        : [['Tarifa', 'Tarifa'], ['Frete', 'Frete'], ['Imposto', 'Imposto']];
    expenseFields.forEach(([key, label]) => {
        const cell = promoElement('div', 'promo-expense');
        cell.appendChild(promoElement('span', '', label));
        cell.appendChild(promoBoundValue(row, key, 'promo-expense-value'));
        expenses.appendChild(cell);
    });
    full.appendChild(expenses);
    const conditions = promoElement('div', 'promo-conditions');
    const lines = second
        ? [[['ML % Campanha', 'Desconto'], ['Frete Gratis ML', 'Frete grátis ao comprador']], [['Desconto ML', 'Benefício ML na tarifa']]]
        : [[['% Fixa', 'Desconto'], ['Frete Gratis', 'Frete grátis ao comprador']]];
    lines.forEach(fieldsLine => {
        const line = promoElement('div', 'promo-condition-line');
        fieldsLine.forEach(([key, label]) => {
            const part = promoElement('span', 'promo-condition');
            part.appendChild(document.createTextNode(`${label}: `));
            part.appendChild(promoBoundValue(row, key, 'promo-condition-value'));
            line.appendChild(part);
        });
        conditions.appendChild(line);
    });
    full.appendChild(conditions);
    section.appendChild(full);
    return section;
}

function criarIdentificacaoComparacaoPromocoes(row) {
    const ad = promoElement('div', 'promo-ad');
    const main = promoElement('div', 'promo-ad-main');
    const identity = promoElement('div', 'promo-ad-identity');
    main.appendChild(criarFotoComparacaoPromocoes(row));
    identity.appendChild(promoElement('h3', 'promo-ad-title', promoDisplayValue(row, 'Título')));
    const ids = promoElement('div', 'promo-ad-ids');
    [['SKU', 'SKU'], ['MLB', 'MLB']].forEach(([key, label]) => {
        const part = promoElement('span');
        part.appendChild(promoElement('small', '', `${label} `));
        part.appendChild(promoBoundValue(row, key, 'promo-id-value'));
        ids.appendChild(part);
    });
    identity.appendChild(ids);
    main.appendChild(identity);
    ad.appendChild(main);
    const full = promoElement('div', 'promo-full');
    const meta = promoElement('div', 'promo-ad-meta');
    meta.appendChild(promoBoundValue(row, 'Tipo', 'promo-listing-type'));
    const status = promoElement('span');
    status.appendChild(document.createTextNode('P1: '));
    status.appendChild(promoBoundValue(row, 'Status', 'promo-p1-status'));
    meta.appendChild(status);
    const saleRate = promoElement('span');
    saleRate.appendChild(document.createTextNode('Taxa de venda: '));
    saleRate.appendChild(promoBoundValue(row, '%', 'promo-condition-value'));
    meta.appendChild(saleRate);
    full.appendChild(meta);
    const cost = promoElement('div', 'promo-ad-cost');
    cost.appendChild(document.createTextNode('Custo '));
    cost.appendChild(promoBoundValue(row, 'Custo', 'promo-cost-value'));
    const tax = promoElement('span', 'promo-tax-rate');
    tax.appendChild(document.createTextNode('Alíquota: '));
    tax.appendChild(promoBoundValue(row, 'Imposto %', 'promo-condition-value'));
    cost.appendChild(tax);
    full.appendChild(cost);
    ad.appendChild(full);
    return ad;
}

function criarDecisaoComparacaoPromocoes(row, tableRow) {
    const decision = promoElement('div', 'promo-decision');
    const top = promoElement('div', 'promo-decision-top');
    const control = promoElement('div', 'promo-action-control');
    const actionCell = Array.from(tableRow.children).find(td => td.dataset.promoColumnKey === 'Ação');
    const select = actionCell?.querySelector('select');
    if (select) {
        select.setAttribute('aria-label', `Ação para ${promoDisplayValue(row, 'MLB')}`);
        control.appendChild(select);
    }
    top.appendChild(control);
    const ficha = promoElement('button', 'promo-ficha', 'Ficha');
    ficha.type = 'button';
    ficha.setAttribute('aria-label', `Ficha completa de ${promoDisplayValue(row, 'Título')}`);
    ficha.addEventListener('click', () => abrirFichaComparacaoPromocoes(row));
    top.appendChild(ficha);
    decision.appendChild(top);
    if (actionCell) Array.from(actionCell.children).forEach(child => decision.appendChild(child));
    const info = promoElement('div', 'promo-offer-info promo-full');
    info.appendChild(promoElement('span', 'promo-p2-status', `P2: ${row.action_ja_participa === true ? 'Já participa' : 'Participação não confirmada'}`));
    const campaign = Array.from(tableRow.children).find(td => td.dataset.promoColumnKey === 'Campanha ML');
    if (isLinhaOfertaRelampago(row)) {
        const windowLabel = campaign?.querySelector('.promo-lightning-window');
        info.appendChild(windowLabel || promoElement('small', 'promo-lightning-window', formatarJanelaOfertaRelampago(row)));
        const stock = campaign?.querySelector('.promo-lightning-stock');
        if (stock) {
            info.appendChild(stock);
            info.appendChild(promoElement('small', 'promo-stock-limits', `Limites: ${row.lightning_stock_min} a ${row.lightning_stock_max} un.`));
        } else if (obterEstoqueReservadoRelampago(row) !== null) {
            info.appendChild(promoElement('span', 'promo-stock-limits', `Estoque reservado: ${row.lightning_stock} un.`));
        }
    } else {
        info.appendChild(promoBoundValue(row, 'Campanha ML', 'promo-row-campaign'));
    }
    decision.appendChild(info);
    return decision;
}

function renderComparacaoPromocoes(data) {
    const rows = document.getElementById('promoComparisonRows');
    if (!rows) return;
    rows.replaceChildren();
    promoComparisonCards.clear();
    const tableRows = document.querySelectorAll('#tabelaAnalise tbody tr');
    const fragment = document.createDocumentFragment();
    (Array.isArray(data) ? data : []).forEach((row, index) => {
        const article = promoElement('article', 'promo-comparison-row');
        article.dataset.originalIndex = String(index);
        if (row.analysis_client_ref) article.dataset.analysisClientRef = String(row.analysis_client_ref);
        if (row.lightning_offer_key) article.dataset.lightningOfferKey = String(row.lightning_offer_key);
        article.setAttribute('aria-label', `Anúncio ${promoDisplayValue(row, 'MLB')}`);
        article.appendChild(criarIdentificacaoComparacaoPromocoes(row));
        article.appendChild(promoCampaignMetrics(row, false));
        article.appendChild(promoCampaignMetrics(row, true));
        article.appendChild(criarDecisaoComparacaoPromocoes(row, tableRows[index]));
        promoComparisonCards.set(row, article);
        fragment.appendChild(article);
    });
    rows.appendChild(fragment);
    atualizarCabecalhoComparacaoPromocoes();
    aplicarFiltrosComparacaoPromocoes();
    aplicarDensidadeComparacaoPromocoes();
}

function atualizarComparacaoFinanceiraPromocoes(row) {
    const article = promoComparisonCards.get(row);
    if (!article) return;
    article.querySelectorAll('[data-promo-display-key]').forEach(element => {
        preencherValorComparacaoPromocoes(element, row, element.dataset.promoDisplayKey);
    });
    article.querySelectorAll('[data-promo-delta-key]').forEach(element => {
        preencherDiferencaComparacaoPromocoes(element, row, element.dataset.promoDeltaKey, element.dataset.promoBaseKey);
    });
    const reason = article.querySelector('.promo-decision-reason:not(.promo-decision-technical)');
    if (reason) {
        reason.textContent = obterMotivoSugestaoPromocoes(row);
        reason.style.display = reason.textContent ? 'block' : 'none';
    }
    const status = article.querySelector('.promo-p2-status');
    if (status) status.textContent = `P2: ${row.action_ja_participa === true ? 'Já participa' : 'Participação não confirmada'}`;
    const warning = article.querySelector('.promo-decision-technical');
    if (warning) {
        warning.textContent = obterAvisoParticipacaoPromocoes(row);
        warning.style.display = warning.textContent ? 'block' : 'none';
    }
}

function promoSearchText(text) {
    return String(text || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}

function aplicarFiltrosComparacaoPromocoes() {
    const search = promoSearchText(document.getElementById('promoSearch')?.value).trim();
    const filter = document.getElementById('promoDecisionFilter')?.value || 'todos';
    let shown = 0;
    promoComparisonCards.forEach((article, row) => {
        const match = !search || ['Título', 'SKU', 'MLB'].some(key => promoSearchText(getTableCellValue(row, key)).includes(search));
        const actionMatch = filter === 'todos' || (filter === 'participar') === isDecisaoParticipar(obterDecisaoPromocoes(row));
        article.hidden = !(match && actionMatch);
        if (!article.hidden) shown += 1;
    });
    const count = document.getElementById('promoVisibleCount');
    if (count) count.textContent = `${shown} de ${promoComparisonCards.size} anúncios na tela`;
    const empty = document.getElementById('promoFilterEmpty');
    if (empty) empty.hidden = shown > 0 || promoComparisonCards.size === 0;
}

function aplicarDensidadeComparacaoPromocoes() {
    const wrap = document.getElementById('promoComparison');
    if (!wrap) return;
    const short = document.getElementById('promoDensity')?.value === 'resumido';
    wrap.classList.toggle('resumido', short);
    wrap.classList.toggle('completo', !short);
    wrap.classList.toggle('sem-diferencas', document.getElementById('promoDifferences')?.checked === false);
}

function atualizarCabecalhoComparacaoPromocoes() {
    const referenceSelect = document.getElementById('apiPromoASelect');
    const active = Array.isArray(apiAnalisesPorCampanha) ? apiAnalisesPorCampanha[apiAnaliseAtiva] : null;
    let referenceId = '';
    try { referenceId = JSON.parse(active?.participacao_contexto || '{}').promoA || ''; } catch (_error) {}
    const reference = referenceId
        ? Array.from(referenceSelect?.options || []).find(option => option.value === referenceId)
        : referenceSelect?.selectedOptions?.[0];
    const campaign = active ? findApiPromoBCampaignById(active.promo_b_id, active.selection_id) : null;
    const p1 = document.getElementById('promoReferenceName');
    const p2 = document.getElementById('promoComparisonName');
    if (p1) {
        const label = reference?.textContent || referenceId || 'Referência';
        const metadata = label.lastIndexOf(' - ');
        p1.textContent = metadata >= 0 ? label.slice(0, metadata) : label;
        p1.title = label;
    }
    if (p2) p2.textContent = campaign ? getPromoCampaignDisplayName(campaign) : active?.promo_b_nome || 'Campanha selecionada';
    const selection = document.getElementById('promoCampaignSelectionLabel');
    if (selection) {
        const selected = getApiPromoBSelections();
        selection.textContent = selected.length === 1 ? (selected[0].promoType === 'LIGHTNING'
            ? getPromoCampaignDisplayName({ type: 'LIGHTNING', lightning_window: selected[0].lightningWindow }) : selected[0].matchName) || '1 campanha selecionada'
            : `${selected.length} campanhas selecionadas`;
    }
}

function alternarVisualizacaoPromocoes(mode) {
    const wrap = document.getElementById('promoComparison');
    const legacy = document.getElementById('promoLegacyTableWrap');
    if (!wrap || !legacy) return;
    if (promoViewMode === 'planilha') saveColumnWidthPrefsFromDomRobust(true);
    promoViewMode = mode === 'planilha' ? 'planilha' : 'comparacao';
    const compact = comparacaoPromocoesAtiva();
    legacy.hidden = compact;
    wrap.hidden = !compact;
    document.getElementById('promoComparisonFilters').hidden = !compact;
    document.querySelectorAll('[data-promo-view]').forEach(button => {
        button.setAttribute('aria-pressed', String(button.dataset.promoView === promoViewMode));
    });
    if (compact) document.getElementById('columnsPanel').style.display = 'none';
    if (currentData.length) renderTable(currentData);
    else {
        document.getElementById('promoComparisonRows').replaceChildren();
        promoComparisonCards.clear();
        atualizarCabecalhoComparacaoPromocoes();
        aplicarFiltrosComparacaoPromocoes();
    }
}

function configurarColunasComparacaoPromocoes() {
    alternarVisualizacaoPromocoes('planilha');
    toggleColumnsPanel();
}

function abrirFichaComparacaoPromocoes(row) {
    const dialog = document.getElementById('promoDetailsDialog');
    if (!dialog) return;
    document.getElementById('promoDetailsTitle').textContent = promoDisplayValue(row, 'Título');
    const details = document.getElementById('promoDetailsFields');
    details.replaceChildren();
    TABLE_COLUMNS_MODERN.forEach(column => {
        const term = promoElement('dt', '', column.label);
        const definition = promoElement('dd');
        definition.appendChild(promoBoundValue(row, column.key, 'promo-detail-value'));
        details.append(term, definition);
    });
    if (isLinhaOfertaRelampago(row)) {
        details.append(promoElement('dt', '', 'Janela da oferta'), promoElement('dd', '', formatarJanelaOfertaRelampago(row)));
        details.append(promoElement('dt', '', 'Estoque reservado'), promoElement('dd', '', row.lightning_stock == null ? 'Não informado' : String(row.lightning_stock)));
    }
    const reason = obterMotivoSugestaoPromocoes(row) || obterAvisoParticipacaoPromocoes(row);
    if (reason) details.append(promoElement('dt', '', 'Observação'), promoElement('dd', '', reason));
    if (!dialog.open) dialog.showModal();
}

function inicializarComparacaoPromocoes() {
    if (!document.getElementById('promoComparison')) return;
    document.querySelectorAll('[data-promo-view]').forEach(button => {
        button.addEventListener('click', () => alternarVisualizacaoPromocoes(button.dataset.promoView));
    });
    ['promoSearch', 'promoDecisionFilter'].forEach(id => document.getElementById(id).addEventListener('input', aplicarFiltrosComparacaoPromocoes));
    ['promoDensity', 'promoDifferences'].forEach(id => document.getElementById(id).addEventListener('change', aplicarDensidadeComparacaoPromocoes));
    document.getElementById('promoComparisonRows').addEventListener('change', aplicarFiltrosComparacaoPromocoes);
    document.getElementById('apiPromoASelect').addEventListener('change', atualizarCabecalhoComparacaoPromocoes);
    const selector = document.getElementById('apiPromoBSelect');
    const picker = selector.closest('.promo-campaign-picker');
    document.addEventListener('click', event => {
        if (picker?.open && !picker.contains(event.target)) picker.open = false;
    }, true);
    new MutationObserver(atualizarCabecalhoComparacaoPromocoes).observe(selector, { childList: true, subtree: true, characterData: true });
    document.getElementById('promoDetailsClose').addEventListener('click', () => document.getElementById('promoDetailsDialog').close());
    document.getElementById('promoDetailsDialog').addEventListener('click', event => {
        if (event.target === event.currentTarget) {
            const bounds = event.currentTarget.getBoundingClientRect();
            if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) event.currentTarget.close();
        }
    });
    atualizarCabecalhoComparacaoPromocoes();
}

inicializarComparacaoPromocoes();
