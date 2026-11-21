"""
mcs8m.py - a small, dependency-free Python library for the Sony MCS-8M RS-232 protocol.

Python 3.7+. Linux/macOS: no dependencies (uses termios).
Windows: py -m pip install pyserial, then use a port name like "COM3".

    from mcs8m import MCS8M
    with MCS8M("/dev/ttyUSB0") as sw:
        print("on air:", sw.program())
        print("faders:", sw.faders())

Writes (anything that changes the switcher) are refused unless you open with
allow_writes=True.  See ../../docs/ for the protocol itself.
"""
import os, select, time

DEL, STX, ETX, ACK = 0x01, 0x02, 0x03, 0x04
FID = 0x50
VIDEO, AUDIO = (0x01, 0x20), (0x02, 0x30)          # (PID, TID)
PROT = 0x03

ME, AUX1, AUX2, EFILE = 0x00, 0x02, 0x03, 0x21     # video sections (EFF)
BUS_A, BUS_B, KEY_FILL, KEY_SRC = 0x00, 0x01, 0x07, 0x08


class WritesDisabled(Exception):
    pass


class NoReply(Exception):
    pass


# --------------------------------------------------------------------- frames
def build_frame(pid_tid, msg):
    """Wrap MSG bytes in DEL STX BC(big-endian!) PID FID TID ARQ ART ... CS ETX."""
    pid, tid = pid_tid
    payload = bytes([pid, FID, tid, 0x00, 0x00]) + bytes(msg)
    body = len(payload).to_bytes(2, "big") + payload           # BC is BIG-endian
    return bytes([DEL, STX]) + body + bytes([(-sum(body)) & 0xFF, ETX])


def video_msg(eff, cmd, *args):
    return [PROT, 2 + len(args), eff, cmd, *args]


def audio_msg(cmd, *data):
    return [cmd, *data]


class FrameParser:
    """Feed raw bytes, get complete frames back. Frames by BC, never by 0x03."""

    def __init__(self):
        self.buf = bytearray()
        self.acks = 0

    def feed(self, data):
        self.buf += data
        frames = []
        while self.buf:
            if self.buf[0] != DEL:                 # not a frame start
                if self.buf.pop(0) == ACK:
                    self.acks += 1
                continue
            if len(self.buf) < 4:
                break
            bc = (self.buf[2] << 8) | self.buf[3]
            if self.buf[1] != STX or not 5 <= bc <= 512:
                self.buf.pop(0)                    # resync
                continue
            if len(self.buf) < bc + 6:
                break                              # wait for the rest
            f = bytes(self.buf[:bc + 6])
            if f[-1] == ETX and sum(f[2:-1]) & 0xFF == 0:
                del self.buf[:bc + 6]
                frames.append(Frame(f))
            else:
                self.buf.pop(0)                    # bad checksum: resync
        return frames


class Frame:
    """A received frame, with the useful fields pulled out."""

    def __init__(self, raw):
        self.raw = raw
        self.pid = raw[4]
        self.msg = raw[9:-2]
        if self.pid == VIDEO[0]:
            self.kind = "video"
            self.eff, self.cmd, self.data = self.msg[2], self.msg[3] & 0x7F, self.msg[4:]
        else:
            self.kind = "audio"
            self.eff, self.cmd, self.data = None, self.msg[0] & 0x7F, self.msg[1:]

    def __repr__(self):
        where = f"EFF {self.eff:02X} " if self.kind == "video" else ""
        return f"<{self.kind} {where}CMD {self.cmd:02X} data {self.data.hex(' ')}>"


# --------------------------------------------------------------------- serial port
class _PosixPort:
    """Linux/macOS: 38400 8N1, raw, no flow control - standard library only."""

    def __init__(self, path, baud=38400):
        import termios
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        a = termios.tcgetattr(self.fd)
        a[0] = a[1] = a[3] = 0                                     # raw in/out, no echo
        a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL        # 8N1, no RTS/CTS
        a[4] = a[5] = getattr(termios, f"B{baud}")
        a[6][termios.VMIN] = a[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, a)
        termios.tcflush(self.fd, termios.TCIOFLUSH)

    def write(self, data):
        os.write(self.fd, data)

    def read(self, timeout):
        ready, _, _ = select.select([self.fd], [], [], max(0.0, timeout))
        return os.read(self.fd, 4096) if ready else b""

    def close(self):
        os.close(self.fd)


class _PySerialPort:
    """Windows, or anywhere pyserial is installed (pip install pyserial). Ports like COM3."""

    def __init__(self, path, baud=38400):
        try:
            import serial
        except ImportError:
            raise SystemExit("On Windows this needs pyserial:  py -m pip install pyserial")
        self.s = serial.Serial(path, baud, bytesize=8, parity="N", stopbits=1, timeout=0,
                               xonxoff=False, rtscts=False, dsrdtr=False)
        self.s.reset_input_buffer()

    def write(self, data):
        self.s.write(data)

    def read(self, timeout):
        self.s.timeout = max(0.0, timeout)
        first = self.s.read(1)                       # wait up to `timeout` for the first byte
        if not first:
            return b""
        n = self.s.in_waiting
        return first + (self.s.read(n) if n else b"")

    def close(self):
        self.s.close()


