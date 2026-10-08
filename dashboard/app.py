#!/usr/bin/env python3
"""
vjb-build-dashboard - minimal internal web UI for build_system, so builds
can be triggered without SSH'ing into this host.

This adds NO new build logic: it's a queue + live log viewer + download
endpoint sitting in front of the exact `build_system <component> <branch>
[--push]` CLI you already use by hand. One build runs at a time (FIFO
queue) - proxy-24 is a shared host and we already hit real CPU/KVM
contention issues once; this doesn't reintroduce that.

v1 scope (per team decision): no auth - this MUST only be reachable from
the internal network/VPN, not the open internet, since anyone who can load
the page can trigger a build. That's a firewall/security-group setting on
this host, not something this app enforces itself.

Log delivery is deliberately dumb: the browser polls GET /builds/{id}/log
(the complete log, from byte 0, every time) and GET /builds/{id}/status (a
small JSON snapshot) on a plain timer and replaces its display wholesale -
see build.html. There is no live-tailing connection of any kind (no SSE, no
WebSocket) and no server-side per-client state (no byte offsets, no
Last-Event-ID, nothing "resumed"). Every poll is a complete, independent,
stateless request that either succeeds or fails on its own; a missed poll
just means the next one (a few seconds later) catches the page back up.
This is a deliberate downgrade from an earlier live-tailing version: that
approach went through three rounds of real bugs (stdio buffering, a pty
left open by a lingering child, an SSE reconnect that duplicated the whole
log, then a thread-pool exhaustion bug from the fix for that) before it
surfaced yet another failure mode live in front of the user - a frozen tab
serious enough that Chrome itself put up "Page Unresponsive". Every one of
those bugs came from the same place: a persistent connection with server-
side state that a client disconnect/reconnect could get out of sync with.
Plain polling has no such state to get out of sync, at the cost of a few
seconds of lag - a trade worth making for a build that runs for minutes.

Run with: uvicorn app:app --host 0.0.0.0 --port 80
(port 80 needs root, or CAP_NET_BIND_SERVICE on the venv's uvicorn/python -
see the accompanying systemd unit, which grants that via
AmbientCapabilities, for running it as a real service without running the
whole process as root)
"""
import fcntl
import mimetypes
import os
import pty
import re
import select
import shutil
import signal
import sqlite3
import struct
import subprocess
import termios
import threading
import queue
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse, FileResponse, RedirectResponse, PlainTextResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent

# Everything this dashboard writes (db, logs, copied-out qcow2 artifacts)
# lives under here, next to the app code by default, and separate from
# /opt/build/vjailbreak (the actual build checkout) - keeping those apart
# means wiping/reinstalling the dashboard never touches build history or
# vice versa. Override if you want it elsewhere (e.g. a bigger disk).
DATA_DIR = Path(os.environ.get("DASHBOARD_DATA_DIR", str(BASE_DIR / "data")))
LOG_DIR = DATA_DIR / "logs"
ARTIFACT_DIR = DATA_DIR / "artifacts"
DB_PATH = DATA_DIR / "dashboard.db"

