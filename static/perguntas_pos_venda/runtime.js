function safeJsonParse(value, fallback) {
    try {
        return JSON.parse(value);
    } catch (_err) {
        return fallback;
    }
}

if (!verificarSessao()) {
    // verificarSessao redireciona quando não há token.
}

const permissions = safeJsonParse(localStorage.getItem('permissions') || '{}', {});
if (!(permissions.full === true || permissions.perguntas_pos_venda === true)) {
    alert('Você não tem permissão para acessar este módulo.');
    window.location.href = 'dashboard.html';
}

const state = {
    lojas: [],
    lojaSelecionada: '',
    lojaConfiguracaoPerguntas: '',
    perguntas: [],
    perguntaSelecionadaKey: '',
    paginaPerguntas: 1,
    totalPerguntas: 0,
    tamanhoPaginaPerguntas: 20,
    carregandoPerguntas: false,
    ultimaAtualizacaoPerguntasEm: 0,
    ultimaAtualizacaoPerguntasTimer: null,
    ultimaChecagemAutomacaoPerguntasEm: 0,
    carregandoPosVenda: false,
    posVendaPagina: 1,
    posVendaOffset: 0,
    posVendaNextOffset: null,
    posVendaOffsets: [0],
    posVendaCarregadoPara: '',
    posVendaConversas: [],
    posVendaConversaSelecionada: null,
    posVendaDetalheCarregando: false,
    posVendaModoDetalhe: false,
    carregandoMediacoes: false,
    mediacaoCarregadoPara: '',
    mediacaoConversas: [],
    treinamentoCarregado: false,
    treinamentoTipo: 'perguntas_anuncio',
    treinamentoDados: {
        perguntas_anuncio: { orientacoes: '', updated_at: null, exemplos: [] },
        pos_venda: { orientacoes: '', updated_at: null, exemplos: [] }
    },
    treinamentoContexto: {
        contexto_loja: '',
        compatibilidade_autopecas: '',
        proibicoes: '',
        notas_sku: {}
    },
    treinamentoEscopoLoja: '',
    treinamentoSkuNotasAtual: '',
    produtosTreinamento: [],
    produtosTreinamentoCarregados: false,
    automacaoPerguntasTimers: [],
    automacaoPerguntasStartupTimers: [],
    automacaoPerguntasGeracao: 0,
    automacaoPerguntasCountdownTimer: null,
    automacaoPerguntasStatusTimer: null,
    automacaoPerguntasStatusCarregando: false,
    automacaoPerguntasStatusDisponivel: false,
    automacaoPerguntasStatusErro: '',
    automacaoPerguntasStatusLojas: {},
    automacaoPerguntasChangeTokensLojas: {},
    automacaoPerguntasQuestionIdsLojas: {},
    automacaoPerguntasRefreshLojas: new Set(),
    automacaoPerguntasWorkerIniciado: false,
    automacaoPerguntasBackendExecutando: false,
    automacaoPerguntasProximaChecagemBackendEm: 0,
    automacaoPerguntasRefreshTimer: null,
    automacaoPerguntasRefreshPendente: false,
    automacaoPerguntasNextChecks: {},
    automacaoPerguntasRodandoLojas: new Set(),
    notificacoes: {
        carregando: false,
        atualizadoEm: 0,
        lojas: {},
        totais: { perguntas: 0 },
        erros: {}
    },
    aprovacoesNotificadas: new Set()
};
const TODAS_LOJAS_VALUE = '__todas_contas__';

