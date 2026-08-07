// ==================== GRÁFICOS ====================
let chartInstance = null;
let chartEstoqueInstance = null;
let chartSkusEstoqueInstance = null;
let periodoGrafico = '3m';
let tipoGrafico = 'linha';
let metricaGrafico = 'valor';
let intervaloGrafico = 'dia';
let skuAtualGrafico = null;
let mostrarEstoqueGeralGrafico = true;
let mostrarEstoqueSkuGrafico = false;
let carregarGraficoToken = 0;
let carregarGraficoController = null;
let carregarGraficoPromise = null;
let carregarGraficoRequestKey = '';
let rankingSidebarModo = 'vendidos';
let compararAnoPassado = false;
const GRAFICO_ESTOQUE_LAYOUT_VERSION = 2;
let rankingVendidosTop5 = [];
let rankingDevolTop5 = [];
let rankingVendidosTop20 = [];
let rankingDevolTop20 = [];
let rankingVendidosComDevolTodos = [];
let rankingDevolTodos = [];
let ociososSidebarModo = '30dias';
let ociososDados = { ultimos_30_dias: [], ultimos_60_dias: [], ultimos_90_dias: [] };

function renderizarRankingModalVendas(listaTop20, modo) {
    if (!Array.isArray(listaTop20) || listaTop20.length === 0) {
        return '<li>Sem dados para o período/filtros selecionados.</li>';
    }

    if (modo === 'devolucoes') {
        return listaTop20.map((item, idx) => {
            const qtd = Math.round(Number(item?.itens || 0)).toLocaleString('pt-BR');
            const taxa = Number(item?.taxa || 0).toFixed(1).replace('.', ',');
            return `<li><strong>${idx + 1}. ${item?.sku || 'N/D'}</strong>: ${qtd} item(ns) devolvidos | taxa ${taxa}%</li>`;
        }).join('');
    } else {
        return listaTop20.map((item, idx) => {
            const qtd = Math.round(Number(item?.itens || 0)).toLocaleString('pt-BR');
            const valor = Number(item?.valor || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
            return `<li><strong>${idx + 1}. ${item?.sku || 'N/D'}</strong>: ${qtd} item(ns) | ${valor}</li>`;
        }).join('');
    }
}

function atualizarBotoesRankingSidebar() {
    if (btnRankingVendidosVendas) {
        btnRankingVendidosVendas.classList.toggle('active', rankingSidebarModo === 'vendidos');
    }
    if (btnRankingDevolucoesVendas) {
        btnRankingDevolucoesVendas.classList.toggle('active', rankingSidebarModo === 'devolucoes');
    }
}

function renderRankingSidebarVendas() {
    if (!graficoRankingVendas) return;
    const listaAtiva = rankingSidebarModo === 'devolucoes' ? rankingDevolTop5 : rankingVendidosTop5;
    if (!Array.isArray(listaAtiva) || listaAtiva.length === 0) {
        graficoRankingVendas.innerHTML = '<li>Sem dados para o período/filtros selecionados.</li>';
        atualizarBotoesRankingSidebar();
        return;
    }

    if (rankingSidebarModo === 'devolucoes') {
        graficoRankingVendas.innerHTML = listaAtiva.map(item => {
            const qtd = Math.round(Number(item?.itens || 0)).toLocaleString('pt-BR');
            const taxa = Number(item?.taxa || 0).toFixed(1).replace('.', ',');
            return `<li><strong>${item?.sku || 'N/D'}</strong>: ${qtd} item(ns) devolvidos | taxa ${taxa}%</li>`;
        }).join('');
    } else {
        graficoRankingVendas.innerHTML = listaAtiva.map(item => {
            const qtd = Math.round(Number(item?.itens || 0)).toLocaleString('pt-BR');
            const valor = Number(item?.valor || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
            return `<li><strong>${item?.sku || 'N/D'}</strong>: ${qtd} item(ns) | ${valor}</li>`;
        }).join('');
    }
    atualizarBotoesRankingSidebar();
}

function atualizarBotoesOciosos() {
    if (btnOciosos7d) btnOciosos7d.classList.toggle('active', ociososSidebarModo === '7dias');
    if (btnOciosos15d) btnOciosos15d.classList.toggle('active', ociososSidebarModo === '15dias');
    if (btnOciosos30d) btnOciosos30d.classList.toggle('active', ociososSidebarModo === '30dias');
    if (btnOciosos60d) btnOciosos60d.classList.toggle('active', ociososSidebarModo === '60dias');
    if (btnOciosos90d) btnOciosos90d.classList.toggle('active', ociososSidebarModo === '90dias');
}

function renderOciososSidebarVendas() {
    if (!graficoOciososVendas) return;
    
    const periodoMap = {
        '7dias': { chave: 'ultimos_7_dias', label: '7 dias' },
        '15dias': { chave: 'ultimos_15_dias', label: '15 dias' },
        '30dias': { chave: 'ultimos_30_dias', label: '30 dias' },
        '60dias': { chave: 'ultimos_60_dias', label: '60 dias' },
        '90dias': { chave: 'ultimos_90_dias', label: '90 dias' }
    };
    
    const config = periodoMap[ociososSidebarModo] || periodoMap['30dias'];
    const listaAtiva = ociososDados[config.chave] || [];
    
    if (!Array.isArray(listaAtiva) || listaAtiva.length === 0) {
        graficoOciososVendas.innerHTML = `<li>Sem SKUs sem vender por ${config.label} com estoque.</li>`;
        atualizarBotoesOciosos();
        return;
    }
    
    graficoOciososVendas.innerHTML = listaAtiva.map(item => {
        const estoque = Math.round(Number(item?.estoque || 0)).toLocaleString('pt-BR');
        const diasStr = item?.dias_sem_vender ? `${item.dias_sem_vender} dias` : 'Nunca';
        return `<li><strong>${item?.sku || 'N/D'}</strong>: estoque ${estoque} | última venda ${diasStr}</li>`;
    }).join('');
    atualizarBotoesOciosos();
}

async function carregarOciososSidebarVendas() {
    if (!graficoOciososVendas) return;
    
    try {
        const params = new URLSearchParams();
        if (lojaSelecionada && lojaSelecionada !== '__todas') {
            params.append('loja', lojaSelecionada);
        }
        if (unidadeNegocioSelect && unidadeNegocioSelect.value && unidadeNegocioSelect.value !== '__todos') {
            params.append('unidade_negocio', unidadeNegocioSelect.value);
        }
        
        const url = '/api/vendas/skus-sem-venda' + (params.toString() ? `?${params.toString()}` : '');
        const response = await fetchComTimeout(url, { headers: obterAuthHeaders() });
        
        if (!response.ok) {
            graficoOciososVendas.innerHTML = '<li>Erro ao carregar SKUs ociosos.</li>';
            return;
        }
        
        ociososDados = await response.json();
        renderOciososSidebarVendas();
    } catch (error) {
        console.error('Erro ao carregar SKUs ociosos:', error);
        if (graficoOciososVendas) {
            graficoOciososVendas.innerHTML = '<li>Não foi possível carregar SKUs ociosos.</li>';
        }
    }
}

function chavePreferenciaGrafico() {
    const cid = clientId || obterClientId() || 'anon';
    return `vendas_grafico_pref_${cid}`;
}

function salvarPreferenciaGrafico() {
    try {
        localStorage.setItem(chavePreferenciaGrafico(), JSON.stringify({
            periodo: periodoGrafico,
            tipo: tipoGrafico,
            metrica: metricaGrafico,
            intervalo: intervaloGrafico,
            compararAnoPassado,
            mostrarEstoqueGeralGrafico,
            mostrarEstoqueSkuGrafico,
            skuAtualGrafico,
            estoqueLayoutVersion: GRAFICO_ESTOQUE_LAYOUT_VERSION
        }));
    } catch (_e) {
        // Ignora erro de localStorage.
    }
}

function carregarPreferenciaGrafico() {
    try {
        const raw = localStorage.getItem(chavePreferenciaGrafico());
        if (!raw) return;
        const pref = JSON.parse(raw);
        if (pref?.periodo) periodoGrafico = pref.periodo;
        if (pref?.tipo) tipoGrafico = pref.tipo;
        if (pref?.metrica) metricaGrafico = pref.metrica;
        if (pref?.intervalo) intervaloGrafico = pref.intervalo;
        compararAnoPassado = Boolean(pref?.compararAnoPassado);
        const prefEstoqueAtualizada = Number(pref?.estoqueLayoutVersion || 0) >= GRAFICO_ESTOQUE_LAYOUT_VERSION;
        mostrarEstoqueGeralGrafico = prefEstoqueAtualizada ? pref?.mostrarEstoqueGeralGrafico !== false : true;
        mostrarEstoqueSkuGrafico = Boolean(pref?.mostrarEstoqueSkuGrafico);
        skuAtualGrafico = String(pref?.skuAtualGrafico || '').trim() || null;
    } catch (_e) {
        // Ignora preferência inválida.
    }
}

function aplicarPreferenciaGraficoUI() {
    document.querySelectorAll('.grafico-btn[data-periodo]').forEach(b => {
        b.classList.toggle('active', b.dataset.periodo === periodoGrafico);
    });
    document.querySelectorAll('.grafico-btn[data-tipo]').forEach(b => {
        b.classList.toggle('active', b.dataset.tipo === tipoGrafico);
    });
    document.querySelectorAll('.grafico-btn[data-metrica]').forEach(b => {
        b.classList.toggle('active', b.dataset.metrica === metricaGrafico);
    });
    document.querySelectorAll('.grafico-btn[data-intervalo]').forEach(b => {
        b.classList.toggle('active', b.dataset.intervalo === intervaloGrafico);
    });
    if (compareAnoPassado) {
        compareAnoPassado.checked = compararAnoPassado;
    }
    if (mostrarEstoqueGeralCheck) {
        mostrarEstoqueGeralCheck.checked = mostrarEstoqueGeralGrafico;
    }
    if (mostrarEstoqueSkuCheck) {
        mostrarEstoqueSkuCheck.checked = mostrarEstoqueSkuGrafico;
    }
    if (graficoEstoqueSkuInput) {
        graficoEstoqueSkuInput.value = skuAtualGrafico || '';
    }

    const btnsPeriodo = document.querySelectorAll('.grafico-btn[data-periodo]');
    if (intervaloGrafico === 'semana') {
        btnsPeriodo.forEach(btn => {
            btn.disabled = true;
            btn.style.opacity = '0.5';
            btn.style.cursor = 'not-allowed';
        });
    } else {
        btnsPeriodo.forEach(btn => {
            btn.disabled = false;
            btn.style.opacity = '1';
            btn.style.cursor = 'pointer';
        });
    }
}

function formatarLabelDataGrafico(label) {
    if (/^\d{4}-\d{2}-\d{2}$/.test(label || '')) {
        const [ano, mes, dia] = label.split('-');
        return `${dia}/${mes}/${ano}`;
    }
    return label;
}

function atualizarObservacoesGraficoVendas(data) {
    if (!graficoObservacoesVendas) return;
    const labels = Array.isArray(data?.labels) ? data.labels : [];
    const qtdV = Array.isArray(data?.quantidades_vendas) ? data.quantidades_vendas : [];
    const qtdD = Array.isArray(data?.quantidades_devolucoes) ? data.quantidades_devolucoes : [];
    const valV = Array.isArray(data?.valores_vendas) ? data.valores_vendas : [];
    const valD = Array.isArray(data?.valores_devolucoes) ? data.valores_devolucoes : [];

    const totalQtdV = qtdV.reduce((a, b) => a + Number(b || 0), 0);
    const totalQtdD = qtdD.reduce((a, b) => a + Number(b || 0), 0);
    const totalValV = valV.reduce((a, b) => a + Number(b || 0), 0);
    const totalValD = valD.reduce((a, b) => a + Number(b || 0), 0);

    const taxa = totalQtdV > 0 ? (totalQtdD / totalQtdV) * 100 : 0;
    const seriePico = metricaGrafico === 'quantidade' ? qtdV : valV;
    let idxPico = -1;
    let valorPico = -1;
    seriePico.forEach((v, i) => {
        const n = Number(v || 0);
        if (n > valorPico) { valorPico = n; idxPico = i; }
    });
    const periodoPico = idxPico >= 0 ? formatarLabelDataGrafico(labels[idxPico] || '-') : '-';

    const unidadeSel = unidadeNegocioSelect?.value || '__todos';
    const dataIniIso = getDataIniISO();
    const dataFimIso = getDataFimISO();

    const dentroPeriodo = (raw) => {
        const d = String(raw || '').slice(0, 10);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return false;
        if (dataIniIso && d < dataIniIso) return false;
        if (dataFimIso && d > dataFimIso) return false;
        return true;
    };

    const vendasContexto = (Array.isArray(dados) ? dados : []).filter(row => {
        if (!dentroPeriodo(row?.data)) return false;
        if (lojaSelecionada && lojaSelecionada !== '__todas' && !mesmaLoja(row?.loja_conta, lojaSelecionada)) return false;
        if (unidadeSel && unidadeSel !== '__todos' && !mesmaUnidade(row?.unidade_negocio, unidadeSel)) return false;
        if (vendaEhEbazarSomatorio(row)) return false;
        return true;
    });

    const devolContexto = dadosModoResumo
        ? vendasContexto
            .filter(row => Number(row?.devolucoes || 0) > 0)
            .map(row => ({
                sku: row?.sku,
                descricao: row?.produto,
                quantidade: row?.itensDevolucao || 0,
                valor_total: row?.valorDevolucao || 0,
                data_emissao: row?.data || dataFimIso || dataIniIso || '',
                loja_conta: row?.loja_conta || lojaSelecionada,
                unidade_negocio_virtual: row?.unidade_negocio || '',
                unidade_negocio: row?.unidade_negocio || '',
                ocorrencias: row?.devolucoes || 0
            }))
        : (Array.isArray(devolucaoItens) ? devolucaoItens : []).filter(dev => {
        if (!dentroPeriodo(dev?.data_emissao)) return false;
        if (lojaSelecionada && lojaSelecionada !== '__todas' && !mesmaLoja(dev?.loja_conta, lojaSelecionada)) return false;
        if (unidadeSel && unidadeSel !== '__todos') {
            const uv = normalizarChaveFiltro(dev?.unidade_negocio_virtual || dev?.unidade_negocio || '', true);
            if (uv !== normalizarChaveFiltro(unidadeSel, true)) return false;
        }
        return true;
    });

    const mapaVendidos = {};
    vendasContexto.forEach(v => {
        const sku = String(v?.sku || 'N/D');
        if (!mapaVendidos[sku]) {
            mapaVendidos[sku] = { sku, produto: String(v?.produto || '-'), itens: 0, valor: 0, pedidos: 0 };
        }
        mapaVendidos[sku].itens += Number(v?.itens ?? v?.quantidade ?? 0);
        mapaVendidos[sku].valor += Number(v?.valor || 0);
        mapaVendidos[sku].pedidos += Number(v?.__resumo ? (v?.pedidos || 0) : 1);
    });
    const vendidosOrdenados = Object.values(mapaVendidos).sort((a, b) => (b.itens - a.itens) || (b.valor - a.valor));
    const topVendido = vendidosOrdenados[0] || null;

    const mapaDevol = {};
    devolContexto.forEach(dv => {
        const sku = String(dv?.sku || 'N/D');
        if (!mapaDevol[sku]) {
            mapaDevol[sku] = { sku, produto: String(dv?.descricao || '-'), itens: 0, valor: 0, ocorrencias: 0 };
        }
        mapaDevol[sku].itens += Number(dv?.quantidade || 0);
        mapaDevol[sku].valor += Number(dv?.valor_total || 0);
        mapaDevol[sku].ocorrencias += Number(dv?.ocorrencias || 1);
    });
    const devolOrdenados = Object.values(mapaDevol)
        .map(item => {
            const vendidosSku = Number(mapaVendidos[item.sku]?.itens || 0);
            const taxaSku = vendidosSku > 0 ? (Number(item.itens || 0) / vendidosSku) * 100 : 0;
            return { ...item, taxa: taxaSku };
        })
        .sort((a, b) => (b.taxa - a.taxa) || (b.itens - a.itens) || (b.valor - a.valor));
    const skusComDevolucao = new Set(Object.keys(mapaDevol));
    const vendidosComDevolOrdenados = Object.values(mapaVendidos)
        .filter(item => skusComDevolucao.has(item.sku))
        .sort((a, b) => (Number(b.itens || 0) - Number(a.itens || 0)) || (Number(b.valor || 0) - Number(a.valor || 0)));
    const topDevol = devolOrdenados[0] || null;

    rankingVendidosTop5 = vendidosOrdenados.slice(0, 5);
    rankingDevolTop5 = devolOrdenados.slice(0, 5);
    rankingVendidosTop20 = vendidosOrdenados.slice(0, 20);
    rankingDevolTop20 = devolOrdenados.slice(0, 20);
    rankingVendidosComDevolTodos = vendidosComDevolOrdenados;
    rankingDevolTodos = devolOrdenados;
    renderRankingSidebarVendas();

    const qtdDevolTotalContexto = devolContexto.reduce((acc, cur) => acc + Number(cur?.quantidade || 0), 0);
    const qtdVendasContexto = vendasContexto.reduce((acc, cur) => acc + Number(cur?.quantidade || 0), 0);
    const taxaContexto = qtdVendasContexto > 0 ? (qtdDevolTotalContexto / qtdVendasContexto) * 100 : 0;

    const obsCards = `
        <div class="sidebar-obs-card sidebar-obs-vendas">
            <div class="sidebar-obs-label">💰 Total Vendido</div>
            <div class="sidebar-obs-valor">${Math.round(totalQtdV).toLocaleString('pt-BR')} item(ns)</div>
            <div class="sidebar-obs-detalhe">${totalValV.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}</div>
        </div>
        <div class="sidebar-obs-card sidebar-obs-devolucao">
            <div class="sidebar-obs-label">📦 Total Devolvido</div>
            <div class="sidebar-obs-valor">${Math.round(totalQtdD).toLocaleString('pt-BR')} item(ns)</div>
            <div class="sidebar-obs-detalhe">${totalValD.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}</div>
        </div>
        <div class="sidebar-obs-card sidebar-obs-devolucao">
            <div class="sidebar-obs-label">📊 Taxa Devolução</div>
            <div class="sidebar-obs-valor">${taxaContexto.toFixed(1).replace('.', ',')}%</div>
            <div class="sidebar-obs-detalhe">por quantidade</div>
        </div>
        <div class="sidebar-obs-card sidebar-obs-neutro">
            <div class="sidebar-obs-label">📈 Maior Pico</div>
            <div class="sidebar-obs-valor">${periodoPico}</div>
            <div class="sidebar-obs-detalhe">${metricaGrafico === 'quantidade' ? 'por quantidade' : 'por valor'}</div>
        </div>
    `;

    graficoObservacoesVendas.innerHTML = obsCards;
}

function aplicarFiltroTempoDoGrafico(labelOriginal) {
    if (!labelOriginal) return;

    let inicio = null;
    let fim = null;

    if (/^\d{4}-\d{2}-\d{2}$/.test(labelOriginal)) {
        inicio = labelOriginal;
        fim = labelOriginal;
    } else if (/^\d{2}\/\d{2}\/\d{4}$/.test(labelOriginal)) {
        const [dd, mm, yyyy] = labelOriginal.split('/');
        const base = `${yyyy}-${mm}-${dd}`;
        if (intervaloGrafico === 'semana') {
            const d = new Date(base + 'T00:00:00');
            const fimSemana = new Date(d);
            fimSemana.setDate(d.getDate() + 6);
            inicio = d.toISOString().slice(0, 10);
            fim = fimSemana.toISOString().slice(0, 10);
        } else {
            inicio = base;
            fim = base;
        }
    } else {
        const m = String(labelOriginal).trim().match(/^([A-Za-zÀ-ÿ]{3})\/(\d{4})$/);
        if (m) {
            const mesNome = m[1].toLowerCase();
            const ano = m[2];
            const mapaMes = { jan: '01', fev: '02', mar: '03', abr: '04', mai: '05', jun: '06', jul: '07', ago: '08', set: '09', out: '10', nov: '11', dez: '12' };
            const mes = mapaMes[mesNome.slice(0, 3)];
            if (mes) {
                inicio = `${ano}-${mes}-01`;
                const ultimoDia = new Date(Number(ano), Number(mes), 0).getDate();
                fim = `${ano}-${mes}-${String(ultimoDia).padStart(2, '0')}`;
                const valorMesAno = `${ano}-${mes}`;
                if ([...mesAno.options].some(opt => opt.value === valorMesAno)) {
                    mesAno.value = valorMesAno;
                }
            }
        }
    }

    if (!inicio || !fim) return;
    sincronizarPeriodoTopo(inicio, fim);
    atualizarPeriodoComRecarregamento(true);
}

function deslocarAnoIso(dataIso, deltaAnos) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dataIso || '')) return '';
    const [anoStr, mesStr, diaStr] = dataIso.split('-');
    const ano = Number(anoStr) + Number(deltaAnos || 0);
    const mes = Number(mesStr);
    const dia = Number(diaStr);
    const ultimoDia = new Date(ano, mes, 0).getDate();
    const diaAjustado = Math.min(dia, ultimoDia);
    return `${String(ano).padStart(4, '0')}-${String(mes).padStart(2, '0')}-${String(diaAjustado).padStart(2, '0')}`;
}

