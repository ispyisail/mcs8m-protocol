#!/usr/bin/env python3
"""
loopback_test.py - check a serial adapter and cable WITHOUT the switcher.

Disconnect the switcher, connect pin 2 to pin 3 at the far end of the cable,
then:   python3 tools/loopback_test.py /dev/ttyUSB0

Sends all 256 byte values at 38400 8N1 and checks they come back unchanged.
Never run this with the switcher connected: it sends arbitrary bytes.
"""
import os, select, sys, termios, time

def open_port(path, baud=38400):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    a[0] = a[1] = a[3] = 0
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    a[4] = a[5] = getattr(termios, f"B{baud}")
    a[6][termios.VMIN] = a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd

def main():
    if len(sys.argv) != 2 or sys.argv[1].startswith("-"):
        sys.exit(__doc__)
    path = sys.argv[1]
    try:
        fd = open_port(path)
    except PermissionError:
        sys.exit(f"FAIL: permission denied on {path}. Add yourself to 'dialout' "
                 "(sudo usermod -aG dialout $USER), then log in again.")
    except OSError as e:
        sys.exit(f"FAIL: can't open {path}: {e.strerror}. Check the name with: ls -l /dev/serial/by-id/")
    sent = bytes(range(256))
    os.write(fd, sent)
    got, end = b"", time.monotonic() + 1.0          # 256 bytes take ~67 ms at 38400
    while len(got) < len(sent) and time.monotonic() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            got += os.read(fd, 1024)
    os.close(fd)
    if got == sent:
        print("PASS: 256/256 bytes came back unchanged at 38400 8N1.")
        return
    if not got:
        sys.exit("FAIL: nothing came back. Is pin 2 joined to pin 3 at the cable end? Right port? "
                 "Is it a real RS-232 adapter (not a TTL/3.3 V cable)?")
    same = sum(a == b for a, b in zip(sent, got))
    sys.exit(f"FAIL: {len(got)}/256 bytes came back, {same} unchanged. "
             "Usually a TTL adapter, a bad connection or electrical noise.")

if __name__ == "__main__":
    main()
