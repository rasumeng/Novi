# Novi — Open-Source Local AI Agent Platform

Novi is a privacy-first, fully local AI agent platform designed to autonomously understand requests, select appropriate tools, route tasks to specialized models, and execute multi-step workflows without requiring cloud AI services.

Unlike traditional chat assistants that rely on manual mode selection, Novi uses an intelligent orchestration layer that analyzes every request, determines the required capabilities, and dynamically coordinates models, tools, memory, and retrieval systems.

Built with Python, FastAPI, React, Ollama, and modern LLM engineering practices, Novi explores the future of personal AI assistants through agent orchestration, retrieval grounding, local inference, and extensible tool execution.

```bash
novi webui
```

---

# Overview

Modern AI assistants often depend on external APIs, require users to select specific modes, and provide limited transparency into how responses are generated.

Novi takes a different approach:

* **One intelligent assistant** — every interaction is treated as a task, with the system deciding how to respond.
* **Local-first architecture** — AI inference, memory, and data processing can run entirely on user hardware.
* **Model-agnostic design** — supports multiple providers and role-based model routing.
* **Transparent execution** — exposes planning, retrieval, tool execution, and reasoning traces.
* **Extensible capabilities** — supports custom tools, MCP servers, and specialized workflows.

---

# Engineering Highlights

## AI Agent Orchestration

Built an intent-driven agent architecture that:

* Classifies user requests
* Determines required capabilities
* Creates execution plans
* Selects appropriate tools
* Routes tasks to specialized models
* Executes multi-step workflows

## LLM Infrastructure

Implemented a provider-agnostic AI layer supporting:

* Local Ollama inference
* Cloud model providers
* Role-based model selection
* Capability-aware model routing
* Resource-aware model management

## Retrieval & Grounding

Developed a reliability-focused retrieval pipeline including:

* Evidence detection
* Retrieval policy decisions
* Source ranking
* Context injection
* Retrieval quality evaluation

Designed to reduce hallucinations by determining when external information is required before generation.

## Memory Systems

Built persistent AI memory using:

* LanceDB vector storage
* Hybrid semantic search
* Importance scoring
* Conversation summarization
* Knowledge indexing

## Full-Stack AI Application

Developed a complete AI application stack:

* React + TypeScript frontend
* FastAPI backend
* WebSocket streaming
* Real-time execution traces
* Permission management
* Interactive agent controls

---

# Architecture

```
                         User Input
                              |
                              v
                    Orchestrator Analysis
                              |
        +---------------------+----------------------+
        |                     |                      |
        v                     v                      v
 Intent Detection     Evidence Detection     Complexity Analysis
        |                     |                      |
        +---------------------+----------------------+
                              |
                              v
                    Grounding & Retrieval Policy
                              |
                              v
                       Execution Plan
                              |
                              v
                       Novi Runtime
                              |
        +---------------------+----------------------+
        |                     |                      |
        v                     v                      v
 Retrieval System       Tool Execution        Agent Loop
        |                     |                      |
        v                     v                      v
 Evidence Bundle       Tool Results          Final Response
```

---

# Core Components

## Runtime (`novi/runtime/`)

The execution engine responsible for coordinating AI workflows.

Features:

* Unified execution pipeline
* Retrieval coordination
* Tool execution
* Recovery handling
* Event streaming
* Execution tracing
* Permission enforcement

Key components:

* `NoviRuntime`
* `RetrievalCoordinator`
* `EvidenceCollector`
* `ExecutionTrace`
* `EventBus`
* `PermissionResolver`

---

## Orchestrator (`novi/orchestrator/`)

The decision-making layer responsible for understanding tasks.

Components:

### Intent Detection

Classifies requests into:

* Conversation
* Research
* Coding
* Planning
* Vision

### Evidence Detection

Determines whether a task requires external information using signals such as:

* Time sensitivity
* Dynamic information
* Comparisons
* External facts
* Project context

### Complexity Estimation

Evaluates task difficulty to optimize:

* Model selection
* Resource usage
* Execution strategy

---

## Memory (`novi/memory/`)

Persistent memory architecture powered by LanceDB.

Capabilities:

* Vector-based retrieval
* Hybrid search
* Short-term conversation buffers
* Long-term semantic memory
* Knowledge base indexing
* Automated summarization

---

## Providers (`novi/providers/`)

Provider abstraction layer supporting:

* Ollama
* OpenAI-compatible providers

Features:

* Model role assignment
* Provider switching
* Configuration-based routing
* Local inference support

---

## Tools & Capabilities

Novi includes 20+ built-in tools:

| Category      | Examples                                       |
| ------------- | ---------------------------------------------- |
| Files         | Read, write, edit, search                      |
| Code          | Execute Python, Git operations, terminal tools |
| Web           | Search, fetch, retrieval pipelines             |
| Knowledge     | Create, update, query knowledge bases          |
| Desktop       | Screenshots, clipboard, image analysis         |
| Agents        | Subagent spawning                              |
| Scheduling    | Background task execution                      |
| Communication | Telegram integration                           |

---

# Features

## Implemented

### AI Core

* Intelligent task routing
* Multi-model support
* Local LLM inference
* AI agent execution loops
* Tool calling
* Planning workflows

### Retrieval

* Evidence detection
* Retrieval policies
* Source ranking
* Context injection
* Knowledge indexing

### Memory

* LanceDB vector database
* Hybrid retrieval
* Importance scoring
* Conversation summarization

### Developer Experience

* CLI interface
* WebUI
* Streaming responses
* Debug traces
* Model configuration
* Permission controls

### Integrations

* MCP server support
* Telegram bot
* File/image processing
* Git tooling

---

# Web Interface

Novi includes a React-based WebUI providing:

* Streaming conversations
* Agent execution traces
* Permission controls
* Project organization
* Model configuration
* File/image attachments
* Code workflows

Launch:

```bash
novi webui
```

---

# Quick Start

## Requirements

* Python >= 3.10
* Ollama installed
* Local AI models available

## Installation

```bash
git clone https://github.com/rasumeng/novi.git

cd novi

pip install -e .

novi init

novi webui
```

---

# Configuration

Configuration is managed through:

```
~/.novi/config.toml
```

Example:

```toml
[llm]
default_model = "qwen3:8b"

[providers]
default = "ollama"

[providers.ollama]
url = "http://localhost:11434"
```

---

# Testing

Novi includes automated tests covering:

* Agent orchestration
* Retrieval pipelines
* Evidence detection
* Configuration handling
* Runtime execution
* Tool systems

Current status:

```
200+ tests passing
```

---

# Roadmap

## In Progress

* Advanced evidence processing
* Long-context optimization
* Expanded test coverage

## Planned

* Plugin ecosystem
* Background autonomous agents
* Codebase-aware memory
* Multimodal generation
* Additional model providers

---

# Technology Stack

| Area           | Technologies        |
| -------------- | ------------------- |
| Language       | Python, TypeScript  |
| Backend        | FastAPI             |
| Frontend       | React, Tailwind CSS |
| AI Runtime     | Ollama, LLM APIs    |
| Memory         | LanceDB             |
| Communication  | WebSockets          |
| Infrastructure | Docker, Linux       |
| Development    | Git, GitHub Actions |

---

# License

MIT License
