#!/usr/bin/env python3
"""
mcs8m_dump.py - read every readable setting from a Sony MCS-8M over RS-232
and write it to JSON for analysis.

READ-ONLY: this script only ever sends status/READ requests. A guard refuses
to send anything that the protocol would treat as a WRITE.

Requirements: Python 3 standard library only (no pyserial). Linux/macOS.

Usage:
  python3 mcs8m_dump.py                          # /dev/ttyUSB0 -> mcs8m_dump_<time>.json
  python3 mcs8m_dump.py -p /dev/ttyUSB1 -o out.json
  python3 mcs8m_dump.py --dry-run                # print the frames, don't open the port
  python3 mcs8m_dump.py --selftest               # check frame builder against manual examples

Close anything else using the serial port first (e.g. stop Node-RED).

Switcher prerequisite (manual s.3): Setup > System > "Back" + press V3 knob ->
For Services > Serial Cmd Group = "Rem 1", then save via Startup Define & restart.
Never use "FACT".

---------------------------------------------------------------------------
PROTOCOL NOTES / KNOWN ERRORS IN THE SONY PROTOCOL MANUAL (1st ed., 2011)
---------------------------------------------------------------------------
 1. *** BC byte order ***  The Data Format table says BC is "2 byte Little
    Endian". It is NOT. All 40 example frames in the manual, and the
    real switcher, use BIG-endian (high byte first): 01 02 00 0B ...
    A little-endian BC makes the switcher silently discard the frame.
 2. BC counts PID .. last MSG byte (excludes CS and ETX).
    CS = two's complement so (BC..CS) sums to 0x00 mod 256.
 3. ETX (0x03) is NOT a unique terminator. PROT is always 0x03 and data
    can contain 0x03 (EFF=Aux2, codes, checksum). Never split the stream on
    0x03 - frame by BC. Same applies to 0x01/0x02/0x04 appearing in data.
 4. READ/WRITE high bit is INVERTED between the two command sets:
      video: 0x0n = READ,  0x8n = WRITE
      audio: 0x0n = WRITE, 0x8n = STATUS (read)
    Setting the high bit "to read" on a video command executes a write
    (e.g. 0x80 on EFF 0x21 = SNAPSHOT STORE overwrites a register).
 5. Audio execution requests get a bare 0x04 ACK byte (not framed).
 6. Audio status-response example (s.4-3) has a bad checksum (5F, should
    be 5E) and its text says OFF while the data byte shows 01.
 7. Other typos: EQ LOW IN example uses cmd 0x17 (should be 0x19);
    SNAPSHOT register table lists "0x01: 2" (should be 0x02); AUTO TRANSITION
    note says byte5/byte6 (means byte4/byte5); fader table codes 60 and 61
    both read -34.2 dB.
"""
import argparse, json, os, select, sys, time
from datetime import datetime, timezone

DEL, STX, ETX, ACK = 0x01, 0x02, 0x03, 0x04
FID = 0x50
PID_VIDEO, TID_VIDEO = 0x01, 0x20
PID_AUDIO, TID_AUDIO = 0x02, 0x30
PROT = 0x03

# ---------------------------------------------------------------- framing
def checksum(body):
    return (-sum(body)) & 0xFF

def build_frame(pid, tid, msg, bc_endian="big"):
    payload = bytes([pid, FID, tid, 0x00, 0x00]) + bytes(msg)
    n = len(payload)
    bc = bytes([(n >> 8) & 0xFF, n & 0xFF]) if bc_endian == "big" else bytes([n & 0xFF, (n >> 8) & 0xFF])
    body = bc + payload
    return bytes([DEL, STX]) + body + bytes([checksum(body), ETX])

def video_read(eff, cmd, *args, bc_endian="big"):
    if cmd & 0x80:
        raise ValueError(f"refusing video cmd 0x{cmd:02X}: high bit = WRITE on video")
    m = [eff, cmd, *args]
    return build_frame(PID_VIDEO, TID_VIDEO, [PROT, len(m), *m], bc_endian)

