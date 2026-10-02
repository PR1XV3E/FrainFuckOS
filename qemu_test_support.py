"""Shared QEMU monitor helpers; base Machine targets the archived native OS."""
import socket
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QEMU = Path(r"D:\msys2\ucrt64\bin\qemu-system-x86_64.exe")


class Machine:
    def __init__(self, data_disk=None, readonly=False):
        self.data_disk = data_disk
        self.readonly = readonly

    def __enter__(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        args = [str(QEMU), "-drive", f"format=raw,file={ROOT / 'legacy-native' / 'bfos.img'},if=floppy,index=0",
                "-boot", "a", "-display", "none", "-monitor",
                f"tcp:127.0.0.1:{port},server=on,wait=off"]
        if self.data_disk is not None:
            option = ",readonly=on" if self.readonly else ""
            args += ["-drive", f"format=raw,file={self.data_disk},if=floppy,index=1{option}"]
        self.process = subprocess.Popen(args,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            time.sleep(1.5)
            self.monitor = socket.create_connection(("127.0.0.1", port))
            self.monitor.settimeout(10)
            self._response()
            self.wait_for("BFOS v0.3 - persistent workstation")
        except BaseException:
            self.process.terminate()
            self.process.wait(timeout=5)
            raise
        return self

    def _response(self):
        response = b""
        while b"(qemu)" not in response:
            chunk = self.monitor.recv(8192)
            if not chunk:
                raise ConnectionError("QEMU monitor closed")
            response += chunk
        return response

    def command(self, text):
        self.monitor.sendall((text + "\n").encode())
        return self._response()

    def key(self, key):
        self.command(f"sendkey {key} 20")
        time.sleep(0.035)

    def text(self, text):
        shifted = {"+": "equal", "*": "8", ">": "dot", "<": "comma",
                   "!": "1", "_": "minus", ":": "semicolon", "?": "slash"}
        normal = {" ": "spc", "\r": "ret", "\n": "ret", "\b": "backspace",
                  ".": "dot", "-": "minus", "/": "slash", "=": "equal",
                  "[": "bracket_left", "]": "bracket_right", ",": "comma"}
        for char in text:
            if char in shifted:
                self.key("shift-" + shifted[char])
            elif char.isupper():
                self.key("shift-" + char.lower())
            else:
                self.key(normal.get(char, char))

    def line(self, text):
        self.text(text)
        self.key("ret")
        time.sleep(0.12)

    def vga(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vga.bin"
            self.command(f'pmemsave 0xb8000 4000 "{path.as_posix()}"')
            return path.read_bytes()

    def screen(self):
        data = self.vga()
        return "\n".join(data[i*160:(i+1)*160:2].decode("cp437") for i in range(25))

    def wait_for(self, text, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            screen = self.screen()
            if text in screen:
                return screen
            time.sleep(0.1)
        raise AssertionError(f"Expected {text!r}:\n{self.screen()}")

    def screenshot(self, path):
        self.command(f'screendump "{Path(path).as_posix()}"')

    def __exit__(self, *_):
        self.monitor.close()
        self.process.terminate()
        self.process.wait(timeout=5)
