(function installJKMediasTabelaColunas(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('tabela-colunas', ['core', 'state', 'api'], (context) => {
        const LARGURA_MANUAL_MAXIMA = 1200;
        let periodoPerfilLargurasCarregado = null;
        let autoAjusteLargurasPendente = false;
        let contextoMedicaoTexto = null;
        const perfisLargurasSessao = Object.create(null);

        function obterChaveBaseLargura(colKey) {
            return String(colKey || '').startsWith('mes_') ? 'mes' : String(colKey || '');
        }
        function obterLarguraMinimaColuna(colKey) {
            const chaveBase = obterChaveBaseLargura(colKey);
            return Number(LARGURAS_MINIMAS_COLUNAS[chaveBase] || 56);
        }
        function obterLarguraMaximaColuna(colKey) {
            const chaveBase = obterChaveBaseLargura(colKey);
            return Number(LARGURAS_MAXIMAS_COLUNAS[chaveBase] || 220);
        }
        function normalizarLarguraColuna(colKey, valor) {
            const chaveBase = obterChaveBaseLargura(colKey);
            const larguraMinima = obterLarguraMinimaColuna(colKey);
            const larguraPadrao = Number(LARGURAS_PADRAO_COLUNAS[chaveBase] || larguraMinima);
            const larguraInformada = Number(valor);
            const larguraBase = Number.isFinite(larguraInformada) && larguraInformada > 0
                ? Math.round(larguraInformada)
                : larguraPadrao;
            return Math.min(LARGURA_MANUAL_MAXIMA, Math.max(larguraMinima, larguraBase));
        }
        function normalizarPeriodoLarguras(periodo) {
            const periodoNumero = Number(periodo);
            return [3, 6, 12].includes(periodoNumero) ? periodoNumero : 12;
        }
        function obterChaveLargurasColunas(periodo) {
            if (!LS_COL_WIDTHS_KEY) return '';
            return LS_COL_WIDTHS_KEY + '_' + normalizarPeriodoLarguras(periodo) + 'm';
        }
        function perfilPersistidoValido(perfil, periodo) {
            const periodoNormalizado = normalizarPeriodoLarguras(periodo);
            const chavesObrigatorias = ['sku', 'foto', 'titulo_anuncio', 'total_periodo', 'media_mensal', 'saldo_estoque', 'estoque_transito', 'posicao_estoque', 'cobertura_meses', 'compra_sugerida', 'acao']
                .concat(Array.from({ length: periodoNormalizado }, (_valor, indice) => 'mes_' + indice));
            return Boolean(
                perfil
                && typeof perfil === 'object'
                && !Array.isArray(perfil)
                && perfil.schema === PERFIL_LARGURAS_COLUNAS_SCHEMA
                && Number(perfil.period) === periodoNormalizado
                && perfil.initialized === true
                && perfil.widths
                && typeof perfil.widths === 'object'
                && !Array.isArray(perfil.widths)
                && chavesObrigatorias.every(chave => Object.prototype.hasOwnProperty.call(perfil.widths, chave)
                    && Number.isFinite(Number(perfil.widths[chave])) && Number(perfil.widths[chave]) > 0),
            );
        }
        function lerPerfilLargurasPersistido(periodo) {
            const chave = obterChaveLargurasColunas(periodo);
            if (!chave) return null;
            try {
                const perfil = JSON.parse(localStorage.getItem(chave) || 'null');
                return perfilPersistidoValido(perfil, periodo) ? perfil : null;
            } catch (_e) {
                // Um perfil inválido volta ao autoajuste do primeiro uso.
            }
            return null;
        }
        function carregarPerfilLargurasColunas(periodo) {
            const periodoNormalizado = normalizarPeriodoLarguras(periodo);
            if (periodoPerfilLargurasCarregado === periodoNormalizado) return;

            const chavePeriodo = String(periodoNormalizado);
            const perfilPersistido = lerPerfilLargurasPersistido(periodoNormalizado);
            const perfilSessao = perfisLargurasSessao[chavePeriodo];
            const perfil = perfilPersistidoValido(perfilPersistido, periodoNormalizado)
                ? perfilPersistido
                : (perfilPersistidoValido(perfilSessao, periodoNormalizado) ? perfilSessao : null);

            largurasColunas = perfil ? { ...perfil.widths } : {};
            periodoPerfilLargurasCarregado = periodoNormalizado;
            autoAjusteLargurasPendente = !perfil;
        }
        function obterMapaLargurasAtuais() {
            const mapa = {};
            (chavesColunasAtuais || []).forEach((colKey) => {
                mapa[colKey] = normalizarLarguraColuna(colKey, largurasColunas[colKey]);
            });
            return mapa;
        }
        function salvarLargurasColunas(periodoEsperado) {
            if (
                periodoEsperado !== undefined
                && normalizarPeriodoLarguras(periodoEsperado) !== periodoPerfilLargurasCarregado
            ) return false;
            const periodoNormalizado = normalizarPeriodoLarguras(periodoPerfilLargurasCarregado || periodoAtual);
            const chavePeriodo = String(periodoNormalizado);
            const perfil = {
                schema: PERFIL_LARGURAS_COLUNAS_SCHEMA,
                period: periodoNormalizado,
                initialized: true,
                widths: obterMapaLargurasAtuais(),
            };
            perfisLargurasSessao[chavePeriodo] = perfil;
            const chavePersistencia = obterChaveLargurasColunas(periodoNormalizado);
            if (!chavePersistencia) return true;

            try {
                localStorage.setItem(chavePersistencia, JSON.stringify(perfil));
            } catch (_e) {
                // Sem bloqueio: persistência é complementar.
            }
            return true;
        }

        function salvarLargurasColunasPedidos() {
            try {
                localStorage.setItem(LS_PEDIDOS_COL_WIDTHS_KEY, JSON.stringify(largurasColunasPedidos));
            } catch (_e) {
                // Sem bloqueio: persistência é complementar.
            }
        }

        function sanearLargurasColunas(chavesColunas) {
            let alterou = false;
            if (!largurasColunas || typeof largurasColunas !== 'object' || Array.isArray(largurasColunas)) {
                largurasColunas = {};
                alterou = true;
            }

            (chavesColunas || []).forEach((colKey) => {
                const temLargura = Object.prototype.hasOwnProperty.call(largurasColunas, colKey);
                const larguraNormalizada = temLargura
                    ? normalizarLarguraColuna(colKey, largurasColunas[colKey])
                    : (autoAjusteLargurasPendente
                        ? obterLarguraMinimaColuna(colKey)
                        : normalizarLarguraColuna(colKey, undefined));
                if (largurasColunas[colKey] !== larguraNormalizada) {
                    largurasColunas[colKey] = larguraNormalizada;
                    alterou = true;
                }
            });
            if (alterou && !autoAjusteLargurasPendente) salvarLargurasColunas();
        }
        function sincronizarLarguraTabelaPrincipal() {
            const tabela = document.getElementById('tblResultado');
            if (!tabela || !chavesColunasAtuais.length) return;
            const larguraTotal = chavesColunasAtuais.reduce(
                (total, colKey) => total + normalizarLarguraColuna(colKey, largurasColunas[colKey]),
                0,
            );
            tabela.style.width = larguraTotal + 'px';
            tabela.style.minWidth = larguraTotal + 'px';
        }
        function aplicarLarguraColuna(th, colKey) {
            const largura = normalizarLarguraColuna(colKey, largurasColunas[colKey]);
            th.style.width = largura + 'px';
            th.style.minWidth = largura + 'px';
        }
        function aplicarLarguraColunaPorIndice(colIndex, larguraPx) {
            const col = colElementsAtuais[colIndex];
            if (!col) return;
            const colKey = chavesColunasAtuais[colIndex];
            const largura = normalizarLarguraColuna(colKey, larguraPx);
            col.style.width = largura + 'px';
            col.style.minWidth = largura + 'px';
            largurasColunas[colKey] = largura;
            sincronizarLarguraTabelaPrincipal();
        }
        function obterContextoMedicaoTexto() {
            if (contextoMedicaoTexto) return contextoMedicaoTexto;
            const canvas = document.createElement('canvas');
            contextoMedicaoTexto = canvas.getContext('2d');
            return contextoMedicaoTexto;
        }
        function numeroCss(valor) {
            const numeroValor = Number.parseFloat(valor);
            return Number.isFinite(numeroValor) ? numeroValor : 0;
        }
        function criarMetricasTexto(elemento, pesoFonte) {
            const estilo = getComputedStyle(elemento);
            const fonteCalculada = [estilo.fontStyle, estilo.fontVariant, pesoFonte || estilo.fontWeight, estilo.fontSize, estilo.fontFamily]
                .filter(Boolean).join(' ');
            return {
                font: pesoFonte ? fonteCalculada : (estilo.font || fonteCalculada),
                letterSpacing: numeroCss(estilo.letterSpacing),
                espacoHorizontal: numeroCss(estilo.paddingLeft)
                    + numeroCss(estilo.paddingRight)
                    + numeroCss(estilo.borderLeftWidth)
                    + numeroCss(estilo.borderRightWidth),
            };
        }
        function medirTextoComMetricas(metricas, texto) {
            const contexto = obterContextoMedicaoTexto();
            const conteudo = String(texto || '').trim();
            if (!conteudo) return 0;
            if (!contexto) return conteudo.length * 8;

            contexto.font = metricas.font;
            return conteudo.split(/\r?\n/).reduce((maior, linha) => {
                const largura = contexto.measureText(linha).width
                    + Math.max(0, linha.length - 1) * metricas.letterSpacing;
                return Math.max(maior, largura);
            }, 0);
        }
        function medirTextoElemento(elemento, texto) {
            return medirTextoComMetricas(criarMetricasTexto(elemento), texto);
        }
        function medirLarguraCabecalho(th) {
            const partesMes = Array.from(th.querySelectorAll('.mes-ano-topo, .mes-nome-base'));
            const elementos = partesMes.length
                ? partesMes
                : [th.querySelector('.cabecalho-coluna-label') || th];
            const larguraConteudo = elementos.reduce(
                (maior, elemento) => Math.max(maior, medirTextoElemento(elemento, elemento.textContent)),
                0,
            );
            return larguraConteudo + criarMetricasTexto(th).espacoHorizontal + 8;
        }

        function criarMedidorColuna(primeiraLinha, cabecalho, indice, colKey) {
            const td = primeiraLinha && primeiraLinha.children[indice];
            const botao = td && td.querySelector('button');
            const ehCobertura = colKey === 'cobertura_meses';
            const elementoTexto = (ehCobertura && td)
                || botao
                || (td && td.querySelector('strong, .cobertura-sem-venda, .transito-quantidade'))
                || td
                || cabecalho;
            const metricasTexto = criarMetricasTexto(elementoTexto, ehCobertura ? '800' : '');
            const espacoCelula = td ? criarMetricasTexto(td).espacoHorizontal : 17;
            const espacoControle = botao ? criarMetricasTexto(botao).espacoHorizontal : 0;
            return {
                metricasTexto,
                espacoHorizontal: espacoCelula + espacoControle,
            };
        }

        function obterTextosItemColuna(colKey, item, colunasMeses) {
            if (colKey === 'sku') return [item && item.sku];
            if (colKey === 'foto') return [item && item.foto ? '' : 'Sem foto'];
            if (colKey === 'titulo_anuncio') {
                return [item && item.titulo_anuncio, item && item.aviso_sem_venda];
            }
            if (colKey.startsWith('mes_')) {
                const indiceMes = Number(colKey.slice(4));
                const colunaMes = colunasMeses[indiceMes];
                const valor = colunaMes && item && item.vendas_mensais
                    ? item.vendas_mensais[colunaMes.key]
                    : 0;
                return [numero(valor)];
            }
            if (colKey === 'total_periodo') return [numero(item && item.total_vendas_periodo)];
            if (colKey === 'media_mensal') return [numero(item && item.media_mensal)];
            if (colKey === 'saldo_estoque') return [numero(item && item.saldo_atual_estoque)];
            if (colKey === 'estoque_transito') return [numero(item && item.estoque_em_transito)];
            if (colKey === 'posicao_estoque') return [numero(item && item.posicao_estoque)];
            if (colKey === 'cobertura_meses') {
                const cobertura = calcularCoberturaMeses(
                    item && item.posicao_estoque,
                    item && item.media_mensal,
                );
                return [cobertura === null ? 'Sem venda' : numero(cobertura)];
            }
            if (colKey === 'compra_sugerida') return [numero(item && item.compra_sugerida)];
            if (colKey === 'acao') return ['Reexibir'];
            return [''];
        }

        function normalizarLarguraAutoAjuste(colKey, larguraMedida) {
            const larguraMinima = obterLarguraMinimaColuna(colKey);
            const larguraMaxima = Math.max(larguraMinima, obterLarguraMaximaColuna(colKey));
            return Math.min(larguraMaxima, Math.max(larguraMinima, Math.ceil(Number(larguraMedida) || 0)));
        }

        function autoAjustarLargurasColunasPrimeiroUso(itensBase, colunasMeses) {
            if (!autoAjusteLargurasPendente) return false;
            if (periodoPerfilLargurasCarregado !== normalizarPeriodoLarguras(periodoAtual)) return false;
            if (!Array.isArray(itensBase) || !itensBase.length) return false;

            const tabela = document.getElementById('tblResultado');
            if (!tabela || !chavesColunasAtuais.length) return false;
            const cabecalhos = Array.from(tabela.querySelectorAll('thead th[data-col-key]'));
            const primeiraLinha = Array.from(tabela.querySelectorAll('tbody tr'))
                .find(linha => linha.children.length === chavesColunasAtuais.length);
            const medidores = chavesColunasAtuais.map((colKey, indice) => ({
                colKey,
                ...criarMedidorColuna(primeiraLinha, cabecalhos[indice], indice, colKey),
            }));
            const largurasMedidas = medidores.map((medidor, indice) => {
                const cabecalho = cabecalhos[indice];
                return Math.max(
                    obterLarguraMinimaColuna(medidor.colKey),
                    cabecalho ? medirLarguraCabecalho(cabecalho) : 0,
                );
            });

            itensBase.forEach((item) => {
                medidores.forEach((medidor, indice) => {
                    obterTextosItemColuna(medidor.colKey, item, colunasMeses || []).forEach((texto) => {
                        const largura = medirTextoComMetricas(medidor.metricasTexto, texto)
                            + medidor.espacoHorizontal;
                        largurasMedidas[indice] = Math.max(largurasMedidas[indice], largura);
                    });
                });
            });

            const novasLarguras = {};
            chavesColunasAtuais.forEach((colKey, indice) => {
                if (obterChaveBaseLargura(colKey) === 'acao') {
                    largurasMedidas[indice] = Math.max(largurasMedidas[indice], 100);
                }
                novasLarguras[colKey] = normalizarLarguraAutoAjuste(colKey, largurasMedidas[indice]);
            });

            largurasColunas = novasLarguras;
            chavesColunasAtuais.forEach((colKey, indice) => {
                const col = colElementsAtuais[indice];
                const largura = novasLarguras[colKey];
                if (col) {
                    col.style.width = largura + 'px';
                    col.style.minWidth = largura + 'px';
                }
                if (cabecalhos[indice]) aplicarLarguraColuna(cabecalhos[indice], colKey);
            });
            autoAjusteLargurasPendente = false;
            sincronizarLarguraTabelaPrincipal();
            salvarLargurasColunas();
            return true;
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
                    const larguraMinima = obterLarguraMinimaColuna(colKey);
                    const periodoResize = periodoPerfilLargurasCarregado;

                    function onMouseMove(ev) {
                        if (periodoPerfilLargurasCarregado !== periodoResize) return;
                        const delta = ev.clientX - startX;
                        const novaLargura = normalizarLarguraColuna(colKey, Math.max(larguraMinima, Math.round(startWidth + delta)));
                        th.style.width = novaLargura + 'px';
                        th.style.minWidth = novaLargura + 'px';
                        aplicarLarguraColunaPorIndice(colIndex, novaLargura);
                    }

                    function onMouseUp() {
                        if (periodoPerfilLargurasCarregado !== periodoResize) {
                            document.removeEventListener('mousemove', onMouseMove);
                            document.removeEventListener('mouseup', onMouseUp);
                            return;
                        }
                        const finalWidth = normalizarLarguraColuna(colKey, Math.round(th.getBoundingClientRect().width));
                        th.style.width = finalWidth + 'px';
                        th.style.minWidth = finalWidth + 'px';
                        aplicarLarguraColunaPorIndice(colIndex, finalWidth);
                        salvarLargurasColunas(periodoResize);
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
            obterLarguraMinimaColuna,
            obterLarguraMaximaColuna,
            normalizarLarguraColuna,
            obterChaveLargurasColunas,
            carregarPerfilLargurasColunas,
            sanearLargurasColunas,
            sincronizarLarguraTabelaPrincipal,
            aplicarLarguraColuna,
            aplicarLarguraColunaPorIndice,
            autoAjustarLargurasColunasPrimeiroUso,
            prepararColunasListaPedidos,
            aplicarLarguraColunaPedidoPorIndice,
            habilitarResizeColunasListaPedidos,
            habilitarResizeColunas
        };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
