# Novi Beta — Scope and Boundaries

Written before the first public beta so the limits are deliberate and visible
rather than discovered by a user.

This document is the "what does Novi actually do right now" contract. If the
code and this document disagree, that's a bug in one of them — please say which.

---

## 1. What the beta does

Novi is a desktop assistant that runs a local model through a tool-using agent
loop, with a persistent conversation history.

| Capability | Status | Notes |
|---|---|---|
| Local chat (Ollama) | Working | Any model Ollama can serve |
| Streaming responses + reasoning display | Working | |
| Conversation persistence | Working | Conversations survive restart |
| Run timeline | Working | Backed by `runs.sqlite` |
| Tool calling | Working | Gated by the permission system |
| File attachments, including pasted images | Working | Requires a vision-capable model |
| Projects, skills, workspaces | Working | |
| Web search | Working | Opt-in; unconfigured by default |

## 2. What the beta deliberately does not do

These are **not** bugs. They are out of scope for beta, gated off by default,
and reversible with a single config flag.

### Long-term memory — off

```toml
[memory]
enabled = false
```

- No recall across conversations
- `search_knowledge` and `search_memory` are **withheld from the model entirely**
  (not exposed-and-refusing, which would just waste turns)
- Memories are not automatically injected into prompts

### The Brain — off

```toml
[brain]
enabled = false
```

The Brain owns the vector store. Constructing it embeds the whole corpus during
startup, which is why it is gated: on a modest corpus that was ~3 minutes of
embedding calls before the first chat connection could complete.

- No knowledge items, scenarios, relationships, or vault
- No curation worker, no reasoning tiers
- **No embeddings at startup at all**

### Why

The memory and Brain subsystems were built with substantial AI assistance and
have not been verified to a standard we trust yet. Shipping them dormant means
the beta cannot corrupt a user's data with machinery we don't yet understand,
and it makes startup fast enough to be pleasant.

Both subsystems are **fully present in the repository**, not deleted. They are
the reference material for the planned backend rework.

## 3. Configuration flags that change beta behaviour

| Flag | Default | Effect when off |
|---|---|---|
| `memory.enabled` | `false` | Withholds memory/knowledge tools; stops auto-injection; skips boot-time knowledge re-index |
| `brain.enabled` | `false` | Brain is never constructed; no vector store; no startup embeddings |

Turning either **on** re-enables that subsystem. There is no migration: the
Brain rebuilds its store from the markdown knowledge base on first use.

## 4. Data boundaries

This is a hard rule, enforced by `.gitignore` and by the path layout.

**The repository ships Novi's base state only** — source, docs, and test
fixtures. No user data, ever.

All mutable state lives in the user profile directory (`~/.novi`, or
`Novi/.novi` on Windows), which lives *outside* the install directory:

```
~/.novi/
  config.toml        user configuration
  chats/             conversations (markdown, human-readable)
  attachments/       uploaded and pasted files
  brain/runs.sqlite  run + event history (timeline)
  memory/            long-term memory        [dormant in beta]
  brain/             knowledge, vectors      [dormant in beta]
  skills/  projects/  knowledge/  timeline/  jobs/
```

The only files that sit next to the code are **read-only assets that are meant
to ship with the download**: the built frontend (`novi/webui/dist`) and the
default skills (`novi/default_skills`).

`.gitignore` blocks `*.sqlite`, `*.lance/`, `*.parquet`, `*.db`, and friends.
If any of those ever appear in the working tree, a test booted the app against
the repository instead of a temporary profile — that is the bug to look for.

## 5. Known limitations

- **Vision depends on your model.** Novi sends images to whatever model is
  configured. Some models advertise vision support and cannot actually read
  images; Novi cannot detect this. If a model claims it cannot see an image,
  switch models — `qwen3.5:2b` is known to read images correctly.
- **`llm.max_tokens` is a generation cap, not a target.** A very high value
  lets a small model ramble for a long time.
- **Single machine, single user.** No sync, no accounts, no multi-device.
- **Ollama is required** for local inference. There is no bundled model.
