(function (global) {
    'use strict';

    const formTools = global.JKCadastroForm;
    if (!formTools) throw new Error('Núcleo de formulários do Cadastro não inicializado.');
    if (formTools.criarEditorMlb && formTools.extrairMlbFreteEditor) return;

    function criarLinhaMlb(mlb = '', frete = '') {
        const row = document.createElement('div');
        row.className = 'mlb-row';
        const inputFrete = document.createElement('input');
        inputFrete.type = 'text';
        inputFrete.className = 'mlb-frete';
        inputFrete.placeholder = 'Frete';
        inputFrete.value = String(frete || '');
        const inputMlb = document.createElement('input');
        inputMlb.type = 'text';
        inputMlb.className = 'mlb-id';
        inputMlb.placeholder = 'MLB123456789';
        inputMlb.value = String(mlb || '');
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'mlb-del';
        remove.textContent = '×';
        remove.addEventListener('click', () => row.remove());
        row.append(inputFrete, inputMlb, remove);
        return row;
    }

    function criarEditorMlb(produto = {}) {
        const field = document.createElement('div');
        field.className = 'field full';
        const label = document.createElement('label');
        label.textContent = 'mlb_ids (frete separado)';
        const box = document.createElement('div');
        box.className = 'mlb-editor';
        const head = document.createElement('div');
        head.className = 'mlb-head';
        head.innerHTML = '<div>Frete</div><div>MLB</div><div>Ação</div>';
        const rows = document.createElement('div');
        rows.className = 'mlb-rows';
        rows.id = 'mlbRows';
        const ids = String(produto.mlb_ids || '').split('|').map(value => value.trim()).filter(Boolean);
        const fretes = String(produto.custos_frete_mlb || '').split('|').map(value => value.trim());
        if (ids.length) ids.forEach((id, index) => rows.appendChild(criarLinhaMlb(id, fretes[index] || '')));
        else rows.appendChild(criarLinhaMlb());
        const add = document.createElement('button');
        add.type = 'button';
        add.className = 'mlb-add';
        add.textContent = '+ Adicionar MLB';
        add.addEventListener('click', () => rows.appendChild(criarLinhaMlb()));
        box.append(head, rows, add);
        field.append(label, box);
        return field;
    }

    function extrairMlbFreteEditor(container) {
        const ids = [];
        const fretes = [];
        container.querySelectorAll('.mlb-row').forEach(row => {
            const id = String((row.querySelector('.mlb-id') || {}).value || '').trim();
            const frete = String((row.querySelector('.mlb-frete') || {}).value || '').trim();
            if (!id) return;
            ids.push(id);
            fretes.push(frete || '-');
        });
        return { mlb_ids: ids.join('|'), custos_frete_mlb: fretes.join('|') };
    }

    global.JKCadastroForm = Object.freeze({ ...formTools, criarEditorMlb, extrairMlbFreteEditor });
})(window);
