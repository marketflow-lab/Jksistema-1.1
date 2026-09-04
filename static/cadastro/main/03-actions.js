(function (global) {
    'use strict';

    const cadastro = global.JKCadastro;
    if (!cadastro || !cadastro.components.has('custos-mlb')) throw new Error('Painéis do Cadastro não inicializados.');
    if (cadastro.components.has('actions')) return;

    const { elements, state } = cadastro.runtime;
    const core = cadastro.core;
    const tabela = cadastro.produtosTabela;
    const secondary = cadastro.custosMlb;
    const storeTools = global.JKCadastroStore;
    let carregamentoProdutosSeq = 0;

    function authHeaders(extra) {
        if (typeof global.obterAuthHeaders !== 'function') throw new Error('Autenticação indisponível.');
        return global.obterAuthHeaders(extra);
    }

    function lojaPorId(storeId) {
        return state.lojas.find(loja => loja.store_id === storeId) || null;
    }

    function chaveNomeLoja(valor) {
        return String(valor || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
            .toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
    }

    function rotuloLojaExibicao(loja) {
        const nome = String(loja && loja.nome || loja && loja.store_id || '').trim();
        const chave = chaveNomeLoja(nome);
        const homonimas = state.lojas.filter(item => chaveNomeLoja(item && item.nome) === chave);
        return homonimas.length > 1 ? `${nome} (${loja.store_id})` : nome;
    }

    function atualizarBotoesLojas() {
        const desabilitado = elements.cadastroLojaSelect.disabled;
        elements.cadastroLojaBotoes.querySelectorAll('.loja-btn').forEach(button => {
            const ativo = button.dataset.storeId === state.storeIdSelecionado;
            button.classList.toggle('active', ativo);
            button.setAttribute('aria-pressed', String(ativo));
            button.disabled = desabilitado;
        });
    }

    function setSeletorLojaDisabled(disabled) {
        elements.cadastroLojaSelect.disabled = Boolean(disabled);
        atualizarBotoesLojas();
    }

    function renderBotoesLojas() {
        elements.cadastroLojaBotoes.innerHTML = '';
        const opcoes = [
            { store_id: '', rotulo: 'Todas as lojas' },
            ...state.lojas.map(loja => ({ store_id: loja.store_id, rotulo: rotuloLojaExibicao(loja) })),
        ];
        opcoes.forEach(opcao => {
            const button = global.document.createElement('button');
            button.type = 'button';
            button.className = 'loja-btn';
            button.dataset.storeId = opcao.store_id;
            button.textContent = opcao.rotulo;
            button.addEventListener('click', async () => {
                try {
                    await selecionarLoja(button.dataset.storeId);
                } catch (error) {
                    core.setStatus(`Erro ao selecionar loja: ${error.message}`, 'error');
                }
            });
            elements.cadastroLojaBotoes.appendChild(button);
        });
        atualizarBotoesLojas();
    }

    function atualizarEstadoMutacoes() {
        const semLoja = !state.storeIdSelecionado;
        const somenteLeitura = semLoja || state.carregandoProdutos;
        const importacaoCatalogoAtiva = Boolean(state.importacaoCatalogoAtiva);
        const importacaoCatalogoAplicando = Boolean(state.importacaoCatalogoAplicando);
        const importacaoCatalogoCancelando = Boolean(state.importacaoCatalogoCancelando);
        const importacaoCatalogoOcupada = importacaoCatalogoAplicando || importacaoCatalogoCancelando;
        const nomeLoja = String((lojaPorId(state.storeIdSelecionado) || {}).nome || state.storeIdSelecionado);
        setSeletorLojaDisabled(importacaoCatalogoOcupada);
        [elements.btnSyncNcm, elements.btnEditarCadastro, elements.btnIncluirCadastro,
            elements.btnImportarColunas, elements.btnAtualizarCustosImpostos]
            .forEach(button => { button.disabled = somenteLeitura || importacaoCatalogoOcupada; });
        [elements.btnImportarBling, elements.btnImportarMercadoLivre]
            .forEach(button => { button.disabled = somenteLeitura || importacaoCatalogoAtiva || importacaoCatalogoOcupada; });
        if (!somenteLeitura && !importacaoCatalogoOcupada && global.NCM_SYNC && typeof global.NCM_SYNC.isRunning === 'function') {
            elements.btnSyncNcm.disabled = global.NCM_SYNC.isRunning();
        }
        elements.cadastroLojaAviso.textContent = semLoja
            ? 'Visualização consolidada. Selecione uma loja para incluir, editar, importar ou sincronizar.'
            : state.carregandoProdutos
                ? `Carregando produtos de ${nomeLoja}. Alterações temporariamente bloqueadas.`
                : `Alterações limitadas a ${nomeLoja}.`;
        atualizarBotoesLojas();
        if (cadastro.fornecedores && typeof cadastro.fornecedores.atualizarEstadoMutacoes === 'function') {
            cadastro.fornecedores.atualizarEstadoMutacoes();
        }
    }

    function extrairProdutos(payload) {
        if (Array.isArray(payload)) return payload;
        return payload && Array.isArray(payload.produtos) ? payload.produtos : [];
    }

    async function buscarProdutosLoja(loja) {
        const response = await global.fetch(storeTools.apiLoja(loja.store_id, 'produtos'), { headers: authHeaders() });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload && payload.detail || `HTTP ${response.status}`);
        return extrairProdutos(payload).map(item => ({
            ...item,
            store_id: loja.store_id,
            loja_sync: rotuloLojaExibicao(loja),
        }));
    }

    async function carregarProdutos() {
        const requestSeq = ++carregamentoProdutosSeq;
        state.carregandoProdutos = true;
        atualizarEstadoMutacoes();
        core.setStatus('Carregando produtos...', 'loading');
        elements.btnAtualizar.disabled = true;
        // Remove imediatamente o escopo anterior. Durante uma troca de loja,
        // nenhuma linha antiga deve continuar clicavel enquanto a nova resposta
        // ainda esta em transito.
        state.produtos = [];
        secondary.atualizarFiltroLojasCustos();
        tabela.renderTabela();
        secondary.renderCustos();
        secondary.montarSeletorSku();
        secondary.renderDetalhesMlbPorSku('');
        try {
            state.clientId = state.clientId || core.obterClientId();
            if (!state.clientId) {
                global.location.href = '/frontend_index.html';
                return false;
            }
            const lojasAlvo = state.storeIdSelecionado ? [lojaPorId(state.storeIdSelecionado)].filter(Boolean) : state.lojas;
            const lotes = await Promise.all(lojasAlvo.map(buscarProdutosLoja));
            if (requestSeq !== carregamentoProdutosSeq) return false;
            state.produtos = lotes.flat();
            secondary.atualizarFiltroLojasCustos();
            tabela.renderTabela();
            secondary.renderCustos();
            secondary.montarSeletorSku();
            secondary.renderDetalhesMlbPorSku(elements.skuSelect.value || '');
            const escopo = state.storeIdSelecionado ? (lojaPorId(state.storeIdSelecionado) || {}).nome : `${lojasAlvo.length} loja(s)`;
            core.setStatus(`Produtos carregados: ${state.produtos.length}${escopo ? ` em ${escopo}` : ''}`, 'success');
            return true;
        } catch (error) {
            if (requestSeq !== carregamentoProdutosSeq) return false;
            state.produtos = [];
            tabela.renderTabela();
            core.setStatus(`Erro ao carregar produtos: ${error.message}`, 'error');
            return false;
        } finally {
            if (requestSeq === carregamentoProdutosSeq) {
                state.carregandoProdutos = false;
                elements.btnAtualizar.disabled = false;
                atualizarEstadoMutacoes();
            }
        }
    }

    async function inicializarLojas() {
        state.clientId = core.obterClientId();
        if (!state.clientId) {
            global.location.href = '/frontend_index.html';
            return false;
        }
        state.lojas = await storeTools.carregarLojas(authHeaders);
        storeTools.preencherSeletor(elements.cadastroLojaSelect, state.lojas, { permitirTodas: true });
        renderBotoesLojas();
        try {
            state.storeIdSelecionado = storeTools.resolverStoreId(state.lojas, { clientId: state.clientId });
        } catch (error) {
            state.storeIdSelecionado = '';
            atualizarEstadoMutacoes();
            throw error;
        }
        elements.cadastroLojaSelect.value = state.storeIdSelecionado;
        atualizarEstadoMutacoes();
        return true;
    }

    async function selecionarLoja(storeId) {
        if (state.importacaoCatalogoAplicando || state.importacaoCatalogoCancelando) {
            throw new Error('Aguarde a operação da importação antes de trocar de loja.');
        }
        const valor = String(storeId || '').trim();
        if (valor && !storeTools.lojaExiste(state.lojas, valor)) throw new Error('Loja inválida ou indisponível para este cliente.');
        const lojaAnterior = state.storeIdSelecionado;
        state.storeIdSelecionado = valor;
        if (lojaAnterior !== valor && cadastro.importacoesCatalogos) cadastro.importacoesCatalogos.onStoreChanged(valor);
        elements.cadastroLojaSelect.value = valor;
        state.paginaAtual = 1;
        state.paginaCustosAtual = 1;
        storeTools.salvarPreferencia(state.clientId, valor, state.lojas);
        storeTools.atualizarUrl(valor);
        atualizarEstadoMutacoes();
        await carregarProdutos();
    }

    function setStatusNcmGlobal(html, cls) {
        if (!html) {
            elements.statusNcmGlobal.style.display = 'none';
            return;
        }
        elements.statusNcmGlobal.innerHTML = html;
        elements.statusNcmGlobal.className = `status-bar ${cls || ''}`;
        elements.statusNcmGlobal.style.display = 'block';
    }

    async function sincronizarNcmBackground() {
        try {
            if (!state.storeIdSelecionado) throw new Error('Selecione uma loja específica para sincronizar NCM.');
            setStatusNcmGlobal('Iniciando sincronização de NCM e classificação monofásica...', 'loading');
            elements.btnSyncNcm.disabled = true;
            await global.NCM_SYNC.iniciarSincronizacao(state.storeIdSelecionado);
        } catch (error) {
            console.error('Erro ao iniciar sincronização NCM:', error);
            setStatusNcmGlobal(`Erro: ${error.message}`, 'error');
            atualizarEstadoMutacoes();
        }
    }

    function onNcmSyncComplete(status) {
        atualizarEstadoMutacoes();
        if (status === 'done') {
            setStatusNcmGlobal('✅ NCM e classificação monofásica atualizados. Recarregando produtos...', 'success');
            global.setTimeout(() => {
                carregarProdutos();
                setStatusNcmGlobal('', '');
            }, 1500);
        } else if (status === 'error') {
            setStatusNcmGlobal('❌ Sincronização NCM finalizou com erro.', 'error');
        }
    }

    function validarCliente() {
        state.clientId = state.clientId || core.obterClientId();
        if (state.clientId) return true;
        global.location.href = '/frontend_index.html';
        return false;
    }

    function validarLojaEspecifica() {
        if (!validarCliente()) return '';
        const storeId = String(state.storeIdSelecionado || '').trim();
        if (!storeTools.lojaExiste(state.lojas, storeId)) throw new Error('Selecione uma loja específica.');
        return storeId;
    }

    async function importarColunasPorSku(arquivo) {
        if (!arquivo) return;
        core.setStatus('Importando colunas da planilha e vinculando por SKU...', 'loading');
        elements.btnAtualizar.disabled = true;
        elements.btnSyncNcm.disabled = true;
        elements.btnImportarColunas.disabled = true;
        setSeletorLojaDisabled(true);
        try {
            const storeId = validarLojaEspecifica();
            if (!storeId) return;
            const formData = new FormData();
            formData.append('arquivo', arquivo);
            formData.append('store_id', storeId);
            formData.append('modo', 'geral');
            const response = await global.fetch(storeTools.apiLoja(storeId, 'importar-colunas'), { method: 'POST', headers: authHeaders(), body: formData });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload && payload.detail || `HTTP ${response.status}`);
            const criadas = Array.isArray(payload.colunas_criadas) ? payload.colunas_criadas.length : Number(payload.colunas_criadas || 0);
            const mensagem = `Importação concluída. Colunas criadas: ${criadas}. SKUs atualizados: ${Number(payload.skus_atualizados || 0)}. SKUs incluídos: ${Number(payload.skus_incluidos || 0)}. SKUs sem correspondência: ${Number(payload.skus_sem_correspondencia || 0)}.`;
            await carregarProdutos();
            core.setStatus(mensagem, 'success');
        } catch (error) {
            core.setStatus(`Erro ao importar colunas: ${error.message}`, 'error');
        } finally {
            elements.inputImportarColunas.value = '';
            elements.btnAtualizar.disabled = false;
            setSeletorLojaDisabled(false);
            atualizarEstadoMutacoes();
        }
    }

    async function atualizarCustosImpostosPorSku(arquivo) {
        if (!arquivo) return;
        core.setStatus('Atualizando custo e imposto por SKU...', 'loading');
        elements.btnAtualizarCustosImpostos.disabled = true;
        setSeletorLojaDisabled(true);
        try {
            const storeId = validarLojaEspecifica();
            if (!storeId) return;
            const formData = new FormData();
            formData.append('arquivo', arquivo);
            formData.append('store_id', storeId);
            formData.append('modo', 'custos');
            const response = await global.fetch(storeTools.apiLoja(storeId, 'importar-colunas'), { method: 'POST', headers: authHeaders(), body: formData });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload && payload.detail || `HTTP ${response.status}`);
            const atualizados = Number(payload.skus_atualizados || 0);
            const incluidos = Number(payload.skus_incluidos || 0);
            const custosAtualizados = Number(payload.custos_loja_atualizados ?? atualizados);
            const custosIncluidos = Number(payload.custos_loja_incluidos ?? incluidos);
            const nomeLoja = payload.custos_loja || payload.loja_sync || '';
            const loja = nomeLoja ? ` em ${nomeLoja}` : '';
            const mensagem = `Custos/impostos salvos por conta${loja}. Atualizados: ${custosAtualizados}. Incluídos: ${custosIncluidos}. Sem correspondência: ${Number(payload.skus_sem_correspondencia || 0)}.`;
            await carregarProdutos();
            secondary.trocarAba('custos');
            core.setStatus(mensagem, 'success');
        } catch (error) {
            core.setStatus(`Erro ao atualizar custos/impostos: ${error.message}`, 'error');
        } finally {
            elements.inputAtualizarCustosImpostos.value = '';
            setSeletorLojaDisabled(false);
            atualizarEstadoMutacoes();
        }
    }

    function inicializarNcm() {
        if (!global.NCM_SYNC || typeof global.NCM_SYNC.init !== 'function') throw new Error('NCM_SYNC indisponível.');
        global.NCM_SYNC.init(setStatusNcmGlobal, onNcmSyncComplete);
    }

    cadastro.actions = Object.freeze({
        atualizarCustosImpostosPorSku, atualizarEstadoMutacoes, carregarProdutos, importarColunasPorSku, inicializarLojas,
        inicializarNcm, selecionarLoja, setSeletorLojaDisabled, sincronizarNcmBackground,
    });
    cadastro.publicApi.carregarProdutos = carregarProdutos;
    global.carregarProdutos = carregarProdutos;
    cadastro.components.add('actions');
})(window);
