'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const LOCK_NAME = 'installer-runtime.lock.json';
const SHA256_RE = /^[a-f0-9]{64}$/;

function readJson(candidate) {
  return JSON.parse(fs.readFileSync(candidate, 'utf8').replace(/^\uFEFF/, ''));
}

function sha256Text(value) {
  return crypto.createHash('sha256').update(String(value), 'utf8').digest('hex');
}

function identityParts(contract) {
  return [
    contract?.python?.portable?.tree_sha256,
    contract?.wheelhouse?.requirements_sha256,
    contract?.wheelhouse?.manifest_sha256,
    contract?.visual_cpp?.sha256,
    contract?.whisper?.manifest_sha256,
  ].map(value => String(value || '').trim().toLowerCase());
}

function calculateRuntimeId(contract) {
  return sha256Text(identityParts(contract).join('\n'));
}

function validateRuntimeContract(contract) {
  const failures = [];
  if (!contract || Number(contract.schema_version) !== 1) {
    failures.push('schema_version do contrato de runtime deve ser 1');
    return failures;
  }
  if (String(contract.platform || '') !== 'win32-x64') {
    failures.push('plataforma do contrato de runtime deve ser win32-x64');
  }
  const parts = identityParts(contract);
  const labels = [
    'python.portable.tree_sha256',
    'wheelhouse.requirements_sha256',
    'wheelhouse.manifest_sha256',
    'visual_cpp.sha256',
    'whisper.manifest_sha256',
  ];
  parts.forEach((value, index) => {
    if (!SHA256_RE.test(value)) failures.push(`SHA-256 invalido: ${labels[index]}`);
  });
  const declaredId = String(contract.runtime_id || '').trim().toLowerCase();
  const calculatedId = calculateRuntimeId(contract);
  if (!SHA256_RE.test(declaredId) || declaredId !== calculatedId) {
    failures.push(`runtime_id divergente: esperado ${calculatedId}, encontrado ${declaredId || 'ausente'}`);
  }
  const python = contract.python || {};
  const portable = python.portable || {};
  if (!String(python.version || '').trim() || !String(python.abi || '').trim()) {
    failures.push('versao/ABI Python ausentes no contrato de runtime');
  }
  for (const [label, value] of [
    ['python.portable.file_count', portable.file_count],
    ['python.portable.total_size', portable.total_size],
    ['wheelhouse.wheel_count', contract?.wheelhouse?.wheel_count],
    ['whisper.total_bytes', contract?.whisper?.total_bytes],
  ]) {
    if (!Number.isSafeInteger(Number(value)) || Number(value) <= 0) failures.push(`valor invalido: ${label}`);
  }
  const whisperFiles = Array.isArray(contract?.whisper?.files) ? contract.whisper.files : [];
  if (!whisperFiles.length) failures.push('lista de arquivos Whisper ausente');
  const seen = new Set();
  for (const entry of whisperFiles) {
    const relative = String(entry?.path || '').replace(/\\/g, '/');
    if (
      !relative
      || path.posix.isAbsolute(relative)
      || path.posix.normalize(relative) !== relative
      || relative.split('/').some(part => !part || part === '.' || part === '..' || part.includes(':'))
    ) {
      failures.push(`caminho Whisper invalido: ${relative || 'ausente'}`);
      continue;
    }
    if (seen.has(relative.toLowerCase())) failures.push(`arquivo Whisper duplicado: ${relative}`);
    seen.add(relative.toLowerCase());
    if (!Number.isSafeInteger(Number(entry.size)) || Number(entry.size) < 0) {
      failures.push(`tamanho Whisper invalido: ${relative}`);
    }
    if (!SHA256_RE.test(String(entry.sha256 || '').toLowerCase())) {
      failures.push(`SHA-256 Whisper invalido: ${relative}`);
    }
  }
  return failures;
}

function loadRuntimeContract(rootDir) {
  const candidate = path.join(path.resolve(rootDir), LOCK_NAME);
  const contract = readJson(candidate);
  const failures = validateRuntimeContract(contract);
  if (failures.length) throw new Error(`Contrato de runtime invalido:\n- ${failures.join('\n- ')}`);
  return { contract, path: candidate };
}

module.exports = {
  LOCK_NAME,
  calculateRuntimeId,
  identityParts,
  loadRuntimeContract,
  validateRuntimeContract,
};
