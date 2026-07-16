function mostrarCarregamentoSku() {
    if (!skuLoadingOverlay) return;
    skuLoadingOverlay.setAttribute('aria-hidden', 'false');
    skuLoadingOverlay.classList.add('active');
}

function ocultarCarregamentoSku() {
    if (!skuLoadingOverlay) return;
    skuLoadingOverlay.classList.remove('active');
    skuLoadingOverlay.setAttribute('aria-hidden', 'true');
}




window.addEventListener('pageshow', () => {
    ocultarCarregamentoSku();
});


tBody.addEventListener('click', (event) => {
    const tr = event.target && event.target.closest ? event.target.closest('tr[data-sku]') : null;
    if (!tr) return;
    const sku = tr.getAttribute('data-sku') || '';
    if (!sku) return;
    mostrarCarregamentoSku();
    const qs = new URLSearchParams();
    qs.append('sku', sku);
    const iniIso = getDataIniISO();
    const fimIso = getDataFimISO();
    if (iniIso) qs.append('data_inicio', iniIso);
    if (fimIso) qs.append('data_fim', fimIso);
    if (lojaSelecionada && lojaSelecionada !== '__todas') qs.append('loja', lojaSelecionada);
    if (unidadeNegocioSelect.value && unidadeNegocioSelect.value !== '__todos') {
        qs.append('unidade_negocio', unidadeNegocioSelect.value);
    }
    requestAnimationFrame(() => {
        window.location.href = `/vendas_sku.html?${qs.toString()}`;
    });
});

function atualizarPeriodoComRecarregamento(marcarSelecaoManual = false) {
    if (marcarSelecaoManual) {
        periodoSelecionadoPeloUsuario = true;
    }
    atualizarPeriodoTexto();
    if (!getDataIniISO() || !getDataFimISO()) {
        return;
    }
    salvarPreferenciaPeriodoData();
    if (periodoApplyTimer) {
        clearTimeout(periodoApplyTimer);
    }
    periodoApplyTimer = setTimeout(() => {
        carregarVendas().then(() => agendarSegundoPlano(() => carregarGrafico(), 220));
    }, 250);
}

function setSyncButtons(isSyncing) {
    btnSync.disabled = isSyncing;
    btnCancel.disabled = !isSyncing;
}

function renderProgresso(p, logs) {
    if (!p) return;
    const percent = Math.max(0, Math.min(100, Number(p.percentual || 0)));
    const logsList = (logs || []).slice(-10).map(l => `<div style="color:#9bd1ff; margin:2px 0;">${escaparHtmlVendas(l)}</div>`).join('');
    statusEl.className = 'status-bar loading';
    statusEl.innerHTML = `${spinnerHtml}<strong>${escaparHtmlVendas(p.mensagem || 'Sincronizando...')}</strong>
        <div style="margin-top:8px;background:#222;border-radius:6px;overflow:hidden;">
            <div style="height:8px;width:${percent}%;background:#4facfe;"></div>
        </div>
        <div style="margin-top:6px;font-size:0.85rem;color:#9bd1ff;">${percent}%</div>
        <div style="margin-top:8px;font-size:0.85rem;">${logsList}</div>`;
}

let progressMonitorSubscribed = false;
let progressReloadInFlight = false;

function aplicarEstadoMonitorSyncVendas(data) {
    if (!data) return;
    const progresso = data.progress || null;
    const etapa = String(progresso?.etapa || '').toLowerCase();
    const loteLocalAtivo = !!syncController;

    if (data.active) {
        syncEmAndamento = true;
        setSyncButtons(true);
        if (progresso) {
            renderProgresso(progresso, data.logs);
        } else {
            statusEl.className = 'status-bar loading';
            statusEl.innerHTML = `${spinnerHtml}<strong>Aguardando resposta do servidor...</strong>`;
        }
        return;
    }

    if (!loteLocalAtivo) {
        syncEmAndamento = false;
        setSyncButtons(false);
    }
    if (etapa === 'erro' && !loteLocalAtivo) {
        statusEl.className = 'status-bar error';
        statusEl.textContent = `Erro ao sincronizar: ${corrigirTextoVendas(progresso?.mensagem || 'Falha na sincronização.')}`;
    } else if (etapa === 'cancelamento' && !loteLocalAtivo) {
        statusEl.className = 'status-bar';
        statusEl.textContent = corrigirTextoVendas(progresso?.mensagem || 'Sincronização cancelada.');
    }
}

