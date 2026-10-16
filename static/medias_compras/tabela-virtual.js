(function installJKMediasTabelaVirtual(global) {
    'use strict';

    global.JKMedias.defineModule('tabela-virtual', [], () => {
        function criarTabelaVirtual({ raiz, tabela, tbody, colunas, renderizarLinha, overscan = 10 }) {
            let itens = [], alturas = [], offsets = [0], frame = null, encerrada = false;
            let geometriaPendente = false, ancoraPendente = null, ancoraEstavel = null;
            const linhas = new Map();
            const espacadores = new Map();
            const alturaEstimada = 64;
            let ultimaLargura = tabela.getBoundingClientRect().width;
            let ultimaAlturaRaiz = raiz.clientHeight;
            let ultimaAlturaCabecalho = 0;
            const cabecalho = tabela.tHead;
            const chaveItem = item => String(item && item.sku || '');
            const alturaCabecalho = () => cabecalho ? cabecalho.getBoundingClientRect().height : 0;
            const inicioCorpo = () => tabela.getBoundingClientRect().top - raiz.getBoundingClientRect().top
                - raiz.clientTop + raiz.scrollTop + alturaCabecalho();

            function reconstruirOffsets() {
                offsets = [0];
                for (const altura of alturas) offsets.push(offsets[offsets.length - 1] + altura);
            }

            function indiceEm(y) {
                let inicio = 0, fim = itens.length;
                while (inicio < fim) {
                    const meio = (inicio + fim) >>> 1;
                    if (offsets[meio + 1] <= y) inicio = meio + 1;
                    else fim = meio;
                }
                return Math.min(Math.max(0, itens.length - 1), inicio);
            }

            function capturarAncora() {
                if (!itens.length) return null;
                if (!raiz.clientHeight) return ancoraEstavel;
                const y = raiz.scrollTop + alturaCabecalho() - inicioCorpo();
                const indice = indiceEm(Math.max(0, y));
                const maximo = raiz.scrollHeight - raiz.clientHeight;
                return { chave: chaveItem(itens[indice]), indice, deslocamento: y - offsets[indice],
                    fim: maximo > 0 && maximo - raiz.scrollTop <= 2 };
            }

            function scrollDaAncora(ancora) {
                if (!ancora || !itens.length) return raiz.scrollTop;
                const encontrado = itens.findIndex(item => chaveItem(item) === ancora.chave);
                const indice = encontrado >= 0 ? encontrado : Math.min(ancora.indice || 0, itens.length - 1);
                return Math.max(0, inicioCorpo() + offsets[indice] + (ancora.deslocamento || 0) - alturaCabecalho());
            }

            function agendar() {
                if (!encerrada && frame === null) frame = global.requestAnimationFrame(atualizarJanela);
            }

            const observer = typeof global.ResizeObserver === 'function' ? new global.ResizeObserver(entries => {
                let mudou = false;
                for (const entry of entries) {
                    if (entry.target === tabela) {
                        const largura = tabela.getBoundingClientRect().width;
                        if (Math.abs(largura - ultimaLargura) > 0.5) {
                            ultimaLargura = largura;
                            geometriaPendente = true;
                            mudou = true;
                        }
                    } else if (entry.target === raiz) {
                        if (ultimaAlturaRaiz !== raiz.clientHeight) {
                            ultimaAlturaRaiz = raiz.clientHeight;
                            mudou = true;
                        }
                    } else if (entry.target === cabecalho) {
                        const altura = alturaCabecalho();
                        if (Math.abs(altura - ultimaAlturaCabecalho) > 0.25) {
                            ultimaAlturaCabecalho = altura;
                            mudou = true;
                        }
                    } else {
                        const indice = Number(entry.target.dataset.virtualIndex);
                        if (linhas.get(indice) === entry.target &&
                            Math.abs(entry.target.getBoundingClientRect().height - alturas[indice]) > 0.25) mudou = true;
                    }
                }
                if (mudou) agendar();
            }) : null;

            function espacador(de, ate) {
                const chave = de + ':' + ate;
                let linha = espacadores.get(chave);
                if (!linha) {
                    linha = document.createElement('tr');
                    linha.className = 'virtual-spacer';
                    linha.setAttribute('aria-hidden', 'true');
                    const celula = document.createElement('td');
                    celula.colSpan = colunas;
                    linha.appendChild(celula);
                    espacadores.set(chave, linha);
                }
                linha.firstChild.style.height = Math.max(0, offsets[ate] - offsets[de]) + 'px';
                return linha;
            }

            function atualizarJanela() {
                frame = null;
                if (encerrada || !itens.length || !raiz.clientHeight) return;
                const ancora = ancoraPendente || (geometriaPendente && ancoraEstavel) || capturarAncora();
                ancoraPendente = null;
                let mudouAltura = geometriaPendente;
                if (geometriaPendente) alturas.fill(alturaEstimada);
                geometriaPendente = false;
                for (const [indice, linha] of linhas) {
                    const medida = linha.getBoundingClientRect().height;
                    if (medida > 0 && Math.abs(medida - alturas[indice]) > 0.25) {
                        alturas[indice] = medida;
                        mudouAltura = true;
                    }
                }
                if (mudouAltura) reconstruirOffsets();
                const restaurar = ancora && (mudouAltura || ancora.restaurar || ancora.fim);
                const scroll = restaurar
                    ? (ancora.fim ? Math.max(0, inicioCorpo() + offsets[itens.length] - raiz.clientHeight) : scrollDaAncora(ancora))
                    : raiz.scrollTop;
                const y = scroll - inicioCorpo();
                const renderizarTudo = !observer;
                const inicio = renderizarTudo ? 0 : Math.max(0, indiceEm(Math.max(0, y + alturaCabecalho())) - overscan);
                const fim = renderizarTudo ? itens.length : Math.min(itens.length, indiceEm(Math.max(0, y + raiz.clientHeight)) + overscan + 1);
                const indices = new Set();
                for (let i = inicio; i < fim; i++) indices.add(i);
                const ativa = document.activeElement && document.activeElement.closest('[data-jk-virtual-row]');
                if (ativa && tbody.contains(ativa)) indices.add(Number(ativa.dataset.virtualIndex));
                const ordenados = [...indices].sort((a, b) => a - b);
                const desejados = [];
                let anterior = 0;
                for (const indice of ordenados) {
                    if (indice > anterior) desejados.push(espacador(anterior, indice));
                    let linha = linhas.get(indice);
                    if (!linha) {
                        linha = renderizarLinha(itens[indice], indice);
                        linha.dataset.jkVirtualRow = '1';
                        linha.dataset.virtualIndex = String(indice);
                        linha.dataset.virtualKey = chaveItem(itens[indice]);
                        linha.setAttribute('aria-rowindex', String(indice + 2));
                        linhas.set(indice, linha);
                        if (observer) observer.observe(linha);
                    }
                    desejados.push(linha);
                    anterior = indice + 1;
                }
                if (anterior < itens.length) desejados.push(espacador(anterior, itens.length));
                const manter = new Set(desejados);
                for (const [indice, linha] of linhas) {
                    if (manter.has(linha)) continue;
                    if (observer) observer.unobserve(linha);
                    linha.remove();
                    linhas.delete(indice);
                }
                for (const [chave, linha] of espacadores) {
                    if (manter.has(linha)) continue;
                    linha.remove();
                    espacadores.delete(chave);
                }
                let cursor = tbody.firstChild;
                for (const node of desejados) {
                    if (node === cursor) cursor = cursor.nextSibling;
                    else tbody.insertBefore(node, cursor);
                }
                if (restaurar) {
                    raiz.scrollTop = ancora.fim ? raiz.scrollHeight - raiz.clientHeight : scroll;
                }
                ancoraEstavel = capturarAncora();
                // Novas linhas so podem ser medidas depois de inseridas. O observador
                // confirma a altura sem exigir a montagem das linhas fora da janela.
                if (!observer && desejados.some(node => node.dataset.jkVirtualRow)) {
                    for (const [indice, linha] of linhas) alturas[indice] = linha.getBoundingClientRect().height || alturaEstimada;
                    reconstruirOffsets();
                }
            }

            function atualizarItens(novosItens, { ancora = null } = {}) {
                if (frame !== null) { global.cancelAnimationFrame(frame); frame = null; }
                for (const linha of linhas.values()) { if (observer) observer.unobserve(linha); }
                linhas.clear();
                espacadores.clear();
                tbody.replaceChildren();
                itens = Array.isArray(novosItens) ? novosItens : [];
                ancoraEstavel = null;
                alturas = itens.map(() => alturaEstimada);
                reconstruirOffsets();
                tabela.setAttribute('aria-rowcount', String(itens.length + 1));
                tabela.dataset.totalRows = String(itens.length);
                ancoraPendente = ancora ? { ...ancora, restaurar: true } : null;
                atualizarJanela();
            }

            function invalidarGeometria() { geometriaPendente = true; agendar(); }
            function garantirLinhaVisivel(chave) {
                const indice = itens.findIndex(item => chaveItem(item) === String(chave));
                if (indice < 0) return false;
                ancoraPendente = { chave: chaveItem(itens[indice]), indice, deslocamento: 0, restaurar: true };
                agendar();
                return true;
            }
            function depoisDoFoco() { Promise.resolve().then(agendar); }
            function aoRolar() {
                if (!geometriaPendente && Math.abs(tabela.getBoundingClientRect().width - ultimaLargura) <= 0.5) {
                    ancoraEstavel = capturarAncora();
                }
                agendar();
            }
            raiz.addEventListener('scroll', aoRolar, { passive: true });
            tbody.addEventListener('focusin', agendar);
            tbody.addEventListener('focusout', depoisDoFoco);
            global.addEventListener('resize', invalidarGeometria);
            if (observer) {
                observer.observe(raiz);
                observer.observe(tabela);
                if (cabecalho) observer.observe(cabecalho);
            }
            function destruir() {
                encerrada = true;
                if (frame !== null) global.cancelAnimationFrame(frame);
                if (observer) observer.disconnect();
                raiz.removeEventListener('scroll', aoRolar);
                tbody.removeEventListener('focusin', agendar);
                tbody.removeEventListener('focusout', depoisDoFoco);
                global.removeEventListener('resize', invalidarGeometria);
            }
            return { atualizarItens, capturarAncora, invalidarGeometria, garantirLinhaVisivel, destruir };
        }
        return { criarTabelaVirtual };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
