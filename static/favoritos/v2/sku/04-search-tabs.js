(function (global) {
        'use strict';

        const feature = global.FavoritosV2 && global.FavoritosV2.sku;
        if (!feature || !feature.__runtimeInitialized) {
            throw new Error('Runtime do SKU nao inicializado.');
        }
        if (feature.components.has('searchTabs')) return;

        function garantirSidebarSkuUnico() {
            const tabsEl = document.querySelector('.tabs');
            const containerEl = document.querySelector('.container');
            if (mlSkuSidebarSectionEl && tabsEl && containerEl && mlSkuSidebarSectionEl.parentElement !== containerEl) {
                tabsEl.insertAdjacentElement('afterend', mlSkuSidebarSectionEl);
            }
            document.querySelectorAll('.favoritos-ml-sidebar').forEach(sidebar => {
                sidebar.remove();
            });
        }



        async function buscar() {
            const termo = termoEl.value.trim();
            if (!termo) {
                alert('Informe um termo ou link para pesquisar.');
                return;
            }

            if (!/^https?:\/\//i.test(termo) && hasInternalBrowserApi) {
                mlSearchTermInput.value = termo;
                mudarAba('navegador');
                await pesquisarNoMercadoLivreNoPrograma();
                return;
            }

            statusEl.textContent = 'Buscando. Aguarde...';
            resultadosEl.classList.add('hidden');
            topBody.innerHTML = '';
            allBody.innerHTML = '';
            linkProdutoInfo.innerHTML = '';
            linkProdutoUrls.innerHTML = '';

            try {
                const response = await fetch('/api/favoritos/pesquisar', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ termo: termo })
                });

                if (!response.ok) {
                    let detail = 'Erro ao buscar.';
                    try {
                        const errJson = await response.json();
                        detail = errJson.detail || detail;
                    } catch (e) {}
                    throw new Error(detail);
                }

                const data = await response.json();

                if (data.tipo === 'link_produto') {
                    displayLinkProduto(data);
                } else if (data.tipo === 'busca_termo') {
                    displayBuscaTermos(data);
                } else {
                    throw new Error('Tipo de resposta desconhecido: ' + data.tipo);
                }

                resultadosEl.classList.remove('hidden');
            } catch (err) {
                statusEl.textContent = '';
                alert(err.message || 'Erro inesperado.');
            }
        }

        function displayLinkProduto(data) {
            statusEl.textContent = 'Produto encontrado. Selecione uma estratégia de busca:';

            buscaTermoWrap.classList.add('hidden');
            linkProdutoWrap.classList.remove('hidden');

            const dados = data.dados || {};
            if (dados.titulo) {
                const tr = document.createElement('tr');
                tr.innerHTML = `<td><strong>Título:</strong></td><td>${dados.titulo}</td>`;
                linkProdutoInfo.appendChild(tr);
            }
            if (dados.codigo) {
                const tr = document.createElement('tr');
                tr.innerHTML = `<td><strong>Código OEM:</strong></td><td>${dados.codigo}</td>`;
                linkProdutoInfo.appendChild(tr);
            }
            if (dados.veiculo) {
                const tr = document.createElement('tr');
                tr.innerHTML = `<td><strong>Veículo:</strong></td><td>${dados.veiculo}</td>`;
                linkProdutoInfo.appendChild(tr);
            }
            if (dados.anos && dados.anos.length > 0) {
                const tr = document.createElement('tr');
                tr.innerHTML = `<td><strong>Anos:</strong></td><td>${dados.anos.join(', ')}</td>`;
                linkProdutoInfo.appendChild(tr);
            }

            const urls = data.urls_busca || [];
            linkProdutoUrls.innerHTML = '';
            urls.forEach(item => {
                const button = document.createElement('a');
                button.href = item.url;
                button.target = '_blank';
                button.rel = 'noopener';
                button.className = 'search-button';
                button.innerHTML = `<strong>${item.label}</strong><span class="tipo">${item.tipo}</span>`;
                linkProdutoUrls.appendChild(button);
            });
        }

        function displayBuscaTermos(data) {
            statusEl.textContent = `Total de anúncios analisados: ${data.total}`;

            linkProdutoWrap.classList.add('hidden');
            buscaTermoWrap.classList.remove('hidden');

            renderTable(topBody, data.top || []);
            renderTable(allBody, data.resultados || []);

            topEmpty.classList.toggle('hidden', (data.top || []).length > 0);
            topWrap.classList.toggle('hidden', (data.top || []).length === 0);
            allEmpty.classList.toggle('hidden', (data.resultados || []).length > 0);
            allWrap.classList.toggle('hidden', (data.resultados || []).length === 0);
        }

        function renderTable(tbody, rows) {
            tbody.innerHTML = '';
            rows.forEach(row => {
                const tr = document.createElement('tr');

                const tdTitulo = document.createElement('td');
                tdTitulo.textContent = row.titulo || '';

                const tdPreco = document.createElement('td');
                tdPreco.textContent = row.preco || '';

                const tdVendas = document.createElement('td');
                tdVendas.textContent = row.vendas !== null && row.vendas !== undefined ? row.vendas : '';

                const tdMeses = document.createElement('td');
                tdMeses.textContent = row.meses !== null && row.meses !== undefined ? row.meses : '';

                const tdMedia = document.createElement('td');
                tdMedia.textContent = row.media_vendas !== null && row.media_vendas !== undefined ? row.media_vendas : '';

                const tdLink = document.createElement('td');
                if (row.url) {
                    const actions = document.createElement('span');
                    actions.className = 'link-actions';

                    const a = document.createElement('a');
                    a.className = 'link';
                    a.href = row.url;
                    a.target = '_blank';
                    a.rel = 'noopener';
                    a.textContent = 'Abrir';
                    a.addEventListener('click', (event) => abrirAnuncioComAvantPro(row.url, event));

                    const copyBtn = document.createElement('button');
                    copyBtn.type = 'button';
                    copyBtn.className = 'copy-link-btn';
                    copyBtn.textContent = 'Copiar';
                    copyBtn.addEventListener('click', () => copiarLinkAnuncio(row.url, copyBtn));

                    actions.appendChild(a);
                    actions.appendChild(copyBtn);
                    tdLink.appendChild(actions);
                }

                tr.appendChild(tdTitulo);
                tr.appendChild(tdPreco);
                tr.appendChild(tdVendas);
                tr.appendChild(tdMeses);
                tr.appendChild(tdMedia);
                tr.appendChild(tdLink);
                tbody.appendChild(tr);
            });
        }

        function mudarAba(nomeAba, evt) {
            document.querySelectorAll('.aba-conteudo').forEach(aba => aba.classList.remove('active'));
            document.querySelectorAll('.tab-button').forEach(btn => btn.classList.remove('active'));
            document.body.classList.toggle('favoritos-tab-favoritos', nomeAba === 'favoritos');
            document.body.classList.toggle('favoritos-tab-historico', nomeAba === 'historico');

            const abaElement = document.getElementById('aba-' + nomeAba);
            if (abaElement) {
                abaElement.classList.add('active');
            }
            if (evt && evt.target) {
                evt.target.classList.add('active');
            } else {
                const botaoAba = Array.from(document.querySelectorAll('.tab-button'))
                    .find(btn => String(btn.getAttribute('onclick') || '').includes(`'${nomeAba}'`));
                if (botaoAba) botaoAba.classList.add('active');
            }

            atualizarEstadoSidebarRanking();
            renderizarSkuSidebarMercadoLivre();
            if (nomeAba === 'navegador') {
                cancelarAberturaMercadoLivreAoEntrar();
            }
            if (nomeAba === 'favoritos') {
                prepararAbaFavoritosMl();
            }
            if (nomeAba === 'historico') {
                prepararAbaHistoricoFavoritos();
            }
            if (nomeAba === 'planilhas') {
                prepararAbaPlanilhasFavoritos();
            }
            if (nomeAba === 'links-alinhados') {
                renderizarLinksAlinhadosFavoritos();
            }
            if (nomeAba === 'vendedores') {
                renderizarVendedoresIgnoradosRanking();
            }
            if (nomeAba === 'anuncios-ignorados') {
                renderizarAnunciosIgnoradosSku();
            }
            if (nomeAba !== 'navegador') {
                ocultarNavegadorMlShellDefinitivo();
            }
            // A pesquisa abre/carrega o quadro interno sob demanda.
        }

        Object.assign(feature.internal, {
            garantirSidebarSkuUnico,
            buscar,
            displayLinkProduto,
            displayBuscaTermos,
            renderTable,
            mudarAba
        });
        feature.components.add('searchTabs');
    })(window);
