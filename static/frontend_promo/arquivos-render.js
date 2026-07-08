function renderPaginationControls(totalRows) {
    const wrap = document.getElementById('paginationControls');
    const sizeSel = document.getElementById('pageSizeSelect');
    const prevBtn = document.getElementById('prevPageBtn');
    const nextBtn = document.getElementById('nextPageBtn');
    const info = document.getElementById('pageInfo');

    if (!wrap || !sizeSel || !prevBtn || !nextBtn || !info) return;

    // Regra solicitada: exibir tudo na mesma página (sem paginação).
    wrap.style.display = 'none';
    pageState.page = 1;
    sizeSel.value = String(pageState.pageSize);
    info.textContent = '';
    prevBtn.disabled = true;
    nextBtn.disabled = true;

    if (!sizeSel.dataset.bound) {
        sizeSel.dataset.bound = '1';
        sizeSel.addEventListener('change', () => {
            pageState.pageSize = Number(sizeSel.value) || 100;
            pageState.page = 1;
            savePagePrefs();
            renderTable(currentData);
        });
    }
    if (!prevBtn.dataset.bound) {
        prevBtn.dataset.bound = '1';
        prevBtn.addEventListener('click', () => {
            if (pageState.page > 1) {
                pageState.page -= 1;
                savePagePrefs();
                renderTable(currentData);
            }
        });
    }
    if (!nextBtn.dataset.bound) {
        nextBtn.dataset.bound = '1';
        nextBtn.addEventListener('click', () => {
            const totalPagesNow = Math.max(1, Math.ceil((currentData.length || 0) / pageState.pageSize));
            if (pageState.page < totalPagesNow) {
                pageState.page += 1;
                savePagePrefs();
                renderTable(currentData);
            }
        });
    }
}

async function processarArquivos() {
    const fileInput = document.getElementById('fileInput');
    const loading = document.getElementById('loading');
    const errorMsg = document.getElementById('errorMsg');
    const resultsArea = document.getElementById('resultsArea');
    const tabsWrap = document.getElementById('apiAnalysisTabs');

    // Garante persistência do último ajuste feito pelo usuário antes de novo processamento.
    saveColumnPrefs();
    savePagePrefs();
    pendingWidthPrefs = {
        ...loadColumnWidthPrefs(),
        ...getColumnWidthPrefsFromDom(),
    };
    mergeAndSaveColumnWidthPrefs(pendingWidthPrefs);
    saveColumnWidthPrefsFromDomRobust(true);

    if (fileInput.files.length === 0) {
        alert("Selecione pelo menos um arquivo.");
        return;
    }

    setProcessingOverlay(true, 'Analisando arquivos e consolidando dados...');
    loading.style.display = 'block';
    errorMsg.style.display = 'none';
    resultsArea.style.display = 'none';
    apiAnalisesPorCampanha = [];
    apiAnaliseAtiva = 0;
    if (tabsWrap) {
        tabsWrap.innerHTML = '';
        tabsWrap.style.display = 'none';
    }
    uploadedFilesMap = {};

    const userData = JSON.parse(localStorage.getItem('user_data') || '{}');
    const clientId = userData.client_id;
    if (!clientId) {
        setProcessingOverlay(false);
        loading.style.display = 'none';
        throw new Error('Sessão sem cliente identificado. Faça login novamente.');
    }

    const formData = new FormData();
    for (let i = 0; i < fileInput.files.length; i++) {
        const file = fileInput.files[i];
        formData.append('files', file);
        uploadedFilesMap[file.name] = file; // Guarda referência para exportação
        uploadedFilesMap[String(file.name).toLowerCase()] = file;
    }

    try {
        const headers = (typeof obterAuthHeaders === 'function')
            ? obterAuthHeaders({ 'X-Client-ID': clientId })
            : { 'X-Client-ID': clientId };

        await carregarColumnWidthPrefsServidor();

        const response = await fetch('/api/promo/processar', {
            method: 'POST',
            headers,
            body: formData
        });

        if (!response.ok) {
            if (response.status === 401) {
                throw new Error('Sessão expirada (401). Faça login novamente e tente processar os arquivos.');
            }
            let detalhe = '';
            try {
                const payloadErro = await response.json();
                detalhe = payloadErro?.detail || payloadErro?.message || '';
            } catch (_) {
                try {
                    detalhe = await response.text();
                } catch (_) {}
            }
            const base = `Erro ao processar arquivos no servidor (HTTP ${response.status}).`;
            throw new Error(detalhe ? `${base} ${detalhe}` : base);
        }

        const result = await response.json();
        if (result.success) {
            currentData = result.data;
            mlFileName = result.ml_file; // Backend nos diz qual é o arquivo ML
            planilhaGeradaAtual = result.planilha_gerada || null;
            aplicarRegraAutomaticaAcaoPorMargem();
            pendingWidthPrefs = {
                ...loadColumnWidthPrefs(),
                ...(pendingWidthPrefs || {}),
            };
            renderTable(currentData);
            resultsArea.style.display = 'block';
        } else {
            throw new Error("O servidor retornou erro na análise.");
        }
    } catch (e) {
        errorMsg.textContent = e.message;
        errorMsg.style.display = 'block';
    } finally {
        setProcessingOverlay(false);
        loading.style.display = 'none';
    }
}

