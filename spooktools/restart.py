"""Restarting ComfyUI from the browser.

Modelled on ComfyUI-Manager's "legacy" restart (`/manager/reboot` in glob/manager_server.py):

- Under comfy-cli (`__COMFY_CLI_SESSION__` is set), touch `<session>.reboot` and exit; comfy-cli starts ComfyUI again.
- Otherwise ("exec", the default) replace the running Python with a fresh one with the same arguments, via
  `os.execv`. On Linux that keeps the PID, so a parent that just waits for the process (e.g. the
  mmartial/ComfyUI-Nvidia-Docker entry script, which stops the container when ComfyUI exits) never notices.
  On Windows `os.execv` starts a new process and ends the old one instead.
- `SPOOKTOOLS_RESTART_MODE=exit` just exits with status 0, for setups where a supervisor restarts ComfyUI
  (Docker restart policy `always`/`unless-stopped`, systemd `Restart=always`, ...).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

RESTART_MODE_ENV = "SPOOKTOOLS_RESTART_MODE"
COMFY_CLI_SESSION_ENV = "__COMFY_CLI_SESSION__"
# Makes ComfyUI open a browser tab on start; dropped so a restart doesn't open another one (as Manager does).
WINDOWS_STANDALONE_FLAG = "--windows-standalone-build"


def restart_mode(environ: Mapping[str, str] | None = None) -> str:
    """Restart mode: "comfy-cli" under comfy-cli, else "exit" if SPOOKTOOLS_RESTART_MODE=exit, else "exec"."""
    environ = os.environ if environ is None else environ
    if environ.get(COMFY_CLI_SESSION_ENV):
        return "comfy-cli"
    if environ.get(RESTART_MODE_ENV, "").strip().lower() == "exit":
        return "exit"
    return "exec"


def interpreter_options(argv: Sequence[str], orig_argv: Sequence[str] | None) -> list[str]:
    """Python's own options from the original command line, e.g. ["-s"] for `python -s main.py --listen`.

    `sys.argv` drops them, and losing `-s` (the Windows portable build's launcher uses it) would let user
    site-packages into the restarted process. `sys.orig_argv` is [python, *options, script | -m module, *args];
    if it doesn't end with argv[1:] it isn't understood, and no options are kept.
    """
    if not orig_argv or not argv:
        return []
    tail = len(argv) - 1
    if list(orig_argv[len(orig_argv) - tail :] if tail else []) != list(argv[1:]):
        return []
    head = list(orig_argv[1 : len(orig_argv) - tail])  # [*options, script] or [*options, "-m", module]
    if len(head) >= 2 and head[-2] == "-m":
        return head[:-2]
    return head[:-1]


def build_restart_command(
    argv: Sequence[str], executable: str, platform: str, orig_argv: Sequence[str] | None = None
) -> list[str]:
    """The argv list for `os.execv(executable, ...)` that starts ComfyUI again the way it was started.

    - `--windows-standalone-build` is dropped so the restart doesn't open another browser tab (as Manager does).
    - `python -m package` runs report `.../package/__main__.py` as argv[0]; re-run them with `-m package`.
    - Interpreter options (`-s`, `-X utf8`, ...) are kept when `orig_argv` (`sys.orig_argv`) shows them.
    - On Windows, execv joins the arguments into one command line without quoting, so quote each argument that
      needs it (Manager quotes only the interpreter and script; this also covers e.g. `--base-directory "D:\\My
      Models"`).
    """
    options = interpreter_options(argv, orig_argv)
    args = [arg for arg in argv if arg != WINDOWS_STANDALONE_FLAG]
    if args and args[0].endswith("__main__.py"):
        module = os.path.basename(os.path.dirname(args[0]))
        command = [executable, *options, "-m", module, *args[1:]]
    else:
        command = [executable, *options, *args]
    if platform.startswith("win32"):
        command = [subprocess.list2cmdline([arg]) for arg in command]
    return command


def exec_process(executable: str, command: list[str]) -> None:
    """Replace this process. Separate so tests can stub it."""
    os.execv(executable, command)


def exit_process(code: int = 0) -> None:
    """Exit right away. `os._exit`, not `sys.exit`: a SystemExit from an event-loop callback would wait on every
    non-daemon thread any custom node started, and could hang instead of exiting."""
    os._exit(code)


def _flush_output() -> None:
    """Make sure everything logged so far reaches the console/log file before the process image goes away."""
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
        try:
            # ComfyUI-Manager's prestartup wraps stdout/stderr to tee into its log file; close that file like its
            # own restart does. ComfyUI's LogInterceptor only needs a flush: the console fds stay open for the
            # new process.
            close_log = getattr(stream, "close_log", None)
            if callable(close_log):
                close_log()
            if stream is not None:
                stream.flush()
        except Exception:
            pass


def perform_restart() -> None:
    """Restart now, in the mode `restart_mode()` selects. Only returns if exec/exit failed (it raises then)."""
    mode = restart_mode()
    if mode == "comfy-cli":
        reboot_marker = Path(os.environ[COMFY_CLI_SESSION_ENV] + ".reboot")
        reboot_marker.touch()
        logger.warning("ComfyUI-SpookTools: restarting ComfyUI (comfy-cli session, wrote %s)", reboot_marker)
        _flush_output()
        exit_process(0)
    elif mode == "exit":
        logger.warning("ComfyUI-SpookTools: restarting ComfyUI by exiting (%s=exit)", RESTART_MODE_ENV)
        _flush_output()
        exit_process(0)
    else:
        command = build_restart_command(sys.argv, sys.executable, sys.platform, getattr(sys, "orig_argv", None))
        logger.warning("ComfyUI-SpookTools: restarting ComfyUI in place: %s", command)
        _flush_output()
        exec_process(sys.executable, command)
