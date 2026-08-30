"""Small shared helpers."""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from collections import namedtuple
from pathlib import Path

from musicocr.pipeline import StageError

LenientResult = namedtuple("LenientResult", "returncode output timed_out")


def run_cmd(
    cmd: list[str],
    *,
    timeout: int | None = None,
    env: dict | None = None,
    log=None,
) -> subprocess.CompletedProcess:
    """Run a command, capturing output. Raises StageError on non-zero exit."""
    if log:
        log("  $ " + " ".join(_quote(c) for c in cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        raise StageError(f"executable not found: {cmd[0]} ({exc})") from exc
    except subprocess.TimeoutExpired as exc:
        raise StageError(f"timed out after {timeout}s: {cmd[0]}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-15:]
        raise StageError(
            f"{Path(cmd[0]).name} exited {proc.returncode}:\n    "
            + "\n    ".join(tail)
        )
    return proc


def run_cmd_lenient(
    cmd: list[str],
    *,
    timeout: int = 300,
    env: dict | None = None,
    log=None,
) -> LenientResult | None:
    """Run a command without raising on non-zero exit.

    Built for MuseScore 4 on macOS, which does the conversion correctly but then
    aborts during shutdown and leaves a ``crashpad_handler`` grandchild that
    would deadlock a normal pipe-capturing ``subprocess.run``. So: output goes to
    a temp file, the child gets its own session, and on timeout the whole process
    group is killed.

    Returns a ``LenientResult`` (returncode/output/timed_out), or ``None`` if the
    executable was missing. The caller judges real success from output files.
    """
    if log:
        log("  $ " + " ".join(_quote(c) for c in cmd))
    tmp = tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False)
    try:
        try:
            proc = subprocess.Popen(
                cmd, stdout=tmp, stderr=subprocess.STDOUT, env=env,
                start_new_session=True,
            )
        except FileNotFoundError:
            if log:
                log(f"  executable not found: {cmd[0]}")
            return None

        deadline = time.monotonic() + timeout
        timed_out = False
        while True:
            if proc.poll() is not None:
                break
            if time.monotonic() > deadline:
                timed_out = True
                _kill_group(proc.pid)
                break
            time.sleep(0.2)

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

        tmp.seek(0)
        output = tmp.read()
        # Reap any lingering grandchildren (MuseScore's crashpad_handler) so they
        # don't outlive the run.
        _kill_group(proc.pid)
        rc = proc.returncode if proc.returncode is not None else -1
        if timed_out and log:
            log(f"  timed out after {timeout}s: {cmd[0]}")
        return LenientResult(rc, output, timed_out)
    finally:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _kill_group(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(pid), sig)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.3)


def _quote(s: str) -> str:
    return f'"{s}"' if " " in s else s


def newest(paths: list[Path]) -> Path | None:
    paths = [p for p in paths if p.exists()]
    if not paths:
        return None
    return max(paths, key=lambda p: p.stat().st_mtime)
