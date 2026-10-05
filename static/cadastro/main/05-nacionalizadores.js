(function (global) {
    'use strict';

    const cadastro = global.JKCadastro;
    if (!cadastro || !cadastro.components.has('core')) throw new Error('Base do Cadastro incompleta.');
    if (cadastro.components.has('nacionalizadores')) return;

    const { elements, state } = cadastro.runtime;
    const core = cadastro.core;
    const campos = Object.freeze([
        ['nome_empresa', 'nacionalizadorNomeEmpresa'],
        ['cnpj', 'nacionalizadorCnpj'],
        ['nome_contato', 'nacionalizadorNomeContato'],
        ['telefone', 'nacionalizadorTelefone'],
        ['endereco_empresa', 'nacionalizadorEnderecoEmpresa'],
    ]);
    let registros = [];
    let cliente = '';
    let requestId = 0;
    let carregando = false;
    let salvando = false;
    let editado = false;
    let inicializado = false;

    function clienteAtual() {
        return String(core.obterClientId() || '').trim();
    }

    function status(mensagem, classe) {
        elements.nacionalizadorStatus.textContent = mensagem || '';
        elements.nacionalizadorStatus.className = `status-bar ${classe || ''}`;
    }

    function limparFormulario() {
        if (salvando) return;
        elements.nacionalizadorForm.reset();
        elements.nacionalizadorId.value = '';
        elements.nacionalizadorFormTitulo.textContent = 'Novo nacionalizador';
        elements.btnSalvarNacionalizador.textContent = 'Incluir nacionalizador';
        editado = false;
    }

    function sincronizarCliente() {
        const atual = clienteAtual();
        if (atual === cliente) return;
        cliente = atual;
        requestId += 1;
        registros = [];
        carregando = false;
        salvando = false;
        limparFormulario();
        status('', '');
    }

    function somenteLeitura() {
        return !cliente || !String(state.storeIdSelecionado || '').trim();
    }

    function renderLista() {
        if (!registros.length) {
            elements.listaNacionalizadores.innerHTML = '<div class="tab-empty-state"><p>Nenhum nacionalizador cadastrado.</p></div>';
            return;
        }
        const escapar = core.escaparHtml;
        elements.listaNacionalizadores.innerHTML = registros.map(item => {
            const id = escapar(item.id || '');
            const acao = somenteLeitura() || salvando ? '' : `<div class="fornecedor-item-actions"><button class="btn btn-secondary" type="button" data-action="editar" data-id="${id}">Editar</button></div>`;
            return `<article class="fornecedor-item">
                <div class="fornecedor-item-header"><h4>${escapar(item.nome_empresa || '')}</h4></div>
                <p class="fornecedor-meta">CNPJ: ${escapar(item.cnpj || 'Não informado')}</p>
                <p class="fornecedor-meta">${escapar(item.nome_contato || 'Contato não informado')} · ${escapar(item.telefone || 'Telefone não informado')}</p>
                <p class="fornecedor-meta">${escapar(item.endereco_empresa || 'Endereço não informado')}</p>
                ${acao}
            </article>`;
        }).join('');
    }

    function atualizarEstadoMutacoes() {
        sincronizarCliente();
        const bloqueado = somenteLeitura() || salvando;
        campos.forEach(([, id]) => { elements[id].disabled = bloqueado; });
        elements.btnSalvarNacionalizador.disabled = bloqueado;
        elements.btnLimparNacionalizador.disabled = bloqueado;
        elements.btnAtualizarNacionalizadores.disabled = !cliente || carregando || salvando;
        elements.nacionalizadorForm.setAttribute('aria-disabled', String(bloqueado));
        renderLista();
        if (somenteLeitura()) status('Selecione uma loja para incluir ou editar nacionalizadores deste cliente.', '');
    }

    async function lerResposta(response) {
        let payload = {};
        try { payload = await response.json(); } catch (_error) {}
        if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
        return payload;
    }

    function respostaAtual(id, clientId) {
        return id === requestId && clientId === clienteAtual();
    }

    async function carregar() {
        atualizarEstadoMutacoes();
        if (!cliente || salvando) return false;
        const clientId = cliente;
        const id = ++requestId;
        carregando = true;
        status('Carregando nacionalizadores...', 'loading');
        atualizarEstadoMutacoes();
        try {
            const response = await global.fetch('/api/cadastro/nacionalizadores', { headers: global.obterAuthHeaders() });
            const payload = await lerResposta(response);
            if (!respostaAtual(id, clientId)) return false;
            if (!Array.isArray(payload)) throw new Error('Resposta inválida do cadastro.');
            registros = payload;
            status('', '');
            return true;
        } catch (error) {
            if (respostaAtual(id, clientId)) status(`Erro ao carregar nacionalizadores: ${error.message}`, 'error');
            return false;
        } finally {
            if (respostaAtual(id, clientId)) carregando = false;
            atualizarEstadoMutacoes();
        }
    }

    function editar(nacionalizadorId) {
        atualizarEstadoMutacoes();
        if (somenteLeitura() || salvando) return false;
        const item = registros.find(registro => String(registro.id || '') === String(nacionalizadorId || ''));
        if (!item) return false;
        elements.nacionalizadorId.value = String(item.id || '');
        campos.forEach(([campo, id]) => { elements[id].value = String(item[campo] || ''); });
        elements.nacionalizadorFormTitulo.textContent = 'Editar nacionalizador';
        elements.btnSalvarNacionalizador.textContent = 'Atualizar nacionalizador';
        editado = true;
        elements.nacionalizadorForm.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return true;
    }

    async function salvar(event) {
        if (event) event.preventDefault();
        atualizarEstadoMutacoes();
        if (somenteLeitura() || salvando) return false;
        const nacionalizadorId = String(elements.nacionalizadorId.value || '').trim();
        const payload = Object.fromEntries(campos.map(([campo, id]) => [campo, String(elements[id].value || '').trim()]));
        const clientId = cliente;
        const id = ++requestId;
        carregando = false;
        salvando = true;
        status('Salvando nacionalizador...', 'loading');
        atualizarEstadoMutacoes();
        try {
            const url = nacionalizadorId ? `/api/cadastro/nacionalizadores/${encodeURIComponent(nacionalizadorId)}` : '/api/cadastro/nacionalizadores';
            const response = await global.fetch(url, {
                method: nacionalizadorId ? 'PUT' : 'POST',
                headers: global.obterAuthHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(payload),
            });
            const resultado = await lerResposta(response);
            if (!respostaAtual(id, clientId)) return false;
            if (!resultado.nacionalizador || !resultado.nacionalizador.id) throw new Error('Resposta inválida do cadastro.');
            registros = registros.filter(item => String(item.id) !== String(resultado.nacionalizador.id));
            registros.push(resultado.nacionalizador);
            registros.sort((a, b) => String(a.nome_empresa || '').localeCompare(String(b.nome_empresa || ''), 'pt-BR'));
            salvando = false;
            limparFormulario();
            status('Nacionalizador salvo com sucesso.', 'success');
            return true;
        } catch (error) {
            if (respostaAtual(id, clientId)) status(`Erro ao salvar nacionalizador: ${error.message}`, 'error');
            return false;
        } finally {
            if (respostaAtual(id, clientId)) salvando = false;
            atualizarEstadoMutacoes();
        }
    }

    function init() {
        if (inicializado) return;
        inicializado = true;
        elements.nacionalizadorForm.addEventListener('input', () => { editado = true; });
        elements.nacionalizadorForm.addEventListener('submit', salvar);
        elements.btnLimparNacionalizador.addEventListener('click', limparFormulario);
        elements.btnAtualizarNacionalizadores.addEventListener('click', carregar);
        elements.listaNacionalizadores.addEventListener('click', event => {
            const button = event.target.closest?.('button[data-action="editar"][data-id]');
            if (button) editar(button.dataset.id);
        });
        atualizarEstadoMutacoes();
    }

    cadastro.nacionalizadores = Object.freeze({
        init, carregar, editar, salvar, limparFormulario, atualizarEstadoMutacoes,
        temEdicaoPendente: () => editado || salvando,
    });
    cadastro.components.add('nacionalizadores');
})(window);
