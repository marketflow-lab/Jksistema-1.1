(function () {
    'use strict';

    const salvando = new Map();
    const editores = new Map();
    const controles = new WeakMap();
    const escapar = valor => String(valor || '').replace(/[&<>"']/g, caractere =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[caractere]);

    function normalizarData(valor) {
        const texto = String(valor || '').trim();
        let partes = texto.match(/^(\d{4})-(\d{2})-(\d{2})$/);
        if (!partes) {
            const brasileira = texto.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
            if (!brasileira) return '';
            partes = [texto, brasileira[3], brasileira[2], brasileira[1]];
        }
        const ano = Number(partes[1]), mes = Number(partes[2]), dia = Number(partes[3]);
        const bissexto = ano % 4 === 0 && (ano % 100 !== 0 || ano % 400 === 0);
        const dias = [31, bissexto ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
        if (ano < 1 || mes < 1 || mes > 12 || dia < 1 || dia > dias[mes - 1]) return '';
        return partes[1] + '-' + partes[2] + '-' + partes[3];
    }

    function formatarData(valor) {
        const data = normalizarData(valor);
        return data ? data.slice(8, 10) + '/' + data.slice(5, 7) + '/' + data.slice(0, 4) :
            String(valor || '').trim() || 'Não informada';
    }

    function html(lista) {
        const texto = formatarData(lista.order_date);
        return '<span class="meta-pill meta-loja meta-data-pedido meta-loja-editavel" data-order-date-pill data-lista-order-date-id="' +
            escapar(lista.id) + '" role="button" tabindex="0" title="Data do pedido: ' + escapar(texto) +
            '. Clique duas vezes para incluir ou alterar.">Data do pedido: <strong>' + escapar(texto) + '</strong></span>';
    }

    function atualizar(id) {
        document.querySelectorAll('[data-order-date-pill]').forEach(pill => {
            const contexto = controles.get(pill);
            if (!contexto || contexto.id !== id) return;
            const ocupado = salvando.has(id);
            pill.setAttribute('aria-busy', String(ocupado));
            pill.setAttribute('aria-disabled', String(ocupado));
            const input = pill.querySelector('input');
            if (input) { input.disabled = ocupado; return; }
            const texto = formatarData(contexto.atual().order_date);
            pill.innerHTML = 'Data do pedido: <strong>' + escapar(texto) + '</strong>';
            pill.title = 'Data do pedido: ' + texto + '. Clique duas vezes para incluir ou alterar.';
        });
    }

    function iniciar(pill, contexto) {
        const { id } = contexto;
        if (pill.querySelector('input') || salvando.has(id)) return;
        const aberto = editores.get(id);
        if (aberto && aberto.pill.isConnected) { aberto.input.focus(); return; }
        editores.delete(id);
        const original = String(contexto.atual().order_date || '').trim();
        const valorInicial = normalizarData(original);
        const input = document.createElement('input');
        let encerrado = false;
        let alterado = false;
        input.type = 'date';
        input.min = '0001-01-01';
        input.max = '9999-12-31';
        input.className = 'meta-loja-input meta-data-pedido-input';
        input.setAttribute('aria-label', 'Data do pedido da lista ' + (contexto.atual().nome_lista || 'Sem nome'));
        input.value = valorInicial;
        pill.textContent = 'Data do pedido: ';
        pill.appendChild(input);

        const fechar = recuperarFoco => {
            if (encerrado) return;
            encerrado = true;
            if (editores.get(id)?.pill === pill) editores.delete(id);
            pill.replaceChildren();
            atualizar(id);
            if (recuperarFoco && pill.isConnected) pill.focus();
        };
        const erro = mensagem => {
            input.setAttribute('aria-invalid', 'true');
            input.title = mensagem;
            contexto.status(mensagem);
            if (input.isConnected) input.focus();
        };
        const salvar = () => {
            const pendente = salvando.get(id);
            if (pendente) return pendente;
            if (encerrado || !pill.isConnected) return Promise.resolve(true);
            const valor = input.value;
            if (!input.validity.valid || (valor && normalizarData(valor) !== valor)) {
                erro('Informe uma data do pedido válida.');
                return Promise.resolve(false);
            }
            if ((!alterado && valor === valorInicial) || (valor === valorInicial && (!original || valorInicial))) {
                fechar(document.activeElement === input);
                return Promise.resolve(true);
            }
            if (valor === String(contexto.atual().order_date || '').trim()) {
                fechar(document.activeElement === input);
                return Promise.resolve(true);
            }
            const recuperarFoco = document.activeElement === input;
            input.removeAttribute('aria-invalid');
            input.removeAttribute('title');
            const operacao = Promise.resolve().then(async () => {
                let sucesso = false;
                try {
                    contexto.status('Salvando data do pedido...');
                    const resposta = await fetch('/api/medias-compras/listas-pedidos/' + encodeURIComponent(id), {
                        method: 'PUT', headers: { ...contexto.headers(), 'Content-Type': 'application/json' },
                        body: JSON.stringify({ order_date: valor })
                    });
                    const dados = await resposta.json();
                    if (!resposta.ok) throw new Error(dados.detail || 'Não foi possível salvar a data do pedido.');
                    contexto.salvo(dados.lista || { order_date: valor });
                    sucesso = true;
                    contexto.status('Data do pedido atualizada com sucesso.');
                } catch (falha) {
                    erro(falha.message || 'Não foi possível salvar a data do pedido.');
                } finally {
                    salvando.delete(id);
                    atualizar(id);
                    if (sucesso) fechar(recuperarFoco && (document.activeElement === input || document.activeElement === document.body));
                    else if (recuperarFoco && input.isConnected) input.focus();
                }
                return sucesso;
            });
            salvando.set(id, operacao);
            atualizar(id);
            return operacao;
        };
        editores.set(id, { pill, input, salvar });
        const marcarAlteracao = () => {
            alterado = true;
            input.removeAttribute('aria-invalid');
            input.removeAttribute('title');
        };
        input.addEventListener('input', marcarAlteracao);
        input.addEventListener('change', marcarAlteracao);
        input.addEventListener('keydown', evento => {
            evento.stopPropagation();
            if (evento.key === 'Enter') { evento.preventDefault(); salvar(); }
            if (evento.key === 'Escape') {
                evento.preventDefault();
                if (!salvando.has(id)) fechar(true);
            }
        });
        input.addEventListener('blur', () => { if (!encerrado) salvar(); });
        input.focus();
    }

    function preparar(pill, configuracao) {
        if (!pill || !configuracao.id) return;
        const contexto = { ...configuracao, id: String(configuracao.id) };
        controles.set(pill, contexto);
        const ocupado = salvando.has(contexto.id);
        pill.setAttribute('aria-busy', String(ocupado));
        pill.setAttribute('aria-disabled', String(ocupado));
        pill.addEventListener('click', evento => {
            evento.stopPropagation();
            if (evento.target === pill || evento.target.tagName === 'STRONG') evento.preventDefault();
        });
        pill.addEventListener('dblclick', evento => {
            evento.stopPropagation();
            if (evento.target.tagName !== 'INPUT') { evento.preventDefault(); iniciar(pill, contexto); }
        });
        pill.addEventListener('keydown', evento => {
            evento.stopPropagation();
            if (evento.target === pill && ['Enter', ' '].includes(evento.key)) {
                evento.preventDefault();
                iniciar(pill, contexto);
            }
        });
        atualizar(contexto.id);
    }

    async function aguardarSalvamento(id) {
        id = String(id || '');
        const pendente = salvando.get(id);
        if (pendente) return await pendente;
        const editor = editores.get(id);
        if (editor && editor.pill.isConnected) return await editor.salvar();
        editores.delete(id);
        return true;
    }

    window.JKDataPedidoLista = { html, preparar, aguardarSalvamento };
})();
