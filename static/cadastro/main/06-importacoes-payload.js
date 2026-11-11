(function (global) {
    'use strict';
    const cadastro = global.JKCadastro;
    if (!cadastro) throw new Error('Cadastro não inicializado.');
    if (cadastro.importacoesCatalogosPayload) return;

    function texto(value, limit = 500) {
        if (value === null || value === undefined) return ''; let result;
        if (typeof value === 'object') { try { result = JSON.stringify(value); } catch (_error) { result = ''; } }
        else result = String(value);
        return result.length > limit ? `${result.slice(0, limit)}…` : result;
    }
    function statusPayload(payload) { return String(payload && (payload.status || payload.estado) || '').trim().toLowerCase(); }
    function erroPayload(payload, fallback) {
        const detail = payload && (payload.detail || payload.error); const message = payload && (payload.message || payload.mensagem);
        if (typeof detail === 'string' && detail.trim()) return detail.trim();
        if (detail && typeof detail === 'object') { const message = detail.message || detail.mensagem || detail.detail;
            if (message) return texto(message, 300); }
        return message ? texto(message, 300) : fallback;
    }

    function numeroResumo(value) { const parsed = Number(value); return Number.isFinite(parsed) && parsed > 0 ? Math.trunc(parsed) : 0; }
    function resumoPayload(payload) {
        const summary = payload && (payload.summary || payload.resumo) || {};
        const conflicts = Array.isArray(payload && payload.conflicts) ? payload.conflicts.length : 0;
        const ignored = Array.isArray(payload && (payload.ignored || payload.ignorados)) ? (payload.ignored || payload.ignorados).length : 0;
        return {
            encontrados: numeroResumo(summary.encontrados ?? summary.found),
            novos: numeroResumo(summary.novos ?? summary.new), preencher: numeroResumo(summary.preencher ?? summary.campos_preencher),
            inalterados: numeroResumo(summary.inalterados ?? summary.unchanged), conflitos: numeroResumo(summary.conflitos ?? summary.conflicts ?? conflicts),
            ignorados: Math.max(numeroResumo(payload && payload.ignored_total), numeroResumo(summary.ignorados ?? summary.ignored), ignored),
            aplicaveis: summary.aplicaveis == null && summary.applicable == null ? numeroResumo(summary.novos ?? summary.new) + numeroResumo(summary.preencher ?? summary.campos_preencher) : numeroResumo(summary.aplicaveis ?? summary.applicable),
            fotosPlanejadas: numeroResumo(summary.fotos_planejadas ?? summary.planned_photos),
        };
    }
    function progressoPayload(payload, status) {
        const progress = payload && (payload.progress || payload.progresso) || {}; const raw = progress.percent ?? progress.percentual ?? payload.progress_percent;
        if (raw === null || raw === undefined || raw === '') return status === 'ready' ? 100 : null;
        let value = Number(raw); if (!Number.isFinite(value)) return status === 'ready' ? 100 : null; if (value > 0 && value <= 1) value *= 100;
        const maximum = status === 'applying' ? 99 : 100;
        return Math.max(0, Math.min(maximum, value));
    }
    function mensagemProgresso(payload) { const progress = payload && (payload.progress || payload.progresso) || {}; return String(progress.message || progress.mensagem || '').trim().slice(0, 300); }
    function warningsUnicos(value) {
        const values = Array.isArray(value) ? value : value === null || value === undefined ? [] : [value];
        return [...new Set(values.map(item => texto(item, 300).trim()).filter(Boolean))];
    }
    function totalWarnings(payload) {
        const items = payload && Array.isArray(payload.items) ? payload.items : [];
        return warningsUnicos(payload && payload.warnings).length + items.reduce((total, item) => total + warningsUnicos(item && item.warnings).length, 0);
    }
    const IGNORED_REASONS = Object.freeze({
        missing_sku: 'SKU não informado no anúncio', sku_missing: 'SKU não informado no anúncio',
        sku_identity_not_confirmed: 'Identificação do SKU não confirmada',
        sku_ambiguous: 'Mais de um SKU possível para o anúncio',
        sku_duplicate_within_listing: 'SKU repetido entre variações do mesmo anúncio',
        user_product_sku_conflict: 'SKU do produto diverge do SKU da variação',
        listing_details_missing: 'Não foi possível consultar os dados do anúncio',
        listing_detail_incomplete: 'Dados do anúncio incompletos',
        variation_details_incomplete: 'Dados das variações incompletos',
        seller_mismatch: 'Anúncio pertence a outro vendedor',
        partial_new_sku_not_applied: 'Novo SKU ficou de fora desta consulta parcial',
        partial_non_store_sku_not_applied: 'SKU sem cadastro confirmado nesta loja ou no cadastro legado',
        sku_excluido: 'SKU excluído do cadastro',
        photo_sku_binding_changed: 'Vínculo do SKU mudou; consulte o catálogo novamente',
        photo_already_saved: 'A foto deste SKU já foi salva',
    });
    function motivoIgnorado(reason) {
        return IGNORED_REASONS[String(reason || '').trim().toLowerCase()] || 'Não foi possível importar este item com segurança';
    }
    function resumoMotivosIgnorados(payload) {
        const counts = payload && payload.ignored_reason_counts;
        if (!counts || typeof counts !== 'object' || Array.isArray(counts)) return '';
        const reasons = Object.entries(counts).filter(([, count]) => numeroResumo(count) > 0)
            .map(([reason, count]) => `${numeroResumo(count)}: ${motivoIgnorado(reason).toLowerCase()}`);
        return reasons.length ? ` Motivos dos itens ignorados: ${reasons.join('; ')}.` : '';
    }
    function mudancaFotoAplicavel(item) {
        return Array.isArray(item && item.changes) && item.changes.some(change =>
            ['foto', 'foto_data_url', 'foto_principal', 'photo'].includes(String(change && (change.field || change.campo) || '').toLowerCase())
            && !['preserve', 'preservar', 'ignored', 'ignorado', 'skip'].includes(String(change && (change.action || change.acao) || '').toLowerCase()));
    }
    function linhasPayload(payload) {
        const raw = payload && (payload.items || payload.preview || payload.diferencas || payload.produtos);
        const rows = [];
        const ignored = payload && (payload.ignored || payload.ignorados);
        (Array.isArray(ignored) ? ignored : []).forEach(item => {
            if (!item || typeof item !== 'object') return;
            const external = item.external_ids && typeof item.external_ids === 'object' ? item.external_ids : {};
            const identifier = item.sku || item.mlb || external.id_bling || external.mlb;
            const details = [motivoIgnorado(item.reason || item.motivo)];
            if (Array.isArray(item.missing_fields)) details.push(`campos: ${item.missing_fields.join(', ')}`);
            rows.push({ sku: identifier, action: 'ignorado', field: item.variation_id || item.entity,
                current: item.mlb && item.mlb !== identifier ? item.mlb : '', incoming: Array.isArray(item.candidates) ? item.candidates.join(', ') : '', note: details.filter(Boolean).join(' | ') });
        });
        if (payload && payload.ignored_truncated) rows.push({ action: 'ignorados limitados',
            note: `Exibindo ${Array.isArray(ignored) ? ignored.length : 0} de ${numeroResumo(payload.ignored_total)} itens ignorados.` });
        (Array.isArray(raw) ? raw : []).forEach(item => {
            if (!item || typeof item !== 'object') return;
            const changes = Array.isArray(item.changes) ? item.changes : [];
            const itemWarnings = warningsUnicos(item.warnings).join(', ');
            if (!changes.length) {
                rows.push({
                    sku: item.sku, action: item.action || item.acao || item.status, field: item.field || item.campo,
                    current: item.current ?? item.valor_atual, incoming: item.incoming ?? item.valor_novo,
                    note: [item.note || item.observacao || item.message, itemWarnings].filter(Boolean).join(' | '),
                });
                return;
            }
            changes.forEach(change => rows.push({
                sku: item.sku, action: change.action || change.acao || item.status, field: change.field || change.campo,
                current: change.current ?? change.valor_atual, incoming: change.incoming ?? change.valor_novo,
                note: [change.note || change.observacao, itemWarnings].filter(Boolean).join(' | '),
            }));
        });
        return rows;
    }

    function conflitosPayload(payload) {
        const explicit = payload && (payload.conflicts || payload.conflitos); if (Array.isArray(explicit)) return explicit;
        const items = payload && payload.items; return Array.isArray(items)
            ? items.filter(item => item && (item.status === 'conflito' || numeroResumo(item.conflicts) > 0)) : [];
    }
    function detalhesTecnicosConflito(item) {
        if (typeof item === 'string') return texto(item, 1000); if (!item || typeof item !== 'object') return '';
        return warningsUnicos([...(Array.isArray(item.conflicts) ? item.conflicts : []), item.message || item.mensagem || item.reason || item.motivo || item.campo || item.status]).join(', ');
    }

    function coberturaGeralPayload(payload) { return payload && (payload.coverage_complete ?? payload.cobertura_completa); }
    function plural(value, singular, pluralValue) { return `${value} ${value === 1 ? singular : pluralValue}`; }

    function progressoDetalhadoAplicacao(payload) {
        const progress = payload && (payload.progress || payload.progresso) || {};
        const stage = String(progress.stage || progress.etapa || '').toLowerCase();
        const current = numeroResumo(progress.current ?? progress.atual);
        const total = numeroResumo(progress.total);
        if (stage === 'photos' && total > 0) return `Baixando fotos do Mercado Livre: ${current} de ${total} processadas.`;
        if (stage === 'saving') return 'Fotos processadas. Salvando as informações no Cadastro...';
        return mensagemProgresso(payload) || 'Preparando informações e fotos para salvar no Cadastro...';
    }
    function resultadoFotosAplicadas(applyResult) {
        const keys = ['fotos_planejadas', 'fotos_salvas', 'fotos_ignoradas'];
        if (!applyResult || !keys.some(key => Object.prototype.hasOwnProperty.call(applyResult, key))) return '';
        const salvas = numeroResumo(applyResult.fotos_salvas);
        const ignoradas = numeroResumo(applyResult.fotos_ignoradas);
        const planejadas = Math.max(numeroResumo(applyResult.fotos_planejadas), salvas + ignoradas);
        if (planejadas === 0) return 'Fotos do Mercado Livre: nenhuma foto nova precisava ser baixada.';
        return `Fotos do Mercado Livre: ${salvas} de ${planejadas} foram baixadas e salvas; ${plural(ignoradas, 'não pôde ser baixada e salva', 'não puderam ser baixadas e salvas')}.`;
    }
    function resultadoCamposAplicados(result) {
        if (!result || !['incluidos', 'atualizados'].some(key => Object.prototype.hasOwnProperty.call(result, key))) return '';
        return `Cadastros: ${plural(numeroResumo(result.incluidos), 'incluído', 'incluídos')} e ${plural(numeroResumo(result.atualizados), 'atualizado', 'atualizados')}. `;
    }
    function detalhesFotosPendentes(result) {
        const failed = Array.isArray(result && result.photos_failed) ? result.photos_failed : [];
        const reasons = { photo_candidate_invalid: 'não foi possível validar a foto do anúncio',
            photo_budget_exceeded: 'o limite de processamento das fotos foi atingido', download_failed: 'não foi possível baixar a foto', photo_download_failed: 'não foi possível baixar a foto',
            invalid_image: 'a imagem recebida não pôde ser utilizada', photo_processing_failed: 'a imagem não pôde ser processada',
            photo_save_failed: 'não foi possível salvar a foto', sku_identity_not_confirmed: 'a identificação do SKU precisa ser conferida',
            photo_candidate_not_confirmed: 'não foi possível confirmar uma foto para este SKU' };
        const labels = failed.filter(item => item && item.sku).slice(0, 50)
            .map(item => `SKU ${texto(item.sku, 100)}: ${reasons[String(item.reason || '').toLowerCase()] || 'não foi possível baixar e salvar a foto'}`);
        if (!labels.length) return '';
        const remaining = Math.max(0, failed.length - labels.length);
        return ` Fotos pendentes: ${labels.join('; ')}.${remaining ? ` Outros ${remaining} SKUs constam no resultado.` : ''}`;
    }

    cadastro.importacoesCatalogosPayload = Object.freeze({
        texto, statusPayload, erroPayload, numeroResumo, resumoPayload, progressoPayload, mensagemProgresso,
        totalWarnings, resumoMotivosIgnorados, mudancaFotoAplicavel, linhasPayload, conflitosPayload,
        detalhesTecnicosConflito, coberturaGeralPayload, plural, progressoDetalhadoAplicacao,
        resultadoFotosAplicadas, resultadoCamposAplicados, detalhesFotosPendentes,
    });
})(window);
