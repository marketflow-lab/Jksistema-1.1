(function (global) {
    'use strict';

    const cadastro = global.JKCadastro;
    if (!cadastro || !cadastro.components.has('custos-mlb')) throw new Error('Painéis do Cadastro não inicializados.');
    if (cadastro.components.has('produtos-carregamento')) return;

    const { elements, state } = cadastro.runtime;
    const core = cadastro.core;
    const tabela = cadastro.produtosTabela;
    const secondary = cadastro.custosMlb;
    const storeTools = global.JKCadastroStore;
    const TIMEOUT_MS = 30000;
    let opcoes = null;
    let requestSeq = 0;
    let controllerAtual = null;

    function configurar(valor) {
        opcoes = Object.freeze({ ...valor });
    }

    function mensagemErroPayload(payload, fallback) {
        if (typeof payload === 'string' && payload.trim()) return payload.trim();
        if (!payload || typeof payload !== 'object') return fallback;
        const detail = payload.detail;
        if (typeof detail === 'string' && detail.trim()) return detail.trim();
        if (detail && typeof detail === 'object') {
            const message = detail.message || detail.mensagem || detail.detail;
            if (typeof message === 'string' && message.trim()) return message.trim();
        }
        const message = payload.message || payload.mensagem || payload.error;
        return typeof message === 'string' && message.trim() ? message.trim() : fallback;
    }

    async function lerRespostaJsonSegura(response) {
        let payload;
        if (response && typeof response.text === 'function') {
            const body = await response.text();
            if (!String(body || '').trim()) {
                throw new Error(response.ok ? 'O servidor retornou uma resposta vazia.' : `Falha HTTP ${response.status}.`);
            }
            try {
                payload = JSON.parse(body);
            } catch (_error) {
                throw new Error(response.ok
                    ? 'O servidor retornou uma resposta inválida.'
                    : `Falha HTTP ${response.status}: resposta inválida do servidor.`);
            }
        } else if (response && typeof response.json === 'function') {
            try {
                payload = await response.json();
            } catch (_error) {
                throw new Error(response && response.ok
                    ? 'O servidor retornou uma resposta inválida.'
                    : `Falha HTTP ${response && response.status || 0}: resposta inválida do servidor.`);
            }
        } else {
            throw new Error('Resposta indisponível do servidor.');
        }
        if (!response.ok) throw new Error(mensagemErroPayload(payload, `Falha HTTP ${response.status}.`));
        return payload;
    }

    function extrairProdutos(payload) {
        const produtos = Array.isArray(payload)
            ? payload
            : payload && typeof payload === 'object' && Array.isArray(payload.produtos)
                ? payload.produtos
                : null;
        if (!produtos) throw new Error('O servidor retornou produtos em formato inválido.');
        if (produtos.some(item => !item || typeof item !== 'object' || Array.isArray(item))) {
            throw new Error('O servidor retornou um produto inválido.');
        }
        return produtos;
    }

    function validarEnvelopeConsolidado(payload, produtos) {
        if (!payload || Array.isArray(payload) || typeof payload !== 'object'
            || !Array.isArray(payload.lojas) || !Number.isInteger(payload.total)
            || payload.total < 0 || typeof payload.partial !== 'boolean') {
            throw new Error('O servidor retornou a carga consolidada em formato inválido.');
        }
        if (payload.total !== produtos.length) {
            throw new Error('O total da carga consolidada não corresponde aos produtos recebidos.');
        }
        const autorizadas = new Set(state.lojas.map(loja => String(loja.store_id || '').trim()));
        const vistas = new Set();
        const totaisProdutos = new Map();
        produtos.forEach(item => {
            const storeId = String(item.store_id || '').trim();
            if (!autorizadas.has(storeId)) throw new Error('O servidor retornou um produto fora das lojas autorizadas.');
            totaisProdutos.set(storeId, (totaisProdutos.get(storeId) || 0) + 1);
        });
        payload.lojas.forEach(item => {
            const storeId = String(item && item.store_id || '').trim();
            const status = String(item && item.status || '').toLowerCase();
            if (!item || typeof item !== 'object' || !autorizadas.has(storeId)
                || vistas.has(storeId) || !['ok', 'error'].includes(status)
                || !Number.isInteger(item.total) || item.total < 0) {
                throw new Error('O servidor retornou o resumo de lojas em formato inválido.');
            }
            const totalProdutosLoja = totaisProdutos.get(storeId) || 0;
            if (item.total !== totalProdutosLoja || (status === 'error' && item.total !== 0)) {
                throw new Error('A contagem de produtos por loja não corresponde ao resumo consolidado.');
            }
            vistas.add(storeId);
        });
        if (vistas.size !== autorizadas.size) {
            throw new Error('O resumo consolidado não inclui todas as lojas autorizadas.');
        }
        const temFalha = payload.lojas.some(item => String(item.status).toLowerCase() !== 'ok');
        const temSucesso = payload.lojas.some(item => String(item.status).toLowerCase() === 'ok');
        if (payload.lojas.length && !temSucesso) {
            throw new Error('Nenhuma loja pôde ser carregada.');
        }
        const somaTotais = payload.lojas.reduce((soma, item) => soma + item.total, 0);
        if (somaTotais !== produtos.length) {
            throw new Error('A soma de produtos das lojas não corresponde à carga consolidada.');
        }
        if (payload.partial !== (temFalha && temSucesso)) {
            throw new Error('O indicador de carga parcial não corresponde ao resumo de lojas.');
        }
    }

    function prepararProdutos(payload, storeId) {
        const produtos = extrairProdutos(payload);
        if (storeId) {
            const loja = opcoes.lojaPorId(storeId);
            if (!loja) throw new Error('Loja inválida ou indisponível para este cliente.');
            return produtos.map(item => ({ ...item, store_id: storeId, loja_sync: opcoes.rotuloLojaExibicao(loja) }));
        }
        validarEnvelopeConsolidado(payload, produtos);
        return produtos.map(item => {
            const itemStoreId = String(item.store_id || '').trim();
            const loja = opcoes.lojaPorId(itemStoreId);
            if (!itemStoreId || !loja) throw new Error('O servidor retornou um produto fora das lojas autorizadas.');
            return { ...item, store_id: itemStoreId, loja_sync: opcoes.rotuloLojaExibicao(loja) };
        });
    }

    function escopoAtual() {
        return state.storeIdSelecionado ? `store:${state.storeIdSelecionado}` : 'all';
    }

    function urlEscopo(storeId) {
        const base = storeId ? storeTools.apiLoja(storeId, 'produtos') : '/api/cadastro/lojas/produtos';
        return `${base}?view=summary`;
    }

    function horario(timestamp) {
        if (!timestamp) return '';
        return new Date(timestamp).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    function atualizarVisual() {
        secondary.atualizarFiltroLojasCustos();
        tabela.renderTabela();
        secondary.renderCustos();
        secondary.montarSeletorSku();
        secondary.renderDetalhesMlbPorSku(elements.skuSelect.value || '');
    }

    function cancelar() {
        requestSeq += 1;
        if (controllerAtual) controllerAtual.abort();
        controllerAtual = null;
        state.carregandoProdutos = false;
        state.carregamentoProdutosProgresso = { concluidas: 0, total: 0 };
        elements.painelProdutos.setAttribute('aria-busy', 'false');
    }

    async function carregar() {
        if (!opcoes) throw new Error('Carregamento de produtos não configurado.');
        if (controllerAtual) controllerAtual.abort();
        const minhaSeq = ++requestSeq;
        const escopoSolicitado = escopoAtual();
        const storeId = String(state.storeIdSelecionado || '').trim();
        const totalLojas = storeId ? 1 : state.lojas.length;
        const preservarTabela = state.produtosEscopoCarregado === escopoSolicitado;
        const controller = typeof global.AbortController === 'function' ? new global.AbortController() : null;
        let tempoEsgotado = false;
        const timeoutId = controller ? global.setTimeout(() => { tempoEsgotado = true; controller.abort(); }, TIMEOUT_MS) : null;
        controllerAtual = controller;
        state.carregandoProdutos = true;
        state.carregamentoProdutosProgresso = { concluidas: 0, total: totalLojas };
        opcoes.atualizarEstadoMutacoes();
        elements.painelProdutos.setAttribute('aria-busy', 'true');
        core.setStatus(totalLojas > 1 ? `Carregando produtos: 0 de ${totalLojas} lojas...` : 'Carregando produtos da loja...', 'loading');
        elements.btnAtualizar.disabled = true;
        if (!preservarTabela) {
            state.produtos = [];
            atualizarVisual();
        }
        try {
            state.clientId = state.clientId || core.obterClientId();
            if (!state.clientId) {
                global.location.href = '/frontend_index.html';
                return false;
            }
            const response = await global.fetch(urlEscopo(storeId), {
                headers: opcoes.authHeaders(), cache: 'no-store', ...(controller ? { signal: controller.signal } : {}),
            });
            const payload = await lerRespostaJsonSegura(response);
            if (minhaSeq !== requestSeq || escopoSolicitado !== escopoAtual()) return false;
            const produtos = prepararProdutos(payload, storeId);
            const estados = !storeId ? payload.lojas : [];
            const falhas = estados.filter(item => String(item.status).toLowerCase() !== 'ok');
            state.carregamentoProdutosProgresso = { concluidas: storeId ? 1 : estados.length, total: totalLojas };
            state.produtos = produtos;
            state.produtosEscopoCarregado = escopoSolicitado;
            state.ultimaAtualizacaoProdutosEm = Date.now();
            atualizarVisual();
            const atualizado = horario(state.ultimaAtualizacaoProdutosEm);
            if (!storeId && payload.partial) {
                const nomes = falhas.map(item => item.loja_sync || (opcoes.lojaPorId(item.store_id) || {}).nome || item.store_id).filter(Boolean);
                core.setStatus(`Carga parcial: ${produtos.length} produto(s) de ${totalLojas - falhas.length} de ${totalLojas} lojas.${nomes.length ? ` Falha em: ${nomes.join(', ')}.` : ''} Atualizado às ${atualizado}.`, 'warning');
            } else {
                const escopo = storeId ? (opcoes.lojaPorId(storeId) || {}).nome : `${totalLojas} loja(s)`;
                core.setStatus(`Produtos carregados: ${produtos.length}${escopo ? ` em ${escopo}` : ''}. Atualizado às ${atualizado}.`, 'success');
            }
            return true;
        } catch (error) {
            if (minhaSeq !== requestSeq || escopoSolicitado !== escopoAtual()) return false;
            if (error && error.name === 'AbortError' && !tempoEsgotado) return false;
            if (!preservarTabela) {
                state.produtos = [];
                atualizarVisual();
            }
            const motivo = tempoEsgotado ? 'A consulta excedeu o limite de 30 segundos.' : error.message;
            const anterior = preservarTabela ? horario(state.ultimaAtualizacaoProdutosEm) : '';
            core.setStatus(`Erro ao carregar produtos: ${motivo}${anterior ? ` Os dados atualizados às ${anterior} continuam na tela.` : ''}`, 'error');
            return false;
        } finally {
            if (timeoutId) global.clearTimeout(timeoutId);
            if (minhaSeq === requestSeq) {
                controllerAtual = null;
                state.carregandoProdutos = false;
                elements.painelProdutos.setAttribute('aria-busy', 'false');
                elements.btnAtualizar.disabled = false;
                opcoes.atualizarEstadoMutacoes();
            }
        }
    }

    cadastro.produtosCarregamento = Object.freeze({ configurar, carregar, cancelar });
    cadastro.components.add('produtos-carregamento');
})(window);
