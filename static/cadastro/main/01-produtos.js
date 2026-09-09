(function (global) {
    'use strict';

    const cadastro = global.JKCadastro;
    if (!cadastro || !cadastro.components.has('core')) throw new Error('Núcleo do Cadastro não inicializado.');
    if (cadastro.components.has('produtos')) return;

    const { elements, state, constants } = cadastro.runtime;
    const core = cadastro.core;
    const storeTools = global.JKCadastroStore;
    const MAX_FOTOS_SIMULTANEAS = 6;
    const fotosTabelaAtivas = new Set();
    const fotosTabelaControllers = new Set();
    const fotosTabelaFila = [];
    let fotosTabelaSeq = 0;
    let fotosTabelaEmAndamento = 0;

    function authHeaders(extra) {
        if (typeof global.obterAuthHeaders !== 'function') throw new Error('Autenticação indisponível.');
        return global.obterAuthHeaders(extra);
    }

    function revogarFotosTabela() {
        fotosTabelaSeq += 1;
        fotosTabelaFila.length = 0;
        fotosTabelaControllers.forEach(controller => controller.abort());
        fotosTabelaControllers.clear();
        fotosTabelaAtivas.forEach(foto => storeTools.revogarFotoCarregada(foto));
        fotosTabelaAtivas.clear();
    }

    function exibirMiniaturaFoto(container, foto) {
        const link = document.createElement('a');
        link.className = 'foto-link';
        link.href = foto.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        const image = document.createElement('img');
        image.className = 'foto-thumb';
        image.src = foto.url;
        image.alt = 'Foto do SKU';
        link.appendChild(image);
        container.innerHTML = '';
        container.removeAttribute('data-foto-url');
        container.appendChild(link);
    }

    async function hidratarFotoTabela(container, requestSeq) {
        const url = String(container && container.dataset && container.dataset.fotoUrl || '').trim();
        if (!url) return;
        const controller = typeof global.AbortController === 'function' ? new global.AbortController() : null;
        if (controller) fotosTabelaControllers.add(controller);
        try {
            const foto = await storeTools.carregarFotoAutenticada(
                url,
                authHeaders,
                controller ? { signal: controller.signal } : undefined,
            );
            if (requestSeq !== fotosTabelaSeq || !elements.tBody.contains(container)) {
                storeTools.revogarFotoCarregada(foto);
                return;
            }
            fotosTabelaAtivas.add(foto);
            exibirMiniaturaFoto(container, foto);
        } catch (error) {
            if (requestSeq !== fotosTabelaSeq || !elements.tBody.contains(container)) return;
            if (error && error.name === 'AbortError') return;
            container.innerHTML = '<span class="foto-empty">Foto indisponível</span>';
            container.removeAttribute('data-foto-url');
        } finally {
            if (controller) fotosTabelaControllers.delete(controller);
        }
    }

    function processarFilaFotosTabela() {
        while (fotosTabelaEmAndamento < MAX_FOTOS_SIMULTANEAS && fotosTabelaFila.length) {
            const tarefa = fotosTabelaFila.shift();
            if (!tarefa || tarefa.requestSeq !== fotosTabelaSeq || !elements.tBody.contains(tarefa.container)) continue;
            fotosTabelaEmAndamento += 1;
            void hidratarFotoTabela(tarefa.container, tarefa.requestSeq).finally(() => {
                fotosTabelaEmAndamento = Math.max(0, fotosTabelaEmAndamento - 1);
                processarFilaFotosTabela();
            });
        }
    }

    function hidratarFotosTabela() {
        const requestSeq = fotosTabelaSeq;
        elements.tBody.querySelectorAll('[data-foto-url]').forEach(container => {
            fotosTabelaFila.push({ container, requestSeq });
        });
        processarFilaFotosTabela();
    }

    function obterLarguraColuna(coluna) {
        const largura = parseInt(state.largurasColunas[coluna], 10);
        return Number.isFinite(largura) ? largura : 180;
    }

    function aplicarLarguraColuna(indice, coluna, width, salvar = false) {
        const largura = Math.max(90, parseInt(width, 10) || 180);
        state.largurasColunas[coluna] = largura;
        if (salvar) core.salvarLarguras();
        const colElement = elements.tCols.children[indice];
        if (colElement) colElement.style.width = `${largura}px`;
        const th = elements.tHead.querySelector(`tr th:nth-child(${indice + 1})`);
        if (th) {
            th.style.width = `${largura}px`;
            th.style.minWidth = `${largura}px`;
            th.style.maxWidth = `${largura}px`;
        }
        elements.tBody.querySelectorAll('tr').forEach(row => {
            const cell = row.children[indice];
            if (!cell) return;
            cell.style.width = `${largura}px`;
            cell.style.minWidth = `${largura}px`;
            cell.style.maxWidth = `${largura}px`;
        });
    }

    function iniciarResizeColuna(event, indice, coluna) {
        event.preventDefault();
        const startX = event.clientX;
        const startWidth = obterLarguraColuna(coluna);
        function onMove(moveEvent) {
            aplicarLarguraColuna(indice, coluna, Math.max(90, startWidth + moveEvent.clientX - startX));
        }
        function onUp() {
            global.removeEventListener('mousemove', onMove);
            global.removeEventListener('mouseup', onUp);
            core.salvarLarguras();
        }
        global.addEventListener('mousemove', onMove);
        global.addEventListener('mouseup', onUp);
    }

    function renderCabecalho(colunas) {
        elements.tHead.innerHTML = '';
        elements.tCols.innerHTML = '';
        if (!colunas.length) return;
        colunas.forEach(coluna => {
            const col = document.createElement('col');
            col.style.width = `${obterLarguraColuna(coluna)}px`;
            elements.tCols.appendChild(col);
        });
        const row = document.createElement('tr');
        colunas.forEach((coluna, indice) => {
            const largura = obterLarguraColuna(coluna);
            const th = document.createElement('th');
            th.style.width = `${largura}px`;
            th.style.minWidth = `${largura}px`;
            th.style.maxWidth = `${largura}px`;
            const content = document.createElement('div');
            content.className = 'th-content';
            content.textContent = core.normalizarLegendaColuna(coluna);
            const resizer = document.createElement('div');
            resizer.className = 'th-resizer';
            resizer.addEventListener('mousedown', event => iniciarResizeColuna(event, indice, coluna));
            content.appendChild(resizer);
            th.appendChild(content);
            row.appendChild(th);
        });
        elements.tHead.appendChild(row);
    }

    function compararSku(a, b) {
        return String(a.sku || '').localeCompare(String(b.sku || ''), undefined, { numeric: true, sensitivity: 'base' });
    }

    function mudarPagina(pagina) {
        state.paginaAtual = pagina;
        renderTabela(false);
        global.scrollTo(0, 0);
    }

    function renderPaginacao(totalItens) {
        elements.paginacao.innerHTML = '';
        const totalPaginas = Math.ceil(totalItens / constants.itensPorPagina);
        if (totalPaginas <= 1) return;
        const anterior = document.createElement('button');
        anterior.textContent = '← Anterior';
        anterior.disabled = state.paginaAtual === 1;
        anterior.addEventListener('click', () => mudarPagina(state.paginaAtual - 1));
        elements.paginacao.appendChild(anterior);
        const inicio = Math.max(1, state.paginaAtual - 2);
        const fim = Math.min(totalPaginas, state.paginaAtual + 2);
        if (inicio > 1) {
            const primeira = document.createElement('button');
            primeira.textContent = '1';
            primeira.addEventListener('click', () => mudarPagina(1));
            elements.paginacao.appendChild(primeira);
            if (inicio > 2) elements.paginacao.insertAdjacentHTML('beforeend', '<span>…</span>');
        }
        for (let pagina = inicio; pagina <= fim; pagina += 1) {
            const button = document.createElement('button');
            button.textContent = pagina;
            if (pagina === state.paginaAtual) button.classList.add('ativo');
            button.addEventListener('click', () => mudarPagina(pagina));
            elements.paginacao.appendChild(button);
        }
        if (fim < totalPaginas) {
            if (fim < totalPaginas - 1) elements.paginacao.insertAdjacentHTML('beforeend', '<span>…</span>');
            const ultima = document.createElement('button');
            ultima.textContent = totalPaginas;
            ultima.addEventListener('click', () => mudarPagina(totalPaginas));
            elements.paginacao.appendChild(ultima);
        }
        const proxima = document.createElement('button');
        proxima.textContent = 'Próximo →';
        proxima.disabled = state.paginaAtual === totalPaginas;
        proxima.addEventListener('click', () => mudarPagina(state.paginaAtual + 1));
        elements.paginacao.appendChild(proxima);
        const info = document.createElement('span');
        const de = (state.paginaAtual - 1) * constants.itensPorPagina + 1;
        const ate = Math.min(state.paginaAtual * constants.itensPorPagina, totalItens);
        info.textContent = `${de} à ${ate} de ${totalItens}`;
        elements.paginacao.appendChild(info);
    }

    function filtrarProdutos() {
        const termoTexto = core.normalizarBuscaTexto(elements.filtro.value);
        const termoSku = core.normalizarBuscaSku(elements.filtro.value);
        if (!termoTexto) return [...state.produtos].sort(compararSku);
        return state.produtos.map((produto, indice) => {
            const skuBruto = String(produto && produto.sku || '');
            const skuExibicao = core.formatarSkuExibicao(skuBruto);
            const skuNormalizado = core.normalizarBuscaSku(skuBruto);
            const exibicaoNormalizada = core.normalizarBuscaSku(skuExibicao);
            let prioridade = null;
            if (termoSku && (skuNormalizado === termoSku || exibicaoNormalizada === termoSku)) prioridade = 0;
            else if (termoSku && (skuNormalizado.startsWith(termoSku) || exibicaoNormalizada.startsWith(termoSku))) prioridade = 1;
            else if (termoSku && (skuNormalizado.includes(termoSku) || exibicaoNormalizada.includes(termoSku))) prioridade = 2;
            if (prioridade === null) {
                const encontrou = Object.values(produto || {}).some(valor => core.normalizarBuscaTexto(valor).includes(termoTexto));
                if (!encontrou) return null;
                prioridade = 3;
            }
            return { produto, prioridade, indice };
        }).filter(Boolean).sort((a, b) => a.prioridade - b.prioridade || compararSku(a.produto, b.produto) || a.indice - b.indice)
            .map(item => item.produto);
    }

    function renderTabela(resetarPagina = true) {
        revogarFotosTabela();
        const listaCompleta = filtrarProdutos();
        if (resetarPagina) state.paginaAtual = 1;
        state.paginaAtual = Math.max(1, Math.min(state.paginaAtual, Math.ceil(listaCompleta.length / constants.itensPorPagina) || 1));
        renderPaginacao(listaCompleta.length);
        const inicio = (state.paginaAtual - 1) * constants.itensPorPagina;
        const lista = listaCompleta.slice(inicio, inicio + constants.itensPorPagina);
        const novasColunas = core.construirColunas(lista.length ? lista : listaCompleta);
        const novaKey = novasColunas.join('|');
        if (novaKey !== state.colunasTabelaKey) {
            state.colunasTabela = novasColunas;
            state.colunasTabelaKey = novaKey;
            renderCabecalho(novasColunas);
        }
        elements.tBody.innerHTML = '';
        if (!lista.length) {
            const row = document.createElement('tr');
            row.innerHTML = `<td class="muted-cell" colspan="${Math.max(state.colunasTabela.length, 1)}">Nenhum produto cadastrado.</td>`;
            elements.tBody.appendChild(row);
            return;
        }
        const fragment = document.createDocumentFragment();
        lista.forEach(item => {
            const row = document.createElement('tr');
            row.className = state.storeIdSelecionado ? 'produto-row-editavel' : '';
            row.dataset.sku = String(item.sku || '').trim();
            row.dataset.storeId = String(item.store_id || state.storeIdSelecionado || '').trim();
            row.title = state.storeIdSelecionado && row.dataset.sku ? `Editar SKU ${core.formatarSkuExibicao(row.dataset.sku)}` : '';
            row.innerHTML = state.colunasTabela.map(coluna =>
                `<td class="${coluna === 'foto' ? 'foto-td' : ''}">${core.renderConteudoCelula(coluna, item)}</td>`
            ).join('');
            fragment.appendChild(row);
        });
        elements.tBody.appendChild(fragment);
        hidratarFotosTabela();
        state.colunasTabela.forEach((coluna, indice) => aplicarLarguraColuna(indice, coluna, obterLarguraColuna(coluna)));
        renderPaginacao(listaCompleta.length);
    }

    function produtoPorLinha(row) {
        const sku = String(row && row.dataset && row.dataset.sku || '');
        const storeId = String(row && row.dataset && row.dataset.storeId || '');
        return state.produtos.find(item => String(item && item.sku || '') === sku
            && String(item && item.store_id || state.storeIdSelecionado || '') === storeId) || null;
    }

    function tratarCliqueTabela(event) {
        if (!state.storeIdSelecionado) return;
        const link = event.target.closest && event.target.closest('.sku-edit-link');
        if (link) {
            const row = link.closest('tr[data-sku]');
            const item = produtoPorLinha(row);
            if (item) core.salvarProdutoParaEdicao(item);
            return;
        }
        if (event.target.closest && event.target.closest('a, button, input, select, textarea, [role="button"]')) return;
        const row = event.target.closest && event.target.closest('tr[data-sku]');
        if (!row || !elements.tBody.contains(row)) return;
        const item = produtoPorLinha(row);
        if (item) core.abrirProdutoParaEdicao(item);
    }

    function vincularNavegacaoLinhas() {
        if (elements.tBody.dataset.editNavigationBound === 'true') return;
        elements.tBody.dataset.editNavigationBound = 'true';
        elements.tBody.addEventListener('click', tratarCliqueTabela);
    }

    if (typeof global.addEventListener === 'function') global.addEventListener('beforeunload', revogarFotosTabela);

    cadastro.produtosTabela = Object.freeze({
        compararSku, hidratarFotosTabela, renderTabela, revogarFotosTabela, tratarCliqueTabela, vincularNavegacaoLinhas,
    });
    cadastro.components.add('produtos');
})(window);
