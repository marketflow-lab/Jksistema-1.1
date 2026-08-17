'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'static', 'configuracoes.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'static', 'configuracoes-whatsapp.js'), 'utf8');

assert(html.includes('id="waAudioPreflight"'));
assert(html.includes('Áudios recebidos no WhatsApp'));
assert(html.includes('O Whisper roda somente nesta máquina e não usa API paga de transcrição.'));
assert(script.includes("payload.audio_messages || {}"));
assert(script.includes("audioMessages.preflight || {}"));
assert(script.includes("audioMessages.queue || {}"));
assert(script.includes("/api/admin/whatsapp/audio/preflight"));
assert(script.includes("inboundMediaCounts.waiting_retry"));

console.log('whatsapp audio frontend contract: OK');