function labelAnoAnterior(labelAtual) {
    const raw = String(labelAtual || '').trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
        return deslocarAnoIso(raw, -1);
    }
    if (/^\d{2}\/\d{2}\/\d{4}$/.test(raw)) {
        const [dd, mm, yyyy] = raw.split('/');
        const iso = `${yyyy}-${mm}-${dd}`;
        const isoPrev = deslocarAnoIso(iso, -1);
        if (!isoPrev) return raw;
        const [ay, am, ad] = isoPrev.split('-');
        return `${ad}/${am}/${ay}`;
    }
    const m = raw.match(/^([A-Za-zÀ-ÿ]{3})\/(\d{4})$/);
    if (m) {
        return `${m[1]}/${String(Number(m[2]) - 1)}`;
    }
    return raw;
}

function alinharSerieComparativa(labelsBase, labelsComparativo, serieComparativo) {
    const base = Array.isArray(labelsBase) ? labelsBase : [];
    const compLabels = Array.isArray(labelsComparativo) ? labelsComparativo : [];
    const compSerie = Array.isArray(serieComparativo) ? serieComparativo : [];
    const indicePorLabel = new Map();
    compLabels.forEach((lb, idx) => indicePorLabel.set(String(lb), idx));

    return base.map((lb, idxBase) => {
        const chavePrev = labelAnoAnterior(lb);
        const idxComp = indicePorLabel.has(chavePrev) ? indicePorLabel.get(chavePrev) : idxBase;
        const valor = compSerie[idxComp];
        return Number.isFinite(Number(valor)) ? Number(valor) : 0;
    });
}