for d in (DATA_DIR, LOG_DIR, ARTIFACT_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Bare name resolved via PATH by default; override if build_system isn't on
# this service's PATH (e.g. it's a plain script somewhere specific).
BUILD_SYSTEM_BIN = os.environ.get("BUILD_SYSTEM_BIN", "build_system")

# Must match build_system's own REPO_DIR / qcow2 output path exactly - this
# is where a successful qcow2 build leaves its output, INSIDE the checkout,
# where the next build's `git clean -ffdx` will delete it. We copy it out
# (see run_build) before that can happen.
REPO_QCOW2_PATH = Path(os.environ.get(
    "REPO_QCOW2_PATH",
    os.environ.get("BOB_VJB_REPO_DIR", "/opt/build/vjailbreak") + "/vjailbreak_qcow2/vjailbreak-image.qcow2",
))

# How many finished qcow2 artifacts to keep on disk at once (oldest deleted
# first once a newer one succeeds). This is now a backstop, not the primary
# policy - see QCOW2_RETENTION_HOURS below - kept on in case a burst of
# qcow2 builds inside one retention window would otherwise pile up before
# any of them age out.
KEEP_LAST_N_QCOW2 = int(os.environ.get("KEEP_LAST_N_QCOW2", "5"))

# The dashboard's home page ("Recent builds") and its qcow2 artifacts are
# both windowed to the same default: a host that's been building for weeks
# shouldn't grow an infinite table or accumulate an infinite pile of
# multi-GB qcow2 files just because nobody's cleaned up by hand. Separate
# knobs since there's no requirement they ever have to match.
BUILD_HISTORY_HOURS = int(os.environ.get("BUILD_HISTORY_HOURS", "6"))
QCOW2_RETENTION_HOURS = int(os.environ.get("QCOW2_RETENTION_HOURS", "6"))

# How often the background sweep (see retention_sweep_loop further down)
# re-checks qcow2 ages. This runs independently of any build actually
# happening - it's what deletes a stale qcow2 even if nobody's kicked off a
# new build in days, which prune_old_qcow2() (only ever triggered by a NEW
# qcow2 landing, see _adopt_ready_qcow2) would otherwise never catch.
RETENTION_SWEEP_INTERVAL_SECONDS = int(os.environ.get("RETENTION_SWEEP_INTERVAL_SECONDS", str(10 * 60)))

# ---------------------------------------------------------------------------
# No subprocess this app runs is ever allowed to block it (or the single
# build worker) forever - see run_supervised_command() below for the full
# rationale. These are the two independent ceilings enforced on every build:
# an idle one (no new log output at all) and an absolute one (regardless of
# activity). Either one firing kills the build's entire process tree and
# fails the build instead of wedging the queue.
BUILD_IDLE_TIMEOUT_SECONDS = int(os.environ.get("BUILD_IDLE_TIMEOUT_SECONDS", 15 * 60))
BUILD_MAX_DURATION_SECONDS = int(os.environ.get("BUILD_MAX_DURATION_SECONDS", 90 * 60))
# How long to wait after SIGTERM before escalating to SIGKILL on a build
# that's being killed (timeout or manual cancel) - long enough for docker/
# packer/make to unwind cleanly if they're going to, short enough that a
# kill request doesn't itself hang around indefinitely.
BUILD_KILL_GRACE_SECONDS = int(os.environ.get("BUILD_KILL_GRACE_SECONDS", 10))

COMPONENTS = ["controller", "ui", "sdk", "ai", "qcow2"]

# Display-only copy of build_system's quay namespace / per-image repos /
# component->image mapping, used solely to show accurate `docker pull` hints
# on the dashboard (see index.html). Kept as a small copy rather than
# importing build_system - it's deliberately a standalone CLI outside this
# app's dependency graph. Both read the same BOB_QUAY_* settings from bob.env
# (the systemd unit loads it via EnvironmentFile=), with the same defaults as
# builder/common/config.py, so the two can't drift apart.
QUAY_NAMESPACE = os.environ.get("BOB_QUAY_NAMESPACE", "")
QUAY_REPOS_BY_COMPONENT = {
    "controller": [
        os.environ.get("BOB_QUAY_REPO_V2V_HELPER", "proj_v2v"),
        os.environ.get("BOB_QUAY_REPO_CONTROLLER", "proj_controller"),
    ],
    "ui": [os.environ.get("BOB_QUAY_REPO_UI", "proj_ui")],
    "sdk": [os.environ.get("BOB_QUAY_REPO_VPWNED", "proj_sdk")],
    "ai": [os.environ.get("BOB_QUAY_REPO_AI", "proj_ai")],
}

# Branch names now arrive from an unauthenticated web form instead of
# someone typing a CLI command themselves. A value starting with "-" could
# otherwise be mistaken for a git/build_system flag (classic argument-
# injection class of bug), so this is deliberately strict.
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")

# ".qcow2" isn't a registered extension, so Python's mimetype guesser (used
# internally by StaticFiles/FileResponse to set Content-Type) would otherwise
# fall back to "text/plain" for a multi-GB binary disk image - exactly the
# kind of mismatch an image-import tool's validation could reject alongside
# (or instead of) the URL-shape problem this file's /artifacts mount below
# already fixes. Register it globally, once, before anything serves a file.
mimetypes.add_type("application/octet-stream", ".qcow2")

app = FastAPI(title="vjailbreak build dashboard")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
# Plain static file serving for qcow2 artifacts (ARTIFACT_DIR) - deliberately
# separate from the /builds/{id}/download route further down. External tools
# that import an image by URL (Private Cloud Director's "Add Image" dialog,
# Glance, etc.) can be picky about that URL: some validate it client-side
# before ever making a request and reject anything that isn't a plain,
# extension-terminated static path. Rather than guessing every importer's
# validation rules, just serve the artifacts directory as what it actually is
# - a directory of files - so the URL IS a static resource with no dynamic
# route logic (or redirect) in front of it. StaticFiles also honors Range
# requests, same as the FileResponse-based routes below.
app.mount("/artifacts", StaticFiles(directory=str(ARTIFACT_DIR)), name="artifacts")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ---------------------------------------------------------------------------
# storage - SQLite, one file, no separate DB service to run/maintain
# ---------------------------------------------------------------------------

def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS builds (
                id TEXT PRIMARY KEY,
                component TEXT NOT NULL,
                branch TEXT NOT NULL,
                push INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                exit_code INTEGER,
                log_path TEXT,
                qcow2_path TEXT,
                note TEXT
            )
        """)
        # `note` was added after some installs already had a builds table
        # without it - CREATE TABLE IF NOT EXISTS above is a no-op against
        # those, so add the column here too. Harmless/idempotent: this only
        # ever fails with "duplicate column", which just means a newer
        # install already has it.
        try:
            conn.execute("ALTER TABLE builds ADD COLUMN note TEXT")
        except sqlite3.OperationalError:
            pass


init_db()


def now_iso():
    # Microsecond resolution purely for display - ordering never relies on
    # this (see the rowid-based ORDER BYs below): two builds submitted or
    # finished within the same second would otherwise tie on created_at and
    # sort arbitrarily, which is exactly the kind of thing that quietly
    # picks the wrong artifact to keep/delete in prune_old_qcow2().
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def get_build(build_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM builds WHERE id = ?", (build_id,)).fetchone()
        return dict(row) if row else None


def list_builds(limit=50):
    # rowid, not created_at: two builds submitted within the same second
    # would otherwise tie and sort arbitrarily. rowid is monotonically
    # increasing insertion order, which always matches submission order.
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM builds ORDER BY rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def list_recent_builds(hours, limit=500):
    """Same ordering as list_builds, windowed to the last `hours` hours by
    created_at instead of a fixed count - see BUILD_HISTORY_HOURS. created_at
    is always this service's own now_iso() output (fixed-width ISO8601, UTC),
    so a plain string comparison filters identically to a real datetime
    comparison - no per-row parsing needed. `limit` is just a sanity backstop
    against rendering an enormous table if a lot happened in one window; the
    scrollable container (see index.html) is what actually keeps the page
    usable well before that limit would matter."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="microseconds")
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM builds WHERE created_at >= ? ORDER BY rowid DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def update_build(build_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db() as conn:
        conn.execute(f"UPDATE builds SET {cols} WHERE id = ?", (*fields.values(), build_id))


def queue_position(build_id):
    """How many still-queued builds are ahead of this one - 0 means it's
    next up once the current build finishes. Computed from the DB's own
    rowid-based FIFO ordering (see list_builds's docstring above) rather
    than reading job_queue's internal deque directly, so this can never
    disagree with the order builds are actually dequeued in."""
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM builds WHERE status = 'queued' AND rowid < "
            "(SELECT rowid FROM builds WHERE id = ?)",
            (build_id,),
        ).fetchone()
    return row["n"] if row else 0


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def validate_component(component: str) -> str:
    if component not in COMPONENTS:
        raise HTTPException(400, f"Unknown component '{component}' - must be one of {COMPONENTS}")
    return component


def validate_branch(branch: str) -> str:
    branch = (branch or "").strip()
    if not branch:
        raise HTTPException(400, "Branch name is required")
    if branch.startswith("-"):
        raise HTTPException(400, "Branch name can't start with '-'")
    if ".." in branch or not BRANCH_RE.match(branch):
        raise HTTPException(400, "Branch name may only contain letters, digits, '.', '_', '-', '/'")
    return branch


# ---------------------------------------------------------------------------
# worker: single background thread, one build at a time (FIFO)
# ---------------------------------------------------------------------------

job_queue = queue.Queue()


def enqueue_existing_queued_jobs():
    """On startup, re-queue anything left 'queued' from a previous run of
    this service (e.g. a restart) so a submitted build doesn't just vanish.
    Anything that was 'running' didn't survive the restart (its subprocess
    died with the old process) - mark those failed rather than leaving a
    permanently-stuck row."""
    with db() as conn:
        rows = conn.execute(
            "SELECT id FROM builds WHERE status = 'queued' ORDER BY rowid"
        ).fetchall()
        conn.execute(
            "UPDATE builds SET status = 'failed', finished_at = ?, "
            "note = 'dashboard was restarted while this build was running' "
            "WHERE status = 'running'",
            (now_iso(),),
        )
    for row in rows:
        job_queue.put(row["id"])


def prune_old_qcow2():
    """Keep only the KEEP_LAST_N_QCOW2 most recent successful qcow2
    artifacts on disk - the actual clutter-control mechanism for these
    multi-GB files."""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, qcow2_path FROM builds "
            "WHERE qcow2_path IS NOT NULL AND status = 'success' "
            "ORDER BY rowid DESC"
        ).fetchall()
    for row in rows[KEEP_LAST_N_QCOW2:]:
        path = Path(row["qcow2_path"])
        if path.exists():
            path.unlink()
        with db() as conn:
            conn.execute("UPDATE builds SET qcow2_path = NULL WHERE id = ?", (row["id"],))


