// Extracted from 07-execucao-render-layout.js lines 2943-3346.
        function carregarLayoutTabelasFavoritos() {
            try {
                const data = JSON.parse(localStorage.getItem(ML_FAVORITOS_TABLE_LAYOUT_KEY) || '{}');
                return data && typeof data === 'object' ? data : {};
            } catch (_err) {
                return {};
            }
        }

        function salvarLayoutTabelasFavoritos() {
            try {
                localStorage.setItem(ML_FAVORITOS_TABLE_LAYOUT_KEY, JSON.stringify(favoritosTableLayout || {}));
            } catch (_err) {}
        }

        function obterLayoutTabelaFavoritos(tableId) {
            if (!favoritosTableLayout || typeof favoritosTableLayout !== 'object') favoritosTableLayout = {};
            if (!favoritosTableLayout[tableId]) favoritosTableLayout[tableId] = { order: [], widths: {} };
            if (!Array.isArray(favoritosTableLayout[tableId].order)) favoritosTableLayout[tableId].order = [];
            if (!favoritosTableLayout[tableId].widths || typeof favoritosTableLayout[tableId].widths !== 'object') favoritosTableLayout[tableId].widths = {};
            return favoritosTableLayout[tableId];
        }

        function obterOrdemBaseTabelaFavoritos(table) {
            const headerRow = table && table.tHead && table.tHead.rows ? table.tHead.rows[0] : null;
            if (!headerRow) return [];
            if (table.dataset.baseOrder) return table.dataset.baseOrder.split(',').filter(Boolean);
            const keys = Array.from(headerRow.cells).map((th, idx) => {
                const key = th.dataset.colKey || `col_${idx}`;
                th.dataset.colKey = key;
                return key;
            });
            table.dataset.baseOrder = keys.join(',');
            return keys;
        }

        function criarColgroupTabelaFavoritos(table, keys) {
            let colgroup = table.querySelector('colgroup');
            if (!colgroup) {
                colgroup = document.createElement('colgroup');
                table.insertBefore(colgroup, table.firstChild);
            }
            const existentes = new Map(Array.from(colgroup.children).map(col => [col.dataset.colKey, col]));
            colgroup.innerHTML = '';
            keys.forEach(key => {
                const col = existentes.get(key) || document.createElement('col');
                col.dataset.colKey = key;
                colgroup.appendChild(col);
            });
            return colgroup;
        }

        function marcarCelulasTabelaFavoritos(table, baseOrder) {
            const bodies = Array.from(table.tBodies || []);
            bodies.forEach(tbody => {
                Array.from(tbody.rows).forEach(row => {
                    if (row.dataset.colKeysReady === '1') return;
                    Array.from(row.cells).forEach((cell, idx) => {
                        cell.dataset.colKey = baseOrder[idx] || `col_${idx}`;
                    });
                    row.dataset.colKeysReady = '1';
                });
            });
        }

        function aplicarOrdemTabelaFavoritos(table, keys) {
            const headerRow = table && table.tHead && table.tHead.rows ? table.tHead.rows[0] : null;
            if (!headerRow) return;
            const ordenarLinha = (row) => {
                const cells = Array.from(row.cells);
                const map = new Map(cells.map(cell => [cell.dataset.colKey, cell]));
                keys.forEach(key => {
                    const cell = map.get(key);
                    if (cell) row.appendChild(cell);
                });
            };
            ordenarLinha(headerRow);
            Array.from(table.tBodies || []).forEach(tbody => {
                Array.from(tbody.rows).forEach(ordenarLinha);
            });
        }

        const ML_FAVORITOS_COL_WIDTHS_PADRAO = {
            'fav-ml-anuncios': {
                ordem: 58,
                alterar: 62,
                foto: 54,
                mlb: 120,
                titulo: 150,
                preco: 130,
                simulador: 120
            },
            'fav-outros-anuncios': {
                acoes: 58,
                foto: 54,
                mlb: 125,
                media: 130,
                titulo: 160
            }
        };

        const ML_FAVORITOS_COL_WIDTHS_MIN = {
            acoes: 58,
            ordem: 54,
            alterar: 58,
            foto: 54,
            mlb: 110,
            media: 140,
            titulo: 130,
            preco: 140,
            simulador: 118
        };

        function obterLarguraDisponivelTabelaFavoritos(table) {
            const wrap = table && table.closest ? table.closest('.favoritos-ml-table-wrap, .ml-favoritos-table-wrap') : null;
            const rect = wrap ? wrap.getBoundingClientRect() : null;
            const largura = rect && Number.isFinite(rect.width) ? Math.floor(rect.width) : 0;
            return largura > 0 ? Math.max(240, largura - 2) : 0;
        }

        function obterLarguraColunaFavoritos(table, key, layout) {
            const salva = Number(layout.widths && layout.widths[key]);
            if (Number.isFinite(salva) && salva > 0) return salva;
            const tableId = table.dataset.tableId || table.id || '';
            const padrao = ML_FAVORITOS_COL_WIDTHS_PADRAO[tableId] && ML_FAVORITOS_COL_WIDTHS_PADRAO[tableId][key];
            return Number.isFinite(padrao) && padrao > 0 ? padrao : null;
        }

        function compactarLargurasTabelaFavoritos(table, keys, layout) {
            const larguras = keys.map(key => obterLarguraColunaFavoritos(table, key, layout));
            if (larguras.some(width => !Number.isFinite(width) || width <= 0)) return larguras;
            const disponivel = obterLarguraDisponivelTabelaFavoritos(table);
            const soma = larguras.reduce((total, width) => total + width, 0);
            if (!disponivel || soma <= disponivel) return larguras;

            const minimos = keys.map(key => ML_FAVORITOS_COL_WIDTHS_MIN[key] || 54);
            const somaMinimos = minimos.reduce((total, width) => total + width, 0);
            if (somaMinimos >= disponivel) {
                return minimos;
            }

            const folgaOriginal = larguras.reduce((total, width, idx) => total + Math.max(0, width - minimos[idx]), 0);
            const folgaDestino = Math.max(0, disponivel - somaMinimos);
            if (folgaOriginal <= 0) return minimos;
            return larguras.map((width, idx) => {
                const extra = Math.max(0, width - minimos[idx]);
                return Math.round(minimos[idx] + (extra * folgaDestino / folgaOriginal));
            });
        }

        function aplicarLargurasTabelaFavoritos(table, keys, layout) {
            const colgroup = criarColgroupTabelaFavoritos(table, keys);
            const largurasCompactadas = compactarLargurasTabelaFavoritos(table, keys, layout);
            const somaLarguras = largurasCompactadas
                .map(width => Number(width))
                .filter(width => Number.isFinite(width) && width > 0)
                .reduce((total, width) => total + Math.max(44, width), 0);
            const disponivel = obterLarguraDisponivelTabelaFavoritos(table);
            if (somaLarguras > 0 && disponivel > 0 && somaLarguras > disponivel) {
                table.style.minWidth = `${somaLarguras}px`;
                table.style.width = `${somaLarguras}px`;
            } else {
                table.style.minWidth = '';
                table.style.width = '';
            }
            keys.forEach((key, idx) => {
                const width = Number(largurasCompactadas[idx]);
                const col = colgroup.children[idx];
                if (!col) return;
                if (Number.isFinite(width) && width > 0) {
                    col.style.width = `${Math.max(44, width)}px`;
                } else {
                    col.style.width = '';
                }
            });
        }

        function obterOrdemAtualTabelaFavoritos(table, baseOrder, layout) {
            const order = (layout.order || []).filter(key => baseOrder.includes(key));
            baseOrder.forEach(key => {
                if (!order.includes(key)) order.push(key);
            });
            return order;
        }

        function moverColunaTabelaFavoritos(table, fromKey, toKey) {
            const tableId = table.dataset.tableId;
            const baseOrder = obterOrdemBaseTabelaFavoritos(table);
            const layout = obterLayoutTabelaFavoritos(tableId);
            const order = obterOrdemAtualTabelaFavoritos(table, baseOrder, layout);
            const fromIndex = order.indexOf(fromKey);
            const toIndex = order.indexOf(toKey);
            if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return;
            order.splice(fromIndex, 1);
            order.splice(toIndex, 0, fromKey);
            layout.order = order;
            salvarLayoutTabelasFavoritos();
            prepararTabelaFavoritosEditavel(table);
            agendarSincronizarLinhasFavoritos();
        }

        function iniciarResizeColunaFavoritos(event, table, th) {
            event.preventDefault();
            event.stopPropagation();
            const tableId = table.dataset.tableId;
            const key = th.dataset.colKey;
            const layout = obterLayoutTabelaFavoritos(tableId);
            const startX = event.clientX;
            const startWidth = th.getBoundingClientRect().width;
            document.body.classList.add('favoritos-resizing-table');

            const mover = (moveEvent) => {
                const nextWidth = Math.max(54, Math.round(startWidth + (moveEvent.clientX - startX)));
                layout.widths[key] = nextWidth;
                const baseOrder = obterOrdemBaseTabelaFavoritos(table);
                const keys = obterOrdemAtualTabelaFavoritos(table, baseOrder, layout);
                aplicarLargurasTabelaFavoritos(table, keys, layout);
                agendarSincronizarLinhasFavoritos();
            };
            const parar = () => {
                document.body.classList.remove('favoritos-resizing-table');
                window.removeEventListener('pointermove', mover);
                window.removeEventListener('pointerup', parar);
                window.removeEventListener('pointercancel', parar);
                salvarLayoutTabelasFavoritos();
                agendarSincronizarLinhasFavoritos();
            };

            window.addEventListener('pointermove', mover);
            window.addEventListener('pointerup', parar, { once: true });
            window.addEventListener('pointercancel', parar, { once: true });
        }

        function prepararTabelaFavoritosEditavel(table) {
            if (!table) return;
            const tableId = table.dataset.tableId || table.id || '';
            if (!tableId) return;
            const headerRow = table.tHead && table.tHead.rows ? table.tHead.rows[0] : null;
            if (!headerRow) return;
            const baseOrder = obterOrdemBaseTabelaFavoritos(table);
            const layout = obterLayoutTabelaFavoritos(tableId);
            const keys = obterOrdemAtualTabelaFavoritos(table, baseOrder, layout);
            marcarCelulasTabelaFavoritos(table, baseOrder);
            aplicarOrdemTabelaFavoritos(table, keys);
            aplicarLargurasTabelaFavoritos(table, keys, layout);

            Array.from(headerRow.cells).forEach(th => {
                if (th.dataset.favEditableReady === '1') return;
                th.dataset.favEditableReady = '1';
                th.draggable = true;
                const handle = document.createElement('span');
                handle.className = 'fav-col-resize-handle';
                handle.title = 'Arraste para ajustar a largura da coluna';
                handle.addEventListener('pointerdown', event => iniciarResizeColunaFavoritos(event, table, th));
                th.appendChild(handle);
                th.addEventListener('dragstart', event => {
                    if (event.target && event.target.classList && event.target.classList.contains('fav-col-resize-handle')) return;
                    th.classList.add('is-dragging');
                    event.dataTransfer.effectAllowed = 'move';
                    event.dataTransfer.setData('text/plain', th.dataset.colKey || '');
                });
                th.addEventListener('dragend', () => {
                    th.classList.remove('is-dragging');
                    Array.from(headerRow.cells).forEach(cell => cell.classList.remove('is-drop-target'));
                });
                th.addEventListener('dragover', event => {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = 'move';
                    th.classList.add('is-drop-target');
                });
                th.addEventListener('dragleave', () => th.classList.remove('is-drop-target'));
                th.addEventListener('drop', event => {
                    event.preventDefault();
                    th.classList.remove('is-drop-target');
                    const fromKey = event.dataTransfer.getData('text/plain');
                    moverColunaTabelaFavoritos(table, fromKey, th.dataset.colKey);
                });
            });
        }

        function atualizarTabelasFavoritosEditaveis() {
            prepararTabelaFavoritosEditavel(favMlAnunciosTableEl);
            prepararTabelaFavoritosEditavel(favOutrosAnunciosTableEl);
            agendarSincronizarLinhasFavoritos();
        }

        function obterAlturaMinimaLinhaFavoritos() {
            const origem = favMlTablesLayoutEl || document.documentElement;
            const valor = window.getComputedStyle(origem).getPropertyValue('--favoritos-ml-row-height');
            const numero = parseFloat(valor);
            return Number.isFinite(numero) && numero > 0 ? numero : 86;
        }

        function limparAlturaLinhaFavoritos(row) {
            if (!row) return;
            row.style.height = '';
            Array.from(row.cells || []).forEach(cell => {
                cell.style.height = '';
            });
        }

        function aplicarAlturaLinhaFavoritos(row, altura) {
            if (!row || !Number.isFinite(altura) || altura <= 0) return;
            const valor = `${altura}px`;
            row.style.height = valor;
            Array.from(row.cells || []).forEach(cell => {
                cell.style.height = valor;
            });
        }

        function sincronizarAlturasLinhasFavoritos() {
            favoritosSyncLinhasRaf = 0;
            if (!favMlAnunciosTableEl || !favOutrosAnunciosTableEl) return;

            const cabecalhos = [
                favMlAnunciosTableEl.tHead && favMlAnunciosTableEl.tHead.rows ? favMlAnunciosTableEl.tHead.rows[0] : null,
                favOutrosAnunciosTableEl.tHead && favOutrosAnunciosTableEl.tHead.rows ? favOutrosAnunciosTableEl.tHead.rows[0] : null
            ].filter(Boolean);
            cabecalhos.forEach(limparAlturaLinhaFavoritos);
            if (cabecalhos.length) {
                const alturaCabecalho = Math.ceil(Math.max(...cabecalhos.map(row => row.getBoundingClientRect().height || 0)));
                cabecalhos.forEach(row => aplicarAlturaLinhaFavoritos(row, alturaCabecalho));
            }

            const linhasMl = Array.from(favMlAnunciosBodyEl && favMlAnunciosBodyEl.rows ? favMlAnunciosBodyEl.rows : []);
            const linhasRanking = Array.from(favOutrosAnunciosBodyEl && favOutrosAnunciosBodyEl.rows ? favOutrosAnunciosBodyEl.rows : []);
            [...linhasMl, ...linhasRanking].forEach(limparAlturaLinhaFavoritos);

            const alturaMinima = obterAlturaMinimaLinhaFavoritos();
            const total = Math.max(linhasMl.length, linhasRanking.length);
            for (let i = 0; i < total; i += 1) {
                const linhaMl = linhasMl[i] || null;
                const linhaRanking = linhasRanking[i] || null;
                const linhaRankingSincronizavel = linhaRanking && !linhaRanking.classList.contains('is-history-list-row')
                    ? linhaRanking
                    : null;
                const altura = Math.ceil(Math.max(
                    alturaMinima,
                    linhaMl ? linhaMl.getBoundingClientRect().height || 0 : 0,
                    linhaRankingSincronizavel ? linhaRankingSincronizavel.getBoundingClientRect().height || 0 : 0
                ));
                aplicarAlturaLinhaFavoritos(linhaMl, altura);
                aplicarAlturaLinhaFavoritos(linhaRankingSincronizavel, altura);
            }
        }

        function agendarSincronizarLinhasFavoritos() {
            const agendar = window.requestAnimationFrame || ((callback) => window.setTimeout(callback, 0));
            const cancelar = window.cancelAnimationFrame || window.clearTimeout;
            if (favoritosSyncLinhasRaf) cancelar(favoritosSyncLinhasRaf);
            favoritosSyncLinhasRaf = agendar(sincronizarAlturasLinhasFavoritos);
        }

        window.addEventListener('resize', () => {
            prepararTabelaFavoritosEditavel(favMlAnunciosTableEl);
            prepararTabelaFavoritosEditavel(favOutrosAnunciosTableEl);
            agendarSincronizarLinhasFavoritos();
        });

        function inicializarLarguraTabelasFavoritos() {
            if (!favMlTablesLayoutEl || favMlTablesLayoutEl.dataset.resizerReady === '1') return;
            favMlTablesLayoutEl.dataset.resizerReady = '1';
            favMlTablesLayoutEl.classList.add('is-resizable');
            const split = Number(favoritosTableLayout && favoritosTableLayout.split);
            if (favoritosTableLayout && favoritosTableLayout.splitUserDefined === true && Number.isFinite(split)) {
                favMlTablesLayoutEl.style.setProperty('--favoritos-ml-left-width', `${Math.min(72, Math.max(32, split))}%`);
            } else {
                favMlTablesLayoutEl.style.setProperty('--favoritos-ml-left-width', '1fr');
                delete favoritosTableLayout.split;
                favoritosTableLayout.splitUserDefined = false;
                salvarLayoutTabelasFavoritos();
            }
            const panels = favMlTablesLayoutEl.querySelectorAll(':scope > .panel');
            if (panels.length < 2) return;
            const handle = document.createElement('div');
            handle.className = 'favoritos-ml-split-resizer';
            handle.title = 'Arraste para ajustar a largura das tabelas';
            panels[0].after(handle);

            handle.addEventListener('pointerdown', event => {
                event.preventDefault();
                document.body.classList.add('favoritos-resizing-table');
                const mover = (moveEvent) => {
                    const rect = favMlTablesLayoutEl.getBoundingClientRect();
                    if (!rect.width) return;
                    const percent = Math.min(72, Math.max(32, ((moveEvent.clientX - rect.left) / rect.width) * 100));
                    favMlTablesLayoutEl.style.setProperty('--favoritos-ml-left-width', `${percent.toFixed(2)}%`);
                    favoritosTableLayout.split = Number(percent.toFixed(2));
                    favoritosTableLayout.splitUserDefined = true;
                    agendarSincronizarLinhasFavoritos();
                };
                const parar = () => {
                    document.body.classList.remove('favoritos-resizing-table');
                    window.removeEventListener('pointermove', mover);
                    window.removeEventListener('pointerup', parar);
                    window.removeEventListener('pointercancel', parar);
                    salvarLayoutTabelasFavoritos();
                    agendarSincronizarLinhasFavoritos();
                };
                window.addEventListener('pointermove', mover);
                window.addEventListener('pointerup', parar, { once: true });
                window.addEventListener('pointercancel', parar, { once: true });
            });
        }
