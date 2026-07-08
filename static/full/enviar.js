function setTabelaMensagem(texto) {
    tbody.innerHTML = `<tr><td colspan="11">${escapeHtml(texto)}</td></tr>`;
}

function renderEnviarFull(lista = enviarFullDados) {
    if (!fullSendAccountSummaryEl || !fullSendTableBodyEl) return;
    const rows = Array.isArray(lista) ? lista : [];
    if (!enviarFullContaSelecionada) {
        fullSendAccountSummaryEl.textContent = 'Nenhuma conta selecionada.';
        fullSendTableBodyEl.innerHTML = '<tr><td colspan="8">Selecione uma conta para carregar os itens Full.</td></tr>';
        return;
    }
    const totalEstoque = rows.reduce((acc, row) => acc + estoqueFull(row), 0);
    const totalGeral = rows.reduce((acc, row) => acc + totalVariacao(row), 0);
    fullSendAccountSummaryEl.textContent = `${enviarFullContaSelecionada}: ${rows.length} item(ns), estoque disponivel ${formatarNumero(totalEstoque)}, total Full ${formatarNumero(totalGeral)}.`;
    if (!rows.length) {
        fullSendTableBodyEl.innerHTML = '<tr><td colspan="8">Nenhum item Full carregado para esta conta.</td></tr>';
        return;
    }
    fullSendTableBodyEl.innerHTML = rows.map(row => `
        <tr>
            <td class="sku">${escapeHtml(idAnuncio(row) || '-')}</td>
            <td class="sku">${escapeHtml(sku(row) || '-')}</td>
            <td class="title">${escapeHtml(titulo(row))}</td>
            <td class="num">${formatarNumero(estoqueFull(row))}</td>
            <td class="num">${formatarNumero(indisponivelVariacao(row))}</td>
            <td class="num">${formatarNumero(totalVariacao(row))}</td>
            <td class="sku">${escapeHtml(primeiro(row, ['inventory_id']) || '-')}</td>
            <td>${linkAnuncio(row) ? `<a class="link" href="${escapeHtml(linkAnuncio(row))}" target="_blank" rel="noopener">Abrir</a>` : '-'}</td>
        </tr>
    `).join('');
}

async function carregarEnviarFull(force = false) {
    const conta = String(enviarFullContaSelecionada || '').trim();
    if (!conta) {
        enviarFullDados = [];
        renderEnviarFull([]);
        setSendStatus('Selecione uma conta para carregar os itens Full.', 'empty');
        return;
    }
    if (btnAplicarEnviarFull) btnAplicarEnviarFull.disabled = true;
    setSendStatus(`Carregando itens Full de ${conta}...`);
    renderEnviarFull(enviarFullDados);
    try {
        enviarFullDados = await buscarAnunciosFullLoja(conta, force);
        renderEnviarFull(enviarFullDados);
        setSendStatus(`${enviarFullDados.length} item(ns) Full carregado(s) para ${conta}.`);
    } catch (e) {
        enviarFullDados = [];
        renderEnviarFull([]);
        setSendStatus(e && e.message ? `Erro ao carregar Enviar Full: ${e.message}` : 'Erro ao carregar Enviar Full.', 'error');
    } finally {
        if (btnAplicarEnviarFull) btnAplicarEnviarFull.disabled = false;
    }
}