def audio_status(cmd, data, bc_endian="big"):
    if cmd & 0x80:
        raise ValueError("pass the base audio cmd; the status bit is added here")
    return build_frame(PID_AUDIO, TID_AUDIO, [cmd | 0x80, *data], bc_endian)

class FrameParser:
    """Stream parser: frames by BC (big-endian), never by ETX."""
    def __init__(self):
        self.buf = bytearray(); self.acks = 0; self.junk = bytearray()
    def feed(self, data):
        self.buf += data
        out = []
        while self.buf:
            if self.buf[0] != DEL:
                b = self.buf.pop(0)
                if b == ACK: self.acks += 1
                else: self.junk.append(b)
                continue
            if len(self.buf) < 4: break
            if self.buf[1] != STX:
                self.junk.append(self.buf.pop(0)); continue
            bc = (self.buf[2] << 8) | self.buf[3]
            if bc < 5 or bc > 512:            # implausible -> resync
                self.junk.append(self.buf.pop(0)); continue
            total = bc + 6
            if len(self.buf) < total: break
            f = bytes(self.buf[:total])
            if f[-1] == ETX and sum(f[2:-1]) & 0xFF == 0:
                del self.buf[:total]; out.append(f)
            else:
                self.junk.append(self.buf.pop(0))
        return out

def frame_msg(f):
    """returns (pid, fid, tid, msg_bytes)"""
    return f[4], f[5], f[6], f[9:-2]

# ---------------------------------------------------------------- serial (stdlib)
class Port:
    def __init__(self, path, baud=38400):
        import termios
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        a = termios.tcgetattr(self.fd)
        speed = getattr(termios, f"B{baud}")
        a[0] = 0                                                  # iflag: raw, no XON/XOFF
        a[1] = 0                                                  # oflag
        a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL        # 8N1, no RTS/CTS
        a[3] = 0                                                  # lflag: no echo/canon
        a[4] = a[5] = speed
        a[6][termios.VMIN] = 0; a[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, a)
        termios.tcflush(self.fd, termios.TCIOFLUSH)
    def write(self, b):
        os.write(self.fd, b)
    def read(self, timeout):
        r, _, _ = select.select([self.fd], [], [], timeout)
        return os.read(self.fd, 4096) if r else b""
    def close(self):
        os.close(self.fd)

