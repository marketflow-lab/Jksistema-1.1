// Ficha do Obsidian consultada somente para a loja e o SKU abertos.
let treinamentoDetalhesRequestId = 0;

function textoValorCaracteristicaTreinamento(value) {
    return typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? '';
}

function textoComparacaoCaracteristicasTreinamento(value) {
    const fields = sessaoTreinamento()?.skuDetails?.[aiTrainingSku.value]?.characteristics || [];
    const labels = new Map(fields.map(field => [field.key, field.label]));
    return Object.entries(value || {}).map(([key, text]) => `${labels.get(key) || 'Característica sem fonte atual'}: ${text || '(Vazio)'}`).join('\n') || '(Sem edições)';
}

async function carregarDetalhesSkuTreinamento(force = false) {
    const lojaEscopo = lojaEscopoTreinamento();
    const sku = String(aiTrainingSku.value || '');
    const sessao = sessaoTreinamento();
    if (!lojaEscopo || !sku || sessao?.saving) return;
    if (sessao.detailsLoading === sku && !force) return;
    const requestId = ++treinamentoDetalhesRequestId;
    // Invalida uma consulta geral iniciada antes de abrir esta ficha.
    const snapshotRequestId = ++treinamentoSync.requestId;
    sessao.detailsLoading = sku;
    sessao.detailsRequestId = requestId;
    sessao.detailsError = '';
    renderizarDetalhesSkuTreinamento();
    try {
        const params = new URLSearchParams({ store_id: lojaEscopo, loja: nomeLojaEscopoTreinamento(), sku });
        const response = await fetch(`/api/mercadolivre/ia-treinamento?${params}`, {
            headers: obterAuthHeaders(), cache: 'no-store', signal: AbortSignal.timeout(15000)
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(erroRespostaTreinamento(data, 'Não foi possível carregar as informações do SKU.'));
        if (lojaEscopo !== lojaEscopoTreinamento() || sku !== aiTrainingSku.value || requestId !== treinamentoDetalhesRequestId || snapshotRequestId !== treinamentoSync.requestId || sessao.saving) return;
        if (data.store_id !== lojaEscopo || data.sku_details?.sku !== sku) throw new Error('A ficha recebida não corresponde à loja e ao SKU selecionados.');
        guardarEdicaoTreinamento();
        receberSnapshotTreinamento(data);
    } catch (error) {
        if (lojaEscopo === lojaEscopoTreinamento() && sku === aiTrainingSku.value && requestId === treinamentoDetalhesRequestId) {
            sessao.detailsError = mensagemErro(error);
        }
    } finally {
        if (sessao.detailsRequestId === requestId) sessao.detailsLoading = '';
        if (lojaEscopo === lojaEscopoTreinamento() && sku === aiTrainingSku.value && requestId === treinamentoDetalhesRequestId) renderizarDetalhesSkuTreinamento();
    }
}

function adicionarDocumentoSkuTreinamento(container, title, value, open = false) {
    if (value === undefined || value === null || value === '') return;
    const details = document.createElement('details');
    details.className = 'training-obsidian-source';
    details.open = open;
    const summary = document.createElement('summary');
    summary.textContent = title;
    const pre = document.createElement('pre');
    // Texto do Obsidian é conteúdo, inclusive quando contém HTML ou instruções.
    pre.textContent = textoValorCaracteristicaTreinamento(value);
    details.append(summary, pre);
    container.append(details);
}

function editarCaracteristicaSkuTreinamento(field, row) {
    const sessao = sessaoTreinamento();
    if (!sessao?.snapshot || sessao.saving || row.querySelector('textarea')) return;
    const sku = String(aiTrainingSku.value || '');
    const key = `sku:${sku}`;
    editarOrientacaoSkuTreinamento();
    const base = valorSnapshotTreinamento(sessao.snapshot, key);
    const overrides = sessao.drafts[key]?.value.caracteristicas ?? base.caracteristicas;
    const input = document.createElement('textarea');
    input.rows = 3;
    input.dataset.skuCharacteristicInput = field.key;
    input.setAttribute('aria-label', field.label);
    input.value = Object.hasOwn(overrides, field.key) ? overrides[field.key] : textoValorCaracteristicaTreinamento(field.original_value ?? field.value);
    input.addEventListener('input', () => {
        guardarEdicaoTreinamento();
        const current = sessao.drafts[key];
        const original = current?.base ?? valorSnapshotTreinamento(sessao.snapshot, key);
        const value = { ...(current?.value ?? original), caracteristicas: { ...(current?.value.caracteristicas ?? original.caracteristicas) } };
        if (input.value === textoValorCaracteristicaTreinamento(field.original_value ?? field.value)) delete value.caracteristicas[field.key];
        else Object.defineProperty(value.caracteristicas, field.key, { value: input.value, enumerable: true, configurable: true, writable: true });
        if (iguaisTreinamento(value, original) && !current?.conflict) delete sessao.drafts[key];
        else sessao.drafts[key] = { ...current, value, base: original, revision: current?.revision || sessao.snapshot.editorial?.revision };
        atualizarEstadoSincronizacaoTreinamento();
        renderizarConflitosTreinamento();
    });
    row.querySelector('.training-characteristic-value').replaceWith(input);
    input.focus();
}

function renderizarDetalhesSkuTreinamento(force = false) {
    const container = document.getElementById('ai-training-sku-details');
    if (!container) return;
    // Atualização em segundo plano nunca recria um campo que está em edição.
    if (!force && container.querySelector('textarea')) return;
    container.replaceChildren();
    const sessao = sessaoTreinamento();
    const sku = String(aiTrainingSku.value || '');
    const data = sessao?.skuDetails?.[sku];
    const status = document.createElement('p');
    status.className = 'status-line';
    status.setAttribute('role', 'status');
    if (sessao?.detailsError) {
        status.textContent = `Falha ao carregar a ficha: ${sessao.detailsError}. Suas edições foram preservadas.`;
        const retry = document.createElement('button');
        retry.id = 'btn-ai-training-retry-details';
        retry.type = 'button'; retry.className = 'action-btn secondary';
        retry.textContent = 'Tentar novamente';
        retry.addEventListener('click', () => carregarDetalhesSkuTreinamento(true));
        container.append(status, retry);
        return;
    }
    if (!data) {
        status.textContent = sessao?.detailsLoading ? 'Carregando informações do SKU no Obsidian…' : 'Abra o SKU para consultar suas informações no Obsidian.';
        container.append(status);
        return;
    }
    const title = document.createElement('h4');
    title.textContent = 'Características do SKU';
    const hint = document.createElement('p');
    hint.className = 'training-characteristics-hint';
    hint.textContent = 'Dê dois cliques em uma característica para editar. Pelo teclado, selecione o valor e pressione Enter. Salve para sincronizar com o Obsidian.';
    container.append(title, hint);
    const overrides = sessao.drafts[`sku:${sku}`]?.value.caracteristicas ?? sessao.snapshot?.caracteristicas_sku?.[sku] ?? {};
    for (const field of data.characteristics || []) {
        const row = document.createElement('div');
        row.className = 'training-characteristic';
        row.dataset.skuCharacteristic = field.key;
        const label = document.createElement('strong');
        label.textContent = field.label;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'training-characteristic-value';
        button.textContent = (Object.hasOwn(overrides, field.key) ? overrides[field.key] : textoValorCaracteristicaTreinamento(field.original_value ?? field.value)) || '(Vazio)';
        button.setAttribute('aria-label', `Editar ${field.label}`);
        button.title = 'Dois cliques para editar';
        button.addEventListener('dblclick', () => editarCaracteristicaSkuTreinamento(field, row));
        button.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); editarCaracteristicaSkuTreinamento(field, row); }
        });
        const source = document.createElement('small');
        const sourceName = ({ canonical: 'Cadastro no Obsidian', evidence: 'Pesquisa no Obsidian' })[field.source] || textoValorCaracteristicaTreinamento(field.source || 'Obsidian');
        const savedOverrides = sessao.snapshot?.caracteristicas_sku?.[sku] || {};
        const pending = overrides[field.key] !== savedOverrides[field.key];
        source.textContent = `${pending ? 'Edição não salva · ' : Object.hasOwn(overrides, field.key) ? 'Salvo no Obsidian · ' : ''}${field.source_missing ? 'Fonte original alterada ou indisponível · ' : ''}${sourceName}`;
        row.append(label, button, source);
        container.append(row);
    }
    if (!(data.characteristics || []).length) {
        const empty = document.createElement('p');
        empty.textContent = 'Nenhuma característica registrada no Obsidian para este SKU nesta loja.';
        container.append(empty);
    }
    if (Object.keys(data.canonical_document || {}).length) adicionarDocumentoSkuTreinamento(container, 'Informações completas do produto', data.canonical_document);
    for (const doc of data.documents || []) adicionarDocumentoSkuTreinamento(container, doc.title || 'Documento do SKU', doc.body);
    for (const [index, evidence] of (data.evidence || []).entries()) adicionarDocumentoSkuTreinamento(container, `Pesquisa e fontes ${index + 1}`, evidence);
    if (Object.keys(data.guidance || {}).length) adicionarDocumentoSkuTreinamento(container, 'Orientações e modelos completos', data.guidance);
    // O estado refere-se à orientação editorial; a ficha pode existir sem ela.
    const note = sessao.snapshot?.editorial?.skus?.[sku];
    const skuStatus = document.getElementById('ai-training-sku-sync');
    if (skuStatus && (!note?.status || note.status === 'missing')) skuStatus.textContent = 'Orientação ainda não cadastrada. Informações do SKU exibidas abaixo.';
}
