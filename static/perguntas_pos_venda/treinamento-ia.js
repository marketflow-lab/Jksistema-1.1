function formatarSkuExibicao(valor) {
    const sku = String(valor || '').trim();
    if (/^0\d{2}$/.test(sku)) {
        const numero = parseInt(sku, 10);
        if (numero >= 10 && numero <= 99) return String(numero);
    }
    return sku;
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
            ? String((item || {}).notas || (item || {}).texto || '').trim()
            : String(item || '').trim();
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
    aiTrainingNotasSku.value = sku ? (state.treinamentoContexto.notas_sku[sku] || '') : '';
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
        .map(([rotulo, texto]) => [rotulo, String(texto || '').trim()])
        .filter(([, texto]) => texto);
    if (!lojaEscopoTreinamento()) {
        aiTrainingGeneralSummary.innerHTML = '<div class="training-guidance-empty">Selecione uma loja para consultar suas orientações gerais.</div>';
        return;
    }
    if (!preenchidas.length) {
        aiTrainingGeneralSummary.innerHTML = '<div class="training-guidance-empty">Esta loja ainda não possui orientações gerais publicadas. Use “Adicionar orientação” para criar um rascunho.</div>';
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
        return `
            <button class="training-sku-card${selecionado === skuOriginal ? ' selected' : ''}" type="button" data-training-sku="${escapeHtml(skuOriginal)}">
                <span class="training-sku-thumb">${fotoHtml}</span>
                <span class="training-sku-card-copy">
                    <strong>SKU ${escapeHtml(sku)}</strong>
                    <span>${escapeHtml(nome)}</span>
                    <em class="training-guidance-badge${possuiOrientacao ? ' configured' : ''}">${possuiOrientacao ? 'Com orientação' : 'Adicionar orientação'}</em>
                </span>
            </button>
        `;
    }).join('');
    aiTrainingSkuList.querySelectorAll('[data-training-sku]').forEach((button) => {
        button.addEventListener('click', () => abrirBalaoSkuTreinamento(button.dataset.trainingSku));
    });
}

function exibirLeituraOrientacaoSku() {
    const sku = String(aiTrainingSku.value || '').trim();
    const orientacao = String(state.treinamentoContexto.notas_sku[sku] || '').trim();
    if (aiTrainingSkuGuidanceView) {
        aiTrainingSkuGuidanceView.innerHTML = orientacao
            ? `<article class="training-guidance-item"><strong>Orientação cadastrada</strong><p>${escapeHtml(orientacao)}</p></article>`
            : '<div class="training-guidance-empty">Este SKU ainda não possui orientação específica publicada.</div>';
    }
    aiTrainingSkuEditor?.classList.add('hidden');
    aiTrainingSkuGuidanceView?.classList.remove('hidden');
    btnAiTrainingCancelarSku?.classList.add('hidden');
    btnAiTrainingSalvarSku?.classList.add('hidden');
    btnAiTrainingEditarSku?.classList.remove('hidden');
    if (btnAiTrainingEditarSku) {
        btnAiTrainingEditarSku.textContent = orientacao ? 'Editar orientação' : 'Adicionar orientação';
    }
}

function editarOrientacaoSkuTreinamento() {
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
    const valor = String(sku || '').trim();
    if (!valor || !Array.from(aiTrainingSku.options).some((option) => option.value === valor)) return;
    aiTrainingSku.value = valor;
    renderizarSkuTreinamentoInfo();
    renderizarNotasSkuTreinamento();
    exibirLeituraOrientacaoSku();
    renderizarListaSkusTreinamento();
    aiTrainingSkuPopover?.classList.remove('hidden');
    aiTrainingSkuPopover?.setAttribute('aria-hidden', 'false');
    btnAiTrainingFecharSku?.focus();
}

function fecharBalaoSkuTreinamento() {
    aiTrainingSkuPopover?.classList.add('hidden');
    aiTrainingSkuPopover?.setAttribute('aria-hidden', 'true');
    exibirLeituraOrientacaoSku();
}

function normalizarExemplosTreinamento(exemplos) {
    return (Array.isArray(exemplos) ? exemplos : [])
        .map((item) => ({
            pergunta: String((item || {}).pergunta || '').trim(),
            resposta: String((item || {}).resposta || '').trim(),
            sku: String((item || {}).sku || '').trim(),
            observacao: String((item || {}).observacao || '').trim(),
            updated_at: (item || {}).updated_at || null
        }))
        .filter((item) => item.pergunta && item.resposta)
        .slice(0, 60);
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
        button.addEventListener('click', () => {
            const idx = Number(button.dataset.removeTrainingExample);
            const listaAtual = normalizarExemplosTreinamento((state.treinamentoDados[tipo] || {}).exemplos || []);
            listaAtual.splice(idx, 1);
            state.treinamentoDados[tipo] = {
                ...(state.treinamentoDados[tipo] || {}),
                exemplos: listaAtual
            };
            renderizarExemplosTreinamento();
        });
    });
}

