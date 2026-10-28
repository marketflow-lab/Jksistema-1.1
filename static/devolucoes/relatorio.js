/* Shared, local-only return report. Values from providers are rendered as text. */
(function (global) {
    'use strict';
    const labels = {
        not_identified: 'Motivo ainda não identificado',
        not_informed: 'Não informado pela origem',
        not_linked: 'Sem vínculo confirmado',
        ambiguous: 'Vínculo ambíguo',
        unavailable: 'Consulta indisponível',
        reconnect_required: 'Reconexão necessária'
    };
    const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;
    const text = value => String(value == null ? '' : value).trim().replace(/\s+/g, ' ');
    const escapeHtml = value => String(value == null ? '' : value).replace(/[&<>"']/g,
        char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    const formatarQuantidade = value => number(value).toLocaleString('pt-BR', {maximumFractionDigits: 2});
    const money = value => number(value).toLocaleString('pt-BR', {style: 'currency', currency: 'BRL'});
    const percent = value => number(value).toLocaleString('pt-BR', {maximumFractionDigits: 1}) + '%';
    const count = item => item.devolucoes_contribuicao == null ? 1 : Math.max(0, number(item.devolucoes_contribuicao));

    function motivo(item) {
        const raw = item && item.motivo_devolucao || {};
        const descricao = text(raw.descricao), codigo = text(raw.codigo);
        const identificado = raw.status === 'identified' && Boolean(descricao || codigo);
        const status = identificado ? 'identified' : Object.hasOwn(labels, raw.status) ? raw.status : 'not_identified';
        const titulo = identificado ? descricao || 'Código ' + codigo : labels[status];
        return {
            status, identificado, titulo, codigo,
            fonte: raw.fonte === 'mercado_livre' ? 'Mercado Livre' : raw.fonte === 'bling' ? 'Bling' : '',
            chave: status + ':' + titulo.toLocaleLowerCase('pt-BR')
        };
    }

    function resumir(itens) {
        return (Array.isArray(itens) ? itens : []).reduce((total, item) => {
            const ocorrencias = count(item);
            total.devolucoes += ocorrencias;
            total.quantidade += number(item.quantidade);
            total.valor += number(item.valor_total);
            if (motivo(item).identificado) total.identificados += ocorrencias;
            return total;
        }, {devolucoes: 0, quantidade: 0, valor: 0, identificados: 0});
    }

    function motivos(itens) {
        const groups = new Map();
        for (const item of Array.isArray(itens) ? itens : []) {
            const reason = motivo(item);
            if (!groups.has(reason.chave)) groups.set(reason.chave, {...reason, devolucoes: 0, quantidade: 0, valor: 0});
            const group = groups.get(reason.chave);
            group.devolucoes += count(item);
            group.quantidade += number(item.quantidade);
            group.valor += number(item.valor_total);
        }
        return [...groups.values()].sort((a, b) => b.devolucoes - a.devolucoes || b.valor - a.valor || a.titulo.localeCompare(b.titulo, 'pt-BR'));
    }

    function cobertura(itens) {
        const total = resumir(itens);
        if (!total.devolucoes) return 'Nenhuma devolução com ocorrência comercial neste período.';
        return formatarQuantidade(total.identificados) + ' de ' + formatarQuantidade(total.devolucoes)
            + ' devoluções com motivo identificado (' + percent(total.identificados / total.devolucoes * 100) + ').';
    }

    function tabelaMotivos(itens) {
        const groups = motivos(itens), total = resumir(itens);
        if (!groups.length) return '<p class="empty-report">Nenhuma devolução encontrada no período.</p>';
        return '<div class="reason-scroll"><table class="reason-table"><thead><tr><th>Motivo</th>'
            + '<th class="numeric">Devoluções</th><th>Participação</th><th class="numeric">Valor devolvido</th></tr></thead><tbody>'
            + groups.map(group => {
                const share = total.devolucoes ? group.devolucoes / total.devolucoes * 100 : 0;
                return '<tr class="' + (group.identificado ? 'reason-identified' : 'reason-unknown') + '">'
                    + '<td><span class="reason-name">' + escapeHtml(group.titulo) + '</span></td>'
                    + '<td class="numeric">' + formatarQuantidade(group.devolucoes) + '</td>'
                    + '<td><div class="participation"><span class="bar-track" aria-hidden="true"><span style="width:'
                    + Math.max(0, Math.min(100, share)) + '%"></span></span><span>' + (total.devolucoes ? percent(share) : '—') + '</span></div></td>'
                    + '<td class="numeric">' + money(group.valor) + '</td></tr>';
            }).join('') + '</tbody></table></div>';
    }

    function motivoRegistro(item) {
        const reason = motivo(item);
        return '<span class="record-reason">' + escapeHtml(reason.titulo) + '</span>'
            + (reason.identificado && reason.fonte ? '<small class="muted">' + escapeHtml(reason.fonte) + '</small>' : '');
    }

    global.JKDevolucoesRelatorio = Object.freeze({
        resumir, motivos, motivo, cobertura, tabelaMotivos, motivoRegistro, escapeHtml, formatarQuantidade
    });
})(window);