async function carregarGrafico() {
    const params = new URLSearchParams({ periodo: periodoGrafico, intervalo: intervaloGrafico });
    const dataInicioAtual = getDataIniISO();
    const dataFimAtual = getDataFimISO();
    if (dataInicioAtual) params.append('data_inicio', dataInicioAtual);
    if (dataFimAtual) params.append('data_fim', dataFimAtual);
    if (lojaSelecionada && lojaSelecionada !== '__todas') params.append('loja', lojaSelecionada);
    if (unidadeNegocioSelect.value && unidadeNegocioSelect.value !== '__todos') {
        params.append('unidade_negocio', unidadeNegocioSelect.value);
    }

    skuAtualGrafico = String(graficoEstoqueSkuInput?.value || '').trim() || null;
    const skuGraficoConsulta = mostrarEstoqueSkuGrafico ? skuAtualGrafico : null;
    if (mostrarEstoqueGeralGrafico) params.append('mostrar_estoque_geral', 'true');
    if (mostrarEstoqueSkuGrafico) params.append('mostrar_estoque_sku', 'true');
    if (skuGraficoConsulta) params.append('sku', skuGraficoConsulta);

    let paramsComp = null;
    if (compararAnoPassado && dataInicioAtual && dataFimAtual) {
        const iniComp = deslocarAnoIso(dataInicioAtual, -1);
        const fimComp = deslocarAnoIso(dataFimAtual, -1);
        if (iniComp && fimComp) {
            paramsComp = new URLSearchParams({
                periodo: periodoGrafico,
                intervalo: intervaloGrafico,
                data_inicio: iniComp,
                data_fim: fimComp
            });
            if (lojaSelecionada && lojaSelecionada !== '__todas') paramsComp.append('loja', lojaSelecionada);
            if (unidadeNegocioSelect.value && unidadeNegocioSelect.value !== '__todos') {
                paramsComp.append('unidade_negocio', unidadeNegocioSelect.value);
            }
            if (skuGraficoConsulta) paramsComp.append('sku', skuGraficoConsulta);
        }
    }

    const requestKey = `${params.toString()}|compare=${paramsComp?.toString() || ''}`;
    if (carregarGraficoPromise && carregarGraficoRequestKey === requestKey) return carregarGraficoPromise;
    if (carregarGraficoController) carregarGraficoController.abort();

    const controller = new AbortController();
    const tokenAtual = ++carregarGraficoToken;
    carregarGraficoController = controller;
    carregarGraficoRequestKey = requestKey;
    carregarGraficoPromise = (async () => {
        try {
            const response = await fetchComTimeout(`/api/vendas/grafico?${params.toString()}`, {
                headers: obterAuthHeaders(),
                cache: 'no-store',
                signal: controller.signal
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();

            let dataComparativo = null;
            if (paramsComp) {
                const respComp = await fetchComTimeout(`/api/vendas/grafico?${paramsComp.toString()}`, {
                    headers: obterAuthHeaders(),
                    cache: 'no-store',
                    signal: controller.signal
                });
                if (respComp.ok) dataComparativo = await respComp.json();
            }

            if (tokenAtual !== carregarGraficoToken) return;
            renderizarGrafico(data, dataComparativo);
            atualizarObservacoesGraficoVendas(data);
            agendarSegundoPlano(() => carregarOciososSidebarVendas(), 180);
        } catch (error) {
            if (error.name === 'AbortError') return;
            console.error('Erro ao carregar gráfico:', error);
            if (graficoObservacoesVendas) {
                graficoObservacoesVendas.innerHTML = '<li>Não foi possível carregar observações do gráfico.</li>';
            }
            if (graficoRankingVendas) {
                graficoRankingVendas.innerHTML = '<li>Não foi possível carregar o ranking de SKUs.</li>';
            }
            if (graficoOciososVendas) {
                graficoOciososVendas.innerHTML = '<li>Não foi possível carregar SKUs ociosos.</li>';
            }
        }
    })();

    try {
        return await carregarGraficoPromise;
    } finally {
        if (carregarGraficoController === controller) {
            carregarGraficoController = null;
            carregarGraficoPromise = null;
        }
    }
}

function compactarDadosGrafico(labels, series, maxPontos = 160) {
    const baseLabels = Array.isArray(labels) ? labels : [];
    if (baseLabels.length <= maxPontos) {
        return { labels: baseLabels, series };
    }

    const passo = Math.max(1, Math.ceil(baseLabels.length / maxPontos));
    const labelsCompactos = [];
    const seriesCompactas = series.map(() => []);

    for (let i = 0; i < baseLabels.length; i += passo) {
        labelsCompactos.push(baseLabels[i]);
        series.forEach((arr, idx) => {
            seriesCompactas[idx].push(Array.isArray(arr) ? arr[i] : undefined);
        });
    }

    return { labels: labelsCompactos, series: seriesCompactas };
}

function renderizarGrafico(data, dataComparativo = null) {
    const ctx = document.getElementById('graficoVendas').getContext('2d');
    const mostrarDevolucoesGrafico = true;
    
    // Destruir gráfico anterior se existir
    if (chartInstance) {
        chartInstance.destroy();
    }

    const tipo = tipoGrafico === 'linha' ? 'line' : 'bar';

    // Função para formatar moeda brasileira
    const formatarMoeda = (valor) => {
        return valor.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
    };

    const labelsOriginais = data.labels || [];
    const valoresVendasOrig = data.valores_vendas || [];
    const valoresDevOrig = data.valores_devolucoes || [];
    const qtdVendasOrig = data.quantidades_vendas || [];
    const qtdDevOrig = data.quantidades_devolucoes || [];
    const normalizarSerieEstoque = (serie) => {
        const arr = Array.isArray(serie) ? serie : [];
        return labelsOriginais.map((_label, idx) => {
            const valor = arr[idx];
            if (valor === null || valor === undefined || valor === '') return null;
            const num = Number(valor);
            return Number.isFinite(num) ? num : null;
        });
    };
    const estoqueGeralOrig = normalizarSerieEstoque(data.estoque_geral);
    const estoqueSkuOrig = normalizarSerieEstoque(data.estoque_sku);
    const skusComEstoqueOrig = normalizarSerieEstoque(data.estoque_skus_com_saldo);
    const skusParetoComEstoqueOrig = normalizarSerieEstoque(data.estoque_skus_pareto_com_saldo);

    const labelsCompOrig = dataComparativo?.labels || [];
    const valoresVendasCompOrig = dataComparativo?.valores_vendas || [];
    const valoresDevCompOrig = dataComparativo?.valores_devolucoes || [];
    const qtdVendasCompOrig = dataComparativo?.quantidades_vendas || [];
    const qtdDevCompOrig = dataComparativo?.quantidades_devolucoes || [];

    const compactado = compactarDadosGrafico(labelsOriginais, [
        valoresVendasOrig,
        valoresDevOrig,
        qtdVendasOrig,
        qtdDevOrig,
        estoqueGeralOrig,
        estoqueSkuOrig,
        skusComEstoqueOrig,
        skusParetoComEstoqueOrig
    ]);

    const labelsRender = compactado.labels;
    const valoresVendas = compactado.series[0] || [];
    const valoresDevolucoes = compactado.series[1] || [];
    const quantidadesVendas = compactado.series[2] || [];
    const quantidadesDevolucoes = compactado.series[3] || [];
    const estoqueGeral = compactado.series[4] || [];
    const estoqueSku = compactado.series[5] || [];
    const skusComEstoque = compactado.series[6] || [];
    const skusParetoComEstoque = compactado.series[7] || [];
    const mostrarEstoqueNoGrafico = false;
    const usandoQuantidade = metricaGrafico === 'quantidade';
    const vendasCompAlinhadas = alinharSerieComparativa(labelsOriginais, labelsCompOrig, usandoQuantidade ? qtdVendasCompOrig : valoresVendasCompOrig);
    const devolCompAlinhadas = alinharSerieComparativa(labelsOriginais, labelsCompOrig, usandoQuantidade ? qtdDevCompOrig : valoresDevCompOrig);
    const qtdVendasCompAlinhadas = alinharSerieComparativa(labelsOriginais, labelsCompOrig, qtdVendasCompOrig);
    const qtdDevolCompAlinhadas = alinharSerieComparativa(labelsOriginais, labelsCompOrig, qtdDevCompOrig);
    const vendasCompRender = compactarDadosGrafico(labelsOriginais, [vendasCompAlinhadas], 160).series[0] || [];
    const devolCompRender = compactarDadosGrafico(labelsOriginais, [devolCompAlinhadas], 160).series[0] || [];
    const qtdVendasCompRender = compactarDadosGrafico(labelsOriginais, [qtdVendasCompAlinhadas], 160).series[0] || [];
    const qtdDevolCompRender = compactarDadosGrafico(labelsOriginais, [qtdDevolCompAlinhadas], 160).series[0] || [];
    const graficoPesado = labelsRender.length > 120;

    // Preparar dados com quantidades como metadados
    const datasetsVendas = {
        label: usandoQuantidade ? 'Vendas (Qtd)' : 'Vendas (R$)',
        data: usandoQuantidade ? quantidadesVendas : valoresVendas,
        yAxisID: 'y',
        backgroundColor: tipoGrafico === 'linha' ? 'rgba(79, 172, 254, 0.2)' : 'rgba(79, 172, 254, 0.6)',
        borderColor: 'rgba(79, 172, 254, 1)',
        borderWidth: 2,
        tension: 0.4,
        fill: true,
        quantidades: quantidadesVendas // Armazenar quantidades como metadado
    };
    
    const datasetsDevol = {
        label: usandoQuantidade ? 'Devoluções (Qtd)' : 'Devoluções (R$)',
        data: usandoQuantidade ? quantidadesDevolucoes : valoresDevolucoes,
        yAxisID: 'y',
        backgroundColor: tipoGrafico === 'linha' ? 'rgba(255, 107, 107, 0.2)' : 'rgba(255, 107, 107, 0.6)',
        borderColor: 'rgba(255, 107, 107, 1)',
        borderWidth: 2,
        tension: 0.4,
        fill: true,
        quantidades: quantidadesDevolucoes // Armazenar quantidades como metadado
    };

    const datasetsVendasAnoPassado = {
        label: usandoQuantidade ? 'Vendas ano passado (Qtd)' : 'Vendas ano passado (R$)',
        data: vendasCompRender,
        yAxisID: 'y',
        backgroundColor: 'rgba(110, 196, 255, 0.05)',
        borderColor: 'rgba(110, 196, 255, 0.95)',
        borderWidth: 2,
        tension: 0.35,
        fill: false,
        borderDash: [8, 5],
        pointRadius: 0,
        pointHoverRadius: 3,
        quantidades: qtdVendasCompRender
    };

    const datasetsDevolAnoPassado = {
        label: usandoQuantidade ? 'Devoluções ano passado (Qtd)' : 'Devoluções ano passado (R$)',
        data: devolCompRender,
        yAxisID: 'y',
        backgroundColor: 'rgba(255, 130, 130, 0.05)',
        borderColor: 'rgba(255, 130, 130, 0.95)',
        borderWidth: 2,
        tension: 0.35,
        fill: false,
        borderDash: [8, 5],
        pointRadius: 0,
        pointHoverRadius: 3,
        quantidades: qtdDevolCompRender
    };

    const datasets = [datasetsVendas];
    if (mostrarDevolucoesGrafico) {
        datasets.push(datasetsDevol);
    }
    if (compararAnoPassado && dataComparativo) {
        datasets.push(datasetsVendasAnoPassado);
        if (mostrarDevolucoesGrafico) {
            datasets.push(datasetsDevolAnoPassado);
        }
    }
    renderizarGraficoEstoque(
        labelsRender,
        estoqueGeral,
        estoqueSku,
        quantidadesVendas,
        graficoPesado,
        data?.estoque_meta
    );
    renderizarGraficoSkusComEstoque(
        labelsRender,
        skusComEstoque,
        skusParetoComEstoque,
        graficoPesado,
        data?.estoque_meta
    );

    const unidadesNoTopoPlugin = {
        id: 'unidadesNoTopo',
        afterDatasetsDraw(chart) {
            const { ctx } = chart;
            ctx.save();
            ctx.font = '700 11px Segoe UI';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'bottom';

            chart.data.datasets.forEach((dataset, datasetIndex) => {
                if (dataset.skipTopLabels) return;
                const meta = chart.getDatasetMeta(datasetIndex);
                if (!meta || meta.hidden) return;

                meta.data.forEach((pointOrBar, index) => {
                    const pos = pointOrBar.tooltipPosition();
                    ctx.fillStyle = dataset.topLabelColor || (datasetIndex === 0 ? '#9bd1ff' : '#ffb3b3');

                    if (tipoGrafico === 'barra') {
                        const valorMetrica = Number((dataset.data || [])[index] || 0);
                        if (!valorMetrica) return;
                        const yBar = Math.min(pos.y - 6, chart.chartArea.bottom - 4);
                        const textoBarra = usandoQuantidade
                            ? Math.round(valorMetrica).toLocaleString('pt-BR')
                            : formatarMoeda(valorMetrica);
                        ctx.fillText(textoBarra, pos.x, yBar);
                        return;
                    }

                    if (tipoGrafico === 'linha') {
                        const valor = Number((dataset.data || [])[index] || 0);
                        if (!Number.isFinite(valor)) return;
                        const texto = usandoQuantidade
                            ? Math.round(valor).toLocaleString('pt-BR')
                            : formatarMoeda(valor);
                        const yLinha = pos.y - 8;
                        ctx.fillText(texto, pos.x, yLinha);
                    }
                });
            });

            ctx.restore();
        }
    };

    chartInstance = new Chart(ctx, {
        type: tipo,
        plugins: graficoPesado ? [] : [unidadesNoTopoPlugin],
        data: {
            labels: labelsRender.map(formatarLabelDataGrafico),
            datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            animation: graficoPesado ? false : { duration: 220 },
            elements: {
                point: { radius: graficoPesado ? 0 : 2, hitRadius: 8 },
                line: { tension: 0.35 }
            },
            onClick: function(_event, elements) {
                if (!elements || !elements.length) return;
                const idx = elements[0].index;
                const labelOriginal = (labelsRender || [])[idx] || '';
                aplicarFiltroTempoDoGrafico(labelOriginal);
            },
            plugins: {
                legend: {
                    display: true,
                    labels: {
                        color: '#e5e5e5'
                    }
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    callbacks: {
                        label: function(context) {
                            const dataset = context.chart.data.datasets[context.datasetIndex];
                            if (dataset.isEstoque) {
                                const valorEstoque = context.parsed?.y;
                                const baseLabel = context.dataset.label || 'Estoque';
                                if (valorEstoque === null || valorEstoque === undefined || !Number.isFinite(Number(valorEstoque))) {
                                    return `${baseLabel}: sem histórico`;
                                }
                                return `${baseLabel}: ${Math.round(Number(valorEstoque)).toLocaleString('pt-BR')} un.`;
                            }
                            const quantidades = dataset.quantidades || [];
                            const quantidade = quantidades[context.dataIndex] || 0;
                            
                            let label = context.dataset.label || '';
                            if (label) {
                                label += ': ';
                            }
                            if (usandoQuantidade) {
                                label += Math.round(context.parsed.y);
                            } else {
                                label += formatarMoeda(context.parsed.y);
                                label += ' | Qtd: ' + Math.round(quantidade);
                            }
                            return label;
                        }
                    }
                }
            },
            scales: {
                x: {
                    ticks: {
                        color: '#e5e5e5',
                        maxRotation: 45,
                        minRotation: 45
                    },
                    grid: {
                        color: 'rgba(255, 255, 255, 0.1)'
                    }
                },
                y: {
                    ticks: {
                        color: '#e5e5e5',
                        callback: function(value) {
                            if (usandoQuantidade) {
                                return Number(value || 0).toLocaleString('pt-BR');
                            }
                            return value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
                        }
                    },
                    grid: {
                        color: 'rgba(255, 255, 255, 0.1)'
                    }
                },
                yEstoque: {
                    display: mostrarEstoqueNoGrafico,
                    position: 'right',
                    ticks: {
                        color: '#ffb199',
                        callback: function(value) {
                            return Number(value || 0).toLocaleString('pt-BR');
                        }
                    },
                    title: {
                        display: mostrarEstoqueNoGrafico,
                        text: 'Estoque',
                        color: '#ffb199'
                    },
                    grid: {
                        drawOnChartArea: false
                    }
                }
            }
        }
    });
}

function renderizarGraficoEstoque(
    labelsRender,
    estoqueGeral,
    estoqueSku,
    quantidadesVendas,
    graficoPesado,
    estoqueMeta = {}
) {
    const wrapper = typeof graficoEstoqueWrapper !== 'undefined' ? graficoEstoqueWrapper : document.getElementById('graficoEstoqueWrapper');
    const canvas = document.getElementById('graficoEstoque');
    const resumoEl = typeof graficoEstoqueResumo !== 'undefined' ? graficoEstoqueResumo : document.getElementById('graficoEstoqueResumo');
    const estadoEl = typeof graficoEstoqueEstado !== 'undefined' ? graficoEstoqueEstado : document.getElementById('graficoEstoqueEstado');
    if (!wrapper || !canvas || typeof Chart === 'undefined') return;

    if (chartEstoqueInstance) {
        chartEstoqueInstance.destroy();
        chartEstoqueInstance = null;
    }

    const estoqueAtivo = mostrarEstoqueGeralGrafico || mostrarEstoqueSkuGrafico;
    wrapper.hidden = !estoqueAtivo;
    if (!estoqueAtivo) return;

    const valoresValidos = (serie) => (Array.isArray(serie) ? serie : [])
        .map((valor, idx) => ({ valor: Number(valor), idx, raw: valor }))
        .filter(item => item.raw !== null && item.raw !== undefined && item.raw !== '' && Number.isFinite(item.valor));
    const pontosGeral = mostrarEstoqueGeralGrafico ? valoresValidos(estoqueGeral) : [];
    const pontosSku = mostrarEstoqueSkuGrafico ? valoresValidos(estoqueSku) : [];
    const pontosVendas = valoresValidos(quantidadesVendas);
    const haGeral = pontosGeral.length > 0;
    const haSku = pontosSku.length > 0;

    wrapper.classList.toggle('is-empty', !(haGeral || haSku));
    if (!(haGeral || haSku)) {
        if (resumoEl) resumoEl.textContent = 'Sem saldo no periodo';
        if (estadoEl) {
            const detalhe = String(estoqueMeta?.detail || '').trim();
            estadoEl.textContent = detalhe || 'Historico de estoque indisponivel para o filtro atual.';
            estadoEl.hidden = false;
        }
        return;
    }

    if (estadoEl) {
        estadoEl.textContent = '';
        estadoEl.hidden = true;
    }

    const serieResumo = haGeral ? pontosGeral : pontosSku;
    const primeiro = serieResumo[0]?.valor || 0;
    const ultimo = serieResumo[serieResumo.length - 1]?.valor || 0;
    const delta = ultimo - primeiro;
    if (resumoEl) {
        const sinal = delta > 0 ? '+' : '';
        const lojas = Number(estoqueMeta?.lojas || 0);
        const lojasComHistorico = Number(estoqueMeta?.lojas_com_historico || lojas);
        const cobertura = lojas > 0 && lojasComHistorico < lojas
            ? ` | Cobertura ${lojasComHistorico}/${lojas} lojas`
            : '';
        resumoEl.textContent = `Atual ${Math.round(ultimo).toLocaleString('pt-BR')} un. | Var. ${sinal}${Math.round(delta).toLocaleString('pt-BR')} un.${cobertura}`;
        resumoEl.title = String(estoqueMeta?.detail || '').trim();
    }

    const datasetsEstoque = [];
    if (haGeral) {
        datasetsEstoque.push({
            label: 'Saldo total de estoque',
            data: estoqueGeral,
            borderColor: 'rgba(255, 143, 112, 0.98)',
            backgroundColor: 'rgba(255, 143, 112, 0.16)',
            borderWidth: 2,
            tension: 0.28,
            fill: true,
            yAxisID: 'y',
            pointRadius: graficoPesado ? 0 : 2,
            pointHoverRadius: 4,
            spanGaps: true
        });
    }
    if (haSku) {
        datasetsEstoque.push({
            label: skuAtualGrafico ? `Saldo SKU ${skuAtualGrafico}` : 'Saldo por SKU',
            data: estoqueSku,
            borderColor: 'rgba(255, 214, 128, 0.98)',
            backgroundColor: 'rgba(255, 214, 128, 0.08)',
            borderWidth: 2,
            borderDash: [5, 4],
            tension: 0.28,
            fill: false,
            yAxisID: 'y',
            pointRadius: graficoPesado ? 0 : 2,
            pointHoverRadius: 4,
            spanGaps: true
        });
    }
    if (pontosVendas.length > 0) {
        datasetsEstoque.push({
            label: 'Unidades vendidas',
            data: quantidadesVendas,
            borderColor: 'rgba(83, 181, 255, 0.98)',
            backgroundColor: 'rgba(83, 181, 255, 0.08)',
            borderWidth: 2,
            borderDash: [8, 4],
            tension: 0.28,
            fill: false,
            yAxisID: 'yComparacao',
            pointRadius: graficoPesado ? 0 : 2,
            pointHoverRadius: 4,
            isSalesCount: true
        });
    }
    chartEstoqueInstance = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            labels: (labelsRender || []).map(formatarLabelDataGrafico),
            datasets: datasetsEstoque
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            animation: graficoPesado ? false : { duration: 220 },
            elements: {
                point: { radius: graficoPesado ? 0 : 2, hitRadius: 8 },
                line: { tension: 0.28 }
            },
            onClick: function(_event, elements) {
                if (!elements || !elements.length) return;
                const idx = elements[0].index;
                const labelOriginal = (labelsRender || [])[idx] || '';
                aplicarFiltroTempoDoGrafico(labelOriginal);
            },
            plugins: {
                legend: {
                    display: true,
                    labels: { color: '#e5e5e5' }
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    callbacks: {
                        label: function(context) {
                            const valor = context.parsed?.y;
                            if (valor === null || valor === undefined || !Number.isFinite(Number(valor))) {
                                return `${context.dataset.label || 'Estoque'}: sem historico`;
                            }
                            const valorFormatado = Math.round(Number(valor)).toLocaleString('pt-BR');
                            return `${context.dataset.label || 'Estoque'}: ${valorFormatado} un.`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    ticks: {
                        color: '#e5e5e5',
                        maxRotation: 45,
                        minRotation: 45
                    },
                    grid: { color: 'rgba(255, 255, 255, 0.08)' }
                },
                y: {
                    min: 0,
                    beginAtZero: true,
                    ticks: {
                        color: '#ffb199',
                        callback: function(value) {
                            return Number(value || 0).toLocaleString('pt-BR');
                        }
                    },
                    title: {
                        display: true,
                        text: 'Unidades em estoque',
                        color: '#ffb199'
                    },
                    grid: { color: 'rgba(255, 255, 255, 0.08)' }
                },
                yComparacao: {
                    min: 0,
                    beginAtZero: true,
                    position: 'right',
                    ticks: {
                        color: '#83cfff',
                        callback: function(value) {
                            return Number(value || 0).toLocaleString('pt-BR');
                        }
                    },
                    title: {
                        display: true,
                        text: 'Unidades vendidas',
                        color: '#83cfff'
                    },
                    grid: { drawOnChartArea: false }
                }
            }
        }
    });
}

function renderizarGraficoSkusComEstoque(
    labelsRender,
    skusComEstoque,
    skusParetoComEstoque,
    graficoPesado,
    estoqueMeta = {}
) {
    const wrapper = document.getElementById('graficoSkusEstoqueWrapper');
    const canvas = document.getElementById('graficoSkusEstoque');
    const resumoEl = document.getElementById('graficoSkusEstoqueResumo');
    const estadoEl = document.getElementById('graficoSkusEstoqueEstado');
    if (!wrapper || !canvas || typeof Chart === 'undefined') return;

    if (chartSkusEstoqueInstance) {
        chartSkusEstoqueInstance.destroy();
        chartSkusEstoqueInstance = null;
    }

    const estoqueAtivo = mostrarEstoqueGeralGrafico || mostrarEstoqueSkuGrafico;
    wrapper.hidden = !estoqueAtivo;
    if (!estoqueAtivo) return;

    const pontosValidos = (Array.isArray(skusComEstoque) ? skusComEstoque : [])
        .map((valor, idx) => ({ valor: Number(valor), idx, raw: valor }))
        .filter(item => item.raw !== null && item.raw !== undefined && item.raw !== '' && Number.isFinite(item.valor));
    const haDados = pontosValidos.length > 0;
    const pontosParetoValidos = (Array.isArray(skusParetoComEstoque) ? skusParetoComEstoque : [])
        .map((valor, idx) => ({ valor: Number(valor), idx, raw: valor }))
        .filter(item => item.raw !== null && item.raw !== undefined && item.raw !== '' && Number.isFinite(item.valor));

    wrapper.classList.toggle('is-empty', !haDados);
    if (!haDados) {
        if (resumoEl) resumoEl.textContent = 'Sem contagem no periodo';
        if (estadoEl) {
            const detalhe = String(estoqueMeta?.detail || '').trim();
            estadoEl.textContent = detalhe || 'Contagem de SKUs com estoque indisponivel para o filtro atual.';
            estadoEl.hidden = false;
        }
        return;
    }

    if (estadoEl) {
        estadoEl.textContent = '';
        estadoEl.hidden = true;
    }

    const primeiro = pontosValidos[0]?.valor || 0;
    const ultimo = pontosValidos[pontosValidos.length - 1]?.valor || 0;
    const delta = ultimo - primeiro;
    if (resumoEl) {
        const sinal = delta > 0 ? '+' : '';
        const lojas = Number(estoqueMeta?.lojas || 0);
        const lojasComHistorico = Number(estoqueMeta?.lojas_com_historico || lojas);
        const cobertura = lojas > 0 && lojasComHistorico < lojas
            ? ` | Cobertura ${lojasComHistorico}/${lojas} lojas`
            : '';
        const paretoAtual = pontosParetoValidos[pontosParetoValidos.length - 1]?.valor;
        const paretoTotal = Number(estoqueMeta?.pareto_skus_total || 0);
        const paretoResumo = Number.isFinite(paretoAtual) && paretoTotal > 0
            ? ` | Pareto 80%: ${Math.round(paretoAtual)}/${paretoTotal} com estoque`
            : '';
        resumoEl.textContent = `Atual ${Math.round(ultimo).toLocaleString('pt-BR')} SKU(s) | Var. ${sinal}${Math.round(delta).toLocaleString('pt-BR')} SKU(s)${paretoResumo}${cobertura}`;
        const participacaoPareto = Number(estoqueMeta?.pareto_participacao || 0);
        const detalhePareto = paretoTotal > 0
            ? `Pareto do período: ${paretoTotal} SKU(s), ${(participacaoPareto * 100).toLocaleString('pt-BR', { maximumFractionDigits: 1 })}% do faturamento.`
            : '';
        resumoEl.title = [String(estoqueMeta?.detail || '').trim(), detalhePareto].filter(Boolean).join(' ');
    }

    chartSkusEstoqueInstance = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            labels: (labelsRender || []).map(formatarLabelDataGrafico),
            datasets: [{
                label: 'SKUs com estoque',
                data: skusComEstoque,
                borderColor: 'rgba(91, 214, 160, 0.98)',
                backgroundColor: 'rgba(91, 214, 160, 0.13)',
                borderWidth: 2,
                tension: 0.28,
                fill: true,
                pointRadius: graficoPesado ? 0 : 2,
                pointHoverRadius: 4,
                spanGaps: true
            }, {
                label: 'SKUs Pareto 80% com estoque',
                data: skusParetoComEstoque,
                borderColor: 'rgba(255, 205, 92, 0.98)',
                backgroundColor: 'rgba(255, 205, 92, 0.05)',
                borderWidth: 2,
                borderDash: [7, 5],
                tension: 0.24,
                fill: false,
                pointRadius: graficoPesado ? 0 : 2,
                pointHoverRadius: 4,
                spanGaps: true,
                hidden: pontosParetoValidos.length === 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            animation: graficoPesado ? false : { duration: 220 },
            elements: {
                point: { radius: graficoPesado ? 0 : 2, hitRadius: 8 },
                line: { tension: 0.28 }
            },
            onClick: function(_event, elements) {
                if (!elements || !elements.length) return;
                const idx = elements[0].index;
                const labelOriginal = (labelsRender || [])[idx] || '';
                aplicarFiltroTempoDoGrafico(labelOriginal);
            },
            plugins: {
                legend: {
                    display: true,
                    labels: { color: '#dff9eb' }
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    callbacks: {
                        label: function(context) {
                            const valor = context.parsed?.y;
                            if (valor === null || valor === undefined || !Number.isFinite(Number(valor))) {
                                return 'SKUs com estoque: sem historico';
                            }
                            return `${context.dataset.label || 'SKUs com estoque'}: ${Math.round(Number(valor)).toLocaleString('pt-BR')} SKU(s)`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    ticks: {
                        color: '#e5e5e5',
                        maxRotation: 45,
                        minRotation: 45
                    },
                    grid: { color: 'rgba(91, 214, 160, 0.08)' }
                },
                y: {
                    min: 0,
                    beginAtZero: true,
                    ticks: {
                        color: '#7ce4b3',
                        precision: 0,
                        callback: function(value) {
                            return Math.round(Number(value || 0)).toLocaleString('pt-BR');
                        }
                    },
                    title: {
                        display: true,
                        text: 'Quantidade de SKUs',
                        color: '#7ce4b3'
                    },
                    grid: { color: 'rgba(91, 214, 160, 0.10)' }
                }
            }
        }
    });
}

function obterLimitesComVendas() {
    let lista = Array.isArray(dados) ? dados : [];

    if (lojaSelecionada && lojaSelecionada !== '__todas') {
        lista = lista.filter(row => mesmaLoja(row.loja_conta, lojaSelecionada));
    }
    if (unidadeNegocioSelect.value && unidadeNegocioSelect.value !== '__todos') {
        lista = lista.filter(row => mesmaUnidade(row.unidade_negocio, unidadeNegocioSelect.value));
    }

    const datas = lista
        .map(row => String(row.data || '').slice(0, 10))
        .filter(d => /^\d{4}-\d{2}-\d{2}$/.test(d))
        .sort();

    if (!datas.length) return null;
    return { inicio: datas[0], fim: datas[datas.length - 1] };
}

async function obterLimitesComVendasServidor() {
    try {
        const params = new URLSearchParams();
        if (lojaSelecionada && lojaSelecionada !== '__todas') {
            params.append('loja', lojaSelecionada);
        }
        if (unidadeNegocioSelect.value && unidadeNegocioSelect.value !== '__todos') {
            params.append('unidade_negocio', unidadeNegocioSelect.value);
        }
        const url = '/api/vendas/limites' + (params.toString() ? `?${params.toString()}` : '');
        const resp = await fetchComTimeout(url, { headers: obterAuthHeaders() });
        if (!resp.ok) return null;
        const data = await resp.json();
        const inicio = String(data?.inicio || '').trim();
        const fim = String(data?.fim || '').trim();
        if (!/^\d{4}-\d{2}-\d{2}$/.test(inicio) || !/^\d{4}-\d{2}-\d{2}$/.test(fim)) {
            return null;
        }
        return { inicio, fim };
    } catch (_e) {
        return null;
    }
}

// Event listeners para controles de gráfico
document.querySelectorAll('.grafico-btn[data-periodo]').forEach(btn => {
    btn.addEventListener('click', async function() {
        document.querySelectorAll('.grafico-btn[data-periodo]').forEach(b => b.classList.remove('active'));
        this.classList.add('active');
        periodoGrafico = this.dataset.periodo;
        salvarPreferenciaGrafico();
        
        // Calcular e aplicar datas correspondentes
        const hoje = new Date();
        let dataInicial;
        
        if (periodoGrafico === '3m') {
            dataInicial = new Date(hoje);
            dataInicial.setDate(hoje.getDate() - 90);
        } else if (periodoGrafico === '6m') {
            dataInicial = new Date(hoje);
            dataInicial.setDate(hoje.getDate() - 180);
        } else if (periodoGrafico === '1a') {
            dataInicial = new Date(hoje);
            dataInicial.setDate(hoje.getDate() - 365);
        } else if (periodoGrafico === '2a') {
            dataInicial = new Date(hoje);
            dataInicial.setDate(hoje.getDate() - 730);
        } else if (periodoGrafico === 'max') {
            const limites = await obterLimitesComVendasServidor() || obterLimitesComVendas();
            if (limites) {
                sincronizarPeriodoTopo(limites.inicio, limites.fim);
                atualizarPeriodoComRecarregamento(true);
                return;
            }
            dataInicial = new Date(hoje);
            dataInicial.setDate(hoje.getDate() - 90);
        } else {
            // Padrão: 3 meses
            dataInicial = new Date(hoje);
            dataInicial.setDate(hoje.getDate() - 90);
        }
        
        // Atualizar inputs de data
        sincronizarPeriodoTopo(dataInicial.toISOString().split('T')[0], hoje.toISOString().split('T')[0]);
        atualizarPeriodoComRecarregamento(true);
    });
});

document.querySelectorAll('.grafico-btn[data-tipo]').forEach(btn => {
    btn.addEventListener('click', function() {
        document.querySelectorAll('.grafico-btn[data-tipo]').forEach(b => b.classList.remove('active'));
        this.classList.add('active');
        tipoGrafico = this.dataset.tipo;
        salvarPreferenciaGrafico();
        carregarGrafico();
    });
});

document.querySelectorAll('.grafico-btn[data-metrica]').forEach(btn => {
    btn.addEventListener('click', function() {
        document.querySelectorAll('.grafico-btn[data-metrica]').forEach(b => b.classList.remove('active'));
        this.classList.add('active');
        metricaGrafico = this.dataset.metrica;
        salvarPreferenciaGrafico();
        filtrar();
        carregarGrafico();
    });
});

document.querySelectorAll('.grafico-btn[data-intervalo]').forEach(btn => {
    btn.addEventListener('click', function() {
        document.querySelectorAll('.grafico-btn[data-intervalo]').forEach(b => b.classList.remove('active'));
        this.classList.add('active');
        intervaloGrafico = this.dataset.intervalo;
        salvarPreferenciaGrafico();
        
        // Desabilitar botões de período se intervalo = semana
        const btnsPeriodo = document.querySelectorAll('.grafico-btn[data-periodo]');
        if (intervaloGrafico === 'semana') {
            btnsPeriodo.forEach(btn => {
                btn.disabled = true;
                btn.style.opacity = '0.5';
                btn.style.cursor = 'not-allowed';
            });
        } else {
            btnsPeriodo.forEach(btn => {
                btn.disabled = false;
                btn.style.opacity = '1';
                btn.style.cursor = 'pointer';
            });
        }
        
        carregarGrafico();
    });
});

if (compareAnoPassado) {
    compareAnoPassado.addEventListener('change', function() {
        compararAnoPassado = Boolean(this.checked);
        salvarPreferenciaGrafico();
        carregarGrafico();
    });
}

function atualizarSkuGraficoPeloInput() {
    skuAtualGrafico = String(graficoEstoqueSkuInput?.value || '').trim() || null;
    if (graficoEstoqueSkuInput) {
        graficoEstoqueSkuInput.value = skuAtualGrafico || '';
    }
}

if (mostrarEstoqueGeralCheck) {
    mostrarEstoqueGeralCheck.addEventListener('change', function() {
        mostrarEstoqueGeralGrafico = Boolean(this.checked);
        salvarPreferenciaGrafico();
        carregarGrafico();
    });
}

if (mostrarEstoqueSkuCheck) {
    mostrarEstoqueSkuCheck.addEventListener('change', function() {
        mostrarEstoqueSkuGrafico = Boolean(this.checked);
        atualizarSkuGraficoPeloInput();
        salvarPreferenciaGrafico();
        if (mostrarEstoqueSkuGrafico && !skuAtualGrafico) {
            graficoEstoqueSkuInput?.focus();
        }
        carregarGrafico();
    });
}

if (graficoEstoqueSkuInput) {
    graficoEstoqueSkuInput.addEventListener('change', function() {
        atualizarSkuGraficoPeloInput();
        salvarPreferenciaGrafico();
        if (mostrarEstoqueSkuGrafico) {
            carregarGrafico();
        }
    });
    graficoEstoqueSkuInput.addEventListener('keydown', function(event) {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        atualizarSkuGraficoPeloInput();
        mostrarEstoqueSkuGrafico = true;
        if (mostrarEstoqueSkuCheck) {
            mostrarEstoqueSkuCheck.checked = true;
        }
        salvarPreferenciaGrafico();
        carregarGrafico();
    });
}
