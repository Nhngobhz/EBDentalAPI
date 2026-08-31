"""
Running the SAP catalogue sync on demand, from the admin Settings screen.

The sync itself is scripts/sap_sync.py and stays there. That script is what the nightly
scheduled task runs (scripts/sap_sync_scheduled.cmd), and a button that executed a
second, hand-rolled copy of the same job is how the two quietly drift apart until one of
them is wrong. This module is only the part that was missing: starting that script,
watching it, and giving the admin screen something to poll.

Why a subprocess rather than an import
--------------------------------------
`from scripts.sap_sync import main; main()` would run the sync inside the API worker, and
every one of its failure modes would become the API's. It calls sys.exit() when the
delisting rail trips or the extract comes back empty; it opens a session of its own and
holds a transaction over ~8,000 rows for as long as a SQL Server query takes to cross a
network; it reconfigures sys.stdout. A child process keeps all of that where it already
is - and means this button runs byte-for-byte what the scheduled task runs, argparse
defaults and all.

Where the state lives
---------------------
In this module, not in a table. The API is a single uvicorn process (deploy/bootstrap.ps1
starts it with no --workers), so one module-level record of the run in flight is visible
to every request that polls for it, and a run lost to a restart was killed by that
restart anyway. What has to survive is the *result*, and that already does: the script
writes its run reports into sap_extract/, which this reads back from disk - so the panel
shows the nightly scheduled run too, even though this process never started it.

Deliberately not exposed: --max-delist-ratio. Overriding the safety rail means telling
the sync that hiding most of the catalogue is correct, which is a decision made at a
command line with the extract in front of you, not a checkbox on a web page. A tripped
rail writes nothing and says so in the output.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.core.logging_conf import get_logger

logger = get_logger("sap_sync")

# The store-api checkout root: the directory holding scripts/ and .env. Derived from this
# file rather than taken from os.getcwd(), because the child inherits neither - the NSSM
# service happens to set AppDirectory correctly, but a `python -m app.main` started from
# somewhere else would otherwise run the sync against a different .env, which means a
# different DATABASE_URL.
ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "sap_extract"

# What --catalogue accepts. Must match scripts/sap_db_pull.CATALOGUES (plus the script's
# own "all"); a name that isn't there is rejected by argparse, which would surface as a
# failed run rather than a bad request, so it is checked here first.
CATALOGUES = {
    "all": "Everything",
    "materials": "Materials",
    "spare-parts": "Spare parts",
}

# The report file scripts/sap_sync.py writes per catalogue: out_dir / "<name, with - as
# _>_sync_report.md". A literal map rather than an import of the script's own CATALOGUES,
# because `scripts` is not an installed package and importing it from the app would make
# startup depend on the working directory. Getting this wrong costs a report the panel
# cannot find, nothing more.
REPORT_FILES = {
    "materials": "materials_sync_report.md",
    "spare-parts": "spare_parts_sync_report.md",
}

# Enough for a whole run's console output - the script prints its full report at the end,
# which is the long part - without letting a process stuck in a print loop grow this one.
MAX_OUTPUT_LINES = 2000


class SapSyncBusy(RuntimeError):
    """A run is already in flight.

    Raised rather than queued. Two syncs writing the same rows at once is the one thing
    this must not do, and an admin who clicked twice wants to be told the first click
    worked - not to have a second run start by surprise four minutes later.
    """


_lock = threading.Lock()
_run: dict | None = None  # the run in flight, or the last to finish. Guarded by _lock.
_output: deque[str] = deque(maxlen=MAX_OUTPUT_LINES)  # its console output, same lock.


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _python() -> str:
    """The interpreter to run the sync with - the venv's, however this process started.

    Normally sys.executable. Under a pip console-script launcher (the service runs
    venv\\Scripts\\uvicorn.exe) that can be the launcher instead, and
    `uvicorn.exe -m scripts.sap_sync` would start a second web server. The interpreter
    sits next to it in the same Scripts/ directory either way.
    """
    exe = Path(sys.executable)
    if exe.stem.lower().startswith("python"):
        return str(exe)
    for name in ("python.exe", "python"):
        candidate = exe.with_name(name)
        if candidate.exists():
            return str(candidate)
    return str(exe)


def _child_env() -> dict:
    """The parent's environment, plus a UTF-8 stdout.

    sap_sync.main() reconfigures its own streams, but only once it is running: a
    traceback raised before that is written using the Windows console codepage, and a
    Khmer item name in one would then raise UnicodeEncodeError inside the error path.
    PYTHONIOENCODING settles it before the first byte.
    """
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}


def _terminate(proc: subprocess.Popen) -> None:
    """Kill the run and the sqlcmd/ssh it is waiting on.

    proc.kill() alone ends the Python process and leaves its child holding a SQL Server
    session open until it notices the closed pipe. taskkill /T ends the tree, which is
    the point of killing it at all.
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        proc.kill()


def _append(line: str) -> None:
    with _lock:
        _output.append(line)


def _finish(**fields) -> None:
    with _lock:
        if _run is not None:
            _run.update(fields)
            started = _run.get("_started")
            if started is not None:
                _run["duration_seconds"] = round((_now() - started).total_seconds(), 1)
            _run["finished_at"] = _now().isoformat()


