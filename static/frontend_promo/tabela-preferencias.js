function formatPercentValueBr(num) {
    if (!Number.isFinite(num)) return '';
    const rounded = Math.round(num * 100) / 100;
    return `${String(rounded).replace('.', ',')}%`;
}

function temValorApi(row, aliases) {
    const valor = getFirstRowValueByAliases(row, aliases);
    if (!valor) return false;
    const normalizado = String(valor).trim().toLowerCase();
    return !['-', 'nan', 'none', 'null', '<na>'].includes(normalizado);
}

function temValoresEssenciaisProgramado(row) {
    return (
        temValorApi(row, ['Custo']) &&
        temValorApi(row, ['Tarifa ML']) &&
        temValorApi(row, ['Preço Final ML', 'Preço Final Promoção 2', 'M ML']) &&
        temValorApi(row, ['Valor líquido ML', 'Valor Líquido ML']) &&
        parsePercentValue(getFirstRowValueByAliases(row, ['Margem ML'])) !== null
    );
}

function normalizeApiRowPercentAndAction(row) {
    if (!row || typeof row !== 'object') return;
    preencherCamposCanonicosTabelaPromo(row);

    // Regras solicitadas para Ação no modo API.
    const statusOriginal = String(row['Status'] ?? '');
    const statusNorm = statusOriginal.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    const ativoPromo1 = statusNorm === 'ativo' || statusNorm.includes('ativo na promocao 1');
    const programadoPromo1 = statusNorm === 'programado' || statusNorm === 'programada' || statusNorm.includes('programado na promocao 1') || statusNorm.includes('programada na promocao 1');
    const elegivelPromo1 = statusNorm === 'elegivel' || statusNorm === 'eligible';
    const semPromoFixa = (
        statusNorm === 'sem promo fixa' ||
        statusNorm.includes('somente arquivo promocao 2') ||
        statusNorm.includes('somente promocao 2')
    );
    row['Status'] = ativoPromo1 ? 'Ativo' : (programadoPromo1 ? 'Programada' : (elegivelPromo1 ? 'Elegível' : 'Sem Promo Fixa'));
    const margemMinima = Number(document.getElementById('apiMargemMinima')?.value || 15);
    const minPct = Number.isFinite(margemMinima) ? margemMinima : 15;
    const margem1 = parsePercentValue(row['Margem']);
    const margem2 = parsePercentValue(row['Margem ML']);

    let decisao = 'Participar';
    if (semPromoFixa || (!ativoPromo1 && !programadoPromo1)) {
        decisao = 'Não participar';
    } else if (programadoPromo1) {
        decisao = temValoresEssenciaisProgramado(row) ? 'Participar' : 'Não participar';
    } else if (margem1 === null || margem1 < minPct) {
        decisao = 'Não participar';
    } else if (margem2 === null || margem2 < minPct) {
        decisao = 'Não participar';
    } else if (margem1 !== null && margem2 < margem1) {
        decisao = 'Não participar';
    }

    row['Ação'] = decisao;
    row['Participar ou não'] = decisao;
}

function normalizeApiDatasetRules(dataRows) {
    const rows = Array.isArray(dataRows) ? dataRows : [];
    rows.forEach((r) => normalizeApiRowPercentAndAction(r));
    return rows;
}

function isDecisaoParticipar(valor) {
    const raw = String(valor ?? '').trim();
    if (!raw) return false;
    const upperRaw = raw.toUpperCase();
    const normalized = upperRaw.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    const negativa = normalized.includes('NAO') || upperRaw.includes('NÃO') || upperRaw.includes('NÃ');
    return normalized.includes('PARTICIPAR') && !negativa;
}

function _normalizeLookupKeyBase(value) {
    return String(value ?? '')
        .replace(/\ufeff/g, '')
        .trim()
        .toLowerCase()
        .replace(/\s+/g, ' ');
}

