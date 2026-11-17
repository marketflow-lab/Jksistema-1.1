(function () {
  'use strict';
  if (window.JKAIModelCatalog) return;

  const labels = { none: 'Sem raciocínio', minimal: 'Mínimo', low: 'Baixo', medium: 'Médio', high: 'Alto', xhigh: 'Muito alto', max: 'Máximo', ultra: 'Ultra' };
  const stripCodex = value => String(value || '').replace(/^codex:/i, '');

  function decorate(option, model) {
    option.dataset.reasoningEfforts = JSON.stringify(Array.isArray(model.reasoning_efforts) ? model.reasoning_efforts : []);
    option.dataset.defaultReasoningEffort = model.default_reasoning_effort || '';
    option.disabled = model.available === false;
    if (model.description) option.title = model.description;
  }

  function fill(select, models, selected, options = {}) {
    if (!select) return;
    const valueOf = model => options.rawCodex ? stripCodex(model.id || model.name) : String(model.name || model.id || '');
    const chosen = String(selected || options.defaultModel || '');
    select.replaceChildren();
    let found = false;
    (models || []).forEach(model => {
      const value = valueOf(model);
      if (!value || (model.available === false && value !== chosen)) return;
      const option = document.createElement('option');
      option.value = value;
      option.textContent = model.display_name || value;
      decorate(option, model);
      select.appendChild(option);
      if (value === chosen) found = true;
    });
    if (chosen && !found) {
      const option = document.createElement('option');
      option.value = chosen;
      option.textContent = `${chosen} (indisponível no momento)`;
      option.disabled = true;
      select.appendChild(option);
      found = true;
    }
    if (found) select.value = chosen;
    else if (options.defaultModel) select.value = options.defaultModel;
  }

  function reasoning(select, modelSelect, preferred, fallback = 'medium') {
    if (!select) return;
    const option = modelSelect?.selectedOptions?.[0];
    let efforts = [];
    try { efforts = JSON.parse(option?.dataset.reasoningEfforts || '[]'); } catch (_) {}
    efforts = Array.isArray(efforts) ? [...new Set(efforts.filter(value => Object.hasOwn(labels, value)))] : [];
    const previous = String(preferred || select.value || fallback);
    select.replaceChildren();
    if (!efforts.length) {
      const saved = document.createElement('option');
      saved.value = previous;
      saved.textContent = 'Não aplicado a este modelo';
      select.appendChild(saved);
      select.disabled = true;
      return previous;
    }
    efforts.forEach(value => {
      const item = document.createElement('option');
      item.value = value;
      item.textContent = labels[value];
      select.appendChild(item);
    });
    const modelDefault = option?.dataset.defaultReasoningEffort || fallback;
    select.value = efforts.includes(previous) ? previous : (efforts.includes(modelDefault) ? modelDefault : efforts[0]);
    select.disabled = false;
    return select.value;
  }

  // One conservative fallback for offline startup; the server supplies the full catalog.
  function fallbackCodex() {
    return [{ id: 'gpt-6.1-sol', name: 'codex:gpt-6.1-sol', display_name: 'GPT-6.1 Sol', reasoning_efforts: ['low', 'medium', 'high', 'xhigh'], default_reasoning_effort: 'medium', available: null }];
  }

  window.JKAIModelCatalog = { decorate, fill, reasoning, fallbackCodex, stripCodex, labels };
})();
