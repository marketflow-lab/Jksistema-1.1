(function installJKAtualizacaoArquivo(global) {
    'use strict';

    let operacaoAtiva = false;
    const registros = valor => Array.isArray(valor) ? valor : [];
    const nomesCampos = { status: 'Status', compra_aprovada: 'Compra aprovada', quantidade: 'Quantidade',
        valor_unitario: 'Valor unidade', valor_total: 'Valor total', nome_lista: 'Nome da lista' };

    function elemento(tag, texto, classe) {
        const node = document.createElement(tag);
        if (texto !== undefined) node.textContent = String(texto);
        if (classe) node.className = classe;
        return node;
    }

    function formatar(valor) {
        if (valor === null || valor === undefined || valor === '') return 'Em branco';
        if (typeof valor === 'boolean') return valor ? 'Sim' : 'Não';
        if (typeof valor === 'number') return valor.toLocaleString('pt-BR', { maximumFractionDigits: 12 });
        return typeof valor === 'object' ? JSON.stringify(valor) : String(valor);
    }

    function identidade(headers) {
        return JSON.stringify([Object.keys(headers).sort().map(chave => [chave.toLowerCase(), String(headers[chave])]),
            ...['access_token', 'user_data', 'permissions'].map(chave => global.localStorage?.getItem(chave) || '')]);
    }

    function selecionarArquivo() {
        return new Promise(resolve => {
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = '.xlsx,.xlsm,.xltx,.xltm';
            input.hidden = true;
            document.body.appendChild(input);
            const concluir = arquivo => { input.remove(); resolve(arquivo); };
            input.addEventListener('change', () => concluir(input.files?.[0] || null), { once: true });
            input.addEventListener('cancel', () => concluir(null), { once: true });
            input.click();
        });
    }

    function dialogo(id, titulo, descricao, vigente) {
        const focoAnterior = document.activeElement;
        const node = elemento('dialog', undefined, 'jk-arquivo-dialogo');
        node.id = id;
        node.setAttribute('aria-labelledby', id + 'Titulo');
        node.setAttribute('aria-describedby', id + 'Descricao');
        const cabecalho = elemento('header');
        const heading = elemento('h2', titulo);
        heading.id = id + 'Titulo';
        const introducao = elemento('p', descricao);
        introducao.id = id + 'Descricao';
        cabecalho.append(heading, introducao);
        const corpo = elemento('div', undefined, 'jk-arquivo-corpo');
        corpo.tabIndex = 0;
        const acoes = elemento('footer', undefined, 'jk-arquivo-acoes');
        node.append(cabecalho, corpo, acoes);
        document.body.appendChild(node);
        let concluir;
        let fechado = false;
        const promessa = new Promise(resolve => { concluir = resolve; });
        const fechar = resultado => {
            if (fechado) return;
            fechado = true;
            clearInterval(verificacao);
            node.close();
            node.remove();
            if (vigente() && focoAnterior?.isConnected) focoAnterior.focus();
            concluir(resultado);
        };
        const verificacao = setInterval(() => { if (!vigente()) fechar(null); }, 250);
        node.addEventListener('cancel', event => { event.preventDefault(); fechar(null); });
        function botao(idBotao, texto, resultado, destaque = false) {
            const button = elemento('button', texto, destaque ? 'jk-arquivo-primario' : '');
            button.id = idBotao;
            button.type = 'button';
            button.addEventListener('click', () => fechar(vigente() ? resultado : null));
            acoes.appendChild(button);
            return button;
        }
        return { node, corpo, promessa, botao, abrir: () => node.showModal() };
    }

    function secao(corpo, titulo) {
        const section = elemento('section', undefined, 'jk-arquivo-secao');
        section.appendChild(elemento('h3', titulo));
        corpo.appendChild(section);
        return section;
    }

    function diferencas(pai, campos) {
        const lista = elemento('ul', undefined, 'jk-arquivo-diferencas');
        registros(campos).forEach(campo => {
            const li = elemento('li');
            li.append(elemento('strong', (nomesCampos[campo.campo] || campo.campo || 'Campo') + ': '),
                elemento('span', formatar(campo.antes) + ' → ' + formatar(campo.depois)));
            lista.appendChild(li);
        });
        pai.appendChild(lista);
    }

    async function confirmar(previa, nomeLista, vigente) {
        const pendencias = registros(previa.pendencias);
        const ausentes = pendencias.filter(p => p.tipo === 'sku_ausente');
        const modal = dialogo('jkAtualizacaoArquivoConfirmacao', 'Confirmar atualização da lista',
            'Lista: ' + nomeLista + '. Confira os itens antes de aplicar o arquivo.', vigente);
        if (ausentes.length) {
            const bloco = secao(modal.corpo, 'SKUs ausentes do arquivo (' + ausentes.length + ')');
            bloco.appendChild(elemento('p', 'Excluir estes itens deixará os SKUs da lista iguais aos do arquivo. Você também pode mantê-los.'));
            const lista = elemento('ul');
            ausentes.forEach(p => lista.appendChild(elemento('li', 'SKU ' + p.sku)));
            bloco.appendChild(lista);
        }
        const inclusoes = pendencias.filter(p => p.tipo === 'sku_novo');
        if (inclusoes.length) {
            const bloco = secao(modal.corpo, 'SKUs a incluir (' + inclusoes.length + ')');
            const lista = elemento('ul');
            inclusoes.forEach(p => lista.appendChild(elemento('li', 'SKU ' + p.sku + (p.titulo_arquivo ? ' — ' + p.titulo_arquivo : ''))));
            bloco.appendChild(lista);
        }
        const titulos = pendencias.filter(p => p.tipo === 'titulo_diferente');
        if (titulos.length) {
            const bloco = secao(modal.corpo, 'Títulos diferentes (' + titulos.length + ')');
            titulos.forEach(p => {
                const item = elemento('div', undefined, 'jk-arquivo-item');
                item.appendChild(elemento('strong', 'SKU ' + p.sku));
                diferencas(item, [{ campo: 'Título', antes: p.titulo_atual, depois: p.titulo_arquivo }]);
                bloco.appendChild(item);
            });
        }
        modal.botao('jkAtualizacaoArquivoCancelar', 'Cancelar', null);
        if (ausentes.length) {
            modal.botao('jkAtualizacaoArquivoManter', 'Manter e atualizar', { excluir: false });
            modal.botao('jkAtualizacaoArquivoExcluir', 'Excluir e atualizar', { excluir: true }, true);
        } else {
            modal.botao('jkAtualizacaoArquivoConfirmar', 'Confirmar atualização', { excluir: false }, true);
        }
        modal.abrir();
        return modal.promessa;
    }

    function itensRelatorio(corpo, titulo, itens) {
        if (!itens.length) return;
        const bloco = secao(corpo, titulo + ' (' + itens.length + ')');
        itens.forEach(registro => {
            const item = elemento('div', undefined, 'jk-arquivo-item');
            item.appendChild(elemento('strong', 'SKU ' + registro.sku));
            if (registro.campos) diferencas(item, registro.campos);
            else {
                const campos = elemento('dl', undefined, 'jk-arquivo-valores');
                Object.entries(registro.item || {}).filter(([campo]) => !['SKU', 'valor_total_informado', 'valor_unitario_informado'].includes(campo)).forEach(([campo, valor]) => {
                    campos.append(elemento('dt', nomesCampos[campo] || campo), elemento('dd', formatar(valor)));
                });
                item.appendChild(campos);
            }
            bloco.appendChild(item);
        });
    }

    function mostrarRelatorio(data, nomeLista, vigente) {
        const relatorio = data.relatorio || {};
        const atualizados = registros(relatorio.atualizados);
        const incluidos = registros(relatorio.incluidos);
        const excluidos = registros(relatorio.excluidos);
        const mantidos = registros(relatorio.mantidos);
        const modal = dialogo('jkAtualizacaoArquivoRelatorio', 'Atualização concluída', 'Lista: ' + nomeLista +
            '. ' + atualizados.length + ' SKU(s) alterado(s), ' + incluidos.length + ' incluído(s), ' +
            excluidos.length + ' excluído(s) e ' + mantidos.length + ' mantido(s) fora do arquivo.', vigente);
        const aviso = elemento('p', '', 'jk-arquivo-aviso');
        aviso.id = 'jkAtualizacaoArquivoAviso';
        aviso.setAttribute('role', 'status');
        aviso.setAttribute('aria-live', 'polite');
        modal.corpo.appendChild(aviso);
        itensRelatorio(modal.corpo, 'Itens alterados: antes → depois', atualizados);
        itensRelatorio(modal.corpo, 'Itens incluídos', incluidos);
        itensRelatorio(modal.corpo, 'Itens excluídos', excluidos);
        itensRelatorio(modal.corpo, 'Itens mantidos fora do arquivo', mantidos);
        if (registros(relatorio.lista).length) diferencas(secao(modal.corpo, 'Alterações na lista'), relatorio.lista);
        if (registros(relatorio.inalterados).length) {
            secao(modal.corpo, 'SKUs sem alteração (' + relatorio.inalterados.length + ')')
                .appendChild(elemento('p', relatorio.inalterados.join(', ')));
        }
        if (!atualizados.length && !incluidos.length && !excluidos.length && !registros(relatorio.lista).length) {
            modal.corpo.appendChild(elemento('p', 'Nenhuma alteração foi necessária nos itens ou no status da lista.'));
        }
        modal.botao('jkAtualizacaoArquivoFechar', 'Fechar', true, true);
        modal.abrir();
        return { ...modal, aviso };
    }

    async function atualizar(config) {
        if (operacaoAtiva || !config.listaId) return;
        operacaoAtiva = true;
        const headersAtuais = () => typeof config.headers === 'function' ? config.headers() : (config.headers || {});
        const headers = { ...headersAtuais() };
        const sessao = identidade(headers);
        const vigente = () => sessao === identidade(headersAtuais()) && (!config.vigente || config.vigente());
        const status = (mensagem, erro = false) => { if (vigente()) config.status?.(mensagem, erro); };
        const nomeLista = String(config.nomeLista || 'Sem nome');
        const enviar = async (arquivo, previa, token, excluir) => {
            if (!vigente()) return null;
            const form = new FormData();
            form.append('file', arquivo);
            form.append('previsualizar', previa ? '1' : '0');
            form.append('sincronizar_arquivo', config.sincronizar ? '1' : '0');
            form.append('confirmar_inclusoes', previa ? '0' : '1');
            if (!previa) {
                form.append('confirmar_exclusoes', excluir ? '1' : '0');
                form.append('token_confirmacao', token);
            }
            const resp = await (config.fetch || global.fetch)('/api/medias-compras/listas-pedidos/' +
                encodeURIComponent(String(config.listaId)) + '/importar-excel-precos', { method: 'POST', headers, body: form });
            const data = await resp.json().catch(() => ({}));
            if (!vigente()) return null;
            if (!resp.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Não foi possível atualizar a lista pelo arquivo.');
            return data;
        };
        try {
            const arquivo = await selecionarArquivo();
            if (!arquivo || !vigente()) return;
            await config.preparar?.();
            if (!vigente()) return;
            status('Conferindo o arquivo para atualizar a lista "' + nomeLista + '"...');
            const previa = await enviar(arquivo, true);
            if (!previa || !vigente()) return;
            if (previa.previsualizacao !== true || typeof previa.token_confirmacao !== 'string' || !previa.token_confirmacao) {
                throw new Error('O servidor não confirmou a conferência do arquivo. Atualize a tela e tente novamente.');
            }
            let escolha = { excluir: false };
            if (previa.requer_confirmacao || registros(previa.pendencias).length) escolha = await confirmar(previa, nomeLista, vigente);
            if (!escolha || !vigente()) { status('Atualização cancelada. A lista foi preservada.'); return; }
            status('Aplicando a atualização da lista...');
            const data = await enviar(arquivo, false, previa.token_confirmacao, escolha.excluir);
            if (!data || !vigente()) return;
            if (data.success !== true) throw new Error(data.mensagem || 'O servidor não confirmou a atualização. Confira o arquivo novamente.');
            status('Atualização concluída. Consulte o relatório das alterações.');
            const relatorio = mostrarRelatorio(data, nomeLista, vigente);
            try {
                await config.recarregar?.(data);
                status('Atualização concluída. Consulte o relatório das alterações.');
            } catch (_erro) {
                const mensagem = 'A atualização foi salva. Não foi possível recarregar a tela; atualize a lista para ver os dados atuais.';
                if (vigente()) relatorio.aviso.textContent = mensagem;
                status(mensagem);
            }
            await relatorio.promessa;
            return data;
        } catch (erro) {
            status(erro.message || 'Erro ao atualizar a lista pelo arquivo.', true);
        } finally {
            operacaoAtiva = false;
        }
    }

    global.JKAtualizacaoArquivo = { atualizar };
})(typeof globalThis !== 'undefined' ? globalThis : this);
