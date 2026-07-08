const transitoStatusLabels = {
    aguardando_inicio: 'Aguardando inicio',
    em_processamento: 'Em processamento',
    finalizando: 'Finalizando',
    finalizado: 'Finalizado',
    recebimento_pendente: 'Recebimento pendente',
    recebido: 'Recebido',
    inativo: 'Inativo'
};

function transitoStatusLabel(status) {
    return transitoStatusLabels[status] || transitoStatusLabels.aguardando_inicio;
}

function transitoDataBR(iso) {
    const texto = String(iso || '').slice(0, 10);
    const m = texto.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    return m ? `${m[3]}/${m[2]}/${m[1]}` : '-';
}

function transitoMesLabel(date) {
    const nomes = ['Janeiro', 'Fevereiro', 'Marco', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'];
    return `${nomes[date.getMonth()]} ${date.getFullYear()}`;
}

function transitoFiltrarPorConta(lista) {
    const conta = String(transitoContaSelecionada || '').trim();
    if (!conta) return lista;
    return lista.filter(item => String(item.loja || '').trim() === conta);
}

function transitoCalendarioKey(date = transitoCalendarDate) {
    const d = new Date(date);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

function transitoCalendarioPayloadAtual(payload = transitoCalendarioComercial) {
    if (!payload) return false;
    return `${payload.ano}-${String(payload.mes).padStart(2, '0')}` === transitoCalendarioKey();
}

function transitoMapaCalendarioComercial() {
    if (!transitoCalendarioPayloadAtual()) return new Map();
    return new Map((transitoCalendarioComercial.dias || []).map(item => [String(item.data || ''), item]));
}

function atualizarResumoCalendarioTransito() {
    if (!transitoCalendarMeta) return;
    const key = transitoCalendarioKey();
    if (transitoCalendarioComercialLoadingKey === key && !transitoCalendarioPayloadAtual()) {
        transitoCalendarMeta.innerHTML = '<span>Buscando feriados e dias comerciais...</span>';
        return;
    }
    if (!transitoCalendarioPayloadAtual()) {
        transitoCalendarMeta.innerHTML = '<span>Calendario comercial pendente</span>';
        return;
    }
    const resumo = transitoCalendarioComercial.resumo || {};
    const diasComerciais = Number(resumo.dias_comerciais || 0);
    const feriados = Number(resumo.feriados || 0);
    const erros = Array.isArray(transitoCalendarioComercial.erros)
        ? transitoCalendarioComercial.erros.filter(Boolean)
        : [];
    transitoCalendarMeta.innerHTML = [
        `<span>${formatarNumero(diasComerciais)} dias comerciais</span>`,
        `<span>${feriados ? `${formatarNumero(feriados)} feriado(s)` : 'Sem feriados nacionais'}</span>`,
        '<small>Feriados nacionais via BrasilAPI</small>',
        erros.length ? `<small class="warn">${escapeHtml(erros[0])}</small>` : ''
    ].filter(Boolean).join('');
}

async function carregarCalendarioComercialTransito(force = false) {
    const mesAtual = new Date(transitoCalendarDate.getFullYear(), transitoCalendarDate.getMonth(), 1);
    const ano = mesAtual.getFullYear();
    const mes = mesAtual.getMonth() + 1;
    const key = `${ano}-${String(mes).padStart(2, '0')}`;
    if (!force && transitoCalendarioPayloadAtual() && transitoCalendarioKey() === key) {
        atualizarResumoCalendarioTransito();
        return;
    }
    if (!force && transitoCalendarioComercialLoadingKey === key) return;
    transitoCalendarioComercialLoadingKey = key;
    atualizarResumoCalendarioTransito();
    try {
        const payload = await fetchJsonFullCached(
            chaveCacheFull('calendario_comercial', [ano, mes]),
            `/api/full/calendario-comercial?ano=${ano}&mes=${mes}`,
            { ttlMs: FULL_CACHE_TTL_FERIADOS_MS, force }
        );
        if (transitoCalendarioKey() !== key) return;
        transitoCalendarioComercial = payload;
    } catch (e) {
        if (transitoCalendarioKey() === key) {
            transitoCalendarioComercial = {
                ano,
                mes,
                dias: [],
                resumo: { dias_comerciais: 0, feriados: 0, fins_semana: 0 },
                erros: [e && e.message ? e.message : 'Nao foi possivel carregar feriados.']
            };
        }
    } finally {
        if (transitoCalendarioComercialLoadingKey === key) transitoCalendarioComercialLoadingKey = '';
        renderCalendarioTransito();
    }
}

async function fetchTransitoFull(url, options = {}) {
    const headers = obterAuthHeaders(options.headers || {});
    const resp = await fetch(url, { ...options, headers, cache: 'no-store' });
    if (!resp.ok) {
        let msg = `HTTP ${resp.status}`;
        try {
            const payload = await resp.json();
            msg = payload.detail || payload.message || msg;
        } catch (_e) {}
        throw new Error(msg);
    }
    return resp;
}

async function carregarTransitoFull(force = false) {
    transitoCarregado = true;
    if (btnAtualizarTransitoFull) btnAtualizarTransitoFull.disabled = true;
    setTransitoStatus('Carregando envios em transito...');
    try {
        if (!lojasMl.length && force) await carregarLojasMl(true);
        const resp = await fetchTransitoFull('/api/full/envios-transito?status=all');
        const payload = await resp.json();
        transitoEnvios = Array.isArray(payload.results) ? payload.results : [];
        renderTransitoFull({ forceCalendario: force });
        const filtrados = transitoFiltrarPorConta(transitoEnvios);
        setTransitoStatus(`${filtrados.length} envio(s) carregado(s)${transitoContaSelecionada ? ` para ${transitoContaSelecionada}` : ''}.`);
    } catch (e) {
        transitoEnvios = [];
        renderTransitoFull({ forceCalendario: force });
        setTransitoStatus(e && e.message ? `Erro ao carregar envios: ${e.message}` : 'Erro ao carregar envios.', 'error');
    } finally {
        if (btnAtualizarTransitoFull) btnAtualizarTransitoFull.disabled = false;
    }
}

function renderTransitoFull(options = {}) {
    renderCalendarioTransito();
    carregarCalendarioComercialTransito(Boolean(options.forceCalendario));
    renderInativosTransito();
}

function renderCalendarioTransito() {
    if (!transitoCalendarGrid || !transitoCalendarTitle) return;
    const mesAtual = new Date(transitoCalendarDate.getFullYear(), transitoCalendarDate.getMonth(), 1);
    transitoCalendarTitle.textContent = transitoMesLabel(mesAtual);
    atualizarResumoCalendarioTransito();
    const diasSemana = ['DOM', 'SEG', 'TER', 'QUA', 'QUI', 'SEX', 'SAB'];
    const inicio = new Date(mesAtual);
    inicio.setDate(1 - inicio.getDay());
    const ativos = transitoFiltrarPorConta(transitoEnvios).filter(item => item.ativo && item.status !== 'inativo');
    const porDia = {};
    ativos.forEach(item => {
        const key = String(item.data_envio || item.created_at || '').slice(0, 10);
        if (!key) return;
        (porDia[key] ||= []).push(item);
    });
    const diasComerciais = transitoMapaCalendarioComercial();
    const hoje = hojeISO();
    const partes = diasSemana.map(dia => `<div class="transito-weekday">${dia}</div>`);
    for (let i = 0; i < 42; i++) {
        const dia = new Date(inicio);
        dia.setDate(inicio.getDate() + i);
        const key = dataISO(dia);
        const enviosDia = porDia[key] || [];
        const infoComercial = diasComerciais.get(key);
        const mesDoCalendario = dia.getMonth() === mesAtual.getMonth() && dia.getFullYear() === mesAtual.getFullYear();
        const fimSemana = infoComercial ? Boolean(infoComercial.fim_semana) : dia.getDay() === 0 || dia.getDay() === 6;
        const feriado = mesDoCalendario && infoComercial ? Boolean(infoComercial.feriado) : false;
        const diaComercial = mesDoCalendario && infoComercial ? Boolean(infoComercial.dia_comercial) : (mesDoCalendario && !fimSemana);
        const nomeFeriado = String(infoComercial?.nome_feriado || '').trim();
        const etiquetaDia = feriado
            ? `<span class="transito-day-label holiday" title="${escapeHtml(nomeFeriado || 'Feriado nacional')}">${escapeHtml(nomeFeriado || 'Feriado')}</span>`
            : (diaComercial ? '<span class="transito-day-label business">Dia comercial</span>' : '');
        const classes = [
            'transito-day',
            !mesDoCalendario ? 'other' : '',
            diaComercial ? 'business-day' : '',
            fimSemana ? 'weekend' : '',
            feriado ? 'holiday' : '',
            key === hoje ? 'today' : ''
        ].filter(Boolean).join(' ');
        partes.push(`
            <div class="${classes}">
                <strong>${dia.getDate()}</strong>
                ${etiquetaDia}
                ${enviosDia.slice(0, 3).map(item => `<span class="transito-day-tag st-${escapeHtml(item.status || 'aguardando_inicio')}" data-transito-open="${escapeHtml(item.id)}" title="${escapeHtml(item.codigo_envio || '')}">${escapeHtml(item.codigo_envio || '-')}: ${formatarNumero(item.total_unidades || 0)} un.</span>`).join('')}
                ${enviosDia.length > 3 ? `<span class="transito-day-tag st-em_processamento">+${enviosDia.length - 3} envio(s)</span>` : ''}
            </div>
        `);
    }
    transitoCalendarGrid.innerHTML = partes.join('');
}

function renderInativosTransito() {
    if (!transitoInativosList) return;
    const termo = transitoBusca ? transitoBusca.value.trim().toLowerCase() : '';
    let rows = transitoFiltrarPorConta(transitoEnvios).filter(item => !item.ativo || item.status === 'inativo');
    if (termo) {
        rows = rows.filter(item => {
            const itens = Array.isArray(item.itens) ? item.itens : [];
            const textoItens = itens.map(i => `${i.sku || ''} ${i.mlb || ''} ${i.produto || ''}`).join(' ');
            return [item.codigo_envio, item.loja, item.status, textoItens].some(v => String(v || '').toLowerCase().includes(termo));
        });
    }
    rows.sort((a, b) => {
        if (transitoSort === 'data') return String(b.data_envio || '').localeCompare(String(a.data_envio || '')) || String(b.codigo_envio || '').localeCompare(String(a.codigo_envio || ''));
        return String(a.codigo_envio || '').localeCompare(String(b.codigo_envio || ''), 'pt-BR', { numeric: true });
    });
    if (transitoTotalInativos) transitoTotalInativos.textContent = `${rows.length} envio(s)`;
    if (!rows.length) {
        transitoInativosList.innerHTML = '<div class="transito-card"><div>Nenhum envio inativo encontrado.</div></div>';
        return;
    }
    transitoInativosList.innerHTML = rows.map(item => `
        <article class="transito-card" data-transito-id="${item.id}">
            <div>
                <div class="transito-card-title">#${escapeHtml(item.codigo_envio || item.id)}</div>
                <div class="transito-card-meta">
                    <span>${formatarNumero(item.total_unidades || 0)} unidades</span>
                    <span>${transitoDataBR(item.data_envio)}</span>
                    <span>${escapeHtml(item.loja || 'Sem conta')}</span>
                    <span class="transito-status st-${escapeHtml(item.status || 'inativo')}">${escapeHtml(transitoStatusLabel(item.status || 'inativo'))}</span>
                </div>
            </div>
            <div class="transito-card-actions">
                <button class="btn" type="button" data-transito-action="edit">Editar</button>
                <button class="btn" type="button" data-transito-action="products">Lista</button>
                ${item.arquivo_pdf ? '<button class="btn" type="button" data-transito-action="pdf">PDF</button>' : ''}
                <button class="btn" type="button" data-transito-action="reactivate">Reativar</button>
                <button class="btn" type="button" data-transito-action="delete">Excluir</button>
            </div>
        </article>
    `).join('');
}

function transitoItemPorId(id) {
    return transitoEnvios.find(item => String(item.id) === String(id));
}

function abrirManualTransito(item = null) {
    if (!transitoManualPanel) return;
    const envio = item || {};
    transitoManualId.value = envio.id || '';
    transitoManualCodigo.value = envio.codigo_envio || '';
    transitoManualData.value = String(envio.data_envio || hojeISO()).slice(0, 10);
    transitoManualStatus.value = envio.status || 'aguardando_inicio';
    transitoManualUnidades.value = envio.total_unidades || '';
    transitoManualObs.value = envio.observacoes || '';
    const itens = Array.isArray(envio.itens) ? envio.itens : [];
    transitoManualItens.value = itens.map(i => `${i.sku || ''}; ${i.produto || ''}; ${i.quantidade || 0}`).join('\n');
    transitoManualPanel.hidden = false;
    transitoManualCodigo.focus();
}

function fecharManualTransito() {
    if (!transitoManualPanel) return;
    transitoManualPanel.hidden = true;
    [transitoManualId, transitoManualCodigo, transitoManualData, transitoManualUnidades, transitoManualItens, transitoManualObs].forEach(el => { if (el) el.value = ''; });
    if (transitoManualStatus) transitoManualStatus.value = 'aguardando_inicio';
}

function parseItensManualTransito() {
    return String(transitoManualItens?.value || '').split(/\r?\n/).map(linha => {
        const partes = linha.split(';').map(p => p.trim());
        if (!partes.some(Boolean)) return null;
        return { sku: partes[0] || '', produto: partes[1] || '', quantidade: numero(partes[2] || 0), variacao: '', mlb: '' };
    }).filter(Boolean);
}

async function salvarManualTransito() {
    const id = String(transitoManualId?.value || '').trim();
    const payload = {
        codigo_envio: transitoManualCodigo.value.trim(),
        loja: transitoContaSelecionada,
        data_envio: transitoManualData.value || hojeISO(),
        status: transitoManualStatus.value || 'aguardando_inicio',
        total_unidades: numero(transitoManualUnidades.value),
        observacoes: transitoManualObs.value.trim(),
        ativo: transitoManualStatus.value !== 'inativo',
        itens: parseItensManualTransito()
    };
    if (!payload.codigo_envio) {
        setTransitoStatus('Informe o numero do envio.', 'error');
        return;
    }
    try {
        const options = {
            method: id ? 'PATCH' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        };
        const url = id ? `/api/full/envios-transito/${encodeURIComponent(id)}` : '/api/full/envios-transito/manual';
        await fetchTransitoFull(url, options);
        fecharManualTransito();
        await carregarTransitoFull(true);
        setTransitoStatus('Envio salvo com sucesso.');
    } catch (e) {
        setTransitoStatus(e && e.message ? `Erro ao salvar envio: ${e.message}` : 'Erro ao salvar envio.', 'error');
    }
}

async function uploadTransitoPdfs(files) {
    const lista = Array.from(files || []).filter(file => /\.pdf$/i.test(file.name || ''));
    if (!lista.length) {
        setTransitoStatus('Selecione ao menos um PDF valido.', 'error');
        return;
    }
    if (lista.length > 20) {
        setTransitoStatus('Envie no maximo 20 PDFs por vez.', 'error');
        return;
    }
    const form = new FormData();
    lista.forEach(file => form.append('files', file));
    form.append('loja', transitoContaSelecionada || '');
    setTransitoStatus(`Enviando ${lista.length} PDF(s)...`);
    try {
        await fetchTransitoFull('/api/full/envios-transito/upload', { method: 'POST', body: form });
        await carregarTransitoFull(true);
        setTransitoStatus(`${lista.length} PDF(s) processado(s). Confira os dados extraidos.`);
    } catch (e) {
        setTransitoStatus(e && e.message ? `Erro no upload: ${e.message}` : 'Erro no upload dos PDFs.', 'error');
    }
}

function mostrarProdutosTransito(item) {
    if (!transitoProductsPanel || !item) return;
    const itens = Array.isArray(item.itens) ? item.itens : [];
    transitoProductsPanel.hidden = false;
    transitoProductsPanel.innerHTML = `
        <div class="transito-list-head">
            <div>
                <h3>Produtos do envio #${escapeHtml(item.codigo_envio || item.id)}</h3>
                <span>${formatarNumero(item.total_unidades || 0)} unidade(s), ${itens.length} produto(s)</span>
            </div>
            <button class="btn" type="button" id="btnFecharProdutosTransito">Fechar</button>
        </div>
        <div class="transito-products-list">
            ${itens.length ? itens.map(prod => `
                <div class="transito-product-row">
                    <strong>${escapeHtml(prod.sku || '-')}</strong>
                    <span>${escapeHtml(prod.produto || '-')}</span>
                    <span>${formatarNumero(prod.quantidade || 0)} un.</span>
                </div>
            `).join('') : '<div class="transito-product-row"><span>Nenhum produto extraido. Edite o envio para preencher manualmente.</span></div>'}
        </div>
    `;
    document.getElementById('btnFecharProdutosTransito')?.addEventListener('click', () => {
        transitoProductsPanel.hidden = true;
    });
}

async function baixarPdfTransito(item) {
    if (!item) return;
    try {
        const resp = await fetchTransitoFull(`/api/full/envios-transito/${encodeURIComponent(item.id)}/pdf`);
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        window.open(url, '_blank');
        setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch (e) {
        setTransitoStatus(e && e.message ? `Erro ao abrir PDF: ${e.message}` : 'Erro ao abrir PDF.', 'error');
    }
}

async function acaoTransito(id, action) {
    const item = transitoItemPorId(id);
    if (!item) return;
    if (action === 'products') {
        mostrarProdutosTransito(item);
        return;
    }
    if (action === 'edit') {
        abrirManualTransito(item);
        return;
    }
    if (action === 'pdf') {
        baixarPdfTransito(item);
        return;
    }
    try {
        if (action === 'reactivate') {
            await fetchTransitoFull(`/api/full/envios-transito/${encodeURIComponent(id)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ativo: true, status: 'aguardando_inicio' })
            });
            await carregarTransitoFull(true);
            setTransitoStatus('Envio reativado.');
        }
        if (action === 'delete') {
            if (!confirm('Excluir este envio definitivamente?')) return;
            await fetchTransitoFull(`/api/full/envios-transito/${encodeURIComponent(id)}`, { method: 'DELETE' });
            await carregarTransitoFull(true);
            setTransitoStatus('Envio excluido.');
        }
    } catch (e) {
        setTransitoStatus(e && e.message ? `Erro na acao: ${e.message}` : 'Erro ao atualizar envio.', 'error');
    }
}