const lojasGrid = document.getElementById('lojas-grid');
const lojasStatus = document.getElementById('lojas-status');
const perguntasAutomationControls = document.getElementById('perguntas-automation-controls');
const tabPerguntasNotificacao = document.getElementById('tab-perguntas-notificacao');
const perguntasStatus = document.getElementById('perguntas-status');
const perguntasSummary = document.getElementById('perguntas-summary');
const perguntasList = document.getElementById('perguntas-list');
const perguntasDetail = document.getElementById('perguntas-detail');
const perguntasPagination = document.getElementById('perguntas-pagination');
const statusFiltro = document.getElementById('status-filtro');
const btnRecarregar = document.getElementById('btn-recarregar');
const perguntasUltimaAtualizacao = document.getElementById('perguntas-ultima-atualizacao');
const posVendaLojaStatus = document.getElementById('pos-venda-loja-status');
const posVendaDias = document.getElementById('pos-venda-dias');
const posVendaBusca = document.getElementById('pos-venda-busca');
const posVendaNaoLidas = document.getElementById('pos-venda-nao-lidas');
const btnPosVendaBuscar = document.getElementById('btn-pos-venda-buscar');
const btnPosVendaLimparBusca = document.getElementById('btn-pos-venda-limpar-busca');
const btnPosVendaRecarregar = document.getElementById('btn-pos-venda-recarregar');
const posVendaLojasGrid = document.getElementById('pos-venda-lojas-grid');
const posVendaStatus = document.getElementById('pos-venda-status');
const posVendaSummary = document.getElementById('pos-venda-summary');
const posVendaList = document.getElementById('pos-venda-list');
const posVendaPagination = document.getElementById('pos-venda-pagination');
const posVendaDetail = document.getElementById('pos-venda-detail');
const mediacaoLojaStatus = document.getElementById('mediacao-loja-status');
const mediacaoDias = document.getElementById('mediacao-dias');
const mediacaoBusca = document.getElementById('mediacao-busca');
const btnMediacaoBuscar = document.getElementById('btn-mediacao-buscar');
const btnMediacaoLimparBusca = document.getElementById('btn-mediacao-limpar-busca');
const btnMediacaoRecarregar = document.getElementById('btn-mediacao-recarregar');
const mediacaoStatus = document.getElementById('mediacao-status');
const mediacaoSummary = document.getElementById('mediacao-summary');
const mediacaoList = document.getElementById('mediacao-list');
const aiTrainingStatus = document.getElementById('ai-training-status');
const aiTrainingTypeTabs = Array.from(document.querySelectorAll('.training-type-tab'));
const aiTrainingOrientacoesLabel = document.getElementById('ai-training-orientacoes-label');
const aiTrainingOrientacoes = document.getElementById('ai-training-orientacoes');
const aiTrainingScope = document.getElementById('ai-training-scope');
const aiTrainingContextoLoja = document.getElementById('ai-training-contexto-loja');
const aiTrainingCompatibilidade = document.getElementById('ai-training-compatibilidade');
const aiTrainingProibicoes = document.getElementById('ai-training-proibicoes');
const aiTrainingSku = document.getElementById('ai-training-sku');
const aiTrainingSkuInfo = document.getElementById('ai-training-sku-info');
const aiTrainingNotasSku = document.getElementById('ai-training-notas-sku');
const aiTrainingExemploPergunta = document.getElementById('ai-training-exemplo-pergunta');
const aiTrainingExemploResposta = document.getElementById('ai-training-exemplo-resposta');
const aiTrainingExemploEscopo = document.getElementById('ai-training-exemplo-escopo');
const btnAiTrainingAdicionarExemplo = document.getElementById('btn-ai-training-adicionar-exemplo');
const aiTrainingExamplesList = document.getElementById('ai-training-examples-list');
const aiTrainingPergunta = document.getElementById('ai-training-pergunta');
const aiTrainingContexto = document.getElementById('ai-training-contexto');
const aiTrainingChat = document.getElementById('ai-training-chat');
const aiTrainingChatHead = document.getElementById('ai-training-chat-head');
const btnAiTrainingSalvar = document.getElementById('btn-ai-training-salvar');
const btnAiTrainingSimular = document.getElementById('btn-ai-training-simular');

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[char]));
}

function corrigirTextoQuebrado(value) {
    const texto = String(value ?? '');
    if (!/[ÃÂ]/.test(texto)) return texto;
    try {
        return decodeURIComponent(escape(texto));
    } catch (_error) {
        return texto;
    }
}

function normalizarMensagemErroValor(value) {
    if (value === null || value === undefined || value === '') return '';
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
        return value.map(normalizarMensagemErroValor).filter(Boolean).join(' | ');
    }
    if (typeof value === 'object') {
        const direto = value.message || value.error || value.detail || value.msg || value.reason;
        if (direto) return normalizarMensagemErroValor(direto);
        try {
            return JSON.stringify(value);
        } catch (_error) {
            return String(value);
        }
    }
    return String(value);
}

function mensagemErroApi(data, fallback) {
    return corrigirTextoQuebrado(normalizarMensagemErroValor(data && data.detail ? data.detail : data) || fallback || 'Erro inesperado.');
}

function mensagemErro(error) {
    return corrigirTextoQuebrado(normalizarMensagemErroValor(error && error.message ? error.message : error) || 'Erro inesperado.');
}

