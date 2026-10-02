"""Run the raw BF image with an isolated embedded data volume."""
import shutil
import socket
import subprocess
import tempfile
import time
import json
from pathlib import Path
from qemu_test_support import Machine as LegacyMachine, ROOT, QEMU
from package_raw import DATA_OFFSET
from disk import SIZE


class Machine(LegacyMachine):
    def key(self,key):
        self.command(f"sendkey {key} 40")
        time.sleep(0.075)

    def wait_prompt(self,timeout=60,cancel_view=False):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            screen = self.screen()
            if cancel_view and "VIEWER" in screen.splitlines()[24]:
                self.key("esc")
            for row in reversed(screen.splitlines()[6:22]):
                if "BFOS>" in row and not row.split("BFOS>",1)[1].strip(" ║"):
                    return
            time.sleep(0.1)
        raise AssertionError("Shell prompt did not return:\n"+self.screen())

    def line(self,text):
        self.wait_prompt(cancel_view=text.strip()=="clear")
        self.text(text)
        self.key("ret")
        time.sleep(0.2)
        command = text.split(" ",1)[0]
        if command not in ("edit","run","cat","ls","reboot","halt"):
            self.wait_prompt()

    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory()
        self.boot_image = Path(self.directory.name) / "boot.img"
        shutil.copyfile(ROOT / "bfos-raw.img", self.boot_image)
        with self.boot_image.open("r+b") as disk:
            disk.seek(DATA_OFFSET)
            disk.write(self.data_disk.read_bytes() if self.data_disk else b"\0"*SIZE)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1",0))
            port = reservation.getsockname()[1]
        args = [str(QEMU),"-m","128","-boot","c","-display","none",
                "-monitor",f"tcp:127.0.0.1:{port},server=on,wait=off"]
        if self.readonly:
            # IDE disks cannot use QEMU's readonly backend. Inject write EIO
            # instead, to exercise the same BIOS failure path on real writes.
            backend = {"driver":"blkdebug","node-name":"disk",
                       "inject-error":[{"event":"none","iotype":"write","errno":5,
                                        "once":False,"immediately":True}],
                       "image":{"driver":"raw","file":{"driver":"file","filename":str(self.boot_image)}}}
            args += ["-blockdev",json.dumps(backend),"-device","ide-hd,drive=disk,werror=report"]
        else:
            args += ["-drive",f"format=raw,file={self.boot_image},if=ide"]
        self.process = subprocess.Popen(args,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            time.sleep(0.5)
            self.monitor = socket.create_connection(("127.0.0.1",port))
            self.monitor.settimeout(10)
            self._response()
            self.wait_for("BFOS v0.4 - raw Brainfuck workstation",timeout=60)
            self.wait_for("BFOS>",timeout=10)
        except BaseException:
            if self.process.poll() is not None:
                message = self.process.stderr.read().decode(errors="replace")
                self.directory.cleanup()
                raise RuntimeError(message)
            self.process.terminate(); self.process.wait(timeout=5)
            self.directory.cleanup()
            raise
        return self

    def __exit__(self,*args):
        try:
            self.monitor.close()
            self.process.terminate(); self.process.wait(timeout=5)
            if self.data_disk:
                with self.boot_image.open("rb") as disk:
                    disk.seek(DATA_OFFSET)
                    self.data_disk.write_bytes(disk.read(SIZE))
        finally:
            self.directory.cleanup()
