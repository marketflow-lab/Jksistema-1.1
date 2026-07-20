const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const ui = fs.readFileSync(path.join(root, 'static/ia-sidebar/02-ui-modelos.part.js'), 'utf8');
const runtime = fs.readFileSync(path.join(root, 'static/ia-sidebar/03-init-shell-codex.part.js'), 'utf8');
const bindings = fs.readFileSync(path.join(root, 'static/ia-sidebar/09-chat-bootstrap.part.js'), 'utf8');

assert(ui.includes('id="jk-codex-voice"'));
assert(ui.includes('aria-label="Gravar comando de voz"'));
assert(ui.includes('aria-pressed="false"'));
assert(ui.includes('id="jk-codex-voice-status" role="status" aria-live="polite"'));
assert(ui.includes('id="jk-codex-voice-cancel"'));

assert(runtime.includes("navigator.mediaDevices?.getUserMedia"));
assert(runtime.includes('new MediaRecorder'));
assert(runtime.includes("/api/codex/audio/transcriptions"));
assert(runtime.includes("input.value = text"));
assert(runtime.includes('Revise o texto e pressione Enviar'));
assert(!runtime.includes("_codexVoiceTranscribe(new Blob(chunks, { type: mime }), mime);\n          _codexCriarTarefa"));
assert(runtime.includes("raw_audio_retained") || runtime.includes('Transcrevendo localmente'));

assert(bindings.includes("getElementById('jk-codex-voice')"));
assert(bindings.includes("getElementById('jk-codex-voice-cancel')"));
assert(bindings.includes("event.key === 'Escape' && codexVoiceState === 'recording'"));

console.log('black jhon voice input contract: OK');
