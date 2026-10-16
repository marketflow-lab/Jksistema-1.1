(function installJKMediasEditorProdutos(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('editor-produtos', ['core', 'state', 'api', 'editor', 'editor-salvamento', 'resultados'], (context) => {
        const fetch = context.modules.api.request;
        const obterAuthHeaders = context.modules.api.authHeaders;

        function obterCampoProdutoCadastro(produto, chaves) {
            if (!produto || typeof produto !== 'object') return '';
            const normalizarChave = (txt) => String(txt || '')
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .replace(/_/g, ' ')
                .toLowerCase()
                .replace(/[^a-z0-9\s]/g, ' ')
                .replace(/\s+/g, ' ')
                .trim()
                .replace(/^cg\s+/, '');
            const mapa = {};
            Object.keys(produto).forEach((k) => {
                const nk = normalizarChave(k);
                if (!nk) return;
                const val = produto[k];
                if (val === undefined || val === null || !String(val).trim()) return;
                if (mapa[nk] === undefined) {
                    mapa[nk] = val;
                }
            });
            for (const chave of (chaves || [])) {
                const key = normalizarChave(chave);
                if (!key) continue;
                const val = mapa[key];
                if (val !== undefined && val !== null && String(val).trim()) {
                    return String(val).trim();
                }
                for (const mk of Object.keys(mapa)) {
                    if (mk === key || mk.includes(key) || key.includes(mk)) {
                        const mv = mapa[mk];
                        if (mv !== undefined && mv !== null && String(mv).trim()) {
                            return String(mv).trim();
                        }
                    }
                }
            }
            return '';
        }

        function rotaCadastroIndisponivel(resp, data) {
            return resp.status === 405 || (resp.status === 404 && data.detail === 'Not Found');
        }

        function erroCadastro(data, mensagem) {
            return new Error(typeof data?.detail === 'string' ? data.detail : data?.detail?.message || mensagem);
        }

        async function buscarProdutoCadastroPorSku(sku, headersEscopo) {
            const skuTxt = String(sku || '').trim();
            if (!skuTxt) throw new Error('Informe um SKU válido.');
            const headers = { ...(headersEscopo || obterAuthHeaders()) };
            const sessao = context.modules.resultados.identidadeSessao(headers);
            context.modules.resultados.exigirSessao(sessao);
            const resp = await fetch('/api/cadastro/produto?sku=' + encodeURIComponent(skuTxt), { method: 'GET', headers });
            const data = await resp.json().catch(() => ({}));
            context.modules.resultados.exigirSessao(sessao);
            if (resp.ok) return data.produto || {};
            if (!rotaCadastroIndisponivel(resp, data)) throw erroCadastro(data, 'SKU não encontrado no cadastro.');

            const respLista = await fetch('/api/cadastro/produtos', { method: 'GET', headers });
            const lista = await respLista.json().catch(() => ({}));
            context.modules.resultados.exigirSessao(sessao);
            if (!respLista.ok) throw erroCadastro(lista, 'SKU não encontrado no cadastro.');
            const skuCmp = normalizarSku(skuTxt);
            const encontrado = (Array.isArray(lista) ? lista : []).find(produto =>
                normalizarSku(produto && (produto.cubagem_sku || produto.sku || produto.SKU) || '') === skuCmp);
            if (!encontrado) throw new Error('SKU não encontrado no cadastro.');
            return encontrado;
        }

        function criarItemListaPedidoComProdutoCadastro(produto, skuFallback, preservados = {}) {
            const skuCadastro = normalizarSku(obterCampoProdutoCadastro(produto, ['cubagem sku', 'sku']) || skuFallback);
            const quantidade = toNumeroDecimal(preservados.Quantidade || 0);
            const valorUnidade = toNumeroDecimal(preservados['Valor unidade'] || 0);
            const valorTotal = Math.round(((quantidade * valorUnidade) + Number.EPSILON) * 100) / 100;
            const cbmIndividualCadastro = toNumeroDecimal(obterCampoProdutoCadastro(produto, ['estimed cbm', 'estimated cbm', 'cbm', 'm3 individual', 'm3', 'cg m3 individual', 'cg_m3 individual']));
            const cbmIndividual = cbmIndividualCadastro > 0
                ? cbmIndividualCadastro
                : toNumeroDecimal(preservados['M3 individual'] || preservados['MÃ‚Â³ individual'] || preservados['m3_individual'] || 0);
            const cbmTotal = (cbmIndividual > 0 && quantidade > 0) ? (cbmIndividual * quantidade) : 0;
            const titulo = obterCampoProdutoCadastro(produto, [
                'produtos blig', 'produto blig', 'produtos bling', 'produto bling',
                'titulo do produto em ingles', 'tÃ­tulo do produto em inglÃªs', 'titulo', 'produto',
                'nome', 'product name', 'description', 'product description', 'traduÃ§Ã£o ptbr ou nome na bling'
            ]);
            return {
                ...preservados,
                SKU: skuCadastro,
                Foto: obterCampoProdutoCadastro(produto, ['foto']),
                'Título do produto em inglês': titulo,
                OEM: obterCampoProdutoCadastro(produto, ['oem', 'oem/ model', 'oem model', 'codigo oem', 'part number']),
                'Color/side': obterCampoProdutoCadastro(produto, ['color side', 'color/side', 'cor lado', 'cor/lado', 'lado cor', 'lado/cor', 'lado', 'cor', 'color', 'side']),
                Link: obterCampoProdutoCadastro(produto, ['link', 'link aliexpress', 'url aliexpress', 'url', 'mlb principal']),
                Quantidade: quantidade,
                'Valor unidade': valorUnidade,
                'Valor total': valorTotal,
                'Estimed CBM': cbmTotal > 0 ? formatarNumeroListaPedido(cbmTotal, 6) : String(preservados['Estimed CBM'] || '').trim(),
                'M3 individual': cbmIndividual > 0 ? cbmIndividual : String(preservados['M3 individual'] || '').trim(),
                'Estimed Weigh': obterCampoProdutoCadastro(produto, ['estimed weigh', 'estimated weigh', 'estimated weight', 'peso', 'weight']) || String(preservados['Estimed Weigh'] || '').trim(),
                'Individual packaging': obterCampoProdutoCadastro(produto, ['individual packaging', 'embalagem', 'packaging']) || String(preservados['Individual packaging'] || '').trim(),
            };
        }

        const consultasSku = new WeakMap();

        function consultarSkuEditado(input, registro) {
            const emCurso = consultasSku.get(input);
            if (emCurso) return emCurso;
            const row = input?.closest('tr[data-lista-pedido-row="1"]');
            const anterior = normalizarSku(input?.dataset.originalSku || '');
            const novo = normalizarSku(input?.value || '');
            if (!row || !novo || novo === anterior) {
                if (input) input.value = anterior || novo;
                return Promise.resolve({ ok: true, sku: novo });
            }
            const rows = [...row.parentNode.querySelectorAll('tr[data-lista-pedido-row="1"]')];
            if (rows.some(linha => linha !== row && normalizarSku(linha.querySelector('[data-kind="sku"]')?.value) === novo)) {
                input.value = anterior;
                input.dataset.skuRejeitado = '1';
                const item = registro?.confirmado.itens[Number(row.dataset.idx)];
                if (item) item.SKU = anterior;
                setStatusListaPedido('SKU ' + novo + ' já existe nesta lista.');
                abrirAvisoSkuCentral('SKU ' + novo + ' já existe nesta lista.');
                return Promise.resolve({ ok: false });
            }
            const valoresAntes = Object.fromEntries(['cbm', 'peso', 'embalagem'].map(kind => [kind, row.querySelector('[data-kind="' + kind + '"]')?.value]));
            input.dataset.aplicandoSku = '1';
            input.disabled = true;
            const promessa = (async () => {
                try {
                    const produto = await buscarProdutoCadastroPorSku(novo, registro.headers);
                    if (contextoEdicaoListaPedidoVigente(registro) && input.isConnected && normalizarSku(input.value) === novo) {
                        const atualizado = criarItemListaPedidoComProdutoCadastro(produto, novo, capturarItemListaPedidoDaLinha(row));
                        listaPedidoAtual.itens[Number(row.dataset.idx)] = atualizado;
                        input.value = atualizado.SKU;
                        input.dataset.originalSku = atualizado.SKU;
                        row.dataset.skuCmp = normalizarSkuComparacaoLocal(atualizado.SKU);
                        row.cells[2].textContent = atualizado['Título do produto em inglês'] || '';
                        row.cells[2].title = row.cells[2].textContent;
                        row.cells[3].textContent = atualizado.OEM || '';
                        row.cells[4].textContent = atualizado['Color/side'] || '';
                        row.cells[5].innerHTML = atualizado.Link ? '<a href="' + escaparHtml(atualizado.Link) + '" target="_blank" rel="noopener noreferrer">' + escaparHtml(atualizado.Link) + '</a>' : '';
                        const foto = obterUrlFoto(atualizado.Foto || '');
                        row.cells[1].innerHTML = foto ? '<img class="foto-sku" ' + atributoSrcFotoCadastro(foto) + ' alt="Foto" loading="lazy">' : '<span class="foto-empty">Sem foto</span>';
                        for (const [kind, campo] of [['cbm', 'Estimed CBM'], ['peso', 'Estimed Weigh'], ['embalagem', 'Individual packaging']]) {
                            const el = row.querySelector('[data-kind="' + kind + '"]');
                            if (el && el.value === valoresAntes[kind]) el.value = String(atualizado[campo] || '');
                        }
                        row.querySelector('[data-kind="cbm"]').dataset.cbmIndividual = String(atualizado['M3 individual'] || '');
                        configurarDetalhesLinhaListaPedido(row);
                        atualizarTotaisListaPedido();
                    }
                    return { ok: true, sku: novo, produto };
                } catch (erro) {
                    if (contextoEdicaoListaPedidoVigente(registro)) {
                        if (input.isConnected) { input.value = anterior; input.dataset.skuRejeitado = '1'; }
                        const item = registro.confirmado.itens[Number(row.dataset.idx)];
                        if (item) item.SKU = anterior;
                        setStatusListaPedido(erro.message || 'Não foi possível alterar o SKU.');
                        if (/sku/i.test(String(erro.message || ''))) abrirAvisoSkuCentral(erro.message);
                    }
                    return { ok: false };
                } finally {
                    input.disabled = false;
                    delete input.dataset.aplicandoSku;
                    consultasSku.delete(input);
                }
            })();
            consultasSku.set(input, promessa);
            return promessa;
        }

        async function atualizarSkuItemListaPedido(ev) {
            return (await consultarSkuEditado(ev?.target, obterContextoEdicaoListaPedido())).ok;
        }

        function prepararSkusSnapshotListaPedido(dados, registro) {
            const pendentes = [];
            document.querySelectorAll('#tblListaPedidoItens input[data-kind="sku"]').forEach(input => {
                const sku = normalizarSku(input.value || '');
                if (!consultasSku.has(input) && sku === normalizarSku(input.dataset.originalSku || '')) return;
                const indice = dados.itens.findIndex(item => normalizarSku(item.SKU) === sku);
                if (indice < 0) return;
                pendentes.push(consultarSkuEditado(input, registro).then(resultado => {
                    if (!resultado.ok) return false;
                    if (resultado.produto) {
                        dados.itens[indice] = criarItemListaPedidoComProdutoCadastro(resultado.produto, sku, dados.itens[indice]);
                        atualizarCadastroSkuConfirmado(registro, sku, resultado.produto);
                    }
                    return true;
                }));
            });
            return Promise.all(pendentes).then(resultados => resultados.every(Boolean));
        }

        async function aplicarEdicoesSkuPendentesListaPedido() {
            const registro = obterContextoEdicaoListaPedido();
            const dados = capturarDadosEditorListaPedido();
            return await prepararSkusSnapshotListaPedido(dados, registro);
        }

        async function persistirNovoItemListaPedido(dados, registro, snapshot, revisao) {
            const { skuDigitado, quantidade, valorUnidade, nomeLista, lojaLista, storeIdLista } = dados;
            const headers = { 'Content-Type': 'application/json', ...registro.headers };
            const url = '/api/medias-compras/listas-pedidos/' + encodeURIComponent(registro.id);
            context.modules.resultados.exigirSessao(registro.identidade);
            setStatusModalAdicionarSku('Salvando SKU na lista...');
            let resp = await fetch(url + '/adicionar-sku', { method: 'POST', headers,
                body: JSON.stringify({ sku: skuDigitado, quantidade, valor_unitario: valorUnidade }) });
            let data = await resp.json().catch(() => ({}));
            context.modules.resultados.exigirSessao(registro.identidade);
            let skuCadastro = normalizarSku(data.sku || skuDigitado);
            let listaLegada = null;
            if (!resp.ok && rotaCadastroIndisponivel(resp, data)) {
                setStatusModalAdicionarSku('Buscando dados do SKU no cadastro...');
                const produto = await buscarProdutoCadastroPorSku(skuDigitado, registro.headers);
                context.modules.resultados.exigirSessao(registro.identidade);
                const novoItem = criarItemListaPedidoComProdutoCadastro(produto, skuDigitado, {
                    Quantidade: quantidade, 'Valor unidade': valorUnidade,
                    'Estimed CBM': '', 'Estimed Weigh': '', 'Individual packaging': '',
                });
                skuCadastro = novoItem.SKU;
                const itens = snapshot.itens.map(item => ({ ...item }));
                const indice = itens.findIndex(item => normalizarSku(item.SKU) === skuCadastro);
                if (indice >= 0) itens[indice] = { ...itens[indice], ...novoItem };
                else itens.push(novoItem);
                listaLegada = { ...snapshot, id: registro.id, itens };
                setStatusModalAdicionarSku('Salvando SKU na lista...');
                resp = await fetch(url, { method: 'PUT', headers,
                    body: JSON.stringify({ nome_lista: nomeLista, loja: lojaLista, store_id: storeIdLista, itens }) });
                data = await resp.json().catch(() => ({}));
                context.modules.resultados.exigirSessao(registro.identidade);
                skuCadastro = normalizarSku(data.sku || skuCadastro);
            }
            if (!resp.ok) throw erroCadastro(data, 'Falha ao salvar item na lista.');
            const lista = data.lista || listaLegada;
            if (!lista || String(lista.id) !== registro.id) throw new Error('A resposta não contém a lista solicitada.');
            const salvo = lista.itens?.find(item => normalizarSku(item.SKU) === skuCadastro);
            if (!salvo) throw new Error('O item não retornou na lista após salvar. Tente novamente.');
            const incorporar = incorporarItemSalvoListaPedido(registro, salvo, snapshot);
            if (contextoEdicaoListaPedidoVigente(registro)) {
                context.modules.api.cancelarTarefa('listas-resumo');
                const resumos = listasPedidosResumo || [];
                const existente = resumos.find(resumo => String(resumo.id) === registro.id);
                const resumoSalvo = { ...existente, ...lista, total_itens: lista.itens.length };
                const storeIdResposta = String(lista.store_id || '').trim();
                // O resumo já foi filtrado pelo servidor, inclusive quando uma lista legada ainda não tem ID.
                const pertenceAoFiltro = !lojaSelecionada || lojaSelecionada === '__todas' ||
                    (storeIdSelecionado ? (storeIdResposta ? storeIdResposta === String(storeIdSelecionado) : !!existente)
                        : String(lista.loja || '') === String(lojaSelecionada));
                listasPedidosResumo = existente
                    ? resumos.flatMap(resumo => String(resumo.id) === registro.id ? (pertenceAoFiltro ? [resumoSalvo] : []) : [resumo])
                    : pertenceAoFiltro ? [...resumos, resumoSalvo] : resumos;
                renderListaPedidosResumo();
                if (registro.revisao === revisao && !registro.pendentes.size) {
                    listaPedidoAtual = lista;
                    renderEditorListaPedido();
                } else {
                    const rascunho = capturarDadosEditorListaPedido();
                    incorporar(rascunho);
                    listaPedidoAtual = { ...lista, ...rascunho };
                }
            }
            const existia = snapshot.itens.some(item => normalizarSku(item.SKU) === skuCadastro);
            return { data, skuCadastro, msgAcao: 'SKU ' + skuCadastro + (existia ? ' já existia e foi atualizado.' : ' adicionado na lista.') };
        }

        async function confirmarAdicionarSkuPedido() {
            if (!listaPedidoAtual || !listaPedidoAtual.id) {
                setStatusListaPedido('Selecione uma lista para adicionar SKU.');
                return;
            }

            const registro = obterContextoEdicaoListaPedido();
            if (!registro) { setStatusModalAdicionarSku('A sessão mudou. Reabra a lista para continuar.'); return; }
            const snapshot = capturarDadosEditorListaPedido();
            const revisao = registro.revisao;
            const inputSku = document.getElementById('addSkuInput');
            const inputQtd = document.getElementById('addSkuQtdInput');
            const inputValor = document.getElementById('addSkuValorInput');
            const btnConfirmar = document.getElementById('btnConfirmarAdicionarSku');
            const inputNome = document.getElementById('nomeListaPedidoEdit');
            if (!inputSku || !inputQtd || !inputValor || !btnConfirmar || !inputNome) return;

            const skuDigitado = String(inputSku.value || '').trim();
            const lojaLista = obterLojaListaPedidoSelecionada();
            const storeIdLista = obterStoreIdListaPedidoSelecionado();
            const qtdNumero = toNumeroPrompt(inputQtd.value || '');
            const quantidade = Math.max(0, Math.round(Number.isFinite(qtdNumero) ? qtdNumero : 0));
            const valorNumero = toNumeroPrompt(inputValor.value || '');

            if (!skuDigitado) {
                setStatusModalAdicionarSku('Informe o SKU.');
                inputSku.focus();
                return;
            }
            if (!Number.isFinite(qtdNumero) || quantidade <= 0) {
                setStatusModalAdicionarSku('Informe uma quantidade válida (maior que zero).');
                inputQtd.focus();
                return;
            }
            if (!Number.isFinite(valorNumero) || valorNumero <= 0) {
                setStatusModalAdicionarSku('Informe um valor unitário válido (maior que zero).');
                inputValor.focus();
                return;
            }

            const nomeLista = String((inputNome && inputNome.value) || '').trim();
            if (!nomeLista) {
                setStatusListaPedido('Informe o nome da lista antes de adicionar SKU.');
                return;
            }

            try {
                btnConfirmar.disabled = true;
                setStatusModalAdicionarSku('Aguardando salvamento em andamento...');
                await executarMutacaoListaPedido(registro, async () => {
                const valorUnidade = Math.round((Number(valorNumero) + Number.EPSILON) * 100) / 100;
                const { data, msgAcao, skuCadastro } = await persistirNovoItemListaPedido({
                    skuDigitado, quantidade, valorUnidade, nomeLista, lojaLista, storeIdLista,
                }, registro, snapshot, revisao);
                if (!contextoEdicaoListaPedidoVigente(registro)) return;

                if (registro.revisao !== revisao || registro.pendentes.size) {
                    fecharModalAdicionarSkuPedido();
                    setStatusListaPedido('SKU salvo. Alterações pendentes de confirmação.');
                    return;
                }
                const apareceuVisual = destacarSkuNaTabelaPedido(skuCadastro);
                if (!apareceuVisual) {
                    throw new Error('SKU salvo, mas não foi localizado visualmente na tabela.');
                }

                const acaoServidor = String((data && data.acao) || '').toLowerCase();
                let msgFinal = msgAcao;
                if (acaoServidor === 'adicionado') {
                    msgFinal = 'SKU ' + skuCadastro + ' adicionado na lista.';
                } else if (acaoServidor === 'atualizado') {
                    msgFinal = 'SKU ' + skuCadastro + ' já existia e foi atualizado.';
                }

                fecharModalAdicionarSkuPedido();
                setStatusListaPedido(msgFinal + ' Salvo com sucesso.');
                });
            } catch (e) {
                if (!contextoEdicaoListaPedidoVigente(registro)) return;
                const msgErro = e.message || 'Não foi possível adicionar o SKU ao pedido.';
                setStatusModalAdicionarSku(msgErro);
                if (/sku/i.test(String(msgErro))) {
                    abrirAvisoSkuCentral(msgErro);
                }
            } finally {
                btnConfirmar.disabled = false;
            }
        }

        return {
            obterCampoProdutoCadastro,
            buscarProdutoCadastroPorSku,
            criarItemListaPedidoComProdutoCadastro,
            atualizarSkuItemListaPedido,
            aplicarEdicoesSkuPendentesListaPedido,
            prepararSkusSnapshotListaPedido,
            confirmarAdicionarSkuPedido
        };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
