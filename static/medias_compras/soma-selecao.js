(function installJKMediasSomaSelecao(global) {
    'use strict';

    global.JKMedias.defineModule('soma-selecao', ['core'], () => {
        const ignorar = '.previsao-detalhe, .previsao-seta, .previsao-tendencia, input, textarea, select, [contenteditable]:not([contenteditable="false"])';
        let balao = null;
        let frame = null;
        let iniciado = false;

        function ocultarSomaSelecao() {
            if (frame !== null) global.cancelAnimationFrame(frame);
            frame = null;
            if (balao) balao.hidden = true;
        }

        function elementoDoNo(no) {
            return no?.nodeType === 1 ? no : no?.parentElement;
        }

        function textoSelecionado(celula, selecao) {
            const walker = document.createTreeWalker(celula, NodeFilter.SHOW_TEXT);
            const partes = [];
            let no;
            while ((no = walker.nextNode())) {
                if (no.parentElement.closest(ignorar) || !selecao.intersectsNode(no)) continue;
                const trecho = document.createRange();
                trecho.selectNodeContents(no);
                if (selecao.compareBoundaryPoints(Range.START_TO_START, trecho) > 0) {
                    trecho.setStart(selecao.startContainer, selecao.startOffset);
                }
                if (selecao.compareBoundaryPoints(Range.END_TO_END, trecho) < 0) {
                    trecho.setEnd(selecao.endContainer, selecao.endOffset);
                }
                partes.push(trecho.toString());
            }
            return partes.join('').trim();
        }

        function atualizarSomaSelecao() {
            frame = null;
            const selecao = global.getSelection();
            const tabela = document.getElementById('tblResultado');
            const corpo = tabela?.tBodies[0];
            if (!corpo || selecao?.rangeCount !== 1 || selecao.isCollapsed
                || !corpo.contains(selecao.anchorNode) || !corpo.contains(selecao.focusNode)
                || elementoDoNo(document.activeElement)?.closest(ignorar)
                || !tabela.getClientRects().length) return ocultarSomaSelecao();

            const intervalo = selecao.getRangeAt(0);
            let soma = 0;
            let quantidade = 0;
            for (const celula of corpo.querySelectorAll('td.num')) {
                if (!intervalo.intersectsNode(celula)) continue;
                const texto = textoSelecionado(celula, intervalo).replace(/\u2212/g, '-');
                if (!/^[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?$/.test(texto)) continue;
                const valor = Number(texto.replace(/\./g, '').replace(',', '.'));
                if (!Number.isFinite(valor)) continue;
                soma += valor;
                quantidade += 1;
            }
            if (!quantidade || !Number.isFinite(soma)) return ocultarSomaSelecao();

            // A ponta ativa acompanha tanto o arrasto normal quanto a seleção invertida.
            const ponta = intervalo.cloneRange();
            ponta.collapse(selecao.focusNode === intervalo.startContainer && selecao.focusOffset === intervalo.startOffset);
            const rect = ponta.getClientRects()[0] || intervalo.getBoundingClientRect();
            const area = tabela.closest('#secaoTabelaPrincipal')?.getBoundingClientRect() || tabela.getBoundingClientRect();
            if (rect.bottom < Math.max(0, area.top) || rect.top > Math.min(global.innerHeight, area.bottom)
                || rect.right < Math.max(0, area.left) || rect.left > Math.min(global.innerWidth, area.right)) {
                return ocultarSomaSelecao();
            }

            if (!balao) {
                balao = document.createElement('div');
                balao.id = 'balaoSomaSelecao';
                balao.className = 'soma-selecao-balao';
                balao.setAttribute('role', 'status');
                balao.setAttribute('aria-live', 'polite');
                balao.setAttribute('aria-atomic', 'true');
                document.body.appendChild(balao);
            }
            balao.textContent = 'Soma: ' + numero(Number(soma.toFixed(2)) || 0) + ' · ' + quantidade + (quantidade === 1 ? ' valor' : ' valores');
            balao.hidden = false;
            const tamanho = balao.getBoundingClientRect();
            const margem = 8;
            const left = Math.max(margem, Math.min(rect.right, global.innerWidth - tamanho.width - margem));
            let top = rect.bottom + margem;
            if (top + tamanho.height > global.innerHeight - margem) top = rect.top - tamanho.height - margem;
            balao.style.left = Math.round(left) + 'px';
            balao.style.top = Math.round(Math.max(margem, top)) + 'px';
        }

        function iniciarSomaSelecao() {
            if (iniciado) return;
            iniciado = true;
            document.addEventListener('selectionchange', () => {
                if (frame === null) frame = global.requestAnimationFrame(atualizarSomaSelecao);
            });
            document.addEventListener('keydown', evento => {
                if (evento.key === 'Escape') ocultarSomaSelecao();
            });
            document.addEventListener('focusin', evento => {
                if (elementoDoNo(evento.target)?.closest(ignorar)) ocultarSomaSelecao();
            });
            global.addEventListener('scroll', ocultarSomaSelecao, true);
            global.addEventListener('resize', ocultarSomaSelecao);
            global.addEventListener('blur', ocultarSomaSelecao);
            global.addEventListener('pagehide', ocultarSomaSelecao);
        }

        return { iniciarSomaSelecao, ocultarSomaSelecao };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