function iniciarMonitoramentoProgresso() {
    if (!progressMonitorSubscribed) {
        vendasSyncMonitor.subscribe(aplicarEstadoMonitorSyncVendas);
        progressMonitorSubscribed = true;
    }
    vendasSyncMonitor.start();
    void vendasSyncMonitor.refresh();
}

function pararMonitoramentoProgresso() {
    progressTimer = null;
    progressPollInFlight = false;
}

async function verificarSyncEmAndamentoNaEntrada() {
    clientId = clientId || obterClientId();
    if (!clientId) return false;
    iniciarMonitoramentoProgresso();
    const data = await vendasSyncMonitor.refresh();
    aplicarEstadoMonitorSyncVendas(data);
    return !!data?.active;
}

window.addEventListener('jk:vendas-sync-finished', event => {
    const payload = event?.detail?.payload || {};
    const etapa = String(payload?.progress?.etapa || '').toLowerCase();
    if (syncController || progressReloadInFlight || etapa === 'erro' || etapa === 'cancelamento') return;
    progressReloadInFlight = true;
    statusEl.className = 'status-bar loading';
    statusEl.innerHTML = `${spinnerHtml}Sincronização finalizada. Atualizando dados...`;
    void (async () => {
        try {
            await carregarVendas();
            await carregarGrafico();
            statusEl.className = 'status-bar';
            statusEl.textContent = '';
        } finally {
            progressReloadInFlight = false;
        }
    })();
});

// Funções utilitárias
function formatDate(dateStr) {
    if (!dateStr) return '';
    if (/^\d{2}\/\d{2}\/\d{4}$/.test(dateStr)) return dateStr;
    if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
        const [year, month, day] = dateStr.split('-');
        return day + '/' + month + '/' + year;
    }
    return dateStr;
}

function toIsoDateFromInput(value) {
    const v = String(value || '').trim();
    if (!v) return '';
    if (/^\d{4}-\d{2}-\d{2}$/.test(v)) return v;
    if (/^\d{2}\/\d{2}\/\d{4}$/.test(v)) {
        const [d, m, y] = v.split('/');
        return `${y}-${m}-${d}`;
    }
    return '';
}

function normalizarChaveFiltro(valor, removerPrefixoUnidade = false) {
    let txt = String(valor || '').trim().toLowerCase();
    if (!txt) return '';
    txt = txt.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    if (removerPrefixoUnidade) {
        txt = txt.replace(/^\s*unidade\s+/i, '');
    }
    txt = txt.replace(/\s+/g, ' ').trim();
    return txt;
}

function mesmaLoja(a, b) {
    return normalizarChaveFiltro(a) === normalizarChaveFiltro(b);
}

function mesmaUnidade(a, b) {
    return normalizarChaveFiltro(a, true) === normalizarChaveFiltro(b, true);
}

function getDataIniISO() {
    return toIsoDateFromInput(dataIni.value);
}

function getDataFimISO() {
    return toIsoDateFromInput(dataFim.value);
}

function getHojeISO() {
    return new Date().toISOString().split('T')[0];
}

function chavePreferenciaPeriodoData() {
    const cid = clientId || obterClientId() || 'anon';
    return `vendas_periodo_pref_${cid}`;
}

function salvarPreferenciaPeriodoData() {
    const iniIso = getDataIniISO();
    const fimIso = getDataFimISO();
    if (!iniIso || !fimIso) return;

    try {
        localStorage.setItem(chavePreferenciaPeriodoData(), JSON.stringify({
            data_inicio: iniIso,
            data_fim: fimIso
        }));
    } catch (_e) {
        // Ignora erro de localStorage.
    }
}

