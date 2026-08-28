(function (global) {
    'use strict';

    const cadastro = global.JKCadastro;
    const required = ['runtime', 'core'];
    if (!cadastro || required.some(component => !cadastro.components.has(component))) {
        throw new Error('Base do Cadastro de fornecedores incompleta.');
    }
    if (cadastro.components.has('fornecedores')) return;

    const { elements, state } = cadastro.runtime;
    const core = cadastro.core;
    const campos = Object.freeze([
        ['nome_empresa', 'fornecedorNomeEmpresa'],
        ['nome_contato', 'fornecedorNomeContato'],
        ['endereco_empresa', 'fornecedorEnderecoEmpresa'],
        ['telefone', 'fornecedorTelefone'],
        ['email', 'fornecedorEmail'],
        ['moeda_pagamento', 'fornecedorMoedaPagamento'],
        ['conta_beneficiario', 'fornecedorContaBeneficiario'],
        ['swift', 'fornecedorSwift'],
        ['pais_regiao_beneficiario', 'fornecedorPaisRegiao'],
        ['nome_beneficiario', 'fornecedorNomeBeneficiario'],
        ['endereco_beneficiario', 'fornecedorEnderecoBeneficiario'],
        ['banco_beneficiario', 'fornecedorBancoBeneficiario'],
        ['endereco_banco', 'fornecedorEnderecoBanco'],
        ['codigo_banco', 'fornecedorCodigoBanco'],
        ['codigo_agencia', 'fornecedorCodigoAgencia'],
        ['observacao_pagamento', 'fornecedorObservacaoPagamento'],
    ]);
    let inicializado = false;

    function authHeaders(extra) {
        if (typeof global.obterAuthHeaders !== 'function') throw new Error('Autenticação indisponível.');
        return global.obterAuthHeaders(extra);
    }

    function setStatus(mensagem, classe) {
        elements.fornecedorStatus.textContent = mensagem || '';
        elements.fornecedorStatus.className = `status-bar ${classe || ''}`;
    }

    async function lerResposta(response) {
        let payload = {};
        try { payload = await response.json(); } catch (_error) {}
        if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
        return payload;
    }

    function payloadFormulario() {
        return Object.fromEntries(campos.map(([campo, elementName]) => [campo, String(elements[elementName].value || '').trim()]));
    }

    function contaMascarada(valor) {
        const conta = String(valor || '').trim();
        if (!conta) return '';
        return conta.length <= 4 ? conta : `•••• ${conta.slice(-4)}`;
    }

    function renderLista() {
        if (!state.fornecedores.length) {
            elements.listaFornecedores.innerHTML = '<div class="tab-empty-state"><p>Nenhum fornecedor cadastrado.</p></div>';
            return;
        }
        elements.listaFornecedores.innerHTML = state.fornecedores.map(item => {
            const id = core.escaparHtml(item.id || '');
            const contato = core.escaparHtml(item.nome_contato || 'Contato não informado');
            const email = core.escaparHtml(item.email || 'E-mail não informado');
            const banco = core.escaparHtml(item.banco_beneficiario || 'Banco não informado');
            const moeda = core.escaparHtml(item.moeda_pagamento || '-');
            const conta = core.escaparHtml(contaMascarada(item.conta_beneficiario));
            return `<article class="fornecedor-item" data-id="${id}">
                <div class="fornecedor-item-header">
                    <h4>${core.escaparHtml(item.nome_empresa || '')}</h4>
                    <div class="fornecedor-item-actions">
                        <button class="btn btn-secondary" type="button" data-action="editar" data-id="${id}">Editar</button>
                        <button class="btn btn-danger" type="button" data-action="excluir" data-id="${id}">Excluir</button>
                    </div>
                </div>
                <p class="fornecedor-meta">${contato} · ${email}</p>
                <p class="fornecedor-meta">${banco} · ${moeda}${conta ? ` · Conta ${conta}` : ''}</p>
            </article>`;
        }).join('');
    }

    async function carregar() {
        setStatus('Carregando fornecedores...', 'loading');
        elements.btnAtualizarFornecedores.disabled = true;
        try {
            const response = await global.fetch('/api/cadastro/fornecedores', { headers: authHeaders() });
            const payload = await lerResposta(response);
            state.fornecedores = Array.isArray(payload) ? payload : [];
            renderLista();
            setStatus('', '');
            return true;
        } catch (error) {
            setStatus(`Erro ao carregar fornecedores: ${error.message}`, 'error');
            return false;
        } finally {
            elements.btnAtualizarFornecedores.disabled = false;
        }
    }

    function limparFormulario() {
        elements.fornecedorForm.reset();
        elements.fornecedorId.value = '';
        elements.fornecedorFormTitulo.textContent = 'Novo fornecedor';
        elements.btnSalvarFornecedor.textContent = 'Salvar fornecedor';
    }

    function editar(fornecedorId) {
        const item = state.fornecedores.find(registro => String(registro.id || '') === String(fornecedorId || ''));
        if (!item) return false;
        elements.fornecedorId.value = String(item.id || '');
        campos.forEach(([campo, elementName]) => { elements[elementName].value = String(item[campo] || ''); });
        elements.fornecedorFormTitulo.textContent = 'Editar fornecedor';
        elements.btnSalvarFornecedor.textContent = 'Atualizar fornecedor';
        elements.fornecedorForm.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return true;
    }

    async function salvar(event) {
        if (event) event.preventDefault();
        const fornecedorId = String(elements.fornecedorId.value || '').trim();
        const url = fornecedorId
            ? `/api/cadastro/fornecedores/${encodeURIComponent(fornecedorId)}`
            : '/api/cadastro/fornecedores';
        setStatus(fornecedorId ? 'Atualizando fornecedor...' : 'Salvando fornecedor...', 'loading');
        elements.btnSalvarFornecedor.disabled = true;
        try {
            const response = await global.fetch(url, {
                method: fornecedorId ? 'PUT' : 'POST',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(payloadFormulario()),
            });
            await lerResposta(response);
            limparFormulario();
            await carregar();
            setStatus('Fornecedor salvo com sucesso.', 'success');
            return true;
        } catch (error) {
            setStatus(`Erro ao salvar fornecedor: ${error.message}`, 'error');
            return false;
        } finally {
            elements.btnSalvarFornecedor.disabled = false;
        }
    }

    async function excluir(fornecedorId) {
        const item = state.fornecedores.find(registro => String(registro.id || '') === String(fornecedorId || ''));
        if (!item || !global.confirm(`Excluir o fornecedor ${item.nome_empresa}?`)) return false;
        setStatus('Excluindo fornecedor...', 'loading');
        try {
            const response = await global.fetch(`/api/cadastro/fornecedores/${encodeURIComponent(fornecedorId)}`, {
                method: 'DELETE',
                headers: authHeaders(),
            });
            await lerResposta(response);
            if (String(elements.fornecedorId.value || '') === String(fornecedorId || '')) limparFormulario();
            await carregar();
            setStatus('Fornecedor excluído.', 'success');
            return true;
        } catch (error) {
            setStatus(`Erro ao excluir fornecedor: ${error.message}`, 'error');
            return false;
        }
    }

    function tratarCliqueLista(event) {
        const button = event.target.closest && event.target.closest('button[data-action][data-id]');
        if (!button) return;
        if (button.dataset.action === 'editar') editar(button.dataset.id);
        if (button.dataset.action === 'excluir') excluir(button.dataset.id);
    }

    function init() {
        if (inicializado) return;
        inicializado = true;
        elements.fornecedorForm.addEventListener('submit', salvar);
        elements.btnLimparFornecedor.addEventListener('click', limparFormulario);
        elements.btnAtualizarFornecedores.addEventListener('click', carregar);
        elements.listaFornecedores.addEventListener('click', tratarCliqueLista);
        carregar();
    }

    cadastro.fornecedores = Object.freeze({ carregar, editar, excluir, init, limparFormulario, salvar });
    cadastro.components.add('fornecedores');
})(window);