function _normalizeLookupKeyAccentInsensitive(value) {
    const base = _normalizeLookupKeyBase(value);
    try {
        return base.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    } catch (_e) {
        return base;
    }
}

function _tryFixMojibake(text) {
    const raw = String(text ?? '');
    try {
        return decodeURIComponent(escape(raw));
    } catch (_e) {
        return raw;
    }
}

function normalizeLookupKey(value) {
    return _normalizeLookupKeyAccentInsensitive(value);
}

function _keysAreEquivalent(a, b) {
    const a1 = _normalizeLookupKeyAccentInsensitive(a);
    const b1 = _normalizeLookupKeyAccentInsensitive(b);
    if (a1 === b1) return true;

    const a2 = _normalizeLookupKeyAccentInsensitive(_tryFixMojibake(a));
    const b2 = _normalizeLookupKeyAccentInsensitive(_tryFixMojibake(b));
    return a2 === b2;
}

function getFirstRowValueByAliases(row, aliases) {
    if (!row || typeof row !== 'object') return '';
    const keys = Object.keys(row || {});
    for (const alias of aliases) {
        for (const k of keys) {
            if (!_keysAreEquivalent(k, alias)) continue;
            const v = row[k];
            if (v === null || v === undefined) continue;
            const s = String(v).trim();
            if (!s || ['nan', 'none', 'null', '<na>', '-'].includes(s.toLowerCase())) continue;
            return s;
        }
    }
    return '';
}

function getTableCellValue(row, key) {
    if (!row || typeof row !== 'object') return '';
    const direto = row[key];
    if (direto !== null && direto !== undefined && String(direto).trim() !== '') {
        return direto;
    }
    const aliases = TABLE_COLUMN_ALIASES[key] || [key];
    return getFirstRowValueByAliases(row, aliases);
}

function preencherCamposCanonicosTabelaPromo(row) {
    if (!row || typeof row !== 'object') return row;
    TABLE_COLUMNS_MODERN.forEach((col) => {
        if (col.editable) return;
        const atual = row[col.key];
        if (atual !== null && atual !== undefined && String(atual).trim() !== '') return;
        const valor = getTableCellValue(row, col.key);
        if (valor !== null && valor !== undefined && String(valor).trim() !== '') {
            row[col.key] = valor;
        }
    });
    return row;
}

function resolvePromoPercentDisplay(row) {
    // Preferência: coluna % explícita do payload.
    let pct = getFirstRowValueByAliases(row, TABLE_COLUMN_ALIASES['%']);
    if (pct) return pct;
    return '';
}

function aplicarRegraAutomaticaAcaoPorMargem() {
    (currentData || []).forEach((r) => {
        const status = String(r['Status'] ?? '').trim().toLowerCase();
        if (status !== 'ativo') {
            r['Ação'] = 'Não participar';
            r['Participar ou não'] = 'Não participar';
            return;
        }
        const margem = parsePercentValue(r['Margem']);
        const margemMl = parsePercentValue(r['Margem ML']);
        if (margem === null || margemMl === null) {
            r['Ação'] = 'Não participar';
            r['Participar ou não'] = 'Não participar';
            return;
        }
        const participar = margemMl > margem;
        const decisao = participar ? 'Participar' : 'Não participar';
        r['Ação'] = decisao;
        r['Participar ou não'] = decisao;
    });
}

function promptToleranciaAcao() {
    const atual = String(actionTolerancePct).replace('.', ',');
    const entrada = prompt('Informe a tolerância (%) para Ação automática por Margem ML vs Margem:', atual);
    if (entrada === null) return;
    const n = Number(String(entrada).replace(',', '.'));
    if (!Number.isFinite(n) || n < 0) {
        alert('Valor inválido. Informe um número maior ou igual a 0.');
        return;
    }
    actionTolerancePct = n;
    saveActionTolerance();
    aplicarRegraAutomaticaAcaoPorMargem();
    renderTable(currentData);
}

function getDefaultColumnState() {
    return tableColumns.map((c, i) => ({ key: c.key, visible: true, order: i }));
}