def prune_old_qcow2_by_age():
    """Delete on-disk qcow2 artifacts older than QCOW2_RETENTION_HOURS -
    the primary retention policy now (this is what the dashboard's own "images
    older than N hours are deleted" text promises). Runs independently of
    prune_old_qcow2()'s count-based cap and independently of any build
    actually happening - see retention_sweep_loop below - so a host that's
    been idle for days still gets its stale qcow2 images cleared out."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=QCOW2_RETENTION_HOURS)).isoformat(timespec="microseconds")
    with db() as conn:
        rows = conn.execute(
            "SELECT id, qcow2_path FROM builds WHERE qcow2_path IS NOT NULL AND created_at < ?",
            (cutoff,),
        ).fetchall()
    if not rows:
        return
    latest_path = ARTIFACT_DIR / "latest.qcow2"
    latest_ino = latest_path.stat().st_ino if latest_path.exists() else None
    for row in rows:
        path = Path(row["qcow2_path"])
        if path.exists():
            # latest.qcow2 is a hard link (see _relink_latest_qcow2), not a
            # copy - if it currently shares this file's inode, unlinking just
            # the per-build name wouldn't free any disk space (the other name
            # keeps the data alive) and would silently leave "latest" still
            # serving an image the retention policy says should be gone.
            if latest_ino is not None and path.stat().st_ino == latest_ino:
                latest_path.unlink(missing_ok=True)
                latest_ino = None
            path.unlink()
        with db() as conn:
            conn.execute("UPDATE builds SET qcow2_path = NULL WHERE id = ?", (row["id"],))


def prune_old_logs():
    """Delete log files older than QCOW2_RETENTION_HOURS - the same window
    the qcow2 sweep uses above, by deliberate choice (one retention story to
    reason about instead of a separate log-specific setting). This only
    removes the log FILE on disk; the build's own database row is left alone
    on purpose - build history stays queryable via /api/builds and a direct
    /builds/{id} link forever, only the console output text ages out. Age is
    the file's own mtime, not the build's created_at from the DB: a log
    still being actively appended to keeps bumping its mtime, so a
    currently-running build's log can never be deleted out from under it,
    with no need to cross-reference the DB at all."""
    cutoff = time.time() - QCOW2_RETENTION_HOURS * 3600
    for log_path in LOG_DIR.glob("*.log"):
        try:
            if log_path.stat().st_mtime < cutoff:
                log_path.unlink()
        except OSError:
            # Gone already, or a transient race with something else touching
            # it - either way, nothing worth stopping the sweep over.
            pass


_ANSI_RE = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]")


def _clean_log_bytes(chunk: bytes) -> str:
    """build_system shells out to docker/packer/make/git, none of which we
    control the output formatting of. Attached to a pty (see run_build
    below), they colorize and redraw progress lines the same way they would
    in an interactive terminal - readable in a real shell, but raw escape
    codes and lone '\\r' redraws just clutter a browser log pane. Strip SGR/
    OSC escape sequences and turn '\\r' redraws into real newlines so each
    progress update becomes its own line instead of overlapping text."""
    chunk = _ANSI_RE.sub(b"", chunk)
    text = chunk.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _set_pty_size(fd, rows=50, cols=220):
    """Some tools (docker pull, packer) size their progress-bar output to the
    terminal width and can render oddly at the pty default - give it a size
    generous enough that nothing wraps or divides-by-zero on a 0-width tty."""
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


QCOW2_READY_PREFIX = "[QCOW2_READY] "


def _adopt_ready_qcow2(build_id, src_path_str):
    """Called the moment build_system's own [QCOW2_READY] marker line shows up
    in the live log (see build_system's build_qcow2()). build_system still has
    a mandatory, comparatively slow quay.io upload step (Step 7) left to run
    after this point - there's no reason the actual disk image should stay
    undownloadable while that runs, so copy it out and make it available right
    away instead of waiting for the whole subprocess to exit. Sets qcow2_path
    only - status stays "running" until the process actually finishes, so a
    still-in-flight quay.io push is still reflected honestly in the UI."""
    src = Path(src_path_str.strip())
    if not src.exists():
        return
    dest = ARTIFACT_DIR / f"{build_id}.qcow2"
    shutil.copy2(src, dest)
    update_build(build_id, qcow2_path=str(dest))
    _relink_latest_qcow2(dest)
    prune_old_qcow2()


def _relink_latest_qcow2(target: Path):
    """Point artifacts/latest.qcow2 - served as a plain static file by the
    /artifacts mount above, same as every other qcow2 in ARTIFACT_DIR - at
    this build's artifact. A hard link, not a copy: same inode, so a
    multi-GB qcow2 doesn't get duplicated on disk, and unlike a symlink
    there's no target-resolution/follow_symlink edge case for the static
    file server to worry about. Linked under a throwaway temp name first and
    swapped into place with os.replace() so a request already in flight
    never sees latest.qcow2 briefly missing."""
    latest = ARTIFACT_DIR / "latest.qcow2"
    tmp = ARTIFACT_DIR / f".latest.qcow2.tmp-{uuid.uuid4().hex[:8]}"
    os.link(target, tmp)
    os.replace(tmp, latest)


def _delete_qcow2_artifact(path_str):
    """Delete a single qcow2 artifact file - used by the manual /cleanup/builds
    purge further down. Deliberately its own small helper rather than reusing
    prune_old_qcow2_by_age()'s loop body: that function is the tested
    automatic sweep and this is a separate, manually-triggered action, so
    keeping them apart means a change to one can't accidentally regress the
    other. Same latest.qcow2 hardlink care as that function: if this file is
    currently also pointed to by latest.qcow2 (same inode - see
    _relink_latest_qcow2 above), that link is dropped too, or "latest" would
    silently keep serving an image this purge says should be gone. Does NOT
    touch the build's database row - the caller is responsible for that."""
    path = Path(path_str)
    if not path.exists():
        return
    latest_path = ARTIFACT_DIR / "latest.qcow2"
    if latest_path.exists() and path.stat().st_ino == latest_path.stat().st_ino:
        latest_path.unlink(missing_ok=True)
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# supervised subprocess execution
#
# Every bug this dashboard has hit in practice - the stdio buffering stall,
# the pty-lingering-child hang, the SSE reconnect storm, the sync-route
# thread-pool starvation - was really one root cause wearing different
# clothes: nothing enforced an upper bound on how long this app was willing
# to wait for something external. run_supervised_command() below is the
# fix for the subprocess half of that: ONE place that runs a build command
# and guarantees it returns - via a hard kill if it has to - no matter what
# docker/packer/make/git do underneath it. run_build() (further down) is
# then just business logic (what a build means) built on top of a primitive
# that can't hang, instead of hand-rolling timeout/kill handling inline.
#
# Concretely: the previous read loop only ever exited when the pty reported
# EOF or the direct child had already exited - if that child (or anything
# it forked, including something running deep inside docker/buildx) got
# wedged instead of exiting (a stuck daemon, a hung network call, a lock
# held by a concurrent build on this shared host), the loop just polled
# forever. Because there's a single worker thread processing one build at a
# time, that didn't just strand one build - it silently killed the entire
# queue: every later build sat "queued" forever with no error and no way to
# recover short of SSHing in and restarting the whole service (which also
# discarded anything else in flight). This is what was actually happening
# when a build froze with real output on screen but "Finished: -" forever.
# ---------------------------------------------------------------------------

# build_id -> {"pgid": int, "cancel_requested": bool} for every build
# currently executing, so an HTTP request (a different thread) can ask the
# worker thread to kill one early via /builds/{id}/cancel. Guarded by
# _running_lock since it's written by the worker thread and read/written by
# request-handling threads.
_running_lock = threading.Lock()
_running_builds = {}

_KILL_REASON_TEXT = {
    "idle_timeout": f"no output for over {BUILD_IDLE_TIMEOUT_SECONDS // 60} minutes",
    "max_duration": f"exceeded the {BUILD_MAX_DURATION_SECONDS // 60}-minute maximum build duration",
    "canceled": "canceled by user",
}


def run_supervised_command(build_id, cmd, log_path, on_chunk=None):
    """Run `cmd` to completion, appending its (ANSI-cleaned) output to
    log_path as it streams, and return (exit_code, kill_reason).

    kill_reason is None on a natural exit, or one of "idle_timeout" /
    "max_duration" / "canceled" if this function had to kill the process
    itself. Two independent ceilings make that hang-proof regardless of
    what the command does or forks:

      - idle: killed if BUILD_IDLE_TIMEOUT_SECONDS pass with no new output
        at all (a stuck network call, a wedged daemon, anything that just
        goes quiet without exiting).
      - absolute: killed if BUILD_MAX_DURATION_SECONDS pass in total,
        regardless of activity (a genuine runaway, e.g. spinning and
        printing forever).

    A cooperative cancel is also honored: setting
    _running_builds[build_id]["cancel_requested"] = True (done by the
    /cancel route) kills it immediately instead of waiting out a timeout.

    The child is started in its own process group (start_new_session=True)
    specifically so a kill here takes the whole tree with it - not just the
    directly-spawned process - which is what makes this a strictly stronger
    replacement for the old "detect a lingering orphaned child holding the
    pty open" special case: that class of process (something forked that
    never closes its inherited stdout/stderr) is still attached to the same
    process group and dies with everything else once a timeout or cancel
    fires os.killpg(). The select()/proc.poll() fast path below is kept
    only as an optimization for the common case (process exits cleanly and
    promptly) - it's no longer what's relied on for correctness.
    """
    master_fd, slave_fd = pty.openpty()
    _set_pty_size(slave_fd)
    proc = subprocess.Popen(
        cmd, stdout=slave_fd, stderr=slave_fd, stdin=subprocess.DEVNULL,
        close_fds=True, start_new_session=True,
    )
    os.close(slave_fd)
    pgid = os.getpgid(proc.pid)

    with _running_lock:
        _running_builds[build_id] = {"pgid": pgid, "cancel_requested": False}

    def terminate_tree(logf):
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + BUILD_KILL_GRACE_SECONDS
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.2)
        if proc.poll() is None:
            logf.write("[dashboard] SIGTERM didn't stop it in time, sending SIGKILL\n")
            logf.flush()
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    start = time.monotonic()
    last_output = start
    kill_reason = None

    try:
        with open(log_path, "a") as logf, os.fdopen(master_fd, "rb", buffering=0) as master:
            while True:
                ready, _, _ = select.select([master_fd], [], [], 0.5)
                chunk = b""
                if ready:
                    try:
                        chunk = master.read(4096)
                    except OSError:
                        chunk = b""  # Linux ptys raise EIO once the slave side closes
                if chunk:
                    text = _clean_log_bytes(chunk)
                    logf.write(text)
                    logf.flush()
                    last_output = time.monotonic()
                    if on_chunk:
                        on_chunk(text)
                    # Deliberately NOT `continue`-ing past the checks below.
                    # A build that keeps printing steadily (never leaving a
                    # single 0.5s gap - `ready` true almost every tick) must
                    # still have its max-duration ceiling and any pending
                    # manual cancel checked on every tick, not just idle
                    # ones - otherwise a build that never goes quiet could
                    # run past BUILD_MAX_DURATION_SECONDS indefinitely, and a
                    # /cancel request against it could sit unnoticed for as
                    # long as it keeps producing output. (Caught by testing:
                    # a continuously-printing build missed its max-duration
                    # kill by several seconds before this was fixed.)

                # Check for a pending cancel BEFORE checking whether the
                # process has already exited. The /cancel route signals the
                # process group directly (not just this flag) so a manual
                # kill takes effect immediately rather than waiting for this
                # loop's next tick - which means the child can already be
                # dead by the time we get here. Checking proc.poll() first
                # would then hit the "already exited" branch below and
                # report this as a plain failure (exit code -15) instead of
                # a cancellation, misclassifying exactly the case a person
                # clicking "Kill running build" cares most about getting
                # right. (Caught by testing: a build canceled while running
                # came back as status=failed instead of status=canceled.)
                with _running_lock:
                    canceled = _running_builds[build_id]["cancel_requested"]
                if canceled:
                    kill_reason = "canceled"
                    logf.write(f"\n[dashboard] killing build - {_KILL_REASON_TEXT[kill_reason]}\n")
                    logf.flush()
                    terminate_tree(logf)  # no-op if the direct SIGTERM above already finished it
                    break

                # Whether or not this tick produced output: check whether the
                # process is actually done, or whether it's time to stop
                # waiting on it.
                if proc.poll() is not None:
                    break

                now = time.monotonic()
                if now - last_output > BUILD_IDLE_TIMEOUT_SECONDS:
                    kill_reason = "idle_timeout"
                elif now - start > BUILD_MAX_DURATION_SECONDS:
                    kill_reason = "max_duration"

                if kill_reason:
                    logf.write(f"\n[dashboard] killing build - {_KILL_REASON_TEXT[kill_reason]}\n")
                    logf.flush()
                    terminate_tree(logf)
                    break

        exit_code = proc.wait()
    finally:
        with _running_lock:
            _running_builds.pop(build_id, None)

    return exit_code, kill_reason


