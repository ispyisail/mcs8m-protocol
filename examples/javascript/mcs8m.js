/*
 * mcs8m.js - Sony MCS-8M RS-232 frame building and parsing for Node.js.
 * No dependencies. Pair it with any serial library (e.g. the `serialport` package).
 *
 *   const m = require('./mcs8m');
 *   port.write(m.videoRead(m.ME, m.BUS_A));          // ask: what's on program?
 *   const parser = new m.FrameParser();
 *   port.on('data', d => parser.feed(d).forEach(f => console.log(m.describe(f))));
 */
'use strict';

const DEL = 0x01, STX = 0x02, ETX = 0x03, ACK = 0x04, FID = 0x50, PROT = 0x03;
const VIDEO = [0x01, 0x20], AUDIO = [0x02, 0x30];           // [PID, TID]
const ME = 0x00, AUX1 = 0x02, AUX2 = 0x03, EFILE = 0x21;
const BUS_A = 0x00, BUS_B = 0x01, KEY_FILL = 0x07, KEY_SRC = 0x08;

/** Wrap MSG bytes: DEL STX BC(big-endian!) PID FID TID ARQ ART MSG CS ETX */
function buildFrame([pid, tid], msg) {
    const payload = [pid, FID, tid, 0x00, 0x00, ...msg];
    const body = [(payload.length >> 8) & 0xff, payload.length & 0xff, ...payload];   // BC is BIG-endian
    const cs = (0x100 - (body.reduce((a, b) => a + b, 0) & 0xff)) & 0xff;
    return Buffer.from([DEL, STX, ...body, cs, ETX]);
}

/** Video READ (high bit clear). Throws on write codes so a typo can't change the switcher. */
function videoRead(eff, cmd, ...args) {
    if (cmd & 0x80) throw new Error('video codes with the high bit set are WRITES');
    return buildFrame(VIDEO, [PROT, 2 + args.length, eff, cmd, ...args]);
}

/** Video WRITE - changes the switcher. e.g. videoWrite(AUX1, BUS_A, 0x00, 3) */
function videoWrite(eff, cmd, ...args) {
    return buildFrame(VIDEO, [PROT, 2 + args.length, eff, cmd | 0x80, ...args]);
}

/** Audio STATUS request (high bit set). data defaults to [channel, 0]. */
function audioRead(cmd, channel = 0, ...extra) {
    const data = extra.length ? [channel, ...extra] : [channel, 0];
    return buildFrame(AUDIO, [cmd | 0x80, ...data]);
}

/** Stream parser. Frames by BC - never split on 0x03, it appears inside frames. */
class FrameParser {
    constructor() { this.buf = Buffer.alloc(0); this.acks = 0; }

    feed(chunk) {
        this.buf = Buffer.concat([this.buf, chunk]);
        const frames = [];
        while (this.buf.length) {
            if (this.buf[0] !== DEL) {                       // ACK or noise
                if (this.buf[0] === ACK) this.acks++;
                this.buf = this.buf.subarray(1); continue;
            }
            if (this.buf.length < 4) break;
            const bc = (this.buf[2] << 8) | this.buf[3];
            if (this.buf[1] !== STX || bc < 5 || bc > 512) { this.buf = this.buf.subarray(1); continue; }
            if (this.buf.length < bc + 6) break;             // wait for the rest
            const f = this.buf.subarray(0, bc + 6);
            let sum = 0; for (let i = 2; i < f.length - 1; i++) sum += f[i];
            if (f[f.length - 1] === ETX && (sum & 0xff) === 0) {
                frames.push(decode(Buffer.from(f)));
                this.buf = this.buf.subarray(bc + 6);
            } else {
                this.buf = this.buf.subarray(1);             // bad checksum: resync
            }
        }
        return frames;
    }
}

/** Pull the useful fields out of a complete frame. */
function decode(raw) {
    const msg = raw.subarray(9, raw.length - 2);
    if (raw[4] === VIDEO[0]) {
        return { kind: 'video', eff: msg[2], cmd: msg[3] & 0x7f, data: [...msg.subarray(4)], raw };
    }
    return { kind: 'audio', cmd: msg[0] & 0x7f, data: [...msg.subarray(1)], raw };
}

/** Human-readable summary for the commonest frames. */
function describe(f) {
    const hex = f.data.map(b => b.toString(16).padStart(2, '0')).join(' ');
    if (f.kind === 'video') {
        const sec = { 0: 'M/E', 2: 'Aux1', 3: 'Aux2', 0x20: 'EFF20', 0x21: 'E-File' }[f.eff] || `EFF${f.eff}`;
        if (f.cmd < 0x10 && f.eff <= 3) {
            const bus = { 0: 'A', 1: 'B', 7: 'key fill', 8: 'key src' }[f.cmd] || `bus ${f.cmd}`;
            const role = f.eff === ME && f.cmd === 0 ? ' (PROGRAM)' : f.eff === ME && f.cmd === 1 ? ' (PRESET)' : '';
            return `${sec} ${bus}${role} = button ${f.data[1]}`;
        }
        if (f.eff === 0x20 && f.cmd === 0x37) return f.data[2] ? 'transition running' : 'transition finished';
        return `${sec} CMD ${f.cmd.toString(16)}: ${hex}`;
    }
    if (f.cmd === 0x13) return `CH${f.data[0] + 1} ${f.data[1] === 1 ? 'MUTED' : 'live'}`;   // 1 = muted!
    return `audio CMD ${f.cmd.toString(16)}: ${hex}`;
}

module.exports = {
    buildFrame, videoRead, videoWrite, audioRead, FrameParser, decode, describe,
    ME, AUX1, AUX2, EFILE, BUS_A, BUS_B, KEY_FILL, KEY_SRC,
};
