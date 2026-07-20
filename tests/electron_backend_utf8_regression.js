const assert = require('assert');
const fs = require('fs');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..');
const backendSource = fs.readFileSync(
  path.join(repoRoot, 'electron_app', 'main', 'modules', 'backend.js'),
  'utf8'
);

const pythonProbe = '"%PYTHON_EXE%" -B -I -X utf8 -c "import sys, uvicorn; print(sys.version)"';
const backendStart = '"%PYTHON_EXE%" -X utf8 -B -I -m uvicorn --app-dir "%CD%" backend_api:app';

assert(
  backendSource.includes(pythonProbe),
  'generated Python probe must preserve isolated mode and force UTF-8 via -X utf8'
);
assert(
  backendSource.includes(backendStart),
  'generated backend command must preserve isolated mode and force UTF-8 via -X utf8'
);

console.log('electron backend UTF-8 regression checks passed');
