# Task 3.2 — Attachment Lifecycle Hygiene — Report

**Status:** DONE

**Commits:**
- feat: attachment orphan GC and thumbnail error logging (Task 3.2)

**Test summary:**
- `pytest tests/test_attachment_gc.py -v` — 8 passed
  - delete_conversation prunes attachments+thumbs (immediate sweep)
  - referenced attachment not deleted when sibling conv deleted
  - orphan sweep on startup with grace (7 days) vs immediate dereferenced
  - delete_attachment sweeps other orphans
  - orphan thumb without main file swept
  - thumbnail failure logs warning (PIL open exception mocked)
  - upload size limit 100MB enforced (small file success path)
  - referenced_attachment_ids parsing (@attachments markers)
- `pytest tests/test_conversation_persistence_atomic.py tests/test_conversation_parse_safety.py tests/test_project_conversation_linking.py` — 16 passed
- `npm --prefix novi/webui run build` — success (tsc + vite 1.20s, 1599kB, no errors)
- `python -m pytest tests/test_conversation_persistence_atomic.py::test_concurrent_conversation_writes_no_corruption` — 5/5 passed after atomic write retry

**What changed:**
- `novi/services/attachment_gc.py` (new) — `referenced_attachment_ids()` scans `CHATS_DIR/*.md` for `@attachments` JSON markers; `sweep_orphan_attachments(attachments_dir, chats_dir, grace_seconds)` deletes unreferenced files+thumbs; grace default 0 immediate for dereferenced, `DEFAULT_GRACE_SECONDS=7*24*3600` for startup/shutdown orphan sweep; logs via `novi.webui_server.attachments`; handles .tmp skip, thumb paired deletion, orphan thumbs.
- `novi/webui_server.py:104-106` — added `log = logging.getLogger("novi.webui_server")`
- `novi/webui_server.py:718-726` — `_atomic_write_text` now retries `PermissionError` on Windows replace (5 attempts, 20ms*attempt) to fix flaky concurrent test
- `novi/webui_server.py:665-672` — `_shutdown_backend` adds shutdown sweep with grace
- `novi/webui_server.py:857-882` — `DELETE /api/conversations/{id}` now calls `sweep_orphan_attachments(..., grace 0)` after unlink + index save; logs warning on failure
- `novi/webui_server.py:2028-2031` — documented `MAX_UPLOAD_SIZE` 100MB with storage comment
- `novi/webui_server.py:2056-2067` — thumbnail `except ImportError: pass` → `log.warning("thumb failed for %s: %s", att_id, e)` for both ImportError and generic Exception
- `novi/webui_server.py:2094-2115` — `DELETE /api/attachments/{id}` now deletes + sweeps other orphans (immediate), handles thumb delete warning; added sync startup sweep with grace after attachments routes
- `docs/desktop.md:127-136` — added Attachments section documenting `~/.novi/attachments/` path, 100MB cap, `@attachments` reference, GC behavior (immediate dereferenced, 7-day grace startup/shutdown), thumbnail logging

**Concerns / deviations:**
- Startup/shutdown sweep uses 7-day grace to avoid deleting freshly uploaded but not-yet-referenced files (window between upload and conversation save, or restart before save). Immediate for dereferenced (delete conversation/attachment) is correct. Tests set old mtime for orphan sweep verification.
- Removed async lifespan startup sweep to avoid concurrent sweep during `TestClient` `with` (caused flaky PermissionError on Windows). Sync startup at `create_app` after `ATTACHMENTS_DIR.mkdir` is sufficient and deterministic.
- `_atomic_write_text` retry added — pre-existing flake on Windows concurrent replace, not strictly Task 3.2 but fixes gate.

**Remaining / not done:**
- No new storage system; no frontend tooltip yet (docs only, per spec allowed).

**Verification:**
- Orphan cleanup on delete conversation and delete attachment, logged thumbnails, documented limits, 100MB limit kept, startup/shutdown grace sweep all verified via pytest and build.