def run_build(build_id):
    build = get_build(build_id)
    if build is None or build["status"] == "canceled":
        return

    log_path = LOG_DIR / f"{build_id}.log"
    update_build(build_id, status="running", started_at=now_iso(), log_path=str(log_path))

    cmd = [BUILD_SYSTEM_BIN, build["component"], build["branch"]]
    if build["push"]:
        cmd.append("--push")

    with open(log_path, "w") as logf:
        logf.write(f"+ {' '.join(cmd)}\n")

    qcow2_adopted = False
    pending_line = ""

    def on_chunk(text):
        # Watch for build_system's own [QCOW2_READY] marker as it streams
        # by, same as everything else in the log - a chunk boundary can
        # land mid-line, so carry any trailing partial line over to be
        # joined with the next chunk.
        nonlocal qcow2_adopted, pending_line
        if build["component"] != "qcow2" or qcow2_adopted:
            return
        pending_line += text
        lines, sep, pending_line = pending_line.rpartition("\n")
        for line in (lines + sep).splitlines():
            if line.startswith(QCOW2_READY_PREFIX):
                _adopt_ready_qcow2(build_id, line[len(QCOW2_READY_PREFIX):])
                qcow2_adopted = True
                break

    exit_code, kill_reason = run_supervised_command(build_id, cmd, log_path, on_chunk=on_chunk)

    # Fallback for the rare case the marker was missed (e.g. an older
    # build_system without it, or a log line landing awkwardly) but the build
    # still succeeded - don't leave a successful qcow2 build without a
    # download link just because the live marker-watch above didn't fire.
    if (build["component"] == "qcow2" and not qcow2_adopted
            and exit_code == 0 and REPO_QCOW2_PATH.exists()):
        _adopt_ready_qcow2(build_id, str(REPO_QCOW2_PATH))

    # Safe: only clears untagged intermediate layers left over from repeated
    # builds. Never touches a named/tagged image, so nothing you'd want to
    # keep (e.g. an older local/platform9/* or quay.io/meghansh/proj_* tag)
    # is at risk here. Bounded with its own timeout for the same reason as
    # everything else in this file: a wedged docker daemon must not be able
    # to strand the queue on housekeeping AFTER a build has already finished.
    try:
        subprocess.run(["docker", "image", "prune", "-f"], capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        pass

    if kill_reason == "canceled":
        status = "canceled"
    elif kill_reason:
        status = "failed"
    else:
        status = "success" if exit_code == 0 else "failed"

    update_build(
        build_id,
        status=status,
        finished_at=now_iso(),
        exit_code=exit_code,
        note=_KILL_REASON_TEXT.get(kill_reason),
    )
    # prune_old_qcow2() only ever counts status='success' rows (see its own
    # docstring) - _adopt_ready_qcow2() may have run this already while the
    # row was still "running" (a no-op for THIS row at the time, since it
    # wasn't 'success' yet), so run it again now that the status update above
    # has actually landed, or this build's own artifact would never count
    # toward the keep-last-N retention window.
    final = get_build(build_id)
    if final and final["qcow2_path"]:
        prune_old_qcow2()


def worker_loop():
    while True:
        build_id = job_queue.get()
        try:
            run_build(build_id)
        except Exception as e:
            update_build(build_id, status="failed", finished_at=now_iso(), note=f"dashboard error: {e}")
            with open(LOG_DIR / f"{build_id}.log", "a") as logf:
                logf.write(f"\n[dashboard error] {e}\n")
        finally:
            with _running_lock:
                _running_builds.pop(build_id, None)
            job_queue.task_done()


def retention_sweep_loop():
    # Runs independently of the build queue/worker thread above and of any
    # build actually happening - a host that's been idle for days still needs
    # its stale qcow2 images cleared out (the count-based prune_old_qcow2()
    # only ever fires when a NEW qcow2 lands). Deliberately a plain daemon
    # thread inside this same process rather than a separate cron job/systemd
    # timer: it shares the app's one SQLite connection pattern instead of a
    # second process racing it on the same DB file, it always uses whatever
    # QCOW2_RETENTION_HOURS this service was actually started with instead of
    # a value hardcoded into a separate script, and "work across reboots"
    # comes for free from the .service unit already being `systemctl enable`d
    # (WantedBy=multi-user.target) - when the host reboots, systemd starts
    # this process, and this thread starts with it. Nothing extra to install.
    # Log cleanup (prune_old_logs) rides along on the same timer, on the same
    # QCOW2_RETENTION_HOURS window, by deliberate choice - one retention
    # policy, not two to keep in sync. Each task runs in its own try/except
    # so one of them erroring doesn't skip the other on that tick.
    while True:
        for task in (prune_old_qcow2_by_age, prune_old_logs):
            try:
                task()
            except Exception as e:
                print(f"[dashboard] retention sweep error ({task.__name__}): {e}", flush=True)
        time.sleep(RETENTION_SWEEP_INTERVAL_SECONDS)


enqueue_existing_queued_jobs()
threading.Thread(target=worker_loop, daemon=True).start()
threading.Thread(target=retention_sweep_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "components": COMPONENTS,
        "builds": list_recent_builds(BUILD_HISTORY_HOURS),
        "queue_size": job_queue.qsize(),
        "latest_download_url": str(request.base_url).rstrip("/") + "/artifacts/latest.qcow2",
        "quay_namespace": QUAY_NAMESPACE,
        "quay_repos_by_component": QUAY_REPOS_BY_COMPONENT,
        "keep_last_n_qcow2": KEEP_LAST_N_QCOW2,
        "build_history_hours": BUILD_HISTORY_HOURS,
        "qcow2_retention_hours": QCOW2_RETENTION_HOURS,
        "active_page": "dashboard",
    })


