/**
 * Favoritos - cola uma execucao estatica do historico na planilha da loja.
 */
(function () {
    'use strict';

    function statusHistoricoPlanilha(texto, erro = false, statusElOverride = null) {
        const statusEl = statusElOverride || document.getElementById('ml-links-alinhados-status');
        if (!statusEl) return;
        statusEl.textContent = texto || '';
        statusEl.classList.toggle('is-error', !!erro);
        statusEl.classList.toggle('is-success', !!texto && !erro);
    }

    function texto(valor) {
        return String(valor || '').replace(/\s+/g, ' ').trim();
    }

    function clonarAnuncioPlanilha(anuncio) {
        const item = anuncio && typeof anuncio === 'object' ? anuncio : {};
        return {
            id: texto(item.id || item.mlb || item.item_id),
            mlb: texto(item.mlb || item.id || item.item_id),
            item_id: texto(item.item_id || item.id || item.mlb),
            url: texto(item.url || item.permalink || item.link),
            permalink: texto(item.permalink || item.url || item.link),
            link: texto(item.link || item.url || item.permalink),
            titulo: texto(item.titulo || item.title),
            title: texto(item.title || item.titulo),
            vendedor: texto(item.vendedor || item.seller || item.seller_name),
            seller: texto(item.seller || item.vendedor || item.seller_name),
            price: item.price ?? '',
            preco: item.preco ?? '',
            valor: item.valor ?? '',
            standard_price: item.standard_price ?? '',
            base_price: item.base_price ?? '',
            preco_original: item.preco_original ?? '',
            original_price: item.original_price ?? '',
            preco_promocional: item.preco_promocional ?? '',
            promotional_price: item.promotional_price ?? '',
            promotion_price: item.promotion_price ?? '',
            deal_price: item.deal_price ?? '',
            discounted_price: item.discounted_price ?? '',
            custo: item.custo ?? item.custo_unitario ?? item.custo_produto ?? item.preco_custo ?? item.valor_custo ?? '',
            custo_unitario: item.custo_unitario ?? item.custo ?? item.custo_produto ?? item.preco_custo ?? item.valor_custo ?? '',
            custo_produto: item.custo_produto ?? item.custo ?? item.custo_unitario ?? item.preco_custo ?? item.valor_custo ?? '',
            preco_custo: item.preco_custo ?? item.custo ?? item.custo_unitario ?? item.custo_produto ?? item.valor_custo ?? '',
            valor_custo: item.valor_custo ?? item.custo ?? item.custo_unitario ?? item.custo_produto ?? item.preco_custo ?? '',
            custo_frete: item.custo_frete ?? '',
            sale_price: item.sale_price && typeof item.sale_price === 'object' ? { ...item.sale_price } : item.sale_price ?? '',
            discount_pct: item.discount_pct ?? item.desconto_percentual ?? '',
            desconto_percentual: item.desconto_percentual ?? item.discount_pct ?? ''
        };
    }

    function clonarRelatorioPlanilha(relatorio) {
        if (!relatorio || typeof relatorio !== 'object') return relatorio || null;
        return {
            titulo: texto(relatorio.titulo || relatorio.title),
            resumo: texto(relatorio.resumo || relatorio.mensagem || relatorio.status),
            mensagem: texto(relatorio.mensagem || relatorio.resumo),
            detalhe: texto(relatorio.detalhe || relatorio.detail || relatorio.mensagem || relatorio.resumo),
            status: texto(relatorio.status || relatorio.tipo),
            tipo: texto(relatorio.tipo || relatorio.status)
        };
    }

    function montarPayloadHistoricoPlanilha(item) {
        const historico = item && typeof item === 'object' ? item : {};
        const vinculos = Array.isArray(historico.vinculos) ? historico.vinculos : [];
        return {
            sku: texto(historico.sku),
            titulo: texto(historico.titulo),
            loja: texto(historico.loja),
            usuario: texto(historico.usuario),
            data_iso: texto(historico.data_iso),
            mensagem_final: texto(historico.mensagem_final),
            vinculos: vinculos.map((vinculo, index) => {
                const itemVinculo = vinculo && typeof vinculo === 'object' ? vinculo : {};
                return {
                    ordem: Number(itemVinculo.ordem || itemVinculo.rank || itemVinculo.posicao || index + 1) || index + 1,
                    sku: texto(itemVinculo.sku || historico.sku),
                    loja: texto(itemVinculo.loja || historico.loja),
                    itemId: texto(itemVinculo.itemId || itemVinculo.item_id),
                    status: texto(itemVinculo.status || itemVinculo.tipo),
                    status_texto: texto(itemVinculo.status_texto || itemVinculo.mensagem),
                    nosso: clonarAnuncioPlanilha(itemVinculo.nosso || itemVinculo.anuncio),
                    base: clonarAnuncioPlanilha(itemVinculo.base || itemVinculo.ranking),
                    simulacao: itemVinculo.simulacao && typeof itemVinculo.simulacao === 'object' ? { ...itemVinculo.simulacao } : {},
                    relatorio_inicial: clonarRelatorioPlanilha(itemVinculo.relatorio_inicial),
                    relatorio_final: clonarRelatorioPlanilha(itemVinculo.relatorio_final)
                };
            })
        };
    }

    function obterLojaSelecionadaPlanilha(item) {
        const bruto = (
            (typeof mlSkuLojaSelecionada !== 'undefined' && mlSkuLojaSelecionada)
            || (typeof skuLojaSelecionada !== 'undefined' && skuLojaSelecionada)
            || ''
        );
        if (typeof favoritosEhTodasLojas === 'function' && favoritosEhTodasLojas(bruto)) {
            throw new Error('Selecione uma loja especifica antes de colar na planilha.');
        }
        const lojaApi = typeof favoritosLojaSelecionadaParaApi === 'function'
            ? favoritosLojaSelecionadaParaApi(bruto)
            : texto(bruto);
        const loja = lojaApi || texto(item && item.loja);
        if (!loja || (typeof favoritosEhTodasLojas === 'function' && favoritosEhTodasLojas(loja))) {
            throw new Error('Selecione uma loja especifica antes de colar na planilha.');
        }
        return loja;
    }

    async function colarHistoricoPlanilha(item, botao, statusElOverride = null) {
        const textoOriginal = botao ? botao.textContent : '';
        try {
            const loja = obterLojaSelecionadaPlanilha(item);
            const historico = montarPayloadHistoricoPlanilha(item);
            if (!historico.sku) throw new Error('Historico sem SKU.');
            if (!historico.vinculos.length) throw new Error('Historico sem anuncios relacionados.');
            if (botao) {
                botao.disabled = true;
                botao.textContent = 'Colando...';
            }
            statusHistoricoPlanilha(`Colando historico do SKU ${historico.sku} na planilha de ${loja}...`, false, statusElOverride);
            const response = await fetch('/api/favoritos/planilhas-lojas/colar-historico', {
                method: 'POST',
                headers: typeof headersJsonAutenticado === 'function' ? headersJsonAutenticado() : { 'Content-Type': 'application/json' },
                body: JSON.stringify({ loja, historico })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.success) {
                throw new Error(data.detail || data.message || `HTTP ${response.status}`);
            }
            const avisos = Array.isArray(data.avisos) && data.avisos.length
                ? ` Avisos: ${data.avisos.join(' | ')}`
                : '';
            statusHistoricoPlanilha(`Historico do SKU ${data.sku || historico.sku} colado na linha ${data.linha}. ${data.pares_colados || 0} vinculo(s).${avisos}`, false, statusElOverride);
        } catch (err) {
            statusHistoricoPlanilha(`Erro ao colar na planilha: ${err && err.message ? err.message : err}`, true, statusElOverride);
        } finally {
            if (botao) {
                botao.disabled = false;
                botao.textContent = textoOriginal || 'Colar na planilha';
            }
        }
    }

    function favoritosCriarBotaoColarHistoricoPlanilha(item, opcoes = {}) {
        const statusEl = opcoes && opcoes.statusEl || null;
        const botao = document.createElement('button');
        botao.type = 'button';
        botao.className = 'btn-back ml-links-alinhados-colar-planilha';
        botao.textContent = 'Colar na planilha';
        botao.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            colarHistoricoPlanilha(item, botao, statusEl);
        });
        return botao;
    }

    window.favoritosCriarBotaoColarHistoricoPlanilha = favoritosCriarBotaoColarHistoricoPlanilha;
    window.favoritosColarHistoricoPlanilha = colarHistoricoPlanilha;
})();
