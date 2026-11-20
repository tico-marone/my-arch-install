"""Subprocess execution, dry-run simulation, and Live ISO logging."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
import shlex
import subprocess
import time

from . import __version__


DEFAULT_LOG_PATH = Path("/tmp/my-arch-install.log")


class CommandError(RuntimeError):
    """Raised when an underlying system command fails."""

    def __init__(self, command: str, returncode: int, tail_lines: list[str]) -> None:
        self.command = command
        self.returncode = returncode
        self.tail_lines = tail_lines
        tail_preview = "\n".join(tail_lines[-10:]) if tail_lines else "(no output)"
        super().__init__(
            f"Command failed (exit code {returncode}): {command}\n{tail_preview}"
        )


class CommandRunner:
    """Executes system commands with RAM-only logging and dry-run support."""

    def __init__(
        self,
        *,
        dry_run: bool = False,
        log_path: Path = DEFAULT_LOG_PATH,
    ) -> None:
        self.dry_run = dry_run
        self.log_path = log_path
        self.recorded_commands: list[str] = []
        self._init_log()

    def _init_log(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"=== my-arch-install v{__version__} session started "
            f"{datetime.now().isoformat()} (dry_run={self.dry_run}) ===\n"
        )
        self.log_path.write_text(header, encoding="utf-8")

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")

    @staticmethod
    def format_cmd(args: Sequence[str]) -> str:
        return " ".join(shlex.quote(a) for a in args)

    def run(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        redact_stdin: bool = False,
        on_line: Callable[[str], None] | None = None,
        env: dict[str, str] | None = None,
        allow_in_dry_run: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command, streaming stdout/stderr to the RAM log file."""
        cmd_str = self.format_cmd(args)
        self.recorded_commands.append(cmd_str)
        self.log(f"$ {cmd_str}")

        if self.dry_run and not allow_in_dry_run:
            if redact_stdin and input_text is not None:
                self.log("[stdin redacted]")
            self.log("[DRY-RUN] Skipped execution")
            time.sleep(0.03)
            return subprocess.CompletedProcess(list(args), 0, stdout="", stderr="")

        if input_text is not None:
            if redact_stdin:
                self.log("[stdin redacted]")
            proc = subprocess.run(
                list(args),
                input=input_text,
                text=True,
                capture_output=True,
                env=env,
            )
            if proc.stdout:
                for line in proc.stdout.splitlines():
                    self.log(line)
                    if on_line:
                        on_line(line)
            if proc.stderr:
                for line in proc.stderr.splitlines():
                    self.log(line)
                    if on_line:
                        on_line(line)
            if proc.returncode != 0:
                tail = (proc.stdout + "\n" + proc.stderr).strip().splitlines()[-15:]
                raise CommandError(cmd_str, proc.returncode, tail)
            return proc

        tail_buffer: deque[str] = deque(maxlen=25)
        stdout_lines: list[str] = []

        process = subprocess.Popen(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip("\r\n")
            stdout_lines.append(line)
            tail_buffer.append(line)
            self.log(line)
            if on_line:
                on_line(line)

        returncode = process.wait()
        if returncode != 0:
            raise CommandError(cmd_str, returncode, list(tail_buffer))

        return subprocess.CompletedProcess(
            list(args), returncode, stdout="\n".join(stdout_lines), stderr=""
        )

    def arch_chroot(
        self,
        target: Path,
        command: Sequence[str],
        *,
        input_text: str | None = None,
        redact_stdin: bool = False,
        on_line: Callable[[str], None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command inside target using `arch-chroot -S` (systemd-aware)."""
        return self.run(
            ["arch-chroot", "-S", str(target), *command],
            input_text=input_text,
            redact_stdin=redact_stdin,
            on_line=on_line,
        )

    def write_file(
        self,
        path: Path,
        content: str,
        *,
        mode: int | None = None,
        redact_content: bool = False,
    ) -> None:
        """Write a configuration file to disk (or log in dry-run mode)."""
        self.log(f"WRITE {path} ({len(content)} bytes)")
        if self.dry_run:
            if not redact_content:
                for line in content.splitlines()[:20]:
                    self.log(f"  | {line}")
            self.log("[DRY-RUN] Skipped file write")
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if mode is not None:
            path.chmod(mode)
