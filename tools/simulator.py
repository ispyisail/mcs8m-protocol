#!/usr/bin/env python3
"""
simulator.py - a fake Sony MCS-8M on a virtual serial port, for trying the
examples without hardware.  Linux/macOS.

    python3 tools/simulator.py            # prints e.g. "Simulator on /dev/pts/5"
    python3 examples/python/01_read_program.py /dev/pts/5

It copies the real unit's behaviour as documented in docs/:
  * video replies after ~1.01 s, audio replies after ~11 ms (--fast: 50 ms / 5 ms)
  * audio writes get a bare 04 ACK; video writes get an answer frame
  * unknown commands get no reply at all
  * the reply is sometimes sent twice, ~1.5 s apart (--duplicates)
  * panel changes are pushed unsolicited, and EFF 20 CMD B7 01/00 marks a transition

Type panel actions on stdin:
  p N     select button N on program (M/E A-bus)     t        AUTO TRANS (swap A/B)
  v N     select button N on preset  (M/E B-bus)     a1 N     Aux1 = N (push is a guess)
  m CH    toggle mute on channel 1-6                 s        show state       q  quit

Values are only examples; this is not a full emulation.
"""
import argparse, os, pty, random, sys, threading, time, tty

DEL, STX, ETX, ACK = 0x01, 0x02, 0x03, 0x04


def frame(pid, tid, msg):
    payload = bytes([pid, 0x50, tid, 0, 0]) + bytes(msg)
    body = len(payload).to_bytes(2, "big") + payload
    return bytes([DEL, STX]) + body + bytes([(-sum(body)) & 0xFF, ETX])


def vframe(eff, cmd, *data):
    return frame(0x01, 0x20, [0x03, 2 + len(data), eff, cmd, *data])


def aframe(cmd, *data):
    return frame(0x02, 0x30, [cmd, *data])


