(function installJKMediasEditor(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('editor', ['core', 'state', 'api', 'listas', 'editor-produtos', 'tabela-colunas'], (context) => {
        const fetch = context.modules.api.request;
        const obterAuthHeaders = context.modules.api.authHeaders;

        function setStatusModalAdicionarSku(msg) {
            const el = document.getElementById('modalAdicionarSkuStatus');
            if (el) el.textContent = msg || '';
        }

        function abrirAvisoSkuCentral(msg) {
            const modal = document.getElementById('modalAvisoSkuCentral');
            const txt = document.getElementById('textoModalAvisoSkuCentral');
            if (txt) txt.textContent = String(msg || 'SKU não encontrado na lista.');
            if (!modal) return;
            modal.classList.add('open');
            modal.setAttribute('aria-hidden', 'false');
        }

        function fecharAvisoSkuCentral() {
            const modal = document.getElementById('modalAvisoSkuCentral');
            if (!modal) return;
            modal.classList.remove('open');
            modal.setAttribute('aria-hidden', 'true');
        }

        function abrirModalAdicionarSkuPedido() {
            if (!listaPedidoAtual || !listaPedidoAtual.id) {
                setStatusListaPedido('Selecione uma lista para adicionar SKU.');
                return;
            }
            const modal = document.getElementById('modalAdicionarSkuPedido');
            const inputSku = document.getElementById('addSkuInput');
            const inputQtd = document.getElementById('addSkuQtdInput');
            const inputValor = document.getElementById('addSkuValorInput');
            if (!modal || !inputSku || !inputQtd || !inputValor) return;

            setStatusModalAdicionarSku('');
            inputSku.value = '';
            inputQtd.value = '1';
            inputValor.value = '';
            modal.classList.add('open');
            modal.setAttribute('aria-hidden', 'false');
            setTimeout(() => inputSku.focus(), 40);
        }

        function fecharModalAdicionarSkuPedido() {
            const modal = document.getElementById('modalAdicionarSkuPedido');
            if (!modal) return;
            modal.classList.remove('open');
            modal.setAttribute('aria-hidden', 'true');
            setStatusModalAdicionarSku('');
        }

        function renderEditorListaPedido() {
            const inputNome = document.getElementById('nomeListaPedidoEdit');
            const selectLoja = document.getElementById('lojaListaPedidoEdit');
            const inputStatus = document.getElementById('statusListaPedidoEdit');
            const painelEditor = document.getElementById('editorListaPedido');
            const tbody = document.querySelector('#tblListaPedidoItens tbody');
            const btnSalvar = document.getElementById('btnSalvarListaPedido');
            if (!inputNome || !selectLoja || !inputStatus || !painelEditor || !tbody || !btnSalvar) return;

            prepararColunasListaPedidos();
            habilitarResizeColunasListaPedidos();

            tbody.innerHTML = '';
            if (!listaPedidoAtual) {
                painelEditor.classList.add('hidden');
                inputNome.value = '';
                preencherSelectLojaListaPedido(selectLoja, null);
                renderStatusListaPedidoVisual('Lista gerada');
                btnSalvar.disabled = true;
                tbody.innerHTML = '<tr><td colspan="13">Selecione uma lista para editar.</td></tr>';
                return;
            }

            painelEditor.classList.remove('hidden');
            btnSalvar.disabled = false;
            inputNome.value = String(listaPedidoAtual.nome_lista || '');
            preencherSelectLojaListaPedido(selectLoja, listaPedidoAtual);
            renderStatusListaPedidoVisual(listaPedidoAtual.status);
            const itens = Array.isArray(listaPedidoAtual.itens) ? listaPedidoAtual.itens : [];
            if (!itens.length) {
                tbody.innerHTML = '<tr><td colspan="13">Lista sem itens.</td></tr>';
                atualizarTotaisListaPedido();
                return;
            }

            const termoSku = abaAtual === 'listas_pedidos' ? obterPesquisaSkuNormalizada() : '';
            let totalItensPesquisa = 0;
            itens.forEach((item, idx) => {
                const sku = String(item.SKU || '');
                const foto = String(item.Foto || '');
                const titulo = String(item['Título do produto em inglês'] || '');
                const oem = String(item.OEM || '');
                const corLado = String(item['Color/side'] || '');
                const link = String(item.Link || '');
                const qtd = Number(item.Quantidade || 0);
                const vUnit = Number(item['Valor unidade'] || 0);
                const vTotal = Number(item['Valor total'] || (qtd * vUnit));
                const cbm = String(item['Estimed CBM'] || '');
                const cbmIndividual = toNumeroDecimal(item['M3 individual'] || item['MÃ‚Â³ individual'] || item['m3_individual'] || 0);
                const peso = String(item['Estimed Weigh'] || '');
                const embalagem = String(item['Individual packaging'] || '');
                const fotoUrl = obterUrlFoto(foto);

                const tr = document.createElement('tr');
                tr.className = 'lista-pedido-row';
                const linhaCombina = !termoSku || normalizarSkuComparacaoLocal(sku).includes(termoSku);
                if (linhaCombina) {
                    totalItensPesquisa += 1;
                } else {
                    tr.classList.add('sku-row-filter-hidden');
                }
                tr.draggable = true;
                tr.dataset.idx = String(idx);
                tr.dataset.listaPedidoRow = '1';
                tr.dataset.skuCmp = normalizarSkuComparacaoLocal(sku);
                tr.innerHTML =
                    '<td><input class="sku-edit-input" data-kind="sku" data-idx="' + idx + '" data-original-sku="' + escaparHtml(sku) + '" type="text" value="' + escaparHtml(sku) + '" autocomplete="off" spellcheck="false" title="Altere o SKU e saia do campo para buscar os dados no cadastro"></td>' +
                    '<td>' + (fotoUrl ? '<img class="foto-sku" src="' + escaparHtml(fotoUrl) + '" alt="Foto ' + escaparHtml(sku) + '" loading="lazy">' : '<span class="foto-empty">Sem foto</span>') + '</td>' +
                    '<td title="' + escaparHtml(titulo) + '">' + escaparHtml(titulo) + '</td>' +
                    '<td>' + escaparHtml(oem) + '</td>' +
                    '<td>' + escaparHtml(corLado) + '</td>' +
                    '<td>' + (link ? '<a href="' + escaparHtml(link) + '" target="_blank" rel="noopener noreferrer">' + escaparHtml(link) + '</a>' : '') + '</td>' +
                    '<td><input data-kind="qtd" data-idx="' + idx + '" type="number" min="0" step="1" value="' + escaparHtml(String(qtd)) + '"></td>' +
                    '<td><input data-kind="vun" data-idx="' + idx + '" type="text" inputmode="decimal" value="' + escaparHtml(formatarMoedaUSD(vUnit)) + '"></td>' +
                    '<td><input data-kind="vtot" data-idx="' + idx + '" type="hidden" value="' + escaparHtml(String(vTotal)) + '"><span class="money-locked" data-kind="vtot_fmt" data-idx="' + idx + '">' + escaparHtml(formatarMoedaUSD(vTotal)) + '</span></td>' +
                    '<td><input data-kind="cbm" data-idx="' + idx + '" data-cbm-individual="' + escaparHtml(String(cbmIndividual || '')) + '" type="text" inputmode="decimal" value="' + escaparHtml(cbm) + '"></td>' +
                    '<td><input data-kind="peso" data-idx="' + idx + '" type="text" inputmode="decimal" value="' + escaparHtml(peso) + '"></td>' +
                    '<td><input data-kind="embalagem" data-idx="' + idx + '" type="text" value="' + escaparHtml(embalagem) + '"></td>' +
                    '<td><button type="button" class="btn-danger-soft btn-icon-action btn-mini" data-kind="excluir-item" data-idx="' + idx + '" title="Excluir SKU" aria-label="Excluir SKU">' +
                        '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
                            '<path d="M3 6H21" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>' +
                            '<path d="M8 6V4C8 2.9 8.9 2 10 2H14C15.1 2 16 2.9 16 4V6" stroke="currentColor" stroke-width="2"/>' +
                            '<path d="M19 6L18 20C17.9 21.1 17 22 15.9 22H8.1C7 22 6.1 21.1 6 20L5 6" stroke="currentColor" stroke-width="2"/>' +
                            '<path d="M10 11V17" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>' +
                            '<path d="M14 11V17" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>' +
                        '</svg>' +
                    '</button></td>';
                tbody.appendChild(tr);
            });

            if (termoSku && totalItensPesquisa === 0) {
                const trVazio = document.createElement('tr');
                trVazio.innerHTML = '<td colspan="13">Nenhum SKU encontrado nesta lista.</td>';
                tbody.appendChild(trVazio);
            }

            tbody.querySelectorAll('input[data-kind="sku"]').forEach(inp => {
                inp.addEventListener('keydown', (ev) => {
                    if (ev.key === 'Enter') {
                        ev.preventDefault();
                        inp.blur();
                    } else if (ev.key === 'Escape') {
                        inp.value = String(inp.dataset.originalSku || '');
                        inp.blur();
                    }
                });
                inp.addEventListener('blur', atualizarSkuItemListaPedido);
            });
            tbody.querySelectorAll('input[data-kind="qtd"], input[data-kind="vun"]').forEach(inp => {
                inp.addEventListener('input', recalcularLinhaListaPedido);
            });
            tbody.querySelectorAll('input[data-kind="vun"]').forEach(inp => {
                inp.addEventListener('blur', () => {
                    const vu = toNumeroDecimal(inp.value || 0);
                    inp.value = formatarMoedaUSD(vu);
                    recalcularLinhaListaPedido({ target: inp });
                });
            });
            tbody.querySelectorAll('button[data-kind="excluir-item"]').forEach(btn => {
                btn.addEventListener('click', () => {
                    const idxItem = Number(btn.dataset.idx || -1);
                    excluirItemListaPedido(idxItem);
                });
            });
            habilitarArrasteLinhasListaPedido(tbody);
            atualizarTotaisListaPedido();
        }

        function obterValorInputLinhaPedido(row, kind) {
            const el = row ? row.querySelector('[data-kind="' + kind + '"]') : null;
            return el && el.value !== undefined ? el.value : '';
        }

        function obterTextoInputLinhaPedido(row, kind, fallback = '') {
            const el = row ? row.querySelector('[data-kind="' + kind + '"]') : null;
            if (el && el.value !== undefined) return String(el.value || '').trim();
            return String(fallback || '').trim();
        }

        function capturarItemListaPedidoDaLinha(row) {
            const idx = Number(row && row.dataset ? row.dataset.idx : -1);
            const base = (Number.isInteger(idx) && listaPedidoAtual && Array.isArray(listaPedidoAtual.itens))
                ? (listaPedidoAtual.itens[idx] || {})
                : {};
            const quantidade = toNumeroDecimal(obterValorInputLinhaPedido(row, 'qtd') || base.Quantidade || 0);
            const valorUnidade = toNumeroDecimal(obterValorInputLinhaPedido(row, 'vun') || base['Valor unidade'] || 0);
            const valorTotal = toNumeroDecimal(obterValorInputLinhaPedido(row, 'vtot') || base['Valor total'] || (quantidade * valorUnidade));
            return {
                ...base,
                SKU: normalizarSku(obterValorInputLinhaPedido(row, 'sku') || base.SKU || ''),
                Quantidade: quantidade,
                'Valor unidade': valorUnidade,
                'Valor total': valorTotal,
                'Estimed CBM': obterTextoInputLinhaPedido(row, 'cbm', base['Estimed CBM']),
                'Estimed Weigh': obterTextoInputLinhaPedido(row, 'peso', base['Estimed Weigh']),
                'Individual packaging': obterTextoInputLinhaPedido(row, 'embalagem', base['Individual packaging']),
            };
        }

        function capturarItensListaPedidoDoEditor() {
            const tbody = document.querySelector('#tblListaPedidoItens tbody');
            const rows = tbody ? Array.from(tbody.querySelectorAll('tr[data-lista-pedido-row="1"]')) : [];
            if (!rows.length) {
                return Array.isArray(listaPedidoAtual && listaPedidoAtual.itens) ? [...listaPedidoAtual.itens] : [];
            }
            return rows.map(row => capturarItemListaPedidoDaLinha(row));
        }

        function obterLinhaDepoisDoPonteiroListaPedido(tbody, y) {
            const rows = Array.from(tbody.querySelectorAll('tr[data-lista-pedido-row="1"]:not(.dragging)'));
            return rows.reduce((closest, child) => {
                const box = child.getBoundingClientRect();
                const offset = y - box.top - (box.height / 2);
                if (offset < 0 && offset > closest.offset) {
                    return { offset, element: child };
                }
                return closest;
            }, { offset: Number.NEGATIVE_INFINITY, element: null }).element;
        }

        function finalizarArrasteLinhasListaPedido(tbody) {
            if (!tbody || !listaPedidoAtual || !Array.isArray(listaPedidoAtual.itens)) return;
            const ordemInicial = String(tbody.dataset.dragStartOrder || '');
            const ordemAtual = Array.from(tbody.querySelectorAll('tr[data-lista-pedido-row="1"]')).map(row => String(row.dataset.idx || '')).join('|');
            tbody.querySelectorAll('.dragging').forEach(row => row.classList.remove('dragging'));
            delete tbody.dataset.dragStartOrder;
            if (!ordemInicial || ordemInicial === ordemAtual) return;
            listaPedidoAtual.itens = capturarItensListaPedidoDoEditor();
            renderEditorListaPedido();
            setStatusListaPedido('Ordem dos SKUs alterada. Clique em "Salvar alteraÃ§Ãµes" para confirmar.');
        }

        function habilitarArrasteLinhasListaPedido(tbody) {
            if (!tbody) return;
            tbody.querySelectorAll('tr[data-lista-pedido-row="1"]').forEach(row => {
                row.addEventListener('dragstart', (ev) => {
                    const alvo = ev.target && ev.target.closest ? ev.target.closest('input, textarea, select, button, a') : null;
                    if (alvo) {
                        ev.preventDefault();
                        return;
                    }
                    tbody.dataset.dragStartOrder = Array.from(tbody.querySelectorAll('tr[data-lista-pedido-row="1"]')).map(r => String(r.dataset.idx || '')).join('|');
                    row.classList.add('dragging');
                    if (ev.dataTransfer) {
                        ev.dataTransfer.effectAllowed = 'move';
                        ev.dataTransfer.setData('text/plain', String(row.dataset.idx || ''));
                    }
                });
                row.addEventListener('dragover', (ev) => {
                    const dragging = tbody.querySelector('tr.dragging');
                    if (!dragging) return;
                    ev.preventDefault();
                    const after = obterLinhaDepoisDoPonteiroListaPedido(tbody, ev.clientY);
                    if (!after) tbody.appendChild(dragging);
                    else if (after !== dragging) tbody.insertBefore(dragging, after);
                });
                row.addEventListener('drop', (ev) => {
                    ev.preventDefault();
                    finalizarArrasteLinhasListaPedido(tbody);
                });
                row.addEventListener('dragend', () => finalizarArrasteLinhasListaPedido(tbody));
            });
        }

        function excluirItemListaPedido(idx) {
            if (!listaPedidoAtual || !Array.isArray(listaPedidoAtual.itens)) return;
            const pos = Number(idx);
            if (!Number.isInteger(pos) || pos < 0 || pos >= listaPedidoAtual.itens.length) return;

            const item = listaPedidoAtual.itens[pos] || {};
            const sku = String(item.SKU || '').trim() || ('item #' + String(pos + 1));
            const confirmar = window.confirm('Deseja excluir o SKU ' + sku + ' desta lista?');
            if (!confirmar) return;

            const itens = [...listaPedidoAtual.itens];
            itens.splice(pos, 1);
            listaPedidoAtual.itens = itens;
            renderEditorListaPedido();
            setStatusListaPedido('SKU ' + sku + ' removido. Clique em "Salvar alterações" para confirmar.');
        }

        function destacarSkuNaTabelaPedido(sku) {
            const skuCmp = normalizarSkuComparacaoLocal(sku);
            if (!skuCmp) return false;
            const rows = Array.from(document.querySelectorAll('#tblListaPedidoItens tbody tr'));
            const alvo = rows.find((r) => String(r.dataset.skuCmp || '') === skuCmp);
            if (!alvo) return false;

            rows.forEach((r) => {
                r.style.outline = '';
                r.style.background = '';
            });

            alvo.scrollIntoView({ behavior: 'smooth', block: 'center' });
            alvo.style.outline = '2px solid rgba(99, 187, 255, 0.95)';
            alvo.style.background = 'rgba(52, 103, 168, 0.22)';
            setTimeout(() => {
                alvo.style.outline = '';
                alvo.style.background = '';
            }, 2200);
            return true;
        }

        function atualizarTotaisListaPedido() {
            const tbody = document.querySelector('#tblListaPedidoItens tbody');
            if (!tbody) return;
            let somaQtd = 0, somaVtot = 0;
            tbody.querySelectorAll('input[data-kind="qtd"]').forEach(el => { somaQtd += toNumeroDecimal(el.value || 0); });
            tbody.querySelectorAll('input[data-kind="vtot"]').forEach(el => { somaVtot += toNumeroDecimal(el.value || 0); });
            const elQtd = document.getElementById('tfootQtd');
            const elVtot = document.getElementById('tfootVtot');
            if (elQtd) elQtd.textContent = somaQtd.toLocaleString('pt-BR');
            if (elVtot) elVtot.textContent = formatarMoedaUSD(somaVtot);
        }

        function recalcularLinhaListaPedido(ev) {
            const el = ev && ev.target ? ev.target : null;
            if (!el) return;
            const idx = String(el.dataset.idx || '');
            const qtdEl = document.querySelector('input[data-kind="qtd"][data-idx="' + idx + '"]');
            const vunEl = document.querySelector('input[data-kind="vun"][data-idx="' + idx + '"]');
            const vtotEl = document.querySelector('input[data-kind="vtot"][data-idx="' + idx + '"]');
            const vtotFmtEl = document.querySelector('[data-kind="vtot_fmt"][data-idx="' + idx + '"]');
            if (!qtdEl || !vunEl || !vtotEl) return;
            const q = toNumeroDecimal(qtdEl.value || 0);
            const vu = toNumeroDecimal(vunEl.value || 0);
            const total = Math.max(0, q * vu);
            vtotEl.value = String(Math.round(total * 100) / 100);
            if (vtotFmtEl) vtotFmtEl.textContent = formatarMoedaUSD(total);
            const cbmEl = document.querySelector('input[data-kind="cbm"][data-idx="' + idx + '"]');
            if (cbmEl && el.dataset.kind === 'qtd') {
                const cbmIndividual = toNumeroDecimal(cbmEl.dataset.cbmIndividual || 0);
                if (cbmIndividual > 0 && q > 0) {
                    cbmEl.value = formatarNumeroListaPedido(cbmIndividual * q, 6);
                }
            }
            atualizarTotaisListaPedido();
        }

        async function salvarListaPedidoAtual() {
            if (!listaPedidoAtual || !listaPedidoAtual.id) {
                setStatusListaPedido('Selecione uma lista para salvar.');
                return;
            }
            const inputNome = document.getElementById('nomeListaPedidoEdit');
            const selectLoja = document.getElementById('lojaListaPedidoEdit');
            const nomeLista = String((inputNome && inputNome.value) || '').trim();
            if (!nomeLista) {
                setStatusListaPedido('Informe o nome da lista.');
                return;
            }
            const lojaLista = obterLojaListaPedidoSelecionada();
            if (!lojaLista) {
                setStatusListaPedido('Selecione a loja da lista.');
                if (selectLoja) selectLoja.focus();
                return;
            }

            const skuOk = await aplicarEdicoesSkuPendentesListaPedido();
            if (!skuOk) return;
            const itensOriginais = capturarItensListaPedidoDoEditor();
            const itens = itensOriginais.map((item, idx) => {
                const qtdEl = document.querySelector('input[data-kind="qtd"][data-idx="' + idx + '"]');
                const vunEl = document.querySelector('input[data-kind="vun"][data-idx="' + idx + '"]');
                const vtotEl = document.querySelector('input[data-kind="vtot"][data-idx="' + idx + '"]');
                const cbmEl = document.querySelector('input[data-kind="cbm"][data-idx="' + idx + '"]');
                const pesoEl = document.querySelector('input[data-kind="peso"][data-idx="' + idx + '"]');
                const embalagemEl = document.querySelector('input[data-kind="embalagem"][data-idx="' + idx + '"]');
                return {
                    ...item,
                    Quantidade: toNumeroDecimal((qtdEl && qtdEl.value) || item.Quantidade || 0),
                    'Valor unidade': toNumeroDecimal((vunEl && vunEl.value) || item['Valor unidade'] || 0),
                    'Valor total': toNumeroDecimal((vtotEl && vtotEl.value) || item['Valor total'] || 0),
                    'Estimed CBM': (cbmEl && cbmEl.value !== undefined) ? String(cbmEl.value || '').trim() : String(item['Estimed CBM'] || '').trim(),
                    'Estimed Weigh': (pesoEl && pesoEl.value !== undefined) ? String(pesoEl.value || '').trim() : String(item['Estimed Weigh'] || '').trim(),
                    'Individual packaging': (embalagemEl && embalagemEl.value !== undefined) ? String(embalagemEl.value || '').trim() : String(item['Individual packaging'] || '').trim(),
                };
            });

            try {
                setStatusListaPedido('Salvando alterações...');
                const resp = await fetch('/api/medias-compras/listas-pedidos/' + encodeURIComponent(String(listaPedidoAtual.id)), {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                        ...obterAuthHeaders()
                    },
                    body: JSON.stringify({ nome_lista: nomeLista, loja: lojaLista, itens })
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.detail || 'Falha ao salvar alterações da lista.');
                }
                const data = await resp.json();
                listaPedidoAtual = data.lista || listaPedidoAtual;
                await carregarListasPedidos();
                if (listaPedidoAtual && listaPedidoAtual.id) {
                    await abrirListaPedidoPorId(listaPedidoAtual.id);
                }
                setStatusListaPedido('Lista salva com sucesso.');
            } catch (e) {
                setStatusListaPedido(e.message || 'Erro ao salvar lista.');
            }
        }

        return {
            setStatusModalAdicionarSku,
            abrirAvisoSkuCentral,
            fecharAvisoSkuCentral,
            abrirModalAdicionarSkuPedido,
            fecharModalAdicionarSkuPedido,
            renderEditorListaPedido,
            obterValorInputLinhaPedido,
            obterTextoInputLinhaPedido,
            capturarItemListaPedidoDaLinha,
            capturarItensListaPedidoDoEditor,
            obterLinhaDepoisDoPonteiroListaPedido,
            finalizarArrasteLinhasListaPedido,
            habilitarArrasteLinhasListaPedido,
            excluirItemListaPedido,
            destacarSkuNaTabelaPedido,
            atualizarTotaisListaPedido,
            recalcularLinhaListaPedido,
            salvarListaPedidoAtual
        };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
