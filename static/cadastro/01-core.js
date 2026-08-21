(function (global) {
    'use strict';

    const cadastro = global.JKCadastro;
    if (!cadastro || !cadastro.__runtimeInitialized) throw new Error('Runtime do Cadastro não inicializado.');
    if (cadastro.components.has('core')) return;

    const { elements, state, constants } = cadastro.runtime;
    const colunasFixasPrimeiro = [
        'sku', 'foto', 'produto_bling', 'nome', 'produto', 'ncm', 'ncm_validade', 'monofasico',
        'monofasico_confianca', 'cest', 'categoria', 'custo', 'preco', 'descricao', 'imposto', 'updated_at'
    ];
    const colunasOcultas = new Set([
        'marca', 'mlb_principal', 'mlb_ids', 'qtd_anuncios_mlb', 'titulos_anuncios_mlb',
        'custos_frete_mlb', 'modalidades_mlb', 'gtins_mlb', 'monofasico_status',
        'monofasico_fundamento', 'monofasico_fonte', 'monofasico_motivo', 'monofasico_verificado_em',
        'ncm_fonte_auditoria', 'ncm_descricao_oficial', 'ncm_verificado_em'
    ]);

    function salvarLarguras() {
        try {
            global.localStorage.setItem(constants.largurasStorageKey, JSON.stringify(state.largurasColunas));
        } catch (_error) {}
    }

    function colunaEhIndesejada(coluna) {
        const raw = String(coluna || '').trim();
        if (!raw || raw.length > 120) return true;
        if (/^\d{1,2}\/\d{1,2}\/\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$/.test(raw)) return true;
        const normalizada = raw.toLowerCase().replace(/_/g, ' ');
        return normalizada.includes('unnamed')
            || normalizada === 'sku key'
            || normalizada === 'cg sku'
            || normalizada === 'custoattr'
            || normalizada === 'custo attr'
            || normalizada === 'impostoattr'
            || normalizada === 'imposto attr';
    }

    function obterClientId() {
        try {
            const user = JSON.parse(global.localStorage.getItem('user_data') || 'null');
            return user && user.client_id ? user.client_id : null;
        } catch (_error) {
            return null;
        }
    }

    function setStatus(msg, cls) {
        elements.status.className = `status-bar ${cls || ''}`;
        elements.status.textContent = msg || '';
    }

    function formatarNumero(valor) {
        if (valor === null || valor === undefined || valor === '') return '';
        const numero = Number(valor);
        if (!Number.isFinite(numero)) return String(valor);
        return 'R$ ' + numero.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function formatarSkuExibicao(valor) {
        const sku = String(valor || '').trim();
        if (/^0\d{2}$/.test(sku)) {
            const numero = parseInt(sku, 10);
            if (numero >= 10 && numero <= 99) return String(numero);
        }
        return sku;
    }

    function normalizarBuscaTexto(valor) {
        return String(valor || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
    }

    function normalizarBuscaSku(valor) {
        return normalizarBuscaTexto(valor).replace(/^sku\s*/i, '').replace(/[^a-z0-9]/g, '');
    }

    function normalizarLegendaColuna(coluna) {
        const mapa = {
            sku: 'SKU', nome: 'Nome', mlb_principal: 'MLB Principal', mlb_ids: 'MLBs',
            qtd_anuncios_mlb: 'Anúncios MLB', produto: 'Produto', categoria: 'Para que serve',
            marca: 'Marca', foto: 'Foto', ncm: 'NCM', ncm_validade: 'Validade NCM', cest: 'CEST',
            monofasico: 'Monofásico', monofasico_confianca: 'Confiança', produto_bling: 'Produto Bling',
            custo: 'Custo', preco: 'Preço', descricao: 'Descrição', imposto: 'Imposto', updated_at: 'Atualizado em'
        };
        return mapa[coluna] || String(coluna || '').replace(/^cg_/i, '').replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
    }

    function escaparHtml(valor) {
        return String(valor || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function obterUrlFoto(valor) {
        const foto = String(valor || '').trim();
        if (!foto) return '';
        if (/^https?:\/\//i.test(foto)) return foto;
        const nomeArquivo = foto.split('/').pop();
        return nomeArquivo ? `/api/cadastro/foto-arquivo/${encodeURIComponent(nomeArquivo)}` : '';
    }

    function renderConteudoCelula(coluna, item) {
        if (coluna === 'custo' || coluna === 'preco') return escaparHtml(formatarNumero(item[coluna]));
        if (coluna === 'sku') return escaparHtml(formatarSkuExibicao(item[coluna]));
        if (coluna === 'foto') {
            const fotoUrl = obterUrlFoto(item[coluna]);
            if (!fotoUrl) return '<span class="foto-empty">Sem foto</span>';
            const segura = escaparHtml(fotoUrl);
            return `<div class="foto-cell"><a class="foto-link" href="${segura}" target="_blank" rel="noopener noreferrer"><img class="foto-thumb" src="${segura}" alt="Foto do SKU"></a></div>`;
        }
        return escaparHtml(String(item[coluna] || ''));
    }

    function construirColunas(lista) {
        if (!lista.length) return [];
        const presentes = new Set();
        lista.forEach(item => Object.keys(item || {}).forEach(chave => presentes.add(chave)));
        const fixas = [...colunasFixasPrimeiro];
        const extras = Array.from(presentes)
            .filter(coluna => !fixas.includes(coluna) && !colunasOcultas.has(coluna) && !colunaEhIndesejada(coluna))
            .sort((a, b) => a.localeCompare(b));
        return [...fixas, ...extras];
    }

    function splitLista(raw, separador) {
        return String(raw || '').split(separador).map(item => item.trim()).filter(Boolean);
    }

    function textoProdutoSuspeito(valor) {
        const texto = String(valor === undefined || valor === null ? '' : valor).trim();
        return !texto || texto.includes('\n') || texto.includes('\r') || texto.length > 220 || (texto.match(/;/g) || []).length >= 2;
    }

    function obterNomeProdutoCadastro(item) {
        for (const candidato of [item && item.nome, item && item.produto, item && item.produto_bling, item && item.titulo]) {
            const texto = String(candidato === undefined || candidato === null ? '' : candidato).trim();
            if (texto && !textoProdutoSuspeito(texto)) return texto;
        }
        return '';
    }

    cadastro.core = Object.freeze({
        construirColunas, escaparHtml, formatarNumero, formatarSkuExibicao,
        normalizarBuscaSku, normalizarBuscaTexto, normalizarLegendaColuna, obterClientId,
        obterNomeProdutoCadastro, renderConteudoCelula, salvarLarguras, setStatus, splitLista,
    });
    cadastro.components.add('core');
})(window);