# ---------------------------------------------------------------- value tables
FADER_DB = ("-inf -77.1 -76.2 -75.4 -74.5 -73.7 -72.8 -72.0 -71.1 -70.2 -69.4 -68.5 -67.7 -66.8 -66.0 -65.1 -64.2 -63.4 -62.5 -61.7 -60.8 -60.0 -59.2 -58.4 -57.6 -56.8 -56.0 -55.2 -54.4 -53.6 -52.8 -52.0 -51.2 -50.4 -49.6 -48.8 -48.0 -47.2 -46.4 -45.6 -44.8 -44.0 -43.2 -42.4 -41.6 -40.8 -40.0 -39.6 -39.2 -38.8 -38.4 -38.0 -37.6 -37.3 -36.9 -36.5 -36.1 -35.7 -35.3 -35.0 -34.2 -34.2 -33.8 -33.4 -33.0 -32.6 -32.3 -31.9 -31.5 -31.1 -30.7 -30.3 -30.0 -29.6 -29.3 -29.0 -28.6 -28.3 -28.0 -27.6 -27.3 -27.0 -26.6 -26.3 -26.0 -25.6 -25.3 -25.0 -24.6 -24.3 -24.0 -23.6 -23.3 -23.0 -22.6 -22.3 -22.0 -21.6 -21.3 -21.0 -20.6 -20.3 -20.0 -19.6 -19.3 -19.0 -18.7 -18.3 -18.0 -17.7 -17.4 -17.0 -16.7 -16.4 -16.1 -15.8 -15.4 -15.1 -14.8 -14.5 -14.1 -13.8 -13.5 -13.2 -12.9 -12.5 -12.2 -11.9 -11.6 -11.2 -10.9 -10.6 -10.3 -10.0 -9.8 -9.6 -9.5 -9.3 -9.1 -9.0 -8.8 -8.7 -8.5 -8.3 -8.2 -8.0 -7.9 -7.7 -7.5 -7.4 -7.2 -7.0 -6.9 -6.7 -6.6 -6.4 -6.2 -6.1 -5.9 -5.8 -5.6 -5.4 -5.3 -5.1 -5.0 -4.8 -4.6 -4.5 -4.3 -4.1 -4.0 -3.8 -3.6 -3.5 -3.3 -3.1 -3.0 -2.8 -2.6 -2.5 -2.3 -2.1 -2.0 -1.8 -1.6 -1.5 -1.3 -1.1 -1.0 -0.8 -0.6 -0.5 -0.3 -0.1 0 0.1 0.3 0.4 0.6 0.8 0.9 1.1 1.2 1.4 1.6 1.7 1.9 2.0 2.2 2.4 2.5 2.7 2.9 3.0 3.2 3.3 3.5 3.7 3.8 4.0 4.1 4.3 4.5 4.6 4.8 5.0 5.1 5.3 5.5 5.6 5.8 6.0 6.1 6.3 6.5 6.6 6.8 7.0 7.1 7.3 7.5 7.6 7.8 8.0 8.1 8.3 8.5 8.6 8.8 9.0 9.1 9.3 9.5 9.6 9.8 10.0").split()
MASTER_DB = "-inf -60 -52 -48 -44 -40 -36 -32 -28 -24 -20 -18 -16 -14 -12 -10 -8 -6 -4 -2 0 1 2 3 4 5 6 7 8 9 10".split()
GAIN_DB = [str(i - 15) for i in range(31)]          # TRIM and EQ GAIN: -15..+15 dB
EQ_LOW_HZ = "31.0 33.8 36.8 40.2 43.8 47.8 52.2 56.9 62.1 67.7 73.9 80.6 87.9 95.9 104.6 114.1 124.4 135.7 148.0 161.5 176.1 192.1 209.6 228.6 249.3 272.0 296.7 323.6 353.0 385.0 420.0".split()
EQ_MID_HZ = "260.0 289.2 321.8 358.1 398.5 443.4 493.4 549.0 610.8 679.7 756.3 841.5 936.3 1040 1150 1280 1430 1590 1770 1970 2200 2440 2720 3030 3370 3750 4170 4640 5160 5750 6400".split()
EQ_HIGH_HZ = "1300 1410 1540 1680 1830 2000 2180 2380 2590 2830 3080 3360 3660 4000 4360 4750 5180 5650 6160 6720 7320 7990 8710 9490 10350 11290 12310 13420 14630 15950 17400".split()
PAN = [f"{15-i}L" for i in range(15)] + ["C"] + [f"{i}R" for i in range(1, 16)]
ONOFF = {0: "OFF", 1: "ON"}
MASTER_CH = {0: "MIX-1/2", 1: "MIX-3/4", 2: "AUX-1", 3: "AUX-2"}
MON_SEL = {1: "PGM-L/R", 2: "MIX-L/R", 4: "AUX-1", 5: "AUX-2"}
EFF_NAME = {0x00: "M/E", 0x02: "Aux1", 0x03: "Aux2"}
NEXT_TRANS = {1: "BKGD", 2: "KEY", 3: "BKGD+KEY"}
TRANS_TYPE = {0x02: "MIX", 0x04: "WIPE", 0x08: "NAM", 0x14: "CUT", 0x20: "DME WIPE"}
TRANS_FUNC = {0x00: "Effect", 0x01: "Key", 0x06: "FTB"}

