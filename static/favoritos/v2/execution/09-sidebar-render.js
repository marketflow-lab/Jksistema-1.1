// Extracted from 07-execucao-render-layout.js lines 2753-2942.
        function formatarPrecoFavoritosMl(valor) {
            if (valor === null || valor === undefined || valor === '') return '';
            const numero = Number(valor);
            if (!Number.isFinite(numero)) return String(valor);
            return numero.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
        }
        function renderizarFavoritosSkuSidebar() {
            if (!favMlSkuSidebarListEl || !favMlSkuSidebarEmptyEl || !favMlSkuSidebarCountEl) return;
            const todosItens = montarItensSkuSidebarMercadoLivre();
            const termoBusca = favMlSkuSidebarSearchEl ? favMlSkuSidebarSearchEl.value || '' : '';
            const itensFiltrados = filtrarItensSkuSidebarMercadoLivre(todosItens, termoBusca);
            const itens = aplicarSkuExatoHistoricoSidebar(itensFiltrados, termoBusca);
            const selecionadoChave = skuChaveSku(favMlSkuSelecionado);
            const selecionadoLoja = skuNormalizarLoja(favMlLojaSelecionada);
            const selecionadoTemRanking = selecionadoChave && favMlHistoricoExecucaoSelecionadaId && obterGrupoRankingFavoritosSku(favMlSkuSelecionado);
            const selecionadoTemHistorico = selecionadoChave && obterHistoricoMaisRecenteSku(favMlSkuSelecionado);

            favMlSkuSidebarListEl.innerHTML = '';
            favMlSkuSidebarCountEl.textContent = todosItens.length
                ? (itens.length === todosItens.length ? `${todosItens.length} SKU(s)` : `${itens.length} de ${todosItens.length} SKU(s)`)
                : '';
            favMlSkuSidebarEmptyEl.textContent = termoBusca.trim()
                ? 'Nenhum SKU encontrado para essa pesquisa.'
                : favoritosWarningLojaAtual
                ? favoritosWarningLojaAtual
                : mlSkuLojaSelecionada
                ? 'Nenhum SKU encontrado nos anuncios ativos desta loja.'
                : 'Escolha uma loja integrada para carregar os SKUs dos anuncios ativos.';
            favMlSkuSidebarEmptyEl.classList.toggle('hidden', itens.length > 0);
            if (deveBuscarSkuRemotoParaTermo(itensFiltrados, termoBusca)) {
                agendarBuscaRemotaSkuSidebarMercadoLivre(termoBusca);
            }

            if (selecionadoChave && !selecionadoTemRanking && !selecionadoTemHistorico && !todosItens.some(item => skuChaveSku(item.sku) === selecionadoChave && (!selecionadoLoja || skuNormalizarLoja(item.loja) === selecionadoLoja))) {
                favMlSkuSelecionado = '';
                favMlLojaSelecionada = '';
                favMlHistoricoExecucaoSelecionadaId = '';
                favMlAnunciosSkuAtual = [];
                renderizarFavoritosAnunciosMl([], '');
                renderizarFavoritosOutrosAnuncios('');
            }

            itens.forEach(item => {
                const button = document.createElement('button');
                button.type = 'button';
                const itemAtivo = skuChaveSku(item.sku) === skuChaveSku(favMlSkuSelecionado)
                    && (!favMlLojaSelecionada || skuNormalizarLoja(item.loja) === skuNormalizarLoja(favMlLojaSelecionada));
                button.className = 'ml-sku-sidebar-item' + (itemAtivo ? ' is-active' : '');

                const main = document.createElement('span');
                main.className = 'ml-sku-sidebar-main';

                const codigo = document.createElement('span');
                codigo.className = 'ml-sku-sidebar-code';
                codigo.textContent = item.sku;

                const titulo = document.createElement('span');
                titulo.className = 'ml-sku-sidebar-name';
                titulo.textContent = item.titulo || 'SKU encontrado em anuncio ativo';

                const meta = document.createElement('span');
                meta.className = 'ml-sku-sidebar-meta';
                const partes = [];
                if (item.loja) partes.push(`Loja: ${item.loja}`);
                if (item.totalAnuncios) partes.push(`${item.totalAnuncios} anuncio(s)`);
                if (item.itemIds && item.itemIds.length) partes.push(item.itemIds.slice(0, 2).join(', '));
                meta.textContent = partes.join(' | ');

                main.appendChild(codigo);
                main.appendChild(titulo);
                if (meta.textContent) main.appendChild(meta);
                button.appendChild(main);
                button.addEventListener('click', () => {
                    if (itemAtivo) {
                        if (typeof limparFavoritosSkuSelecionado === 'function') {
                            limparFavoritosSkuSelecionado();
                        }
                        return;
                    }
                    carregarFavoritosAnunciosSku(item.sku, item.loja, { itemSidebar: item });
                });
                favMlSkuSidebarListEl.appendChild(button);
            });
        }

        function criarCelulaTextoFavoritos(valor, opcoes = {}) {
            const td = document.createElement('td');
            const texto = document.createElement('div');
            texto.className = 'favoritos-ml-cell-text'
                + (opcoes.long ? ' is-long' : '')
                + (opcoes.nowrap ? ' is-nowrap' : '');
            texto.textContent = valor === null || valor === undefined ? '' : String(valor);
            td.appendChild(texto);
            return td;
        }

        function vendedorInternacionalFavoritos(...valores) {
            const texto = valores
                .map(valor => String(valor || ''))
                .join(' ')
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .toLowerCase();
            return /\b(vendedor\s+internacional|internacional|international\s+seller|cross\s*border)\b/.test(texto);
        }

        function criarCelulaMlbLojaFavoritos(mlb, loja, status = '', tipo = '', vendedor = '') {
            const td = document.createElement('td');
            td.className = 'ml-favoritos-mlb-cell';
            const codigo = document.createElement('span');
            codigo.className = 'ml-favoritos-mlb-main';
            codigo.textContent = mlb === null || mlb === undefined ? '' : String(mlb);
            td.appendChild(codigo);
            const lojaTexto = String(loja || '').trim();
            if (lojaTexto) {
                const lojaEl = document.createElement('span');
                lojaEl.className = 'ml-favoritos-mlb-loja';
                if (vendedorInternacionalFavoritos(lojaTexto, vendedor)) {
                    const icone = document.createElement('span');
                    icone.className = 'ml-favoritos-seller-international';
                    icone.textContent = '✈';
                    icone.title = 'Vendedor internacional';
                    lojaEl.appendChild(icone);
                    lojaEl.appendChild(document.createTextNode(` ${lojaTexto}`));
                } else {
                    lojaEl.textContent = lojaTexto;
                }
                td.appendChild(lojaEl);
            }
            const statusTexto = String(status || '').trim();
            if (statusTexto) {
                const statusEl = document.createElement('span');
                statusEl.className = 'ml-favoritos-mlb-status';
                statusEl.textContent = statusTexto;
                td.appendChild(statusEl);
            }
            const tipoTexto = String(tipo || '').trim();
            if (tipoTexto) {
                const tipoEl = document.createElement('span');
                tipoEl.className = 'ml-favoritos-mlb-tipo';
                tipoEl.textContent = tipoTexto;
                td.appendChild(tipoEl);
            }
            const botaoVendedor = criarBotaoIgnorarVendedorFavoritos(vendedor);
            if (botaoVendedor) {
                const action = document.createElement('div');
                action.className = 'ml-favoritos-seller-action';
                action.appendChild(botaoVendedor);
                td.appendChild(action);
            }
            return td;
        }

        function obterNomeLojaVendedoraHistoricoFavoritos(anuncio) {
            if (!anuncio || typeof anuncio !== 'object') return '';
            const seller = anuncio.seller && typeof anuncio.seller === 'object' ? anuncio.seller : {};
            const sellerInfo = anuncio.seller_info && typeof anuncio.seller_info === 'object' ? anuncio.seller_info : {};
            const candidatos = [
                anuncio.loja_vendedora,
                anuncio.vendedor,
                anuncio.seller_name,
                anuncio.sellerName,
                anuncio.seller_nickname,
                anuncio.sellerNickname,
                anuncio.nickname,
                anuncio.official_store_name,
                anuncio.officialStoreName,
                seller.nickname,
                seller.name,
                seller.seller_nickname,
                sellerInfo.nickname,
                sellerInfo.name,
                sellerInfo.seller_nickname
            ];
            for (const valor of candidatos) {
                const nome = limparNomeLojaVendedoraHistoricoFavoritos(valor);
                if (vendedorValido(nome)) return nome;
            }
            return '';
        }

        function limparNomeLojaVendedoraHistoricoFavoritos(valor) {
            return String(valor || '')
                .replace(/^(vendido\s+por|loja\s+oficial|oficial\s+loja)\s*/i, '')
                .replace(/&quot;|\\\"/g, '"')
                .replace(/\s+/g, ' ')
                .trim();
        }