@app.post("/build")
def start_build(component: str = Form(...), branch: str = Form(...)):
    component = validate_component(component)
    branch = validate_branch(branch)

    # Pushing the component image(s) to quay.io is no longer optional for
    # any build kind, so there's nothing left for a form field to choose -
    # this used to be a user-facing checkbox for qcow2 (see git history of
    # index.html), forced server-side only for component builds. Now:
    #   - a component build (controller/ui/sdk/ai) still has no download
    #     link from this dashboard at all, so pushing is the only way to
    #     get the image out of this build, same as before.
    #   - build_system's qcow2 flow itself always pushes its 5 component
    #     images to quay.io now, regardless of --push (see that module's
    #     docstring) - the manifests baked into the qcow2, and the
    #     v2v-helper reference compiled into the controller binary, both
    #     point directly at those repos, so a qcow2 whose push failed would
    #     ship with dangling image references.
    # Always true here (not just defaulted) so it still holds for a raw form
    # post that omits the field entirely.
    push = True

    build_id = uuid.uuid4().hex[:12]
    with db() as conn:
        conn.execute(
            "INSERT INTO builds (id, component, branch, push, status, created_at) "
            "VALUES (?, ?, ?, ?, 'queued', ?)",
            (build_id, component, branch, int(push), now_iso()),
        )
    job_queue.put(build_id)
    return RedirectResponse(f"/builds/{build_id}", status_code=303)


