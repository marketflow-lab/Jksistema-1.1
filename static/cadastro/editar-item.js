(function (global) {
    'use strict';

    const formTools = global.JKCadastroForm;
    const storeTools = global.JKCadastroStore;
    const mlTools = global.JKCadastroMercadoLivre;
    const layoutTools = global.JKCadastroEditarLayout;
    if (!formTools || !storeTools || !mlTools || !layoutTools) throw new Error('Núcleo de formulários do Cadastro não inicializado.');

    const elements = {
        status: document.getElementById('status'),
        fotoFile: document.getElementById('fotoFile'),
        btnEnviarFoto: document.getElementById('btnEnviarFoto'),
        btnBuscarMercadoLivre: document.getElementById('btnBuscarMercadoLivre'),
        fotoPaste: document.getElementById('fotoPaste'),
        fotoPreview: document.getElementById('fotoPreview'),
        form: document.getElementById('formEditarItem'),
        formGrid: document.getElementById('formGrid'),
        lojaSelect: document.getElementById('cadastroLojaSelect'),
        lojaAviso: document.getElementById('cadastroLojaAviso'),
        voltarCadastroLink: document.getElementById('voltarCadastroLink'),
    };
    const state = {
        skuOriginal: '', colunas: [], produto: null, fotoDataUrl: '', fotoFilename: '',
        clientId: '', lojas: [], storeIdSelecionado: '', carregadoDaRede: false, fotoCarregada: null,
    };
    let fotoPreviewSeq = 0;
    let consultaMercadoLivreSeq = 0;
    const camposControleLoja = new Set([
        'store_id', 'loja_sync', 'sku_normalizado', 'row_version', 'updated_at_utc', 'deleted_at_utc', 'scope_source',
    ]);
    const prioridadeCampos = [
        'sku', 'nome', 'titulo_ml', 'categoria', 'categoria_id_mlb', 'marca', 'modelo', 'gtins_mlb', formTools.campoM3Individual, 'ncm', 'ncm_validade',
        'ncm_descricao_oficial', 'ncm_fonte_auditoria', 'monofasico', 'monofasico_status',
        'monofasico_confianca', 'monofasico_motivo', 'monofasico_fundamento', 'monofasico_fonte',
        'monofasico_verificado_em', 'cest', 'custo', 'imposto', 'preco', 'descricao', 'updated_at'
    ];

    function setStatus(message, className) {
        formTools.setStatus(elements.status, message, className);
    }

    function authHeaders(extra) {
        if (typeof global.obterAuthHeaders !== 'function') throw new Error('Autenticação indisponível.');
        return global.obterAuthHeaders(extra);
    }

    function obterClientId() {
        return state.clientId || storeTools.obterClientId();
    }

    function renderFormulario(produto) {
        elements.formGrid.innerHTML = '';
        if (!produto) return;
        const campos = state.colunas.length ? [...state.colunas] : [];
        if (!campos.includes(formTools.campoM3Individual)) campos.push(formTools.campoM3Individual);
        Object.keys(produto).forEach(campo => { if (!campos.includes(campo) && !camposControleLoja.has(campo)) campos.push(campo); });
        const grupos = {
            identificacao: layoutTools.criarGrupoCampos('Dados do Produto'),
            fabricante: layoutTools.criarGrupoCampos('Informações do fabricante'),
            fiscal: layoutTools.criarGrupoCampos('Dados fiscais'),
            precos: layoutTools.criarGrupoCampos('Custos e preços'),
            anuncios: layoutTools.criarGrupoCampos('Anúncios e marketplaces'),
            datas: layoutTools.criarGrupoCampos('Datas e controle'),
            outros: layoutTools.criarGrupoCampos('Outras características'),
        };
        let temEditorMlb = false;
        const ordenados = formTools.ordemCampos(campos.filter(campo => !camposControleLoja.has(campo)), prioridadeCampos);
        ordenados.forEach(campo => {
            if (campo === 'custos_frete_mlb' && ordenados.includes('mlb_ids')) return;
            const grupo = grupos[layoutTools.obterGrupoCampo(formTools, campo)] || grupos.outros;
            if (campo === 'mlb_ids') {
                grupo.grid.appendChild(formTools.criarEditorMlb(produto));
                temEditorMlb = true;
            } else grupo.grid.appendChild(layoutTools.criarCampo(formTools, campo, produto[campo] || ''));
        });
        ['identificacao', 'fabricante', 'fiscal', 'precos', 'anuncios', 'datas', 'outros'].forEach(nome => {
            const grupo = grupos[nome];
            if (grupo.grid.children.length > 0 || nome === 'anuncios' && temEditorMlb) elements.formGrid.appendChild(grupo.section);
        });
        atualizarPreviewFoto();
    }
    async function buscarDadosMercadoLivre() {
        const requestSeq = ++consultaMercadoLivreSeq;
        setStatus('Consultando o Mercado Livre da loja selecionada...', 'loading');
        habilitarEdicao(false);
        try {
            const payload = await mlTools.consultar({ storeTools, storeId: state.storeIdSelecionado,
                sku: state.skuOriginal, mlbPrincipal: state.produto && state.produto.mlb_principal, authHeaders });
            if (requestSeq !== consultaMercadoLivreSeq) return;
            habilitarEdicao(state.carregadoDaRede);
            const applied = mlTools.aplicarCampos(elements.formGrid, payload.campos);
            const photo = mlTools.foto(payload);
            const inputFoto = obterInputFoto();
            if (photo.dataUrl) {
                state.fotoDataUrl = photo.dataUrl;
                state.fotoFilename = photo.filename;
                if (inputFoto && photo.url) inputFoto.value = photo.url;
            }
            await atualizarPreviewFoto();
            setStatus(mlTools.resumo(payload, applied), 'success');
        } catch (error) {
            if (requestSeq !== consultaMercadoLivreSeq) return;
            setStatus(`Erro ao trazer dados do Mercado Livre: ${error.message}`, 'error');
        } finally {
            if (requestSeq === consultaMercadoLivreSeq) habilitarEdicao(state.carregadoDaRede);
        }
    }

    function obterInputFoto() {
        return elements.formGrid.querySelector('input[name="foto"], textarea[name="foto"]');
    }

    function obterUrlFoto(valor) {
        return storeTools.urlFoto(obterClientId(), state.storeIdSelecionado, valor);
    }

    function revogarPreviewFotoAtual() {
        if (!state.fotoCarregada) return;
        storeTools.revogarFotoCarregada(state.fotoCarregada);
        state.fotoCarregada = null;
    }

    function exibirFotoCarregada(foto) {
        const link = document.createElement('a');
        link.href = foto.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        const image = document.createElement('img');
        image.src = foto.url;
        image.alt = 'Prévia da imagem';
        link.appendChild(image);
        elements.fotoPreview.innerHTML = '';
        elements.fotoPreview.appendChild(link);
    }

    async function atualizarPreviewFoto() {
        const requestSeq = ++fotoPreviewSeq;
        revogarPreviewFotoAtual();
        if (state.fotoDataUrl) {
            mlTools.renderizarFotoPendente(elements.fotoPreview, state.fotoDataUrl);
            return;
        }
        const inputFoto = obterInputFoto();
        if (!inputFoto) {
            elements.fotoPreview.innerHTML = '<span class="foto-muted">Campo foto não disponível para este cadastro.</span>';
            return;
        }
        const url = obterUrlFoto(inputFoto.value || '');
        if (!url) {
            elements.fotoPreview.innerHTML = '<span class="foto-muted">Sem imagem vinculada.</span>';
            return;
        }
        elements.fotoPreview.innerHTML = '<span class="foto-muted">Carregando imagem...</span>';
        try {
            const foto = await storeTools.carregarFotoAutenticada(url, authHeaders);
            if (requestSeq !== fotoPreviewSeq) {
                storeTools.revogarFotoCarregada(foto);
                return;
            }
            state.fotoCarregada = foto;
            exibirFotoCarregada(foto);
        } catch (_error) {
            if (requestSeq !== fotoPreviewSeq) return;
            elements.fotoPreview.innerHTML = '<span class="foto-muted">Não foi possível carregar a imagem.</span>';
        }
    }

    async function prepararImagemPendente(fileOrBlob, nomeArquivo) {
        if (!state.skuOriginal) {
            setStatus('SKU inválido para anexar imagem.', 'error');
            return;
        }
        const file = fileOrBlob instanceof File
            ? fileOrBlob
            : new File([fileOrBlob], nomeArquivo || `${state.skuOriginal}.png`, { type: fileOrBlob.type || 'image/png' });
        try {
            state.fotoDataUrl = await formTools.lerArquivoComoDataUrl(file);
            state.fotoFilename = file.name || `${state.skuOriginal}.png`;
            atualizarPreviewFoto();
            setStatus('Imagem anexada. Ela será salva somente ao clicar em Salvar alterações.', 'success');
        } catch (error) {
            setStatus(`Erro ao preparar imagem: ${error.message}`, 'error');
        }
    }

    async function carregarColunasCadastro() {
        try {
            const response = await global.fetch(storeTools.apiLoja(state.storeIdSelecionado, 'colunas'), { headers: authHeaders() });
            if (!response.ok) {
                state.colunas = formTools.garantirCamposProduto([]);
                return;
            }
            const payload = await response.json();
            state.colunas = Array.isArray(payload.colunas) ? payload.colunas : [];
            state.colunas = formTools.garantirCamposProduto(state.colunas.filter(campo => !camposControleLoja.has(campo)));
        } catch (_error) {
            state.colunas = formTools.garantirCamposProduto([]);
        }
    }

    async function carregarProduto() {
        if (!state.skuOriginal) throw new Error('SKU não informado.');
        const url = storeTools.apiLoja(state.storeIdSelecionado, `produtos/${encodeURIComponent(state.skuOriginal)}`);
        const response = await global.fetch(url, { headers: authHeaders() });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload && payload.detail || `HTTP ${response.status}`);
        const remoto = payload && payload.produto ? payload.produto : payload;
        if (!remoto || typeof remoto !== 'object' || !String(remoto.sku || '').trim()) {
            throw new Error('SKU não encontrado. Volte à lista e selecione novamente.');
        }
        state.produto = { ...remoto, store_id: state.storeIdSelecionado };
        state.carregadoDaRede = true;
        storeTools.salvarCacheEdicao(state.clientId, state.storeIdSelecionado, state.produto);
    }

    function habilitarEdicao(habilitada) {
        elements.form.querySelectorAll('input, textarea, button').forEach(element => { element.disabled = !habilitada; });
        elements.fotoFile.disabled = !habilitada;
        elements.btnEnviarFoto.disabled = !habilitada;
        elements.fotoPaste.tabIndex = habilitada ? 0 : -1;
    }

    function atualizarContextoLoja() {
        const loja = state.lojas.find(item => item.store_id === state.storeIdSelecionado);
        elements.lojaAviso.textContent = loja ? `Edição limitada a ${loja.nome}.` : 'Selecione uma loja específica.';
        elements.voltarCadastroLink.href = storeTools.urlPagina('/cadastro_editar.html', state.storeIdSelecionado);
    }

    async function carregarContextoLoja() {
        state.clientId = storeTools.obterClientId();
        if (!state.clientId) throw new Error('Cliente não identificado.');
        state.lojas = await storeTools.carregarLojas(authHeaders);
        storeTools.preencherSeletor(elements.lojaSelect, state.lojas, { permitirTodas: false });
        state.storeIdSelecionado = storeTools.resolverStoreId(state.lojas, { clientId: state.clientId });
        elements.lojaSelect.value = state.storeIdSelecionado;
        atualizarContextoLoja();
        if (!state.storeIdSelecionado) throw new Error('Selecione uma loja específica para editar o SKU.');
    }

    async function iniciarTela() {
        setStatus('Carregando dados do SKU...', 'loading');
        habilitarEdicao(false);
        try {
            await carregarContextoLoja();
            const params = new URLSearchParams(global.location.search);
            state.skuOriginal = String(params.get('sku') || storeTools.obterSkuCache(state.clientId, state.storeIdSelecionado) || '').trim();
            if (!state.skuOriginal) throw new Error('SKU não informado na URL.');
            await Promise.all([carregarColunasCadastro(), carregarProduto()]);
            renderFormulario(state.produto);
            habilitarEdicao(true);
            setStatus(`SKU carregado: ${state.skuOriginal}`, 'success');
        } catch (error) {
            state.carregadoDaRede = false;
            habilitarEdicao(false);
            atualizarContextoLoja();
            setStatus(`Erro ao carregar SKU: ${error.message}`, 'error');
        }
    }

    async function salvarAlteracoes(event) {
        event.preventDefault();
        if (!state.skuOriginal || !state.storeIdSelecionado || !state.carregadoDaRede) {
            setStatus('SKU sem confirmação atual da loja. Recarregue antes de editar.', 'error');
            return;
        }
        const data = {};
        elements.formGrid.querySelectorAll('input[name], textarea[name]').forEach(input => {
            data[input.name] = input.dataset.tipo === 'moeda' ? formTools.brlParaNumeric(input.value) : input.value || '';
        });
        Object.assign(data, formTools.extrairMlbFreteEditor(elements.formGrid));
        if (state.produto && state.produto.row_version !== undefined) data.row_version = state.produto.row_version;
        if (state.fotoDataUrl) {
            data.__foto_data_url = state.fotoDataUrl;
            data.__foto_filename = state.fotoFilename || `${state.skuOriginal}.png`;
        }
        setStatus('Salvando alterações...', 'loading');
        try {
            const response = await global.fetch(storeTools.apiLoja(state.storeIdSelecionado, `produtos/${encodeURIComponent(state.skuOriginal)}`), {
                method: 'PUT', headers: authHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify(data)
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload && payload.detail || `HTTP ${response.status}`);
            const produtoAtualizado = payload && payload.produto && typeof payload.produto === 'object'
                ? payload.produto
                : null;
            state.skuOriginal = String(
                (produtoAtualizado && produtoAtualizado.sku) || payload.sku || data.sku || state.skuOriginal
            ).trim();
            const fotoAtualizada = String(
                (produtoAtualizado && produtoAtualizado.foto)
                || (payload && typeof payload.foto === 'string' ? payload.foto : '')
                || ''
            ).trim();
            if (fotoAtualizada) {
                const inputFoto = obterInputFoto();
                if (inputFoto) inputFoto.value = fotoAtualizada;
            }
            if (produtoAtualizado) {
                state.produto = { ...produtoAtualizado, store_id: state.storeIdSelecionado };
            }
            state.fotoDataUrl = '';
            state.fotoFilename = '';
            atualizarPreviewFoto();
            setStatus(`Alterações salvas com sucesso: ${state.skuOriginal}`, 'success');
            global.location.href = storeTools.urlPagina('/cadastro_editar.html', state.storeIdSelecionado);
        } catch (error) {
            setStatus(`Erro ao salvar SKU: ${error.message}`, 'error');
        }
    }

    elements.form.addEventListener('submit', salvarAlteracoes);
    elements.btnBuscarMercadoLivre.addEventListener('click', buscarDadosMercadoLivre);
    elements.btnEnviarFoto.addEventListener('click', async () => {
        const file = elements.fotoFile.files && elements.fotoFile.files[0];
        if (file) await prepararImagemPendente(file, file.name);
        else setStatus('Selecione uma imagem para enviar.', 'error');
    });
    elements.fotoPaste.addEventListener('paste', async event => {
        const items = event.clipboardData && event.clipboardData.items || [];
        for (const item of items) {
            if (!item.type || !item.type.startsWith('image/')) continue;
            event.preventDefault();
            const blob = item.getAsFile();
            if (blob) await prepararImagemPendente(blob, `${state.skuOriginal || 'sku'}.png`);
            return;
        }
    });
    elements.fotoPaste.addEventListener('click', () => elements.fotoPaste.focus());
    if (typeof global.addEventListener === 'function') {
        global.addEventListener('beforeunload', () => {
            fotoPreviewSeq += 1;
            revogarPreviewFotoAtual();
        });
    }
    elements.lojaSelect.addEventListener('change', () => {
        consultaMercadoLivreSeq += 1;
        const storeId = String(elements.lojaSelect.value || '').trim();
        if (!storeTools.lojaExiste(state.lojas, storeId)) {
            elements.lojaSelect.value = state.storeIdSelecionado;
            setStatus('Selecione uma loja específica para editar.', 'error');
            habilitarEdicao(state.carregadoDaRede);
            return;
        }
        storeTools.salvarPreferencia(state.clientId, storeId, state.lojas);
        global.location.href = storeTools.urlPagina('/cadastro_editar_item.html', storeId, { sku: state.skuOriginal });
    });
    if (global.verificarSessao()) iniciarTela();
})(window);
