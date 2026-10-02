(function instalarFornecedorListas(global) {
    'use strict';

    const geracoes = new WeakMap();
    let cancelarSelecaoAtual = null;

    function nome(lista) {
        return String(lista && (lista.supplier || lista.fornecedor) || '').trim() || 'Fornecedor não informado';
    }

    function opcao(select, valor, texto, desabilitada) {
        const item = document.createElement('option');
        item.value = valor;
        item.textContent = texto;
        item.disabled = !!desabilitada;
        select.appendChild(item);
        return item;
    }

    function resetarSelect(select, texto) {
        select.innerHTML = '';
        opcao(select, '', texto, false);
        select.value = '';
    }

    async function preencherSelect(select, lista, configuracao) {
        const config = configuracao || {};
        const request = config.fetch || global.fetch.bind(global);
        const headers = typeof config.headers === 'function' ? config.headers() :
            (config.headers || (typeof global.obterAuthHeaders === 'function' ? global.obterAuthHeaders() : {}));
        const geracao = {};
        geracoes.set(select, geracao);
        select.disabled = true;
        resetarSelect(select, 'Carregando fornecedores...');

        const vigente = () => geracoes.get(select) === geracao;
        try {
            const resposta = await request('/api/cadastro/fornecedores', { headers: { ...headers } });
            const dados = await resposta.json();
            if (!resposta.ok) throw new Error(dados.detail || 'Não foi possível carregar os fornecedores.');
            if (!Array.isArray(dados)) throw new Error('Cadastro de fornecedores inválido.');
            const fornecedores = dados.filter(item => item && String(item.id || '').trim() && String(item.nome_empresa || '').trim());
            if (!vigente()) return [];

            resetarSelect(select, fornecedores.length ? 'Selecione um fornecedor cadastrado' : 'Nenhum fornecedor cadastrado');
            fornecedores.forEach(item => opcao(select, String(item.id), String(item.nome_empresa), false));
            const fornecedorId = String(lista && lista.fornecedor_id || '').trim();
            if (fornecedorId) {
                if (!fornecedores.some(item => String(item.id) === fornecedorId)) {
                    opcao(select, fornecedorId, 'Fornecedor indisponível: ' + nome(lista), true);
                }
                select.value = fornecedorId;
            } else if (lista && String(lista.supplier || lista.fornecedor || '').trim()) {
                select.options[0].textContent = 'Fornecedor sem vínculo: ' + nome(lista);
            }
            select.disabled = !fornecedores.length;
            return fornecedores;
        } catch (erro) {
            if (vigente()) resetarSelect(select, 'Não foi possível carregar os fornecedores');
            throw erro;
        }
    }

    function instalarEstilos() {
        if (document.getElementById('jkFornecedorListaEstilos')) return;
        const style = document.createElement('style');
        style.id = 'jkFornecedorListaEstilos';
        style.textContent = `
            .jk-fornecedor-overlay { position:fixed; inset:0; z-index:12000; background:rgba(3,10,20,.78); display:flex; align-items:center; justify-content:center; padding:20px; box-sizing:border-box; }
            .jk-fornecedor-dialogo { width:min(460px,100%); box-sizing:border-box; border:1px solid #39739b; border-radius:14px; padding:24px; background:#102034; color:#e8f1ff; box-shadow:0 18px 60px #0007; font:14px system-ui,sans-serif; }
            .jk-fornecedor-dialogo h2 { margin:0 0 12px; font-size:21px; }
            .jk-fornecedor-dialogo p { color:#bed1e6; line-height:1.5; }
            .jk-fornecedor-dialogo label { display:block; font-weight:600; margin:18px 0 8px; }
            .jk-fornecedor-dialogo select { width:100%; box-sizing:border-box; padding:11px; border:1px solid #39739b; border-radius:8px; background:#0b1729; color:#eef6ff; font:inherit; }
            .jk-fornecedor-status { min-height:42px; margin:12px 0; color:#bed1e6; line-height:1.5; }
            .jk-fornecedor-acoes { display:flex; justify-content:flex-end; gap:10px; }
            .jk-fornecedor-acoes button { border:1px solid #39739b; border-radius:8px; padding:9px 14px; color:#eef6ff; background:#193650; cursor:pointer; font:inherit; }
            .jk-fornecedor-acoes .jk-fornecedor-confirmar { background:#197db0; }
            .jk-fornecedor-acoes button:disabled { opacity:.5; cursor:default; }
            .jk-fornecedor-dialogo :focus-visible { outline:2px solid #67caff; outline-offset:3px; }
        `;
        document.head.appendChild(style);
    }

    function selecionar(configuracao) {
        const config = configuracao || {};
        if (cancelarSelecaoAtual) cancelarSelecaoAtual();
        instalarEstilos();
        const focoAnterior = document.activeElement;
        const overlay = document.createElement('div');
        overlay.id = 'jkFornecedorListaModal';
        overlay.className = 'jk-fornecedor-overlay';
        overlay.innerHTML = '<div class="jk-fornecedor-dialogo" role="dialog" aria-modal="true" aria-labelledby="jkFornecedorListaTitulo" aria-describedby="jkFornecedorListaAjuda">' +
            '<h2 id="jkFornecedorListaTitulo"></h2>' +
            '<p id="jkFornecedorListaAjuda">Informe o fornecedor cadastrado para esta lista.</p>' +
            '<label for="jkFornecedorListaSelect">Fornecedor *</label>' +
            '<select id="jkFornecedorListaSelect" required aria-describedby="jkFornecedorListaStatus"></select>' +
            '<div id="jkFornecedorListaStatus" class="jk-fornecedor-status" role="status" aria-live="polite">Carregando fornecedores...</div>' +
            '<div class="jk-fornecedor-acoes"><button id="jkFornecedorListaCancelar" type="button">Cancelar</button>' +
            '<button id="jkFornecedorListaConfirmar" type="button" class="jk-fornecedor-confirmar" disabled>Continuar</button></div></div>';
        overlay.querySelector('#jkFornecedorListaTitulo').textContent = config.titulo || 'Fornecedor da lista';
        document.body.appendChild(overlay);
        const select = overlay.querySelector('#jkFornecedorListaSelect');
        const status = overlay.querySelector('#jkFornecedorListaStatus');
        const confirmar = overlay.querySelector('#jkFornecedorListaConfirmar');
        const cancelar = overlay.querySelector('#jkFornecedorListaCancelar');

        return new Promise(resolve => {
            let encerrado = false;
            let fornecedores = [];
            const concluir = (registro) => {
                if (encerrado) return;
                encerrado = true;
                geracoes.delete(select);
                overlay.remove();
                document.removeEventListener('keydown', teclado, true);
                if (cancelarSelecaoAtual === cancelarAtual) cancelarSelecaoAtual = null;
                if (focoAnterior && focoAnterior.isConnected && typeof focoAnterior.focus === 'function') focoAnterior.focus();
                resolve(registro || null);
            };
            const cancelarAtual = () => concluir(null);
            cancelarSelecaoAtual = cancelarAtual;
            const teclado = (event) => {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    event.stopPropagation();
                    concluir(null);
                } else if (event.key === 'Tab') {
                    const focaveis = [select, cancelar, confirmar].filter(item => !item.disabled);
                    const primeiro = focaveis[0];
                    const ultimo = focaveis[focaveis.length - 1];
                    if (event.shiftKey && document.activeElement === primeiro) { event.preventDefault(); ultimo.focus(); }
                    else if (!event.shiftKey && document.activeElement === ultimo) { event.preventDefault(); primeiro.focus(); }
                }
            };
            document.addEventListener('keydown', teclado, true);
            cancelar.addEventListener('click', cancelarAtual);
            overlay.addEventListener('click', event => { if (event.target === overlay) concluir(null); });
            select.addEventListener('change', () => {
                confirmar.disabled = !fornecedores.some(item => String(item.id) === select.value);
                status.textContent = confirmar.disabled ? 'Selecione um fornecedor cadastrado para continuar.' : '';
            });
            confirmar.addEventListener('click', () => {
                const fornecedor = fornecedores.find(item => String(item.id) === select.value);
                if (fornecedor) concluir(fornecedor);
            });
            cancelar.focus();
            preencherSelect(select, null, config).then(registros => {
                if (encerrado) return;
                fornecedores = registros;
                status.textContent = registros.length ? 'Selecione um fornecedor cadastrado para continuar.' :
                    'Cadastre um fornecedor em Cadastro > Fornecedores para criar a lista.';
                if (registros.length) select.focus();
            }).catch(erro => {
                if (!encerrado) status.textContent = (erro.message || 'Não foi possível carregar os fornecedores.') + ' Feche e tente novamente.';
            });
        });
    }

    global.JKFornecedorListas = Object.freeze({ preencherSelect, selecionar, nome });
})(typeof window !== 'undefined' ? window : globalThis);
