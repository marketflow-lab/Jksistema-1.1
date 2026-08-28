(function installJKMediasTabelaColunas(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('tabela-colunas', ['core', 'state', 'api'], (context) => {
        const fetch = context.modules.api.request;
        const obterAuthHeaders = context.modules.api.authHeaders;

        function salvarLargurasColunas() {
            try {
                localStorage.setItem(LS_COL_WIDTHS_KEY, JSON.stringify(largurasColunas));
            } catch (_e) {
                // Sem bloqueio: persistência é complementar.
            }
        }

        function salvarLargurasColunasPedidos() {
            try {
                localStorage.setItem(LS_PEDIDOS_COL_WIDTHS_KEY, JSON.stringify(largurasColunasPedidos));
            } catch (_e) {
                // Sem bloqueio: persistência é complementar.
            }
        }

        function aplicarLarguraColuna(th, colKey) {
            const largura = Number(largurasColunas[colKey] || 0);
            if (largura > 0) {
                th.style.width = largura + 'px';
                th.style.minWidth = largura + 'px';
            }
        }

        function aplicarLarguraColunaPorIndice(colIndex, larguraPx) {
            const col = colElementsAtuais[colIndex];
            if (!col) return;
            col.style.width = larguraPx + 'px';
            col.style.minWidth = larguraPx + 'px';
        }

        function prepararColunasListaPedidos() {
            const tabela = document.getElementById('tblListaPedidoItens');
            if (!tabela) return;

            const colgroupAntigo = tabela.querySelector('colgroup');
            if (colgroupAntigo) colgroupAntigo.remove();

            const colgroup = document.createElement('colgroup');
            colElementsPedidos = [];

            CHAVES_COLUNAS_PEDIDOS.forEach((colKey) => {
                const col = document.createElement('col');
                const larguraSalva = Number(largurasColunasPedidos[colKey] || 0);
                const larguraPadrao = Number(LARGURAS_PADRAO_COLUNAS_PEDIDOS[colKey] || 0);
                const largura = Math.max(0, larguraSalva || larguraPadrao);
                if (largura > 0) {
                    col.style.width = largura + 'px';
                    col.style.minWidth = largura + 'px';
                }
                colgroup.appendChild(col);
                colElementsPedidos.push(col);
            });

            tabela.insertBefore(colgroup, tabela.firstChild);
        }

        function aplicarLarguraColunaPedidoPorIndice(colIndex, larguraPx) {
            const col = colElementsPedidos[colIndex];
            if (!col) return;
            col.style.width = larguraPx + 'px';
            col.style.minWidth = larguraPx + 'px';
        }

        function habilitarResizeColunasListaPedidos() {
            const ths = Array.from(document.querySelectorAll('#tblListaPedidoItens thead th'));
            ths.forEach((th, idx) => {
                const colKey = CHAVES_COLUNAS_PEDIDOS[idx] || ('col_' + idx);
                th.dataset.colKey = colKey;
                th.dataset.colIndex = String(idx);
                th.classList.add('resizable');

                const larguraSalva = Number(largurasColunasPedidos[colKey] || 0);
                const larguraPadrao = Number(LARGURAS_PADRAO_COLUNAS_PEDIDOS[colKey] || 0);
                const largura = Math.max(0, larguraSalva || larguraPadrao);
                if (largura > 0) {
                    th.style.width = largura + 'px';
                    th.style.minWidth = largura + 'px';
                }

                if (th.querySelector('.col-resizer')) return;

                const grip = document.createElement('span');
                grip.className = 'col-resizer';
                grip.title = 'Arraste para ajustar largura';

                grip.addEventListener('mousedown', function (e) {
                    e.preventDefault();
                    e.stopPropagation();

                    const startX = e.clientX;
                    const rect = th.getBoundingClientRect();
                    const startWidth = rect.width;
                    const colIndex = Number(th.dataset.colIndex || -1);

                    function onMouseMove(ev) {
                        const delta = ev.clientX - startX;
                        const novaLargura = Math.max(1, Math.round(startWidth + delta));
                        th.style.width = novaLargura + 'px';
                        th.style.minWidth = novaLargura + 'px';
                        aplicarLarguraColunaPedidoPorIndice(colIndex, novaLargura);
                    }

                    function onMouseUp() {
                        const finalWidth = Math.round(th.getBoundingClientRect().width);
                        largurasColunasPedidos[colKey] = Math.max(1, finalWidth);
                        salvarLargurasColunasPedidos();
                        document.removeEventListener('mousemove', onMouseMove);
                        document.removeEventListener('mouseup', onMouseUp);
                    }

                    document.addEventListener('mousemove', onMouseMove);
                    document.addEventListener('mouseup', onMouseUp);
                });

                th.appendChild(grip);
            });
        }

        function habilitarResizeColunas() {
            const ths = Array.from(document.querySelectorAll('#tblResultado thead th'));
            ths.forEach(th => {
                const colKey = th.dataset.colKey;
                if (!colKey) return;

                th.classList.add('resizable');
                if (th.querySelector('.col-resizer')) return;

                const grip = document.createElement('span');
                grip.className = 'col-resizer';
                grip.title = 'Arraste para ajustar largura';

                grip.addEventListener('mousedown', function (e) {
                    e.preventDefault();
                    e.stopPropagation();

                    const startX = e.clientX;
                    const rect = th.getBoundingClientRect();
                    const startWidth = rect.width;
                    const colIndex = Number(th.dataset.colIndex || -1);

                    function onMouseMove(ev) {
                        const delta = ev.clientX - startX;
                        const novaLargura = Math.max(8, Math.round(startWidth + delta));
                        th.style.width = novaLargura + 'px';
                        th.style.minWidth = novaLargura + 'px';
                        aplicarLarguraColunaPorIndice(colIndex, novaLargura);
                    }

                    function onMouseUp() {
                        const finalWidth = Math.round(th.getBoundingClientRect().width);
                        largurasColunas[colKey] = Math.max(8, finalWidth);
                        salvarLargurasColunas();
                        document.removeEventListener('mousemove', onMouseMove);
                        document.removeEventListener('mouseup', onMouseUp);
                    }

                    document.addEventListener('mousemove', onMouseMove);
                    document.addEventListener('mouseup', onMouseUp);
                });

                th.appendChild(grip);
            });
        }

        return {
            salvarLargurasColunas,
            salvarLargurasColunasPedidos,
            aplicarLarguraColuna,
            aplicarLarguraColunaPorIndice,
            prepararColunasListaPedidos,
            aplicarLarguraColunaPedidoPorIndice,
            habilitarResizeColunasListaPedidos,
            habilitarResizeColunas
        };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