function loadColumnPrefs() {
    try {
        const raw = localStorage.getItem(getColumnPrefsKey());
        if (!raw) {
            columnState = getDefaultColumnState();
            return;
        }
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) {
            columnState = getDefaultColumnState();
            return;
        }
        const validKeys = new Set(tableColumns.map(c => c.key));
        const clean = parsed.filter(x => x && validKeys.has(x.key));
        const missing = tableColumns.filter(c => !clean.some(x => x.key === c.key)).map((c, i) => ({ key: c.key, visible: true, order: 1000 + i }));
        columnState = [...clean, ...missing]
            .map((c, i) => ({ key: c.key, visible: c.visible !== false, order: Number.isFinite(c.order) ? c.order : i }))
            .sort((a, b) => a.order - b.order)
            .map((c, i) => ({ ...c, order: i }));
    } catch (_e) {
        columnState = getDefaultColumnState();
    }
}

function saveColumnPrefs() {
    try {
        localStorage.setItem(getColumnPrefsKey(), JSON.stringify(columnState));
    } catch (_e) {}
    agendarSalvarColumnWidthPrefsServidor();
}

function getOrderedColumnKeys() {
    return [...columnState]
        .sort((a, b) => a.order - b.order)
        .map((c) => c.key)
        .filter(Boolean);
}

function getVisibleColumnKeys() {
    return [...columnState]
        .filter((c) => c.visible)
        .sort((a, b) => a.order - b.order)
        .map((c) => c.key)
        .filter(Boolean);
}

function applyServerColumnPrefs(payload) {
    if (!payload || typeof payload !== 'object') return;
    const ordem = Array.isArray(payload.ordem_colunas) ? payload.ordem_colunas.map((v) => String(v || '').trim()).filter(Boolean) : [];
    const visiveis = Array.isArray(payload.colunas_visiveis) ? payload.colunas_visiveis.map((v) => String(v || '').trim()).filter(Boolean) : [];
    if (!ordem.length && !Array.isArray(payload.colunas_visiveis)) return;

    const validKeys = new Set(tableColumns.map((c) => c.key));
    const ordemFiltrada = [];
    ordem.forEach((k) => {
        if (validKeys.has(k) && !ordemFiltrada.includes(k)) ordemFiltrada.push(k);
    });

    const baseOrder = ordemFiltrada.length ? ordemFiltrada : getOrderedColumnKeys();
    tableColumns.forEach((c) => {
        if (!baseOrder.includes(c.key)) baseOrder.push(c.key);
    });

    const visibleSet = new Set(visiveis.filter((k) => validKeys.has(k)));
    const applyVisibility = Array.isArray(payload.colunas_visiveis) && visiveis.length > 0;

    columnState = baseOrder.map((key, i) => ({
        key,
        visible: applyVisibility ? visibleSet.has(key) : true,
        order: i,
    }));
    saveColumnPrefs();
}

function loadPagePrefs() {
    try {
        const raw = localStorage.getItem(getPagePrefsKey());
        if (!raw) return;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object') return;
        const size = Number(parsed.pageSize);
        const page = Number(parsed.page);
        if ([25, 50, 100, 200].includes(size)) pageState.pageSize = size;
        if (Number.isFinite(page) && page >= 1) pageState.page = Math.floor(page);
    } catch (_e) {}
}

function savePagePrefs() {
    try {
        localStorage.setItem(getPagePrefsKey(), JSON.stringify(pageState));
    } catch (_e) {}
}

