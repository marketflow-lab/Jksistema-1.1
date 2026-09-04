(function (global) {
    'use strict';

    const cadastro = global.JKCadastro;
    if (!cadastro || !cadastro.__runtimeInitialized) throw new Error('Runtime do Cadastro não inicializado.');
    if (cadastro.components.has('core')) return;

    const { elements, state, constants } = cadastro.runtime;
    const storeTools = global.JKCadastroStore;
    if (!storeTools) throw new Error('Escopo de loja do Cadastro não inicializado.');
    const colunasFixasPrimeiro = [
        'sku', 'loja_sync', 'foto', 'produto_bling', 'nome', 'produto', 'ncm', 'ncm_validade', 'monofasico',
        'monofasico_confianca', 'cest', 'categoria', 'custo', 'preco', 'descricao', 'imposto', 'updated_at'
    ];
    const colunasOcultas = new Set([
        'marca', 'mlb_principal', 'mlb_ids', 'qtd_anuncios_mlb', 'titulos_anuncios_mlb',
        'custos_frete_mlb', 'modalidades_mlb', 'gtins_mlb', 'monofasico_status',
        'monofasico_fundamento', 'monofasico_fonte', 'monofasico_motivo', 'monofasico_verificado_em',
        'ncm_fonte_auditoria', 'ncm_descricao_oficial', 'ncm_verificado_em', 'store_id', 'sku_normalizado',
        'row_version', 'updated_at_utc', 'deleted_at_utc', 'scope_source'
    ]);
    const colunasDetalheProvedor = new Set([
        'id_bling', 'id_produto_pai_bling', 'nome_bling', 'situacao_bling', 'tipo_bling', 'formato_bling',
        'data_validade_bling', 'tipo_producao_bling', 'condicao_bling', 'frete_gratis_bling',
        'action_estoque_bling', 'linha_produto_bling', 'artigo_perigoso_bling', 'duns_bling', 'ncm_bling',
        'cest_bling', 'categoria_id_bling', 'categoria_bling', 'marca_bling', 'gtin_bling',
        'gtin_embalagem_bling', 'preco_bling', 'custo_bling', 'estoque_fisico_bling',
        'estoque_virtual_bling', 'estoques_bling_json', 'unidade_bling', 'estoque_minimo_bling',
        'estoque_maximo_bling', 'localizacao_bling', 'cross_docking_bling', 'crossdocking_bling',
        'peso_liquido_bling', 'peso_bruto_bling', 'largura_bling', 'altura_bling', 'profundidade_bling',
        'volumes_bling', 'itens_por_caixa_bling', 'descricao_curta_bling', 'descricao_bling',
        'descricao_complementar_bling', 'descricao_embalagem_discreta_bling', 'observacoes_bling',
        'link_externo_bling', 'imagem_url_bling', 'imagens_bling_json', 'imagens_bling', 'video_bling',
        'fornecedor_bling_json', 'tributacao_bling_json', 'tributacao_bling', 'variacoes_bling_json',
        'variacoes_bling', 'componentes_bling_json', 'estrutura_bling', 'campos_customizados_bling_json',
        'campos_customizados_bling', 'unidade_medida_dimensoes_bling', 'dimensoes_bling',
        'consultado_em_utc_bling', 'mlb_principal', 'mlb_ids', 'qtd_anuncios_mlb', 'titulos_anuncios_mlb',
        'categoria_id_mlb', 'gtins_mlb', 'preco_ml', 'moeda_ml', 'preco_base_ml', 'preco_original_ml',
        'estoque_disponivel_ml', 'vendidos_acumulados_ml', 'termos_venda_ml_json', 'envio_ml_json',
        'condicao_ml', 'condicao_nome_ml', 'garantia_ml', 'criado_em_ml', 'atualizado_em_ml',
        'canais_ml_json', 'tags_ml_json', 'familia_nome_ml', 'familia_id_ml', 'familia_ids_ml',
        'dominio_id_ml', 'site_id_ml', 'user_product_nome_ml', 'site_id_user_product_ml',
        'catalog_product_id_user_product_ml', 'catalog_product_ids_user_product_ml',
        'criado_em_user_product_ml', 'atualizado_em_user_product_ml',
        'atributos_user_product_ml_json', 'imagens_user_product_ml_json',
        'miniatura_user_product_ml_json', 'miniatura_url_user_product_ml', 'tags_user_product_ml_json',
        'bundle_user_product_ml_json', 'estoque_user_product_total_ml', 'estoque_localizacoes_ml_json',
        'estoque_multiorigem_ml', 'anuncio_catalogo_ml', 'modo_compra_ml', 'status_ml',
        'variacao_id_ml', 'variacao_ids_ml',
        'listing_type_ml', 'catalog_product_id_ml', 'catalog_product_ids_ml', 'user_product_id_ml',
        'user_product_ids_ml', 'inventory_id_ml', 'inventory_ids_ml', 'link_ml', 'foto_url_ml',
        'imagens_ml_json',
        'atributos_ml_json', 'anuncios_ml_json', 'consultado_em_utc'
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

    function colunaEhDetalheProvedor(coluna) {
        const key = String(coluna || '').trim().toLowerCase();
        return colunasDetalheProvedor.has(key);
    }

    function obterClientId() {
        return storeTools.obterClientId() || null;
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
            sku: 'SKU', loja_sync: 'Loja', nome: 'Nome', mlb_principal: 'MLB Principal', mlb_ids: 'MLBs',
            qtd_anuncios_mlb: 'Anúncios MLB', produto: 'Produto', categoria: 'Para que serve',
            marca: 'Marca', foto: 'Foto', ncm: 'NCM', ncm_validade: 'Validade NCM', cest: 'CEST',
            monofasico: 'Monofásico', monofasico_confianca: 'Confiança', produto_bling: 'Produto Bling',
            titulo_ml: 'Título Mercado Livre', custo: 'Custo', preco: 'Preço', descricao: 'Descrição',
            imposto: 'Imposto', updated_at: 'Atualizado em'
        };
        return mapa[coluna] || String(coluna || '').replace(/^cg_/i, '').replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
    }

    function escaparHtml(valor) {
        return String(valor || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function obterUrlFoto(valor, storeId) {
        return storeTools.urlFoto(obterClientId(), storeId || state.storeIdSelecionado, valor);
    }

    function urlEdicaoSku(sku, storeId) {
        return storeTools.urlPagina('/cadastro_editar_item.html', storeId || state.storeIdSelecionado, { sku: String(sku || '').trim() });
    }

    function salvarProdutoParaEdicao(item) {
        const sku = String(item && item.sku || '').trim();
        const storeId = String(item && item.store_id || state.storeIdSelecionado || '').trim();
        if (!sku || !storeId || !state.clientId) return false;
        return storeTools.salvarCacheEdicao(state.clientId, storeId, item);
    }

    function abrirProdutoParaEdicao(item) {
        if (!salvarProdutoParaEdicao(item)) return false;
        global.location.href = urlEdicaoSku(item.sku, item.store_id || state.storeIdSelecionado);
        return true;
    }

    function renderConteudoCelula(coluna, item) {
        if (coluna === 'custo' || coluna === 'preco') return escaparHtml(formatarNumero(item[coluna]));
        if (coluna === 'sku') {
            const sku = String(item[coluna] || '').trim();
            if (!state.storeIdSelecionado) return escaparHtml(formatarSkuExibicao(sku));
            const href = urlEdicaoSku(sku, item.store_id || state.storeIdSelecionado);
            return `<a class="sku-edit-link" href="${escaparHtml(href)}" data-sku="${escaparHtml(sku)}">${escaparHtml(formatarSkuExibicao(sku))}</a>`;
        }
        if (coluna === 'foto') {
            const fotoUrl = obterUrlFoto(item[coluna], item.store_id);
            if (!fotoUrl) return '<span class="foto-empty">Sem foto</span>';
            const segura = escaparHtml(fotoUrl);
            return `<div class="foto-cell" data-foto-url="${segura}"><span class="foto-empty">Carregando foto...</span></div>`;
        }
        return escaparHtml(String(item[coluna] || ''));
    }

    function construirColunas(lista) {
        if (!lista.length) return [];
        const presentes = new Set();
        lista.forEach(item => Object.keys(item || {}).forEach(chave => presentes.add(chave)));
        const fixas = [...colunasFixasPrimeiro];
        const extras = Array.from(presentes)
            .filter(coluna => !fixas.includes(coluna) && !colunasOcultas.has(coluna)
                && !colunaEhIndesejada(coluna) && !colunaEhDetalheProvedor(coluna))
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
        for (const candidato of [item && item.nome, item && item.produto, item && item.produto_bling,
            item && item.titulo_ml, item && item.titulo]) {
            const texto = String(candidato === undefined || candidato === null ? '' : candidato).trim();
            if (texto && !textoProdutoSuspeito(texto)) return texto;
        }
        return '';
    }

    cadastro.core = Object.freeze({
        abrirProdutoParaEdicao, construirColunas, escaparHtml, formatarNumero, formatarSkuExibicao,
        normalizarBuscaSku, normalizarBuscaTexto, normalizarLegendaColuna, obterClientId,
        obterNomeProdutoCadastro, renderConteudoCelula, salvarLarguras, salvarProdutoParaEdicao,
        setStatus, splitLista, urlEdicaoSku,
    });
    cadastro.components.add('core');
})(window);
