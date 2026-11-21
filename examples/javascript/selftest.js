// Offline check of mcs8m.js against frames captured from a real MCS-8M:  node selftest.js
'use strict';
const assert = require('assert');
const m = require('./mcs8m');
const hex = s => Buffer.from(s.replace(/ /g, ''), 'hex');

assert.deepStrictEqual(m.videoRead(m.ME, m.BUS_A), hex('01 02 00 09 01 50 20 00 00 03 02 00 00 81 03'));
assert.deepStrictEqual(m.videoRead(m.AUX2, m.BUS_A), hex('01 02 00 09 01 50 20 00 00 03 02 03 00 7e 03'));
assert.deepStrictEqual(m.audioRead(0x13, 5), hex('01 02 00 08 02 50 30 00 00 93 05 00 de 03'));
assert.deepStrictEqual(m.videoWrite(m.AUX1, m.BUS_A, 0, 1), hex('01 02 00 0b 01 50 20 00 00 03 04 02 80 00 01 fa 03'));
assert.throws(() => m.videoRead(0x21, 0x80));

// stream with an ACK, a frame full of 0x03 bytes, split into awkward pieces, plus a corrupt frame
const stream = Buffer.concat([hex('04'), hex('01 02 00 0b 01 50 20 00 00 03 04 03 80 00 07 f3 03'),
    hex('01 02 00 08 02 50 30 00 00 13 05 00 5f 03'),                 // bad checksum - must be dropped
    hex('01 02 00 0c 01 50 20 00 00 03 05 20 b7 00 00 01 a3 03')]);
const p = new m.FrameParser(); let got = [];
for (let i = 0; i < stream.length; i += 3) got = got.concat(p.feed(stream.subarray(i, i + 3)));
assert.deepStrictEqual(got.map(m.describe), ['Aux2 A = button 7', 'transition running']);
assert.strictEqual(p.acks, 1);
console.log('mcs8m.js selftest: OK');
