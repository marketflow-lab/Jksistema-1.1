function setStatus(texto, tipo = '') {
    statusEl.className = 'status' + (tipo ? ' ' + tipo : '');
    statusEl.textContent = texto || '';
}

function setSalesStatus(texto, tipo = '') {
    if (!fullSalesStatusEl) return;
    fullSalesStatusEl.className = 'sales-status' + (tipo ? ' ' + tipo : '');
    fullSalesStatusEl.textContent = texto || '';
}

function setAbcStatus(texto, tipo = '') {
    if (!fullAbcStatusEl) return;
    fullAbcStatusEl.className = 'sales-status' + (tipo ? ' ' + tipo : '');
    fullAbcStatusEl.textContent = texto || '';
}

function setSendStatus(texto, tipo = '') {
    if (!fullSendStatusEl) return;
    fullSendStatusEl.className = 'sales-status' + (tipo ? ' ' + tipo : '');
    fullSendStatusEl.textContent = texto || '';
}

function setTransitoStatus(texto, tipo = '') {
    if (!fullTransitoStatusEl) return;
    fullTransitoStatusEl.className = 'sales-status' + (tipo ? ' ' + tipo : '');
    fullTransitoStatusEl.textContent = texto || '';
}

function trocarAbaFull(nome) {
    abaAtiva = ['estoque', 'enviar', 'vendas', 'abc', 'transito'].includes(nome) ? nome : 'estoque';
    tabButtons.forEach(btn => {
        const active = btn.dataset.fullTab === abaAtiva;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    tabPanels.forEach(panel => {
        const active = panel.dataset.fullPanel === abaAtiva;
        panel.hidden = !active;
    });
    if (abaAtiva === 'vendas' && !vendasFullCarregado) {
        carregarVendasFull();
    }
    if (abaAtiva === 'enviar') {
        renderEnviarFull(enviarFullDados);
        if (enviarFullContaSelecionada && !enviarFullDados.length) carregarEnviarFull();
    }
    if (abaAtiva === 'abc' && !abcCarregado) {
        carregarCurvaAbcFull();
    }
    if (abaAtiva === 'transito' && !transitoCarregado) {
        carregarTransitoFull();
    }
}

function escapeHtml(valor) {
    return String(valor ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
}

function numero(valor) {
    if (typeof valor === 'number') return Number.isFinite(valor) ? valor : 0;
    const texto = String(valor ?? '').trim();
    if (!texto) return 0;
    const normalizado = texto.replace(/\./g, '').replace(',', '.').replace(/[^\d.-]/g, '');
    const n = Number(normalizado);
    return Number.isFinite(n) ? n : 0;
}

function primeiro(row, campos) {
    for (const campo of campos) {
        const valor = row && row[campo];
        if (valor !== undefined && valor !== null && String(valor).trim() !== '') return valor;
    }
    return '';
}

function idAnuncio(row) { return String(primeiro(row, ['id', 'item_id', 'mlb']) || '').trim(); }
function sku(row) { return String(primeiro(row, ['sku_display', 'sku', 'SKU', 'seller_sku', 'codigo', 'Codigo']) || '').trim(); }
function titulo(row) { return String(primeiro(row, ['title', 'titulo', 'titulo_anuncio', 'nome', 'produto', 'descricao']) || '-').trim(); }
function loja(row) { return String(primeiro(row, ['loja_sync', 'loja', 'Loja']) || '-').trim(); }
function foto(row) { return String(primeiro(row, ['thumbnail', 'secure_thumbnail', 'foto', 'image']) || '').trim(); }
function estoqueFull(row) { return numero(primeiro(row, ['available_quantity', 'saldo_full', 'estoque_full', 'full', 'fulfillment', 'quantidade_full'])); }
function vendidos(row) { return numero(primeiro(row, ['sold_quantity', 'vendidos', 'sales'])); }
function preco(row) { return numero(primeiro(row, ['price', 'preco', 'valor'])); }
function statusAnuncio(row) { return String(primeiro(row, ['status', 'situacao']) || '-').trim(); }
function tipoAnuncio(row) { return String(primeiro(row, ['listing_type_name', 'listing_type_id']) || '-').trim(); }
function linkAnuncio(row) { return String(primeiro(row, ['permalink', 'link', 'url']) || '').trim(); }
function variacoes(row) {
    return Array.isArray(row?.variations) ? row.variations.filter(item => item && typeof item === 'object') : [];
}
function chaveVariacoes(row, indice) {
    return idAnuncio(row) || `${loja(row)}-${sku(row)}-${indice}`;
}
function tituloVariacao(row, indice) {
    return String(primeiro(row, ['title', 'titulo', 'name']) || `Variação ${indice + 1}`).trim();
}
function idVariacao(row) { return String(primeiro(row, ['id', 'variation_id']) || '-').trim(); }
function skuVariacao(row) { return String(primeiro(row, ['sku', 'seller_sku', 'codigo']) || '-').trim(); }
function inventoryVariacao(row) { return String(primeiro(row, ['inventory_id']) || '-').trim(); }
function estoqueVariacao(row) { return numero(primeiro(row, ['full_available_quantity', 'available_quantity', 'saldo_full'])); }
function indisponivelVariacao(row) { return numero(primeiro(row, ['full_not_available_quantity', 'not_available_quantity'])); }
function totalVariacao(row) { return numero(primeiro(row, ['full_total_quantity', 'total_quantity'])) || estoqueVariacao(row) + indisponivelVariacao(row); }
function vendidosVariacao(row) { return numero(primeiro(row, ['sold_quantity', 'vendidos', 'sales'])); }
function precoVariacao(row, anuncio) { return numero(primeiro(row, ['price', 'preco', 'valor'])) || preco(anuncio); }

function formatarNumero(valor) {
    return Number(valor || 0).toLocaleString('pt-BR', { maximumFractionDigits: 2 });
}

function formatarMoeda(valor) {
    return Number(valor || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function hojeISO() {
    const d = new Date();
    const tz = d.getTimezoneOffset() * 60000;
    return new Date(d.getTime() - tz).toISOString().slice(0, 10);
}

function dataISO(date) {
    const d = new Date(date);
    const tz = d.getTimezoneOffset() * 60000;
    return new Date(d.getTime() - tz).toISOString().slice(0, 10);
}

function somarDiasISO(iso, dias) {
    const d = new Date(`${iso}T12:00:00`);
    d.setDate(d.getDate() + dias);
    return dataISO(d);
}

function labelMesCurto(date) {
    const nomes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
    return `${nomes[date.getMonth()]}/${String(date.getFullYear()).slice(-2)}`;
}

function ultimosSeisMesesFull() {
    const hoje = new Date(`${hojeISO()}T12:00:00`);
    const meses = [];
    for (let i = 5; i >= 0; i--) {
        const inicio = new Date(hoje.getFullYear(), hoje.getMonth() - i, 1, 12, 0, 0);
        const fimMes = new Date(hoje.getFullYear(), hoje.getMonth() - i + 1, 0, 12, 0, 0);
        const fim = i === 0 ? hoje : fimMes;
        const key = `${inicio.getFullYear()}-${String(inicio.getMonth() + 1).padStart(2, '0')}`;
        meses.push({
            key,
            label: labelMesCurto(inicio),
            inicio: dataISO(inicio),
            fim: dataISO(fim)
        });
    }
    return meses;
}

function mesmoDiaMesAnteriorISO(iso) {
    const d = new Date(`${iso}T12:00:00`);
    d.setMonth(d.getMonth() - 1);
    return dataISO(d);
}

function mesmoDiaAnoAnteriorISO(iso) {
    const d = new Date(`${iso}T12:00:00`);
    d.setFullYear(d.getFullYear() - 1);
    return dataISO(d);
}
