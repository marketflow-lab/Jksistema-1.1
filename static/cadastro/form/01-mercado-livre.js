(function (global) {
    'use strict';

    if (global.JKCadastroMercadoLivre) return;

    const camposPermitidos = Object.freeze([
        'mlb_principal',
        'mlb_ids',
        'qtd_anuncios_mlb',
        'titulo_ml',
        'titulos_anuncios_mlb',
        'categoria',
        'categoria_id_mlb',
        'marca',
        'modelo',
        'gtins_mlb',
        'descricao',
    ]);

    function texto(value) {
        return value === undefined || value === null ? '' : String(value).trim();
    }

    function mensagemErro(payload, status) {
        const detail = payload && payload.detail;
        if (typeof detail === 'string' && detail.trim()) return detail.trim();
        if (detail && typeof detail === 'object') {
            const message = texto(detail.message || detail.detail);
            if (message) return message;
        }
        const message = texto(payload && payload.message);
        return message || `HTTP ${status}`;
    }

    function urlConsulta(storeTools, storeId, sku, mlbPrincipal) {
        const params = new URLSearchParams({ sku: texto(sku) });
        if (texto(mlbPrincipal)) params.set('mlb_principal', texto(mlbPrincipal));
        return `${storeTools.apiLoja(storeId, 'mercado-livre/produto')}?${params.toString()}`;
    }

    async function consultar(options) {
        const config = options || {};
        const sku = texto(config.sku);
        const storeId = texto(config.storeId);
        if (!storeId) throw new Error('Selecione uma loja específica antes de consultar o Mercado Livre.');
        if (!sku) throw new Error('Informe o SKU antes de consultar o Mercado Livre.');
        if (!config.storeTools || typeof config.storeTools.apiLoja !== 'function') {
            throw new Error('Núcleo de lojas do Cadastro indisponível.');
        }
        if (typeof config.authHeaders !== 'function') throw new Error('Autenticação indisponível.');
        const response = await global.fetch(
            urlConsulta(config.storeTools, storeId, sku, config.mlbPrincipal),
            { method: 'GET', headers: config.authHeaders() },
        );
        let payload = null;
        try { payload = await response.json(); } catch (_error) {}
        if (!response.ok) throw new Error(mensagemErro(payload, response.status));
        if (!payload || payload.success !== true || !payload.campos || typeof payload.campos !== 'object') {
            throw new Error('O Mercado Livre retornou dados inválidos para o Cadastro.');
        }
        if (texto(payload.store_id) !== storeId || texto(payload.sku).toLocaleUpperCase() !== sku.toLocaleUpperCase()) {
            throw new Error('A resposta do Mercado Livre não corresponde à loja e ao SKU solicitados.');
        }
        if (payload.coverage_complete !== true) {
            throw new Error('A consulta do Mercado Livre ficou incompleta. Nenhum campo foi alterado; tente novamente.');
        }
        return payload;
    }

    function mapaFretes(rows) {
        const result = new Map();
        Array.from(rows && rows.querySelectorAll('.mlb-row') || []).forEach(row => {
            const id = texto((row.querySelector('.mlb-id') || {}).value).toUpperCase();
            const freight = texto((row.querySelector('.mlb-frete') || {}).value);
            if (id && !result.has(id)) result.set(id, freight);
        });
        return result;
    }

    function preencherEditorMlb(container, value) {
        const ids = texto(value).split('|').map(item => texto(item).toUpperCase()).filter(Boolean);
        const rows = container.querySelector('.mlb-rows');
        const add = container.querySelector('.mlb-add');
        if (!rows || !add || !ids.length) return false;
        const freights = mapaFretes(rows);
        while (rows.querySelectorAll('.mlb-row').length < ids.length) {
            const before = rows.querySelectorAll('.mlb-row').length;
            add.click();
            if (rows.querySelectorAll('.mlb-row').length === before) return false;
        }
        const currentRows = Array.from(rows.querySelectorAll('.mlb-row'));
        currentRows.forEach((row, index) => {
            if (index >= ids.length) {
                row.remove();
                return;
            }
            const idInput = row.querySelector('.mlb-id');
            const freightInput = row.querySelector('.mlb-frete');
            if (idInput) idInput.value = ids[index];
            if (freightInput) freightInput.value = freights.get(ids[index]) || '';
        });
        return true;
    }

    function aplicarCampos(container, fields) {
        const applied = [];
        camposPermitidos.forEach(field => {
            const value = texto(fields && fields[field]);
            if (!value) return;
            if (field === 'mlb_ids') {
                if (preencherEditorMlb(container, value)) applied.push(field);
                return;
            }
            const input = container.querySelector(`[name="${field}"]`);
            if (!input) return;
            input.value = value;
            applied.push(field);
        });
        return applied;
    }

    function foto(payload) {
        const source = payload && payload.foto && typeof payload.foto === 'object' ? payload.foto : {};
        const dataUrl = texto(source.data_url);
        const url = texto(source.url);
        return {
            dataUrl: /^data:image\/(?:jpeg|png|webp|gif);base64,[A-Za-z0-9+/]+={0,2}$/i.test(dataUrl) ? dataUrl : '',
            filename: texto(source.filename) || 'mercado-livre.jpg',
            url: /^https:\/\//i.test(url) ? url : '',
        };
    }

    function renderizarFotoPendente(container, dataUrl) {
        const image = global.document.createElement('img');
        image.src = dataUrl;
        image.alt = 'Prévia da imagem pendente';
        const note = global.document.createElement('div');
        note.className = 'foto-pending-note';
        note.textContent = 'Imagem pendente. Será salva somente ao confirmar.';
        container.replaceChildren(image, note);
    }

    function resumo(payload, applied) {
        const amount = Number.parseInt(texto(payload && payload.campos && payload.campos.qtd_anuncios_mlb), 10) || 0;
        const warnings = Array.isArray(payload && payload.avisos)
            ? payload.avisos.map(texto).filter(Boolean)
            : [];
        const fieldCount = Array.isArray(applied) ? applied.length : 0;
        const base = `${amount} anúncio(s) encontrado(s); ${fieldCount} campo(s) preenchido(s). Revise e confirme no botão de salvar.`;
        return warnings.length ? `${base} Avisos: ${warnings.join(' ')}` : base;
    }

    global.JKCadastroMercadoLivre = Object.freeze({
        aplicarCampos,
        camposPermitidos,
        consultar,
        foto,
        mensagemErro,
        preencherEditorMlb,
        renderizarFotoPendente,
        resumo,
        urlConsulta,
    });
})(window);
