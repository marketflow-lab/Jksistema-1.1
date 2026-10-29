(function installJKMediasListasUniao(global) {
    'use strict';

    global.JKMedias.defineModule('listas-uniao', ['core', 'state', 'api', 'resultados', 'listas-cache', 'listas', 'editor-salvamento'], ({ modules }) => {
        const selecionadas = new Set();
        const endpoint = '/api/medias-compras/listas-pedidos/uniao';
        const preparatorios = ['Lista gerada', 'Em Orçamento', 'Analisando orçamento'];
        let dialogo = null;
        let identidadeSelecao = '';

        function listaPedidoUnida(lista = listaPedidoAtual) { return !!lista?.unida_em_lista_id; }
        function emEmbarqueLocal(id) {
            try {
                const embarques = JSON.parse(global.localStorage.getItem('jk_embarques_importacoes') || '[]');
                return Array.isArray(embarques) && embarques.some(embarque => Array.isArray(embarque.lista_ids) &&
                    embarque.lista_ids.some(listaId => String(listaId) === String(id)));
            } catch (_erro) { return false; }
        }
        function impedimento(lista) {
            if (emEmbarqueLocal(lista?.id)) return 'Lista vinculada a um embarque. Retire o vínculo antes de unir.';
            if (lista.pode_unir === false) return lista.motivo_bloqueio_uniao || 'Esta lista não pode ser unida.';
            return '';
        }
        function elegivel(lista) {
            return !!lista?.id && !listaPedidoUnida(lista) && !impedimento(lista) && preparatorios.includes(lista.status || 'Lista gerada');
        }
        function atualizarSelecaoUniao() {
            const identidade = modules.resultados.identidadeSessao();
            if (identidade !== identidadeSelecao) { selecionadas.clear(); identidadeSelecao = identidade; }
            const disponiveis = new Set((listasPedidosResumo || []).filter(elegivel).map(lista => String(lista.id)));
            for (const id of selecionadas) if (!disponiveis.has(id)) selecionadas.delete(id);
            const botao = document.getElementById('btnUnirListasPedido');
            if (botao) {
                botao.textContent = 'Unir listas' + (selecionadas.size ? ' (' + selecionadas.size + ')' : '');
                botao.disabled = selecionadas.size < 2;
            }
        }
        function avisoOrigem(lista) {
            const aviso = document.createElement('div');
            aviso.className = 'uniao-origem-aviso';
            const link = document.createElement('button');
            link.type = 'button';
            link.className = 'uniao-link';
            link.textContent = lista.unida_em_lista_nome || 'lista unificada';
            link.addEventListener('click', evento => {
                evento.stopPropagation();
                void abrirListaPedidoPorId(lista.unida_em_lista_id);
            });
            aviso.append('Unida à lista ', link, '. Disponível para consulta.');
            return aviso;
        }
        function decorarListaParaUniao(div, lista) {
            if (lista.uniao_origens?.length) {
                const excluir = div.querySelector('[data-action="excluir"]');
                if (excluir) {
                    excluir.disabled = true;
                    excluir.title = 'Esta lista possui origens vinculadas. Cancele o pedido para preservar o histórico.';
                }
            }
            if (listaPedidoUnida(lista)) {
                div.classList.add('lista-unida');
                div.append(avisoOrigem(lista));
                div.querySelectorAll('[data-action="anexar"], [data-action="excluir"]').forEach(botao => {
                    botao.disabled = true;
                    botao.title = 'Lista unida: disponível para consulta.';
                });
                return;
            }
            const label = document.createElement('label');
            label.className = 'uniao-selecao';
            const input = document.createElement('input');
            input.type = 'checkbox';
            input.className = 'uniao-lista-checkbox';
            input.dataset.listaId = String(lista.id);
            input.checked = selecionadas.has(String(lista.id));
            input.disabled = !elegivel(lista);
            label.title = impedimento(lista) || (input.disabled ? 'Somente listas em preparação podem ser unidas.' : '');
            input.setAttribute('aria-label', 'Selecionar para unir: ' + (lista.nome_lista || 'Sem nome'));
            input.addEventListener('change', () => {
                if (input.checked) selecionadas.add(String(lista.id));
                else selecionadas.delete(String(lista.id));
                atualizarSelecaoUniao();
            });
            label.addEventListener('click', evento => evento.stopPropagation());
            label.append(input, 'Selecionar para unir');
            div.prepend(label);
        }
        function aplicarLeituraListaUnida() {
            const painel = document.getElementById('editorListaPedido');
            if (!painel) return;
            painel.querySelector('.uniao-origem-aviso')?.remove();
            painel.querySelectorAll('[data-uniao-bloqueado]').forEach(elemento => {
                elemento.disabled = elemento.dataset.uniaoBloqueado === 'true';
                delete elemento.dataset.uniaoBloqueado;
            });
            if (!listaPedidoUnida()) return;
            painel.prepend(avisoOrigem(listaPedidoAtual));
            painel.querySelectorAll('input, select, button:not(.uniao-link)').forEach(elemento => {
                elemento.dataset.uniaoBloqueado = String(elemento.disabled);
                elemento.disabled = true;
            });
            painel.querySelectorAll('tr[draggable]').forEach(linha => { linha.draggable = false; });
            invalidarContextoEdicaoListaPedido();
        }
        function vigente(registro) {
            return dialogo === registro && registro.sessao === modules.resultados.identidadeSessao();
        }
        function fecharUniaoListas() {
            if (!dialogo || dialogo.confirmando) return;
            const registro = dialogo;
            dialogo = null;
            registro.controller?.abort();
            registro.overlay.remove();
            document.removeEventListener('keydown', registro.teclado, true);
            if (registro.focoAnterior?.isConnected) registro.focoAnterior.focus();
        }
        function status(registro, mensagem) { registro.status.textContent = mensagem || ''; }
        function atualizarConfirmacao(registro) {
            const conflitos = registro.previa?.conflitos || [];
            registro.confirmar.disabled = registro.carregando || registro.confirmando || !registro.previa ||
                !registro.nome.value.trim() || conflitos.some(conflito => !registro.escolhas[conflito.id]);
        }
        function ocupado(registro, ativo) {
            registro.overlay.querySelectorAll('input, select, button').forEach(elemento => { elemento.disabled = ativo; });
            if (registro.payloadConfirmacao) {
                registro.nome.disabled = true;
                registro.conflitos.querySelectorAll('select').forEach(elemento => { elemento.disabled = true; });
                registro.overlay.querySelector('[data-uniao="atualizar"]').disabled = true;
            }
            atualizarConfirmacao(registro);
        }
        function erroTexto(retorno) {
            return typeof retorno?.detail === 'string' ? retorno.detail : retorno?.detail?.message || 'Não foi possível concluir a união. Tente novamente.';
        }
        async function enviar(registro, rota, payload, signal) {
            modules.resultados.exigirSessao(registro.sessao);
            const controller = new AbortController();
            let rejeitar, tempo;
            const limite = new Promise((_resolve, reject) => { rejeitar = reject; });
            const cancelar = () => { controller.abort(); rejeitar(new Error('Solicitação cancelada.')); };
            signal?.addEventListener('abort', cancelar, { once: true });
            tempo = global.setTimeout(() => {
                controller.abort();
                rejeitar(new Error('O servidor não confirmou a operação em 30 segundos.'));
            }, 30000);
            const executar = async () => {
                const resposta = await modules.api.request(rota, {
                    method: 'POST', headers: registro.headers, body: JSON.stringify(payload), signal: controller.signal,
                });
                const retorno = await resposta.json();
                modules.resultados.exigirSessao(registro.sessao);
                if (!resposta.ok) {
                    const erro = new Error(erroTexto(retorno));
                    erro.status = resposta.status;
                    throw erro;
                }
                return retorno;
            };
            try { return await Promise.race([executar(), limite]); }
            finally { global.clearTimeout(tempo); signal?.removeEventListener('abort', cancelar); }
        }
        function valorOpcao(valor) {
            if (valor === null || valor === undefined || valor === '') return '(não preenchido)';
            return typeof valor === 'number' ? valor.toLocaleString('pt-BR', { maximumFractionDigits: 12 }) : String(valor);
        }
        function renderResultado(registro) {
            const previa = registro.previa;
            const linhas = (previa.itens || []).map(item => ({ ...item, campos: { ...item.campos } }));
            for (const conflito of previa.conflitos || []) {
                const opcao = conflito.opcoes.find(item => item.id === registro.escolhas[conflito.id]);
                const item = linhas.find(linha => linha.sku === conflito.sku);
                if (opcao && item) {
                    item.campos[conflito.campo] = opcao.valor;
                    if (conflito.campo === 'Valor unidade') item.valor_unitario = opcao.valor;
                    if (conflito.campo === 'Título do produto em inglês') item.descricao = opcao.valor;
                }
            }
            registro.resumo.textContent = previa.total_listas + ' listas · ' + previa.total_skus + (previa.total_skus === 1 ? ' SKU · ' : ' SKUs · ') +
                previa.skus_repetidos + (previa.skus_repetidos === 1 ? ' SKU com quantidade somada · ' : ' SKUs com quantidades somadas · ') + numero(previa.total_quantidade) + ' unidades';
            registro.tabela.innerHTML = linhas.map(item => '<tr><td>' + escaparHtml(item.sku) + '</td><td>' +
                escaparHtml(item.descricao || '') + '</td><td class="num">' + numero(item.quantidade) + '</td><td class="num">' +
                escaparHtml(valorOpcao(item.valor_unitario)) + '</td><td class="num">' +
                numero(Number(item.quantidade || 0) * Number(item.valor_unitario || 0)) + '</td></tr>').join('');
            const pendentes = (previa.conflitos || []).filter(item => !registro.escolhas[item.id]).length;
            registro.total.textContent = 'Total (' + (previa.currency || 'USD') + '): ' + numero(linhas.reduce((total, item) =>
                total + Number(item.quantidade || 0) * Number(item.valor_unitario || 0), 0)) + (pendentes ? ' · Revise ' + pendentes + ' diferença(s) abaixo.' : '');
            atualizarConfirmacao(registro);
        }
        function renderPrevia(registro) {
            const previa = registro.previa;
            registro.conflitos.replaceChildren();
            registro.origens.textContent = (previa.listas || []).map(lista => lista.nome_lista || lista.id).join(' + ') +
                ' · ' + (previa.loja || '') + ' · ' + (previa.supplier || '') + ' · ' + (previa.currency || 'USD');
            for (const conflito of previa.conflitos || []) {
                const label = document.createElement('label');
                label.className = 'uniao-conflito';
                const titulo = document.createElement('span');
                titulo.textContent = conflito.sku + ' — ' + (conflito.rotulo || conflito.campo);
                const select = document.createElement('select');
                select.dataset.conflitoId = conflito.id;
                select.setAttribute('aria-label', titulo.textContent);
                select.add(new Option('Escolha a informação que será usada', ''));
                for (const opcao of conflito.opcoes) select.add(new Option(valorOpcao(opcao.valor) + ' — ' +
                    opcao.listas.map(lista => lista.nome_lista || lista.id).join(', '), opcao.id));
                select.addEventListener('change', () => { registro.escolhas[conflito.id] = select.value; renderResultado(registro); });
                label.append(titulo, select);
                registro.conflitos.append(label);
            }
            renderResultado(registro);
        }
        async function carregarPreviaUniao(registro) {
            if (!vigente(registro) || registro.carregando || registro.confirmando) return;
            registro.carregando = true;
            registro.previa = null;
            registro.escolhas = {};
            registro.controller = new AbortController();
            registro.tabela.replaceChildren();
            registro.conflitos.replaceChildren();
            registro.total.textContent = '';
            status(registro, 'Conferindo listas e somando quantidades...');
            ocupado(registro, true);
            registro.cancelar.disabled = false;
            try {
                const edicao = registro.ids.includes(String(listaPedidoAtual?.id)) && !listaPedidoUnida() ? obterContextoEdicaoListaPedido() : null;
                if (edicao?.pendentes.size && !await confirmarEdicaoListaPedido(null, { integral: true })) {
                    throw new Error('Salve ou corrija as alterações da lista aberta antes de unir.');
                }
                await Promise.all(registro.ids.map(id => aguardarSalvamentoListaPedido(id)));
                if (edicao?.ultimo?.falhou) throw new Error('Não foi possível salvar a lista aberta. Salve as alterações antes de unir.');
                if (!vigente(registro)) return;
                const retorno = await enviar(registro, endpoint + '/previa', { lista_ids: registro.ids }, registro.controller.signal);
                if (!vigente(registro)) return;
                if (!retorno.previa?.token || !Array.isArray(retorno.previa.itens)) throw new Error('A prévia recebida está incompleta. Tente novamente.');
                registro.previa = retorno.previa;
                registro.operacao = global.crypto?.randomUUID?.() || Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
                renderPrevia(registro);
                status(registro, retorno.previa.conflitos?.length ? 'Escolha qual informação usar em cada diferença. As quantidades serão somadas.' : 'Prévia pronta. Confira os produtos e o nome da nova lista.');
            } catch (erro) {
                if (vigente(registro)) status(registro, erro.message || 'Não foi possível carregar a prévia.');
                else if (dialogo === registro) fecharUniaoListas();
            } finally {
                registro.carregando = false;
                if (vigente(registro)) ocupado(registro, false);
            }
        }
        async function confirmarUniaoListas(registro) {
            if (!vigente(registro)) { if (dialogo === registro) fecharUniaoListas(); return; }
            if (registro.confirmar.disabled) return;
            if (!registro.payloadConfirmacao && registro.ids.some(emEmbarqueLocal)) {
                registro.previa = null;
                status(registro, 'Lista vinculada a um embarque. Retire o vínculo antes de unir e atualize a prévia.');
                atualizarConfirmacao(registro);
                return;
            }
            registro.payloadConfirmacao ||= JSON.parse(JSON.stringify({
                lista_ids: registro.ids, nome_lista: registro.nome.value.trim(), previa_token: registro.previa.token,
                operacao_id: registro.operacao, escolhas: registro.escolhas,
            }));
            registro.confirmando = true;
            ocupado(registro, true);
            status(registro, 'Criando lista unificada...');
            try {
                const cache = modules['listas-cache'].cacheListasPedidos;
                const retorno = await enviar(registro, endpoint, registro.payloadConfirmacao);
                if (!vigente(registro)) return;
                if (!retorno.lista?.id) throw new Error('A resposta não contém a lista unificada. Tente novamente.');
                await cache.invalidar(cache.capturar(['uniao']));
                if (!vigente(registro)) return;
                cancelarAberturaListaPedido();
                invalidarContextoEdicaoListaPedido();
                listasPedidosResumo = listasPedidosResumo.map(lista => registro.ids.includes(String(lista.id))
                    ? { ...lista, unida_em_lista_id: retorno.lista.id, unida_em_lista_nome: retorno.lista.nome_lista } : lista);
                if (!listasPedidosResumo.some(lista => lista.id === retorno.lista.id)) listasPedidosResumo.unshift(retorno.lista);
                listaPedidoAtual = retorno.lista;
                selecionadas.clear();
                registro.confirmando = false;
                fecharUniaoListas();
                renderListaPedidosResumo();
                renderEditorListaPedido();
                await carregarListasPedidos(true);
                setStatusListaPedido('Lista unificada criada. As listas de origem estão disponíveis para consulta.');
            } catch (erro) {
                if (vigente(registro)) {
                    if (erro.status >= 400 && erro.status < 500) { registro.previa = null; registro.payloadConfirmacao = null; }
                    status(registro, (erro.message || 'Não foi possível concluir a união.') +
                        (!registro.previa ? ' Atualize a prévia para revisar as listas novamente.' : ' Tente novamente para confirmar a mesma operação.'));
                }
            } finally {
                registro.confirmando = false;
                if (vigente(registro)) ocupado(registro, false);
                else if (dialogo === registro) fecharUniaoListas();
            }
        }
        function abrirUniaoListas() {
            atualizarSelecaoUniao();
            if (dialogo || selecionadas.size < 2) return;
            const overlay = document.createElement('div');
            overlay.id = 'modalUniaoListas';
            overlay.className = 'modal-backdrop open';
            overlay.innerHTML = '<section class="modal-card uniao-modal" role="dialog" aria-modal="true" aria-labelledby="tituloUniaoListas">' +
                '<h2 id="tituloUniaoListas" class="modal-title">Unir listas de pedidos</h2>' +
                '<p class="modal-subtitle">Uma nova lista será criada. As listas de origem ficarão disponíveis para consulta.</p>' +
                '<p class="modal-subtitle">Fotos, cubagem e dados fiscais seguem o cadastro da loja.</p>' +
                '<div class="campo-lista"><label for="nomeUniaoListas">Nome da nova lista</label>' +
                '<input id="nomeUniaoListas" class="input-lista" maxlength="80" value="Lista unificada"></div>' +
                '<p class="uniao-origens"></p><p class="uniao-resumo" aria-live="polite"></p>' +
                '<div class="uniao-tabela-wrap"><table class="mini-table"><thead><tr><th>SKU</th><th>Produto</th><th>Quantidade</th><th>Valor unitário</th><th>Total</th></tr></thead><tbody></tbody></table></div>' +
                '<p class="uniao-total" aria-live="polite"></p><div class="uniao-conflitos"></div>' +
                '<div class="modal-status" role="status" aria-live="polite"></div><div class="modal-actions">' +
                '<button type="button" class="btn-secondary" data-uniao="atualizar">Atualizar prévia</button>' +
                '<button type="button" class="btn-secondary" data-uniao="cancelar">Cancelar</button>' +
                '<button type="button" class="btn-primary" data-uniao="confirmar" disabled>Criar lista unificada</button></div></section>';
            const registro = { overlay, ids: [...selecionadas], sessao: modules.resultados.identidadeSessao(),
                headers: { ...modules.api.authHeaders(), 'Content-Type': 'application/json' }, focoAnterior: document.activeElement,
                nome: overlay.querySelector('#nomeUniaoListas'), status: overlay.querySelector('[role="status"]'),
                resumo: overlay.querySelector('.uniao-resumo'), origens: overlay.querySelector('.uniao-origens'),
                tabela: overlay.querySelector('tbody'), total: overlay.querySelector('.uniao-total'), conflitos: overlay.querySelector('.uniao-conflitos'),
                cancelar: overlay.querySelector('[data-uniao="cancelar"]'), confirmar: overlay.querySelector('[data-uniao="confirmar"]') };
            registro.teclado = evento => {
                if (evento.key === 'Escape') { evento.preventDefault(); evento.stopPropagation(); fecharUniaoListas(); }
                if (evento.key !== 'Tab') return;
                const focaveis = [...overlay.querySelectorAll('input:not(:disabled), select:not(:disabled), button:not(:disabled)')];
                const primeiro = focaveis[0], ultimo = focaveis[focaveis.length - 1];
                if (evento.shiftKey && document.activeElement === primeiro) { evento.preventDefault(); ultimo?.focus(); }
                else if (!evento.shiftKey && document.activeElement === ultimo) { evento.preventDefault(); primeiro?.focus(); }
            };
            dialogo = registro;
            document.body.append(overlay);
            document.addEventListener('keydown', registro.teclado, true);
            registro.cancelar.addEventListener('click', fecharUniaoListas);
            registro.confirmar.addEventListener('click', () => void confirmarUniaoListas(registro));
            registro.nome.addEventListener('input', () => atualizarConfirmacao(registro));
            overlay.querySelector('[data-uniao="atualizar"]').addEventListener('click', () => void carregarPreviaUniao(registro));
            void carregarPreviaUniao(registro);
            registro.cancelar.focus();
        }
        function iniciarUniaoListas() {
            document.getElementById('btnUnirListasPedido')?.addEventListener('click', abrirUniaoListas);
            global.addEventListener('pagehide', () => {
                selecionadas.clear();
                if (dialogo) { dialogo.confirmando = false; fecharUniaoListas(); }
            });
        }
        return { listaPedidoUnida, atualizarSelecaoUniao, decorarListaParaUniao, aplicarLeituraListaUnida, abrirUniaoListas, fecharUniaoListas, iniciarUniaoListas };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
