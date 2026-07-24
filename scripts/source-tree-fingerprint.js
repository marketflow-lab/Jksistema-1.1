'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const MAX_GIT_OUTPUT = 128 * 1024 * 1024;

function gitOutput(repoRoot, args) {
  const result = spawnSync('git', ['-c', 'color.ui=false', ...args], {
    cwd: repoRoot,
    encoding: null,
    env: { ...process.env, GIT_OPTIONAL_LOCKS: '0' },
    maxBuffer: MAX_GIT_OUTPUT,
  });
  if (result.status !== 0 || result.error) {
    throw new Error(`Nao foi possivel obter a proveniencia Git (${args[0]}).`);
  }
  return Buffer.from(result.stdout || []);
}

function updateRecord(hash, label, value) {
  const labelBuffer = Buffer.from(label, 'utf8');
  const valueBuffer = Buffer.isBuffer(value) ? value : Buffer.from(String(value), 'utf8');
  hash.update(Buffer.from(`${labelBuffer.length}:`, 'ascii'));
  hash.update(labelBuffer);
  hash.update(Buffer.from(`:${valueBuffer.length}:`, 'ascii'));
  hash.update(valueBuffer);
}

function splitNull(buffer) {
  const values = [];
  let start = 0;
  for (let index = 0; index < buffer.length; index += 1) {
    if (buffer[index] !== 0) continue;
    if (index > start) values.push(buffer.subarray(start, index));
    start = index + 1;
  }
  if (start < buffer.length) values.push(buffer.subarray(start));
  return values;
}

function untrackedDigest(repoRoot, relativePathBuffer) {
  const relativePath = relativePathBuffer.toString('utf8');
  const absolutePath = path.resolve(repoRoot, relativePath);
  const resolvedRoot = path.resolve(repoRoot);
  if (absolutePath !== resolvedRoot && !absolutePath.startsWith(`${resolvedRoot}${path.sep}`)) {
    throw new Error('Git retornou um caminho fora da arvore de trabalho.');
  }
  const stat = fs.lstatSync(absolutePath);
  const hash = crypto.createHash('sha256');
  if (stat.isSymbolicLink()) {
    updateRecord(hash, 'type', 'symlink');
    updateRecord(hash, 'target', fs.readlinkSync(absolutePath));
  } else if (stat.isFile()) {
    updateRecord(hash, 'type', 'file');
    updateRecord(hash, 'bytes', fs.readFileSync(absolutePath));
  } else {
    updateRecord(hash, 'type', 'other');
  }
  return hash.digest();
}

function captureGitSourceState(repoRoot) {
  const commitSha = gitOutput(repoRoot, ['rev-parse', 'HEAD']).toString('utf8').trim();
  if (!/^[a-f0-9]{40}$/i.test(commitSha)) throw new Error('Commit Git invalido para a suite completa.');

  const headTree = gitOutput(repoRoot, ['rev-parse', 'HEAD^{tree}']);
  const status = gitOutput(repoRoot, ['status', '--porcelain=v1', '-z', '--untracked-files=all']);
  const stagedDiff = gitOutput(repoRoot, ['diff', '--cached', '--binary', '--full-index', '--no-ext-diff', '--no-textconv', 'HEAD', '--', '.']);
  const worktreeDiff = gitOutput(repoRoot, ['diff', '--binary', '--full-index', '--no-ext-diff', '--no-textconv', '--', '.']);
  const untracked = splitNull(gitOutput(repoRoot, ['ls-files', '--others', '--exclude-standard', '-z']))
    .sort(Buffer.compare);

  const hash = crypto.createHash('sha256');
  updateRecord(hash, 'schema', 'jk-source-tree-v1');
  updateRecord(hash, 'head-tree', headTree);
  updateRecord(hash, 'status', status);
  updateRecord(hash, 'staged-diff', stagedDiff);
  updateRecord(hash, 'worktree-diff', worktreeDiff);
  for (const relativePath of untracked) {
    updateRecord(hash, 'untracked-path', relativePath);
    updateRecord(hash, 'untracked-content', untrackedDigest(repoRoot, relativePath));
  }

  return {
    commitSha: commitSha.toLowerCase(),
    worktreeState: status.length ? 'dirty' : 'clean',
    worktreeFingerprint: `sha256:${hash.digest('hex')}`,
  };
}

function gitSourceStateIsStable(initialState, finalState) {
  return Boolean(
    initialState
      && finalState
      && initialState.commitSha === finalState.commitSha
      && initialState.worktreeFingerprint === finalState.worktreeFingerprint,
  );
}

module.exports = { captureGitSourceState, gitSourceStateIsStable };
