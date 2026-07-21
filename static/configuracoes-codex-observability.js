(function () {
  'use strict';

  const byId = (id) => document.getElementById(id);
  let loading = false;
  let loaded = false;

  function authHeaders() {
    const token = localStorage.getItem('access_token') || localStorage.getItem('token') || '';
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  function publicError(data, fallback) {
    const detail = data && data.detail;
    if (detail && typeof detail === 'object') return detail.message || detail.error_code || fallback;
    return String(detail || data?.message || fallback);
  }

  async function api(path, options = {}) {
    const headers = { ...authHeaders(), ...(options.headers || {}) };
    const response = await fetch(path, { cache: 'no-store', ...options, headers });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(publicError(data, `Falha HTTP ${response.status}`));
    return data;
  }

  function setStatus(id, text, type = '') {
    const element = byId(id);
    if (!element) return;
    element.textContent = text || '';
    element.className = `status${type ? ` ${type}` : ''}`;
  }

  function number(value, digits = 0) {
    return Number(value || 0).toLocaleString('pt-BR', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function percent(value) {
    return `${number(Number(value || 0) * 100, 1)}%`;
  }

  function metric(label, value, explanation) {
    const card = document.createElement('div');
    card.className = 'setting-card';
    const title = document.createElement('h3');
    title.textContent = label;
    const main = document.createElement('div');
    main.style.cssText = 'font-size:1.55rem;font-weight:900;color:#bce9e2;margin:6px 0;';
    main.textContent = value;
    const hint = document.createElement('div');
    hint.className = 'small';
    hint.textContent = explanation;
    card.append(title, main, hint);
    return card;
  }

  function renderCounts(id, values, emptyText) {
    const target = byId(id);
    if (!target) return;
    target.replaceChildren();
    const entries = Object.entries(values || {}).sort((a, b) => Number(b[1]) - Number(a[1]));
    if (!entries.length) {
      target.textContent = emptyText;
      return;
    }
    entries.forEach(([key, value]) => {
      const row = document.createElement('div');
      row.textContent = `${key || 'não informado'}: ${number(value)}`;
      target.appendChild(row);
    });
  }

  function renderSummary(payload, timeseries) {
    const summary = payload?.summary || {};
    const target = byId('codexObsMetrics');
    target?.replaceChildren(
      metric('Consultas registradas', number(summary.events), 'Todas as superfícies internas no período.'),
      metric('Sucesso', percent(summary.success_rate), 'Consultas concluídas sem erro técnico.'),
      metric('Resposta típica', `${number(summary.p50_ms)} ms`, 'Metade das respostas detalhadas ficou abaixo deste tempo.'),
      metric('Resposta lenta', `${number(summary.p95_ms)} ms`, '95% das respostas detalhadas ficou abaixo deste tempo.'),
      metric('Tokens', number(summary.tokens), `Inclui ${number(summary.cached_tokens)} tokens em cache.`),
      metric('Etapas completas', percent(summary.complete_spans_rate), `${number(summary.traces_with_complete_spans)} de ${number(summary.traces_with_expected_spans)} trilhas completas.`),
      metric('Ferramentas consultadas', number(summary.tool_calls), 'Somente quantidade e códigos técnicos; nenhum resultado é armazenado.'),
      metric('Pontos da série', number((timeseries || []).length), 'Intervalos agregados disponíveis para o período.'),
    );
    renderCounts('codexObsModels', summary.by_effective_model, 'Nenhum modelo registrado no período.');
    renderCounts('codexObsErrors', summary.by_error_code, 'Nenhum erro registrado no período.');
  }

  function renderRuns(runs) {
    const target = byId('codexEvalRuns');
    if (!target) return;
    target.replaceChildren();
    if (!Array.isArray(runs) || !runs.length) {
      const empty = document.createElement('div');
      empty.className = 'small';
      empty.textContent = 'Nenhuma avaliação concluída.';
      target.appendChild(empty);
      return;
    }
    runs.slice(0, 20).forEach((run) => {
      const card = document.createElement('div');
      card.className = 'context-hub-finding';
      const title = document.createElement('strong');
      title.textContent = `${run.model || 'modelos'} — ${number(run.mean_score, 1)} pontos`;
      const detail = document.createElement('div');
      detail.className = 'small';
      detail.textContent = `${number(run.passed_cases)}/${number(run.total_cases)} aprovados · ${number(run.critical_failures)} bloqueios críticos · ${run.status || 'sem status'}`;
      card.append(title, detail);
      target.appendChild(card);
    });
  }

  function renderMcp(payload) {
    const rollout = payload?.rollout || {};
    const metrics = payload?.metrics || {};
    if (byId('codexMcpMode')) byId('codexMcpMode').value = rollout.mode || 'off';
    if (byId('codexMcpBaseline')) byId('codexMcpBaseline').value = Number(rollout.baseline_p95_ms || 0);
    if (byId('codexMcpTools')) byId('codexMcpTools').value = (rollout.allowed_tools || []).join(', ');
    if (byId('codexMcpMetrics')) {
      byId('codexMcpMetrics').textContent = `Etapa atual: ${rollout.mode || 'off'} · ${number(metrics.tasks)} tarefas · ${number(metrics.shadow_decisions)} decisões shadow (${number(metrics.shadow_matches)} iguais, ${number(metrics.shadow_divergences)} divergentes) · ${number(metrics.days, 1)} dias · ${number(metrics.protocol_errors)} erros de protocolo · p95 ${number(metrics.observed_p95_ms)} ms.`;
    }
  }

  function queryWindow() {
    const params = new URLSearchParams();
    const from = byId('codexObsFrom')?.value || '';
    const to = byId('codexObsTo')?.value || '';
    if (from) params.set('from_at', `${from}T00:00:00Z`);
    if (to) params.set('to_at', `${to}T23:59:59Z`);
    [
      ['surface', 'codexObsSurface'],
      ['model', 'codexObsModel'],
      ['provider', 'codexObsProvider'],
      ['status', 'codexObsResult'],
    ].forEach(([parameter, elementId]) => {
      const value = byId(elementId)?.value || '';
      if (value) params.set(parameter, value);
    });
    return params.toString();
  }

  async function load(force = false) {
    if (loading || (loaded && !force)) return;
    loading = true;
    setStatus('codexObsStatus', 'Carregando indicadores agregados...', 'loading');
    try {
      const query = queryWindow();
      const suffix = query ? `?${query}` : '';
      const [summary, timeseries, runs, mcp] = await Promise.all([
        api(`/api/admin/codex/telemetry/summary${suffix}`),
        api(`/api/admin/codex/telemetry/timeseries${suffix}${suffix ? '&' : '?'}bucket=day`),
        api('/api/admin/codex/evaluations/runs?limit=20'),
        api('/api/admin/codex/mcp/rollout'),
      ]);
      renderSummary(summary, timeseries.series);
      renderRuns(runs.runs);
      renderMcp(mcp);
      loaded = true;
      setStatus('codexObsStatus', 'Indicadores atualizados.', 'success');
    } catch (error) {
      setStatus('codexObsStatus', error.message || 'Não foi possível carregar os indicadores.', 'error');
    } finally {
      loading = false;
    }
  }

  async function runEvaluation() {
    const models = Array.from(document.querySelectorAll('#codexEvalModels input:checked')).map((item) => item.value);
    if (!models.length) {
      setStatus('codexEvalStatus', 'Selecione pelo menos um modelo.', 'error');
      return;
    }
    setStatus('codexEvalStatus', 'Executando avaliação sem rede...', 'loading');
    try {
      await api('/api/admin/codex/evaluations/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          models,
          repetitions: Math.max(1, Math.min(5, Number(byId('codexEvalRepetitions')?.value || 1))),
          split: byId('codexEvalSplit')?.value || 'holdout',
          purpose: 'manual_admin_panel',
        }),
      });
      const runs = await api('/api/admin/codex/evaluations/runs?limit=20');
      renderRuns(runs.runs);
      setStatus('codexEvalStatus', 'Avaliação concluída e registrada.', 'success');
      loaded = false;
    } catch (error) {
      setStatus('codexEvalStatus', error.message || 'Não foi possível executar a avaliação.', 'error');
    }
  }

  async function saveMcp() {
    const allowedTools = String(byId('codexMcpTools')?.value || '')
      .split(',').map((item) => item.trim()).filter(Boolean);
    if (!window.confirm('Aplicar esta etapa do MCP interno? O servidor bloqueará avanços sem evidência suficiente.')) return;
    setStatus('codexMcpStatus', 'Validando a etapa no servidor...', 'loading');
    try {
      const payload = await api('/api/admin/codex/mcp/rollout', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: byId('codexMcpMode')?.value || 'off',
          allowed_tools: allowedTools,
          baseline_p95_ms: Math.max(0, Number(byId('codexMcpBaseline')?.value || 0)),
        }),
      });
      renderMcp(payload.automatic_rollback ? { rollout: payload.automatic_rollback.rollout, metrics: {} } : payload);
      const current = await api('/api/admin/codex/mcp/rollout');
      renderMcp(current);
      setStatus('codexMcpStatus', payload.automatic_rollback ? 'O MCP foi desligado automaticamente por segurança.' : 'Etapa aplicada.', payload.automatic_rollback ? 'error' : 'success');
    } catch (error) {
      setStatus('codexMcpStatus', error.message || 'A etapa não pôde ser aplicada.', 'error');
    }
  }

  byId('codexObsRefresh')?.addEventListener('click', () => load(true));
  byId('codexEvalRefresh')?.addEventListener('click', async () => {
    try { renderRuns((await api('/api/admin/codex/evaluations/runs?limit=20')).runs); }
    catch (error) { setStatus('codexEvalStatus', error.message, 'error'); }
  });
  byId('codexEvalRun')?.addEventListener('click', runEvaluation);
  byId('codexMcpSave')?.addEventListener('click', saveMcp);
  window.jkCodexObservabilityLoad = load;
})();
