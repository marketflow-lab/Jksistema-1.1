'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const config = fs.readFileSync(path.join(root, '.codex', 'config.toml'), 'utf8');
const agentInstructions = fs.readFileSync(path.join(root, 'AGENTS.md'), 'utf8');

assert.match(config, /^approval_policy\s*=\s*"on-request"\s*$/m, 'escritas devem depender de aprovacao explicita');
assert.match(config, /^approvals_reviewer\s*=\s*"user"\s*$/m, 'a aprovacao deve ser encaminhada ao usuario');
assert.match(config, /^sandbox_mode\s*=\s*"read-only"\s*$/m, 'sessao deve iniciar sem permissao de escrita');
assert.doesNotMatch(config, /workspace-write|danger-full-access|auto_review/, 'perfil versionado nao pode liberar ou autoaprovar escrita');
assert.match(
  agentInstructions,
  /primeira aprovacao[\s\S]*projeto confiavel[\s\S]*sandbox somente leitura[\s\S]*Overrides de maior precedencia nao suspendem esta regra processual/i,
  'AGENTS.md deve documentar o gate tecnico de primeira aprovacao',
);

console.log('Governanca Codex: primeira aprovacao protegida por sandbox read-only.');
