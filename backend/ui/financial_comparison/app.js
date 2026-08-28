(() => {
  'use strict';

  const form = document.getElementById('comparison-form');
  const storeInput = document.getElementById('store');
  const loadButton = document.getElementById('load');
  const status = document.getElementById('status');
  const samplesBody = document.getElementById('samples');
  const quoteForm = document.getElementById('quote-form');
  const quoteButton = document.getElementById('quote-load');
  const quoteStatus = document.getElementById('quote-status');

  const setText = (id, value) => {
    document.getElementById(id).textContent = String(value);
  };

  const money = (value) => {
    if (value === null || value === undefined || value === '') return '—';
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return '—';
    return parsed.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
  };

  const percent = (value) => {
    if (value === null || value === undefined || value === '') return '—';
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return '—';
    return `${parsed.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 6 })}%`;
  };

  const dateTime = (value) => {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('pt-BR');
  };

  const cell = (value, className = '') => {
    const td = document.createElement('td');
    td.textContent = value;
    if (className) td.className = className;
    return td;
  };

  const renderSamples = (samples) => {
    samplesBody.replaceChildren();
    if (!Array.isArray(samples) || samples.length === 0) {
      const row = document.createElement('tr');
      const empty = cell('Ainda não há observações para esta loja.', 'empty');
      empty.colSpan = 12;
      row.appendChild(empty);
      samplesBody.appendChild(row);
      return;
    }
    samples.forEach((sample) => {
      const row = document.createElement('tr');
      const legacy = sample.legacy || {};
      const quote = sample.quote || {};
      const delta = sample.delta || {};
      const inputs = sample.inputs || {};
      row.append(
        cell(dateTime(sample.captured_at)),
        cell(sample.origin || '—'),
        cell(sample.classification || '—', `classification ${sample.classification || ''}`),
        cell(money(inputs.effective_price)),
        cell(money(inputs.sale_fee)),
        cell(money(inputs.seller_shipping)),
        cell(money(legacy.net_amount)),
        cell(money(quote.net_amount)),
        cell(money(delta.net_amount)),
        cell(percent(legacy.margin_percent)),
        cell(percent(quote.margin_percent)),
        cell(percent(delta.margin_percent))
      );
      samplesBody.appendChild(row);
    });
  };

  const renderSummary = (payload) => {
    const classifications = payload.classifications || {};
    const differences = ['rounding_difference', 'exactness_difference', 'value_difference', 'shadow_error']
      .reduce((sum, key) => sum + Number(classifications[key] || 0), 0);
    setText('total', payload.total || 0);
    setText('matches', classifications.match || 0);
    setText('differences', differences);
    setText('incomplete', classifications.shadow_incomplete || 0);
    renderSamples(payload.samples || []);
  };

  const authHeaders = () => {
    const token = window.localStorage.getItem('access_token') || '';
    return token ? { Authorization: `Bearer ${token}` } : {};
  };

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const store = storeInput.value.trim();
    if (!store) return;
    loadButton.disabled = true;
    status.className = '';
    status.textContent = 'Carregando observações efêmeras…';
    try {
      const response = await fetch('/api/internal/financial-comparison/summary', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ loja: store, limit: 100 }),
        credentials: 'same-origin',
        cache: 'no-store'
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || `Falha HTTP ${response.status}`);
      }
      renderSummary(payload);
      status.textContent = payload.total
        ? `${payload.total} observação(ões) carregada(s).`
        : 'Nenhuma observação nesta janela. Abra Promoções ou Favoritos com o piloto habilitado para gerar amostras.';
    } catch (error) {
      status.className = 'error';
      status.textContent = error instanceof Error ? error.message : 'Falha ao carregar comparações.';
    } finally {
      loadButton.disabled = false;
    }
  });

  quoteForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const values = {
      loja: document.getElementById('quote-store').value.trim(),
      item_id: document.getElementById('quote-item').value.trim().toUpperCase(),
      preco: document.getElementById('quote-price').value,
      custo: document.getElementById('quote-cost').value,
      imposto_percentual: document.getElementById('quote-tax').value
    };
    quoteButton.disabled = true;
    quoteStatus.className = '';
    quoteStatus.textContent = 'Consultando tarifa e frete para o preço candidato…';
    try {
      const response = await fetch('/api/internal/financial-comparison/candidate-quote', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(values),
        credentials: 'same-origin',
        cache: 'no-store'
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || `Falha HTTP ${response.status}`);
      }
      const components = payload.components || {};
      const statusLabels = { exact: 'Exata', incomplete: 'Incompleta', invalid: 'Inválida' };
      const issues = [...(payload.missing || []), ...(payload.conflicts || [])];
      setText('quote-state', statusLabels[payload.status] || 'Indeterminada');
      setText('quote-fee', money((components.sale_fee || {}).amount));
      setText('quote-shipping', money((components.seller_shipping || {}).amount));
      setText('quote-net', money(payload.net_amount));
      setText('quote-margin', percent(payload.margin_percent));
      if (payload.decision_eligible) {
        quoteStatus.textContent = 'Cotação completa para análise consultiva.';
      } else {
        const reason = issues.length ? ` Motivos: ${issues.join(', ')}.` : '';
        quoteStatus.textContent = `${payload.status === 'invalid' ? 'Cotação inválida' : 'Cotação incompleta'}: nenhum valor deve ser usado para decisão automática.${reason}`;
      }
    } catch (error) {
      quoteStatus.className = 'error';
      quoteStatus.textContent = error instanceof Error ? error.message : 'Falha ao recotar o preço candidato.';
      ['quote-state', 'quote-fee', 'quote-shipping', 'quote-net', 'quote-margin'].forEach((id) => setText(id, '—'));
    } finally {
      quoteButton.disabled = false;
    }
  });
})();
