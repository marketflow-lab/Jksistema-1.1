// Edições permanecem apenas na memória desta página e sempre vinculadas à loja exata.
const treinamentoSync = { stores: new Map(), requestId: 0, skuRequestId: 0 };

function treinamentoVisivel() {
    return !document.hidden && document.getElementById('aba-treinar-ai')?.classList.contains('active');
}

function sessaoTreinamento() {
    const store = lojaEscopoTreinamento();
    if (!store) return null;
    if (!treinamentoSync.stores.has(store)) treinamentoSync.stores.set(store, { snapshot: null, drafts: {}, error: '' });
    return treinamentoSync.stores.get(store);
}

function iguaisTreinamento(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

function valorGeralSnapshotTreinamento(data = {}) {
    return {
        orientacoes: data.orientacoes_perguntas ?? data.orientacoes ?? '',
        contexto_loja: data.contexto_loja || '',
        compatibilidade_autopecas: data.compatibilidade_autopecas || '',
        proibicoes: data.proibicoes || '',
        exemplos: exemplosDoSkuTreinamento(data.exemplos?.perguntas_anuncio, '')
    };
}

function valorGeralFormularioTreinamento() {
    return {
        orientacoes: aiTrainingOrientacoes.value || '',
        contexto_loja: aiTrainingContextoLoja.value || '',
        compatibilidade_autopecas: aiTrainingCompatibilidade.value || '',
        proibicoes: aiTrainingProibicoes.value || '',
        exemplos: exemplosDoSkuTreinamento(state.treinamentoDados.perguntas_anuncio?.exemplos, '')
    };
}

function valorSnapshotTreinamento(data, key) {
    return key === 'general' ? valorGeralSnapshotTreinamento(data) : {
        notas: normalizarNotasTreinamento(data?.notas_sku)[key.slice(4)] || '',
        exemplos: exemplosDoSkuTreinamento(data?.exemplos?.perguntas_anuncio, key.slice(4)),
        caracteristicas: data?.caracteristicas_sku?.[key.slice(4)] || {}
    };
}

function guardarEdicaoTreinamento() {
    const sessao = sessaoTreinamento();
    if (!sessao?.snapshot || !state.treinamentoCarregado) return;
    const valores = { general: valorGeralFormularioTreinamento() };
    const sku = String(state.treinamentoSkuNotasAtual || '');
    const exemplos = state.treinamentoDados.perguntas_anuncio?.exemplos || [];
    const skus = new Set([sku, ...exemplos.map(item => String(item.sku || '').trim()),
        ...(sessao.snapshot.exemplos?.perguntas_anuncio || []).map(item => String(item.sku || '').trim()),
        ...Object.keys(sessao.drafts).filter(key => key.startsWith('sku:')).map(key => key.slice(4))]);
    skus.forEach(item => {
        if (!item) return;
        const key = `sku:${item}`;
        valores[key] = {
            notas: item === sku ? (aiTrainingNotasSku.value || '') :
                (sessao.drafts[key]?.value.notas ?? valorSnapshotTreinamento(sessao.snapshot, key).notas),
            exemplos: exemplosDoSkuTreinamento(exemplos, item),
            caracteristicas: sessao.drafts[key]?.value.caracteristicas ?? valorSnapshotTreinamento(sessao.snapshot, key).caracteristicas
        };
    });
    Object.entries(valores).forEach(([key, value]) => {
        const existente = sessao.drafts[key];
        const base = existente ? existente.base : valorSnapshotTreinamento(sessao.snapshot, key);
        if (iguaisTreinamento(value, base) && !existente?.conflict) {
            delete sessao.drafts[key];
        } else {
            sessao.drafts[key] = { ...existente, value, base, revision: existente?.revision || sessao.snapshot.editorial?.revision };
        }
    });
}

function sincronizarLojaTreinamento() {
    const anterior = lojaEscopoTreinamento();
    montarSeletorEscopoTreinamento();
    if (anterior === lojaEscopoTreinamento()) return;
    treinamentoSync.requestId++;
    treinamentoSync.skuRequestId++;
    state.treinamentoCarregado = false;
    state.treinamentoSkuNotasAtual = '';
    state.produtosTreinamento = [];
    state.produtosTreinamentoCarregados = false;
    state.produtosTreinamentoEscopo = null;
    state.produtosTreinamentoErro = '';
    state.treinamentoDados = { perguntas_anuncio: { orientacoes: '', exemplos: [] }, pos_venda: { orientacoes: '', exemplos: [] } };
    state.treinamentoContexto = { contexto_loja: '', compatibilidade_autopecas: '', proibicoes: '', notas_sku: {} };
    [aiTrainingOrientacoes, aiTrainingContextoLoja, aiTrainingCompatibilidade, aiTrainingProibicoes, aiTrainingNotasSku, aiTrainingSku, aiTrainingSkuSearch].forEach((input) => { if (input) input.value = ''; });
    fecharBalaoSkuTreinamento();
    fecharEditorOrientacoesGerais(false);
    montarSeletorSkusTreinamento();
    renderizarExemplosTreinamento();
    limparChatTreinamento();
    renderizarConflitosTreinamento();
    controlesSalvarTreinamento(Boolean(sessaoTreinamento()?.saving));
    aiTrainingStatus.textContent = lojaEscopoTreinamento() ? 'Carregando orientações da loja...' : 'Selecione uma loja para consultar as orientações.';
}

function receberSnapshotTreinamento(data) {
    const sessao = sessaoTreinamento();
    if (!sessao) return;
    const anterior = sessao.snapshot;
    Object.entries(sessao.drafts).forEach(([key, draft]) => {
        const atual = valorSnapshotTreinamento(data, key);
        const caracteristicasAlteradas = key.startsWith('sku:') && !iguaisTreinamento(draft.value.caracteristicas, draft.base.caracteristicas);
        const fonteAlterada = caracteristicasAlteradas && anterior?.context_generation_id !== data.context_generation_id;
        if (fonteAlterada) draft.sourceConflict = true;
        if (!iguaisTreinamento(atual, draft.base) || fonteAlterada) draft.conflict = true;
        else if (!draft.conflict) draft.revision = data.editorial?.revision;
    });
    sessao.snapshot = data;
    if (data.sku_details_error) sessao.detailsError = typeof data.sku_details_error === 'string'
        ? data.sku_details_error : (data.sku_details_error.message || 'Informações salvas; não foi possível atualizar a ficha.');
    if (data.sku_details?.sku) {
        sessao.detailsError = '';
        sessao.skuDetails ??= {};
        sessao.skuDetails[data.sku_details.sku] = data.sku_details;
    }
    sessao.error = '';
    aplicarSnapshotTreinamento();
    if (anterior && anterior.context_generation_id !== data.context_generation_id) carregarSkusTreinamentoAI(true);
    if (!data.sku_details && anterior?.editorial?.revision !== data.editorial?.revision
        && aiTrainingSku.value && !aiTrainingSkuPopover?.classList.contains('hidden')) carregarDetalhesSkuTreinamento();
}

function aplicarSnapshotTreinamento() {
    const sessao = sessaoTreinamento();
    if (!sessao?.snapshot) return;
    const data = sessao.snapshot;
    const general = sessao.drafts.general?.value || valorGeralSnapshotTreinamento(data);
    const skus = new Set([
        ...(data.exemplos?.perguntas_anuncio || []).map(item => String(item.sku || '').trim()),
        ...Object.keys(sessao.drafts).filter(key => key.startsWith('sku:')).map(key => key.slice(4))
    ]);
    const exemplos = [...general.exemplos];
    skus.forEach(sku => {
        if (sku) exemplos.push(...(sessao.drafts[`sku:${sku}`]?.value.exemplos ?? exemplosDoSkuTreinamento(data.exemplos?.perguntas_anuncio, sku)));
    });
    state.treinamentoDados.perguntas_anuncio = { orientacoes: general.orientacoes, exemplos, updated_at: data.updated_at_perguntas || data.updated_at };
    state.treinamentoDados.pos_venda = { orientacoes: data.orientacoes_pos_venda || '', exemplos: normalizarExemplosTreinamento(data.exemplos?.pos_venda || []) };
    state.treinamentoContexto = { ...general, notas_sku: normalizarNotasTreinamento(data.notas_sku) };
    aiTrainingContextoLoja.value = general.contexto_loja;
    aiTrainingCompatibilidade.value = general.compatibilidade_autopecas;
    aiTrainingProibicoes.value = general.proibicoes;
    state.treinamentoCarregado = true;
    atualizarIndicadorPerfilTreinamento(data);
    renderizarTipoTreinamento(false);
    renderizarNotasSkuTreinamento();
    renderizarListaSkusTreinamento();
    if (aiTrainingSkuEditor?.classList.contains('hidden')) exibirLeituraOrientacaoSku();
    atualizarEstadoSincronizacaoTreinamento();
    renderizarConflitosTreinamento();
    renderizarFonteObsidianTreinamento();
    renderizarDetalhesSkuTreinamento();
}

function renderizarFonteObsidianTreinamento() {
    const metadata = sessaoTreinamento()?.snapshot?.editorial || {};
    for (const [target, note] of [['general', metadata.general], ['sku', metadata.skus?.[aiTrainingSku.value]]]) {
        const details = document.getElementById(`ai-training-${target}-source`);
        if (!details) continue;
        const body = typeof note?.source_body === 'string' ? note.source_body : '';
        details.classList.toggle('hidden', !body);
        details.querySelector('pre').textContent = body;
    }
}

function rotuloEstadoEditorialTreinamento(status, requiresCatalogSync = false) {
    if (requiresCatalogSync) return 'Pendente de sincronização do cadastro';
    return ({ published: 'Sincronizado', synced: 'Sincronizado', draft: 'Pendente de publicação', reviewed: 'Pendente de publicação', approved: 'Pendente de publicação', validated: 'Pendente de publicação', rejected: 'Revisão reprovada', pending_review: 'Pendente de publicação', conflict: 'Conflito', missing: 'Arquivo ausente', deleted: 'Arquivo excluído', invalid: 'Arquivo inválido' })[status] || 'Estado de publicação indisponível';
}

function atualizarEstadoSincronizacaoTreinamento() {
    const sessao = sessaoTreinamento();
    if (!sessao) return;
    if (sessao.error || sessao.saveError) {
        aiTrainingStatus.textContent = `Falha ao sincronizar: ${sessao.saveError || sessao.error}. As edições foram preservadas.`;
        return;
    }
    const drafts = Object.values(sessao.drafts);
    const metadata = sessao.snapshot?.editorial || {};
    const notas = [metadata.general, ...Object.values(metadata.skus || {})];
    const status = notas.map(item => item?.status);
    const catalogoPendente = notas.some(item => item?.requires_catalog_sync === true);
    const explicacaoCatalogo = 'A orientação está salva no Obsidian. Sincronize a base de conhecimento desta loja antes de publicar.';
    let label = metadata.general?.status ? 'Sincronizado com o Obsidian' : 'Estado de publicação indisponível';
    if (drafts.some(d => d.conflict) || status.includes('conflict')) label = 'Conflito: compare as versões antes de salvar';
    else if (status.includes('invalid')) label = 'Arquivo inválido no Obsidian';
    else if (status.includes('deleted')) label = 'Arquivo excluído no Obsidian';
    else if (status.includes('rejected')) label = 'Revisão reprovada: a IA continua usando a última versão publicada';
    else if (catalogoPendente) label = `Pendente de sincronização do cadastro: ${explicacaoCatalogo}`;
    else if (['draft', 'reviewed', 'approved', 'validated', 'pending_review'].some(s => status.includes(s))) label = 'Pendente de publicação: salvo no Obsidian. Revise e publique para ativar na IA';
    else if (status.some(s => s && !['published', 'synced', 'missing'].includes(s))) label = 'Estado de publicação indisponível';
    else if (metadata.general?.status === 'missing') label = 'Arquivo ausente: esta loja ainda não possui orientação geral no Obsidian';
    aiTrainingStatus.textContent = `${label.replace(/\.$/, '')}${drafts.length ? ' · Há edições locais não salvas.' : '.'}`;
    const generalStatus = document.getElementById('ai-training-general-sync');
    if (generalStatus) generalStatus.textContent = rotuloEstadoEditorialTreinamento(metadata.general?.status, metadata.general?.requires_catalog_sync)
        + (metadata.general?.requires_catalog_sync ? `. ${explicacaoCatalogo}` : '');
    const skuStatus = document.getElementById('ai-training-sku-sync');
    const notaSku = metadata.skus?.[aiTrainingSku.value];
    if (skuStatus) skuStatus.textContent = rotuloEstadoEditorialTreinamento(notaSku?.status || 'missing', notaSku?.requires_catalog_sync)
        + (notaSku?.requires_catalog_sync ? `. ${explicacaoCatalogo}` : '');
}

function textoComparacaoTreinamento(value) {
    if (typeof value === 'string') return value || '(Sem orientação)';
    if ('notas' in value) return `Orientações do SKU\n${value.notas || '(Vazio)'}\n\nCaracterísticas editadas\n${textoComparacaoCaracteristicasTreinamento(value.caracteristicas)}\n\nModelos\n${(value.exemplos || []).map(e => `${e.pergunta}\n${e.resposta}`).join('\n\n') || '(Vazio)'}`;
    return [ ['Orientações para perguntas', value.orientacoes], ['Base de conhecimento da loja', value.contexto_loja], ['Compatibilidade e autopeças', value.compatibilidade_autopecas], ['O que a IA nunca deve afirmar', value.proibicoes], ['Modelos', (value.exemplos || []).map(e => `${e.pergunta}\n${e.resposta}`).join('\n\n')] ]
        .map(([label, text]) => `${label}\n${text || '(Vazio)'}`).join('\n\n');
}

function renderizarConflitosTreinamento() {
    const sessao = sessaoTreinamento();
    for (const target of ['general', 'sku']) {
        const panel = document.getElementById(`ai-training-${target}-conflict`);
        if (!panel) continue;
        const key = target === 'sku' ? `sku:${aiTrainingSku.value}` : 'general';
        const draft = sessao?.drafts[key];
        panel.classList.toggle('hidden', !draft?.conflict);
        if (!draft?.conflict) { panel.replaceChildren(); continue; }
        const title = document.createElement('strong');
        title.textContent = draft.sourceConflict
            ? 'Conflito: as informações de origem do SKU mudaram durante sua edição. Confira a ficha atual antes de continuar.'
            : 'Conflito: o Obsidian mudou durante sua edição.';
        const versions = document.createElement('div');
        versions.className = 'training-conflict-versions';
        for (const [label, value] of [['Sua edição preservada', draft.value], ['Versão atual do Obsidian', valorSnapshotTreinamento(sessao.snapshot, key)]]) {
            const section = document.createElement('div');
            const heading = document.createElement('b'); heading.textContent = label;
            const pre = document.createElement('pre'); pre.textContent = textoComparacaoTreinamento(value);
            section.append(heading, pre); versions.append(section);
        }
        const reload = document.createElement('button'); reload.type = 'button'; reload.className = 'action-btn secondary';
        reload.textContent = 'Usar versão do Obsidian';
        reload.addEventListener('click', () => descartarEdicaoTreinamento(target));
        const keep = document.createElement('button'); keep.type = 'button'; keep.className = 'action-btn secondary';
        keep.textContent = 'Continuar com minha edição após comparar';
        keep.addEventListener('click', () => {
            draft.base = valorSnapshotTreinamento(sessao.snapshot, key);
            draft.revision = sessao.snapshot.editorial?.revision;
            draft.conflict = false;
            draft.sourceConflict = false;
            aplicarSnapshotTreinamento();
            if (target === 'general') abrirEditorOrientacoesGerais(); else editarOrientacaoSkuTreinamento();
        });
        panel.replaceChildren(title, versions, reload, keep);
    }
}

function descartarEdicaoTreinamento(target) {
    const sessao = sessaoTreinamento();
    if (!sessao) return;
    delete sessao.drafts[target === 'sku' ? `sku:${aiTrainingSku.value}` : 'general'];
    aplicarSnapshotTreinamento();
    if (target === 'sku') exibirLeituraOrientacaoSku();
}

function erroRespostaTreinamento(data, fallback) {
    return typeof data.detail === 'string' ? data.detail : (data.detail?.message || fallback);
}

function controlesSalvarTreinamento(disabled) {
    [btnAiTrainingSalvar, btnAiTrainingSimular, btnAiTrainingSalvarGerais, btnAiTrainingSalvarSku].forEach(button => { if (button) button.disabled = disabled; });
}

function atualizarTreinamentoVisivel(atualizarCatalogo = false) {
    if (!treinamentoVisivel()) return;
    if (atualizarCatalogo && aiTrainingSku.value && !aiTrainingSkuPopover?.classList.contains('hidden')) carregarDetalhesSkuTreinamento();
    else carregarTreinamentoAI(true);
    carregarSkusTreinamentoAI(atualizarCatalogo);
}

function iniciarSincronizacaoTreinamento() {
    [aiTrainingOrientacoes, aiTrainingContextoLoja, aiTrainingCompatibilidade, aiTrainingProibicoes, aiTrainingNotasSku].forEach(input => input?.addEventListener('input', () => {
        guardarEdicaoTreinamento();
        atualizarEstadoSincronizacaoTreinamento();
        renderizarConflitosTreinamento();
    }));
    window.setInterval(() => atualizarTreinamentoVisivel(), 5000);
    window.addEventListener('focus', () => atualizarTreinamentoVisivel(true));
    document.addEventListener('visibilitychange', () => atualizarTreinamentoVisivel(true));
    window.addEventListener('beforeunload', event => {
        guardarEdicaoTreinamento();
        if ([...treinamentoSync.stores.values()].some(s => Object.keys(s.drafts).length)) {
            event.preventDefault(); event.returnValue = '';
        }
    });
}

function formatarSkuExibicao(valor) {
    return String(valor || '').trim();
}

function obterNomeProdutoCadastro(item) {
    for (const campo of ['nome', 'produto', 'produto_bling', 'nome_bling']) {
        const valor = String((item || {})[campo] || '').trim();
        if (valor) return valor;
    }
    return 'Produto sem nome';
}

function obterFotoProdutoCadastro(item) {
    const foto = String((item || {}).foto || (item || {}).imagem || '').trim();
    if (!foto) return '';
    const helper = globalThis.JKAuthenticatedMedia;
    if (helper && typeof helper.normalizarUrlFotoCadastro === 'function') {
        const normalizada = helper.normalizarUrlFotoCadastro(foto);
        if (normalizada) return normalizada;
        if (/^(?:cadastro_fotos\/|\/api\/cadastro\/(?:foto-arquivo\/|foto\/))/i.test(foto)) return '';
    }
    if (/^(?:https?:)?\/\//i.test(foto) || /^(\/api\/|\/img\/)/i.test(foto)) return foto;
    return '';
}

function ehUrlFotoCadastroProtegida(url) {
    const helper = globalThis.JKAuthenticatedMedia;
    if (helper && typeof helper.ehUrlProtegidaCadastro === 'function') {
        return helper.ehUrlProtegidaCadastro(url);
    }
    return /^(\/api\/cadastro\/foto-arquivo\/|\/api\/cadastro\/foto\/)/i.test(String(url || '').trim());
}

function atributoSrcFotoCadastro(url) {
    const foto = escapeHtml(url);
    return ehUrlFotoCadastroProtegida(url)
        ? `data-jk-auth-src="${foto}"`
        : `src="${foto}"`;
}

function produtoTreinamentoSelecionado() {
    const sku = String(aiTrainingSku.value || '').trim();
    if (!sku) return null;
    return state.produtosTreinamento.find((item) => String(item.sku || '') === sku) || null;
}

function renderizarSkuTreinamentoInfo() {
    const produto = produtoTreinamentoSelecionado();
    if (!produto) {
        aiTrainingSkuInfo.classList.add('hidden');
        aiTrainingSkuInfo.innerHTML = '';
        return;
    }

    const nome = obterNomeProdutoCadastro(produto);
    const sku = formatarSkuExibicao(produto.sku);
    const foto = obterFotoProdutoCadastro(produto);
    const fotoHtml = foto
        ? `<img ${atributoSrcFotoCadastro(foto)} alt="${escapeHtml(nome)}" loading="lazy">`
        : '<span>Sem foto</span>';
    const meta = [
        produto.categoria ? `Categoria ${produto.categoria}` : '',
        produto.marca ? `Marca ${produto.marca}` : '',
        produto.mlb_ids ? `MLB ${produto.mlb_ids}` : ''
    ].filter(Boolean).join(' · ');

    aiTrainingSkuInfo.classList.remove('hidden');
    if (aiTrainingSkuPopoverTitle) {
        aiTrainingSkuPopoverTitle.textContent = `SKU ${sku} · ${nome}`;
    }
    aiTrainingSkuInfo.innerHTML = `
        <div class="training-sku-thumb">${fotoHtml}</div>
        <div>
            <div class="training-sku-name">SKU ${escapeHtml(sku)} · ${escapeHtml(nome)}</div>
            <div class="training-sku-meta">${escapeHtml(meta || 'Dados do cadastro serão enviados junto com a pergunta.')}</div>
        </div>
    `;
}

function normalizarNotasTreinamento(notas) {
    const origem = notas && typeof notas === 'object' ? notas : {};
    const normalizadas = {};
    Object.entries(origem).forEach(([sku, item]) => {
        const chave = String(sku || '').trim();
        if (!chave) return;
        const texto = typeof item === 'object'
            ? String((item || {}).notas || (item || {}).texto || '')
            : String(item || '');
        if (texto) normalizadas[chave] = texto;
    });
    return normalizadas;
}

function salvarNotasSkuTreinamentoAtual() {
    if (!aiTrainingNotasSku) return;
    const sku = String(state.treinamentoSkuNotasAtual || '').trim();
    if (!sku) return;
    const texto = aiTrainingNotasSku.value || '';
    if (texto.trim()) {
        state.treinamentoContexto.notas_sku[sku] = texto;
    } else {
        delete state.treinamentoContexto.notas_sku[sku];
    }
}

function renderizarNotasSkuTreinamento() {
    if (!aiTrainingNotasSku) return;
    const sku = String(aiTrainingSku.value || '').trim();
    state.treinamentoSkuNotasAtual = sku;
    aiTrainingNotasSku.disabled = !sku;
    aiTrainingNotasSku.value = sku ? (sessaoTreinamento()?.drafts[`sku:${sku}`]?.value.notas ?? state.treinamentoContexto.notas_sku[sku] ?? '') : '';
    renderizarConflitosTreinamento();
    aiTrainingNotasSku.placeholder = sku
        ? 'Aplicações confirmadas, códigos, variações, exceções e cuidados para este SKU.'
        : 'Selecione um SKU para salvar notas específicas.';
}

function orientacoesGeraisTreinamento() {
    return [
        ['Orientações para perguntas', aiTrainingOrientacoes?.value || ''],
        ['Base de conhecimento da loja', aiTrainingContextoLoja?.value || ''],
        ['Compatibilidade e autopeças', aiTrainingCompatibilidade?.value || ''],
        ['O que a IA nunca deve afirmar', aiTrainingProibicoes?.value || '']
    ];
}

function renderizarOrientacoesGeraisTreinamento() {
    if (!aiTrainingGeneralSummary) return;
    const preenchidas = orientacoesGeraisTreinamento()
        .map(([rotulo, texto]) => [rotulo, String(texto || '')])
        .filter(([, texto]) => texto.trim());
    if (!lojaEscopoTreinamento()) {
        aiTrainingGeneralSummary.innerHTML = '<div class="training-guidance-empty">Selecione uma loja para consultar suas orientações gerais.</div>';
        return;
    }
    if (!preenchidas.length) {
        aiTrainingGeneralSummary.innerHTML = '<div class="training-guidance-empty">Esta loja ainda não possui orientações gerais no Obsidian. Use “Adicionar orientação” para criar um rascunho.</div>';
        return;
    }
    aiTrainingGeneralSummary.innerHTML = preenchidas.map(([rotulo, texto]) => `
        <article class="training-guidance-item">
            <strong>${escapeHtml(rotulo)}</strong>
            <p>${escapeHtml(texto)}</p>
        </article>
    `).join('');
}

function abrirEditorOrientacoesGerais() {
    if (lojaEscopoTreinamento() && !state.treinamentoCarregado) {
        aiTrainingStatus.textContent = 'Aguarde o carregamento das orientações para editar.';
        return;
    }
    if (!lojaEscopoTreinamento()) {
        aiTrainingStatus.textContent = 'Selecione uma loja antes de adicionar ou editar orientações.';
        aiTrainingScope?.focus();
        return;
    }
    aiTrainingGeneralEditor?.classList.remove('hidden');
    aiTrainingGeneralSummary?.classList.add('hidden');
    btnAiTrainingAdicionarGeral?.classList.add('hidden');
    btnAiTrainingEditarGerais?.classList.add('hidden');
    aiTrainingOrientacoes?.focus();
}

function fecharEditorOrientacoesGerais(restaurar = false) {
    if (restaurar) {
        descartarEdicaoTreinamento('general');
        const dados = state.treinamentoDados[state.treinamentoTipo] || {};
        aiTrainingOrientacoes.value = dados.orientacoes || '';
        aiTrainingContextoLoja.value = state.treinamentoContexto.contexto_loja || '';
        aiTrainingCompatibilidade.value = state.treinamentoContexto.compatibilidade_autopecas || '';
        aiTrainingProibicoes.value = state.treinamentoContexto.proibicoes || '';
    }
    aiTrainingGeneralEditor?.classList.add('hidden');
    aiTrainingGeneralSummary?.classList.remove('hidden');
    btnAiTrainingAdicionarGeral?.classList.remove('hidden');
    btnAiTrainingEditarGerais?.classList.remove('hidden');
    renderizarOrientacoesGeraisTreinamento();
}

function textoBuscaSkuTreinamento(item) {
    return [
        item?.sku,
        obterNomeProdutoCadastro(item),
        item?.marca,
        item?.categoria,
        item?.mlb_ids
    ].map((valor) => String(valor || '').toLocaleLowerCase('pt-BR')).join(' ');
}

function renderizarListaSkusTreinamento() {
    if (!aiTrainingSkuList) return;
    if (state.produtosTreinamentoErro && lojaEscopoTreinamento()) {
        if (aiTrainingSkuCount) aiTrainingSkuCount.textContent = 'Falha ao carregar';
        aiTrainingSkuList.innerHTML = `<div class="training-guidance-empty">Falha ao carregar SKUs: ${escapeHtml(state.produtosTreinamentoErro)}</div>`;
        return;
    }
    const termo = String(aiTrainingSkuSearch?.value || '').trim().toLocaleLowerCase('pt-BR');
    const produtos = [...state.produtosTreinamento]
        .filter((item) => !termo || textoBuscaSkuTreinamento(item).includes(termo))
        .sort((a, b) => String(a.sku || '').localeCompare(String(b.sku || ''), undefined, {
            numeric: true,
            sensitivity: 'base'
        }));
    const total = state.produtosTreinamento.length;
    if (aiTrainingSkuCount) {
        aiTrainingSkuCount.textContent = termo
            ? `${produtos.length} de ${total} SKU(s)`
            : `${total} SKU(s)`;
    }
    if (!lojaEscopoTreinamento()) {
        if (aiTrainingSkuCount) aiTrainingSkuCount.textContent = 'Selecione uma loja';
        aiTrainingSkuList.innerHTML = '<div class="training-guidance-empty">Selecione uma loja para listar os SKUs.</div>';
        return;
    }
    if (!produtos.length) {
        aiTrainingSkuList.innerHTML = `<div class="training-guidance-empty">${termo ? 'Nenhum SKU encontrado nesta busca.' : 'Nenhum SKU cadastrado nesta loja.'}</div>`;
        return;
    }
    const selecionado = String(aiTrainingSku.value || '').trim();
    aiTrainingSkuList.innerHTML = produtos.map((item) => {
        const skuOriginal = String(item.sku || '').trim();
        const sku = formatarSkuExibicao(skuOriginal);
        const nome = obterNomeProdutoCadastro(item);
        const foto = obterFotoProdutoCadastro(item);
        const fotoHtml = foto
            ? `<img ${atributoSrcFotoCadastro(foto)} alt="${escapeHtml(nome)}" loading="lazy">`
            : '<span>Sem foto</span>';
        const possuiOrientacao = Boolean(String(state.treinamentoContexto.notas_sku[skuOriginal] || '').trim());
        const catalogoPendente = sessaoTreinamento()?.snapshot?.editorial?.skus?.[skuOriginal]?.requires_catalog_sync === true;
        return `
            <button class="training-sku-card${selecionado === skuOriginal ? ' selected' : ''}" type="button" data-training-sku="${escapeHtml(skuOriginal)}">
                <span class="training-sku-thumb">${fotoHtml}</span>
                <span class="training-sku-card-copy">
                    <strong>SKU ${escapeHtml(sku)}</strong>
                    <span>${escapeHtml(nome)}</span>
                    <em class="training-guidance-badge${possuiOrientacao ? ' configured' : ''}">${catalogoPendente ? 'Pendente de sincronização do cadastro' : possuiOrientacao ? 'Com orientação' : 'Sem orientação cadastrada'}</em>
                </span>
            </button>
        `;
    }).join('');
    aiTrainingSkuList.querySelectorAll('[data-training-sku]').forEach((button) => {
        button.addEventListener('click', () => abrirBalaoSkuTreinamento(button.dataset.trainingSku));
    });
}

function exibirLeituraOrientacaoSku() {
    atualizarEstadoSincronizacaoTreinamento();
    renderizarFonteObsidianTreinamento();
    const sku = String(aiTrainingSku.value || '').trim();
    const orientacao = String(state.treinamentoContexto.notas_sku[sku] || '');
    if (aiTrainingSkuGuidanceView) {
        aiTrainingSkuGuidanceView.innerHTML = orientacao
            ? `<article class="training-guidance-item"><strong>Orientação cadastrada</strong><p>${escapeHtml(orientacao)}</p></article>`
            : '<div class="training-guidance-empty">Este SKU ainda não possui orientação específica no Obsidian.</div>';
    }
    aiTrainingSkuEditor?.classList.add('hidden');
    aiTrainingSkuGuidanceView?.classList.remove('hidden');
    btnAiTrainingCancelarSku?.classList.add('hidden');
    btnAiTrainingSalvarSku?.classList.add('hidden');
    btnAiTrainingEditarSku?.classList.remove('hidden');
    if (btnAiTrainingEditarSku) {
        btnAiTrainingEditarSku.textContent = orientacao ? 'Editar orientação' : 'Adicionar orientação';
    }
    renderizarDetalhesSkuTreinamento(true);
}

function editarOrientacaoSkuTreinamento() {
    if (!state.treinamentoCarregado) {
        aiTrainingStatus.textContent = 'Aguarde o carregamento das orientações para editar.';
        return;
    }
    if (!aiTrainingSku.value) return;
    renderizarNotasSkuTreinamento();
    aiTrainingSkuEditor?.classList.remove('hidden');
    aiTrainingSkuGuidanceView?.classList.add('hidden');
    btnAiTrainingCancelarSku?.classList.remove('hidden');
    btnAiTrainingSalvarSku?.classList.remove('hidden');
    btnAiTrainingEditarSku?.classList.add('hidden');
    aiTrainingNotasSku?.focus();
}

function abrirBalaoSkuTreinamento(sku) {
    guardarEdicaoTreinamento();
    const valor = String(sku || '').trim();
    if (!valor || !Array.from(aiTrainingSku.options).some((option) => option.value === valor)) return;
    aiTrainingSku.value = valor;
    renderizarSkuTreinamentoInfo();
    renderizarNotasSkuTreinamento();
    exibirLeituraOrientacaoSku();
    renderizarListaSkusTreinamento();
    aiTrainingSkuPopover?.classList.remove('hidden');
    aiTrainingSkuPopover?.setAttribute('aria-hidden', 'false');
    if (sessaoTreinamento()?.drafts[`sku:${valor}`]) editarOrientacaoSkuTreinamento();
    renderizarConflitosTreinamento();
    btnAiTrainingFecharSku?.focus();
    carregarDetalhesSkuTreinamento(true);
}

function fecharBalaoSkuTreinamento() {
    guardarEdicaoTreinamento();
    aiTrainingSkuPopover?.classList.add('hidden');
    aiTrainingSkuPopover?.setAttribute('aria-hidden', 'true');
    exibirLeituraOrientacaoSku();
}

function normalizarExemplosTreinamento(exemplos) {
    // O snapshot editorial deve atravessar a tela sem perda de texto, campos ou modelos.
    return Array.isArray(exemplos) ? exemplos.map(item => ({ ...item })) : [];
}

function exemplosDoSkuTreinamento(exemplos, sku) {
    return normalizarExemplosTreinamento(exemplos).filter(item => String(item.sku || '').trim() === sku);
}

function renderizarExemplosTreinamento() {
    if (!aiTrainingExamplesList) return;
    const tipo = state.treinamentoTipo === 'pos_venda' ? 'pos_venda' : 'perguntas_anuncio';
    const exemplos = normalizarExemplosTreinamento((state.treinamentoDados[tipo] || {}).exemplos || []);
    state.treinamentoDados[tipo] = {
        ...(state.treinamentoDados[tipo] || {}),
        exemplos
    };

    if (!exemplos.length) {
        aiTrainingExamplesList.innerHTML = '<div class="training-chat-empty">Nenhum exemplo salvo para este tipo ainda.</div>';
        return;
    }

    aiTrainingExamplesList.innerHTML = exemplos.map((item, index) => {
        const skuModelo = String(item.sku || '').trim();
        const escopoClasse = skuModelo ? 'sku' : 'geral';
        const escopoTexto = skuModelo ? `SKU ${formatarSkuExibicao(skuModelo)}` : 'Geral';
        return `
        <div class="training-example-item">
            <strong><span class="training-example-badge ${escopoClasse}">${escapeHtml(escopoTexto)}</span> Modelo ${index + 1}</strong>
            <p><b>Pergunta:</b> ${escapeHtml(item.pergunta)}</p>
            <p><b>Resposta:</b> ${escapeHtml(item.resposta)}</p>
            <button class="mini-btn" type="button" data-remove-training-example="${index}">Remover</button>
        </div>
    `;
    }).join('');

    aiTrainingExamplesList.querySelectorAll('[data-remove-training-example]').forEach((button) => {
        button.addEventListener('click', async () => {
            if (sessaoTreinamento()?.saving) return;
            const idx = Number(button.dataset.removeTrainingExample);
            const listaAtual = normalizarExemplosTreinamento((state.treinamentoDados[tipo] || {}).exemplos || []);
            const [removido] = listaAtual.splice(idx, 1);
            state.treinamentoDados[tipo] = {
                ...(state.treinamentoDados[tipo] || {}),
                exemplos: listaAtual
            };
            guardarEdicaoTreinamento();
            renderizarExemplosTreinamento();
            try { await salvarTreinamentoAI(removido?.sku ? 'sku' : 'general', String(removido?.sku || '')); }
            catch (_error) { /* O salvamento preserva o rascunho e exibe a falha. */ }
        });
    });
}

async function adicionarExemploTreinamento() {
    if (sessaoTreinamento()?.saving) return;
    if (!state.treinamentoCarregado) {
        aiTrainingStatus.textContent = 'Selecione uma loja e aguarde as orientações antes de adicionar modelos.';
        return;
    }
    const pergunta = String(aiTrainingExemploPergunta?.value || '').trim();
    const resposta = String(aiTrainingExemploResposta?.value || '').trim();
    if (!pergunta || !resposta) {
        aiTrainingStatus.textContent = 'Informe pergunta e resposta para adicionar um modelo.';
        return;
    }
    const escopo = String(aiTrainingExemploEscopo?.value || 'geral');
    const skuModelo = escopo === 'sku' ? String(aiTrainingSku.value || '').trim() : '';
    if (escopo === 'sku' && !skuModelo) {
        aiTrainingStatus.textContent = 'Escolha um SKU antes de adicionar um modelo específico.';
        aiTrainingSku?.focus();
        return;
    }
    const tipo = state.treinamentoTipo === 'pos_venda' ? 'pos_venda' : 'perguntas_anuncio';
    const exemplos = normalizarExemplosTreinamento((state.treinamentoDados[tipo] || {}).exemplos || []);
    exemplos.unshift({
        pergunta,
        resposta,
        sku: skuModelo,
        observacao: skuModelo ? `Modelo específico do SKU ${formatarSkuExibicao(skuModelo)}` : 'Modelo geral para todos os SKUs',
        updated_at: new Date().toISOString()
    });
    state.treinamentoDados[tipo] = {
        ...(state.treinamentoDados[tipo] || {}),
        exemplos
    };
    guardarEdicaoTreinamento();
    aiTrainingExemploPergunta.value = '';
    aiTrainingExemploResposta.value = '';
    renderizarExemplosTreinamento();
    try { await salvarTreinamentoAI(skuModelo ? 'sku' : 'general', skuModelo); }
    catch (_error) { /* O modelo continua no rascunho para comparar ou tentar novamente. */ }
}

function montarSeletorSkusTreinamento(skuAnterior = aiTrainingSku.value) {
    const skuAtual = String(skuAnterior || '').trim();
    aiTrainingSku.innerHTML = '';
    const optDefault = document.createElement('option');
    optDefault.value = '';
    optDefault.textContent = 'Nenhum SKU selecionado';
    aiTrainingSku.appendChild(optDefault);

    const ordenados = [...state.produtosTreinamento].sort((a, b) => (
        String(a.sku || '').localeCompare(String(b.sku || ''), undefined, { numeric: true, sensitivity: 'base' })
    ));
    ordenados.forEach((item) => {
        const sku = String(item.sku || '').trim();
        if (!sku) return;
        const option = document.createElement('option');
        option.value = sku;
        option.textContent = `${formatarSkuExibicao(sku)} - ${obterNomeProdutoCadastro(item)}`;
        aiTrainingSku.appendChild(option);
    });
    aiTrainingSku.value = Array.from(aiTrainingSku.options).some((option) => option.value === skuAtual)
        ? skuAtual
        : '';
    renderizarSkuTreinamentoInfo();
    renderizarNotasSkuTreinamento();
    renderizarListaSkusTreinamento();
}

async function carregarSkusTreinamentoAI(forcar = false) {
    const lojaEscopo = String(lojaEscopoTreinamento() || '').trim();
    if (
        !forcar && state.produtosTreinamentoCarregados
        && Date.now() - (state.produtosTreinamentoAtualizadosEm || 0) < 30000
        && state.produtosTreinamentoEscopo === lojaEscopo
    ) {
        renderizarListaSkusTreinamento();
        return;
    }
    if (!lojaEscopo) {
        state.produtosTreinamento = [];
        state.produtosTreinamentoCarregados = false;
        state.produtosTreinamentoEscopo = null;
        montarSeletorSkusTreinamento();
        return;
    }
    const sessao = sessaoTreinamento();
    if (sessao?.catalogLoading) return;
    if (sessao) sessao.catalogLoading = true;
    const requestId = ++treinamentoSync.skuRequestId;
    const skuAnterior = aiTrainingSku.value;
    if (!state.produtosTreinamento.length) aiTrainingSku.innerHTML = '<option value="">Carregando SKUs...</option>';
    if (aiTrainingSkuCount) aiTrainingSkuCount.textContent = 'Carregando SKUs...';
    if (aiTrainingSkuList) aiTrainingSkuList.innerHTML = '<div class="training-guidance-empty">Carregando SKUs da loja...</div>';
    try {
        const params = new URLSearchParams();
        if (lojaEscopo) {
            params.set('store_id', lojaEscopo);
            params.set('loja', nomeLojaEscopoTreinamento());
        }
        const url = `/api/mercadolivre/ia-treinamento/skus${params.toString() ? `?${params.toString()}` : ''}`;
        const response = await fetch(url, {
            headers: obterAuthHeaders(),
            cache: 'no-store', signal: AbortSignal.timeout(15000)
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Erro ao carregar SKUs.');
        if (lojaEscopo !== lojaEscopoTreinamento() || requestId !== treinamentoSync.skuRequestId) return;
        const produtos = Array.isArray(data.produtos) ? data.produtos : [];
        state.produtosTreinamento = produtos.filter((item) => String((item || {}).sku || '').trim());
        state.produtosTreinamentoErro = '';
        state.produtosTreinamentoAtualizadosEm = Date.now();
        state.produtosTreinamentoCarregados = true;
        state.produtosTreinamentoEscopo = lojaEscopo;
        montarSeletorSkusTreinamento(skuAnterior);
    } catch (error) {
        if (lojaEscopo !== lojaEscopoTreinamento() || requestId !== treinamentoSync.skuRequestId) return;
        state.produtosTreinamentoErro = mensagemErro(error);
        state.produtosTreinamentoCarregados = false;
        aiTrainingSku.innerHTML = '<option value="">Erro ao carregar SKUs</option>';
        aiTrainingSkuInfo.classList.add('hidden');
        aiTrainingSkuInfo.innerHTML = '';
        if (aiTrainingSkuCount) aiTrainingSkuCount.textContent = 'Falha ao carregar';
        if (aiTrainingSkuList) aiTrainingSkuList.innerHTML = `<div class="training-guidance-empty">${escapeHtml(mensagemErro(error))}</div>`;
        aiTrainingStatus.textContent = `Erro ao carregar SKUs: ${mensagemErro(error)}`;
    } finally {
        if (sessao) sessao.catalogLoading = false;
    }
}

function rotuloTipoTreinamento(tipo = state.treinamentoTipo) {
    return tipo === 'pos_venda' ? 'pós-venda' : 'perguntas de anúncio';
}

function atualizarIndicadorPerfilTreinamento(data = {}) {
    const indicador = document.getElementById('ai-training-profile-status');
    if (!indicador) return;
    const methodVersion = String(data.method_version || indicador.dataset.methodVersion || 'seller-conversion-v1');
    const profileVersion = Number(data.profile_version || indicador.dataset.profileVersion || 2);
    const profileActive = data.profile_active !== false;
    const profileScope = String(data.profile_scope || (lojaEscopoTreinamento() ? 'store' : 'global'));
    state.treinamentoProfileMetadata = {
        method_version: methodVersion,
        profile_version: profileVersion,
        profile_active: profileActive,
        profile_scope: profileScope
    };
    indicador.dataset.methodVersion = methodVersion;
    indicador.dataset.profileVersion = String(profileVersion);
    indicador.dataset.profileScope = profileScope;
    indicador.dataset.profileActive = profileActive ? 'true' : 'false';
    const titulo = indicador.querySelector('.training-sku-name');
    const detalhe = indicador.querySelector('.training-sku-meta');
    if (titulo) titulo.textContent = `Método RVC v6 ${profileActive ? 'ativo' : 'indisponível'} · perfil de vendedor v${profileVersion}`;
    if (detalhe) {
        const escopo = profileScope === 'store' ? 'loja selecionada' : profileScope === 'sku' ? 'SKU selecionado' : 'todas as lojas';
        detalhe.textContent = `Personalização aplicada ao escopo ${escopo}. A IA esclarece primeiro e só conduz à compra com adequação comprovada; instruções salvas não alteram políticas, pesquisa ou fatos atuais.`;
    }
}

function sincronizarOrientacoesTreinamentoAtual() {
    const tipo = state.treinamentoTipo === 'pos_venda' ? 'pos_venda' : 'perguntas_anuncio';
    state.treinamentoDados[tipo] = {
        ...(state.treinamentoDados[tipo] || {}),
        orientacoes: aiTrainingOrientacoes.value || '',
        exemplos: normalizarExemplosTreinamento((state.treinamentoDados[tipo] || {}).exemplos || [])
    };
    state.treinamentoContexto.contexto_loja = aiTrainingContextoLoja.value || '';
    state.treinamentoContexto.compatibilidade_autopecas = aiTrainingCompatibilidade.value || '';
    state.treinamentoContexto.proibicoes = aiTrainingProibicoes.value || '';
    salvarNotasSkuTreinamentoAtual();
}

function limparChatTreinamento() {
    if (!aiTrainingChat) return;
    aiTrainingChat.innerHTML = '<div class="training-chat-empty">Digite a pergunta do comprador para testar como a IA responderia.</div>';
    aiTrainingPergunta.value = '';
}

function atualizarStatusTreinamentoTipo() {
    const dados = state.treinamentoDados[state.treinamentoTipo] || {};
    const rotulo = rotuloTipoTreinamento();
    const escopo = rotuloEscopoTreinamento();
    if (dados.updated_at) {
        aiTrainingStatus.textContent = `Orientacoes de ${rotulo} (${escopo}) salvas em ${formatarData(dados.updated_at)}`;
    } else if (dados.orientacoes) {
        aiTrainingStatus.textContent = `Orientacoes de ${rotulo} (${escopo}) carregadas.`;
    } else {
        aiTrainingStatus.textContent = `Nenhuma orientacao de ${rotulo} (${escopo}) salva ainda.`;
    }
}

function renderizarTipoTreinamento(limparChat = false) {
    const tipo = state.treinamentoTipo === 'pos_venda' ? 'pos_venda' : 'perguntas_anuncio';
    const dados = state.treinamentoDados[tipo] || { orientacoes: '', updated_at: null };

    aiTrainingTypeTabs.forEach((button) => {
        const ativo = button.dataset.trainingType === tipo;
        button.classList.toggle('active', ativo);
        button.setAttribute('aria-selected', ativo ? 'true' : 'false');
    });

    aiTrainingOrientacoesLabel.textContent = tipo === 'pos_venda'
        ? 'Orientações para pós-venda'
        : 'Orientações para perguntas de anúncio';
    aiTrainingOrientacoes.placeholder = tipo === 'pos_venda'
        ? 'Ex.: agradecer a compra, pedir fotos ou vídeo quando necessário, orientar garantia e troca sem prometer aprovação...'
        : 'Ex.: responder sem Markdown, não prometer prazo sem confirmação, pedir modelo/ano quando a aplicação do produto for incerta...';
    if (aiTrainingChatHead) {
        aiTrainingChatHead.textContent = tipo === 'pos_venda'
            ? 'Simulação de pós-venda'
            : 'Simulação de pergunta de anúncio';
    }
    aiTrainingOrientacoes.value = dados.orientacoes || '';
    renderizarExemplosTreinamento();
    renderizarOrientacoesGeraisTreinamento();
    atualizarStatusTreinamentoTipo();
    if (limparChat) limparChatTreinamento();
}

function trocarTipoTreinamento(tipo) {
    const novoTipo = 'perguntas_anuncio';
    if (novoTipo === state.treinamentoTipo) return;
    sincronizarOrientacoesTreinamentoAtual();
    state.treinamentoTipo = novoTipo;
    renderizarTipoTreinamento(true);
}

async function carregarTreinamentoAI(forcar = false) {
    const lojaEscopo = lojaEscopoTreinamento();
    if (!lojaEscopo) {
        aiTrainingStatus.textContent = 'Selecione uma loja para consultar as orientações.';
        renderizarOrientacoesGeraisTreinamento();
        renderizarListaSkusTreinamento();
        return;
    }
    const sessao = sessaoTreinamento();
    if (sessao.loading || sessao.detailsLoading || sessao.saving || (!forcar && state.treinamentoCarregado)) return;
    const requestId = ++treinamentoSync.requestId;
    sessao.loading = true;
    if (!sessao.snapshot) aiTrainingStatus.textContent = `Carregando orientações (${rotuloEscopoTreinamento()})...`;
    try {
        const params = new URLSearchParams();
        params.set('store_id', lojaEscopo);
        params.set('loja', nomeLojaEscopoTreinamento());
        const response = await fetch(`/api/mercadolivre/ia-treinamento?${params}`, {
            headers: obterAuthHeaders(), cache: 'no-store', signal: AbortSignal.timeout(15000)
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(erroRespostaTreinamento(data, 'Erro ao carregar orientações.'));
        if (lojaEscopo !== lojaEscopoTreinamento() || requestId !== treinamentoSync.requestId) return;
        guardarEdicaoTreinamento();
        receberSnapshotTreinamento(data);
    } catch (error) {
        if (lojaEscopo !== lojaEscopoTreinamento() || requestId !== treinamentoSync.requestId) return;
        sessao.error = mensagemErro(error);
        atualizarEstadoSincronizacaoTreinamento();
    } finally {
        sessao.loading = false;
    }
}

async function salvarTreinamentoAI(editTarget = 'general', targetSku = aiTrainingSku.value) {
    guardarEdicaoTreinamento();
    const lojaEscopo = lojaEscopoTreinamento();
    const sessao = sessaoTreinamento();
    const sku = editTarget === 'sku' ? String(targetSku || '') : '';
    const key = editTarget === 'sku' ? `sku:${sku}` : 'general';
    if (!lojaEscopo || !sessao?.snapshot || (editTarget === 'sku' && !sku)) {
        aiTrainingStatus.textContent = 'Aguarde as orientações da loja antes de salvar.';
        throw new Error('Orientações ainda não carregadas.');
    }
    const draft = sessao.drafts[key];
    if (draft?.conflict) {
        renderizarConflitosTreinamento();
        aiTrainingStatus.textContent = 'Conflito: compare a edição com a versão atual do Obsidian antes de salvar.';
        throw new Error('Conflito de edição.');
    }
    const revision = draft?.revision || sessao.snapshot.editorial?.revision;
    const general = valorGeralFormularioTreinamento();
    const characteristicEdits = editTarget === 'sku'
        ? draft?.value.caracteristicas ?? valorSnapshotTreinamento(sessao.snapshot, key).caracteristicas : {};
    const characteristicsChanged = editTarget === 'sku' && !iguaisTreinamento(
        characteristicEdits, valorSnapshotTreinamento(sessao.snapshot, key).caracteristicas);
    const payload = {
        tipo: state.treinamentoTipo, loja: nomeLojaEscopoTreinamento(), store_id: lojaEscopo,
        edit_target: editTarget, expected_revision: revision, sku,
        ...(editTarget === 'sku' ? {
            notas_sku: draft?.value.notas ?? valorSnapshotTreinamento(sessao.snapshot, key).notas,
            exemplos: exemplosDoSkuTreinamento(state.treinamentoDados.perguntas_anuncio?.exemplos, sku),
            ...(characteristicsChanged ? { caracteristicas_sku: characteristicEdits } : {})
        } : general)
    };
    const savedValue = editTarget === 'sku' ? { notas: payload.notas_sku, exemplos: payload.exemplos, caracteristicas: characteristicEdits } : general;
    sessao.saving = true;
    sessao.saveError = '';
    treinamentoSync.requestId++;
    controlesSalvarTreinamento(true);
    aiTrainingStatus.textContent = `Salvando no Obsidian (${rotuloEscopoTreinamento()})...`;
    try {
        const response = await fetch('/api/mercadolivre/ia-treinamento', {
            method: 'POST', headers: { ...obterAuthHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify(payload), signal: AbortSignal.timeout(20000)
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (response.status === 409) {
                if (sessao.drafts[key]) sessao.drafts[key].conflict = true;
                sessao.error = '';
            }
            const error = new Error(erroRespostaTreinamento(data, 'Erro ao salvar orientações.'));
            error.conflict = response.status === 409;
            throw error;
        }
        if (lojaEscopo === lojaEscopoTreinamento()) guardarEdicaoTreinamento();
        if (iguaisTreinamento(sessao.drafts[key]?.value ?? savedValue, savedValue)) delete sessao.drafts[key];
        if (lojaEscopo !== lojaEscopoTreinamento()) {
            sessao.snapshot = data;
            return data;
        }
        receberSnapshotTreinamento(data);
        return data;
    } catch (error) {
        if (lojaEscopo === lojaEscopoTreinamento()) {
            sessao.saveError = error.conflict ? '' : mensagemErro(error);
            atualizarEstadoSincronizacaoTreinamento();
            renderizarConflitosTreinamento();
        }
        throw error;
    } finally {
        sessao.saving = false;
        if (lojaEscopo === lojaEscopoTreinamento()) {
            controlesSalvarTreinamento(false);
            carregarTreinamentoAI(true);
        }
    }
}

async function salvarOrientacoesGeraisTreinamento() {
    const store = lojaEscopoTreinamento();
    try {
        await salvarTreinamentoAI('general');
        if (store === lojaEscopoTreinamento() && !sessaoTreinamento()?.drafts.general) fecharEditorOrientacoesGerais(false);
    } catch (_error) { /* Preserva a edição e o estado de sincronização. */ }
}

async function salvarOrientacaoSkuTreinamento() {
    const store = lojaEscopoTreinamento();
    const sku = aiTrainingSku.value;
    try {
        await salvarTreinamentoAI('sku');
        if (store === lojaEscopoTreinamento() && sku === aiTrainingSku.value && !sessaoTreinamento()?.drafts[`sku:${sku}`]) exibirLeituraOrientacaoSku();
    } catch (_error) { /* Preserva a edição e o estado de sincronização. */ }
}

function adicionarMensagemTreinamentoChat(tipo, texto, opcoes = {}) {
    if (!aiTrainingChat) {
        return {
            setText() {},
            setError() {}
        };
    }

    const vazio = aiTrainingChat.querySelector('.training-chat-empty');
    if (vazio) vazio.remove();

    const mensagem = document.createElement('div');
    mensagem.className = `training-message ${tipo === 'user' ? 'user' : 'assistant'}${opcoes.error ? ' error' : ''}`;

    const papel = document.createElement('span');
    papel.className = 'training-message-role';
    papel.textContent = tipo === 'user' ? 'Comprador' : 'IA';

    const corpo = document.createElement('div');
    corpo.className = 'training-message-body';
    corpo.textContent = texto || '';

    mensagem.appendChild(papel);
    mensagem.appendChild(corpo);
    aiTrainingChat.appendChild(mensagem);
    aiTrainingChat.scrollTop = aiTrainingChat.scrollHeight;

    return {
        setText(novoTexto) {
            corpo.textContent = novoTexto || '';
            aiTrainingChat.scrollTop = aiTrainingChat.scrollHeight;
        },
        setError(novoTexto) {
            mensagem.classList.add('error');
            corpo.textContent = novoTexto || '';
            aiTrainingChat.scrollTop = aiTrainingChat.scrollHeight;
        }
    };
}

async function simularTreinamentoAI() {
    const pergunta = (aiTrainingPergunta.value || '').trim();
    if (!pergunta) {
        aiTrainingStatus.textContent = 'Digite uma pergunta para simular.';
        aiTrainingPergunta.focus();
        return;
    }

    adicionarMensagemTreinamentoChat('user', pergunta);
    aiTrainingPergunta.value = '';
    const respostaChat = adicionarMensagemTreinamentoChat('assistant', 'Gerando resposta...');
    try {
        btnAiTrainingSalvar.disabled = true;
        btnAiTrainingSimular.disabled = true;
        aiTrainingStatus.textContent = 'Gerando resposta de simulação...';

        const response = await fetch('/api/mercadolivre/ia-treinamento/simular', {
            method: 'POST',
            headers: {
                ...obterAuthHeaders(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                pergunta,
                tipo: state.treinamentoTipo,
                loja: nomeLojaEscopoTreinamento(),
                store_id: lojaEscopoTreinamento(),
                sku: aiTrainingSku.value || '',
                contexto: aiTrainingContexto.value || ''
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Erro ao simular resposta.');

        respostaChat.setText(data.resposta || 'A IA nao retornou uma resposta para esta simulacao.');
        aiTrainingStatus.textContent = 'Simulação gerada com as orientações publicadas. Alterações pendentes precisam de revisão e publicação.';
    } catch (error) {
        respostaChat.setError(`Erro na simulação: ${mensagemErro(error)}`);
        aiTrainingStatus.textContent = `Erro na simulação: ${mensagemErro(error)}`;
    } finally {
        btnAiTrainingSalvar.disabled = false;
        btnAiTrainingSimular.disabled = false;
    }
}