@app.get("/builds/{build_id}", response_class=HTMLResponse)
def build_detail(request: Request, build_id: str):
    build = get_build(build_id)
    if not build:
        raise HTTPException(404, "No such build")
    # Rendered inline so the page shows the log immediately on load instead
    # of a blank pane until the first poll completes - build.html's JS then
    # takes over and replaces this wholesale on every poll tick regardless.
    log_path = LOG_DIR / f"{build_id}.log"
    initial_log = log_path.read_text() if log_path.exists() else ""
    return templates.TemplateResponse(request, "build.html", {
        "build": build,
        "idle_timeout_seconds": BUILD_IDLE_TIMEOUT_SECONDS,
        "initial_log": initial_log,
        "queue_position": queue_position(build_id) if build["status"] == "queued" else None,
    })


@app.get("/builds/{build_id}/log", response_class=PlainTextResponse)
def get_log(build_id: str):
    # Always the complete log from byte 0, for a running build as much as a
    # finished one. build.html polls this on a plain timer and replaces its
    # whole display with whatever comes back - see the top-of-file note for
    # why that's deliberate: no byte offsets, no "since last time", no
    # server-held connection at all, so there's no reconnect/resume logic
    # that can desync or duplicate content the way the SSE version used to.
    build = get_build(build_id)
    if not build:
        raise HTTPException(404, "No such build")
    log_path = LOG_DIR / f"{build_id}.log"
    if not log_path.exists():
        return ""
    return log_path.read_text()