def _execute(command: list[str]) -> None:
    """The run itself, on its own thread: stream the output, enforce the time limit."""
    timed_out = threading.Event()
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # one stream: the report and the errors interleave
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_child_env(),
            # Stops a console window flashing up for sqlcmd/ssh when the API is run from
            # a terminal. No effect under the service, and 0 (ignored) off Windows.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:  # the interpreter or scripts/ isn't where we think it is
        logger.exception("SAP sync could not start")
        _finish(state="failed", error=f"Could not start the sync: {exc}")
        return

    def _time_out() -> None:
        timed_out.set()
        _terminate(proc)

    # Without this, a hung sqlcmd - or an SSH session waiting on a host that has gone
    # away - leaves the run marked "running" forever, and every later run is refused as
    # busy by a job that is never coming back.
    watchdog = threading.Timer(settings.SAP_SYNC_TIMEOUT_SECONDS, _time_out)
    watchdog.daemon = True
    watchdog.start()
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            _append(line.rstrip("\r\n"))
        code = proc.wait()
    except Exception as exc:
        # Whatever went wrong reading the pipe, this thread is the only thing that will
        # ever mark the run finished - so it must not die quietly. It would leave the
        # panel spinning on "Running…" and every later run refused as busy until the
        # service restarted.
        logger.exception("SAP sync supervision failed")
        _terminate(proc)
        _finish(state="failed", error=f"Lost track of the running sync: {exc}")
        return
    finally:
        watchdog.cancel()

    if timed_out.is_set():
        minutes = settings.SAP_SYNC_TIMEOUT_SECONDS // 60
        _finish(
            state="failed",
            exit_code=code,
            error=f"Stopped after {minutes} minutes without finishing. Nothing was "
                  "written - each catalogue only commits once it has finished.",
        )
        logger.warning("SAP sync timed out after %ss", settings.SAP_SYNC_TIMEOUT_SECONDS)
        return

    if code == 0:
        _finish(state="succeeded", exit_code=0, error=None)
        logger.info("SAP sync finished: %s", " ".join(command[2:]))
    else:
        # The script's own last words are the useful message, and its last *line* is the
        # whole of it in each of the three ways this fails: the delisting rail and
        # "extract is empty" both sys.exit() a single sentence, and a crash ends on the
        # exception line. Taking more would put stack frames in front of the reader; the
        # panel shows the full output underneath either way.
        with _lock:
            tail = next((line for line in reversed(_output) if line.strip()), "")
        _finish(
            state="failed",
            exit_code=code,
            error=tail or f"The sync exited with code {code}.",
        )
        logger.error("SAP sync failed (exit %s)", code)


def start(catalogue: str, apply: bool, actor: str) -> dict:
    """Kick off a run and return the status straight away.

    Raises SapSyncBusy if one is already going, ValueError on an unknown catalogue.
    """
    global _run

    if catalogue not in CATALOGUES:
        raise ValueError(
            f"Unknown catalogue '{catalogue}' - choose one of "
            + ", ".join(sorted(CATALOGUES))
        )

    command = [
        _python(),
        "-m",
        "scripts.sap_sync",
        "--catalogue",
        catalogue,
        "--transport",
        settings.SAP_SYNC_TRANSPORT,
    ]
    if apply:
        command.append("--apply")

    with _lock:
        if _run is not None and _run["state"] == "running":
            raise SapSyncBusy(
                "A catalogue sync is already running. Wait for it to finish - the panel "
                "updates on its own."
            )
        _output.clear()
        _run = {
            "state": "running",
            "catalogue": catalogue,
            "catalogue_label": CATALOGUES[catalogue],
            "apply": apply,
            "transport": settings.SAP_SYNC_TRANSPORT,
            "started_at": _now().isoformat(),
            "started_by": actor,
            "finished_at": None,
            "duration_seconds": None,
            "exit_code": None,
            "error": None,
            # Not serialised: _finish() measures the run against it. status() builds what
            # goes over the wire and drops the underscore keys.
            "_started": _now(),
        }

    logger.info(
        "SAP sync started by %s: catalogue=%s apply=%s transport=%s",
        actor,
        catalogue,
        apply,
        settings.SAP_SYNC_TRANSPORT,
    )
    threading.Thread(target=_execute, args=(command,), name="sap-sync", daemon=True).start()
    return status()


def _reports(include_text: bool) -> list[dict]:
    """The run reports sitting in sap_extract/, newest first.

    Read from disk on every call rather than remembered from the run that wrote them,
    which is what makes the nightly scheduled task's report show up here as well.
    """
    found = []
    for catalogue, filename in REPORT_FILES.items():
        path = REPORT_DIR / filename
        try:
            stat = path.stat()
        except OSError:
            continue  # never synced, or the report was cleared away. Not an error.
        entry = {
            "catalogue": catalogue,
            "label": CATALOGUES[catalogue],
            "filename": filename,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        }
        if include_text:
            try:
                entry["text"] = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                entry["text"] = f"Could not read {filename}: {exc}"
        found.append(entry)
    return sorted(found, key=lambda report: report["modified_at"], reverse=True)


def status(include_reports: bool = False) -> dict:
    """A snapshot the Settings panel can poll.

    `include_reports` carries the full report text, which runs to thousands of words -
    the panel asks for it when it loads and again when a run ends, and polls without it
    in between.
    """
    with _lock:
        run = None
        if _run is not None:
            run = {k: v for k, v in _run.items() if not k.startswith("_")}
            run["output"] = list(_output)

    return {
        "run": run,
        "running": bool(run and run["state"] == "running"),
        "transport": settings.SAP_SYNC_TRANSPORT,
        "timeout_seconds": settings.SAP_SYNC_TIMEOUT_SECONDS,
        "catalogues": [{"value": key, "label": label} for key, label in CATALOGUES.items()],
        "reports": _reports(include_reports),
    }
