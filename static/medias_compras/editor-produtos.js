(function installJKMediasEditorProdutos(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('editor-produtos', ['core', 'state', 'api', 'editor'], (context) => {
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

        async function buscarProdutoCadastroPorSku(sku) {
            const skuTxt = String(sku || '').trim();
            if (!skuTxt) {
                throw new Error('Informe um SKU válido.');
            }

            const headers = { ...obterAuthHeaders() };
            const resp = await fetch('/api/cadastro/produto?sku=' + encodeURIComponent(skuTxt), {
                method: 'GET',
                headers
            });
            if (resp.ok) {
                const data = await resp.json();
                return (data && data.produto) ? data.produto : {};
            }

            const respLista = await fetch('/api/cadastro/produtos', {
                method: 'GET',
                headers
            });
            if (!respLista.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || 'SKU não encontrado no cadastro.');
            }

            const lista = await respLista.json();
            const produtos = Array.isArray(lista) ? lista : [];
            const skuCmp = normalizarSkuComparacaoLocal(skuTxt);
            const encontrado = produtos.find((p) => {
                const skuProd = (p && (p.sku || p.SKU)) || '';
                return normalizarSkuComparacaoLocal(skuProd) === skuCmp;
            });

            if (!encontrado) {
                throw new Error('SKU não encontrado no cadastro.');
            }
            return encontrado;
        }

        function criarItemListaPedidoComProdutoCadastro(produto, skuFallback, preservados = {}) {
            const skuCadastro = normalizarSku(obterCampoProdutoCadastro(produto, ['sku']) || skuFallback);
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
                'TÃ­tulo do produto em inglÃªs': titulo,
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

        async function atualizarSkuItemListaPedido(ev) {
            const input = ev && ev.target ? ev.target : null;
            if (!input || input.dataset.aplicandoSku === '1') return true;
            const row = input.closest ? input.closest('tr[data-lista-pedido-row="1"]') : null;
            if (!row || !listaPedidoAtual || !Array.isArray(listaPedidoAtual.itens)) return true;

            const skuAnterior = normalizarSku(input.dataset.originalSku || '');
            const skuNovo = normalizarSku(input.value || '');
            if (!skuNovo || skuNovo === skuAnterior) {
                input.value = skuAnterior || skuNovo;
                return true;
            }

            const skuCmpNovo = normalizarSkuComparacaoLocal(skuNovo);
            const rows = Array.from(row.parentNode ? row.parentNode.querySelectorAll('tr[data-lista-pedido-row="1"]') : []);
            const duplicado = rows.some((linha) => {
                if (linha === row) return false;
                const skuLinha = normalizarSku(linha.querySelector('input[data-kind="sku"]')?.value || '');
                return normalizarSkuComparacaoLocal(skuLinha) === skuCmpNovo;
            });
            if (duplicado) {
                input.value = skuAnterior;
                setStatusListaPedido('SKU ' + skuNovo + ' jÃ¡ existe nesta lista.');
                abrirAvisoSkuCentral('SKU ' + skuNovo + ' jÃ¡ existe nesta lista.');
                return false;
            }

            try {
                input.dataset.aplicandoSku = '1';
                input.disabled = true;
                setStatusListaPedido('Buscando dados do SKU ' + skuNovo + ' no cadastro...');
                const itemPreservado = capturarItemListaPedidoDaLinha(row);
                const produto = await buscarProdutoCadastroPorSku(skuNovo);
                const itemAtualizado = criarItemListaPedidoComProdutoCadastro(produto, skuNovo, {
                    ...itemPreservado,
                    SKU: skuNovo
                });
                const itens = capturarItensListaPedidoDoEditor();
                const posVisual = rows.indexOf(row);
                if (posVisual >= 0 && posVisual < itens.length) {
                    itens[posVisual] = itemAtualizado;
                } else {
                    const idx = Number(row.dataset.idx || -1);
                    if (Number.isInteger(idx) && idx >= 0 && idx < itens.length) itens[idx] = itemAtualizado;
                }
                listaPedidoAtual.itens = itens;
                renderEditorListaPedido();
                destacarSkuNaTabelaPedido(itemAtualizado.SKU);
                setStatusListaPedido('SKU ' + skuAnterior + ' alterado para ' + itemAtualizado.SKU + '. Dados atualizados pelo cadastro. Clique em "Salvar alteraÃ§Ãµes" para confirmar.');
                return true;
            } catch (e) {
                input.value = skuAnterior;
                setStatusListaPedido(e.message || 'NÃ£o foi possÃ­vel alterar o SKU.');
                if (/sku/i.test(String(e.message || ''))) {
                    abrirAvisoSkuCentral(e.message || 'SKU nÃ£o encontrado no cadastro.');
                }
                return false;
            } finally {
                input.disabled = false;
                input.dataset.aplicandoSku = '';
            }
        }

        async function aplicarEdicoesSkuPendentesListaPedido() {
            for (let tentativas = 0; tentativas < 30; tentativas += 1) {
                const inputs = Array.from(document.querySelectorAll('#tblListaPedidoItens tbody input[data-kind="sku"]'));
                const pendente = inputs.find((input) => normalizarSku(input.value || '') !== normalizarSku(input.dataset.originalSku || ''));
                if (!pendente) return true;
                const ok = await atualizarSkuItemListaPedido({ target: pendente });
                if (!ok) return false;
            }
            return true;
        }

        async function persistirNovoItemListaPedido(dados) {
            const { skuCadastro, quantidade, valorUnidade, nomeLista, lojaLista, novoItem } = dados;
            const itensAtuais = Array.isArray(listaPedidoAtual.itens) ? [...listaPedidoAtual.itens] : [];
            const idxExistente = itensAtuais.findIndex((i) => normalizarSku(i && i.SKU) === skuCadastro);
            let msgAcao = '';
            if (idxExistente >= 0) {
                itensAtuais[idxExistente] = { ...itensAtuais[idxExistente], ...novoItem };
                msgAcao = 'SKU ' + skuCadastro + ' já existia e foi atualizado.';
            } else {
                itensAtuais.push(novoItem);
                msgAcao = 'SKU ' + skuCadastro + ' adicionado na lista.';
            }

            setStatusModalAdicionarSku('Salvando item na lista...');
            const headers = { 'Content-Type': 'application/json', ...obterAuthHeaders() };
            let data = null;
            let falhaRotaDedicada = false;
            const resp = await fetch('/api/medias-compras/listas-pedidos/' + encodeURIComponent(String(listaPedidoAtual.id)) + '/adicionar-sku', {
                method: 'POST',
                headers,
                body: JSON.stringify({ sku: skuCadastro, quantidade, valor_unitario: valorUnidade })
            });

            if (resp.ok) {
                data = await resp.json();
            } else {
                falhaRotaDedicada = true;
                const err = await resp.json().catch(() => ({}));
                if (resp.status === 401 || resp.status === 403) {
                    throw new Error(err.detail || 'Falha ao salvar item na lista.');
                }
            }

            if (falhaRotaDedicada) {
                const respPut = await fetch('/api/medias-compras/listas-pedidos/' + encodeURIComponent(String(listaPedidoAtual.id)), {
                    method: 'PUT',
                    headers,
                    body: JSON.stringify({ nome_lista: nomeLista, loja: lojaLista || undefined, itens: itensAtuais })
                });
                if (!respPut.ok) {
                    const errPut = await respPut.json().catch(() => ({}));
                    throw new Error(errPut.detail || 'Falha ao salvar item na lista.');
                }
                data = await respPut.json();
            }

            listaPedidoAtual = data.lista || listaPedidoAtual;
            const skuSalvo = Array.isArray(listaPedidoAtual && listaPedidoAtual.itens)
                ? listaPedidoAtual.itens.some((i) => normalizarSkuComparacaoLocal(i && i.SKU) === normalizarSkuComparacaoLocal(skuCadastro))
                : false;
            if (!skuSalvo) throw new Error('O item não retornou na lista após salvar. Tente novamente.');

            await carregarListasPedidos();
            if (listaPedidoAtual && listaPedidoAtual.id) await abrirListaPedidoPorId(listaPedidoAtual.id);
            const skuAposReabrir = Array.isArray(listaPedidoAtual && listaPedidoAtual.itens)
                ? listaPedidoAtual.itens.some((i) => normalizarSkuComparacaoLocal(i && i.SKU) === normalizarSkuComparacaoLocal(skuCadastro))
                : false;
            if (!skuAposReabrir) {
                const itensForcados = Array.isArray(listaPedidoAtual && listaPedidoAtual.itens) ? [...listaPedidoAtual.itens] : [];
                const idxForcado = itensForcados.findIndex((i) => normalizarSkuComparacaoLocal(i && i.SKU) === normalizarSkuComparacaoLocal(skuCadastro));
                if (idxForcado >= 0) itensForcados[idxForcado] = { ...itensForcados[idxForcado], ...novoItem };
                else itensForcados.push({ ...novoItem });

                const respForcado = await fetch('/api/medias-compras/listas-pedidos/' + encodeURIComponent(String(listaPedidoAtual.id)), {
                    method: 'PUT',
                    headers,
                    body: JSON.stringify({ nome_lista: nomeLista, loja: lojaLista || undefined, itens: itensForcados })
                });
                if (!respForcado.ok) {
                    const errForcado = await respForcado.json().catch(() => ({}));
                    throw new Error(errForcado.detail || 'Não foi possível confirmar o SKU na lista.');
                }

                const dataForcado = await respForcado.json();
                listaPedidoAtual = dataForcado.lista || listaPedidoAtual;
                await abrirListaPedidoPorId(listaPedidoAtual.id);
                const skuAposForcar = Array.isArray(listaPedidoAtual && listaPedidoAtual.itens)
                    ? listaPedidoAtual.itens.some((i) => normalizarSkuComparacaoLocal(i && i.SKU) === normalizarSkuComparacaoLocal(skuCadastro))
                    : false;
                if (!skuAposForcar) throw new Error('SKU não apareceu na lista após tentativas de salvamento.');
            }
            return { data, msgAcao };
        }

        async function confirmarAdicionarSkuPedido() {
            if (!listaPedidoAtual || !listaPedidoAtual.id) {
                setStatusListaPedido('Selecione uma lista para adicionar SKU.');
                return;
            }

            const inputSku = document.getElementById('addSkuInput');
            const inputQtd = document.getElementById('addSkuQtdInput');
            const inputValor = document.getElementById('addSkuValorInput');
            const btnConfirmar = document.getElementById('btnConfirmarAdicionarSku');
            const inputNome = document.getElementById('nomeListaPedidoEdit');
            if (!inputSku || !inputQtd || !inputValor || !btnConfirmar || !inputNome) return;

            const skuDigitado = String(inputSku.value || '').trim();
            const lojaLista = obterLojaListaPedidoSelecionada();
            const qtdNumero = toNumeroPrompt(inputQtd.value || '');
            const quantidade = Math.max(0, Math.round(Number.isFinite(qtdNumero) ? qtdNumero : 0));
            const valorNumero = toNumeroPrompt(inputValor.value || '');

            if (!skuDigitado) {
                setStatusModalAdicionarSku('Informe o SKU.');
                inputSku.focus();
                return;
            }
            if (!/^\d+$/.test(skuDigitado)) {
                setStatusModalAdicionarSku('Somente SKU numérico é permitido.');
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
                setStatusModalAdicionarSku('Buscando dados do SKU no cadastro...');
                const produto = await buscarProdutoCadastroPorSku(skuDigitado);

                const skuCadastro = normalizarSku(obterCampoProdutoCadastro(produto, ['sku']) || skuDigitado);
                const titulo = obterCampoProdutoCadastro(produto, [
                    'produtos blig', 'produto blig', 'produtos bling', 'produto bling',
                    'titulo do produto em ingles', 'título do produto em inglês', 'titulo', 'produto',
                    'nome', 'product name', 'description', 'product description', 'tradução ptbr ou nome na bling'
                ]);
                const foto = obterCampoProdutoCadastro(produto, ['foto']);
                const oem = obterCampoProdutoCadastro(produto, ['oem', 'oem/ model', 'oem model', 'codigo oem', 'part number']);
                const corLado = obterCampoProdutoCadastro(produto, ['color side', 'color/side', 'cor lado', 'cor/lado', 'lado cor', 'lado/cor', 'lado', 'cor', 'color', 'side']);
                const link = obterCampoProdutoCadastro(produto, ['link', 'link aliexpress', 'url aliexpress', 'url', 'mlb principal']);
                const valorUnidade = Math.round((Number(valorNumero) + Number.EPSILON) * 100) / 100;
                const valorTotal = Math.round(((quantidade * valorUnidade) + Number.EPSILON) * 100) / 100;

                const novoItem = {
                    SKU: skuCadastro,
                    Foto: foto,
                    'Título do produto em inglês': titulo,
                    OEM: oem,
                    'Color/side': corLado,
                    Link: link,
                    Quantidade: quantidade,
                    'Valor unidade': valorUnidade,
                    'Valor total': valorTotal,
                    'Estimed CBM': '',
                    'Estimed Weigh': '',
                    'Individual packaging': '',
                };

                Object.assign(novoItem, criarItemListaPedidoComProdutoCadastro(produto, skuCadastro, novoItem));
                const { data, msgAcao } = await persistirNovoItemListaPedido({
                    skuCadastro,
                    quantidade,
                    valorUnidade,
                    nomeLista,
                    lojaLista,
                    novoItem,
                });

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
            } catch (e) {
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
            confirmarAdicionarSkuPedido
        };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
