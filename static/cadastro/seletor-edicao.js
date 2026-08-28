(function (global) {
    'use strict';

    const statusEl = document.getElementById('status');
    const buscaSku = document.getElementById('buscaSku');
    const listaSkusEl = document.getElementById('listaSkus');
    const listaPaginacaoEl = document.getElementById('listaPaginacao');
    let produtos = [];
    let paginaSkuAtual = 1;
    const SKUS_POR_PAGINA = 50;

    function setStatus(msg, cls) {
        statusEl.className = 'status ' + (cls || '');
        statusEl.textContent = msg || '';
    }

    function normalizarTexto(v) {
        return String(v || '')
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .toLowerCase()
            .trim();
    }

    function skuTextoSegura(valor) {
        if (valor === undefined || valor === null) return '';
        return String(valor).trim();
    }

    function textoProdutoSuspeito(valor) {
        const texto = skuTextoSegura(valor);
        if (!texto) return true;
        if (texto.includes('\n') || texto.includes('\r')) return true;
        if (texto.length > 220) return true;
        if ((texto.match(/;/g) || []).length >= 2) return true;
        return false;
    }

    function obterNomeProdutoLista(item) {
        const candidatos = [item?.nome, item?.produto, item?.produto_bling, item?.titulo];
        for (const candidato of candidatos) {
            const texto = skuTextoSegura(candidato);
            if (!texto) continue;
            if (textoProdutoSuspeito(texto)) continue;
            return texto;
        }
        return '';
    }

    function listaFiltradaSkus() {
        const termo = normalizarTexto(buscaSku.value);
        return termo
            ? produtos.filter(p => {
                const sku = normalizarTexto(p?.sku || '');
                const nome = normalizarTexto(obterNomeProdutoLista(p));
                return sku.includes(termo) || nome.includes(termo);
            })
            : produtos;
    }

    function renderPaginacaoLista(totalItens) {
        listaPaginacaoEl.innerHTML = '';
        const totalPaginas = Math.max(1, Math.ceil(totalItens / SKUS_POR_PAGINA));
        if (paginaSkuAtual > totalPaginas) paginaSkuAtual = totalPaginas;
        if (totalPaginas <= 1) return;

        const btnPrev = document.createElement('button');
        btnPrev.textContent = '? Anterior';
        btnPrev.disabled = paginaSkuAtual === 1;
        btnPrev.addEventListener('click', () => {
            paginaSkuAtual -= 1;
            renderListaSkus();
        });
        listaPaginacaoEl.appendChild(btnPrev);

        const inicio = Math.max(1, paginaSkuAtual - 2);
        const fim = Math.min(totalPaginas, paginaSkuAtual + 2);
        for (let i = inicio; i <= fim; i++) {
            const btn = document.createElement('button');
            btn.textContent = String(i);
            if (i === paginaSkuAtual) btn.classList.add('ativo');
            btn.addEventListener('click', () => {
                paginaSkuAtual = i;
                renderListaSkus();
            });
            listaPaginacaoEl.appendChild(btn);
        }

        const btnNext = document.createElement('button');
        btnNext.textContent = 'Próximo ?';
        btnNext.disabled = paginaSkuAtual === totalPaginas;
        btnNext.addEventListener('click', () => {
            paginaSkuAtual += 1;
            renderListaSkus();
        });
        listaPaginacaoEl.appendChild(btnNext);

        const de = (paginaSkuAtual - 1) * SKUS_POR_PAGINA + 1;
        const ate = Math.min(totalItens, paginaSkuAtual * SKUS_POR_PAGINA);
        const info = document.createElement('span');
        info.textContent = `${de}-${ate} de ${totalItens}`;
        listaPaginacaoEl.appendChild(info);
    }

    function renderListaSkus() {
        const filtrada = listaFiltradaSkus()
            .sort((a, b) => String(a.sku || '').localeCompare(String(b.sku || ''), undefined, { numeric: true, sensitivity: 'base' }));
        listaSkusEl.innerHTML = '';

        if (!filtrada.length) {
            listaSkusEl.innerHTML = '<div class="sku-vazio">Nenhum SKU encontrado.</div>';
            listaPaginacaoEl.innerHTML = '';
            return;
        }

        const inicio = (paginaSkuAtual - 1) * SKUS_POR_PAGINA;
        const fim = inicio + SKUS_POR_PAGINA;
        const paginaItens = filtrada.slice(inicio, fim);

        const frag = document.createDocumentFragment();
        paginaItens.forEach(p => {
            const item = document.createElement('div');
            item.className = 'sku-item';
            item.innerHTML = `<strong>${p.sku || ''}</strong> - ${obterNomeProdutoLista(p)}`;
            item.addEventListener('click', () => {
                const sku = String(p.sku || '').trim();
                if (!sku) return;
                try {
                    localStorage.setItem('cadastro_editar_sku', sku);
                    localStorage.setItem('cadastro_editar_item', JSON.stringify(p || {}));
                    localStorage.setItem('cadastro_editar_lista', JSON.stringify(produtos || []));
                } catch (_e) {
                    // Ignora falhas de armazenamento local.
                }
                window.location.href = `/cadastro_editar_item.html?sku=${encodeURIComponent(sku)}`;
            });
            frag.appendChild(item);
        });
        listaSkusEl.appendChild(frag);
        renderPaginacaoLista(filtrada.length);
    }

    async function carregarListaSkus() {
        setStatus('Carregando SKUs...', 'loading');
        try {
            const resp = await fetch('/api/cadastro/produtos', { headers: obterAuthHeaders() });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            produtos = await resp.json();
            paginaSkuAtual = 1;
            renderListaSkus();
            setStatus(`SKUs disponíveis: ${produtos.length}`, 'success');
        } catch (e) {
            setStatus(`Erro ao carregar lista de SKUs: ${e.message}`, 'error');
        }
    }

    buscaSku.addEventListener('input', () => {
        paginaSkuAtual = 1;
        renderListaSkus();
    });

    if (global.verificarSessao()) {
        carregarListaSkus();
    }
})(window);
