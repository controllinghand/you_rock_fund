"""Shared rotating-log configuration for every YRVI entry point.

Before this existed each module called `logging.basicConfig()` with a plain
`FileHandler`, so nothing ever bounded the files. On YRVIP `wheel_log.txt`
reached **523 MB / 2.36 M lines** between 2026-06-18 and 2026-08-29 — big
enough that reading it during an incident is itself a chore.

The growth is bursty, not steady: ~40 of those 72 days wrote nothing at all,
while gateway-wedge days (2026-07-17..20, 08-11..17) wrote ~100 k lines each
as IBKR reconnect attempts retried in a loop, every attempt logging ib_insync's
full connect → position-dump → disconnect sequence. So the sizing below has to
survive a single bad day (~25 MB) without evicting every prior week: at
25 MB × 6 backups one wedge day costs one slot and leaves five for history,
with a hard ceiling of 175 MB per log.

Two details this module exists to get right:

1. **Symlinks.** In the containers `/app/<log>.txt` is a symlink into `/data`
   (see docker/entrypoint-secrets.sh). `RotatingFileHandler` rolls over with
   `os.rename(self.baseFilename, ...)`, and renaming a *symlink* moves the link
   rather than the file it points at — the real log would be orphaned in /data
   and new output would land in a container-local regular file that vanishes on
   the next `compose up`. Resolving the path first keeps rotation inside /data.

2. **basicConfig semantics.** `logging.basicConfig()` is a no-op once the root
   logger has handlers, which is load-bearing here: the scheduler configures
   `scheduler_log.txt` at import and only *later* imports `wheel_manager`, whose
   own configuration must not steal the root logger. `configure()` keeps that
   first-caller-wins behaviour exactly.

Note that these files can have more than one writer (the api container picks up
whichever log a lazily-imported module configures, and `python wheel_manager.py
detect` can be run by hand alongside it). `RotatingFileHandler` is not
multi-process safe: if two processes hold the same file across a rollover, the
loser keeps appending to the already-renamed backup. That misfiles some lines
but never corrupts the active log, and in practice the writers here target
different files; a shared-lock handler would mean a new dependency for a
cosmetic gain.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_FORMAT   = "%(asctime)s  %(levelname)s  %(message)s"
MAX_BYTES    = 25 * 1024 * 1024   # ~one wedge-day of ib_insync retry chatter
BACKUP_COUNT = 6                  # → 175 MB ceiling per log


def rotating_handler(filename: str) -> RotatingFileHandler:
    """A size-capped handler for `filename`, following symlinks to the real path."""
    handler = RotatingFileHandler(
        os.path.realpath(filename),      # see note 1 in the module docstring
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return handler


def configure(filename: str, level: int = logging.INFO) -> logging.Logger:
    """Point the root logger at a rotating `filename` plus stderr.

    A drop-in for the `logging.basicConfig(handlers=[FileHandler(...),
    StreamHandler()])` calls this replaced, including the no-op-if-already-
    configured behaviour those relied on (see note 2 in the module docstring).
    """
    root = logging.getLogger()
    if root.handlers:
        return root

    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(LOG_FORMAT))

    root.addHandler(rotating_handler(filename))
    root.addHandler(stream)
    root.setLevel(level)
    return root
