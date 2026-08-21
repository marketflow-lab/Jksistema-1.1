(function (global) {
    'use strict';

    const cadastro = global.JKCadastro;
    if (!cadastro || !cadastro.components.has('produtos')) throw new Error('Tabela de produtos não inicializada.');
    if (cadastro.components.has('custos-mlb')) return;

    const { elements, state, constants } = cadastro.runtime;
    const core = cadastro.core;
    const compararSku = cadastro.produtosTabela.compararSku;

    function obterLojaProduto(item) {
        return String(item && (item.loja_custo || item.loja_sync || item.loja || item.conta || item.loja_conta || item.seller) || 'Sem loja').trim() || 'Sem loja';
    }

    function normalizarLojaCusto(valor) {
        return String(valor || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '');
    }

    function obterDadosCustoLoja(item, loja) {
        const mapa = item && item.custos_por_loja;
        if (!mapa || typeof mapa !== 'object') return null;
        const lojaKey = normalizarLojaCusto(loja);
        if (lojaKey && mapa[lojaKey]) return mapa[lojaKey];
        return Object.values(mapa).find(valor => normalizarLojaCusto(valor && valor.loja_sync) === lojaKey) || null;
    }

    function aplicarDadosCustoLoja(item, loja) {
        const dados = obterDadosCustoLoja(item, loja);
        if (!dados) return { ...item, loja_custo: loja };
        return {
            ...item,
            loja_custo: loja,
            custo: dados.custo !== undefined ? dados.custo : item.custo,
            preco: dados.preco !== undefined ? dados.preco : item.preco,
            imposto: dados.imposto !== undefined ? dados.imposto : item.imposto,
            updated_at: dados.updated_at || item.updated_at,
        };
    }

    function expandirCustosPorLoja(lista) {
        return lista.flatMap(item => {
            const lojas = obterLojaProduto(item).split('|').map(valor => valor.trim()).filter(Boolean);
            return lojas.length ? lojas.map(loja => aplicarDadosCustoLoja(item, loja)) : [{ ...item, loja_custo: 'Sem loja' }];
        });
    }

    function atualizarFiltroLojasCustos() {
        const valorAtual = elements.lojaCustosSelect.value || '';
        const lojas = new Set();
        state.produtos.forEach(item => obterLojaProduto(item).split('|').map(valor => valor.trim()).filter(Boolean).forEach(loja => lojas.add(loja)));
        const ordenadas = Array.from(lojas).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
        elements.lojaCustosSelect.innerHTML = '<option value="">Todas as lojas</option>'
            + ordenadas.map(loja => `<option value="${core.escaparHtml(loja)}">${core.escaparHtml(loja)}</option>`).join('');
        if (valorAtual && ordenadas.includes(valorAtual)) elements.lojaCustosSelect.value = valorAtual;
    }

    function mudarPaginaCustos(pagina) {
        state.paginaCustosAtual = pagina;
        renderCustos(false);
        global.scrollTo(0, 0);
    }

    function renderPaginacaoCustos(totalItens) {
        elements.paginacaoCustos.innerHTML = '';
        const totalPaginas = Math.ceil(totalItens / constants.itensPorPagina);
        if (totalPaginas <= 1) return;
        const anterior = document.createElement('button');
        anterior.textContent = 'Anterior';
        anterior.disabled = state.paginaCustosAtual === 1;
        anterior.addEventListener('click', () => mudarPaginaCustos(state.paginaCustosAtual - 1));
        elements.paginacaoCustos.appendChild(anterior);
        const info = document.createElement('span');
        const de = (state.paginaCustosAtual - 1) * constants.itensPorPagina + 1;
        const ate = Math.min(state.paginaCustosAtual * constants.itensPorPagina, totalItens);
        info.textContent = `${de} à ${ate} de ${totalItens}`;
        elements.paginacaoCustos.appendChild(info);
        const proxima = document.createElement('button');
        proxima.textContent = 'Próximo';
        proxima.disabled = state.paginaCustosAtual === totalPaginas;
        proxima.addEventListener('click', () => mudarPaginaCustos(state.paginaCustosAtual + 1));
        elements.paginacaoCustos.appendChild(proxima);
    }

    function filtrarCustos() {
        const termoTexto = core.normalizarBuscaTexto(elements.filtroCustos.value);
        const termoSku = core.normalizarBuscaSku(elements.filtroCustos.value);
        const lojaSelecionada = String(elements.lojaCustosSelect.value || '').trim();
        const listaBase = expandirCustosPorLoja(state.produtos);
        return (termoTexto ? listaBase.filter(item => {
            const skuBruto = String(item && item.sku || '');
            const skuExibicao = core.formatarSkuExibicao(skuBruto);
            if (termoSku && (core.normalizarBuscaSku(skuBruto).includes(termoSku) || core.normalizarBuscaSku(skuExibicao).includes(termoSku))) return true;
            return [item.nome, item.produto, item.produto_bling, item.custo, item.preco, item.imposto]
                .some(valor => core.normalizarBuscaTexto(valor).includes(termoTexto));
        }) : [...listaBase]).filter(item => !lojaSelecionada || obterLojaProduto(item) === lojaSelecionada)
            .sort((a, b) => obterLojaProduto(a).localeCompare(obterLojaProduto(b), undefined, { sensitivity: 'base' }) || compararSku(a, b));
    }

    function renderCustos(resetarPagina = true) {
        const listaCompleta = filtrarCustos();
        if (resetarPagina) state.paginaCustosAtual = 1;
        const inicio = (state.paginaCustosAtual - 1) * constants.itensPorPagina;
        const lista = listaCompleta.slice(inicio, inicio + constants.itensPorPagina);
        elements.tBodyCustos.innerHTML = '';
        if (!lista.length) {
            elements.tBodyCustos.innerHTML = '<tr><td class="muted-cell" colspan="7">Nenhum custo encontrado.</td></tr>';
            elements.paginacaoCustos.innerHTML = '';
            return;
        }
        const fragment = document.createDocumentFragment();
        let lojaGrupoAtual = '';
        lista.forEach(item => {
            const loja = obterLojaProduto(item);
            if (loja !== lojaGrupoAtual) {
                lojaGrupoAtual = loja;
                const group = document.createElement('tr');
                group.className = 'grupo-loja-row';
                group.innerHTML = `<td colspan="7">Loja: ${core.escaparHtml(loja)}</td>`;
                fragment.appendChild(group);
            }
            const row = document.createElement('tr');
            row.innerHTML = [loja, core.formatarSkuExibicao(item.sku), core.obterNomeProdutoCadastro(item),
                core.formatarNumero(item.custo), core.formatarNumero(item.preco), item.imposto || '', item.updated_at || '']
                .map(valor => `<td>${core.escaparHtml(valor)}</td>`).join('');
            fragment.appendChild(row);
        });
        elements.tBodyCustos.appendChild(fragment);
        renderPaginacaoCustos(listaCompleta.length);
    }

    function trocarAba(aba) {
        const custosAtivo = aba === 'custos';
        elements.tabProdutos.classList.toggle('active', !custosAtivo);
        elements.tabCustos.classList.toggle('active', custosAtivo);
        elements.tabProdutos.setAttribute('aria-selected', String(!custosAtivo));
        elements.tabCustos.setAttribute('aria-selected', String(custosAtivo));
        elements.painelProdutos.hidden = custosAtivo;
        elements.painelCustos.hidden = !custosAtivo;
        if (custosAtivo) renderCustos(false);
    }

    function montarSeletorSku() {
        elements.skuSelect.innerHTML = '';
        const padrao = document.createElement('option');
        padrao.value = '';
        padrao.textContent = 'Selecione um SKU para ver MLBs';
        elements.skuSelect.appendChild(padrao);
        [...state.produtos].sort((a, b) => String(a.sku || '').localeCompare(String(b.sku || ''))).forEach(item => {
            const option = document.createElement('option');
            option.value = String(item.sku || '');
            option.textContent = `${core.formatarSkuExibicao(item.sku)} - ${core.obterNomeProdutoCadastro(item)}`;
            elements.skuSelect.appendChild(option);
        });
    }

    function renderDetalhesMlbPorSku(sku) {
        elements.tBodyMlb.innerHTML = '';
        if (!sku) {
            elements.tBodyMlb.innerHTML = '<tr><td class="muted-cell" colspan="4">Selecione um SKU para visualizar os MLBs.</td></tr>';
            return;
        }
        const item = state.produtos.find(produto => String(produto.sku || '') === String(sku));
        if (!item) {
            elements.tBodyMlb.innerHTML = '<tr><td class="muted-cell" colspan="4">SKU não encontrado.</td></tr>';
            return;
        }
        const mlbs = core.splitLista(item.mlb_ids, '|');
        const titulos = core.splitLista(item.titulos_anuncios_mlb, ' || ');
        const fretes = core.splitLista(item.custos_frete_mlb, '|');
        const categoria = String(item.categoria || '').trim();
        const total = Math.max(mlbs.length, titulos.length, fretes.length);
        if (!total) {
            elements.tBodyMlb.innerHTML = '<tr><td class="muted-cell" colspan="4">Este SKU não possui MLB vinculado.</td></tr>';
            return;
        }
        const fragment = document.createDocumentFragment();
        for (let indice = 0; indice < total; indice += 1) {
            const row = document.createElement('tr');
            row.innerHTML = [mlbs[indice] || '', titulos[indice] || core.obterNomeProdutoCadastro(item), fretes[indice] || '', categoria]
                .map(valor => `<td>${core.escaparHtml(valor)}</td>`).join('');
            fragment.appendChild(row);
        }
        elements.tBodyMlb.appendChild(fragment);
    }

    cadastro.custosMlb = Object.freeze({ atualizarFiltroLojasCustos, montarSeletorSku, renderCustos, renderDetalhesMlbPorSku, trocarAba });
    cadastro.components.add('custos-mlb');
})(window);