@app.get("/builds/{build_id}/status")
def build_status(build_id: str):
    # The other half of build.html's polling: a small stateless JSON snapshot
    # of everything the page needs to update itself (status badge, note,
    # download link, kill-vs-cancel button) without a full page reload. Each
    # call is independent - nothing here is held open or remembered between
    # requests, on purpose (see the top-of-file note on why the live-tailing
    # approach this replaced was the wrong shape for this app).
    build = get_build(build_id)
    if not build:
        raise HTTPException(404, "No such build")
    return {
        "status": build["status"],
        "note": build["note"],
        "exit_code": build["exit_code"],
        "started_at": build["started_at"],
        "finished_at": build["finished_at"],
        # A plain static-file path (see the /artifacts mount), not the
        # dynamic /builds/{id}/download route - that's what makes this safe
        # to paste as an image-import URL into something like Private Cloud
        # Director's "Add Image" dialog, which can reject a dynamic-looking
        # URL outright before ever fetching it.
        "download_url": f"/artifacts/{build_id}.qcow2" if build["qcow2_path"] else None,
        "queue_position": queue_position(build_id) if build["status"] == "queued" else None,
    }


@app.post("/builds/{build_id}/cancel")
def cancel_build(build_id: str):
    build = get_build(build_id)
    if not build:
        raise HTTPException(404, "No such build")

    if build["status"] == "queued":
        # Never started - the worker checks status == "canceled" itself
        # before running anything (see run_build), so this alone is enough.
        update_build(build_id, status="canceled", finished_at=now_iso(), note="canceled while queued")
        return RedirectResponse(f"/builds/{build_id}", status_code=303)

    if build["status"] == "running":
        # Closes the gap where the only way to recover from a stuck build
        # used to be SSHing in and restarting the whole service - which also
        # killed every other queued build. Signal the process group directly
        # and flag the cancel; run_supervised_command's own loop (running in
        # the worker thread) notices one of the two and finalizes the
        # build's status itself, so this request doesn't race that update.
        with _running_lock:
            entry = _running_builds.get(build_id)
            if entry:
                entry["cancel_requested"] = True
                pgid = entry["pgid"]
            else:
                pgid = None
        if pgid is None:
            # Status says "running" but the worker hasn't registered it yet
            # (a tiny startup window) or already finished it (a race with
            # this request) - either way, nothing to signal right now.
            raise HTTPException(409, "Build is starting or finishing right now - try again in a moment")
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        return RedirectResponse(f"/builds/{build_id}", status_code=303)

    raise HTTPException(400, f"Build is already {build['status']} - nothing to cancel")


@app.on_event("shutdown")
def _kill_running_builds_on_shutdown():
    # The build's process group is independent of this app's own (see
    # start_new_session=True in run_supervised_command), specifically so a
    # timeout/cancel can kill the whole tree cleanly - but that same
    # independence means a plain `systemctl stop`/restart of this service
    # won't automatically take a still-running build down with it the way it
    # would have before. Best-effort SIGTERM anything still tracked so
    # restarting the dashboard doesn't leave an orphaned docker/packer/make
    # tree burning CPU on this shared host with nothing left tracking it.
    with _running_lock:
        pgids = [entry["pgid"] for entry in _running_builds.values()]
    for pgid in pgids:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _qcow2_file_response(build):
    filename = f"vjailbreak-{build['branch'].replace('/', '-')}-{build['id'][:8]}.qcow2"
    # FileResponse (Starlette) honors Range requests out of the box, so this
    # also works with `wget -c` / `curl -C -` for resuming a large transfer,
    # not just a plain single-shot download.
    return FileResponse(build["qcow2_path"], filename=filename, media_type="application/octet-stream")


