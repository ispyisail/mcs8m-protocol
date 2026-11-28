"""
vmix.py - vMix connector for the MCS-8M bridge (vMix TCP API, port 8099).

Switcher leads: when the Sony's program changes, show a lower third with the
input's name (SetText + OverlayInputNIn, hidden again after a few seconds) and/or
run any vMix functions you list per input.
vMix leads: when vMix's program input changes, cut the Sony to the mapped input.
Both: either side, with echo suppression so changes don't bounce back and forth.

Loop guard: vMix's program input is reported to the bridge, which refuses any
change that would show the vMix return on the Sony while vMix shows the Sony.
"""
import socket, threading, time, queue
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

ECHO_SECONDS = 3.0
CUT_FUNCTIONS = {"cutdirect", "cut", "fade", "merge", "wipe", "zoom", "stinger1", "stinger2"}


def clean(d):
    """Drop _comment keys from a config dict."""
    return {k: v for k, v in (d or {}).items() if not str(k).startswith("_")}


class VmixConnector:
    def __init__(self, bridge, cfg, log=print):
        self.b, self.log = bridge, log
        self.cfg = clean(cfg)
        self.host, self.port = self.cfg.get("host", "127.0.0.1"), int(self.cfg.get("port", 8099))
        self.lt = clean(self.cfg.get("lower_third"))
        self.on_program = clean(self.cfg.get("on_program"))
        ctl = clean(self.cfg.get("control"))
        self.vmix_to_sony = {str(k): int(v) for k, v in clean(ctl.get("vmix_to_switcher")).items()}
        self.transition = ctl.get("transition", "cut")
        self.sendq = queue.Queue()
        self.expected = []                  # [vmix input, deadline] for changes we caused
        self.program = self.preview = None
        self.input_numbers = {}             # vMix input title / short title / key (lower case) -> number
        self.xml_ready = False              # this connection's first XML has been read
        self.hide_timer = None
        self.sock = None
        bridge.register_app("vmix", self.cfg.get("mode", "switcher_leads"))
        bridge.listeners.append(self.on_bridge_event)

    def mode(self):
        return self.b.apps["vmix"]["mode"]

    def start(self):
        threading.Thread(target=self.run, name="vmix", daemon=True).start()
        return self

    # ------------------------------------------------------------ Sony -> vMix
    def on_bridge_event(self, ev):
        if ev.get("type") != "change" or ev.get("key") != "program" or ev.get("value") is None:
            return
        if self.mode() == "off":
            return
        # the lower third is harmless, so it follows every program change; the extra actions
        # only run when the Sony leads, and never for changes vMix itself caused (echo)
        run_actions = ev.get("origin") != "vmix" and self.mode() in ("switcher_leads", "both")
        first = ev.get("old") is None                           # the bridge's first reading, not a real cut
        self.sendq.put(("follow", (ev["value"], run_actions, first)))

    def follow(self, item):
        n, run_actions, first = item
        name = self.b.name(n)
        ret = (self.b.cfg.get("loop_protection") or {}).get("switcher_input_from_vmix")
        if self.lt.get("enabled") and str(n) != str(ret) and not first:
            only = self.lt.get("for_inputs") or []
            if not only or n in only:
                self.show_lower_third(name)
        if not run_actions:
            return
        for action in (self.on_program.get(str(n)) or []) + (self.on_program.get("*") or []):
            a = clean(action)
            fn = a.pop("function", None) or a.pop("Function", None)
            if not fn:
                continue
            params = {k: str(v).replace("{input}", str(n)).replace("{name}", name) for k, v in a.items()}
            if fn.lower() in CUT_FUNCTIONS and "Input" in params:
                # vMix reports its program as a number, so the loop guard and the echo check
                # need the number even when the action names the input.
                number = self.input_number(params["Input"])
                try:
                    if number is None and self.loop_protection_on():
                        raise ValueError(f"refused: vMix input '{params['Input']}' isn't known yet, so the "
                                         f"video-loop guard can't check it")
                    self.b.guard_vmix(number if number is not None else params["Input"])
                except Exception as e:
                    self.log(f"vmix: {e}")
                    self.b.emit({"type": "refused", "app": "vmix", "error": str(e)})
                    continue
                self.expect(number if number is not None else params["Input"])
            self.function(fn, **params)

    def input_number(self, ref):
        """vMix input number for an Input given by number, title, short title or key; None if unknown."""
        ref = str(ref).strip()
        if ref.isdigit():
            return ref
        return self.input_numbers.get(ref.lower())

    def loop_protection_on(self):
        lp = self.b.cfg.get("loop_protection") or {}
        return lp.get("switcher_input_from_vmix") is not None and lp.get("vmix_input_from_switcher") is not None

    def show_lower_third(self, text):
        inp, ov = self.lt.get("input", "Lower Third"), int(self.lt.get("overlay", 1))
        self.function("SetText", Input=inp, SelectedName=self.lt.get("field", "Headline.Text"), Value=text)
        self.function(f"OverlayInput{ov}In", Input=inp)
        secs = float(self.lt.get("seconds", 6))
        if self.hide_timer:
            self.hide_timer.cancel()
        if secs > 0:
            self.hide_timer = threading.Timer(secs, lambda: self.sendq.put(("raw", f"FUNCTION OverlayInput{ov}Out")))
            self.hide_timer.daemon = True
            self.hide_timer.start()

    def function(self, name, **params):
        q = urlencode(params)
        self.sendq.put(("raw", f"FUNCTION {name}" + (f" {q}" if q else "")))

    def expect(self, vmix_input):
        self.expected.append([str(vmix_input), time.monotonic() + ECHO_SECONDS])

    # ------------------------------------------------------------ vMix -> Sony
    def on_vmix_program(self, active, preview):
        changed = active != self.program
        self.program, self.preview = active, preview
        self.b.update_app("vmix", program=active, preview=preview)
        if not changed or active is None:
            return
        now = time.monotonic()
        self.expected = [e for e in self.expected if e[1] > now]
        mine = next((e for e in self.expected if e[0] == active), None)
        if mine:
            self.expected.remove(mine)
            return                                              # the change we just asked for
        if self.mode() not in ("app_leads", "both"):
            return
        target = self.vmix_to_sony.get(active)
        if target is None:
            return
        try:
            if self.transition == "auto":
                self.b.preset(target, origin="vmix")
                self.b.auto(int(clean(self.cfg.get("control")).get("auto_frames", 30)), origin="vmix")
            else:
                self.b.program(target, origin="vmix")
            self.log(f"vmix: program {active} -> switcher input {target} ({self.transition})")
        except Exception as e:
            self.log(f"vmix: {e}")
            self.b.emit({"type": "refused", "app": "vmix", "error": str(e)})

    # ------------------------------------------------------------ connection
    def run(self):
        while not self.b.stop.is_set():
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=5)
            except OSError as e:
                self.b.update_app("vmix", connected=False)
                self.log(f"vmix: can't connect to {self.host}:{self.port} ({e}) - retrying in 5 s")
                self.b.stop.wait(5)
                continue
            self.log(f"vmix: connected to {self.host}:{self.port}")
            self.b.update_app("vmix", connected=True)
            try:
                self.session()
            except (OSError, ValueError) as e:
                self.log(f"vmix: connection lost ({e})")
            finally:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.b.update_app("vmix", connected=False)
            self.b.stop.wait(2)

    def session(self):
        s = self.sock
        s.settimeout(0.1)
        # Names must map to this vMix's current inputs: forget the old ones, and hold Sony changes
        # until the first XML of this connection is read (named cut actions need it).
        self.input_numbers, self.xml_ready = {}, False
        s.sendall(b"SUBSCRIBE TALLY\r\nXML\r\n")
        buf, xml_wait, last_xml = b"", None, time.monotonic()
        while not self.b.stop.is_set():
            held = []
            while True:                                        # outgoing
                try:
                    kind, item = self.sendq.get_nowait()
                except queue.Empty:
                    break
                if kind == "follow":
                    if self.xml_ready:
                        self.follow(item)
                    else:
                        held.append((kind, item))
                else:
                    s.sendall(item.encode("utf-8") + b"\r\n")
            for h in held:
                self.sendq.put(h)
            if time.monotonic() - last_xml > 10:               # safety-net refresh
                s.sendall(b"XML\r\n"); last_xml = time.monotonic()
            try:
                chunk = s.recv(65536)
                if not chunk:
                    raise OSError("closed by vMix")
                buf += chunk
            except socket.timeout:
                pass
            while True:                                        # incoming
                if xml_wait is not None:
                    if len(buf) < xml_wait:
                        break
                    data, buf, xml_wait = buf[:xml_wait], buf[xml_wait:], None
                    self.parse_xml(data); last_xml = time.monotonic()
                    continue
                if b"\r\n" not in buf:
                    break
                line, buf = buf.split(b"\r\n", 1)
                line = line.decode("utf-8", "replace").strip()
                if line.startswith("XML ") and line[4:].isdigit():
                    xml_wait = int(line[4:])
                elif line.startswith("TALLY OK"):
                    s.sendall(b"XML\r\n")                      # tally also marks overlays: ask for the real program input
                elif line.startswith("FUNCTION ER"):
                    self.log(f"vmix: {line}")
                    self.b.emit({"type": "app_error", "app": "vmix", "error": line})

    def parse_xml(self, data):
        try:
            root = ET.fromstring(data.decode("utf-8", "replace"))
        except ET.ParseError as e:
            self.log(f"vmix: bad XML ({e})")
            return
        inputs = root.find("inputs")
        if inputs is not None:
            names = {}
            for i in inputs.findall("input"):
                number = (i.get("number") or "").strip()
                for name in (i.get("title"), i.get("shortTitle"), i.get("key")):
                    if number and name and name.strip().lower() not in names:
                        names[name.strip().lower()] = number
            self.input_numbers = names
        self.xml_ready = True
        active, preview = root.findtext("active"), root.findtext("preview")
        self.on_vmix_program(active.strip() if active else None, preview.strip() if preview else None)
