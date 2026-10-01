(function (global) {
    'use strict';

    const cadastro = global.JKCadastro;
    if (!cadastro || !cadastro.components.has('runtime')) throw new Error('Base do Cadastro incompleta.');
    if (cadastro.components.has('importador-loja')) return;

    const { elements, state } = cadastro.runtime;
    const campos = Object.freeze([
        ['nome_empresa', 'importadorNomeEmpresa'],
        ['tax_id', 'importadorTaxId'],
        ['telefone', 'importadorTelefone'],
        ['email', 'importadorEmail'],
        ['contato', 'importadorContato'],
        ['logradouro', 'importadorLogradouro'],
        ['bairro', 'importadorBairro'],
        ['cidade', 'importadorCidade'],
        ['cep', 'importadorCep'],
        ['estado', 'importadorEstado'],
        ['pais', 'importadorPais'],
    ]);
    let requestId = 0;
    let dirty = false;

    function status(mensagem, classe) {
        elements.importadorLojaStatus.textContent = mensagem || '';
        elements.importadorLojaStatus.className = `status-bar ${classe || ''}`;
    }

    function atualizarEstadoMutacoes() {
        const semLoja = !String(state.storeIdSelecionado || '').trim();
        campos.forEach(([, id]) => { elements[id].disabled = semLoja; });
        elements.btnSalvarImportadorLoja.disabled = semLoja;
        if (semLoja) status('Selecione uma loja para consultar ou alterar o importador.', '');
    }

    function preencher(registro) {
        campos.forEach(([campo, id]) => { elements[id].value = String((registro || {})[campo] || ''); });
        dirty = false;
    }

    async function ler(response) {
        let payload = {};
        try { payload = await response.json(); } catch (_error) {}
        if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
        return payload;
    }

    async function carregar() {
        const storeId = String(state.storeIdSelecionado || '').trim();
        const currentRequest = ++requestId;
        preencher(null);
        atualizarEstadoMutacoes();
        if (!storeId) return;
        status('Carregando dados do importador/loja...', 'loading');
        try {
            const response = await global.fetch(`/api/cadastro/lojas/${encodeURIComponent(storeId)}/importador-loja`, {
                headers: global.obterAuthHeaders(),
            });
            const registro = await ler(response);
            if (currentRequest !== requestId || storeId !== state.storeIdSelecionado || dirty) return;
            preencher(registro);
            status(registro && registro.nome_empresa ? '' : 'Nenhum dado cadastrado para esta loja.', '');
        } catch (error) {
            if (currentRequest === requestId) status(`Erro ao carregar importador/loja: ${error.message}`, 'error');
        }
    }

    async function salvar(event) {
        event.preventDefault();
        const storeId = String(state.storeIdSelecionado || '').trim();
        if (!storeId) return;
        const registro = Object.fromEntries(campos.map(([campo, id]) => [campo, String(elements[id].value || '').trim()]));
        const currentRequest = ++requestId;
        elements.btnSalvarImportadorLoja.disabled = true;
        status('Salvando dados do importador/loja...', 'loading');
        try {
            const response = await global.fetch(`/api/cadastro/lojas/${encodeURIComponent(storeId)}/importador-loja`, {
                method: 'PUT',
                headers: global.obterAuthHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(registro),
            });
            const resultado = await ler(response);
            if (currentRequest !== requestId || storeId !== state.storeIdSelecionado) return;
            preencher(resultado);
            status('Dados do importador/loja salvos.', 'success');
        } catch (error) {
            if (currentRequest === requestId) status(`Erro ao salvar importador/loja: ${error.message}`, 'error');
        } finally {
            atualizarEstadoMutacoes();
        }
    }

    function init() {
        elements.importadorLojaForm.addEventListener('input', () => { dirty = true; });
        elements.importadorLojaForm.addEventListener('submit', salvar);
        atualizarEstadoMutacoes();
    }

    cadastro.importadorLoja = Object.freeze({ init, carregar, atualizarEstadoMutacoes });
    cadastro.components.add('importador-loja');
})(window);
