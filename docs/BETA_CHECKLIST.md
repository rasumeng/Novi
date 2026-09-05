# Beta Verification Gate — Checklist

> Release gate for `beta` tag. No beta tag unless **Automated gate** AND **Manual matrix** both pass.
> Runner: `python scripts/verify_beta.py` (or `py scripts/verify_beta.py` on Windows).
> Plan: `docs/superpowers/plans/2026-09-03-beta-stabilization-sprint.md` § Task 5.1.

## Automated Gate (must all pass)

Run via `python scripts/verify_beta.py` which executes:

```bash
pytest -q
tsc --noEmit  # in novi/webui
npm --prefix novi/webui run build
python -m pytest tests/test_conversation_persistence_atomic.py tests/test_project_conversation_linking.py tests/test_capability_verification.py tests/test_discovery_honest_errors.py -v
```

| Check | Command | Status |
|-------|---------|--------|
| Full test suite | `pytest -q` | ☐ Pass / ☐ Fail |
| TypeScript typecheck | `tsc --noEmit` in `novi/webui` | ☐ Pass / ☐ Fail |
| Production build | `npm --prefix novi/webui run build` | ☐ Pass / ☐ Fail |
| Targeted persistence/capability/discovery gates | `pytest tests/test_conversation_persistence_atomic.py tests/test_project_conversation_linking.py tests/test_capability_verification.py tests/test_discovery_honest_errors.py -v` | ☐ Pass / ☐ Fail |

CI must block merge to `main`/`beta` if `pytest` or `tsc` fails.

---

## Manual First-Run Matrix (must all pass with evidence)

Fresh profile `~/.novi` or isolated `NOVI_HOME` recommended. Record evidence: screenshot or short note per row.

| # | Scenario | Expected | Pass | Evidence (screenshot / note) |
|---|----------|----------|------|------------------------------|
| 1 | First launch | Clear coherent setup state (no phantom models, hardware bar shows Unknown not 0) | ☐ | |
| 2 | Ollama unavailable | Explicit actionable error (status degraded, stale badge, retry) | ☐ | |
| 3 | Ollama available, no models | Clear guidance (no models detected + install CTA) | ☐ | |
| 4 | New uncached vision model | Not falsely rejected (image succeeds after verify) | ☐ | |
| 5 | Known unsupported model | Correctly blocked (unsupported message) | ☐ | |
| 6 | Capability verification failure | Never reported as confirmed incompatibility (shows verification_failed + retry) | ☐ | |
| 7 | Image attachment | Correct validation and execution | ☐ | |
| 8 | Search disabled | Clear configuration guidance (not_configured) | ☐ | |
| 9 | Permission ignored (timeout) | Explicit timeout/denial trace | ☐ | |
| 10 | App restart | Conversations/projects persist correctly | ☐ | |
| 11 | Rapid conversation updates | No corruption or lost state (concurrent test + manual rapid edits) | ☐ | |
| 12 | Project reassignment | Relationship remains consistent (canonical check) | ☐ | |
| 13 | Attachment deletion | Files eventually cleaned (GC sweep) | ☐ | |
| 14 | Runtime failure | User receives actionable explanation (ModelUnavailableError etc.) | ☐ | |

### Manual test notes

- **First launch**: delete or move `~/.novi`, start app/backend, verify welcome/setup not showing stale phantom models; hardware bar reads `Unknown` not `0` when Ollama down.
- **Ollama unavailable**: stop Ollama daemon, refresh Models → banner `Ollama not reachable at {url} — showing cached inventory … [Retry]`, status `degraded`.
- **No models**: `ollama list` empty but daemon up → banner `No models detected — install with ollama pull …` CTA.
- **New uncached vision model**: `ollama pull <new-vl-model>` never seen before, attach image → not rejected; succeeds after live `/api/show` verify (or `capability_unverified` trace if safe).
- **Known unsupported**: pure text model + image → blocked with `unsupported` message (not `unknown`).
- **Verification failure**: disconnect Ollama mid-verify or 404 → UI shows `verification_failed — Retry` never `does not support`.
- **Image attachment**: upload png/jpeg via PromptInput, validate thumb, send → correct tool/capability path.
- **Search disabled**: `search.backend=""` → status `Search not configured — set Brave API key or SearXNG URL in Settings → Connectors`.
- **Permission timeout**: trigger tool requiring permission, ignore 120s → toast countdown `Deny in 1:58`, trace `permission_denied reason: timeout`.
- **App restart**: create conv+project, restart, `GET /api/conversations` + `GET /api/projects` reloaded intact.
- **Rapid updates**: edit same conversation title/content rapidly 10x or parallel PUTs → `index.json` valid JSON, `.md` parses.
- **Project reassignment**: move conversation between projects via `PUT /api/conversations {projectId}`, verify `GET /api/projects/{id}/conversations` derived list consistent; restart → still consistent.
- **Attachment deletion**: upload attachment, delete conversation, restart → `~/.novi/attachments/{id}` pruned (startup GC) or immediately on delete; thumb log on failure.
- **Runtime failure**: stop Ollama during generation or bad model name → toast/trace `ModelUnavailableError — connect Ollama and retry` not silent.

---

## Sign-off

| Reviewer | Date (UTC) | Automated gate | Manual matrix | Notes / artifact link |
|----------|------------|----------------|---------------|-----------------------|
|          |            | ☐ Pass / ☐ Fail | ☐ Pass / ☐ Fail | |
|          |            | ☐ Pass / ☐ Fail | ☐ Pass / ☐ Fail | |

- Gate passes only when both columns are **Pass** for at least one reviewer.
- Attach `scripts/verify_beta.py` output (or CI artifact) and 14 evidence screenshots/notes to release PR.

## How to run

```bash
# from repo root
python scripts/verify_beta.py
# Windows
py scripts/verify_beta.py
```

Each step prints PASS/FAIL. Non-zero exit if any step fails. On success, prints manual matrix reminder.
