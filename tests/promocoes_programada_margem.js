'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const repoRoot = path.resolve(__dirname, '..');
const source = fs.readFileSync(
  path.join(repoRoot, 'static', 'frontend_promo', 'tabela-preferencias.js'),
  'utf8'
);

const context = {
  console,
  TABLE_COLUMNS_MODERN: [],
  TABLE_COLUMN_ALIASES: {},
  document: {
    getElementById(id) {
      if (id === 'apiMargemMinima') return { value: '15' };
      return null;
    },
  },
  preencherCamposCanonicosTabelaPromo() {},
  getActionTolerancePct() { return 0; },
  getFirstRowValueByAliases(row, aliases) {
    for (const alias of aliases || []) {
      if (Object.prototype.hasOwnProperty.call(row, alias)) return row[alias];
    }
    return '';
  },
  parsePercentValue(value) {
    if (value === null || value === undefined || value === '') return null;
    const parsed = Number(String(value).replace('%', '').replace(',', '.'));
    return Number.isFinite(parsed) ? parsed : null;
  },
};

vm.createContext(context);
vm.runInContext(source, context, { filename: 'tabela-preferencias.js' });

function programada(margem, margemMl) {
  return {
    Status: 'Programada',
    Custo: 'R$ 12,52',
    'Tarifa ML': 'R$ 4,04',
    'Preço Final ML': 'R$ 36,30',
    'Valor líquido ML': 'R$ 4,28',
    Margem: `${String(margem).replace('.', ',')}%`,
    'Margem ML': `${String(margemMl).replace('.', ',')}%`,
    Ação: 'Participar',
  };
}

function decisao(row) {
  context.normalizeApiRowPercentAndAction(row);
  return row['Ação'];
}

assert.strictEqual(
  decisao(programada(31.56, 11.79)),
  'Não participar',
  'o caso real deve ser reprovado por margem ML abaixo de 15% e diferença superior a 3 pontos'
);
assert.strictEqual(
  decisao(programada(17, 14.99)),
  'Não participar',
  'margem ML abaixo de 15% deve ser reprovada'
);
assert.strictEqual(
  decisao(programada(20, 16.99)),
  'Não participar',
  'diferença superior a 3 pontos deve ser reprovada'
);
assert.strictEqual(
  decisao(programada(20, 17)),
  'Participar',
  'diferença exata de 3 pontos com margem ML suficiente deve ser aceita'
);

console.log('promocoes programada margem checks passed');
