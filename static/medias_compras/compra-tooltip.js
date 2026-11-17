(function installJKMediasCompraTooltip(global) {
    'use strict';

    global.JKMedias.defineModule('compra-tooltip', ['core'], () => {
        let alvoAtual = null;
        let balao = null;
        let timerSaida = null;
        let eventosInstalados = false;

        function cancelarSaida() {
            if (timerSaida !== null) global.clearTimeout(timerSaida);
            timerSaida = null;
        }

        function ocultarTooltipCompra() {
            cancelarSaida();
            alvoAtual?.removeAttribute('aria-describedby');
            alvoAtual = null;
            if (!balao) return;
            balao.hidden = true;
            balao.setAttribute('aria-hidden', 'true');
        }

        function agendarSaida() {
            cancelarSaida();
            timerSaida = global.setTimeout(() => {
                timerSaida = null;
                if (alvoAtual?.isConnected && (
                    alvoAtual.matches(':hover') || document.activeElement === alvoAtual || balao?.matches(':hover')
                )) return;
                ocultarTooltipCompra();
            }, 140);
        }

        function posicionar() {
            if (!alvoAtual?.isConnected || !balao || balao.hidden) return ocultarTooltipCompra();
            const margem = 12;
            const espaco = 7;
            const alvo = alvoAtual.getBoundingClientRect();
            const rect = balao.getBoundingClientRect();
            const cabeEsquerda = alvo.left - rect.width - espaco >= margem;
            const cabeDireita = alvo.right + espaco + rect.width <= global.innerWidth - margem;
            const left = cabeEsquerda ? alvo.left - rect.width - espaco
                : cabeDireita ? alvo.right + espaco : Math.max(margem, Math.min(
                    alvo.left + alvo.width / 2 - rect.width / 2,
                    global.innerWidth - rect.width - margem,
                ));
            const abaixo = alvo.bottom + espaco;
            const acima = alvo.top - rect.height - espaco;
            const top = cabeEsquerda || cabeDireita
                ? Math.max(margem, Math.min(alvo.top, global.innerHeight - rect.height - margem))
                : abaixo + rect.height <= global.innerHeight - margem ? abaixo
                    : acima >= margem ? acima : Math.max(margem, global.innerHeight - rect.height - margem);
            balao.style.left = Math.round(left) + 'px';
            balao.style.top = Math.round(top) + 'px';
        }

        function obterBalao() {
            if (balao) return balao;
            balao = document.createElement('div');
            balao.id = 'balaoPlanejamentoCompra';
            balao.className = 'compra-planejamento-balao';
            balao.setAttribute('role', 'tooltip');
            balao.setAttribute('aria-hidden', 'true');
            balao.hidden = true;
            balao.addEventListener('mouseenter', cancelarSaida);
            balao.addEventListener('mouseleave', agendarSaida);
            document.body.appendChild(balao);
            return balao;
        }

        function instalarEventos() {
            if (eventosInstalados) return;
            eventosInstalados = true;
            global.addEventListener('resize', posicionar);
            global.addEventListener('blur', ocultarTooltipCompra);
            global.addEventListener('scroll', evento => {
                if (balao?.contains(evento.target)) return;
                if (alvoAtual && (alvoAtual.matches(':hover') || document.activeElement === alvoAtual || balao?.matches(':hover'))) {
                    posicionar();
                } else ocultarTooltipCompra();
            }, true);
            document.addEventListener('keydown', evento => {
                if (evento.key === 'Escape') ocultarTooltipCompra();
            });
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState !== 'visible') ocultarTooltipCompra();
            });
        }

        function mostrar(alvo, item, manual) {
            if (!alvo.isConnected) return;
            ocultarTooltipCompra();
            instalarEventos();
            const el = obterBalao();
            const titulo = document.createElement('strong');
            titulo.className = 'compra-planejamento-titulo';
            titulo.textContent = 'Planejamento da compra';
            el.replaceChildren(titulo);
            for (const texto of descricaoPlanejamentoCompra(item, manual).split('\n')) {
                const linha = document.createElement('p');
                linha.textContent = texto;
                if (texto.startsWith('Reserva insuficiente:') || texto.startsWith('Falta antes da entrega:')) {
                    linha.className = 'compra-planejamento-aviso';
                }
                el.appendChild(linha);
            }
            alvoAtual = alvo;
            alvo.setAttribute('aria-describedby', el.id);
            el.hidden = false;
            el.setAttribute('aria-hidden', 'false');
            posicionar();
        }

        function configurarTooltipCompra(alvo, item, manual) {
            if (!alvo || !obterPlanejamentoCompra(item)) return;
            alvo.removeAttribute('title');
            alvo.addEventListener('mouseenter', () => mostrar(alvo, item, manual));
            alvo.addEventListener('mouseleave', agendarSaida);
            alvo.addEventListener('focus', () => mostrar(alvo, item, manual));
            alvo.addEventListener('blur', agendarSaida);
            alvo.addEventListener('click', ocultarTooltipCompra);
        }

        return { configurarTooltipCompra, ocultarTooltipCompra };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
