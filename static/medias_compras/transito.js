(function installJKMediasTransito(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('transito', ['core', 'state', 'api'], (context) => {
        const fetch = context.modules.api.request;
        const obterAuthHeaders = context.modules.api.authHeaders;

        function normalizarDetalhesEstoqueEmTransito(item) {
            const detalhes = Array.isArray(item && item.estoque_em_transito_listas)
                ? item.estoque_em_transito_listas
                : [];
            return detalhes
                .map((detalhe) => ({
                    nome_lista: String((detalhe && detalhe.nome_lista) || '').trim() || 'Sem nome',
                    quantidade: Number((detalhe && detalhe.quantidade) || 0)
                }))
                .filter((detalhe) => Number.isFinite(detalhe.quantidade) && detalhe.quantidade > 0);
        }

        function obterBalaoEstoqueEmTransito() {
            let balao = document.getElementById('balaoEstoqueEmTransito');
            if (balao) return balao;
            balao = document.createElement('div');
            balao.id = 'balaoEstoqueEmTransito';
            balao.className = 'transito-balao';
            balao.setAttribute('role', 'tooltip');
            balao.setAttribute('aria-hidden', 'true');
            document.body.appendChild(balao);
            return balao;
        }

        function posicionarBalaoEstoqueEmTransito(alvo, balao) {
            if (!alvo || !balao) return;
            const margem = 12;
            const espaco = 8;
            const alvoRect = alvo.getBoundingClientRect();
            const balaoRect = balao.getBoundingClientRect();
            let left = alvoRect.left + (alvoRect.width / 2) - (balaoRect.width / 2);
            left = Math.max(margem, Math.min(left, window.innerWidth - balaoRect.width - margem));
            let top = alvoRect.bottom + espaco;
            if (top + balaoRect.height > window.innerHeight - margem) {
                top = Math.max(margem, alvoRect.top - balaoRect.height - espaco);
            }
            balao.style.left = Math.round(left) + 'px';
            balao.style.top = Math.round(top) + 'px';
        }

        function mostrarBalaoEstoqueEmTransito(alvo, total, detalhes) {
            if (!alvo || !alvo.isConnected || !Array.isArray(detalhes) || detalhes.length < 1) return;
            configurarEventosGlobaisBalaoEstoqueEmTransito();
            const balao = obterBalaoEstoqueEmTransito();
            const itensHtml = detalhes.map((detalhe) => (
                '<li class="transito-balao-item">' +
                    '<span class="transito-balao-nome">' + escaparHtml(detalhe.nome_lista) + '</span>' +
                    '<strong class="transito-balao-quantidade">' + numero(detalhe.quantidade) + '</strong>' +
                '</li>'
            )).join('');
            balao.innerHTML =
                '<div class="transito-balao-cabecalho"><span>Em trânsito</span><strong>Total ' + numero(total) + '</strong></div>' +
                '<ul class="transito-balao-lista">' + itensHtml + '</ul>';
            balao.classList.add('visivel');
            balao.setAttribute('aria-hidden', 'false');
            alvoBalaoEstoqueEmTransitoAtual = alvo;
            if (frameBalaoEstoqueEmTransito !== null) {
                window.cancelAnimationFrame(frameBalaoEstoqueEmTransito);
            }
            frameBalaoEstoqueEmTransito = window.requestAnimationFrame(() => {
                frameBalaoEstoqueEmTransito = null;
                if (
                    alvoBalaoEstoqueEmTransitoAtual !== alvo ||
                    !alvo.isConnected ||
                    !balao.classList.contains('visivel')
                ) return;
                posicionarBalaoEstoqueEmTransito(alvo, balao);
            });
        }

        function ocultarBalaoEstoqueEmTransito() {
            alvoBalaoEstoqueEmTransitoAtual = null;
            if (frameBalaoEstoqueEmTransito !== null) {
                window.cancelAnimationFrame(frameBalaoEstoqueEmTransito);
                frameBalaoEstoqueEmTransito = null;
            }
            const balao = document.getElementById('balaoEstoqueEmTransito');
            if (!balao) return;
            balao.classList.remove('visivel');
            balao.setAttribute('aria-hidden', 'true');
        }

        function configurarEventosGlobaisBalaoEstoqueEmTransito() {
            if (eventosGlobaisBalaoEstoqueEmTransitoConfigurados) return;
            eventosGlobaisBalaoEstoqueEmTransitoConfigurados = true;
            window.addEventListener('scroll', ocultarBalaoEstoqueEmTransito, true);
            window.addEventListener('resize', ocultarBalaoEstoqueEmTransito);
            window.addEventListener('blur', ocultarBalaoEstoqueEmTransito);
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState !== 'visible') ocultarBalaoEstoqueEmTransito();
            });
        }

        function renderizarCelulaEstoqueEmTransito(item) {
            const total = Number((item && item.estoque_em_transito) || 0);
            const detalhes = normalizarDetalhesEstoqueEmTransito(item);
            const temDetalhes = detalhes.length > 0;
            const resumo = detalhes.map((detalhe) => detalhe.nome_lista + ': ' + numero(detalhe.quantidade)).join('; ');
            const ariaLabel = 'Em trânsito: ' + numero(total) + (resumo ? '. ' + resumo : '');
            const classeDetalhes = temDetalhes ? ' transito-quantidade--detalhes' : '';
            const foco = temDetalhes ? ' tabindex="0"' : '';
            return '<td class="num transito-cell"><span class="transito-quantidade' + classeDetalhes + '" aria-label="' + escaparHtml(ariaLabel) + '"' + foco + '>' + numero(total) + '</span></td>';
        }

        function configurarBalaoEstoqueEmTransito(alvo, total, detalhes) {
            if (!alvo || !Array.isArray(detalhes) || detalhes.length < 1) return;
            const mostrar = () => mostrarBalaoEstoqueEmTransito(alvo, total, detalhes);
            alvo.addEventListener('mouseenter', mostrar);
            alvo.addEventListener('mouseleave', ocultarBalaoEstoqueEmTransito);
            alvo.addEventListener('focus', mostrar);
            alvo.addEventListener('blur', ocultarBalaoEstoqueEmTransito);
            alvo.addEventListener('keydown', (evento) => {
                if (evento.key === 'Escape') ocultarBalaoEstoqueEmTransito();
            });
        }

        return {
            normalizarDetalhesEstoqueEmTransito,
            obterBalaoEstoqueEmTransito,
            posicionarBalaoEstoqueEmTransito,
            mostrarBalaoEstoqueEmTransito,
            ocultarBalaoEstoqueEmTransito,
            configurarEventosGlobaisBalaoEstoqueEmTransito,
            renderizarCelulaEstoqueEmTransito,
            configurarBalaoEstoqueEmTransito
        };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