function carregarPreferenciaPeriodoData() {
    try {
        const raw = localStorage.getItem(chavePreferenciaPeriodoData());
        if (!raw) return null;
        const pref = JSON.parse(raw);
        let inicio = pref?.data_inicio;
        let fim = pref?.data_fim;
        if (!inicio || !fim) return null;

        const hojeIso = getHojeISO();
        if (fim > hojeIso) fim = hojeIso;
        if (inicio > fim) inicio = fim;

        return { inicio, fim };
    } catch (_e) {
        return null;
    }
}

function limitarDataFinalAoHoje() {
    const fimIso = getDataFimISO();
    if (!fimIso) return false;

    const hojeIso = getHojeISO();
    if (fimIso <= hojeIso) return false;

    const iniIso = getDataIniISO() || hojeIso;
    setDataRangeISO(iniIso, hojeIso);
    return true;
}

function garantirOrdemPeriodo() {
    const iniIso = getDataIniISO();
    const fimIso = getDataFimISO();
    if (!iniIso || !fimIso) return false;
    if (fimIso >= iniIso) return false;

    setDataRangeISO(iniIso, iniIso);
    return true;
}

function setDataRangeISO(inicioIso, fimIso) {
    const hojeIso = getHojeISO();
    const fimSeguroIso = fimIso && fimIso > hojeIso ? hojeIso : fimIso;
    const inicioFmt = formatDate(inicioIso);
    const fimFmt = formatDate(fimSeguroIso);

    if (dateRangePicker) {
        dateRangePicker.setDate(inicioIso, false);
    }

    if (dateFimPicker) {
        if (fimSeguroIso) {
            dateFimPicker.setDate(fimSeguroIso, false);
        } else {
            dateFimPicker.clear(false);
        }
    }

    // Garante que os dois inputs fiquem sempre separados (início/fim), sem intervalo combinado no primeiro campo.
    dataIni.value = inicioFmt;
    dataFim.value = fimFmt;

    if (dateRangePicker) {
        setTimeout(() => {
            dataIni.value = inicioFmt;
            dataFim.value = fimFmt;
        }, 0);
    }
}

function atualizarPeriodoTexto() {
    const iniIso = getDataIniISO();
    const fimIso = getDataFimISO();
    if (iniIso && fimIso) {
        periodoTexto.textContent = 'Per\u00edodo: ' + formatDate(iniIso) + ' at\u00e9 ' + formatDate(fimIso);
    } else {
        periodoTexto.textContent = 'Per\u00edodo: não selecionado';
    }
}

function salvarCacheTelaVendas() {
    try {
        const dadosParaCache = Array.isArray(dados) ? dados : [];
        const devolucoesParaCache = Array.isArray(devolucaoItens) ? devolucaoItens : [];
        if ((dadosParaCache.length + devolucoesParaCache.length) > VENDAS_VIEW_CACHE_MAX_ROWS) {
            sessionStorage.removeItem(VENDAS_VIEW_CACHE_KEY);
            return;
        }

        const payload = {
            ts: Date.now(),
            data_inicio: getDataIniISO(),
            data_fim: getDataFimISO(),
            lojaSelecionada,
            unidadeSelecionada: unidadeNegocioSelect.value || '__todos',
            filtroTexto: filtroTexto.value || '',
            dadosModoResumo,
            resumoTotaisVendas,
            vendasResumoMeta,
            dados: dadosParaCache.map(r => ({
                data: r.data || '',
                data_inicio: r.data_inicio || '',
                data_fim: r.data_fim || '',
                __resumo: r.__resumo === true,
                loja_conta: r.loja_conta || '',
                unidade_negocio: r.unidade_negocio || '',
                sku: r.sku || '',
                produto: r.produto || '',
                quantidade: Number(r.quantidade || 0),
                pedidos: Number(r.pedidos || 0),
                itens: Number(r.itens || r.quantidade || 0),
                devolucoes: Number(r.devolucoes || 0),
                itensDevolucao: Number(r.itensDevolucao || 0),
                valorDevolucao: Number(r.valorDevolucao || 0),
                valor: Number(r.valor || 0)
            })),
            devolucaoItens: devolucoesParaCache.map(d => ({
                sku: d.sku || '',
                descricao: d.descricao || '',
                quantidade: Number(d.quantidade || 0),
                valor_total: Number(d.valor_total || 0),
                loja_conta: d.loja_conta || '',
                unidade_negocio: d.unidade_negocio || '',
                unidade_negocio_virtual: d.unidade_negocio_virtual || ''
            })),
            mapeamentoUnidades
        };
        sessionStorage.setItem(VENDAS_VIEW_CACHE_KEY, JSON.stringify(payload));
    } catch (_e) {
        // Cache de tela é best-effort.
    }
}

