(function (global) {
    'use strict';

    if (global.JKCadastroForm) return;
    const campoM3Individual = 'm3 individual';

    function setStatus(element, message, className) {
        element.className = `status ${className || ''}`;
        element.textContent = message || '';
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
            return 'M3 individual (m³ por unidade)';
        }
        return String(chave || '').replace(/^cg_/i, '').replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
    }

    function lerArquivoComoDataUrl(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result || ''));
            reader.onerror = () => reject(new Error('Falha ao ler imagem.'));
            reader.readAsDataURL(file);
        });
    }

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

    global.JKCadastroForm = Object.freeze({
        aplicarMascaraMoeda,
        brlParaNumeric,
        campoM3Individual,
        criarEditorMlb,
        extrairMlbFreteEditor,
        formatarNomeCampo,
        lerArquivoComoDataUrl,
        numericParaBRL,
        ordemCampos,
        setStatus,
    });
})(window);