def lut(table, v, unit=""):
    if isinstance(table, dict): return table.get(v, f"unknown(0x{v:02X})")
    return (table[v] + unit) if 0 <= v < len(table) else f"out-of-range({v})"

def bcd(*bs):
    s = "".join(f"{b:02X}" for b in bs)
    return int(s) if s.isdigit() else None

def wipe_dir(b):
    return {"raw": b, "reverse": bool(b & 4), "normal": bool(b & 2), "normal_reverse": bool(b & 1)}

# ---------------------------------------------------------------- request plan
def plan(bc_endian="big"):
    """list of (key, frame, decoder) - decoder(msg) -> dict"""
    P = []
    V = lambda eff, cmd, *a: video_read(eff, cmd, *a, bc_endian=bc_endian)
    A = lambda cmd, data: audio_status(cmd, data, bc_endian=bc_endian)

    # --- video. ANSWER msg = [PROT, count, EFF, CMD|0x80, byte3...]
    def vdec(expect_eff, expect_cmd, fn):
        def d(m):
            if len(m) < 4 or m[0] != PROT or m[2] != expect_eff or m[3] != (expect_cmd | 0x80):
                raise ValueError("unexpected answer")
            return fn(list(m[4:]))
        return d
    # Verified on the real unit (2026-11-21): an AUTO TRANS swaps the sources
    # between the M/E buses, so A is always PROGRAM (on air) and B is PRESET.
    # byte3 is documented as 0x00, but the unit always returns 0x40 on the M/E
    # A-bus: a fixed program-bus marker, it does not move on transition.
    ROLE = {(0x00, 0x00): "program (on air)", (0x00, 0x01): "preset (next)"}
    def xpt_for(role):
        def xpt(b):
            n = b[-1]
            r = {"button": n, "label": f"{n}" + (f" (Shift {n-7})" if n > 7 else "")}
            if role:
                r["role"] = role
            if len(b) >= 2 and b[-2]:
                r["flags_byte3"] = f"0x{b[-2]:02X}"
            return r
        return xpt
    for eff, buses in ((0x00, {0x00: "A_bus", 0x01: "B_bus", 0x07: "key_fill", 0x08: "key_src"}),
                       (0x02, {0x00: "A_bus", 0x01: "B_bus"}),
                       (0x03, {0x00: "A_bus", 0x01: "B_bus"})):
        for c, name in buses.items():
            P.append((f"video.{EFF_NAME[eff]}.xpt.{name}", V(eff, c), vdec(eff, c, xpt_for(ROLE.get((eff, c))))))
    P.append(("video.M/E.next_transition", V(0, 0x10),
              vdec(0, 0x10, lambda b: {"raw": b[0], "value": lut(NEXT_TRANS, b[0])})))
    for tf in (0x00, 0x01):
        P.append((f"video.M/E.transition_type.{TRANS_FUNC[tf]}", V(0, 0x11, tf, 0x00),
                  vdec(0, 0x11, lambda b: {"raw": b[-1], "value": lut(TRANS_TYPE, b[-1])})))
    for eff, funcs in ((0x00, (0x00, 0x01, 0x06)), (0x02, (0x00,)), (0x03, (0x00,))):
        for tf in funcs:
            P.append((f"video.{EFF_NAME[eff]}.transition_rate.{TRANS_FUNC[tf]}", V(eff, 0x18, tf, 0x00),
                      vdec(eff, 0x18, lambda b: {"frames": bcd(b[-2] & 0x0F, b[-1])})))
    pat = lambda b: {"pattern": bcd(b[-2], b[-1])}
    P.append(("video.M/E.wipe_pattern", V(0, 0x1B, 0x00), vdec(0, 0x1B, pat)))
    P.append(("video.M/E.dme_wipe_pattern", V(0, 0x1C, 0x00), vdec(0, 0x1C, pat)))
    P.append(("video.M/E.key_wipe_pattern", V(0, 0x33, 0x00), vdec(0, 0x33, pat)))
    P.append(("video.M/E.key_dme_wipe_pattern", V(0, 0x1C, 0x10), vdec(0, 0x1C, pat)))
    wd = lambda b: wipe_dir(b[-1])
    P.append(("video.M/E.wipe_direction", V(0, 0x1B, 0x04), vdec(0, 0x1B, wd)))
    P.append(("video.M/E.key_wipe_direction", V(0, 0x33, 0x04), vdec(0, 0x33, wd)))
    d1 = lambda b: {"raw": b[-1], "value": {0: "Normal", 1: "Reverse"}.get(b[-1])}
    d2 = lambda b: {"raw": b[-1], "normal_reverse": ONOFF.get(b[-1])}
    P.append(("video.M/E.dme_wipe_direction", V(0, 0x1C, 0x01), vdec(0, 0x1C, d1)))
    P.append(("video.M/E.dme_wipe_normal_reverse", V(0, 0x1C, 0x02), vdec(0, 0x1C, d2)))
    P.append(("video.M/E.key_dme_wipe_direction", V(0, 0x1C, 0x11), vdec(0, 0x1C, d1)))
    P.append(("video.M/E.key_dme_wipe_normal_reverse", V(0, 0x1C, 0x12), vdec(0, 0x1C, d2)))
    # snapshots (E-File 0x21) are write-only in this protocol -> deliberately not touched

    # --- audio. reply msg = [cmd (high bit clear), data...]
    def adec(cmd, ch, fn):
        def d(m):
            if len(m) < 2 or (m[0] & 0x7F) != cmd:
                raise ValueError("unexpected answer")
            if ch is not None and m[1] != ch:
                raise ValueError(f"channel mismatch (got {m[1]})")
            return fn(list(m[1:]))
        return d
    fader_names = ["CH1", "CH2", "CH3", "CH4", "CH5", "CH6", "reserved", "PGM"]
    P.append(("audio.channel_faders", A(0x10, [0] * 8), adec(0x10, None, lambda d: {
        fader_names[i]: {"code": v, "dB": lut(FADER_DB, v)} for i, v in enumerate(d[:8]) if i != 6})))
    for ch, name in MASTER_CH.items():
        P.append((f"audio.master_fader.{name}", A(0x12, [ch, 0]),
                  adec(0x12, ch, lambda d: {"code": d[1], "dB": lut(MASTER_DB, d[1])})))
    per_ch = [
        # Verified on the real unit (2026-11-21): raw 1 = MUTED (CH ON button dark),
        # raw 0 = live (CH ON lit). The manual's "OFF 0 / ON 1" refers to MUTE, not CH ON.
        ("mute",            0x13, lambda v: {"raw": v, "muted": v == 1,
                                             "ch_on_button": {0: "lit (live)", 1: "dark (muted)"}.get(v, f"unknown({v})")}),
        ("trim",            0x14, lambda v: {"code": v, "dB": lut(GAIN_DB, v)}),
        ("low_cut_filter",  0x17, lambda v: {"raw": v, "value": lut(ONOFF, v)}),
        ("high_cut_filter", 0x18, lambda v: {"raw": v, "value": lut(ONOFF, v)}),
        ("eq_low_in",       0x19, lambda v: {"raw": v, "value": lut(ONOFF, v)}),
        ("eq_low_freq",     0x1B, lambda v: {"code": v, "Hz": lut(EQ_LOW_HZ, v)}),
        ("eq_low_gain",     0x1C, lambda v: {"code": v, "dB": lut(GAIN_DB, v)}),
        ("eq_mid_in",       0x1D, lambda v: {"raw": v, "value": lut(ONOFF, v)}),
        ("eq_mid_freq",     0x1E, lambda v: {"code": v, "Hz": lut(EQ_MID_HZ, v)}),
        ("eq_mid_gain",     0x1F, lambda v: {"code": v, "dB": lut(GAIN_DB, v)}),
        ("eq_high_in",      0x20, lambda v: {"raw": v, "value": lut(ONOFF, v)}),
        ("eq_high_freq",    0x22, lambda v: {"code": v, "Hz": lut(EQ_HIGH_HZ, v)}),
        ("eq_high_gain",    0x23, lambda v: {"code": v, "dB": lut(GAIN_DB, v)}),
        ("pan",             0x33, lambda v: {"code": v, "value": lut(PAN, v)}),
    ]
    for ch in range(6):
        for name, cmd, fn in per_ch:
            P.append((f"audio.CH{ch+1}.{name}", A(cmd, [ch, 0]),
                      adec(cmd, ch, (lambda f: lambda d: f(d[1]))(fn))))
    P.append(("audio.monitor.level", A(0x40, [0, 0]),
              adec(0x40, 0, lambda d: {"code": d[1], "dB": lut(FADER_DB, d[1])})))
    P.append(("audio.monitor.dim", A(0x41, [0, 0]),
              adec(0x41, 0, lambda d: {"raw": d[1], "value": lut(ONOFF, d[1])})))
    P.append(("audio.monitor.select", A(0x43, [0, 0]),
              adec(0x43, 0, lambda d: {"raw": d[1], "value": lut(MON_SEL, d[1])})))
    return P