function agendarSalvarCacheTelaVendas() {
    if (cacheSaveTimer) {
        clearTimeout(cacheSaveTimer);
    }
    cacheSaveTimer = setTimeout(() => {
        cacheSaveTimer = null;
        agendarSegundoPlano(() => salvarCacheTelaVendas(), 60);
    }, 180);
}

async function restaurarCacheTelaVendasSeValido() {
    try {
        const raw = sessionStorage.getItem(VENDAS_VIEW_CACHE_KEY);
        if (!raw) return false;
        const payload = JSON.parse(raw);
        if (!payload || !payload.ts || (Date.now() - Number(payload.ts)) > VENDAS_VIEW_CACHE_TTL_MS) {
            sessionStorage.removeItem(VENDAS_VIEW_CACHE_KEY);
            return false;
        }

        const iniAtual = getDataIniISO() || '';
        const fimAtual = getDataFimISO() || '';
        if ((payload.data_inicio || '') !== iniAtual || (payload.data_fim || '') !== fimAtual) {
            return false;
        }

        if (!Array.isArray(payload.dados)) return false;

        dadosModoResumo = payload.dadosModoResumo === true;
        resumoTotaisVendas = payload.resumoTotaisVendas || null;
        vendasResumoMeta = payload.vendasResumoMeta || null;
        dados = payload.dados;
        devolucaoItens = Array.isArray(payload.devolucaoItens) ? payload.devolucaoItens : [];
        mapeamentoUnidades = payload.mapeamentoUnidades || {};

        if (payload.filtroTexto != null) {
            filtroTexto.value = String(payload.filtroTexto);
        }
        if (payload.lojaSelecionada) {
            lojaSelecionada = payload.lojaSelecionada;
        }

        atualizarUnidadesNegocio(dados, mapeamentoUnidades || {});
        if (payload.unidadeSelecionada) {
            unidadeNegocioSelect.value = payload.unidadeSelecionada;
        }
        renderBotoesLojas(lojasDisponiveis || []);
        await filtrar();
        if (!syncEmAndamento) {
            statusEl.className = 'status-bar';
            statusEl.textContent = '';
        }
        return true;
    } catch (_e) {
        return false;
    }
}

function preencherMeses() {
    const hoje = new Date();
    for (let i = 0; i < 12; i++) {
        const data = new Date(hoje.getFullYear(), hoje.getMonth() - i, 1);
        const mes = (data.getMonth() + 1).toString().padStart(2, '0');
        const ano = data.getFullYear();
        const opcao = document.createElement('option');
        opcao.value = ano + '-' + mes;
        const meses = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 
                      'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];
        opcao.textContent = meses[data.getMonth()] + ' de ' + ano;
        mesAno.appendChild(opcao);
    }
}

function selecionarMes(mesAnoValue) {
    if (!mesAnoValue) return;
    const [ano, mes] = mesAnoValue.split('-');
    const data = new Date(ano, parseInt(mes) - 1, 1);
    const ultimoDia = new Date(ano, parseInt(mes), 0);

    const ini = ano + '-' + mes + '-01';
    const fim = ano + '-' + mes + '-' + ultimoDia.getDate().toString().padStart(2, '0');

    setDataRangeISO(ini, fim);
    atualizarPeriodoComRecarregamento(true);
}

