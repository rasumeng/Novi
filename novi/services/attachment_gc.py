"""Attachment orphan GC — Task 3.2.

Sweeps attachments no longer referenced by any conversation .md
(@attachments markers). Immediate deletion for dereferenced is fine;
no grace window required for beta. Thumbs swept alongside mains.

Attachments stored locally in ~/.novi/attachments — pruned when
conversations deleted or on next restart (startup/shutdown sweep).
100 MB per-file limit enforced at upload (webui_server MAX_UPLOAD_SIZE).
"""

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("novi.webui_server.attachments")

# Grace for startup sweep: orphan files older than this are considered safe to delete.
# 7 days default; dereferenced deletes are immediate (grace 0).
DEFAULT_GRACE_SECONDS = 7 * 24 * 3600


def _is_old_enough(path: Path, grace_seconds: int) -> bool:
    if grace_seconds <= 0:
        return True
    try:
        return (time.time() - path.stat().st_mtime) > grace_seconds
    except Exception:
        return True


def referenced_attachment_ids(chats_dir: Path) -> set[str]:
    """Collect attachment ids referenced by any .md @attachments line."""
    ids: set[str] = set()
    if not chats_dir.exists():
        return ids
    for md in chats_dir.glob("*.md"):
        try:
            text = md.read_text("utf-8")
        except Exception:
            continue
        for line in text.splitlines():
            if not line.startswith("@attachments "):
                continue
            payload = line[len("@attachments ") :].strip()
            if not payload:
                continue
            try:
                arr = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(arr, list):
                continue
            for entry in arr:
                if isinstance(entry, dict) and "id" in entry:
                    ids.add(str(entry["id"]))
    return ids


def sweep_orphan_attachments(
    attachments_dir: Path,
    chats_dir: Path,
    grace_seconds: int = 0,
) -> int:
    """Delete unreferenced files + thumbs. Returns count deleted (files+thumbs).

    grace_seconds: if >0, only delete files older than grace (for startup orphan
    sweep). Dereferenced deletes (after conversation/attachment delete) use 0 for
    immediate cleanup.
    """
    referenced = referenced_attachment_ids(chats_dir)
    if not attachments_dir.exists():
        return 0
    deleted = 0
    # main files (skip thumbs dir)
    for p in list(attachments_dir.iterdir()):
        if p.is_dir():
            continue
        if not p.is_file():
            continue
        if p.name.endswith(".tmp"):
            continue
        att_id = p.stem
        if att_id not in referenced and _is_old_enough(p, grace_seconds):
            try:
                p.unlink()
                deleted += 1
                log.info("gc deleted orphan attachment %s", p.name)
            except Exception as e:
                log.warning("gc delete failed for %s: %s", p, e)
                continue
            # paired thumb (same filename)
            thumb = attachments_dir / "thumbs" / p.name
            if thumb.exists():
                try:
                    thumb.unlink()
                    deleted += 1
                    log.info("gc deleted orphan thumb %s", thumb.name)
                except Exception as e:
                    log.warning("gc delete thumb failed for %s: %s", thumb, e)
            # also sweep any thumb with same stem but different ext (defensive)
            thumbs_dir = attachments_dir / "thumbs"
            if thumbs_dir.exists():
                for tp in list(thumbs_dir.iterdir()):
                    if tp.stem == att_id and tp.exists() and _is_old_enough(tp, grace_seconds):
                        try:
                            tp.unlink()
                            deleted += 1
                            log.info("gc deleted orphan thumb %s", tp.name)
                        except Exception as e:
                            log.warning("gc delete thumb failed for %s: %s", tp, e)
    # orphan thumbs without main file (e.g. main already gone)
    thumbs_dir = attachments_dir / "thumbs"
    if thumbs_dir.exists():
        for tp in list(thumbs_dir.iterdir()):
            if not tp.is_file():
                continue
            att_id = tp.stem
            if att_id not in referenced and _is_old_enough(tp, grace_seconds):
                try:
                    tp.unlink()
                    deleted += 1
                    log.info("gc deleted orphan thumb %s", tp.name)
                except Exception as e:
                    log.warning("gc delete thumb failed for %s: %s", tp, e)
    return deleted
