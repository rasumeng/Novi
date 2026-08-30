# Beta Frontend Polish — Revised Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Novi webui to beta with cohesive, intentional product feel — no redesign, same IA, core agent loop polished first.

**Architecture:** Pure React+Vite+Tailwind edits in `novi/webui/src`. No new deps except guarded motion/focus. Tier 1 core loop → Tier 2 persistence → Tier 3 deferred. Each task is isolated visual pass with `vitest` + `webapp-testing` screenshot verification.

**Tech Stack:** React 18, Tailwind 3.4.4 (base 950 #131418 / accent #7A6EE0), lucide-react, framer-motion, vitest, Playwright

**Spec:** Conversation 2026-08-27 frontend-design brief + revision 11 points + audit at `novi/webui/src`

## Global Constraints
- Dark system locked: base 950/900/800, accent violet, emerald/amber semantics — extend only
- Fonts: Inter + JetBrains Mono foundation — no new display face unless proven
- Responsive ≤375px, visible focus:ring, prefers-reduced-motion required
- Copy: sentence case, plain verbs, active voice, user-side naming
- Protect IA: no new nav layers, no modal sprawl, no decorative animation

---
### Tier Priority
- **Tier 1 (core loop, P0):** LandingPage, Conversation, MessageBubble/ThinkingTrace, PromptInput, Sidebar
- **Tier 2 (persistence, P0/P1):** Knowledge (P0), Activity, Jobs, Timeline, Projects (P1 — minimal if already good)
- **Tier 3 (deferred, P2):** Settings/Models/General — skip unless genuine beta blocker

### Workbench Vocabulary (restrained)
Signal lines, routing indicator, connection states, subtle instrumentation only where they reinforce routing/memory/tools/connections/activity/local state. Never hardware-module skin. One bold place per surface, rest quiet.

### Agent Lifecycle Mapping
Understand (PromptInput) → Route (ModelBadge/vision/DeepResearch micro-label) → Remember (Knowledge preview / sharedContext) → Research (DeepResearch toggle + search Ready) → Use tools/Perform (ThinkingTrace peek → streaming dots → Activity steps) → Continue background (Jobs + Sidebar live + Landing busy) — shared `accent animate-pulse` language, not internal IDs.

---
### Task 1: Globals quality floor (P0)
**Files:** Modify `novi/webui/src/styles/globals.css`, verify `PromptInput.tsx`, `Sidebar.tsx`, `MessageBubble.tsx`
- [ ] Add `@media (prefers-reduced-motion: reduce)` guard
- [ ] Add `focus-visible:ring` on buttons with hover but missing focus
- [ ] Verify `npm run build` + keyboard tab order

### Task 2: LandingPage — ready to work (P0)
**Files:** Modify `novi/webui/src/components/chat/LandingPage.tsx`
- [ ] Thesis framing above input: `Route • Remember • Act` + routing micro-label, input remains dominant
- [ ] Consolidate 5 sections → 3: Continue (max 3), Working (when busy), What Novi noticed (2 previews)
- [ ] Copy pass invitations, screenshot 375/1280

### Task 3: MessageBubble / ThinkingTrace (P0)
**Files:** Modify `novi/webui/src/components/chat/MessageBubble.tsx`, `ThinkingTrace.tsx`, `Conversation.tsx`
- [ ] Thought peek 1-line muted preview when thinking, button label polish, streaming dots only here
- [ ] Inline code `bg-base-800/60 border` toned down
- [ ] Verify markdown/code/attachment render, reduced-motion

### Task 4: PromptInput tier + distinct states (P0)
**Files:** Modify `novi/webui/src/components/chat/PromptInput.tsx`
- [ ] Tier bar: left secondary, right primary; Mic listening vs recording distinct; Deep Research toggle-chip
- [ ] Attach chips 120→160, enlarge close hit
- [ ] Playwright drag-drop + mic toggle smoke

### Task 5: Sidebar coherence (P1)
**Files:** Modify `novi/webui/src/components/sidebar/Sidebar.tsx`, `SidebarItem.tsx`
- [ ] Live pulse on generatingConversationId, project pill, touch fix opacity-100 md:opacity-0
- [ ] Collapsed sr-only, Settings stays bottom, no nav move

### Task 6: Knowledge P0 + Activity/Jobs/Timeline minimal (P1)
**Files:** Modify `novi/webui/src/components/knowledge/KnowledgeOverview.tsx`, `ActivityPanel.tsx`, `JobsPage.tsx`, `TimelinePage.tsx`
- [ ] Knowledge: remove placeholder comment, human-facing copy, grouped cards, empty guide
- [ ] Activity: unify lifecycle copy, keep 280px drawer with reduced-motion skip
- [ ] Jobs: keep inline, fix Cancel focus; Timeline: sticky day header only

### Chanel Cut (per surface, at commit)
After each surface, remove one accessory: badge/divider/icon/animation/copy. Final feels edited, not decorated.

### Verification
Per task: `tsc && vite build` + `vitest run` + `with_server.py` screenshot. Final: full build + 125 tests + focus/375/1280 suite.