class Switcher:
    def __init__(self, fd, fast=False, duplicates=False):
        self.fd, self.lock = fd, threading.Lock()
        self.vdelay, self.adelay = (0.05, 0.005) if fast else (1.011, 0.011)
        self.duplicates = duplicates
        self.xpt = {(e, b): 1 for e in (0, 2, 3) for b in range(16)}
        self.xpt.update({(0, 0): 2, (0, 1): 7, (0, 7): 7, (0, 8): 7, (2, 0): 7, (2, 1): 6, (3, 0): 7, (3, 1): 7})
        self.flags = {(0, 0): 0x40, (2, 8): 0x80, (3, 8): 0x80, **{(e, b): 0x80 for e in (0, 2, 3) for b in (10, 12, 14)}}
        self.next_trans = 0x01
        self.trans_type = {0: 0x02, 1: 0x02}
        self.rate = {(e, f): 0x0030 for e in (0, 2, 3) for f in (0, 1, 6)}
        self.sub = {(0x1B, 0): 0x0001, (0x1B, 4): 0x02, (0x1C, 0): 0x1201, (0x1C, 1): 0, (0x1C, 2): 0,
                    (0x1C, 0x10): 0x1001, (0x1C, 0x11): 0, (0x1C, 0x12): 1, (0x33, 0): 0x0001, (0x33, 4): 0x02}
        self.faders = [0, 0, 0, 0, 0, 190, 0xEE, 190]
        self.chan = {c: [1] * 5 + [0] if c == 0x13 else [15] * 6 if c in (0x14, 0x1B, 0x1C, 0x1E, 0x1F, 0x22, 0x23, 0x33)
                     else [0] * 6 for c in (0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1B, 0x1C, 0x1D, 0x1E,
                                            0x1F, 0x20, 0x22, 0x23, 0x33)}
        self.master = [20, 20, 20, 20]
        self.monitor = {0x40: 124, 0x41: 0, 0x43: 1}

    # ------------------------------------------------------------ output
    def send(self, data, delay=0.0, dup=False):
        def go():
            with self.lock:
                os.write(self.fd, data[:5]); os.write(self.fd, data[5:])     # split delivery, like USB adapters
        threading.Timer(delay, go).start()
        if dup and self.duplicates and random.random() < 0.3:
            threading.Timer(delay + 1.5, go).start()

    def push_program(self):
        self.send(vframe(0, 0x80, self.flags.get((0, 0), 0), self.xpt[0, 0]))

    # ------------------------------------------------------------ requests
    def handle(self, f):
        pid, msg = f[4], list(f[9:-2])
        if pid == 0x01:
            self.video(msg[2], msg[3], msg[4:])
        elif pid == 0x02:
            self.audio(msg[0], msg[1:])

    def video(self, eff, cmd, args):
        write, c = bool(cmd & 0x80), cmd & 0x7F
        reply = None
        if c < 0x10 and eff in (0, 2, 3):
            if write and len(args) >= 2:
                self.xpt[eff, c] = args[1]
            reply = [self.flags.get((eff, c), 0), self.xpt[eff, c]]
        elif eff == 0 and c == 0x10:
            if write: self.next_trans = args[0]
            reply = [self.next_trans]
        elif eff == 0 and c == 0x11 and args:
            if write: self.trans_type[args[0]] = args[2]
            reply = [args[0], 0, self.trans_type.get(args[0], 2)]
        elif c == 0x18 and args and (eff, args[0]) in self.rate:
            if write: self.rate[eff, args[0]] = (args[2] << 8) | args[3]
            r = self.rate[eff, args[0]]
            reply = [args[0], 0, r >> 8, r & 0xFF]
        elif c == 0x16 and write and eff == 0:
            self.auto_trans(((args[1] & 0x0F) * 100 + (args[2] >> 4) * 10 + (args[2] & 0x0F)) if len(args) > 2 else 30)
            return                                                       # no answer frame for AUTO TRANS
        elif eff == 0 and (c, args[0] if args else 0) in self.sub:
            sa = args[0] if args else 0
            if write: self.sub[c, sa] = int.from_bytes(bytes(args[1:]), "big")
            v = self.sub[c, sa]
            reply = [sa, v >> 8, v & 0xFF] if v > 0xFF or sa in (0, 0x10) else [sa, v]
        if reply is not None:
            self.send(vframe(eff, c | 0x80, *reply), self.vdelay)

    def audio(self, cmd, data):
        status, c = bool(cmd & 0x80), cmd & 0x7F
        if not status:                                                   # write: ACK + apply
            self.send(bytes([ACK]), self.adelay)
            if c == 0x10 and len(data) >= 8: self.faders = data[:8]
            elif c == 0x12 and len(data) >= 2: self.master[data[0]] = data[1]
            elif c in self.chan and len(data) >= 2 and data[0] < 6: self.chan[c][data[0]] = data[1]
            elif c in self.monitor and len(data) >= 2: self.monitor[c] = data[1]
            return
        ch = data[0] if data else 0
        if c == 0x10: reply = self.faders
        elif c == 0x12 and ch < 4: reply = [ch, self.master[ch]]
        elif c in self.chan and ch < 6: reply = [ch, self.chan[c][ch]]
        elif c in self.monitor: reply = [0, self.monitor[c]]
        else: return                                                     # unknown: silence
        self.send(aframe(c, *reply), self.adelay, dup=True)

    # ------------------------------------------------------------ panel
    def auto_trans(self, frames=30):
        def run():
            self.send(vframe(0x20, 0xB7, 0, 0, 1))
            time.sleep(frames / 30)
            self.xpt[0, 0], self.xpt[0, 1] = self.xpt[0, 1], self.xpt[0, 0]
            self.push_program()
            self.send(vframe(0x20, 0xB7, 0, 0, 0))
        threading.Thread(target=run, daemon=True).start()

    def panel(self, line):
        p = line.split()
        if not p: return
        try:
            if p[0] == "p": self.xpt[0, 0] = int(p[1]); self.push_program()
            elif p[0] == "v": self.xpt[0, 1] = int(p[1])
            elif p[0] in ("a1", "a2"):
                e = 2 if p[0] == "a1" else 3
                self.xpt[e, 0] = int(p[1]); self.send(vframe(e, 0x80, 0, self.xpt[e, 0]))
            elif p[0] == "t": r = self.rate[0, 0]; self.auto_trans((r >> 8 & 0xF) * 100 + (r >> 4 & 0xF) * 10 + (r & 0xF))
            elif p[0] == "m": ch = int(p[1]) - 1; self.chan[0x13][ch] ^= 1
            elif p[0] == "s":
                print(f"  program {self.xpt[0, 0]}  preset {self.xpt[0, 1]}  aux1 {self.xpt[2, 0]}  aux2 {self.xpt[3, 0]}")
                print("  muted: " + " ".join(f"CH{i + 1}" for i, m in enumerate(self.chan[0x13]) if m))
            elif p[0] == "q": os._exit(0)
            else: print("  ?  (p N, v N, t, a1 N, a2 N, m CH, s, q)")
        except (IndexError, ValueError, KeyError):
            print("  bad arguments")


def serve(sw, fd):
    buf = bytearray()
    while True:
        try:
            buf += os.read(fd, 4096)
        except OSError:
            time.sleep(0.05); continue
        while len(buf) >= 4:
            if buf[0] != DEL or buf[1] != STX:
                buf.pop(0); continue
            n = ((buf[2] << 8) | buf[3]) + 6
            if len(buf) < n: break
            f = bytes(buf[:n])
            if f[-1] == ETX and sum(f[2:-1]) & 0xFF == 0:
                del buf[:n]; sw.handle(f)
            else:
                buf.pop(0)                                    # the real unit silently drops bad frames


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--fast", action="store_true", help="reply quickly instead of the real ~1 s video delay")
    ap.add_argument("--duplicates", action="store_true", help="sometimes repeat a reply 1.5 s later, like the real unit")
    a = ap.parse_args()
    master, slave = pty.openpty()
    tty.setraw(slave)
    sw = Switcher(master, a.fast, a.duplicates)
    threading.Thread(target=serve, args=(sw, master), daemon=True).start()
    print(f"Simulator on {os.ttyname(slave)}   (type p N / v N / t / a1 N / m CH / s / q)", flush=True)
    for line in sys.stdin:
        sw.panel(line)
    while True:                                               # stdin closed (e.g. run in background)
        time.sleep(3600)


if __name__ == "__main__":
    main()