function formatarData(value) {
    if (!value) return '-';
    const data = new Date(value);
    if (Number.isNaN(data.getTime())) return value;
    return data.toLocaleString('pt-BR', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function rotuloStatus(status) {
    const mapa = {
        ANSWERED: 'Respondida',
        UNANSWERED: 'Não respondida',
        CLOSED_UNANSWERED: 'Fechada sem resposta',
        UNDER_REVIEW: 'Em revisão',
        BANNED: 'Bloqueada'
    };
    return mapa[String(status || '').toUpperCase()] || (status || '-');
}

function classeStatus(status) {
    const valor = String(status || '').toUpperCase();
    if (valor === 'ANSWERED') return 'ok';
    if (valor === 'UNANSWERED' || valor === 'UNDER_REVIEW') return 'warn';
    return 'danger';
}

function ordenarPerguntasRecentes(perguntas) {
    return [...perguntas].sort((a, b) => {
        const dataA = new Date(a.date_created || a.last_updated || 0).getTime() || 0;
        const dataB = new Date(b.date_created || b.last_updated || 0).getTime() || 0;
        return dataB - dataA;
    });
}

function formatarTempoRespostaML(minutos) {
    const totalMinutos = Number(minutos);
    if (!Number.isFinite(totalMinutos) || totalMinutos < 0) return 'Sem dados';
    const arredondado = Math.round(totalMinutos);
    if (arredondado < 60) return `${arredondado} min`;
    const horas = Math.floor(arredondado / 60);
    const mins = arredondado % 60;
    if (horas < 24) return mins ? `${horas}h ${mins}min` : `${horas}h`;
    const dias = Math.floor(horas / 24);
    const horasRestantes = horas % 24;
    return horasRestantes ? `${dias}d ${horasRestantes}h` : `${dias}d`;
}

function textoGanhoVendasML(segmento) {
    if (!segmento || segmento.sales_percent_increase === null || segmento.sales_percent_increase === undefined) {
        return 'Sem projeção de aumento';
    }
    const valor = Number(segmento.sales_percent_increase);
    if (!Number.isFinite(valor)) return 'Sem projeção de aumento';
    return `Potencial de +${valor}% em vendas`;
}

function metricTempoRespostaML(titulo, segmento, subtituloPadrao) {
    const tempo = segmento && segmento.response_time !== undefined ? segmento.response_time : null;
    const subtitulo = segmento && segmento.sales_percent_increase !== undefined
        ? textoGanhoVendasML(segmento)
        : subtituloPadrao;
    return `<div class="metric"><strong>${escapeHtml(formatarTempoRespostaML(tempo))}</strong><span>${escapeHtml(titulo)} · ${escapeHtml(subtitulo)}</span></div>`;
}

function formatarCronometroPerguntas(ms) {
    const totalSegundos = Math.max(0, Math.ceil(Number(ms || 0) / 1000));
    const horas = Math.floor(totalSegundos / 3600);
    const minutos = Math.floor((totalSegundos % 3600) / 60);
    const segundos = totalSegundos % 60;
    const doisDigitos = (valor) => String(valor).padStart(2, '0');
    if (horas > 0) return `${horas}h ${doisDigitos(minutos)}min ${doisDigitos(segundos)}s`;
    return `${minutos}min ${doisDigitos(segundos)}s`;
}

function chaveLojaCronometro(nome) {
    return String(nome || '').trim();
}

function lojasMercadoLivreConectadas() {
    return state.lojas.filter((loja) => {
        const nome = String(loja && loja.nome || '').trim();
        return nome && loja.mercadolivre_conectado === true;
    });
}

function montarSeletorEscopoTreinamento() {
    if (!aiTrainingScope) return;
    const valorAtual = String(state.treinamentoEscopoLoja || aiTrainingScope.value || '').trim();
    aiTrainingScope.innerHTML = '';

    const optGlobal = document.createElement('option');
    optGlobal.value = '';
    optGlobal.textContent = 'Padrao para todas as contas';
    aiTrainingScope.appendChild(optGlobal);

    lojasMercadoLivreConectadas().forEach((loja) => {
        const nome = String(loja && loja.nome || '').trim();
        if (!nome) return;
        const option = document.createElement('option');
        option.value = nome;
        option.textContent = nome;
        aiTrainingScope.appendChild(option);
    });

    const valorExiste = !valorAtual || Array.from(aiTrainingScope.options).some((option) => option.value === valorAtual);
    state.treinamentoEscopoLoja = valorExiste ? valorAtual : '';
    aiTrainingScope.value = state.treinamentoEscopoLoja;
}

function lojaEscopoTreinamento() {
    return String(state.treinamentoEscopoLoja || aiTrainingScope?.value || '').trim();
}

function rotuloEscopoTreinamento() {
    const loja = lojaEscopoTreinamento();
    return loja || 'padrao de todas as contas';
}
