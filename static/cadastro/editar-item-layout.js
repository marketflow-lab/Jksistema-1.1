(function (global) {
    'use strict';

    function criarCampo(formTools, chave, valor) {
        const field = document.createElement('div');
        field.className = 'field' + (String(chave).includes('descricao') || String(chave).includes('link') ? ' full' : '');
        const label = document.createElement('label');
        label.setAttribute('for', `f_${chave}`);
        label.textContent = formTools.formatarNomeCampo(chave);
        const texto = String(valor || '');
        const input = texto.length > 120 || String(chave).includes('descricao')
            ? document.createElement('textarea')
            : document.createElement('input');
        if (input.tagName === 'INPUT') input.type = 'text';
        input.id = `f_${chave}`;
        input.name = chave;
        const normalizada = String(chave || '').toLowerCase();
        if (normalizada === 'sku') {
            input.readOnly = true;
            input.title = 'A chave SKU não pode ser alterada; crie outro SKU quando necessário.';
        }
        if (normalizada.startsWith('monofasico') || ['ncm_validade', 'ncm_descricao_oficial', 'ncm_fonte_auditoria', 'ncm_verificado_em'].includes(normalizada)) {
            input.readOnly = true;
            input.title = 'Campo gerado pela auditoria fiscal do cadastro.';
        }
        if (normalizada === formTools.campoM3Individual) {
            input.inputMode = 'decimal';
            input.placeholder = 'Ex.: 0,000054';
            input.title = 'Informe o M³ de uma unidade. A importação multiplica pela quantidade.';
        }
        if (chave === 'custo') {
            input.value = formTools.numericParaBRL(texto);
            input.dataset.tipo = 'moeda';
            formTools.aplicarMascaraMoeda(input);
        } else input.value = texto;
        field.append(label, input);
        return field;
    }

    function obterGrupoCampo(formTools, chave) {
        const c = String(chave || '').trim().toLowerCase();
        if (!c) return 'outros';
        if (['sku', 'nome', 'produto', 'produto_bling', 'categoria', 'marca'].includes(c)) return 'identificacao';
        if (['fabricante', 'fornecedor', 'modelo', 'linha'].includes(c) || c.includes('fabricante') || c.includes('fornecedor')) return 'fabricante';
        if (['ncm', 'cest', 'imposto', formTools.campoM3Individual, 'm3'].includes(c) || c.startsWith('monofasico') || c.startsWith('ncm_')) return 'fiscal';
        if (['custo', 'preco'].includes(c)) return 'precos';
        if (['titulo_ml', 'categoria_id_mlb', 'mlb_ids', 'custos_frete_mlb', 'mlb_principal', 'qtd_anuncios_mlb', 'titulos_anuncios_mlb', 'modalidades_mlb', 'gtins_mlb'].includes(c)) return 'anuncios';
        if (['descricao', 'foto', 'link', 'url'].includes(c) || c.includes('descricao') || c.includes('link') || c.includes('url')) return 'identificacao';
        if (['updated_at', 'created_at', 'data_atualizacao', 'data_criacao'].includes(c) || c.endsWith('_at') || c.startsWith('data_')) return 'datas';
        return 'outros';
    }

    function criarGrupoCampos(titulo) {
        const section = document.createElement('section');
        section.className = 'grupo-campos';
        const heading = document.createElement('h3');
        heading.className = 'grupo-titulo';
        heading.textContent = titulo;
        const grid = document.createElement('div');
        grid.className = 'grupo-grid';
        section.append(heading, grid);
        return { section, grid };
    }

    global.JKCadastroEditarLayout = Object.freeze({ criarCampo, obterGrupoCampo, criarGrupoCampos });
})(window);