# ---------------------------------------------------------------- run
def describe(f):
    """Best-effort description of a frame that was not a reply to our request."""
    pid, _, _, m = frame_msg(f)
    if pid == PID_VIDEO and len(m) >= 4 and m[0] == PROT:
        eff, cmd, b = m[2], m[3], list(m[4:])
        if cmd in (0x80, 0x81, 0x87, 0x88) and len(b) >= 2:
            bus = {0x80: "A_bus", 0x81: "B_bus", 0x87: "key_fill", 0x88: "key_src"}[cmd]
            return f"{EFF_NAME.get(eff, hex(eff))} {bus} -> button {b[-1]} (changed on panel)"
        if eff == 0x20 and cmd == 0xB7:
            return f"undocumented EFF 0x20 cmd 0xB7 value {b[-1]} (seen as 01/00 around a transition)"
        return f"video EFF 0x{eff:02X} cmd 0x{cmd:02X} data {' '.join(f'{x:02X}' for x in b)}"
    if pid == PID_AUDIO and m:
        return f"audio cmd 0x{m[0] & 0x7F:02X} data {' '.join(f'{x:02X}' for x in m[1:])}"
    return "unknown"

def assert_read_only(frame):
    pid, msg = frame[4], frame[9:-2]
    if pid == PID_VIDEO and msg[3] & 0x80: raise RuntimeError("video WRITE blocked")
    if pid == PID_AUDIO and not msg[0] & 0x80: raise RuntimeError("audio WRITE blocked")

