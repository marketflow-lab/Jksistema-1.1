(function (global) {
    'use strict';

    if (global.JKCadastroForm && global.JKCadastroStore) return;
    const { carregarFotoAutenticada, fotoPodeReceberAuth, revogarFotoCarregada, urlFoto } = global.JKCadastroFotos;
    const campoM3Individual = 'm3 individual';
    const camposCubagem = [campoM3Individual, 'm3_unidade', 'm3_origem', 'm3_atualizado_em', 'm3_status'];
    const STORE_CACHE_SCHEMA = 'jk.cadastro.edit-cache.v2';
    const STORE_PREFERENCE_PREFIX = 'cadastro_store_id_por_cliente:';
    const EDIT_ITEM_KEY = 'cadastro_editar_item';
    const EDIT_SKU_KEY = 'cadastro_editar_sku';
    const camposProdutoPadrao = [
        'sku', 'nome', 'produto', 'produto_bling', 'titulo_ml', 'categoria', 'categoria_id_mlb',
        'marca', 'modelo', 'gtins_mlb', ...camposCubagem, 'ncm', 'cest', 'custo', 'imposto',
        'preco', 'descricao', 'foto', 'mlb_principal', 'mlb_ids', 'qtd_anuncios_mlb',
        'titulos_anuncios_mlb', 'custos_frete_mlb',
    ];
    function obterClientId() {
        try {
            const user = JSON.parse(global.localStorage.getItem('user_data') || 'null');
            return String(user && user.client_id || '').trim();
        } catch (_error) {
            return '';
        }
    }
    function normalizarLojas(payload) {
        const origem = Array.isArray(payload) ? payload : payload && Array.isArray(payload.lojas) ? payload.lojas : [];
        const unicas = new Map();
        origem.forEach(item => {
            const storeId = String(item && item.store_id || '').trim();
            if (!storeId || unicas.has(storeId)) return;
            unicas.set(storeId, { ...item, store_id: storeId, nome: String(item.nome || storeId).trim() || storeId });
        });
        return Array.from(unicas.values()).sort((a, b) => a.nome.localeCompare(b.nome, undefined, { sensitivity: 'base' }));
    }
    async function carregarLojas(authHeaders) {
        const response = await global.fetch('/api/lojas', { headers: authHeaders() });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload && payload.detail || `HTTP ${response.status}`);
        return normalizarLojas(payload);
    }
    function lojaExiste(lojas, storeId) {
        const alvo = String(storeId || '').trim();
        return Boolean(alvo && lojas.some(loja => loja.store_id === alvo));
    }
    function chavePreferencia(clientId) {
        return `${STORE_PREFERENCE_PREFIX}${String(clientId || '').trim()}`;
    }
    function storeIdDaUrl(search) {
        const params = new URLSearchParams(search === undefined ? global.location.search : search);
        return { presente: params.has('store_id'), valor: String(params.get('store_id') || '').trim() };
    }
    function resolverStoreId(lojas, options) {
        const config = options || {};
        const clientId = String(config.clientId || obterClientId()).trim();
        const url = storeIdDaUrl(config.search);
        if (url.presente) {
            if (!lojaExiste(lojas, url.valor)) throw new Error('Loja inválida ou indisponível para este cliente.');
            return url.valor;
        }
        let preferida = '';
        try { preferida = String(global.localStorage.getItem(chavePreferencia(clientId)) || '').trim(); } catch (_error) {}
        if (lojaExiste(lojas, preferida)) return preferida;
        return '';
    }
    function salvarPreferencia(clientId, storeId, lojas) {
        const valor = String(storeId || '').trim();
        if (valor && !lojaExiste(lojas, valor)) throw new Error('Loja inválida ou indisponível para este cliente.');
        try { global.localStorage.setItem(chavePreferencia(clientId), valor); } catch (_error) {}
    }
    function preencherSeletor(select, lojas, options) {
        const config = options || {};
        const permitirTodas = config.permitirTodas !== false;
        const chaveNome = valor => String(valor || '').normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
        const contagemNomes = new Map();
        lojas.forEach(loja => {
            const chave = chaveNome(loja.nome);
            contagemNomes.set(chave, (contagemNomes.get(chave) || 0) + 1);
        });
        select.innerHTML = '';
        const inicial = document.createElement('option');
        inicial.value = '';
        inicial.textContent = permitirTodas ? 'Todas as lojas (somente leitura)' : 'Selecione uma loja';
        select.appendChild(inicial);
        lojas.forEach(loja => {
            const option = document.createElement('option');
            option.value = loja.store_id;
            option.textContent = contagemNomes.get(chaveNome(loja.nome)) > 1
                ? `${loja.nome} (${loja.store_id})`
                : loja.nome;
            select.appendChild(option);
        });
        select.value = lojaExiste(lojas, config.storeId) ? String(config.storeId) : '';
    }
    function apiLoja(storeId, suffix) {
        const valor = String(storeId || '').trim();
        if (!valor) throw new Error('Selecione uma loja específica.');
        const final = String(suffix || '').replace(/^\/+/, '');
        return `/api/cadastro/lojas/${encodeURIComponent(valor)}${final ? `/${final}` : ''}`;
    }
    function urlPagina(pathname, storeId, params) {
        const query = new URLSearchParams();
        const valor = String(storeId || '').trim();
        if (valor) query.set('store_id', valor);
        Object.entries(params || {}).forEach(([chave, item]) => {
            if (item !== undefined && item !== null && String(item) !== '') query.set(chave, String(item));
        });
        const texto = query.toString();
        return `${pathname}${texto ? `?${texto}` : ''}`;
    }
    function atualizarUrl(storeId, params) {
        if (!global.history || typeof global.history.replaceState !== 'function') return;
        global.history.replaceState(null, '', urlPagina(global.location.pathname, storeId, params));
    }

    function criarEnvelopeCache(clientId, storeId, sku, produto) {
        return {
            schema: STORE_CACHE_SCHEMA,
            client_id: String(clientId || '').trim(),
            store_id: String(storeId || '').trim(),
            sku: String(sku || '').trim(),
            produto: produto && typeof produto === 'object' ? produto : null,
        };
    }

    function envelopeCacheValido(envelope, clientId, storeId, sku) {
        return Boolean(envelope && envelope.schema === STORE_CACHE_SCHEMA
            && envelope.client_id === String(clientId || '').trim()
            && envelope.store_id === String(storeId || '').trim()
            && String(envelope.sku || '').trim().toLowerCase() === String(sku || '').trim().toLowerCase());
    }

    function salvarCacheEdicao(clientId, storeId, produto) {
        const sku = String(produto && produto.sku || '').trim();
        if (!clientId || !storeId || !sku) return false;
        const envelope = criarEnvelopeCache(clientId, storeId, sku, produto);
        try {
            const serializado = JSON.stringify(envelope);
            global.localStorage.setItem(EDIT_ITEM_KEY, serializado);
            global.localStorage.setItem(EDIT_SKU_KEY, serializado);
            global.localStorage.removeItem('cadastro_editar_lista');
        } catch (_error) {}
        return true;
    }

    function lerCacheEdicao(clientId, storeId, sku) {
        try {
            const envelope = JSON.parse(global.localStorage.getItem(EDIT_ITEM_KEY) || 'null');
            return envelopeCacheValido(envelope, clientId, storeId, sku) ? envelope.produto : null;
        } catch (_error) {
            return null;
        }
    }

    function obterSkuCache(clientId, storeId) {
        try {
            const envelope = JSON.parse(global.localStorage.getItem(EDIT_SKU_KEY) || 'null');
            if (!envelope || envelope.schema !== STORE_CACHE_SCHEMA) return '';
            if (envelope.client_id !== String(clientId || '').trim()) return '';
            if (envelope.store_id !== String(storeId || '').trim()) return '';
            return String(envelope.sku || '').trim();
        } catch (_error) {
            return '';
        }
    }

    function setStatus(element, message, className) {
        element.className = `status ${className || ''}`;
        element.textContent = message || '';
    }

    function garantirCamposProduto(campos) {
        const resultado = Array.isArray(campos) ? campos.filter(campo => campo !== 'cubagem_sku') : [];
        camposProdutoPadrao.forEach(campo => { if (!resultado.includes(campo)) resultado.push(campo); });
        return resultado;
    }

    function ordemCampos(campos, prioridade) {
        const score = campo => {
            const indice = prioridade.indexOf(campo);
            return indice === -1 ? 999 : indice;
        };
        return [...campos].sort((a, b) => score(a) - score(b) || a.localeCompare(b));
    }

    function numericParaBRL(valor) {
        const texto = String(valor || '').trim();
        if (!texto) return '';
        const numero = parseFloat(texto);
        return Number.isNaN(numero)
            ? texto
            : 'R$ ' + numero.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function brlParaNumeric(valor) {
        const texto = String(valor || '').replace(/R\$\s?/gi, '').replace(/\./g, '').replace(',', '.').trim();
        if (!texto) return '';
        const numero = parseFloat(texto);
        return Number.isNaN(numero) ? texto : numero.toFixed(2);
    }

    function aplicarMascaraMoeda(input) {
        input.addEventListener('input', function () {
            const digitos = this.value.replace(/\D/g, '');
            if (!digitos) {
                this.value = '';
                return;
            }
            const centavos = parseInt(digitos, 10);
            const reais = Math.floor(centavos / 100);
            const decimais = centavos % 100;
            this.value = `R$ ${reais.toLocaleString('pt-BR')},${String(decimais).padStart(2, '0')}`;
        });
    }

    function formatarNomeCampo(chave) {
        const normalizada = String(chave || '').trim().toLowerCase();
        if ([campoM3Individual, 'm3', 'cg_m3 individual', 'cg_m³ individual'].includes(normalizada)) {
            return 'M³ de transporte por unidade comercial';
        }
        const rotulosCubagem = { m3_unidade: 'Unidade comercial da cubagem', m3_origem: 'Origem da cubagem', m3_atualizado_em: 'Data da cubagem', m3_status: 'Situação da cubagem' };
        if (rotulosCubagem[normalizada]) return rotulosCubagem[normalizada];
        return String(chave || '').replace(/^cg_/i, '').replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
    }

    function ehCampoCubagemLegado(chave) {
        const normalizada = String(chave || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]/g, '');
        return ['cgm3individual', 'm3', 'cbm'].includes(normalizada);
    }

    function criarControleCubagem(chave, valor) {
        if (!camposCubagem.includes(chave)) return null;
        const input = document.createElement(chave === 'm3_unidade' ? 'select' : 'input');
        if (chave === 'm3_unidade') {
            [['', 'Selecione a unidade'], ['peca', 'Peça'], ['par', 'Par'], ['kit', 'Kit'], ['unidade_comercial', 'Unidade comercial']].forEach(([codigo, rotulo]) => {
                const option = document.createElement('option');
                option.value = codigo;
                option.textContent = rotulo;
                input.appendChild(option);
            });
        } else input.type = 'text';
        input.value = String(valor ?? '');
        if (chave === campoM3Individual) {
            input.inputMode = 'decimal';
            input.placeholder = 'Ex.: 0,000054';
            input.title = 'Volume de transporte com embalagem por peça, par ou kit vendido. Caixas de um único SKU: m³ da caixa ÷ unidades comerciais.';
        }
        if (chave === 'm3_origem') input.placeholder = 'Ex.: Packing List JK55, caixa 12';
        if (['m3_atualizado_em', 'm3_status'].includes(chave)) {
            input.readOnly = true;
            input.title = 'Informação gerada pelo cadastro ao salvar a cubagem.';
        }
        return input;
    }

    function lerArquivoComoDataUrl(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result || ''));
            reader.onerror = () => reject(new Error('Falha ao ler imagem.'));
            reader.readAsDataURL(file);
        });
    }

    global.JKCadastroStore = Object.freeze({
        apiLoja,
        atualizarUrl,
        carregarFotoAutenticada,
        carregarLojas,
        criarEnvelopeCache,
        envelopeCacheValido,
        fotoPodeReceberAuth,
        lerCacheEdicao,
        lojaExiste,
        normalizarLojas,
        obterClientId,
        obterSkuCache,
        preencherSeletor,
        resolverStoreId,
        revogarFotoCarregada,
        salvarCacheEdicao,
        salvarPreferencia,
        schemaCache: STORE_CACHE_SCHEMA,
        storeIdDaUrl,
        urlFoto,
        urlPagina,
    });

    global.JKCadastroForm = Object.freeze({
        aplicarMascaraMoeda,
        brlParaNumeric,
        campoM3Individual,
        camposCubagem,
        criarControleCubagem,
        ehCampoCubagemLegado,
        formatarNomeCampo,
        garantirCamposProduto,
        lerArquivoComoDataUrl,
        numericParaBRL,
        ordemCampos,
        setStatus,
    });
})(window);