function sincronizarPeriodoTopo(inicioIso, fimIso) {
    if (!inicioIso || !fimIso) return;
    setDataRangeISO(inicioIso, fimIso);

    // Se o intervalo for o mês fechado, reflete no seletor de mês.
    const mIni = inicioIso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    const mFim = fimIso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    let valorMes = '';
    if (mIni && mFim && mIni[1] === mFim[1] && mIni[2] === mFim[2] && mIni[3] === '01') {
        const ultimoDiaMes = new Date(Number(mIni[1]), Number(mIni[2]), 0).getDate();
        if (Number(mFim[3]) === ultimoDiaMes) {
            valorMes = `${mIni[1]}-${mIni[2]}`;
        }
    }
    mesAno.value = valorMes;
}

function initDateRangePicker() {
    if (typeof flatpickr !== 'function') {
        return;
    }

    if (flatpickr?.localize && flatpickr?.l10ns?.pt) {
        flatpickr.localize(flatpickr.l10ns.pt);
    }

    dateRangePicker = flatpickr(dataIni, {
        dateFormat: 'd/m/Y',
        disableMobile: true,
        allowInput: true,
        clickOpens: true,
        showMonths: 1,
        maxDate: 'today',
        locale: flatpickr.l10ns.pt,
        onChange: (selectedDates) => {
            if (selectedDates.length === 1) {
                dataFim.value = '';
                if (dateFimPicker) {
                    dateFimPicker.clear(false);
                    dateFimPicker.open();
                } else {
                    const secondInput = document.getElementById('dataFim');
                    if (secondInput) secondInput.focus();
                }
            }
        },
        onValueUpdate: () => {
            atualizarPeriodoTexto();
        },
        onClose: () => {
            limitarDataFinalAoHoje();
            if (getDataIniISO() && getDataFimISO()) {
                atualizarPeriodoComRecarregamento(true);
            }
        }
    });

    dateFimPicker = flatpickr(dataFim, {
        dateFormat: 'd/m/Y',
        disableMobile: true,
        allowInput: true,
        clickOpens: true,
        showMonths: 1,
        maxDate: 'today',
        locale: flatpickr.l10ns.pt,
        onChange: (selectedDates, _dateStr, instance) => {
            if (selectedDates.length === 1 && instance?.isOpen) {
                instance.close();
            }
        },
        onValueUpdate: () => {
            limitarDataFinalAoHoje();
            garantirOrdemPeriodo();
            atualizarPeriodoTexto();
        },
        onClose: () => {
            limitarDataFinalAoHoje();
            garantirOrdemPeriodo();
            if (getDataIniISO() && getDataFimISO()) {
                atualizarPeriodoComRecarregamento(true);
            }
        }
    });
}

function fecharCalendariosData() {
    if (dateRangePicker?.isOpen) {
        dateRangePicker.close();
    }
    if (dateFimPicker?.isOpen) {
        dateFimPicker.close();
    }
    dataIni.blur();
    dataFim.blur();
}

// Ler parâmetros de URL e preencher datas
const params_url = new URLSearchParams(window.location.search);
const data_inicio_param = params_url.get('data_inicio');
const data_fim_param = params_url.get('data_fim');
const retornoDoSku = params_url.get('from_sku') === '1';
const periodoPrefSalvo = carregarPreferenciaPeriodoData();

if (data_inicio_param && data_fim_param) {
    setDataRangeISO(data_inicio_param, data_fim_param);
} else if (periodoPrefSalvo?.inicio && periodoPrefSalvo?.fim) {
    setDataRangeISO(periodoPrefSalvo.inicio, periodoPrefSalvo.fim);
} else {
    dataIni.value = '';
    dataFim.value = '';
}

initDateRangePicker();

dataFim.addEventListener('change', () => {
    if (limitarDataFinalAoHoje()) {
        atualizarPeriodoTexto();
    }
});
dataFim.addEventListener('blur', () => {
    if (limitarDataFinalAoHoje()) {
        atualizarPeriodoTexto();
    }
});

preencherMeses();
atualizarPeriodoTexto();
mesAno.addEventListener('change', (e) => selecionarMes(e.target.value));