def reply_matches(req, rep):
    """Does reply frame `rep` answer request frame `req`?
    video: same EFF, CMD|0x80, and echoes the request's sub-address bytes.
    audio: same cmd (status bit cleared) and same channel byte."""
    q, r = frame_msg(req)[3], frame_msg(rep)[3]
    if req[4] != rep[4]:
        return False
    if req[4] == PID_VIDEO:
        args = q[4:]
        return len(r) >= 4 + len(args) and r[2] == q[2] and r[3] == (q[3] | 0x80) and r[4:4 + len(args)] == args
    if len(r) < 2 or r[0] != (q[0] & 0x7F):
        return False
    return q[0] == 0x90 or r[1] == q[1]          # channel-fader reply carries all 8 values

def query(port, parser, frame, timeout, retries, discarded):
    """Send `frame`, return (matching reply or None, attempts). Late/duplicate
    replies to earlier requests are dropped into `discarded`, never used."""
    for attempt in range(retries + 1):
        # Drain input. A late reply to THIS request (from the previous attempt)
        # is still valid - only replies to other requests are stale.
        for f in parser.feed(port.read(0)):
            if reply_matches(frame, f):
                return f, attempt
            discarded.append(f)
        port.write(frame)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for f in parser.feed(port.read(max(0.0, deadline - time.monotonic()))):
                if reply_matches(frame, f):
                    return f, attempt + 1
                discarded.append(f)
        time.sleep(0.05)
    return None, retries + 1

