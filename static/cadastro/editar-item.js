(function (global) {
    'use strict';

    const formTools = global.JKCadastroForm;
    if (!formTools) throw new Error('Núcleo de formulários do Cadastro não inicializado.');

    const elements = {
        status: document.getElementById('status'),
        fotoFile: document.getElementById('fotoFile'),
        btnEnviarFoto: document.getElementById('btnEnviarFoto'),
        fotoPaste: document.getElementById('fotoPaste'),
        fotoPreview: document.getElementById('fotoPreview'),
        form: document.getElementById('formEditarItem'),
        formGrid: document.getElementById('formGrid'),
    };
    const state = { skuOriginal: '', colunas: [], produto: null, fotoDataUrl: '', fotoFilename: '' };
    const prioridadeCampos = [
        'sku', 'nome', 'categoria', 'marca', formTools.campoM3Individual, 'ncm', 'ncm_validade',
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
        try {
            const user = JSON.parse(global.localStorage.getItem('user_data') || 'null');
            return String(user && user.client_id || 'default').trim() || 'default';
        } catch (_error) {
            return 'default';
        }
    }

    function criarCampo(chave, valor) {
        const field = document.createElement('div');
        field.className = 'field' + (String(chave).includes('descricao') || String(chave).includes('link') ? ' full' : '');
        const label = document.createElement('label');
        label.setAttribute('for', `f_${chave}`);
        label.textContent = formTools.formatarNomeCampo(chave);
        const texto = String(valor || '');
        const input = texto.length > 120 || String(chave).includes('descricao')
            ? document.createElement('textarea')
            : document.createElement('input');
        if (input.tagName === 'INPUT') input.type = 'text';
        input.id = `f_${chave}`;
        input.name = chave;
        const normalizada = String(chave || '').toLowerCase();
        if (normalizada.startsWith('monofasico') || ['ncm_validade', 'ncm_descricao_oficial', 'ncm_fonte_auditoria', 'ncm_verificado_em'].includes(normalizada)) {
            input.readOnly = true;
            input.title = 'Campo gerado pela auditoria fiscal do cadastro.';
        }
        if (normalizada === formTools.campoM3Individual) {
            input.inputMode = 'decimal';
            input.placeholder = 'Ex.: 0,000054';
            input.title = 'Informe o M³ de uma unidade. A importação multiplica pela quantidade.';
        }
        if (chave === 'custo') {
            input.value = formTools.numericParaBRL(texto);
            input.dataset.tipo = 'moeda';
            formTools.aplicarMascaraMoeda(input);
        } else input.value = texto;
        field.append(label, input);
        return field;
    }

    function obterGrupoCampo(chave) {
        const c = String(chave || '').trim().toLowerCase();
        if (!c) return 'outros';
        if (['sku', 'nome', 'produto', 'produto_bling', 'categoria', 'marca'].includes(c)) return 'identificacao';
        if (['fabricante', 'fornecedor', 'modelo', 'linha'].includes(c) || c.includes('fabricante') || c.includes('fornecedor')) return 'fabricante';
        if (['ncm', 'cest', 'imposto', formTools.campoM3Individual, 'm3'].includes(c) || c.startsWith('monofasico') || c.startsWith('ncm_')) return 'fiscal';
        if (['custo', 'preco'].includes(c)) return 'precos';
        if (['mlb_ids', 'custos_frete_mlb', 'mlb_principal', 'qtd_anuncios_mlb', 'titulos_anuncios_mlb', 'modalidades_mlb', 'gtins_mlb'].includes(c)) return 'anuncios';
        if (['descricao', 'foto', 'link', 'url'].includes(c) || c.includes('descricao') || c.includes('link') || c.includes('url')) return 'identificacao';
        if (['updated_at', 'created_at', 'data_atualizacao', 'data_criacao'].includes(c) || c.endsWith('_at') || c.startsWith('data_')) return 'datas';
        return 'outros';
    }

    function criarGrupoCampos(titulo) {
        const section = document.createElement('section');
        section.className = 'grupo-campos';
        const heading = document.createElement('h3');
        heading.className = 'grupo-titulo';
        heading.textContent = titulo;
        const grid = document.createElement('div');
        grid.className = 'grupo-grid';
        section.append(heading, grid);
        return { section, grid };
    }

    function renderFormulario(produto) {
        elements.formGrid.innerHTML = '';
        if (!produto) return;
        const campos = state.colunas.length ? [...state.colunas] : [];
        if (!campos.includes(formTools.campoM3Individual)) campos.push(formTools.campoM3Individual);
        Object.keys(produto).forEach(campo => { if (!campos.includes(campo)) campos.push(campo); });
        const grupos = {
            identificacao: criarGrupoCampos('Dados do Produto'),
            fabricante: criarGrupoCampos('Informações do fabricante'),
            fiscal: criarGrupoCampos('Dados fiscais'),
            precos: criarGrupoCampos('Custos e preços'),
            anuncios: criarGrupoCampos('Anúncios e marketplaces'),
            datas: criarGrupoCampos('Datas e controle'),
            outros: criarGrupoCampos('Outras características'),
        };
        let temEditorMlb = false;
        const ordenados = formTools.ordemCampos(campos, prioridadeCampos);
        ordenados.forEach(campo => {
            if (campo === 'custos_frete_mlb' && ordenados.includes('mlb_ids')) return;
            const grupo = grupos[obterGrupoCampo(campo)] || grupos.outros;
            if (campo === 'mlb_ids') {
                grupo.grid.appendChild(formTools.criarEditorMlb(produto));
                temEditorMlb = true;
            } else grupo.grid.appendChild(criarCampo(campo, produto[campo] || ''));
        });
        ['identificacao', 'fabricante', 'fiscal', 'precos', 'anuncios', 'datas', 'outros'].forEach(nome => {
            const grupo = grupos[nome];
            if (grupo.grid.children.length > 0 || nome === 'anuncios' && temEditorMlb) elements.formGrid.appendChild(grupo.section);
        });
        atualizarPreviewFoto();
    }

    function obterInputFoto() {
        return elements.formGrid.querySelector('input[name="foto"], textarea[name="foto"]');
    }

    function obterUrlFoto(valor) {
        const texto = String(valor || '').trim();
        if (!texto || /^data:/i.test(texto) || /^https?:\/\//i.test(texto)) return texto;
        const nomeArquivo = texto.split('/').pop();
        return nomeArquivo
            ? `/api/cadastro/foto/${encodeURIComponent(obterClientId())}/${encodeURIComponent(nomeArquivo)}`
            : '';
    }

    function atualizarPreviewFoto() {
        if (state.fotoDataUrl) {
            elements.fotoPreview.innerHTML = `<img src="${state.fotoDataUrl}" alt="Prévia da imagem pendente"><div class="foto-pending-note">Imagem pendente. Será salva ao clicar em Salvar alterações.</div>`;
            return;
        }
        const inputFoto = obterInputFoto();
        if (!inputFoto) {
            elements.fotoPreview.innerHTML = '<span class="foto-muted">Campo foto não disponível para este cadastro.</span>';
            return;
        }
        const url = obterUrlFoto(inputFoto.value || '');
        elements.fotoPreview.innerHTML = url
            ? `<a href="${url}" target="_blank" rel="noopener noreferrer"><img src="${url}" alt="Prévia da imagem"></a>`
            : '<span class="foto-muted">Sem imagem vinculada.</span>';
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
            const response = await global.fetch('/api/cadastro/colunas', { headers: authHeaders() });
            if (!response.ok) {
                state.colunas = [];
                return;
            }
            const payload = await response.json();
            state.colunas = Array.isArray(payload.colunas) ? payload.colunas : [];
        } catch (_error) {
            state.colunas = [];
        }
    }

    function carregarProdutoDoStorage() {
        try {
            const rawItem = global.localStorage.getItem('cadastro_editar_item');
            if (rawItem) {
                const item = JSON.parse(rawItem);
                if (item && String(item.sku || '').trim().toLowerCase() === state.skuOriginal.toLowerCase()) return item;
            }
            const rawLista = global.localStorage.getItem('cadastro_editar_lista');
            if (rawLista) {
                const lista = JSON.parse(rawLista);
                if (Array.isArray(lista)) return lista.find(item => String(item && item.sku || '').trim().toLowerCase() === state.skuOriginal.toLowerCase()) || null;
            }
        } catch (_error) {}
        return null;
    }

    async function carregarProduto() {
        if (!state.skuOriginal) throw new Error('SKU não informado.');
        const local = carregarProdutoDoStorage();
        if (local) state.produto = { ...local };
        try {
            const response = await global.fetch('/api/cadastro/produtos', { headers: authHeaders() });
            if (response.ok) {
                const lista = await response.json();
                const encontrado = (lista || []).find(item => String(item && item.sku || '').trim().toLowerCase() === state.skuOriginal.toLowerCase());
                if (encontrado) state.produto = { ...encontrado };
            }
        } catch (_error) {}
        try {
            const response = await global.fetch(`/api/cadastro/produto?sku=${encodeURIComponent(state.skuOriginal)}`, { headers: authHeaders() });
            if (response.ok) {
                const payload = await response.json();
                if (payload && payload.produto) state.produto = { ...(state.produto || {}), ...payload.produto };
            }
        } catch (_error) {}
        if (!state.produto) throw new Error('SKU não encontrado. Volte à lista e selecione novamente.');
    }

    async function iniciarTela() {
        setStatus('Carregando dados do SKU...', 'loading');
        try {
            const params = new URLSearchParams(global.location.search);
            state.skuOriginal = String(params.get('sku') || global.localStorage.getItem('cadastro_editar_sku') || '').trim();
            if (!state.skuOriginal) throw new Error('SKU não informado na URL.');
            await carregarColunasCadastro();
            await carregarProduto();
            renderFormulario(state.produto);
            setStatus(`SKU carregado: ${state.skuOriginal}`, 'success');
        } catch (error) {
            setStatus(`Erro ao carregar SKU: ${error.message}`, 'error');
        }
    }

    async function salvarAlteracoes(event) {
        event.preventDefault();
        if (!state.skuOriginal) {
            setStatus('SKU original inválido.', 'error');
            return;
        }
        const data = {};
        elements.formGrid.querySelectorAll('input[name], textarea[name]').forEach(input => {
            data[input.name] = input.dataset.tipo === 'moeda' ? formTools.brlParaNumeric(input.value) : input.value || '';
        });
        Object.assign(data, formTools.extrairMlbFreteEditor(elements.formGrid));
        if (state.fotoDataUrl) {
            data.__foto_data_url = state.fotoDataUrl;
            data.__foto_filename = state.fotoFilename || `${state.skuOriginal}.png`;
        }
        setStatus('Salvando alterações...', 'loading');
        try {
            const response = await global.fetch(`/api/cadastro/produto?sku_original=${encodeURIComponent(state.skuOriginal)}`, {
                method: 'PUT', headers: authHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify(data)
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload && payload.detail || `HTTP ${response.status}`);
            state.skuOriginal = String(payload.sku || data.sku || state.skuOriginal).trim();
            if (payload && payload.foto) {
                const inputFoto = obterInputFoto();
                if (inputFoto) inputFoto.value = payload.foto;
            }
            state.fotoDataUrl = '';
            state.fotoFilename = '';
            atualizarPreviewFoto();
            setStatus(`Alterações salvas com sucesso: ${state.skuOriginal}`, 'success');
            global.location.href = '/cadastro_editar.html';
        } catch (error) {
            setStatus(`Erro ao salvar SKU: ${error.message}`, 'error');
        }
    }

    elements.form.addEventListener('submit', salvarAlteracoes);
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
    if (global.verificarSessao()) iniciarTela();
})(window);