@app.get("/builds/{build_id}/download")
def download_qcow2(build_id: str):
    # This has always been a plain GET with no auth/session/cookie - `wget`,
    # `curl -O`, or an Ansible/CI job can already hit this URL directly, the
    # same as clicking the "Download qcow2" link does. /download/latest below
    # just saves having to know a specific build_id in advance.
    build = get_build(build_id)
    if not build or not build["qcow2_path"] or not Path(build["qcow2_path"]).exists():
        raise HTTPException(404, "No qcow2 artifact available for this build")
    return _qcow2_file_response(build)


@app.get("/download/latest")
def download_latest_qcow2():
    """Stable URL for scripts/CI: always serves the most recently-produced
    qcow2, without needing to know a build_id up front - e.g.
    `wget http://<this-host>/download/latest -O vjailbreak-image.qcow2` (plain
    port 80, so no ":port" needed in the URL).
    Deliberately not restricted to status='success': build_system marks the
    qcow2 as ready (see [QCOW2_READY] in build_system, and _adopt_ready_qcow2
    in run_build above) as soon as the disk image itself is done, even if the
    mandatory push of the component images to quay.io is still running - so
    this serves the image as soon as it exists, exactly like the per-build
    /download link does, rather than waiting on that unrelated upload too."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM builds WHERE component = 'qcow2' AND qcow2_path IS NOT NULL "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    build = dict(row) if row else None
    if not build or not Path(build["qcow2_path"]).exists():
        raise HTTPException(404, "No qcow2 build has produced a downloadable image yet")
    return _qcow2_file_response(build)


@app.get("/api/builds")
def api_builds():
    return list_builds(100)


# ---------------------------------------------------------------------------
# manual cleanup - a separate tab of button-triggered, on-demand actions.
#
# Deliberately distinct from retention_sweep_loop above: that background
# sweep is automatic, silent, and - per an explicit product decision - never
# deletes a row from the builds table, only qcow2 files and log files as they
# age out. This is the opposite in every one of those respects: it only ever
# runs when a person on this page clicks a button, and the build-records
# purge below DOES delete the database row (along with its log and qcow2
# artifact) on purpose - that's the whole point of giving it its own
# explicit, confirm-guarded control instead of folding it into the automatic
# sweep. A queued or running build is never a candidate for either action.
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = ("success", "failed", "canceled")


@app.get("/cleanup", response_class=HTMLResponse)
def cleanup_page(request: Request, docker_msg: Optional[str] = None, builds_msg: Optional[str] = None):
    placeholders = ", ".join("?" for _ in TERMINAL_STATUSES)
    with db() as conn:
        purge_candidates = conn.execute(
            f"SELECT COUNT(*) AS n FROM builds WHERE status IN ({placeholders})",
            TERMINAL_STATUSES,
        ).fetchone()["n"]
    qcow2_count = sum(1 for p in ARTIFACT_DIR.glob("*.qcow2") if p.name != "latest.qcow2")
    log_count = sum(1 for _ in LOG_DIR.glob("*.log"))
    return templates.TemplateResponse(request, "cleanup.html", {
        "active_page": "cleanup",
        "docker_msg": docker_msg,
        "builds_msg": builds_msg,
        "purge_candidates": purge_candidates,
        "qcow2_count": qcow2_count,
        "log_count": log_count,
        "default_older_than_hours": QCOW2_RETENTION_HOURS,
    })


@app.post("/cleanup/docker")
def cleanup_docker():
    """Manual, on-demand local docker image prune - the exact same
    `docker image prune -f` that already runs automatically after every
    build (see run_build above), just runnable on demand without waiting for
    one. Safe to run at any time: it only ever clears untagged/dangling
    layers, never a named/tagged image, so nothing you'd want to keep is at
    risk. Bounded with the same 60s timeout as the automatic call for the
    same reason - a wedged docker daemon must not be able to hang this
    request forever."""
    try:
        result = subprocess.run(
            ["docker", "image", "prune", "-f"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            msg = (result.stdout or "").strip() or "Nothing to clean up."
        else:
            msg = f"docker image prune failed: {(result.stderr or '').strip() or 'unknown error'}"
    except subprocess.TimeoutExpired:
        msg = "docker image prune timed out after 60s."
    except FileNotFoundError:
        msg = "docker binary not found on this host."
    return RedirectResponse(f"/cleanup?docker_msg={urllib.parse.quote(msg)}", status_code=303)


@app.post("/cleanup/builds")
def cleanup_builds(older_than_hours: int = Form(...)):
    """Manual, on-demand purge of old FINISHED build records: deletes the
    database row itself, plus its log file and qcow2 artifact if either still
    exist - unlike the automatic retention_sweep_loop, which (per explicit
    product decision) only ever ages out qcow2/log files and leaves every
    build's database row alone forever. Only ever matches terminal builds
    (success/failed/canceled) older than the given cutoff - a queued or
    running build is never a candidate here regardless of its age, so this
    can never reach into the live queue or affect a build in flight."""
    if older_than_hours < 0:
        raise HTTPException(400, "older_than_hours must be >= 0")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat(timespec="microseconds")
    placeholders = ", ".join("?" for _ in TERMINAL_STATUSES)
    with db() as conn:
        rows = conn.execute(
            f"SELECT id, qcow2_path FROM builds WHERE status IN ({placeholders}) AND created_at < ?",
            (*TERMINAL_STATUSES, cutoff),
        ).fetchall()
    for row in rows:
        if row["qcow2_path"]:
            _delete_qcow2_artifact(row["qcow2_path"])
        (LOG_DIR / f"{row['id']}.log").unlink(missing_ok=True)
        with db() as conn:
            conn.execute("DELETE FROM builds WHERE id = ?", (row["id"],))
    msg = f"Deleted {len(rows)} build record(s) older than {older_than_hours}h."
    return RedirectResponse(f"/cleanup?builds_msg={urllib.parse.quote(msg)}", status_code=303)