def run(args):
    P = plan(args.bc_endian)
    if args.dry_run:
        for key, f, _ in P:
            print(f"{key:45s} {f.hex(' ')}")
        print(f"\n{len(P)} read requests"); return 0
    t_start = time.monotonic()
    port = Port(args.port, args.baud)
    t_open = time.monotonic() - t_start
    parser = FrameParser()
    port.read(0.2); parser.feed(b"")  # drain
    out = {"device": "Sony MCS-8M", "port": args.port, "baud": args.baud,
           "captured_at": datetime.now(timezone.utc).isoformat(),
           "bc_endian": args.bc_endian, "settings": {}, "errors": {}, "raw": {},
           "unsolicited": []}   # status frames the switcher pushed by itself (panel activity during the dump)
    ok = 0; misses = 0; aborted = False; stale = 0
    try:
        for i, (key, frame, dec) in enumerate(P, 1):
            assert_read_only(frame)
            discarded = []
            t0 = time.monotonic()
            reply, attempts = query(port, parser, frame, args.timeout, args.retries, discarded)
            frames = [reply] if reply else []
            rec = {"tx": frame.hex(" "), "rx": reply.hex(" ") if reply else None, "attempts": attempts,
                   "ms": round((time.monotonic() - t0) * 1000)}
            if discarded:
                rec["discarded_unmatched"] = [f.hex(" ") for f in discarded]
                stale += len(discarded)
                for f in discarded:
                    out["unsolicited"].append({"t_s": round(time.monotonic() - t_start, 2),
                                               "during": key, "frame": f.hex(" "), "meaning": describe(f)})
            out["raw"][key] = rec
            if not frames:
                out["errors"][key] = "no response"; status = "TIMEOUT"; misses += 1
            else:
                misses = 0
                try:
                    out["settings"][key] = dec(frame_msg(frames[-1])[3]); ok += 1; status = "ok"
                except Exception as e:
                    out["errors"][key] = f"{e}: {frames[-1].hex(' ')}"; status = "BAD"
            print(f"[{i:3d}/{len(P)}] {status:7s} {key}", file=sys.stderr)
            if args.abort_after and misses >= args.abort_after and ok == 0:
                print(f"aborting: first {misses} requests got no reply", file=sys.stderr)
                aborted = True; break
            time.sleep(args.gap)
    finally:
        port.close()
    out["summary"] = {"requests": len(P), "decoded": ok, "failed": len(P) - ok, "aborted": aborted,
                      "port_open_ms": round(t_open * 1000), "run_s": round(time.monotonic() - t_start, 1),
                      "slowest_reply_ms": max((r["ms"] for r in out["raw"].values() if r["rx"]), default=None),
                      "unmatched_replies_discarded": stale, "unsolicited_frames": len(out["unsolicited"]), "stray_acks": parser.acks, "junk_bytes": parser.junk.hex(" ")}
    path = args.output or datetime.now().strftime("mcs8m_dump_%Y%m%d_%H%M%S.json")  # local time
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"\n{ok}/{len(P)} settings decoded -> {path}", file=sys.stderr)
    if ok == 0:
        print("Nothing answered. Check: Node-RED stopped? cross (null-modem) cable? "
              "Serial Cmd Group = Rem 1? 38400 8N1?", file=sys.stderr)
    print(json.dumps({"output": os.path.abspath(path), **{k: out["summary"][k] for k in
                      ("requests", "decoded", "failed", "aborted")}}))   # one line for Node-RED
    return 0 if ok else 2

