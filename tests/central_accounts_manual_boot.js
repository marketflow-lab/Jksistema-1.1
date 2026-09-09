'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let calls = 0;
const forbidden = () => { calls++; throw new Error('Automatic activity is forbidden in central mode'); };
const context = {
    window: { jkCentralManualMode: () => true },
    document: { addEventListener: forbidden },
    // Local identity is needed only to scope cross-tab screen invalidation.
    localStorage: { getItem: key => { assert.equal(key, 'user_data'); return JSON.stringify({ client_id: 'synthetic', username: 'operator' }); }, setItem: forbidden },
    fetch: forbidden, setInterval: forbidden, setTimeout: forbidden,
};
vm.runInNewContext(fs.readFileSync('static/auth/shared-sync-boot.js', 'utf8'), context);
assert.equal(calls, 0, 'central bootstrap must not start timers, Firebase, Drive or operational sync');
console.log('central manual boot: passed');
