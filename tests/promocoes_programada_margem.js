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

function programada(margem, margemMl, sugestao = 'Não participar') {
  return {
    Status: 'Programada',
    Custo: 'R$ 12,52',
    'Tarifa ML': 'R$ 4,04',
    'Preço Final ML': 'R$ 36,30',
    'Valor líquido ML': 'R$ 4,28',
    Margem: `${String(margem).replace('.', ',')}%`,
    'Margem ML': `${String(margemMl).replace('.', ',')}%`,
    Ação: sugestao,
  };
}

function decisao(row) {
  context.normalizeApiRowPercentAndAction(row);
  return row['Ação'];
}

assert.strictEqual(
  decisao(programada(31.56, 11.79)),
  'Não participar',
  'a recomendação negativa do backend para margem ML baixa deve ser preservada'
);
assert.strictEqual(
  decisao(programada(17, 14.99)),
  'Não participar',
  'a recomendação negativa do backend abaixo de 15% deve ser preservada'
);
assert.strictEqual(
  decisao(programada(20, 16.99)),
  'Não participar',
  'a recomendação negativa do backend para diferença superior a 3 pontos deve ser preservada'
);
assert.strictEqual(
  decisao(programada(20, 17, 'Participar')),
  'Participar',
  'a recomendação positiva do backend no limite de 3 pontos deve ser preservada'
);

assert.strictEqual(
  decisao(programada(31.56, 11.79, 'Participar')),
  'Participar',
  'a normalização preserva a decisão recebida, inclusive uma escolha manual; regras financeiras são testadas no backend'
);

console.log('promocoes programada margem checks passed');