function adicionarExemploTreinamento() {
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
        exemplos: exemplos.slice(0, 60)
    };
    aiTrainingExemploPergunta.value = '';
    aiTrainingExemploResposta.value = '';
    renderizarExemplosTreinamento();
    aiTrainingStatus.textContent = skuModelo
        ? `Modelo do SKU ${formatarSkuExibicao(skuModelo)} adicionado. Clique em salvar para manter no servidor.`
        : 'Modelo geral adicionado. Clique em salvar para manter no servidor.';
}

function montarSeletorSkusTreinamento() {
    const skuAtual = String(aiTrainingSku.value || '').trim();
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

async function carregarSkusTreinamentoAI() {
    const lojaEscopo = String(lojaEscopoTreinamento() || '').trim();
    if (
        state.produtosTreinamentoCarregados
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
    aiTrainingSku.innerHTML = '<option value="">Carregando SKUs...</option>';
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
            cache: 'no-store'
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Erro ao carregar SKUs.');
        if (lojaEscopo !== lojaEscopoTreinamento()) return;
        const produtos = Array.isArray(data.produtos) ? data.produtos : [];
        state.produtosTreinamento = produtos.filter((item) => String((item || {}).sku || '').trim());
        state.produtosTreinamentoCarregados = true;
        state.produtosTreinamentoEscopo = lojaEscopo;
        montarSeletorSkusTreinamento();
    } catch (error) {
        if (lojaEscopo !== lojaEscopoTreinamento()) return;
        aiTrainingSku.innerHTML = '<option value="">Erro ao carregar SKUs</option>';
        aiTrainingSkuInfo.classList.add('hidden');
        aiTrainingSkuInfo.innerHTML = '';
        if (aiTrainingSkuCount) aiTrainingSkuCount.textContent = 'Falha ao carregar';
        if (aiTrainingSkuList) aiTrainingSkuList.innerHTML = `<div class="training-guidance-empty">${escapeHtml(mensagemErro(error))}</div>`;
        aiTrainingStatus.textContent = `Erro ao carregar SKUs: ${mensagemErro(error)}`;
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
    if (!forcar && state.treinamentoCarregado) return;
    montarSeletorEscopoTreinamento();
    const lojaEscopo = lojaEscopoTreinamento();
    if (!lojaEscopo) {
        aiTrainingStatus.textContent = 'Selecione uma loja para consultar as orientações.';
        renderizarOrientacoesGeraisTreinamento();
        renderizarListaSkusTreinamento();
        return;
    }
    const params = new URLSearchParams();
    if (lojaEscopo) {
        params.set('store_id', lojaEscopo);
        params.set('loja', nomeLojaEscopoTreinamento());
    }
    const url = `/api/mercadolivre/ia-treinamento${params.toString() ? `?${params.toString()}` : ''}`;
    aiTrainingStatus.textContent = `Carregando orientacoes (${rotuloEscopoTreinamento()})...`;
    try {
        const response = await fetch(url, {
            headers: obterAuthHeaders(),
            cache: 'no-store'
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Erro ao carregar orientações.');
        if (lojaEscopo !== lojaEscopoTreinamento()) return;

        state.treinamentoDados.perguntas_anuncio = {
            orientacoes: data.orientacoes_perguntas || data.orientacoes || '',
            updated_at: data.updated_at_perguntas || data.updated_at || null,
            exemplos: normalizarExemplosTreinamento(((data.exemplos || {}).perguntas_anuncio) || [])
        };
        state.treinamentoDados.pos_venda = {
            orientacoes: data.orientacoes_pos_venda || '',
            updated_at: data.updated_at_pos_venda || null,
            exemplos: normalizarExemplosTreinamento(((data.exemplos || {}).pos_venda) || [])
        };
        state.treinamentoContexto = {
            contexto_loja: data.contexto_loja || '',
            compatibilidade_autopecas: data.compatibilidade_autopecas || '',
            proibicoes: data.proibicoes || '',
            notas_sku: normalizarNotasTreinamento(data.notas_sku)
        };
        aiTrainingContextoLoja.value = state.treinamentoContexto.contexto_loja;
        aiTrainingCompatibilidade.value = state.treinamentoContexto.compatibilidade_autopecas;
        aiTrainingProibicoes.value = state.treinamentoContexto.proibicoes;
        atualizarIndicadorPerfilTreinamento(data);
        state.treinamentoCarregado = true;
        renderizarNotasSkuTreinamento();
        renderizarTipoTreinamento(false);
        renderizarListaSkusTreinamento();
        fecharEditorOrientacoesGerais(false);
    } catch (error) {
        if (lojaEscopo !== lojaEscopoTreinamento()) return;
        aiTrainingStatus.textContent = `Erro ao carregar orientações: ${mensagemErro(error)}`;
    }
}

async function salvarTreinamentoAI() {
    sincronizarOrientacoesTreinamentoAtual();
    const tipoAtual = state.treinamentoTipo === 'pos_venda' ? 'pos_venda' : 'perguntas_anuncio';
    const lojaEscopo = lojaEscopoTreinamento();
    if (!lojaEscopo) {
        aiTrainingStatus.textContent = 'Selecione uma loja antes de salvar orientações.';
        aiTrainingScope?.focus();
        throw new Error('Loja não selecionada.');
    }
    btnAiTrainingSalvar.disabled = true;
    btnAiTrainingSimular.disabled = true;
    if (aiTrainingScope) aiTrainingScope.disabled = true;
    if (btnAiTrainingSalvarGerais) btnAiTrainingSalvarGerais.disabled = true;
    if (btnAiTrainingSalvarSku) btnAiTrainingSalvarSku.disabled = true;
    aiTrainingStatus.textContent = `Salvando orientacoes (${rotuloEscopoTreinamento()})...`;
    try {
        const response = await fetch('/api/mercadolivre/ia-treinamento', {
            method: 'POST',
            headers: {
                ...obterAuthHeaders(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                tipo: tipoAtual,
                loja: nomeLojaEscopoTreinamento(),
                store_id: lojaEscopo,
                orientacoes: aiTrainingOrientacoes.value || '',
                contexto_loja: state.treinamentoContexto.contexto_loja || '',
                compatibilidade_autopecas: state.treinamentoContexto.compatibilidade_autopecas || '',
                proibicoes: state.treinamentoContexto.proibicoes || '',
                sku: aiTrainingSku.value || '',
                notas_sku: aiTrainingNotasSku.value || '',
                exemplos: normalizarExemplosTreinamento((state.treinamentoDados[tipoAtual] || {}).exemplos || [])
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Erro ao salvar orientações.');

        state.treinamentoCarregado = true;
        state.treinamentoDados[tipoAtual] = {
            orientacoes: aiTrainingOrientacoes.value || '',
            updated_at: data.updated_at || new Date().toISOString(),
            exemplos: normalizarExemplosTreinamento(((data.exemplos || {})[tipoAtual]) || (state.treinamentoDados[tipoAtual] || {}).exemplos || [])
        };
        state.treinamentoContexto = {
            contexto_loja: typeof data.contexto_loja === 'string' ? data.contexto_loja : (state.treinamentoContexto.contexto_loja || ''),
            compatibilidade_autopecas: typeof data.compatibilidade_autopecas === 'string' ? data.compatibilidade_autopecas : (state.treinamentoContexto.compatibilidade_autopecas || ''),
            proibicoes: typeof data.proibicoes === 'string' ? data.proibicoes : (state.treinamentoContexto.proibicoes || ''),
            notas_sku: normalizarNotasTreinamento(data.notas_sku || state.treinamentoContexto.notas_sku)
        };
        atualizarIndicadorPerfilTreinamento(data);
        renderizarNotasSkuTreinamento();
        renderizarExemplosTreinamento();
        renderizarOrientacoesGeraisTreinamento();
        renderizarListaSkusTreinamento();
        atualizarStatusTreinamentoTipo();
        if (data.requires_review) {
            const skuRascunho = String(data.sku_note_id || '').trim();
            const geralRascunho = String(data.note_id || '').trim();
            const partes = [geralRascunho ? 'orientações gerais' : '', skuRascunho ? 'orientação do SKU' : ''].filter(Boolean);
            aiTrainingStatus.textContent = `Rascunho de ${partes.join(' e ') || 'orientações'} salvo no Obsidian. Revise e publique para ativar na IA.`;
        } else {
            aiTrainingStatus.textContent = `Nenhuma mudança nova em ${rotuloEscopoTreinamento()}.`;
        }
        return data;
    } catch (error) {
        aiTrainingStatus.textContent = `Erro ao salvar orientações: ${mensagemErro(error)}`;
        throw error;
    } finally {
        btnAiTrainingSalvar.disabled = false;
        btnAiTrainingSimular.disabled = false;
        if (aiTrainingScope) aiTrainingScope.disabled = false;
        if (btnAiTrainingSalvarGerais) btnAiTrainingSalvarGerais.disabled = false;
        if (btnAiTrainingSalvarSku) btnAiTrainingSalvarSku.disabled = false;
    }
}

async function salvarOrientacoesGeraisTreinamento() {
    try {
        await salvarTreinamentoAI();
        fecharEditorOrientacoesGerais(false);
    } catch (_error) {
        // O status detalhado já foi exibido por salvarTreinamentoAI.
    }
}

async function salvarOrientacaoSkuTreinamento() {
    const sku = String(aiTrainingSku.value || '').trim();
    if (!sku) {
        aiTrainingStatus.textContent = 'Abra um SKU antes de salvar sua orientação.';
        return;
    }
    try {
        await salvarTreinamentoAI();
        exibirLeituraOrientacaoSku();
        renderizarListaSkusTreinamento();
    } catch (_error) {
        // O status detalhado já foi exibido por salvarTreinamentoAI.
    }
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
        await salvarTreinamentoAI();
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
        aiTrainingStatus.textContent = 'Simulação gerada com as orientações salvas.';
    } catch (error) {
        respostaChat.setError(`Erro na simulação: ${mensagemErro(error)}`);
        aiTrainingStatus.textContent = `Erro na simulação: ${mensagemErro(error)}`;
    } finally {
        btnAiTrainingSalvar.disabled = false;
        btnAiTrainingSimular.disabled = false;
    }
}
