'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const canonical = fs.readFileSync(path.join(root, 'static', 'configuracoes.html'), 'utf8');
const mirror = fs.readFileSync(path.join(root, 'configuracoes.html'), 'utf8');

assert.strictEqual(mirror, canonical, 'os espelhos da tela de configurações divergiram');

for (const id of ['iaModeloPerguntas', 'iaModeloPosVenda', 'iaRaciocinioPerguntas', 'iaRaciocinioPosVenda']) {
    assert(canonical.includes(`id="${id}"`), `controle ausente: ${id}`);
}

for (const key of ['padrao', 'perguntas', 'pos_venda', 'chat', 'favoritos']) {
    assert(canonical.includes(`ia_raciocinio_${key}:`), `persistência ausente: ${key}`);
}
for (const suffix of ['Padrao', 'Perguntas', 'PosVenda', 'Chat', 'Favoritos']) {
    assert(canonical.includes(`id="iaRaciocinio${suffix}"`), `controle ausente: ${suffix}`);
}
for (const provider of ['vertex', 'openai', 'deepseek', 'gemini']) {
    assert(canonical.includes(`defaults.${provider}_ativa === true`), `provedor inativo não está filtrado: ${provider}`);
}
assert(canonical.includes("{ label: 'Codex', itens: data.codex || [] }"));
assert(!/<option value="(?:codex:)?gpt-5/.test(canonical), 'modelos GPT antigos não devem ficar fixos no markup');

class Element {
    constructor(tag) { this.tagName = tag; this.dataset = {}; this.children = []; this._value = ''; this.disabled = false; }
    appendChild(item) { this.children.push(item); if (!this._value && !item.disabled) this._value = item.value; return item; }
    replaceChildren() { this.children = []; this._value = ''; }
    get options() { return this.children; }
    get selectedOptions() { return this.children.filter(item => item.value === this.value); }
    set value(value) { this._value = this.tagName === 'option' || this.children.some(item => item.value === value) ? value : ''; }
    get value() { return this._value; }
}
const sandbox = { window: {}, document: { createElement: tag => new Element(tag) } };
vm.runInNewContext(fs.readFileSync(path.join(root, 'static/ia-model-catalog.js'), 'utf8'), sandbox);
const catalog = sandbox.window.JKAIModelCatalog;
const models = [
    { id: 'gpt-6.1-sol', name: 'codex:gpt-6.1-sol', reasoning_efforts: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'], default_reasoning_effort: 'high', available: true },
    { id: 'gpt-6-luna', name: 'codex:gpt-6-luna', reasoning_efforts: ['none', 'minimal', 'low'], default_reasoning_effort: 'low', available: null },
    { id: 'gpt-hidden', name: 'codex:gpt-hidden', reasoning_efforts: ['low'], available: false },
];
const model = new Element('select');
const effort = new Element('select');
catalog.fill(model, models, 'gpt-6.1-sol', { rawCodex: true });
assert.deepStrictEqual(model.options.map(item => item.value), ['gpt-6.1-sol', 'gpt-6-luna']);
assert.strictEqual(catalog.reasoning(effort, model, 'ultra'), 'ultra');
assert(!effort.options.some(item => ['none', 'minimal'].includes(item.value)), 'Sol não deve aceitar níveis ausentes das capacidades');
model.value = 'gpt-6-luna';
assert.strictEqual(catalog.reasoning(effort, model, 'ultra'), 'low', 'troca de modelo deve ajustar nível incompatível');
assert.deepStrictEqual(effort.options.map(item => item.value), ['none', 'minimal', 'low']);
assert.strictEqual(catalog.reasoning(effort, model, 'none'), 'none');
catalog.fill(model, models, 'gpt-hidden', { rawCodex: true });
assert.strictEqual(model.value, 'gpt-hidden');
assert(model.selectedOptions[0].disabled, 'modelo salvo indisponível deve ser preservado e identificado');
catalog.fill(model, [{ name: 'gpt-api-current', reasoning_efforts: ['medium', 'high'] }, ...models], 'gpt-api-current');
assert.strictEqual(model.value, 'gpt-api-current');
assert(model.options.some(item => item.value === 'codex:gpt-6.1-sol'), 'modelos Codex e API devem manter identidade separada');
catalog.fill(model, [{ name: 'vertex:gemini-2.5-flash' }], 'vertex:gemini-2.5-flash');
assert.strictEqual(catalog.reasoning(effort, model, 'max'), 'max');
assert(effort.disabled, 'provedor sem capacidade de raciocínio deve preservar a preferência sem aplicá-la');

const scripts = [...canonical.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map(match => match[1])
    .filter(source => source.trim());
scripts.forEach((source, index) => {
    assert.doesNotThrow(() => new Function(source), `script inline ${index + 1} possui erro de sintaxe`);
});

console.log('configuracoes_ia_modelo_nivel_frontend: ok');
