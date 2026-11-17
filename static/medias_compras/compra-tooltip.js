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
            const resumo = resumoPlanejamentoCompra(item, manual);
            if (!resumo) return;
            instalarEventos();
            const el = obterBalao();
            const texto = (tag, classe, valor) => {
                const elemento = document.createElement(tag);
                elemento.className = classe;
                elemento.textContent = valor;
                return elemento;
            };
            const cabecalho = document.createElement('div');
            cabecalho.className = 'compra-planejamento-cabecalho';
            cabecalho.append(
                texto('strong', 'compra-planejamento-titulo', resumo.titulo),
                texto('strong', 'compra-planejamento-quantidade', resumo.quantidade),
            );
            el.replaceChildren(cabecalho);
            if (resumo.entrega) el.appendChild(texto('p', 'compra-planejamento-entrega', resumo.entrega));
            const metricas = document.createElement('dl');
            metricas.className = 'compra-planejamento-metricas';
            for (const metrica of resumo.metricas || []) {
                const celula = document.createElement('div');
                celula.className = 'compra-planejamento-metrica';
                celula.append(
                    texto('dt', '', metrica.rotulo),
                    texto('dd', '', metrica.valor),
                );
                metricas.appendChild(celula);
            }
            el.appendChild(metricas);
            if (resumo.alerta?.texto) {
                const classe = 'compra-planejamento-alerta'
                    + (resumo.alerta.tipo === 'info' ? ' compra-planejamento-alerta--info' : '');
                el.appendChild(texto('p', classe, resumo.alerta.texto));
            }
            if (resumo.tendencia?.texto) el.appendChild(texto('p', 'compra-planejamento-tendencia', resumo.tendencia.texto));
            if (resumo.tendencia?.explicacao) el.appendChild(texto('p', 'compra-planejamento-explicacao', resumo.tendencia.explicacao));
            if (resumo.avisos?.length) {
                const avisos = document.createElement('ul');
                avisos.className = 'compra-planejamento-avisos';
                for (const aviso of resumo.avisos) avisos.appendChild(texto('li', '', aviso));
                el.appendChild(avisos);
            }
            if (resumo.rodape) el.appendChild(texto('p', 'compra-planejamento-rodape', resumo.rodape));
            if (resumo.notaManual) el.appendChild(texto('p', 'compra-planejamento-manual', resumo.notaManual));
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
