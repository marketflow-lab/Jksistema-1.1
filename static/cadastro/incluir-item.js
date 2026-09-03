(function (global) {
    'use strict';

    const formTools = global.JKCadastroForm;
    const storeTools = global.JKCadastroStore;
    const mlTools = global.JKCadastroMercadoLivre;
    if (!formTools || !storeTools || !mlTools) throw new Error('Núcleo de formulários do Cadastro não inicializado.');

    const elements = {
        status: document.getElementById('status'),
        form: document.getElementById('formIncluir'),
        formGrid: document.getElementById('formGrid'),
        btnLimpar: document.getElementById('btnLimpar'),
        fotoInput: document.getElementById('fotoInput'),
        btnUploadFoto: document.getElementById('btnUploadFoto'),
        btnPasteFoto: document.getElementById('btnPasteFoto'),
        btnBuscarMercadoLivre: document.getElementById('btnBuscarMercadoLivre'),
        fotoPreview: document.getElementById('fotoPreview'),
        lojaSelect: document.getElementById('cadastroLojaSelect'),
        lojaAviso: document.getElementById('cadastroLojaAviso'),
        voltarCadastroLink: document.getElementById('voltarCadastroLink'),
    };
    const state = {
        colunas: [], fotoDataUrl: '', fotoFilename: '', clientId: '', lojas: [],
        storeIdSelecionado: '', lojaValidada: false, fotoCarregada: null,
    };
    let fotoPreviewSeq = 0;
    const camposControleLoja = new Set([
        'store_id', 'loja_sync', 'sku_normalizado', 'row_version', 'updated_at_utc', 'deleted_at_utc', 'scope_source',
    ]);
    let carregamentoColunasSeq = 0;
    let consultaMercadoLivreSeq = 0;
    const prioridadeCampos = [
        'sku', 'nome', 'titulo_ml', 'categoria', 'categoria_id_mlb', 'marca', 'modelo', 'gtins_mlb',
        formTools.campoM3Individual, 'ncm', 'cest', 'custo', 'imposto', 'preco', 'descricao', 'updated_at'
    ];

    function setStatus(message, className) {
        formTools.setStatus(elements.status, message, className);
    }

    function authHeaders(extra) {
        if (typeof global.obterAuthHeaders !== 'function') throw new Error('Autenticação indisponível.');
        return global.obterAuthHeaders(extra);
    }

    function habilitarFormulario(habilitado) {
        elements.form.querySelectorAll('input, textarea, button').forEach(element => { element.disabled = !habilitado; });
        elements.lojaSelect.disabled = false;
    }

    function atualizarContextoLoja() {
        const loja = state.lojas.find(item => item.store_id === state.storeIdSelecionado);
        elements.lojaAviso.textContent = loja ? `Inclusão limitada a ${loja.nome}.` : 'Selecione uma loja específica para incluir.';
        elements.voltarCadastroLink.href = storeTools.urlPagina('/cadastro.html', state.storeIdSelecionado);
    }

    function revogarPreviewFotoAtual() {
        if (!state.fotoCarregada) return;
        storeTools.revogarFotoCarregada(state.fotoCarregada);
        state.fotoCarregada = null;
    }

    async function atualizarPreviewFoto() {
        const requestSeq = ++fotoPreviewSeq;
        revogarPreviewFotoAtual();
        elements.fotoPreview.textContent = '';
        const inputFoto = elements.formGrid.querySelector('[name="foto"]');
        const url = state.fotoDataUrl || storeTools.urlFoto(
            state.clientId,
            state.storeIdSelecionado,
            inputFoto && inputFoto.value,
        );
        if (!url) {
            const empty = document.createElement('span');
            empty.className = 'foto-muted';
            empty.textContent = 'Sem imagem vinculada.';
            elements.fotoPreview.appendChild(empty);
            return;
        }
        const loading = document.createElement('span');
        loading.className = 'foto-muted';
        loading.textContent = 'Carregando imagem...';
        elements.fotoPreview.appendChild(loading);
        try {
            const foto = await storeTools.carregarFotoAutenticada(url, authHeaders);
            if (requestSeq !== fotoPreviewSeq) {
                storeTools.revogarFotoCarregada(foto);
                return;
            }
            state.fotoCarregada = foto;
            const image = document.createElement('img');
            image.src = foto.url;
            image.alt = 'Prévia da imagem pendente';
            elements.fotoPreview.textContent = '';
            elements.fotoPreview.appendChild(image);
            const note = document.createElement('div');
            note.className = 'foto-pending-note';
            note.textContent = 'Imagem pendente';
            elements.fotoPreview.appendChild(note);
        } catch (_error) {
            if (requestSeq !== fotoPreviewSeq) return;
            elements.fotoPreview.textContent = '';
            const erro = document.createElement('span');
            erro.className = 'foto-muted';
            erro.textContent = 'Não foi possível carregar a imagem.';
            elements.fotoPreview.appendChild(erro);
        }
    }

    async function prepararImagemPendente(fileOrBlob, nomeArquivo) {
        if (!fileOrBlob) return;
        const file = fileOrBlob instanceof File
            ? fileOrBlob
            : new File([fileOrBlob], nomeArquivo || 'imagem.png', { type: fileOrBlob.type || 'image/png' });
        try {
            state.fotoDataUrl = await formTools.lerArquivoComoDataUrl(file);
            state.fotoFilename = file.name || 'imagem.png';
            atualizarPreviewFoto();
            setStatus('Imagem anexada. Será salva somente ao clicar em Cadastrar SKU.', 'success');
        } catch (error) {
            setStatus(`Erro ao preparar imagem: ${error.message}`, 'error');
        }
    }

    function criarCampo(chave) {
        const field = document.createElement('div');
        field.className = 'field' + (String(chave).includes('descricao') || String(chave).includes('link') ? ' full' : '');
        const label = document.createElement('label');
        label.setAttribute('for', `f_${chave}`);
        label.textContent = formTools.formatarNomeCampo(chave);
        const input = String(chave).includes('descricao') ? document.createElement('textarea') : document.createElement('input');
        if (input.tagName === 'INPUT') input.type = 'text';
        input.id = `f_${chave}`;
        input.name = chave;
        input.value = '';
        if (String(chave || '').trim().toLowerCase() === formTools.campoM3Individual) {
            input.inputMode = 'decimal';
            input.placeholder = 'Ex.: 0,000054';
            input.title = 'Informe o M³ de uma unidade. A importação multiplica pela quantidade.';
        }
        if (chave === 'sku') {
            input.required = true;
            label.textContent = 'sku *';
        }
        if (chave === 'updated_at') input.placeholder = 'Será preenchido automaticamente';
        if (chave === 'custo') {
            input.dataset.tipo = 'moeda';
            formTools.aplicarMascaraMoeda(input);
        }
        field.append(label, input);
        return field;
    }

    function montarFormulario() {
        elements.formGrid.innerHTML = '';
        formTools.ordemCampos(state.colunas, prioridadeCampos).forEach(campo => {
            if (campo === 'custos_frete_mlb') return;
            elements.formGrid.appendChild(campo === 'mlb_ids' ? formTools.criarEditorMlb() : criarCampo(campo));
        });
        atualizarPreviewFoto();
    }

    async function buscarDadosMercadoLivre() {
        const skuInput = elements.formGrid.querySelector('[name="sku"]');
        const sku = String(skuInput && skuInput.value || '').trim();
        const requestSeq = ++consultaMercadoLivreSeq;
        setStatus('Consultando o Mercado Livre da loja selecionada...', 'loading');
        habilitarFormulario(false);
        try {
            const payload = await mlTools.consultar({
                storeTools,
                storeId: state.storeIdSelecionado,
                sku,
                authHeaders,
            });
            if (requestSeq !== consultaMercadoLivreSeq) return;
            habilitarFormulario(state.lojaValidada);
            const applied = mlTools.aplicarCampos(elements.formGrid, payload.campos);
            const photo = mlTools.foto(payload);
            const inputFoto = elements.formGrid.querySelector('[name="foto"]');
            if (photo.dataUrl) {
                state.fotoDataUrl = photo.dataUrl;
                state.fotoFilename = photo.filename;
                if (inputFoto && photo.url) inputFoto.value = photo.url;
            }
            atualizarPreviewFoto();
            setStatus(mlTools.resumo(payload, applied), 'success');
        } catch (error) {
            if (requestSeq !== consultaMercadoLivreSeq) return;
            setStatus(`Erro ao trazer dados do Mercado Livre: ${error.message}`, 'error');
        } finally {
            if (requestSeq === consultaMercadoLivreSeq) habilitarFormulario(state.lojaValidada);
        }
    }

    async function carregarColunas() {
        const requestSeq = ++carregamentoColunasSeq;
        if (!state.storeIdSelecionado) {
            state.lojaValidada = false;
            habilitarFormulario(false);
            setStatus('Selecione uma loja específica para incluir um SKU.', 'error');
            return;
        }
        setStatus('Carregando colunas do cadastro...', 'loading');
        state.lojaValidada = false;
        habilitarFormulario(false);
        try {
            const response = await global.fetch(storeTools.apiLoja(state.storeIdSelecionado, 'colunas'), { headers: authHeaders() });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload && payload.detail || `HTTP ${response.status}`);
            if (requestSeq !== carregamentoColunasSeq) return;
            const colunas = Array.isArray(payload.colunas) ? payload.colunas.filter(campo => !camposControleLoja.has(campo)) : [];
            state.colunas = formTools.garantirCamposProduto(colunas);
            if (!state.colunas.includes('sku')) state.colunas.unshift('sku');
            if (!state.colunas.includes(formTools.campoM3Individual)) state.colunas.push(formTools.campoM3Individual);
            montarFormulario();
            state.lojaValidada = true;
            habilitarFormulario(true);
            setStatus('Preencha os dados para incluir um novo SKU.', 'success');
        } catch (error) {
            if (requestSeq !== carregamentoColunasSeq) return;
            habilitarFormulario(false);
            setStatus(`Erro ao carregar colunas: ${error.message}`, 'error');
        }
    }

    function montarPayload() {
        const data = {};
        elements.formGrid.querySelectorAll('input[name], textarea[name]').forEach(input => {
            if (input.name === 'updated_at') return;
            data[input.name] = input.dataset.tipo === 'moeda' ? formTools.brlParaNumeric(input.value) : input.value || '';
        });
        if (state.colunas.includes('mlb_ids')) Object.assign(data, formTools.extrairMlbFreteEditor(elements.formGrid));
        if (state.fotoDataUrl) {
            data.__foto_data_url = state.fotoDataUrl;
            data.__foto_filename = state.fotoFilename || 'imagem.png';
        }
        return data;
    }

    async function incluirSku(event) {
        event.preventDefault();
        const data = montarPayload();
        if (!state.storeIdSelecionado || !state.lojaValidada) {
            setStatus('Selecione uma loja válida antes de cadastrar.', 'error');
            return;
        }
        if (!String(data.sku || '').trim()) {
            setStatus('Informe o SKU para cadastrar.', 'error');
            return;
        }
        setStatus('Incluindo SKU...', 'loading');
        try {
            const response = await global.fetch(storeTools.apiLoja(state.storeIdSelecionado, 'produtos'), {
                method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify(data)
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload && payload.detail || `HTTP ${response.status}`);
            setStatus(`SKU incluído com sucesso: ${payload.sku || data.sku}`, 'success');
            elements.form.reset();
            state.fotoDataUrl = '';
            state.fotoFilename = '';
            atualizarPreviewFoto();
        } catch (error) {
            setStatus(`Erro ao incluir SKU: ${error.message}`, 'error');
        }
    }

    elements.btnLimpar.addEventListener('click', () => {
        consultaMercadoLivreSeq += 1;
        elements.form.reset();
        state.fotoDataUrl = '';
        state.fotoFilename = '';
        atualizarPreviewFoto();
    });
    elements.form.addEventListener('submit', incluirSku);
    elements.btnBuscarMercadoLivre.addEventListener('click', buscarDadosMercadoLivre);
    elements.btnUploadFoto.addEventListener('click', () => elements.fotoInput.click());
    elements.fotoInput.addEventListener('change', event => {
        const file = event.target.files && event.target.files[0];
        if (file) prepararImagemPendente(file, file.name);
    });
    elements.btnPasteFoto.addEventListener('click', async () => {
        try {
            const clipboardItems = await global.navigator.clipboard.read();
            for (const item of clipboardItems) {
                const imageType = item.types.find(type => type.startsWith('image/'));
                if (!imageType) continue;
                await prepararImagemPendente(await item.getType(imageType));
                return;
            }
            setStatus('Nenhuma imagem na área de transferência.', 'error');
        } catch (error) {
            setStatus(`Erro ao colar imagem: ${error.message}`, 'error');
        }
    });
    document.addEventListener('paste', event => {
        const items = event.clipboardData && event.clipboardData.items || [];
        for (const item of items) {
            if (!item.type.startsWith('image/')) continue;
            event.preventDefault();
            prepararImagemPendente(item.getAsFile());
            return;
        }
    });
    async function selecionarLoja(storeId) {
        consultaMercadoLivreSeq += 1;
        state.fotoDataUrl = '';
        state.fotoFilename = '';
        const valor = String(storeId || '').trim();
        if (!storeTools.lojaExiste(state.lojas, valor)) {
            state.storeIdSelecionado = '';
            atualizarContextoLoja();
            await carregarColunas();
            return;
        }
        state.storeIdSelecionado = valor;
        storeTools.salvarPreferencia(state.clientId, valor, state.lojas);
        storeTools.atualizarUrl(valor);
        atualizarContextoLoja();
        await carregarColunas();
    }

    async function iniciar() {
        state.clientId = storeTools.obterClientId();
        if (!state.clientId) {
            global.location.href = '/frontend_index.html';
            return;
        }
        habilitarFormulario(false);
        setStatus('Carregando lojas...', 'loading');
        try {
            state.lojas = await storeTools.carregarLojas(authHeaders);
            state.storeIdSelecionado = storeTools.resolverStoreId(state.lojas, { clientId: state.clientId });
            storeTools.preencherSeletor(elements.lojaSelect, state.lojas, { permitirTodas: false, storeId: state.storeIdSelecionado });
            atualizarContextoLoja();
            await carregarColunas();
        } catch (error) {
            storeTools.preencherSeletor(elements.lojaSelect, state.lojas, { permitirTodas: false });
            state.storeIdSelecionado = '';
            atualizarContextoLoja();
            setStatus(`Erro ao carregar lojas: ${error.message}`, 'error');
        }
    }

    elements.lojaSelect.addEventListener('change', () => selecionarLoja(elements.lojaSelect.value));
    if (typeof global.addEventListener === 'function') {
        global.addEventListener('beforeunload', () => {
            fotoPreviewSeq += 1;
            revogarPreviewFotoAtual();
        });
    }
    if (global.verificarSessao()) iniciar();
})(window);
