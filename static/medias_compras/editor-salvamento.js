(function installJKMediasEditorSalvamento(global) {
    'use strict';

    global.JKMedias.defineModule('editor-salvamento', ['core', 'state', 'api', 'editor', 'editor-produtos', 'resultados', 'listas-cache'], context => {
        const filas = new Map();
        let sessao = null;
        let renderizandoEditor = 0;
        const copiar = valor => JSON.parse(JSON.stringify(valor));
        const campos = '#nomeListaPedidoEdit, #lojaListaPedidoEdit, #fornecedorListaPedidoEdit, #tblListaPedidoItens input[data-kind]:not([type="hidden"])';
        const identidade = () => context.modules.resultados.identidadeSessao();

        function renderizarEditorSemSalvar(renderizar) {
            renderizandoEditor++;
            try { return renderizar(); }
            finally { renderizandoEditor--; }
        }

        function capturarDadosEditorListaPedido() {
            const itens = capturarItensListaPedidoDoEditor();
            if (contextoEdicaoListaPedidoVigente(sessao)) {
                for (const [sku, item] of sessao.novosPersistidos) {
                    if (!itens.some(atual => normalizarSku(atual.SKU) === sku)) itens.push(copiar(sessao.confirmado.itens.find(atual => normalizarSku(atual.SKU) === sku) || item));
                }
            }
            const fornecedor = document.getElementById('fornecedorListaPedidoEdit');
            const opcao = fornecedor?.selectedOptions[0];
            const fornecedorId = String(fornecedor && !fornecedor.disabled && opcao && !opcao.disabled ? fornecedor.value : '').trim();
            return {
                nome_lista: String(document.getElementById('nomeListaPedidoEdit')?.value || '').trim(),
                loja: obterLojaListaPedidoSelecionada(), store_id: obterStoreIdListaPedidoSelecionado(),
                itens, ...(fornecedorId ? { fornecedor_id: fornecedorId } : {}),
            };
        }

        function contextoEdicaoListaPedidoVigente(registro) {
            return !!registro && sessao === registro && String(listaPedidoAtual?.id || '') === registro.id && identidade() === registro.identidade;
        }

        function prepararEditorSalvamentoListaPedido() {
            if (!listaPedidoAtual?.id) return;
            if (contextoEdicaoListaPedidoVigente(sessao)) {
                const visiveis = new Set([...document.querySelectorAll('#tblListaPedidoItens input[data-kind="sku"]')].map(input => normalizarSku(input.value)));
                for (const sku of sessao.novosPersistidos.keys()) if (visiveis.has(sku)) sessao.novosPersistidos.delete(sku);
                if (sessao.modelo !== listaPedidoAtual && !sessao.revisao && !sessao.ultimo) {
                    sessao.confirmado = copiar(capturarDadosEditorListaPedido());
                    sessao.modelo = listaPedidoAtual;
                }
                return;
            }
            const identidadeAtual = identidade();
            sessao = { id: String(listaPedidoAtual.id), identidade: identidadeAtual,
                chave: JSON.stringify([identidadeAtual, String(listaPedidoAtual.id)]),
                headers: { ...context.modules.api.authHeaders() }, revisao: 0, pendentes: new Set(), novosPersistidos: new Map(), modelo: listaPedidoAtual,
                confirmado: copiar(capturarDadosEditorListaPedido()), ultimo: null };
        }

        function obterContextoEdicaoListaPedido() {
            if (context.modules['listas-uniao']?.listaPedidoUnida()) { sessao = null; return null; }
            if (sessao && sessao.identidade !== identidade()) return null;
            prepararEditorSalvamentoListaPedido();
            return contextoEdicaoListaPedidoVigente(sessao) ? sessao : null;
        }

        function edicaoListaPedidoConfirmada() {
            return contextoEdicaoListaPedidoVigente(sessao) && !!sessao.ultimo &&
                !sessao.pendentes.size && !sessao.ultimo.falhou;
        }

        function invalidarContextoEdicaoListaPedido() { sessao = null; }
        function status(registro, texto) { if (contextoEdicaoListaPedidoVigente(registro)) setStatusListaPedido(texto); }

        function enfileirar(registro, tarefa) {
            let fila = filas.get(registro.chave);
            if (!fila) { fila = { executando: false, tarefas: [], ociosos: [] }; filas.set(registro.chave, fila); }
            const promessa = new Promise((resolve, reject) => { tarefa.esperas = [{ resolve, reject }]; });
            const anterior = fila.tarefas[fila.tarefas.length - 1];
            if (tarefa.edicao && anterior?.edicao && anterior.registro === registro) {
                fila.tarefas.pop();
                tarefa.esperas.push(...anterior.esperas);
                tarefa.manual ||= anterior.manual;
            }
            tarefa.registro = registro;
            fila.tarefas.push(tarefa);
            if (!fila.executando) void consumir(fila, registro.chave);
            return promessa;
        }

        async function consumir(fila, chave) {
            fila.executando = true;
            try {
                while (fila.tarefas.length) {
                    const tarefa = fila.tarefas.shift();
                    try {
                        const resultado = tarefa.registro.identidade === identidade()
                            ? await tarefa.executar(tarefa, fila) : false;
                        if (resultado === false) tarefa.falhou = true;
                        tarefa.esperas.forEach(espera => espera.resolve(resultado));
                    } catch (erro) { tarefa.esperas.forEach(espera => espera.reject(erro)); }
                }
            } finally {
                fila.executando = false;
                if (filas.get(chave) === fila) filas.delete(chave);
                fila.ociosos.forEach(resolve => resolve());
            }
        }

        function aguardarSalvamentoListaPedido(id) {
            const fila = filas.get(JSON.stringify([identidade(), String(id)]));
            return fila ? new Promise(resolve => fila.ociosos.push(resolve)) : Promise.resolve();
        }

        function atualizarCadastroSkuConfirmado(registro, sku, produto) {
            if (!registro) return;
            registro.confirmado.itens = registro.confirmado.itens.map(item => normalizarSku(item.SKU) === normalizarSku(sku)
                ? criarItemListaPedidoComProdutoCadastro(produto, sku, item) : item);
        }

        function snapshotConfirmado(registro, alvo, integral) {
            const atual = capturarDadosEditorListaPedido();
            if (integral || !alvo) return copiar(atual);
            const dados = copiar(registro.confirmado);
            if (alvo.id === 'nomeListaPedidoEdit') dados.nome_lista = atual.nome_lista;
            else if (alvo.id === 'lojaListaPedidoEdit') { dados.loja = atual.loja; dados.store_id = atual.store_id; }
            else if (alvo.id === 'fornecedorListaPedidoEdit') {
                if (atual.fornecedor_id) dados.fornecedor_id = atual.fornecedor_id;
                else delete dados.fornecedor_id;
            } else {
                const row = alvo.closest('tr[data-lista-pedido-row]');
                const rows = [...document.querySelectorAll('#tblListaPedidoItens tbody tr[data-lista-pedido-row]')];
                const indice = rows.indexOf(row);
                if (indice < 0 || !dados.itens[indice] || !atual.itens[indice]) return dados;
                const chaves = { sku: ['SKU'], qtd: ['Quantidade', 'Valor total', 'Estimed CBM'],
                    vun: ['Valor unidade', 'Valor total'], cbm: ['Estimed CBM'], peso: ['Estimed Weigh'], embalagem: ['Individual packaging'] };
                for (const chave of chaves[alvo.dataset.kind] || []) dados.itens[indice][chave] = atual.itens[indice][chave];
                if (['qtd', 'vun'].includes(alvo.dataset.kind)) {
                    dados.itens[indice]['Valor total'] = Math.round((Number(dados.itens[indice].Quantidade || 0) * Number(dados.itens[indice]['Valor unidade'] || 0) + Number.EPSILON) * 100) / 100;
                }
            }
            return dados;
        }

        function confirmarEdicaoListaPedido(alvo = null, { manual = false, integral = false, snapshot = null } = {}) {
            const registro = obterContextoEdicaoListaPedido();
            if (!registro || alvo?.dataset.skuRejeitado === '1') return Promise.resolve(false);
            const dados = snapshot ? copiar(snapshot) : snapshotConfirmado(registro, alvo, integral);
            if (!dados.nome_lista || !dados.loja || !dados.store_id) {
                status(registro, !dados.nome_lista ? 'Informe o nome da lista.' : 'Selecione a loja da lista.');
                return Promise.resolve(false);
            }
            const quantidadeInvalida = input => !String(input.value || '').trim() || !input.validity.valid;
            if ((alvo?.dataset.kind === 'qtd' && quantidadeInvalida(alvo)) ||
                ((!alvo || manual) && [...document.querySelectorAll('#tblListaPedidoItens input[data-kind="qtd"]')].some(quantidadeInvalida))) {
                status(registro, 'Informe uma quantidade válida, maior ou igual a zero.');
                return Promise.resolve(false);
            }
            if (alvo) registro.pendentes.delete(alvo);
            else registro.pendentes.clear();
            const anterior = JSON.stringify(registro.confirmado);
            registro.confirmado = copiar(dados);
            const assinatura = JSON.stringify(dados);
            if (!manual && assinatura === anterior) return registro.ultimo?.promessa || Promise.resolve(true);
            if (registro.ultimo?.assinatura === assinatura && !registro.ultimo.falhou && (!manual || !registro.ultimo.concluida)) {
                registro.ultimo.manual ||= manual;
                return registro.ultimo.promessa;
            }
            const tarefa = { edicao: true, manual, dados, assinatura, revisao: registro.revisao, falhou: false };
            // Start SKU lookups while the original rows still belong to this selection.
            const skusProntos = prepararSkusSnapshotListaPedido(dados, registro);
            tarefa.executar = async (_tarefa, fila) => {
                try {
                    if (!await skusProntos) throw new Error('Não foi possível confirmar o SKU. Corrija o campo e tente salvar novamente.');
                    context.modules.resultados.exigirSessao(registro.identidade);
                    status(registro, 'Salvando alterações...');
                    const cache = context.modules['listas-cache'].cacheListasPedidos;
                    const leituraCache = cache.capturarDetalhe(registro.id);
                    const resposta = await context.modules.api.request('/api/medias-compras/listas-pedidos/' + encodeURIComponent(registro.id), {
                        method: 'PUT', headers: { ...registro.headers, 'Content-Type': 'application/json' }, body: JSON.stringify(dados),
                    });
                    const retorno = await resposta.json();
                    if (!resposta.ok) throw new Error(retorno.detail || 'Falha ao salvar alterações da lista.');
                    if (retorno.lista && String(retorno.lista.id) !== registro.id) throw new Error('A resposta não contém a lista solicitada.');
                    if (!contextoEdicaoListaPedidoVigente(registro)) return true;
                    if (registro.revisao !== tarefa.revisao || fila.tarefas.length || registro.pendentes.size) {
                        status(registro, fila.tarefas.length ? 'Salvando alterações...' : 'Alterações pendentes de confirmação.');
                        return true;
                    }
                    if (retorno.lista && Array.isArray(retorno.lista.itens)) {
                        const confirmada = cache.confirmarMutacao(leituraCache, resposta);
                        if (confirmada) await cache.salvar(confirmada, { lista: retorno.lista, meta: null, baseSalva: JSON.stringify(retorno.lista) });
                        if (!contextoEdicaoListaPedidoVigente(registro) || registro.revisao !== tarefa.revisao || fila.tarefas.length || registro.pendentes.size) return true;
                    }
                    listaPedidoAtual = retorno.lista || { ...listaPedidoAtual, ...copiar(dados) };
                    listasPedidosResumo = (listasPedidosResumo || []).map(lista => String(lista.id) === registro.id
                        ? { ...lista, ...listaPedidoAtual, total_itens: listaPedidoAtual.itens?.length || 0 } : lista);
                    renderListaPedidosResumo();
                    if (tarefa.manual) {
                        await carregarListasPedidos(true);
                        if (!contextoEdicaoListaPedidoVigente(registro) || registro.revisao !== tarefa.revisao || fila.tarefas.length) return true;
                        if (!await abrirListaPedidoPorId(registro.id, { preservarConteudo: true, aguardarSalvamento: false })) { tarefa.falhou = true; return false; }
                        setStatusListaPedido('Lista salva com sucesso.');
                    } else status(registro, 'Lista salva com sucesso.');
                    return true;
                } catch (erro) {
                    tarefa.falhou = true;
                    status(registro, erro.message || 'Erro ao salvar lista. Tente novamente.');
                    return false;
                } finally { tarefa.concluida = true; }
            };
            registro.ultimo = tarefa;
            tarefa.promessa = enfileirar(registro, tarefa);
            return tarefa.promessa;
        }

        function salvarEdicaoListaPedidoManual() {
            if (!listaPedidoAtual?.id) { setStatusListaPedido('Selecione uma lista para salvar.'); return Promise.resolve(false); }
            return confirmarEdicaoListaPedido(null, { manual: true, integral: true });
        }

        function executarMutacaoListaPedido(registro, executar) {
            if (!registro) return Promise.reject(new Error('Selecione uma lista.'));
            if (typeof registro === 'string') {
                const identidadeAtual = identidade();
                registro = { id: registro, identidade: identidadeAtual, chave: JSON.stringify([identidadeAtual, registro]) };
            }
            return enfileirar(registro, { executar });
        }

        function incorporarItemSalvoListaPedido(registro, item, base) {
            if (!base.itens.some(atual => normalizarSku(atual.SKU) === normalizarSku(item.SKU))) registro.novosPersistidos.set(normalizarSku(item.SKU), copiar(item));
            const incorporar = dados => {
                const indice = dados.itens.findIndex(atual => normalizarSku(atual.SKU) === normalizarSku(item.SKU));
                if (indice < 0) { dados.itens.push(copiar(item)); return; }
                const anterior = base.itens.find(atual => normalizarSku(atual.SKU) === normalizarSku(item.SKU)) || {};
                const atualizado = { ...copiar(item) };
                for (const [campo, valor] of Object.entries(dados.itens[indice])) {
                    if (JSON.stringify(valor) !== JSON.stringify(anterior[campo])) atualizado[campo] = valor;
                }
                dados.itens[indice] = atualizado;
            };
            incorporar(registro.confirmado);
            for (const tarefa of filas.get(registro.chave)?.tarefas || []) if (tarefa.edicao) incorporar(tarefa.dados);
            return incorporar;
        }

        function ligarSalvamentoAutomaticoListaPedido() {
            const painel = document.getElementById('editorListaPedido');
            if (!painel || painel.dataset.salvamentoLigado) return;
            painel.dataset.salvamentoLigado = '1';
            const editavel = alvo => !renderizandoEditor && !context.modules['listas-uniao']?.listaPedidoUnida() && alvo?.matches?.(campos);
            painel.addEventListener('input', evento => {
                if (!editavel(evento.target)) return;
                delete evento.target.dataset.skuRejeitado;
                const registro = obterContextoEdicaoListaPedido();
                if (registro) { registro.revisao++; registro.pendentes.add(evento.target); status(registro, 'Alterações pendentes de confirmação.'); }
            });
            painel.addEventListener('change', evento => {
                if (!editavel(evento.target)) return;
                void confirmarEdicaoListaPedido(evento.target);
            });
            painel.addEventListener('blur', evento => {
                if (!editavel(evento.target) || evento.target.tagName === 'SELECT') return;
                void confirmarEdicaoListaPedido(evento.target);
            }, true);
            painel.addEventListener('keydown', evento => {
                if (!editavel(evento.target) || evento.target.tagName === 'SELECT' || evento.isComposing) return;
                if (evento.key === 'Enter') { evento.preventDefault(); evento.target.blur(); }
            });
        }

        return { renderizarEditorSemSalvar, capturarDadosEditorListaPedido, contextoEdicaoListaPedidoVigente, prepararEditorSalvamentoListaPedido,
            obterContextoEdicaoListaPedido, invalidarContextoEdicaoListaPedido, edicaoListaPedidoConfirmada, atualizarCadastroSkuConfirmado,
            confirmarEdicaoListaPedido, salvarEdicaoListaPedidoManual, executarMutacaoListaPedido, aguardarSalvamentoListaPedido, incorporarItemSalvoListaPedido, ligarSalvamentoAutomaticoListaPedido };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
