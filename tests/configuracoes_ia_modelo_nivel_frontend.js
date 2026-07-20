'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const canonical = fs.readFileSync(path.join(root, 'static', 'configuracoes.html'), 'utf8');
const mirror = fs.readFileSync(path.join(root, 'configuracoes.html'), 'utf8');

assert.strictEqual(mirror, canonical, 'os espelhos da tela de configurações divergiram');

for (const id of ['iaModeloPerguntas', 'iaModeloPosVenda', 'iaRaciocinioPerguntas', 'iaRaciocinioPosVenda']) {
    assert(canonical.includes(`id="${id}"`), `controle ausente: ${id}`);
}

for (const nivel of ['low', 'medium', 'high', 'xhigh']) {
    const ocorrencias = canonical.match(new RegExp(`<option value="${nivel}"`, 'g')) || [];
    assert(ocorrencias.length >= 2, `nível ${nivel} não está disponível nos dois fluxos`);
}

for (const chave of ['ia_raciocinio_perguntas', 'ia_raciocinio_pos_venda']) {
    assert(canonical.includes(`${chave}:`), `persistência ausente: ${chave}`);
    assert(canonical.includes(`configuracoes?.${chave}`), `retorno salvo não aplicado: ${chave}`);
}

assert(canonical.includes("return ['low', 'medium', 'high', 'xhigh'].includes(nivel) ? nivel : 'medium';"), 'nível inválido deve voltar para medium');
assert(canonical.includes("{ label: 'Codex', itens: data.codex || [] }"), 'catálogo Codex não vem de /api/ia/modelos');
for (const provedor of ['vertex', 'openai', 'deepseek', 'gemini']) {
    assert(canonical.includes(`defaults.${provedor}_ativa === true`), `provedor inativo não está filtrado: ${provedor}`);
}
assert(canonical.includes('grupo.itens.filter(modelo => modelosConfigurados.has'), 'fallback local deve manter somente modelos já configurados');
assert(!canonical.includes('let grupos = fallback;'), 'fallback não pode expor indiscriminadamente todos os modelos conhecidos');

const scripts = [...canonical.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map(match => match[1])
    .filter(source => source.trim());
scripts.forEach((source, index) => {
    assert.doesNotThrow(() => new Function(source), `script inline ${index + 1} possui erro de sintaxe`);
});

console.log('configuracoes_ia_modelo_nivel_frontend: ok');