function loadColumnWidthPrefs() {
    try {
        const raw = localStorage.getItem(getColumnWidthPrefsKey());
        const parsed = raw ? JSON.parse(raw) : {};
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (_e) {
        return {};
    }
}

function _textWidthPx(text, font) {
    const canvas = _textWidthPx.canvas || (_textWidthPx.canvas = document.createElement('canvas'));
    const ctx = canvas.getContext('2d');
    ctx.font = font || '14px Segoe UI';
    return Math.ceil(ctx.measureText(String(text ?? '')).width);
}

function _fontFromElement(el) {
    const cs = getComputedStyle(el);
    return `${cs.fontStyle} ${cs.fontVariant} ${cs.fontWeight} ${cs.fontSize} / ${cs.lineHeight} ${cs.fontFamily}`;
}

function _calcAutoWidthForColumn(table, th, idx) {
    const minW = 44;
    const padding = 26;
    const headerTxt = String(th.textContent || '').trim();
    const headerW = _textWidthPx(headerTxt, _fontFromElement(th));

    let maxCellW = 0;
    const rows = Array.from(table.querySelectorAll('tbody tr'));
    rows.forEach((row) => {
        const td = row.children[idx];
        if (!td) return;
        const select = td.querySelector('select');
        const txt = select ? String(select.value || '') : String(td.textContent || '').trim();
        const w = _textWidthPx(txt, _fontFromElement(td));
        if (w > maxCellW) maxCellW = w;
    });

    return Math.max(minW, headerW, maxCellW) + padding;
}

function saveColumnWidthPrefsFromDom() {
    const table = document.getElementById('tabelaAnalise');
    if (!table) return;
    const ths = Array.from(table.querySelectorAll('thead tr th[data-col-key]'));
    if (!ths.length) return;
    const prefs = loadColumnWidthPrefs();
    const visibleKeys = new Set(ths.map((th) => String(th.dataset.colKey || '').trim()).filter(Boolean));
    visibleKeys.forEach((k) => { delete prefs[k]; });

    ths.forEach((th) => {
        const key = th.dataset.colKey;
        if (!key) return;
        const currentW = Math.round(th.offsetWidth || 0);
        if (currentW > 0) prefs[key] = currentW;
    });
    try {
        localStorage.setItem(getColumnWidthPrefsKey(), JSON.stringify(prefs));
    } catch (_e) {}
    agendarSalvarColumnWidthPrefsServidor();
}

function isResizeInteractionActive() {
    if (document.body && document.body.style && document.body.style.cursor === 'col-resize') return true;
    return !!document.querySelector('.jk-resize-handle.dragging');
}

function getColumnWidthPrefsFromDom() {
    const table = document.getElementById('tabelaAnalise');
    if (!table) return {};
    const ths = Array.from(table.querySelectorAll('thead tr th[data-col-key]'));
    if (!ths.length) return {};
    const out = {};
    ths.forEach((th) => {
        const key = th.dataset.colKey;
        if (!key) return;
        const currentW = Math.round(th.offsetWidth || 0);
        if (currentW > 0) out[key] = currentW;
    });
    return out;
}

function getRenderedColumnWidthsFromDom() {
    const table = document.getElementById('tabelaAnalise');
    if (!table) return {};
    const ths = Array.from(table.querySelectorAll('thead tr th[data-col-key]'));
    if (!ths.length) return {};
    const out = {};
    ths.forEach((th) => {
        const key = String(th.dataset.colKey || '').trim();
        const w = Math.round(th.offsetWidth || 0);
        if (key && w > 0) out[key] = w;
    });
    return out;
}

function mergeAndSaveColumnWidthPrefs(extraPrefs) {
    if (!extraPrefs || typeof extraPrefs !== 'object') return;
    const prefs = loadColumnWidthPrefs();
    Object.keys(extraPrefs).forEach((k) => {
        const w = Math.round(Number(extraPrefs[k] || 0));
        if (k && w > 0) prefs[k] = w;
    });
    try {
        localStorage.setItem(getColumnWidthPrefsKey(), JSON.stringify(prefs));
    } catch (_e) {}
}

function saveColumnWidthPrefsFromDomRobust(force = false) {
    if (!force && !isResizeInteractionActive()) return;
    // Evita perda de preferência quando outro listener finaliza o resize alguns ms depois.
    saveColumnWidthPrefsFromDom();
    setTimeout(saveColumnWidthPrefsFromDom, 0);
    setTimeout(saveColumnWidthPrefsFromDom, 120);
}

async function carregarColumnWidthPrefsServidor() {
    try {
        const userData = JSON.parse(localStorage.getItem('user_data') || '{}');
        const clientId = userData.client_id;
        if (!clientId) return;

        const headers = (typeof obterAuthHeaders === 'function')
            ? obterAuthHeaders({ 'X-Client-ID': clientId })
            : { 'X-Client-ID': clientId };

        const resp = await fetch('/api/promo/preferencias-colunas', {
            method: 'GET',
            headers,
        });
        if (!resp.ok) return;
        const payload = await resp.json();
        applyServerColumnPrefs(payload);
        if (
            payload
            && payload.versao === WIDTH_PREFS_VERSION
            && payload.larguras_colunas
            && typeof payload.larguras_colunas === 'object'
        ) {
            mergeAndSaveColumnWidthPrefs(payload.larguras_colunas);
        }
    } catch (_e) {}
}

async function salvarColumnWidthPrefsServidor() {
    try {
        const userData = JSON.parse(localStorage.getItem('user_data') || '{}');
        const clientId = userData.client_id;
        if (!clientId) return;

        const larguras = loadColumnWidthPrefs();
        const body = JSON.stringify({
            ordem_colunas: getOrderedColumnKeys(),
            colunas_visiveis: getVisibleColumnKeys(),
            larguras_colunas: (larguras && typeof larguras === 'object') ? larguras : {},
            versao: WIDTH_PREFS_VERSION,
        });
        const headersBase = (typeof obterAuthHeaders === 'function')
            ? obterAuthHeaders({ 'X-Client-ID': clientId })
            : { 'X-Client-ID': clientId };
        const headers = { ...headersBase, 'Content-Type': 'application/json' };

        await fetch('/api/promo/preferencias-colunas', {
            method: 'PUT',
            headers,
            body,
        });
    } catch (_e) {}
}

function agendarSalvarColumnWidthPrefsServidor() {
    if (saveWidthPrefsTimer) clearTimeout(saveWidthPrefsTimer);
    saveWidthPrefsTimer = setTimeout(() => {
        saveWidthPrefsTimer = null;
        salvarColumnWidthPrefsServidor();
    }, 350);
}

function fitWidthsToContainer(baseWidths, minWidth, availableWidth) {
    if (!Array.isArray(baseWidths) || !baseWidths.length) return [];
    if (!Number.isFinite(availableWidth) || availableWidth <= 0) return baseWidths.slice();

    const out = baseWidths.map((w) => Math.max(minWidth, Math.round(Number(w) || minWidth)));
    let total = out.reduce((sum, w) => sum + w, 0);

    if (total > availableWidth) {
        const needed = total - availableWidth;
        const reducible = out.reduce((sum, w) => sum + Math.max(0, w - minWidth), 0);
        if (reducible > 0) {
            const factor = Math.min(1, needed / reducible);
            for (let i = 0; i < out.length; i++) {
                const slack = Math.max(0, out[i] - minWidth);
                out[i] = Math.max(minWidth, Math.round(out[i] - (slack * factor)));
            }
        }
        total = out.reduce((sum, w) => sum + w, 0);
        let diff = total - availableWidth;
        for (let i = out.length - 1; i >= 0 && diff > 0; i--) {
            const canReduce = out[i] - minWidth;
            if (canReduce <= 0) continue;
            const step = Math.min(canReduce, diff);
            out[i] -= step;
            diff -= step;
            if (i === 0 && diff > 0) i = out.length;
        }
        return out;
    }

    if (total < availableWidth) {
        const extra = availableWidth - total;
        const baseTotal = total || out.length;
        for (let i = 0; i < out.length; i++) {
            const share = baseTotal > 0 ? (out[i] / baseTotal) : (1 / out.length);
            out[i] += Math.round(extra * share);
        }
        total = out.reduce((sum, w) => sum + w, 0);
        let diff = availableWidth - total;
        let idx = 0;
        while (diff !== 0 && out.length > 0) {
            const i = idx % out.length;
            if (diff > 0) {
                out[i] += 1;
                diff -= 1;
            } else if (out[i] > minWidth) {
                out[i] -= 1;
                diff += 1;
            }
            idx += 1;
            if (idx > out.length * 4 && diff < 0) break;
        }
    }

    return out;
}

function applyColumnWidthPrefsToDom() {
    const table = document.getElementById('tabelaAnalise');
    if (!table) return;
    const prefs = loadColumnWidthPrefs();
    const ths = Array.from(table.querySelectorAll('thead tr th[data-col-key]'));
    if (!ths.length) return;

    const container = table.closest('.table-container');
    const availableWidth = Math.max(0, Math.round((container ? container.clientWidth : table.parentElement?.clientWidth || table.clientWidth || 0) - 2));
    const baseWidths = ths.map((th) => {
        const key = th.dataset.colKey;
        if (!key) return DEFAULT_MIN_COLUMN_WIDTH;
        const saved = Math.round(Number(prefs[key] || 0));
        return saved > 0 ? Math.max(DEFAULT_MIN_COLUMN_WIDTH, saved) : DEFAULT_MIN_COLUMN_WIDTH;
    });
    const fittedWidths = fitWidthsToContainer(baseWidths, DEFAULT_MIN_COLUMN_WIDTH, availableWidth);

    let totalWidth = 0;
    ths.forEach((th, idx) => {
        const w = Math.max(DEFAULT_MIN_COLUMN_WIDTH, Math.round(Number(fittedWidths[idx] || baseWidths[idx] || DEFAULT_MIN_COLUMN_WIDTH)));
        totalWidth += w;
        th.style.width = `${w}px`;
        th.style.minWidth = `${w}px`;
        th.style.maxWidth = `${w}px`;
        Array.from(table.querySelectorAll('tbody tr')).forEach((row) => {
            const td = row.children[idx];
            if (!td) return;
            td.style.width = `${w}px`;
            td.style.minWidth = `${w}px`;
            td.style.maxWidth = `${w}px`;
        });
    });

    // Evita redistribuição automática de largura ao ocultar/exibir colunas.
    if (totalWidth > 0) {
        table.style.width = `${totalWidth}px`;
        table.style.minWidth = `${totalWidth}px`;
        table.style.maxWidth = 'none';
    }
}

function bindPromoMinimizeOnDblClick() {
    const table = document.getElementById('tabelaAnalise');
    if (!table || table.dataset.minimizeDblBoundPromo === '1') return;
    table.dataset.minimizeDblBoundPromo = '1';

    table.addEventListener('dblclick', (ev) => {
        const handle = ev.target && ev.target.closest ? ev.target.closest('.jk-resize-handle') : null;
        if (!handle) return;
        const th = handle.closest('th[data-col-key]');
        const key = th && th.dataset ? String(th.dataset.colKey || '').trim() : '';
        if (!key) return;

        const prefs = loadColumnWidthPrefs();
        prefs[key] = DEFAULT_MIN_COLUMN_WIDTH;
        try {
            localStorage.setItem(getColumnWidthPrefsKey(), JSON.stringify(prefs));
        } catch (_e) {}
        agendarSalvarColumnWidthPrefsServidor();
        applyColumnWidthPrefsToDom();
    });
}

function getVisibleColumns() {
    const byKey = new Map(tableColumns.map(c => [c.key, c]));
    return columnState
        .filter(c => c.visible)
        .sort((a, b) => a.order - b.order)
        .map(c => byKey.get(c.key))
        .filter(Boolean);
}

function garantirColunasVisiveis(keys) {
    const wanted = Array.isArray(keys) ? keys : [];
    if (!wanted.length) return;
    const validKeys = new Set(tableColumns.map((c) => c.key));
    const existentes = new Set(columnState.map((c) => c.key));
    wanted.forEach((key) => {
        if (!validKeys.has(key)) return;
        if (!existentes.has(key)) {
            columnState.push({ key, visible: true, order: columnState.length });
            existentes.add(key);
        }
    });
    columnState = columnState.map((c) => wanted.includes(c.key) ? ({ ...c, visible: true }) : c);
    saveColumnPrefs();
}

function toggleColumnsPanel() {
    const panel = document.getElementById('columnsPanel');
    const isHidden = panel.style.display === 'none' || !panel.style.display;
    panel.style.display = isHidden ? 'block' : 'none';
    if (isHidden) renderColumnsPanel();
}

function voltarParaPlanilha() {
    document.getElementById('columnsPanel').style.display = 'none';
}

function moveColumn(key, direction) {
    const idx = columnState.findIndex(c => c.key === key);
    if (idx < 0) return;
    const newIdx = idx + direction;
    if (newIdx < 0 || newIdx >= columnState.length) return;
    const tmp = columnState[idx];
    columnState[idx] = columnState[newIdx];
    columnState[newIdx] = tmp;
    columnState = columnState.map((c, i) => ({ ...c, order: i }));
    saveColumnPrefs();
    renderColumnsPanel();
    renderTable(currentData);
}

function reorderColumnsByKeys(dragKey, targetKey) {
    if (!dragKey || !targetKey || dragKey === targetKey) return;
    const sourceIdx = columnState.findIndex(c => c.key === dragKey);
    const targetIdx = columnState.findIndex(c => c.key === targetKey);
    if (sourceIdx < 0 || targetIdx < 0) return;

    const source = columnState[sourceIdx];
    columnState.splice(sourceIdx, 1);
    const adjustedTarget = sourceIdx < targetIdx ? targetIdx - 1 : targetIdx;
    columnState.splice(adjustedTarget, 0, source);
    columnState = columnState.map((c, i) => ({ ...c, order: i }));
    saveColumnPrefs();
    renderColumnsPanel();
    renderTable(currentData);
}

function setColumnVisible(key, visible) {
    // Ao ocultar/exibir colunas, preserva as larguras atuais das demais.
    const largurasAtuais = getRenderedColumnWidthsFromDom();
    pendingWidthPrefs = {
        ...loadColumnWidthPrefs(),
        ...(pendingWidthPrefs || {}),
        ...largurasAtuais,
    };
    mergeAndSaveColumnWidthPrefs(pendingWidthPrefs);
    agendarSalvarColumnWidthPrefsServidor();
    columnState = columnState.map(c => c.key === key ? ({ ...c, visible }) : c);
    saveColumnPrefs();
    renderTable(currentData);
}

function renderColumnsPanel() {
    const list = document.getElementById('columnsList');
    list.innerHTML = '';
    const labels = new Map(tableColumns.map(c => [c.key, c.label]));
    columnState.sort((a, b) => a.order - b.order).forEach((col, idx) => {
        const row = document.createElement('div');
        row.className = 'columns-row';

        const left = document.createElement('label');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = col.visible;
        cb.addEventListener('change', () => setColumnVisible(col.key, cb.checked));
        left.appendChild(cb);
        left.appendChild(document.createTextNode(' ' + (labels.get(col.key) || col.key)));

        const actions = document.createElement('div');
        actions.className = 'columns-actions';
        const up = document.createElement('button');
        up.className = 'col-btn-mini';
        up.textContent = '';
        up.disabled = idx === 0;
        up.addEventListener('click', () => moveColumn(col.key, -1));
        const down = document.createElement('button');
        down.className = 'col-btn-mini';
        down.textContent = '';
        down.disabled = idx === columnState.length - 1;
        down.addEventListener('click', () => moveColumn(col.key, 1));
        actions.appendChild(up);
        actions.appendChild(down);

        row.appendChild(left);
        row.appendChild(actions);
        list.appendChild(row);
    });
}
