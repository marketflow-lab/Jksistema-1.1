// Ficha do Obsidian consultada somente para a loja e o SKU abertos.
let treinamentoDetalhesRequestId = 0;
let treinamentoDetalhesController;
let treinamentoCatalogoRequestId = 0;

function rotuloSincronizacaoCadastroTreinamento(sync) {
    return ({pending: 'Aguardando sincronização do cadastro…', running: 'Atualizando informações do cadastro…',
        completed: `Informações atualizadas. ${Number(sync?.total || 0)} ficha(s) na loja.`,
        error: 'Falha ao atualizar informações. Tente novamente.',
        not_synced: 'O cadastro ainda não foi sincronizado com o Obsidian.'})[sync?.status] || '';
}

async function sincronizarCadastroTreinamento(todaLoja = false) {
    const store = lojaEscopoTreinamento();
    const sku = todaLoja ? '' : String(aiTrainingSku.value || '');
    if (!store || (!todaLoja && !sku)) return;
    guardarEdicaoTreinamento();
    const session = sessaoTreinamento();
    const deadline = Date.now() + 120000;
    const requestId = ++treinamentoCatalogoRequestId;
    const current = () => requestId === treinamentoCatalogoRequestId && store === lojaEscopoTreinamento()
        && treinamentoVisivel() && (todaLoja || sku === aiTrainingSku.value);
    const show = sync => {
        session.catalogSynchronization = sync;
        const label = document.getElementById('ai-training-catalog-sync');
        if (label) label.textContent = rotuloSincronizacaoCadastroTreinamento(sync);
        const detail = session.skuDetails?.[aiTrainingSku.value];
        if (detail) detail.synchronization = sync;
        renderizarDetalhesSkuTreinamento();
    };
    try {
        show({status: 'pending'});
        let response = await fetch('/api/mercadolivre/ia-treinamento/sincronizacao', {
            method: 'POST', headers: {...obterAuthHeaders(), 'Content-Type': 'application/json'},
            body: JSON.stringify({store_id: store, loja: nomeLojaEscopoTreinamento(), sku}),
            signal: AbortSignal.timeout(15000)
        });
        let data = await response.json().catch(() => ({}));
        if (!current()) return;
        if (!response.ok) throw new Error(erroRespostaTreinamento(data, 'Falha ao iniciar atualização.'));
        // Limite de dois minutos: o servidor mantém a fila mesmo após fechar a tela.
        for (let attempt = 0; attempt < 60 && Date.now() < deadline; attempt += 1) {
            if (!current()) return;
            if (data.store_id !== store) throw new Error('A sincronização recebida pertence a outra loja.');
            const sync = data.synchronization || {};
            show(sync);
            if (sync.status === 'error') throw new Error('Falha ao atualizar informações. Tente novamente.');
            if (sync.status === 'completed' && !sync.pending) {
                if (aiTrainingSku.value) await carregarDetalhesSkuTreinamento(true);
                return;
            }
            if (!['running', 'pending', 'completed'].includes(sync.status)) return;
            await new Promise(resolve => setTimeout(resolve, 2000));
            if (!current()) return;
            if (Date.now() >= deadline) break;
            response = await fetch(`/api/mercadolivre/ia-treinamento/sincronizacao?${new URLSearchParams({store_id: store})}`, {
                headers: obterAuthHeaders(), cache: 'no-store', signal: AbortSignal.timeout(Math.max(1, Math.min(15000, deadline - Date.now())))
            });
            data = await response.json().catch(() => ({}));
            if (!current()) return;
            if (!response.ok) throw new Error(erroRespostaTreinamento(data, 'Falha ao consultar atualização.'));
        }
        if (current()) {
            const label = document.getElementById('ai-training-catalog-sync');
            if (label) label.textContent = 'A atualização continua no servidor. Consulte novamente em Atualizar informações.';
        }
    } catch (error) {
        if (current()) {
            show({status: 'error'});
            const label = document.getElementById('ai-training-catalog-sync');
            if (label) label.textContent = `${mensagemErro(error)} Suas edições foram preservadas.`;
        }
    }
}

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
    treinamentoDetalhesController?.abort();
    treinamentoDetalhesController = new AbortController();
    const controller = treinamentoDetalhesController;
    const timeout = setTimeout(() => controller.abort(), 15000);
    // Invalida uma consulta geral iniciada antes de abrir esta ficha.
    const snapshotRequestId = ++treinamentoSync.requestId;
    sessao.detailsLoading = sku;
    sessao.detailsRequestId = requestId;
    sessao.detailsError = '';
    renderizarDetalhesSkuTreinamento();
    try {
        const params = new URLSearchParams({ store_id: lojaEscopo, loja: nomeLojaEscopoTreinamento(), sku });
        const response = await fetch(`/api/mercadolivre/ia-treinamento?${params}`, {
            headers: obterAuthHeaders(), cache: 'no-store', signal: controller.signal
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
        clearTimeout(timeout);
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
    if (sku) {
        const refresh = document.createElement('button');
        refresh.id = 'btn-ai-training-refresh-catalog';
        refresh.type = 'button'; refresh.className = 'action-btn secondary';
        refresh.textContent = 'Atualizar informações';
        refresh.addEventListener('click', () => sincronizarCadastroTreinamento(false));
        container.append(refresh);
    }
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
    status.textContent = rotuloSincronizacaoCadastroTreinamento(data.synchronization);
    if (status.textContent) container.append(status);
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
        const sourceName = ({ catalog: 'Cadastro da própria loja', canonical: 'Conhecimento técnico no Obsidian', evidence: 'Pesquisa no Obsidian' })[field.source] || textoValorCaracteristicaTreinamento(field.source || 'Obsidian');
        const savedOverrides = sessao.snapshot?.caracteristicas_sku?.[sku] || {};
        const pending = overrides[field.key] !== savedOverrides[field.key];
        source.textContent = `${pending ? 'Edição não salva · ' : Object.hasOwn(overrides, field.key) ? 'Salvo no Obsidian · ' : ''}${field.source_missing ? 'Fonte original alterada ou indisponível · ' : ''}${field.source_changed ? 'Cadastro alterado; edição preservada · ' : ''}${field.source_conflict ? 'Fontes divergentes · ' : ''}${sourceName}`;
        row.append(label, button, source);
        if (field.edited && field.source_changed) {
            const original = document.createElement('small');
            original.textContent = `Valor atual na fonte: ${textoValorCaracteristicaTreinamento(field.original_value)}`;
            row.append(original);
        }
        container.append(row);
    }
    if (!(data.characteristics || []).length) {
        const empty = document.createElement('p');
        empty.textContent = ['pending', 'running'].includes(data.synchronization?.status)
            ? 'Aguardando informações do cadastro desta loja.'
            : 'Sem dados técnicos cadastrados nesta ficha. Use Atualizar informações para consultar o cadastro da loja.';
        container.append(empty);
    }
    const catalog = data.catalog_document || {};
    const images = catalog.fields?.images || [];
    if (images.length) {
        const gallery = document.createElement('div');
        gallery.className = 'training-catalog-images';
        for (const reference of images) {
            const url = obterFotoProdutoCadastro({foto: reference});
            if (!url) continue;
            const img = document.createElement('img');
            img.alt = 'Imagem do produto no cadastro desta loja';
            img.loading = 'lazy';
            if (ehUrlFotoCadastroProtegida(url)) img.dataset.jkAuthSrc = url;
            else img.src = url;
            gallery.append(img);
        }
        container.append(gallery);
    }
    for (const conflict of catalog.source_conflicts || []) {
        const note = document.createElement('p');
        note.className = 'status-line';
        const label = (data.characteristics || []).find(field => field.field === conflict.field)?.label || conflict.field;
        note.textContent = `Fontes divergentes para ${label}: ${textoValorCaracteristicaTreinamento(conflict.value)} (${conflict.source_field}). Confira o cadastro antes de usar esta informação.`;
        container.append(note);
    }
    if (Object.keys(catalog).length) adicionarDocumentoSkuTreinamento(container, 'Informações completas do cadastro da loja', catalog.fields || catalog, true);
    if (Object.keys(data.canonical_document || {}).length) adicionarDocumentoSkuTreinamento(container, 'Conhecimento técnico existente', data.canonical_document);
    for (const doc of data.documents || []) adicionarDocumentoSkuTreinamento(container, doc.title || 'Documento do SKU', doc.body);
    for (const [index, evidence] of (data.evidence || []).entries()) adicionarDocumentoSkuTreinamento(container, `Pesquisa e fontes ${index + 1}`, evidence);
    if (Object.keys(data.guidance || {}).length) adicionarDocumentoSkuTreinamento(container, 'Orientações e modelos completos', data.guidance);
    // O estado refere-se à orientação editorial; a ficha pode existir sem ela.
    const note = sessao.snapshot?.editorial?.skus?.[sku];
    const skuStatus = document.getElementById('ai-training-sku-sync');
    if (skuStatus && (!note?.status || note.status === 'missing')) skuStatus.textContent = (data.characteristics || []).length || Object.keys(data.catalog_document || {}).length
        ? 'Orientação ainda não cadastrada. Informações do produto disponíveis abaixo.' : 'Orientação ainda não cadastrada.';
}

document.getElementById('btn-ai-training-sync-catalog')?.addEventListener('click', () => sincronizarCadastroTreinamento(true));
