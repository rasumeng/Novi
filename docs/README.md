# Cozmo Documentation

Navigation map for the Cozmo repository. If you are new here, read in this order:

1. **`../README.md`** — product overview, quick start, feature list.
2. **`../PLAN.md`** — the architecture evolution plan and phase roadmap.
3. **`architecture/`** — the durable specs. Start with `brain-evolution.md`, then `brain-architecture.md`, then `phaseF-design.md`.
4. **`archive/`** — historical phase plans and blueprints. Read only if you want the *history* of how a subsystem got built; they do not describe the current system.
5. **`audits/`** — point-in-time review reports and release assessments.
6. **`../CHANGELOG.md`** — user/feature-facing changes by version.
7. **`../DEVLOG.md`** — timestamped engineering journal of what changed and when.

Forward-looking plans live at the docs root:

* **`ROADMAP-phaseG.md`** — the post-Brain-V1 cleanup/debt/migration roadmap.

---

## Layout

```text
docs/
    README.md               this index
    architecture/           durable specs, never archived
        brain-evolution.md      (Brain V1 technical history — start here)
        brain-architecture.md   (Brain design reference)
        phaseF-design.md        (cognitive layer design + Definition of Done)
    archive/                completed implementation plans + historical blueprints
        phaseF-plan.md
        phase9-blueprint.md
        phase9.5-blueprint.md
        phaseC-blueprint.md
    ROADMAP-phaseG.md        post-Brain-V1 cleanup/debt/migration roadmap
    audits/                 point-in-time audit + release reports
        AUDIT.md                    (2026-07-22 v2 stabilization audit)
        AUDIT-WEBUI.md              (WebUI audit)
        AUDIT-Brain-V1.md           (Brain V1 architecture/cognitive audit)
        HARDENING-Brain-V1.md       (Brain V1 wiring verification)
        release-brain-v1.md         (Brain V1 final release assessment)
```

## Conventions

- **`architecture/`** holds what is true *now*. Never move these to `archive/`.
- **`archive/`** holds what was true *then*. Each archived file carries an `ARCHIVED:` banner and cross-links to the current description.
- **`audits/`** reports are date-stamped, findings-only, and intentionally never edit code; recommendations may be superseded by later work.
- The **`DEVLOG.md`** is the chronological history; architectural documents explain *why* decisions were made, not *when*.