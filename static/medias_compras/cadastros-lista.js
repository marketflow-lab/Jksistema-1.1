(function () {
    'use strict';

    const tipos = {
        fornecedor: { rotulo: 'Fornecedor', campo: 'fornecedor_id', nome: 'supplier', plural: 'fornecedores', opcional: false },
        nacionalizador: { rotulo: 'Nacionalizador', campo: 'nacionalizador_id', nome: 'nacionalizador', plural: 'nacionalizadores', opcional: true }
    };
    const consultas = new Map();
    const salvando = new Set();
    const controles = new WeakMap();
    let sequencia = 0;

    const escapar = valor => String(valor || '').replace(/[&<>"']/g, caractere =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[caractere]);
    const chave = (tipo, id) => tipo + ':' + id;
    const nome = (tipo, lista) => String(lista[tipos[tipo].nome] || '').trim();

    function html(tipo, lista) {
        const config = tipos[tipo];
        const texto = nome(tipo, lista) || 'Não informado';
        return '<span class="meta-pill meta-cadastro-lista meta-' + tipo + ' meta-loja-editavel" data-' + tipo +
            '-pill data-lista-cadastro-id="' + escapar(lista.id) + '" tabindex="0" title="' + config.rotulo + ': ' +
            escapar(texto) + '. Clique duas vezes para incluir ou alterar.">' + config.rotulo + ': <strong>' + escapar(texto) + '</strong></span>';
    }

    function atualizar(tipo, id) {
        document.querySelectorAll('[data-' + tipo + '-pill]').forEach(pill => {
            const contexto = controles.get(pill);
            if (!contexto || contexto.id !== id) return;
            const ocupado = salvando.has(chave(tipo, id));
            pill.setAttribute('aria-busy', String(ocupado));
            pill.setAttribute('aria-disabled', String(ocupado));
            const input = pill.querySelector('input');
            if (input) {
                input.disabled = ocupado;
                return;
            }
            const texto = nome(tipo, contexto.atual()) || 'Não informado';
            pill.innerHTML = tipos[tipo].rotulo + ': <strong>' + escapar(texto) + '</strong>';
            pill.title = tipos[tipo].rotulo + ': ' + texto + '. Clique duas vezes para incluir ou alterar.';
        });
    }

    function consultar(tipo, contexto) {
        if (!consultas.has(tipo)) {
            const consulta = (async () => {
                const resposta = await fetch('/api/cadastro/' + tipos[tipo].plural, { headers: contexto.headers() });
                const dados = await resposta.json();
                if (!resposta.ok) throw new Error(dados.detail || 'Não foi possível carregar os ' + tipos[tipo].plural + '.');
                if (!Array.isArray(dados)) throw new Error('Resposta inválida do cadastro.');
                return dados.filter(item => item && item.id && String(item.nome_empresa || '').trim());
            })();
            consultas.set(tipo, consulta);
            const limpar = () => { if (consultas.get(tipo) === consulta) consultas.delete(tipo); };
            consulta.then(limpar, limpar);
        }
        return consultas.get(tipo);
    }

    function iniciar(pill, contexto) {
        const { tipo, id } = contexto;
        const config = tipos[tipo];
        if (pill.querySelector('input') || salvando.has(chave(tipo, id))) return;
        const original = contexto.atual();
        const valorOriginal = nome(tipo, original);
        const input = document.createElement('input');
        const datalist = document.createElement('datalist');
        const datalistId = 'cadastroLista_' + (++sequencia);
        let encerrado = false;
        let carregado = false;
        let alterado = false;
        let salvarDepois = false;
        let opcoes = [];
        input.type = 'text';
        input.className = 'meta-loja-input meta-cadastro-input';
        input.setAttribute('data-' + tipo + '-lista-id', id);
        input.setAttribute('aria-label', config.rotulo + ' da lista ' + (original.nome_lista || 'Sem nome'));
        input.setAttribute('list', datalistId);
        input.value = valorOriginal;
        input.placeholder = 'Carregando cadastro...';
        input.autocomplete = 'off';
        datalist.id = datalistId;
        pill.innerHTML = config.rotulo + ': ';
        pill.append(input, datalist);

        const fechar = (recuperarFoco = false) => {
            if (encerrado) return;
            encerrado = true;
            pill.replaceChildren();
            atualizar(tipo, id);
            if (recuperarFoco && pill.isConnected) pill.focus();
        };
        const erro = mensagem => {
            input.setAttribute('aria-invalid', 'true');
            input.title = mensagem;
            contexto.status(mensagem);
            if (input.isConnected) input.focus();
        };
        const salvar = async () => {
            if (encerrado || !pill.isConnected || salvando.has(chave(tipo, id))) return;
            const valor = input.value.trim();
            if (valor === valorOriginal && !alterado) { fechar(document.activeElement === input); return; }
            if (!carregado && !(config.opcional && !valor)) {
                salvarDepois = true;
                contexto.status('Aguarde o carregamento do cadastro.');
                return;
            }
            const escolhido = opcoes.find(item => item.valor === valor);
            if (!escolhido && !(config.opcional && !valor)) {
                erro('Selecione um ' + tipo + ' cadastrado.');
                return;
            }
            const novoId = escolhido ? String(escolhido.registro.id) : '';
            const atual = contexto.atual();
            const novoNome = escolhido ? String(escolhido.registro.nome_empresa).trim() : '';
            if (novoId === String(atual[config.campo] || '') && novoNome === nome(tipo, atual)) {
                fechar(document.activeElement === input);
                return;
            }
            const recuperarFoco = document.activeElement === input;
            salvando.add(chave(tipo, id));
            input.removeAttribute('aria-invalid');
            input.removeAttribute('title');
            atualizar(tipo, id);
            contexto.status('Salvando ' + tipo + ' da lista...');
            let sucesso = false;
            try {
                const resposta = await fetch('/api/medias-compras/listas-pedidos/' + encodeURIComponent(id), {
                    method: 'PUT', headers: { ...contexto.headers(), 'Content-Type': 'application/json' },
                    body: JSON.stringify({ [config.campo]: novoId })
                });
                const dados = await resposta.json();
                if (!resposta.ok) throw new Error(dados.detail || 'Não foi possível salvar o ' + tipo + '.');
                contexto.salvo(dados.lista || { [config.campo]: novoId, [config.nome]: escolhido ? escolhido.registro.nome_empresa : '' });
                sucesso = true;
                contexto.status(config.rotulo + ' da lista atualizado com sucesso.');
            } catch (falha) {
                erro(falha.message || 'Não foi possível salvar o ' + tipo + '.');
            } finally {
                salvando.delete(chave(tipo, id));
                atualizar(tipo, id);
                if (sucesso) fechar(recuperarFoco && (document.activeElement === input || document.activeElement === document.body));
                else if (recuperarFoco && input.isConnected) input.focus();
            }
        };
        input.addEventListener('input', () => {
            alterado = true;
            salvarDepois = false;
            input.removeAttribute('aria-invalid');
            input.removeAttribute('title');
        });
        input.addEventListener('change', () => { alterado = true; });
        input.addEventListener('keydown', evento => {
            evento.stopPropagation();
            if (evento.key === 'Enter') { evento.preventDefault(); salvar(); }
            if (evento.key === 'Escape') {
                evento.preventDefault();
                if (!salvando.has(chave(tipo, id))) fechar(true);
            }
        });
        input.addEventListener('blur', () => { if (!encerrado) salvar(); });
        consultar(tipo, contexto).then(registros => {
            if (encerrado || !input.isConnected) return;
            const nomes = registros.map(registro => String(registro.nome_empresa).trim());
            opcoes = registros.map(registro => {
                const texto = String(registro.nome_empresa).trim();
                const valor = nomes.filter(item => item === texto).length > 1 ? texto + ' (' + registro.id + ')' : texto;
                const option = document.createElement('option');
                option.value = valor;
                datalist.appendChild(option);
                return { valor, registro };
            });
            carregado = true;
            input.dataset.cadastroCarregado = '1';
            input.placeholder = config.opcional ? 'Não informado' : 'Selecione um fornecedor';
            if (!registros.length) {
                input.title = 'Cadastre um ' + tipo + ' em Cadastro > ' + config.rotulo + ' para vincular esta lista.';
                contexto.status(input.title);
            }
            if (salvarDepois) { salvarDepois = false; salvar(); }
        }).catch(falha => {
            if (!encerrado && input.isConnected) {
                salvarDepois = false;
                erro((falha.message || 'Cadastro indisponível.') + ' Feche com Esc e clique duas vezes para tentar novamente.');
            }
        });
        input.focus();
        input.select();
    }

    function preparar(pill, configuracao) {
        if (!pill || !configuracao.id) return;
        const contexto = { ...configuracao, id: String(configuracao.id) };
        controles.set(pill, contexto);
        const ocupado = salvando.has(chave(contexto.tipo, contexto.id));
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
            if (evento.target === pill && ['Enter', ' '].includes(evento.key)) evento.preventDefault();
        });
        atualizar(contexto.tipo, contexto.id);
    }

    window.JKCadastrosLista = { html, preparar };
})();