def selftest():
    """Rebuild every READ/WRITE example from the manual and compare byte-for-byte."""
    ex = [  # (pid, tid, msg, expected) - examples copied from the manual
        (1, 0x20, [3, 4, 0, 0x80, 0, 1], "01 02 00 0b 01 50 20 00 00 03 04 00 80 00 01 fc 03"),
        (1, 0x20, [3, 6, 0, 0x98, 0, 0, 1, 0x17], "01 02 00 0d 01 50 20 00 00 03 06 00 98 00 00 01 17 c9 03"),
        (1, 0x20, [3, 5, 0x21, 0x90, 1, 0, 0], "01 02 00 0c 01 50 20 00 00 03 05 21 90 01 00 00 c9 03"),
        (2, 0x30, [0x97, 0, 1], "01 02 00 08 02 50 30 00 00 97 00 01 de 03"),
        (2, 0x30, [0x90] + [0] * 8, "01 02 00 0e 02 50 30 00 00 90 00 00 00 00 00 00 00 00 e0 03"),
        (2, 0x30, [0x43, 0, 1], "01 02 00 08 02 50 30 00 00 43 00 01 32 03"),
    ]
    bad = 0
    for pid, tid, msg, exp in ex:
        got = build_frame(pid, tid, msg).hex(" ")
        flag = "OK " if got == exp else "FAIL"; bad += got != exp
        print(f"{flag} {got}")
    le = build_frame(1, 0x20, [3, 4, 0, 0x80, 0, 1], "little").hex(" ")
    print(f"\nlittle-endian BC (as the manual's table says) would give:\n     {le}  <- does NOT match manual examples")
    p = FrameParser()
    stream = bytes([ACK]) + bytes.fromhex("01 02 00 0b 01 50 20 00 00 03 04 03 80 00 03 f7 03")
    fr = p.feed(stream[:7]) + p.feed(stream[7:])
    print(f"parser (ETX-in-data, split delivery, leading ACK): {'OK' if len(fr) == 1 and p.acks == 1 else 'FAIL'}")
    bad += not (len(fr) == 1 and p.acks == 1)
    req = video_read(0, 0x01); good = build_frame(1, 0x20, [3, 4, 0, 0x81, 0, 2]); stale = build_frame(1, 0x20, [3, 4, 0, 0x80, 0x40, 7])
    rq2 = video_read(0, 0x18, 1, 0); wrongtf = build_frame(1, 0x20, [3, 6, 0, 0x98, 0, 0, 0, 0x30])
    aq = audio_status(0x14, [2, 0]); wrongch = build_frame(2, 0x20, [0x14, 1, 15])
    m_ok = reply_matches(req, good) and not reply_matches(req, stale) and not reply_matches(rq2, wrongtf) \
        and not reply_matches(aq, wrongch) and reply_matches(aq, build_frame(2, 0x30, [0x14, 2, 15]))
    print(f"reply matching (stale/wrong-func/wrong-channel rejected): {'OK' if m_ok else 'FAIL'}"); bad += not m_ok
    for _, f, _ in plan(): assert_read_only(f)
    print(f"read-only guard: OK ({len(plan())} requests)")
    return 1 if bad else 0

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Dump all readable Sony MCS-8M settings to JSON (read-only).")
    ap.add_argument("-p", "--port", default="/dev/ttyUSB0")
    ap.add_argument("-b", "--baud", type=int, default=38400)
    ap.add_argument("-o", "--output")
    ap.add_argument("--timeout", type=float, default=2.0, help="seconds to wait per reply (video replies have taken >1s)")
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--gap", type=float, default=0.03, help="pause between requests (s)")
    ap.add_argument("--bc-endian", choices=["big", "little"], default="big",
                    help="BC byte order. 'big' is correct; 'little' only to prove the manual's error")
    ap.add_argument("--abort-after", type=int, default=5,
                    help="give up if this many requests in a row get no reply before any success (0 = never)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    sys.exit(selftest() if a.selftest else run(a))
