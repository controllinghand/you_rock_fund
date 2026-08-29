"""Shared logging setup for YRVI: rotation, plus a fixed home for every log line.

## Rotation

Nothing used to bound these files. On YRVIP `wheel_log.txt` reached **523 MB /
2.36 M lines** between 2026-06-18 and 2026-08-29 — big enough that reading it
during an incident is itself a chore.

The growth is bursty, not steady: ~40 of those 72 days wrote nothing at all,
while gateway-wedge days (2026-07-17..20, 08-11..17) wrote ~100 k lines each
as IBKR reconnect attempts retried in a loop, every attempt logging ib_insync's
full connect → position-dump → disconnect sequence. So the sizing has to
survive a single bad day (~25 MB) without evicting every prior week: at
25 MB × 6 backups one wedge day costs one slot and leaves five for history,
with a hard ceiling of 175 MB per log.

## Routing

Which file a line landed in used to depend on **import order**, because every
module configured the *root* logger and `logging.basicConfig()` is a no-op once
root has handlers. Whoever imported first won, and everyone else's file stayed
empty. The result on YRVIP, where the docs promise one file per concern:

    risk_log.txt      0 bytes since 2026-06-18   (the risk monitor ran ~30 times)
    trade_log.txt   258 bytes since 2026-06-22
    wheel_log.txt   523 MB — almost entirely the *api* container's ib_insync
                    chatter, because a dashboard Run Now lazily imports
                    wheel_manager and that call captured root for the process

Two logger kinds now, and neither depends on import order:

* `get_logger(name, filename)` — for a module. Owns a rotating handler on its
  own file, so its lines always land there no matter who imported it or when.
  It still propagates, so those lines also reach the console and the process
  log below; the per-module file is an addition, not a redirection.
* `configure_root(filename)` — for an entry point, called once. Puts the
  console handler and (for a long-running service) a process log on root. This
  is where library noise ends up — ib_insync, apscheduler, uvicorn — since none
  of it belongs to a YRVI module. `scheduler.py` uses `scheduler_log.txt` and
  `api.py` uses `api_log.txt`; that split alone is what stops dashboard polling
  from filling a *wheel* log.

Running a module directly (`python wheel_manager.py detect`) configures root
console-only: the module's own lines are still persisted by its own handler,
and library chatter goes to the terminal rather than into the module's file.

Module loggers take explicit names rather than `__name__`, which changes to
`"__main__"` under a direct run and would otherwise split one module's lines
across two logger identities.

## Symlinks

In the containers `/app/<log>.txt` is a symlink into `/data` (see
docker/entrypoint-secrets.sh). `RotatingFileHandler` rolls over with
`os.rename(self.baseFilename, ...)`, and renaming a *symlink* moves the link
rather than the file it points at — the real log would be orphaned in /data and
new output would land in a container-local regular file that vanishes on the
next `compose up`. `rotating_handler()` resolves the path first so rotation
happens inside /data and the symlink survives.

## Concurrency

A file can have more than one writing process (the scheduler's Monday run and a
dashboard Run Now both log to `wheel_log.txt`). `RotatingFileHandler` is not
multi-process safe: if two processes hold the same file across a rollover, the
loser keeps appending to the already-renamed backup. That misfiles some lines
but never corrupts the active log, and a shared-lock handler would mean a new
dependency for a cosmetic gain. Within a single process each file has exactly
one handler, which is the case that would actually race.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_FORMAT   = "%(asctime)s  %(levelname)s  %(message)s"
MAX_BYTES    = 25 * 1024 * 1024   # ~one wedge-day of ib_insync retry chatter
BACKUP_COUNT = 6                  # → 175 MB ceiling per log

_OWNED = "_yrvi_handler"          # marks handlers we added, so repeat calls no-op


def rotating_handler(filename: str) -> RotatingFileHandler:
    """A size-capped handler for `filename`, resolving symlinks to the real path."""
    handler = RotatingFileHandler(
        os.path.realpath(filename),      # see "Symlinks" in the module docstring
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    setattr(handler, _OWNED, True)
    return handler


def get_logger(name: str, filename: str, level: int = logging.INFO) -> logging.Logger:
    """A module's logger, pinned to its own file regardless of import order.

    Propagation is left on: the same lines still reach the console and whatever
    process log `configure_root()` installed. This adds a guaranteed home for
    the module's output, it does not take it away from anywhere.
    """
    logger = logging.getLogger(name)
    if not any(getattr(h, _OWNED, False) for h in logger.handlers):
        logger.addHandler(rotating_handler(filename))
        logger.setLevel(level)
    return logger


def configure_root(filename: str | None = None, level: int = logging.INFO) -> logging.Logger:
    """Install the console handler, and a process log when `filename` is given.

    Entry points only, called once — this is where library output (ib_insync,
    apscheduler, uvicorn) lands, since it belongs to no YRVI module. Pass no
    filename for a short-lived direct run, where the terminal is the log.
    """
    root = logging.getLogger()
    if any(getattr(h, _OWNED, False) for h in root.handlers):
        return root

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    setattr(console, _OWNED, True)
    root.addHandler(console)

    if filename:
        root.addHandler(rotating_handler(filename))
    root.setLevel(level)
    return root
