#!/usr/bin/env python3
"""
companion_commands.py - print ready-to-paste hex strings for Bitfocus Companion's
"Generic TCP/UDP: Send HEX encoded Command" action (no spaces - Companion stops
decoding at the first space).  See docs/09-bitfocus-companion.md.

  python3 tools/companion_commands.py aux 1 6         # Aux 1 -> input 6
  python3 tools/companion_commands.py program 3       # cut input 3 to program (on air)
  python3 tools/companion_commands.py preset 2
  python3 tools/companion_commands.py auto 30         # AUTO TRANS, 30 frames (0 = cut)
  python3 tools/companion_commands.py ftb 30          # fade to black
  python3 tools/companion_commands.py keyauto 30      # key transition
  python3 tools/companion_commands.py mute 2 on       # CH2 mute on (CH ON dark)
  python3 tools/companion_commands.py recall 4        # recall snapshot 4
  python3 tools/companion_commands.py --table         # every command, as Markdown
"""
import sys


def frame(pid, tid, msg):
    payload = bytes([pid, 0x50, tid, 0, 0]) + bytes(msg)
    body = len(payload).to_bytes(2, "big") + payload           # BC is big-endian
    return bytes([0x01, 0x02]) + body + bytes([(-sum(body)) & 0xFF, 0x03])


def video(eff, cmd, *args):
    return frame(0x01, 0x20, [0x03, 2 + len(args), eff, cmd, *args])


def audio(cmd, *data):
    return frame(0x02, 0x30, [cmd, *data])


def bcd(frames):
    if not 0 <= frames <= 999:
        raise ValueError("rate must be 0-999 frames")
    return (frames // 100), ((frames // 10 % 10) << 4) | (frames % 10)


def build(kind, *a):
    """Return the frame for one command. All of these are WRITES."""
    if kind == "aux":
        aux, src = int(a[0]), int(a[1])
        assert aux in (1, 2) and 1 <= src <= 14, "aux 1|2 <input 1-14>"
        return video(aux + 1, 0x80, 0x00, src)
    if kind in ("program", "preset"):
        src = int(a[0]); assert 1 <= src <= 14, "input 1-14"
        return video(0x00, 0x80 if kind == "program" else 0x81, 0x00, src)
    if kind in ("auto", "ftb", "keyauto"):
        fn = {"auto": 0x00, "keyauto": 0x01, "ftb": 0x06}[kind]
        return video(0x00, 0x96, fn, *bcd(int(a[0]) if a else 30))
    if kind == "mute":
        ch, state = int(a[0]), a[1].lower()
        assert 1 <= ch <= 6 and state in ("on", "off"), "mute <ch 1-6> on|off"
        return audio(0x13, ch - 1, 1 if state == "on" else 0)          # 1 = muted
    if kind == "recall":
        reg = int(a[0]); assert 1 <= reg <= 20, "snapshot 1-20"
        return video(0x21, 0x90, reg, 0x00, 0x00)                     # 0x90 = RECALL (0x80 would STORE)
    raise ValueError(f"unknown command {kind!r}")


def table():
    rows = []
    add = lambda what, f, status: rows.append(f"| {what} | `{f.hex()}` | {status} |")
    print("| Button | HEX command (paste exactly, no spaces) | Status |\n|---|---|---|")
    for aux in (1, 2):
        for src in range(1, 9):
            add(f"Aux {aux} → input {src}", build("aux", aux, src), "✅ tested")
    for src in range(1, 9):
        add(f"Program (cut) → input {src}", build("program", src), "📄")
    for src in range(1, 9):
        add(f"Preset → input {src}", build("preset", src), "📄")
    add("CUT", build("auto", 0), "📄")
    for r in (15, 30, 60):
        add(f"AUTO TRANS, {r} frames", build("auto", r), "📄")
    add("Key transition, 30 frames", build("keyauto", 30), "📄")
    add("Fade to black, 30 frames", build("ftb", 30), "📄")
    for ch in range(1, 7):
        add(f"CH{ch} mute (CH ON dark)", build("mute", ch, "on"), "📄")
        add(f"CH{ch} unmute (CH ON lit)", build("mute", ch, "off"), "📄")
    for reg in range(1, 11):
        add(f"Recall snapshot {reg}", build("recall", reg), "📄")
    print("\n".join(rows))


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        sys.exit(__doc__)
    if args[0] == "--table":
        table()
    else:
        try:
            print(build(*args).hex())
        except (AssertionError, ValueError, IndexError) as e:
            sys.exit(f"error: {e}\n{__doc__}")
