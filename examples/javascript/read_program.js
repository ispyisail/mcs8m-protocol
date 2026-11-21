// Example: read what's on air and print every frame the switcher sends (Ctrl+C to stop).
// Read-only.   npm install serialport   then   node read_program.js [/dev/ttyUSB0]
'use strict';
const { SerialPort } = require('serialport');
const m = require('./mcs8m');

const port = new SerialPort({ path: process.argv[2] || '/dev/ttyUSB0', baudRate: 38400 });   // 8N1 is the default
const parser = new m.FrameParser();

port.on('data', chunk => {
    for (const f of parser.feed(chunk)) console.log(new Date().toISOString().slice(11, 23), m.describe(f));
});
port.on('open', () => {
    // video replies take ~1 s each, so space the requests out rather than sending a burst
    const asks = [m.videoRead(m.ME, m.BUS_A), m.videoRead(m.ME, m.BUS_B), m.audioRead(0x13, 5)];
    asks.forEach((frame, i) => setTimeout(() => port.write(frame), i * 1200));
    console.log('asking... then listening for panel changes');
});
port.on('error', e => { console.error(e.message); process.exit(1); });
