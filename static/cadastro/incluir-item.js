(function (global) {
    'use strict';

    const formTools = global.JKCadastroForm;
    if (!formTools) throw new Error('Núcleo de formulários do Cadastro não inicializado.');

    const elements = {
        status: document.getElementById('status'),
        form: document.getElementById('formIncluir'),
        formGrid: document.getElementById('formGrid'),
        btnLimpar: document.getElementById('btnLimpar'),
        fotoInput: document.getElementById('fotoInput'),
        btnUploadFoto: document.getElementById('btnUploadFoto'),
        btnPasteFoto: document.getElementById('btnPasteFoto'),
        fotoPreview: document.getElementById('fotoPreview'),
    };
    const state = { colunas: [], fotoDataUrl: '', fotoFilename: '' };
    const prioridadeCampos = [
        'sku', 'nome', 'categoria', 'marca', formTools.campoM3Individual,
        'ncm', 'cest', 'custo', 'imposto', 'preco', 'descricao', 'updated_at'
    ];

    function setStatus(message, className) {
        formTools.setStatus(elements.status, message, className);
    }

    function authHeaders(extra) {
        if (typeof global.obterAuthHeaders !== 'function') throw new Error('Autenticação indisponível.');
        return global.obterAuthHeaders(extra);
    }

    function atualizarPreviewFoto() {
        elements.fotoPreview.innerHTML = state.fotoDataUrl
            ? `<img src="${state.fotoDataUrl}" alt="Prévia da imagem pendente"><div class="foto-pending-note">Imagem<br>pendente</div>`
            : '<span class="foto-muted">Sem imagem vinculada.</span>';
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
    }

    async function carregarColunas() {
        setStatus('Carregando colunas do cadastro...', 'loading');
        try {
            const response = await global.fetch('/api/cadastro/colunas', { headers: authHeaders() });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload && payload.detail || `HTTP ${response.status}`);
            state.colunas = Array.isArray(payload.colunas) ? payload.colunas : [];
            if (!state.colunas.includes('sku')) state.colunas.unshift('sku');
            if (!state.colunas.includes(formTools.campoM3Individual)) state.colunas.push(formTools.campoM3Individual);
            montarFormulario();
            setStatus('Preencha os dados para incluir um novo SKU.', 'success');
        } catch (error) {
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
        if (!String(data.sku || '').trim()) {
            setStatus('Informe o SKU para cadastrar.', 'error');
            return;
        }
        setStatus('Incluindo SKU...', 'loading');
        try {
            const response = await global.fetch('/api/cadastro/produto', {
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
        elements.form.reset();
        state.fotoDataUrl = '';
        state.fotoFilename = '';
        atualizarPreviewFoto();
    });
    elements.form.addEventListener('submit', incluirSku);
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
    if (global.verificarSessao()) carregarColunas();
})(window);