function renderTable(data) {
    const table = document.getElementById('tabelaAnalise');
    table.setAttribute('data-table-key', `tabelaAnalise__${getCurrentUserPrefScope()}`);
    table.setAttribute('data-jk-width-storage', 'off');
    const theadRow = document.querySelector('#tabelaAnalise thead tr');
    const tbody = document.querySelector('#tabelaAnalise tbody');
    theadRow.innerHTML = '';
    tbody.innerHTML = '';

    const totalRows = Array.isArray(data) ? data.length : 0;
    renderPaginationControls(totalRows);
    savePagePrefs();

    const startIdx = 0;
    const pageData = (data || []);

    const visibleColumns = getVisibleColumns();
    let draggingKey = null;
    visibleColumns.forEach(col => {
        const th = document.createElement('th');
        if (col.key === 'Ação') {
            th.innerHTML = `<div style="display:flex; flex-direction:column; gap:6px; align-items:center;"><span>${col.label}</span><button type="button" class="col-btn-mini" title="Definir tolerância" style="padding:2px 6px; font-size:11px;">Tol. ${String(actionTolerancePct).replace('.', ',')}%</button></div>`;
            const btnTol = th.querySelector('button');
            if (btnTol) {
                btnTol.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    promptToleranciaAcao();
                });
            }
        } else {
            th.textContent = col.label;
        }
        th.draggable = true;
        th.dataset.colKey = col.key;
        th.addEventListener('dragstart', (e) => {
            draggingKey = col.key;
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', col.key);
        });
        th.addEventListener('dragover', (e) => {
            e.preventDefault();
            th.classList.add('drag-over');
        });
        th.addEventListener('dragleave', () => th.classList.remove('drag-over'));
        th.addEventListener('drop', (e) => {
            e.preventDefault();
            th.classList.remove('drag-over');
            const targetKey = th.dataset.colKey;
            const sourceKey = e.dataTransfer.getData('text/plain') || draggingKey;
            reorderColumnsByKeys(sourceKey, targetKey);
        });
        th.addEventListener('dragend', () => {
            draggingKey = null;
            document.querySelectorAll('#tabelaAnalise thead th.drag-over').forEach(x => x.classList.remove('drag-over'));
        });
        theadRow.appendChild(th);
    });

    pageData.forEach((row, index) => {
        const dataIndex = startIdx + index;
        const tr = document.createElement('tr');

        visibleColumns.forEach(col => {
            const td = document.createElement('td');
            let rawCellValue = getTableCellValue(row, col.key);
            if ((rawCellValue === null || rawCellValue === undefined || String(rawCellValue).trim() === '') && col.key === '%') {
                rawCellValue = resolvePromoPercentDisplay(row);
            }
            const cellValue = String(rawCellValue ?? '').trim();
            td.style.textAlign = col.key === 'Título' ? 'left' : 'center';
            if (col.editable) {
                const select = document.createElement('select');
                const decisaoAtual = row['Ação'] || row['Participar ou não'] || '';
                const isParticipar = isDecisaoParticipar(decisaoAtual);
                select.className = `decision-select ${isParticipar ? 'participar' : 'nao-participar'}`;
                select.style.cursor = 'pointer';
                select.style.textAlign = 'center';
                select.style.textAlignLast = 'center';

                const optParticipar = document.createElement('option');
                optParticipar.value = 'Participar';
                optParticipar.textContent = 'Participar';

                const optNaoParticipar = document.createElement('option');
                optNaoParticipar.value = 'Não participar';
                optNaoParticipar.textContent = 'Não participar';

                select.appendChild(optParticipar);
                select.appendChild(optNaoParticipar);
                select.value = isParticipar ? 'Participar' : 'Não participar';

                select.addEventListener('change', function() {
                    const val = this.value;
                    const isPart = val === 'Participar';
                    currentData[dataIndex]['Ação'] = val;
                    currentData[dataIndex]['Participar ou não'] = val;
                    this.className = `decision-select ${isPart ? 'participar' : 'nao-participar'}`;
                    this.parentElement.style.backgroundColor = '#fff3cd';
                    updateMetrics();
                });

                td.appendChild(select);
            } else {
                td.textContent = cellValue;
                if (col.key === 'Valor Líquido' || col.key === 'Valor líquido ML') {
                    td.classList.add('col-highlight');
                    td.style.fontWeight = '600';
                }
                if (col.key === 'Margem' || col.key === 'Margem ML') {
                    const pct = parsePercentValue(cellValue);
                    if (pct !== null) {
                        if (pct < 10) td.classList.add('margin-low');
                        else if (pct >= 10 && pct < 13) td.classList.add('margin-mid');
                        else if (pct >= 13 && pct < 15) td.classList.add('margin-high');
                        else if (pct >= 15) td.classList.add('margin-top');
                    }
                }
            }
            tr.appendChild(td);
        });

        tbody.appendChild(tr);
    });

    updateMetrics();

    // Aplica larguras antes do plugin para evitar reset visual no reprocessamento.
    if (pendingWidthPrefs) {
        mergeAndSaveColumnWidthPrefs(pendingWidthPrefs);
    }
    applyColumnWidthPrefsToDom();

    // Reaplica redimensionamento por mouse após rerender do cabeçalho.
    if (window.JKTableColumns && typeof window.JKTableColumns.enhanceTable === 'function') {
        delete table.dataset.jkColumnsEnhanced;
        window.JKTableColumns.enhanceTable(table);
    }
    bindPromoMinimizeOnDblClick();

    // Persistência robusta por chave de coluna (independente de ordem/visibilidade).
    requestAnimationFrame(() => {
        if (pendingWidthPrefs) {
            mergeAndSaveColumnWidthPrefs(pendingWidthPrefs);
        }
        applyColumnWidthPrefsToDom();
        // Segunda passada para cobrir ajustes assíncronos do script de resize.
        setTimeout(applyColumnWidthPrefsToDom, 60);
        setTimeout(applyColumnWidthPrefsToDom, 180);
        setTimeout(() => {
            applyColumnWidthPrefsToDom();
            pendingWidthPrefs = null;
        }, 320);
        if (!document.body.dataset.widthPrefsBoundPromo) {
            document.body.dataset.widthPrefsBoundPromo = '1';
            // Captura o fim do resize mesmo quando o mouse é solto fora da tabela.
            document.addEventListener('mouseup', saveColumnWidthPrefsFromDomRobust);
            document.addEventListener('touchend', saveColumnWidthPrefsFromDomRobust, { passive: true });
            window.addEventListener('beforeunload', saveColumnWidthPrefsFromDomRobust);
        }
    });
}

function updateMetrics() {
    const total = currentData.length;
    const part = currentData.filter(r => {
        return isDecisaoParticipar(r['Ação'] || r['Participar ou não'] || "");
    }).length;

    document.getElementById('totalAnuncios').textContent = total;
    document.getElementById('totalParticipar').textContent = part;
    document.getElementById('totalNaoParticipar').textContent = total - part;
}
