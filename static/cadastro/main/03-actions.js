(function (global) {
    'use strict';

    const cadastro = global.JKCadastro;
    if (!cadastro || !cadastro.components.has('custos-mlb')) throw new Error('Painéis do Cadastro não inicializados.');
    if (cadastro.components.has('actions')) return;

    const { elements, state } = cadastro.runtime;
    const core = cadastro.core;
    const tabela = cadastro.produtosTabela;
    const secondary = cadastro.custosMlb;

    function authHeaders(extra) {
        if (typeof global.obterAuthHeaders !== 'function') throw new Error('Autenticação indisponível.');
        return global.obterAuthHeaders(extra);
    }

    async function carregarProdutos() {
        core.setStatus('Carregando produtos...', 'loading');
        elements.btnAtualizar.disabled = true;
        elements.btnSyncNcm.disabled = true;
        try {
            state.clientId = state.clientId || core.obterClientId();
            if (!state.clientId) {
                global.location.href = '/frontend_index.html';
                return;
            }
            const response = await global.fetch('/api/cadastro/produtos', { headers: authHeaders() });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            state.produtos = await response.json();
            secondary.atualizarFiltroLojasCustos();
            tabela.renderTabela();
            secondary.renderCustos();
            secondary.montarSeletorSku();
            secondary.renderDetalhesMlbPorSku(elements.skuSelect.value || '');
            core.setStatus(`Produtos carregados: ${state.produtos.length}`, 'success');
        } catch (error) {
            core.setStatus(`Erro ao carregar produtos: ${error.message}`, 'error');
        } finally {
            elements.btnAtualizar.disabled = false;
            elements.btnSyncNcm.disabled = false;
        }
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
            setStatusNcmGlobal('Iniciando sincronização de NCM e classificação monofásica...', 'loading');
            elements.btnSyncNcm.disabled = true;
            await global.NCM_SYNC.iniciarSincronizacao();
        } catch (error) {
            console.error('Erro ao iniciar sincronização NCM:', error);
            setStatusNcmGlobal(`Erro: ${error.message}`, 'error');
            elements.btnSyncNcm.disabled = false;
        }
    }

    function onNcmSyncComplete(status) {
        elements.btnSyncNcm.disabled = false;
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

    async function importarColunasPorSku(arquivo) {
        if (!arquivo) return;
        core.setStatus('Importando colunas da planilha e vinculando por SKU...', 'loading');
        elements.btnAtualizar.disabled = true;
        elements.btnSyncNcm.disabled = true;
        elements.btnImportarColunas.disabled = true;
        try {
            if (!validarCliente()) return;
            const formData = new FormData();
            formData.append('arquivo', arquivo);
            const loja = String(elements.lojaCustosSelect.value || '').trim();
            if (!loja) throw new Error('Selecione uma loja para salvar custo e imposto por conta.');
            formData.append('loja', loja);
            formData.append('modo', 'custos');
            const response = await global.fetch('/api/cadastro/importar-colunas', { method: 'POST', headers: authHeaders(), body: formData });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload && payload.detail || `HTTP ${response.status}`);
            const criadas = (payload.colunas_criadas || []).length;
            core.setStatus(`Importação concluída. Colunas criadas: ${criadas}. SKUs atualizados: ${Number(payload.skus_atualizados || 0)}. SKUs incluídos: ${Number(payload.skus_incluidos || 0)}. SKUs sem correspondência: ${Number(payload.skus_sem_correspondencia || 0)}.`, 'success');
            await carregarProdutos();
        } catch (error) {
            core.setStatus(`Erro ao importar colunas: ${error.message}`, 'error');
        } finally {
            elements.inputImportarColunas.value = '';
            elements.btnAtualizar.disabled = false;
            elements.btnSyncNcm.disabled = false;
            elements.btnImportarColunas.disabled = false;
        }
    }

    async function atualizarCustosImpostosPorSku(arquivo) {
        if (!arquivo) return;
        core.setStatus('Atualizando custo e imposto por SKU...', 'loading');
        elements.btnAtualizarCustosImpostos.disabled = true;
        try {
            if (!validarCliente()) return;
            const formData = new FormData();
            formData.append('arquivo', arquivo);
            const response = await global.fetch('/api/cadastro/importar-colunas', { method: 'POST', headers: authHeaders(), body: formData });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload && payload.detail || `HTTP ${response.status}`);
            const atualizados = Number(payload.skus_atualizados || 0);
            const incluidos = Number(payload.skus_incluidos || 0);
            const custosAtualizados = Number(payload.custos_loja_atualizados ?? atualizados);
            const custosIncluidos = Number(payload.custos_loja_incluidos ?? incluidos);
            const loja = payload.custos_loja ? ` em ${payload.custos_loja}` : '';
            core.setStatus(`Custos/impostos salvos por conta${loja}. Atualizados: ${custosAtualizados}. Incluídos: ${custosIncluidos}. Sem correspondência: ${Number(payload.skus_sem_correspondencia || 0)}.`, 'success');
            await carregarProdutos();
            secondary.trocarAba('custos');
        } catch (error) {
            core.setStatus(`Erro ao atualizar custos/impostos: ${error.message}`, 'error');
        } finally {
            elements.inputAtualizarCustosImpostos.value = '';
            elements.btnAtualizarCustosImpostos.disabled = false;
        }
    }

    function inicializarNcm() {
        if (!global.NCM_SYNC || typeof global.NCM_SYNC.init !== 'function') throw new Error('NCM_SYNC indisponível.');
        global.NCM_SYNC.init(setStatusNcmGlobal, onNcmSyncComplete);
    }

    cadastro.actions = Object.freeze({ atualizarCustosImpostosPorSku, carregarProdutos, importarColunasPorSku, inicializarNcm, sincronizarNcmBackground });
    cadastro.publicApi.carregarProdutos = carregarProdutos;
    global.carregarProdutos = carregarProdutos;
    cadastro.components.add('actions');
})(window);
