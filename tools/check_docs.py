#!/usr/bin/env python3
"""Check every hex frame quoted in the docs: big-endian BC, length, checksum, ETX.
Run from anywhere: python3 tools/check_docs.py  (exit code 1 on any bad frame)."""
import glob, os, re, sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAME = re.compile(r"\b01 02 (?:\|\s*)?(?:[0-9A-Fa-f]{2}(?:\s*\|\s*|\s)){4,}[0-9A-Fa-f]{2}\b")
bad = total = 0
for path in sorted(glob.glob(os.path.join(root, "docs", "*.md")) + [os.path.join(root, "README.md")]):
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        for m in FRAME.finditer(line):
            b = bytes.fromhex(m.group(0).replace("|", " "))
            total += 1
            problems = []
            bc = (b[2] << 8) | b[3]
            if len(b) != bc + 6: problems.append(f"length {len(b)} != BC {bc} + 6")
            if b[-1] != 0x03: problems.append("last byte is not ETX 03")
            if sum(b[2:-1]) & 0xFF: problems.append(f"checksum wrong (should be {(-sum(b[2:-2])) & 0xFF:02X})")
            if problems:
                bad += 1
                print(f"{os.path.relpath(path, root)}:{n}: {m.group(0)}\n    " + "; ".join(problems))
print(f"{total} frames checked, {bad} bad")
sys.exit(1 if bad else 0)