def SerialPort(path, baud=38400):
    """Open a port: termios on Linux/macOS, pyserial on Windows.
    Set the environment variable MCS8M_SERIAL=pyserial to use pyserial anywhere."""
    if os.name == "nt" or os.environ.get("MCS8M_SERIAL") == "pyserial":
        return _PySerialPort(path, baud)
    return _PosixPort(path, baud)


# --------------------------------------------------------------------- switcher
class MCS8M:
    def __init__(self, port, allow_writes=False, timeout=2.0):
        self.port = SerialPort(port) if isinstance(port, str) else port
        self.allow_writes = allow_writes
        self.timeout = timeout          # video replies take ~1.01 s - keep this >= 1.5
        self.parser = FrameParser()
        self.events = []                # frames nobody asked for (panel changes etc.)
        self._recent = {}               # raw reply -> time, to drop the unit's duplicates

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.port.close()

    # ---- low level
    def _receive(self, timeout):
        return self.parser.feed(self.port.read(timeout))

    def _event(self, f):
        """Queue a frame nobody asked for - unless it repeats a recent reply.
        (The unit sometimes sends a reply twice, ~1.5 s apart.)"""
        now = time.monotonic()
        self._recent = {r: t for r, t in self._recent.items() if now - t < 3.0}
        if f.raw not in self._recent:
            self.events.append(f)

    def _request(self, frame, matches):
        self.port.write(frame)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            for f in self._receive(deadline - time.monotonic()):
                if matches(f):
                    self._recent[f.raw] = time.monotonic()
                    return f
                self._event(f)                            # not ours: keep it as an event
        raise NoReply(f"no reply to {frame.hex(' ')}")

    def read_video(self, eff, cmd, *args):
        """Send a video READ; return the reply's data bytes (after CMD)."""
        if cmd & 0x80:
            raise ValueError("video codes with the high bit set are WRITES")
        f = self._request(build_frame(VIDEO, video_msg(eff, cmd, *args)),
                          lambda r: r.kind == "video" and r.eff == eff and r.cmd == cmd
                          and r.data[:len(args)] == bytes(args))
        return f.data

    def read_audio(self, cmd, channel=0, *extra):
        """Send an audio STATUS request; return the reply's data bytes."""
        data = [channel, *extra] if extra else [channel, 0]
        f = self._request(build_frame(AUDIO, audio_msg(cmd | 0x80, *data)),
                          lambda r: r.kind == "audio" and r.cmd == cmd
                          and (cmd == 0x10 or r.data[:1] == bytes([channel])))
        return f.data

    def write_video(self, eff, cmd, *args):
        if not self.allow_writes:
            raise WritesDisabled("open with allow_writes=True to change the switcher")
        return self._request(build_frame(VIDEO, video_msg(eff, cmd | 0x80, *args)),
                             lambda r: r.kind == "video" and r.eff == eff and r.cmd == cmd & 0x7F).data

    def listen(self, seconds):
        """Collect unsolicited frames for a while (plus any already queued)."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            for f in self._receive(end - time.monotonic()):
                self._event(f)
        out, self.events = self.events, []
        return out

    # ---- friendly helpers
    def xpt(self, eff, bus):
        """Crosspoint button (1-14) on a bus. Ignores the flag byte (0x40/0x80)."""
        return self.read_video(eff, bus)[1]

    def program(self):
        return self.xpt(ME, BUS_A)          # the M/E A-bus is always on air

    def preset(self):
        return self.xpt(ME, BUS_B)

    def aux(self, n):
        return self.xpt(AUX1 if n == 1 else AUX2, BUS_A)

    def set_aux(self, n, button):
        """WRITE: route crosspoint button (1-14) to Aux 1 or 2."""
        if not 1 <= button <= 14:
            raise ValueError("button must be 1-14")
        return self.write_video(AUX1 if n == 1 else AUX2, BUS_A, 0x00, button)

    def faders(self):
        """Raw fader codes: dict CH1..CH6, PGM (0 = -inf, 194 = 0 dB, 255 = +10 dB)."""
        d = self.read_audio(0x10, *[0] * 8)
        return {**{f"CH{i + 1}": d[i] for i in range(6)}, "PGM": d[7]}

    def muted(self, channel):
        """True if CH(channel+1) is muted. NB: raw 1 = muted (the manual is misleading)."""
        return self.read_audio(0x13, channel)[1] == 1


def fader_db(code):
    """Approximate dB for a fader code. Exact table: ../../tools/values.json."""
    import json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "values.json")
    with open(path) as f:
        return json.load(f)["channel_fader_db"][code]
