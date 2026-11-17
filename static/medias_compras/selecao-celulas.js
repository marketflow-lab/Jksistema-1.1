(function installJKMediasSelecaoCelulas(global) {
    'use strict';

    global.JKMedias.defineModule('selecao-celulas', ['core', 'soma-selecao'], context => {
        const controles = 'button, a, input, textarea, select, [contenteditable]:not([contenteditable="false"])';
        let grade = null, ancora = null, ativa = null, arrasto = null;
        let selecionadas = new Set(), iniciado = false, frameArrasto = null, focoPendente = false;
        const chave = pos => pos.linha + ':' + pos.coluna;
        const posicao = valor => {
            const [linha, coluna] = valor.split(':').map(Number);
            return { linha, coluna };
        };
        const soma = context.modules['soma-selecao'];

        function pararArrasto() {
            arrasto = null;
            if (frameArrasto !== null) global.cancelAnimationFrame(frameArrasto);
            frameArrasto = null;
        }

        function limparSelecaoCelulas(ocultar = true) {
            pararArrasto();
            selecionadas.clear();
            ancora = ativa = null;
            focoPendente = false;
            atualizarSelecaoCelulas();
            if (ocultar) soma.ocultarSomaSelecao();
        }

        function configurarSelecaoCelulas(configuracao) {
            limparSelecaoCelulas();
            grade = { ...configuracao, quantidadeColunas: configuracao.colunasMeses.length + 10 };
            grade.tabela.setAttribute('role', 'grid');
            grade.tabela.setAttribute('aria-multiselectable', 'true');
            grade.tabela.setAttribute('aria-label', 'SKUs e vendas por mês. Selecione células para somar. Use Shift para ampliar e Ctrl para selecionar células separadas.');
            grade.tabela.classList.add('selecao-excel');
            atualizarSelecaoCelulas();
        }

        function celulaMontada(pos) {
            return grade?.tabela.querySelector('tbody tr[data-virtual-index="' + pos.linha + '"]')?.cells[pos.coluna];
        }

        function dadosCelula(pos) {
            const item = grade.itens[pos.linha];
            if (pos.coluna < 3) return { texto: pos.coluna === 0 ? String(item.sku || '') : pos.coluna === 2 ? String(item.titulo_anuncio || '') : '', valor: null };
            const meses = grade.colunasMeses;
            let valor;
            if (pos.coluna < meses.length + 3) valor = item.vendas_mensais?.[meses[pos.coluna - 3].key] || 0;
            else {
                const valores = [item.total_vendas_periodo || 0, item.media_mensal || 0, item.saldo_atual_estoque || 0,
                    item.estoque_em_transito || 0, item.posicao_estoque || 0,
                    calcularCoberturaMeses(item.posicao_estoque, item.media_mensal), item.compra_sugerida || 0];
                valor = valores[pos.coluna - meses.length - 3];
            }
            if (valor === null || !Number.isFinite(Number(valor))) return { texto: 'Sem venda', valor: null };
            const texto = numero(valor);
            return { texto, valor: Number(texto.replace(/\./g, '').replace(',', '.')) };
        }

        function mostrarResumo() {
            if (!ativa || !selecionadas.size) return soma.ocultarSomaSelecao();
            let total = 0, quantidade = 0;
            for (const valor of selecionadas) {
                const dados = dadosCelula(posicao(valor));
                if (dados.valor === null) continue;
                total += dados.valor;
                quantidade += 1;
            }
            const celula = celulaMontada(ativa);
            if (!celula) return soma.ocultarSomaSelecao();
            const rect = celula.getBoundingClientRect();
            const area = grade.tabela.closest('#secaoTabelaPrincipal').getBoundingClientRect();
            if (rect.right <= area.left || rect.left >= area.right || rect.bottom <= Math.max(area.top, grade.tabela.tHead.getBoundingClientRect().bottom)
                || rect.top >= Math.min(area.bottom, global.innerHeight)) return soma.ocultarSomaSelecao();
            soma.mostrarSomaValores(total, quantidade, rect);
        }

        function atualizarSelecaoCelulas() {
            if (!grade) return;
            let primeira = true;
            for (const linha of grade.tabela.querySelectorAll('tbody tr[data-jk-virtual-row]')) {
                for (const celula of linha.cells) {
                    if (celula.cellIndex >= grade.quantidadeColunas) continue;
                    const pos = { linha: Number(linha.dataset.virtualIndex), coluna: celula.cellIndex };
                    const selecionada = selecionadas.has(chave(pos));
                    const ehAtiva = ativa && chave(pos) === chave(ativa);
                    celula.classList.toggle('celula-selecionada', selecionada);
                    celula.classList.toggle('celula-ativa', Boolean(ehAtiva));
                    celula.setAttribute('role', 'gridcell');
                    celula.setAttribute('aria-selected', String(selecionada));
                    celula.tabIndex = ehAtiva || (!ativa && primeira) ? 0 : -1;
                    primeira = false;
                    if (ehAtiva && focoPendente) {
                        focoPendente = false;
                        celula.focus({ preventScroll: true });
                        celula.scrollIntoView({ block: 'nearest', inline: 'nearest' });
                    }
                }
            }
            if (selecionadas.size) mostrarResumo();
        }

        function retangulo(inicio, fim, base = new Set()) {
            selecionadas = new Set(base);
            for (let linha = Math.min(inicio.linha, fim.linha); linha <= Math.max(inicio.linha, fim.linha); linha++) {
                for (let coluna = Math.min(inicio.coluna, fim.coluna); coluna <= Math.max(inicio.coluna, fim.coluna); coluna++) {
                    selecionadas.add(chave({ linha, coluna }));
                }
            }
            ativa = fim;
            atualizarSelecaoCelulas();
        }

        function posicaoDoAlvo(alvo) {
            const celula = alvo?.closest?.('td');
            const linha = celula?.parentElement;
            if (!grade || !celula || !grade.tabela.contains(celula) || !linha.dataset.jkVirtualRow
                || celula.cellIndex >= grade.quantidadeColunas) return null;
            return { linha: Number(linha.dataset.virtualIndex), coluna: celula.cellIndex };
        }

        function focarAtiva() {
            focoPendente = true;
            if (!celulaMontada(ativa)) grade.garantirLinhaVisivel?.(ativa.linha);
            atualizarSelecaoCelulas();
        }

        function moverArrasto() {
            frameArrasto = null;
            if (!arrasto || !grade) return;
            const raiz = grade.tabela.closest('#secaoTabelaPrincipal');
            const area = raiz.getBoundingClientRect();
            const topo = Math.max(area.top, grade.tabela.tHead.getBoundingClientRect().bottom);
            const x = Math.max(area.left + 2, Math.min(arrasto.x, Math.min(area.right, global.innerWidth) - 2));
            const y = Math.max(topo + 2, Math.min(arrasto.y, Math.min(area.bottom, global.innerHeight) - 2));
            const pos = posicaoDoAlvo(document.elementFromPoint(x, y));
            if (pos && chave(pos) !== chave(ativa)) retangulo(ancora, pos, arrasto.base);
            const dx = arrasto.x < area.left + 22 ? -18 : arrasto.x > Math.min(area.right, global.innerWidth) - 22 ? 18 : 0;
            const dy = arrasto.y < topo + 22 ? -18 : arrasto.y > Math.min(area.bottom, global.innerHeight) - 22 ? 18 : 0;
            const antesX = raiz.scrollLeft, antesY = raiz.scrollTop;
            if (dx || dy) raiz.scrollBy(dx, dy);
            if (raiz.scrollLeft !== antesX || raiz.scrollTop !== antesY) frameArrasto = global.requestAnimationFrame(moverArrasto);
        }

        function aoPressionar(evento) {
            if (evento.button !== 0 || evento.isPrimary === false) return;
            const pos = posicaoDoAlvo(evento.target);
            if (!pos || evento.target.closest(controles) || evento.altKey) {
                if (selecionadas.size) limparSelecaoCelulas();
                return;
            }
            evento.preventDefault();
            const adicionar = evento.ctrlKey || evento.metaKey;
            const base = adicionar ? new Set(selecionadas) : new Set();
            if (!evento.shiftKey || !ancora) ancora = pos;
            global.getSelection()?.removeAllRanges();
            if (adicionar && !evento.shiftKey) {
                const codigo = chave(pos);
                if (selecionadas.has(codigo)) selecionadas.delete(codigo);
                else selecionadas.add(codigo);
                ativa = pos;
                atualizarSelecaoCelulas();
                if (!selecionadas.size) soma.ocultarSomaSelecao();
            } else retangulo(ancora, pos, base);
            focarAtiva();
            arrasto = { pointerId: evento.pointerId, x: evento.clientX, y: evento.clientY, base };
        }

        function aoTeclar(evento) {
            if (!grade || !grade.tabela.contains(evento.target) || evento.target.closest(controles)) return;
            if (evento.key === 'Escape') { limparSelecaoCelulas(); return; }
            const pos = ativa || posicaoDoAlvo(evento.target);
            if (!pos) return;
            if ((evento.ctrlKey || evento.metaKey) && evento.key.toLowerCase() === 'a') {
                evento.preventDefault();
                ancora = { linha: 0, coluna: 0 };
                retangulo(ancora, { linha: grade.itens.length - 1, coluna: grade.quantidadeColunas - 1 });
                ativa = pos;
                atualizarSelecaoCelulas();
                return;
            }
            const direcoes = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0] };
            const direcao = direcoes[evento.key];
            if (!direcao || evento.ctrlKey || evento.metaKey || evento.altKey) return;
            evento.preventDefault();
            const destino = { linha: Math.max(0, Math.min(grade.itens.length - 1, pos.linha + direcao[0])),
                coluna: Math.max(0, Math.min(grade.quantidadeColunas - 1, pos.coluna + direcao[1])) };
            if (!evento.shiftKey || !ancora) ancora = destino;
            retangulo(ancora, destino);
            focarAtiva();
        }

        function copiarSelecao(evento) {
            if (!selecionadas.size || !grade?.tabela.contains(document.activeElement) || document.activeElement.closest(controles) || !evento.clipboardData) return;
            let primeiraLinha = Infinity, ultimaLinha = -1, primeiraColuna = Infinity, ultimaColuna = -1;
            for (const codigo of selecionadas) {
                const pos = posicao(codigo);
                primeiraLinha = Math.min(primeiraLinha, pos.linha);
                ultimaLinha = Math.max(ultimaLinha, pos.linha);
                primeiraColuna = Math.min(primeiraColuna, pos.coluna);
                ultimaColuna = Math.max(ultimaColuna, pos.coluna);
            }
            const texto = [];
            for (let linha = primeiraLinha; linha <= ultimaLinha; linha++) {
                const valores = [];
                for (let coluna = primeiraColuna; coluna <= ultimaColuna; coluna++) {
                    valores.push(selecionadas.has(chave({ linha, coluna })) ? dadosCelula({ linha, coluna }).texto.replace(/[\t\r\n]+/g, ' ') : '');
                }
                texto.push(valores.join('\t'));
            }
            evento.clipboardData.setData('text/plain', texto.join('\n'));
            evento.preventDefault();
        }

        function iniciarSelecaoCelulas() {
            if (iniciado) return;
            iniciado = true;
            document.addEventListener('pointerdown', aoPressionar);
            document.addEventListener('pointermove', evento => {
                if (!arrasto || arrasto.pointerId !== evento.pointerId) return;
                arrasto.x = evento.clientX;
                arrasto.y = evento.clientY;
                if (frameArrasto === null) frameArrasto = global.requestAnimationFrame(moverArrasto);
            });
            document.addEventListener('pointerup', evento => {
                if (arrasto) {
                    arrasto.x = evento.clientX;
                    arrasto.y = evento.clientY;
                    moverArrasto();
                }
                pararArrasto();
                if (selecionadas.size) mostrarResumo();
            });
            document.addEventListener('pointercancel', pararArrasto);
            document.addEventListener('keydown', aoTeclar);
            document.addEventListener('copy', copiarSelecao);
            document.addEventListener('selectionchange', () => {
                if (selecionadas.size && !global.getSelection()?.isCollapsed) limparSelecaoCelulas(false);
            });
            global.addEventListener('blur', pararArrasto);
            global.addEventListener('pagehide', limparSelecaoCelulas);
            global.addEventListener('scroll', () => {
                if (selecionadas.size) global.requestAnimationFrame(mostrarResumo);
            }, true);
        }

        return { iniciarSelecaoCelulas, configurarSelecaoCelulas, atualizarSelecaoCelulas, limparSelecaoCelulas,
            temSelecaoCelulas: () => selecionadas.size > 0 };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
