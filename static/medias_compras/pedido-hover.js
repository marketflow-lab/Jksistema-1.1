(function installJKMediasPedidoHover(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('pedido-hover', ['core', 'state', 'api', 'listas'], ({ modules }) => {
        const { escaparHtml, numero, ehUrlFotoCadastroProtegida } = modules.core;
        let ativo = null;
        let eventosConfigurados = false;

        function limparDetalhesListaPedido() {
            if (ativo) {
                if (ativo.controller) ativo.controller.abort();
                if (ativo.timer) global.clearTimeout(ativo.timer);
                if (ativo.descricaoAnterior === null) ativo.alvo.removeAttribute('aria-describedby');
                else ativo.alvo.setAttribute('aria-describedby', ativo.descricaoAnterior);
            }
            ativo = null;
            ['balaoFotoListaPedido', 'balaoQuantidadeListaPedido'].forEach((id) => {
                const balao = document.getElementById(id);
                if (!balao) return;
                balao.classList.remove('visivel');
                balao.setAttribute('aria-hidden', 'true');
                balao.replaceChildren();
            });
        }

        function configurarEventos() {
            if (eventosConfigurados) return;
            eventosConfigurados = true;
            global.addEventListener('scroll', limparDetalhesListaPedido, true);
            global.addEventListener('resize', limparDetalhesListaPedido);
            global.addEventListener('blur', limparDetalhesListaPedido);
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState !== 'visible') limparDetalhesListaPedido();
            });
            document.addEventListener('keydown', (evento) => {
                if (evento.key === 'Escape') limparDetalhesListaPedido();
            });
            document.addEventListener('change', (evento) => {
                if (evento.target.id === 'lojaListaPedidoEdit') limparDetalhesListaPedido();
            });
            document.addEventListener('input', (evento) => {
                if (evento.target.matches('#tblListaPedidoItens input[data-kind="sku"]')) {
                    limparDetalhesListaPedido();
                }
            });
            document.addEventListener('click', (evento) => {
                if (evento.target.closest('[data-jk-action="aba"], .lista-item')) limparDetalhesListaPedido();
            });
        }

        function obterBalao(tipo) {
            const id = tipo === 'foto' ? 'balaoFotoListaPedido' : 'balaoQuantidadeListaPedido';
            let balao = document.getElementById(id);
            if (!balao) {
                balao = document.createElement('div');
                balao.id = id;
                balao.className = 'pedido-hover-balao pedido-hover-' + tipo;
                balao.setAttribute('role', 'tooltip');
                balao.setAttribute('aria-hidden', 'true');
                document.body.appendChild(balao);
            }
            return balao;
        }

        function posicionar(registro) {
            if (ativo !== registro || !registro.alvo.isConnected) return;
            const margem = 12;
            const espaco = 10;
            const rect = registro.alvo.getBoundingClientRect();
            const balaoRect = registro.balao.getBoundingClientRect();
            let left = registro.tipo === 'foto'
                ? rect.right + espaco
                : rect.left + rect.width / 2 - balaoRect.width / 2;
            if (registro.tipo === 'foto' && left + balaoRect.width > global.innerWidth - margem) {
                left = rect.left - balaoRect.width - espaco;
            }
            left = Math.max(margem, Math.min(left, global.innerWidth - balaoRect.width - margem));
            let top = rect.bottom + espaco;
            if (registro.tipo === 'foto') top = rect.top + rect.height / 2 - balaoRect.height / 2;
            else if (top + balaoRect.height > global.innerHeight - margem) {
                top = rect.top - balaoRect.height - espaco;
            }
            top = Math.max(margem, Math.min(top, global.innerHeight - balaoRect.height - margem));
            registro.balao.style.left = Math.round(left) + 'px';
            registro.balao.style.top = Math.round(top) + 'px';
        }

        function abrir(alvo, tipo) {
            configurarEventos();
            limparDetalhesListaPedido();
            const balao = obterBalao(tipo);
            const registro = { alvo, tipo, balao, descricaoAnterior: alvo.getAttribute('aria-describedby') };
            ativo = registro;
            alvo.setAttribute('aria-describedby', [registro.descricaoAnterior, balao.id].filter(Boolean).join(' '));
            balao.classList.add('visivel');
            balao.setAttribute('aria-hidden', 'false');
            return registro;
        }

        function mostrarFoto(alvo) {
            if (ativo && ativo.alvo === alvo) return;
            const fonteProtegida = alvo.getAttribute('data-jk-auth-src') || '';
            const fonte = alvo.currentSrc || alvo.getAttribute('src') || '';
            if (!fonte && !fonteProtegida) return;
            const registro = abrir(alvo, 'foto');
            const imagem = document.createElement('img');
            imagem.alt = alvo.alt || 'Foto do produto ampliada';
            imagem.addEventListener('load', () => posicionar(registro));
            imagem.addEventListener('error', () => {
                if (ativo !== registro) return;
                registro.balao.textContent = 'Não foi possível ampliar esta foto.';
                posicionar(registro);
            });
            registro.balao.appendChild(imagem);
            if (fonte && !ehUrlFotoCadastroProtegida(fonte)) {
                imagem.src = fonte;
            } else if (fonteProtegida && global.JKAuthenticatedMedia) {
                imagem.setAttribute('data-jk-auth-src', fonteProtegida);
                global.JKAuthenticatedMedia.hidratarImagem(imagem, fonteProtegida).then(() => {
                    if (ativo !== registro) return;
                    if (!imagem.getAttribute('src')) registro.balao.textContent = 'Não foi possível ampliar esta foto.';
                    posicionar(registro);
                });
            } else {
                registro.balao.textContent = 'Foto indisponível.';
            }
            posicionar(registro);
        }

        function quantidadeTexto(valor) {
            return typeof valor === 'number' && Number.isFinite(valor) ? numero(valor) : 'Indisponível';
        }

        function escopoAtual(linha) {
            const campoSku = linha.querySelector('input[data-kind="sku"]');
            return {
                sku: String(campoSku && campoSku.dataset.originalSku || '').trim(),
                skuEditado: String(campoSku && campoSku.value || '').trim(),
                loja: modules.listas.obterLojaListaPedidoSelecionada(),
                store_id: modules.listas.obterStoreIdListaPedidoSelecionado(),
                lista: app.state.listaPedidoAtual
            };
        }

        function continuaAtual(registro) {
            if (ativo !== registro || !registro.alvo.isConnected || app.state.abaAtual !== 'listas_pedidos') return false;
            const atual = escopoAtual(registro.linha);
            return atual.lista === registro.escopo.lista && atual.sku === registro.escopo.sku
                && atual.skuEditado === registro.escopo.skuEditado
                && atual.store_id === registro.escopo.store_id && atual.loja === registro.escopo.loja;
        }

        async function mostrarQuantidade(alvo, linha) {
            if (ativo && ativo.alvo === alvo) return;
            const registro = abrir(alvo, 'quantidade');
            registro.linha = linha;
            registro.escopo = escopoAtual(linha);
            const { sku, skuEditado, loja, store_id } = registro.escopo;
            registro.balao.innerHTML = '<div class="pedido-hover-cabecalho">SKU ' + escaparHtml(sku) + '</div>';
            if (!store_id || !loja) {
                registro.balao.append('Selecione a loja da lista para consultar o estoque.');
                posicionar(registro);
                return;
            }
            if (!sku || skuEditado !== sku) {
                registro.balao.append('Conclua a alteração do SKU para consultar o estoque.');
                posicionar(registro);
                return;
            }
            registro.balao.innerHTML += '<div class="pedido-hover-loja">' + escaparHtml(loja) + '</div>'
                + '<div class="pedido-hover-saldo"><span>Estoque atual</span><strong>Carregando...</strong></div>'
                + '<div class="pedido-hover-saldo"><span>Pedidos a caminho</span><strong>Carregando...</strong></div>';
            posicionar(registro);
            registro.controller = new global.AbortController();
            let expirou = false;
            registro.timer = global.setTimeout(() => {
                expirou = true;
                registro.controller.abort();
            }, 5000);
            try {
                const params = new URLSearchParams({ sku, loja, store_id });
                const resposta = await modules.api.request('/api/medias-compras/posicao-estoque?' + params, {
                    headers: modules.api.authHeaders(),
                    cache: 'no-store',
                    signal: registro.controller.signal
                });
                if (!resposta.ok) throw new Error('Consulta indisponível.');
                const dados = await resposta.json();
                if (!continuaAtual(registro)) return;
                if (String(dados.store_id || '') !== store_id || String(dados.sku || '') !== sku) {
                    throw new Error('Resposta de outro SKU ou loja.');
                }
                const detalhes = Array.isArray(dados.estoque_em_transito_listas)
                    ? dados.estoque_em_transito_listas.filter(item => Number.isFinite(item.quantidade) && item.quantidade > 0)
                    : [];
                registro.balao.innerHTML = '<div class="pedido-hover-cabecalho">SKU ' + escaparHtml(sku) + '</div>'
                    + '<div class="pedido-hover-loja">' + escaparHtml(dados.loja || loja) + '</div>'
                    + '<div class="pedido-hover-saldo"><span>Estoque atual</span><strong>' + quantidadeTexto(dados.saldo_atual_estoque) + '</strong></div>'
                    + '<div class="pedido-hover-saldo"><span>Pedidos a caminho</span><strong>' + quantidadeTexto(dados.estoque_em_transito) + '</strong></div>'
                    + (detalhes.length ? '<ul class="pedido-hover-lista">' + detalhes.map(item =>
                        '<li><span>' + escaparHtml(item.nome_lista || 'Sem nome') + '</span><strong>' + numero(item.quantidade) + '</strong></li>'
                    ).join('') + '</ul>' : '')
                    + (dados.estoque_em_transito === 0 ? '<div class="pedido-hover-nota">Nenhum pedido a caminho.</div>' : '')
                    + (dados.estoque_atualizado_em ? '<div class="pedido-hover-nota">Estoque sincronizado em '
                        + escaparHtml(dados.estoque_atualizado_em) + '</div>' : '');
            } catch (_erro) {
                if (!continuaAtual(registro)) return;
                registro.balao.innerHTML = '<div class="pedido-hover-cabecalho">SKU ' + escaparHtml(sku) + '</div>'
                    + '<div class="pedido-hover-saldo"><span>Estoque atual</span><strong>Indisponível</strong></div>'
                    + '<div class="pedido-hover-saldo"><span>Pedidos a caminho</span><strong>Indisponível</strong></div>'
                    + '<div class="pedido-hover-nota">' + (expirou
                        ? 'A consulta demorou demais. Passe o mouse novamente para tentar.'
                        : 'Não foi possível consultar. Passe o mouse novamente para tentar.') + '</div>';
            } finally {
                global.clearTimeout(registro.timer);
                registro.timer = null;
                if (continuaAtual(registro)) posicionar(registro);
            }
        }

        function configurarDetalhesLinhaListaPedido(linha) {
            const foto = linha.querySelector('.foto-sku');
            const quantidade = linha.querySelector('input[data-kind="qtd"]');
            if (foto) {
                foto.tabIndex = 0;
                foto.setAttribute('aria-label', (foto.alt || 'Foto do produto') + '. Passe o mouse ou use o teclado para ampliar.');
            }
            [foto, quantidade].filter(Boolean).forEach(alvo => {
                const mostrar = () => {
                    if (alvo === foto) mostrarFoto(alvo);
                    else void mostrarQuantidade(alvo, linha);
                };
                const ocultar = () => {
                    if (ativo && ativo.alvo === alvo) limparDetalhesListaPedido();
                };
                alvo.addEventListener('mouseenter', mostrar);
                alvo.addEventListener('mouseleave', ocultar);
                alvo.addEventListener('focus', mostrar);
                alvo.addEventListener('blur', ocultar);
            });
        }

        return { limparDetalhesListaPedido, configurarDetalhesLinhaListaPedido };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
