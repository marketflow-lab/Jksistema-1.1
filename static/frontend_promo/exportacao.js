async function exportarAnaliseApi(idx, dadosOverride) {
    const analise = Array.isArray(apiAnalisesPorCampanha) ? apiAnalisesPorCampanha[idx] : null;
    if (!analise) { alert("AnÃ¡lise nÃ£o encontrada."); return; }
    const arquivo_nome = analise.arquivo_nome || `analise_promo_${idx + 1}.xlsx`;
    const fonte = dadosOverride || analise.data || [];
    if (!Array.isArray(fonte) || fonte.length === 0) {
        alert('Esta análise não tem anúncios para exportar. Consulte o resultado da campanha.');
        return;
    }
    const linhasExport = fonte.map((r) => {
        const out = Object.assign({}, r);
        out['AÃ§Ã£o'] = r['AÃ§Ã£o'] || r['Participar ou nÃ£o'] || '';
        out['Participar ou nÃ£o'] = out['AÃ§Ã£o'];
        return out;
    });
    const formData = new FormData();
    formData.append('dados', JSON.stringify(linhasExport));
    formData.append('arquivo_nome', arquivo_nome);
    const jobIdExport = String(analise.job_id || apiAnaliseJobId || '').trim();
    if (jobIdExport) {
        formData.append('job_id', jobIdExport);
    }
    const arquivoOriginal = uploadedFilesMap[arquivo_nome] || uploadedFilesMap[String(arquivo_nome).toLowerCase()];
    if (arquivoOriginal) {
        formData.append('file', arquivoOriginal, arquivoOriginal.name);
    }
    try {
        const response = await fetch('/api/promo/exportar-api', {
            method: 'POST',
            headers: getAuthHeadersWithClient(),
            body: formData
        });
        if (!response.ok) {
            const erro = await response.json().catch(() => ({}));
            throw new Error(erro.detail || "Erro ao gerar arquivo.");
        }
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = arquivoOriginal
            ? arquivoOriginal.name
            : (arquivo_nome.endsWith('.xlsx') ? arquivo_nome : arquivo_nome.replace(/\.[^.]+$/, '') + '.xlsx');
        document.body.appendChild(a);
        a.click();
        a.remove();
    } catch (e) {
        alert("Erro na exportaÃ§Ã£o: " + e.message);
    }
}

async function exportarPlanilha() {
    // Modo API: exporta dados atuais da aba ativa com as decisÃµes editadas na tela
    if (Array.isArray(apiAnalisesPorCampanha) && apiAnalisesPorCampanha.length > 0) {
        await exportarAnaliseApi(apiAnaliseAtiva, currentData);
        return;
    }

    if (!mlFileName && planilhaGeradaAtual) {
        window.open(`/api/promo/planilha/${encodeURIComponent(planilhaGeradaAtual)}`, '_blank');
        return;
    }

    if (!mlFileName || !uploadedFilesMap[mlFileName]) {
        alert("Arquivo original ML nÃ£o identificado. Gere a anÃ¡lise novamente para exportar.");
        return;
    }

    const fileToExport = uploadedFilesMap[mlFileName];
    const linhasAnalise = currentData.map((r) => {
        const rowOut = {};
        tableColumns.forEach((col) => {
            rowOut[col.key] = r[col.key] ?? '';
        });
        rowOut['Participar ou nÃ£o'] = (r['AÃ§Ã£o'] || r['Participar ou nÃ£o'] || 'Participar');
        return rowOut;
    });

    const formData = new FormData();
    formData.append('file', fileToExport);
    formData.append('decisoes', JSON.stringify(linhasAnalise));

    try {
        const headers = (typeof obterAuthHeaders === 'function')
            ? obterAuthHeaders()
            : (() => {
                const token = localStorage.getItem('access_token');
                return token ? { 'Authorization': `Bearer ${token}` } : {};
            })();

        const response = await fetch('/api/promo/exportar', {
            method: 'POST',
            headers,
            body: formData
        });

        if (!response.ok) throw new Error("Erro ao gerar arquivo.");

        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${mlFileName}`;
        document.body.appendChild(a);
        a.click();
        a.remove();
    } catch (e) {
        alert("Erro na exportaÃ§Ã£o: " + e.message);
    }
}

function copiarDecisoes() {
    const decisoes = currentData.map(r => (r['AÃ§Ã£o'] || r['Participar ou nÃ£o'] || 'Participar'));
    const textToCopy = decisoes.join("\n");
    navigator.clipboard.writeText(textToCopy).then(() => {
        alert("DecisÃµes copiadas para a Ã¡rea de transferÃªncia!");
    }).catch(err => {
        alert("Erro ao copiar: " + err);
    });
}

