#!/usr/bin/env python3
"""
loopback_test.py - check a serial adapter and cable WITHOUT the switcher.

Disconnect the switcher, connect pin 2 to pin 3 at the far end of the cable,
then:   python3 tools/loopback_test.py /dev/ttyUSB0
        py tools\\loopback_test.py COM3            (Windows: py -m pip install pyserial)

Sends all 256 byte values at 38400 8N1 and checks they come back unchanged.
Never run this with the switcher connected: it sends arbitrary bytes.
"""
import os, select, sys, time

def open_port(path, baud=38400):
    """Returns (write, read_some, close). termios on Linux/macOS, pyserial on Windows."""
    if os.name == "nt" or os.environ.get("MCS8M_SERIAL") == "pyserial":
        try:
            import serial
        except ImportError:
            sys.exit("This needs pyserial on Windows:  py -m pip install pyserial")
        try:
            s = serial.Serial(path, baud, bytesize=8, parity="N", stopbits=1, timeout=0.05,
                              xonxoff=False, rtscts=False, dsrdtr=False)
        except serial.SerialException as e:
            raise OSError(str(e))
        s.reset_input_buffer()
        return s.write, lambda: s.read(1024), s.close
    import termios
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    a[0] = a[1] = a[3] = 0
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    a[4] = a[5] = getattr(termios, f"B{baud}")
    a[6][termios.VMIN] = a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)
    termios.tcflush(fd, termios.TCIOFLUSH)

    def read_some():
        r, _, _ = select.select([fd], [], [], 0.05)
        return os.read(fd, 1024) if r else b""
    return (lambda b: os.write(fd, b)), read_some, (lambda: os.close(fd))

def main():
    if len(sys.argv) != 2 or sys.argv[1].startswith("-"):
        sys.exit(__doc__)
    path = sys.argv[1]
    try:
        write, read_some, close = open_port(path)
    except PermissionError:
        sys.exit(f"FAIL: permission denied on {path}. Add yourself to 'dialout' "
                 "(sudo usermod -aG dialout $USER), then log in again.")
    except OSError as e:
        sys.exit(f"FAIL: can't open {path}: {getattr(e, 'strerror', None) or e}. "
                 "Check the name (Linux: ls -l /dev/serial/by-id/  Windows: Device Manager > Ports).")
    sent = bytes(range(256))
    write(sent)
    got, end = b"", time.monotonic() + 1.0          # 256 bytes take ~67 ms at 38400
    while len(got) < len(sent) and time.monotonic() < end:
        got += read_some()
    close()
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
