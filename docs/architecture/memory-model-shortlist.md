# Novi small memory-model test shortlist

**Retired September 13, 2026.** Historical research only. The active direction is
[periodic main-model memory updates](main-model-memory-direction.md). Installation
and untested-status statements below describe the original research date, not
the later local experiments. See the active direction for subsequent findings.

Research date: September 11, 2026. Candidates are not installed or qualified. This list supersedes the two-family shortlist in the implementation plan. Test multiple candidates; ship one selected curator model, not a collection running together.

## Selection rule

Choose the cheapest complete memory-curation cycle that meets evidence fidelity, useful-memory recall and device responsiveness requirements. Model parameters, download bytes, peak RAM and CPU time are different measurements. A sub-1GB artifact is not evidence of sub-1GB runtime use. An instruction model can solve bounded semantic tasks without a dedicated thinking mode; long reasoning output is not a requirement.

Use a proposed lightweight track of at most 1 GB incremental peak memory across the inference process tree on the qualification workload. Also require sufficient available system RAM and the implementation plan's foreground-responsiveness gate. This is a test target, not a measured property of every model below. Record private committed bytes, working set, file-backed weights and system pressure separately so memory mapping does not hide cost. Evaluate cold load plus proposal, verification, repairs and unload, not only decode tokens/second.

## Candidates

The priority column is a research judgment based on deployment suitability and published task focus, not a quality ranking.

| Priority | Exact checkpoint | Size label | Why include it / what to test |
|---|---|---|---|
| First round | [LiquidAI/LFM2.5-230M](https://www.liquid.ai/blog/lfm2-5-230m) | 230M | Lowest-resource contender for extraction and constrained edits. Publisher explicitly discourages reasoning-heavy workloads; test correction/relationship failures carefully. |
| First round | [LiquidAI/LFM2.5-350M](https://huggingface.co/LiquidAI/LFM2.5-350M) | 350M | Publisher recommends extraction, structured outputs and tool use. Particularly close to Novi's bounded edit contract. |
| First round | [openbmb/MiniCPM5-1B](https://huggingface.co/openbmb/MiniCPM5-1B) | Approximately 1.08B total | Hybrid thinking/direct mode and local-assistant focus. Strong candidate for harder semantic decisions. Official GGUF exists; benchmark the quantized release, not just BF16 results. |
| First round | [LiquidAI/LFM2.5-1.2B-Instruct](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct) | 1.17B total | Publisher recommends extraction and RAG and reports a small CPU footprint. A serious full-curator candidate despite having more parameters than Qwen 0.8B. |
| First round | [ibm-granite/granite-4.0-350m](https://huggingface.co/ibm-granite/granite-4.0-350m) | 350M | Instruction-tuned Nano model for on-device use. Tests whether compact structured tasks are enough for a very small model. |
| First round | [Qwen/Qwen3.5-0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B) | 0.8B | Retain as a current small-Qwen control; test direct mode and bounded thinking separately. |
| Second round | [ibm-granite/granite-4.0-1b](https://huggingface.co/ibm-granite/granite-4.0-1b) | 1B | Larger sibling to test semantic quality gains against CPU/RAM cost. |
| Second round | [LiquidAI/LFM2.5-1.2B-Thinking](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Thinking) | 1.2B | Reasoning-specific comparison to Instruct. Count additional generation and repairs; keep only if quality gains justify cost. |
| Baseline | [Qwen/Qwen2.5-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct) | 0.5B | Older small instruction baseline. Test multilingual attribution and concise structured extraction. No verified universal best-under-1GB title. |
| Baseline | [HuggingFaceTB/SmolLM2-360M-Instruct](https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct) | 360M | Small instruction/rewriting/summarization baseline; test semantic limitations as well as valid JSON. |
| Baseline | [meta-llama/Llama-3.2-1B-Instruct](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct) | 1B | Established instruction-model comparison; measure the selected quantization and check distribution terms before bundling. |
| Baseline | [google/gemma-3-270m-it](https://huggingface.co/google/gemma-3-270m-it) | 270M | Extreme-small instruction baseline; no assumption that it can perform the complete curator/verifier task. |

Keep Gemma 4 E2B as a deployment-specific comparison from the existing plan. Its mobile text-only footprint uses a different runtime path; do not transfer that footprint to desktop GGUF. Keep SmolLM2-1.7B-Instruct as an optional upper-size control only. Its exact Q4 artifact and runtime determine whether it exceeds the budget, not a blanket 1.5GB claim. [Gemma deployment guidance](https://ai.google.dev/gemma/docs/core), [SmolLM2 model](https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct).

## What the cited numbers establish

- Artificial Analysis published a 17.9 score for MiniCPM5-1B in **non-reasoning mode on May 26, 2026**, compared with Qwen3.5-2B reasoning at 16.3. This supports including it. It does not establish a current ranking under a newer index, quantized accuracy, sub-1GB runtime memory or memory-editing quality. Do not mix index versions. The exact quoted LiveCodeBench comparison was not verified in this research and is not used for selection. [Original evaluator report](https://artificialanalysis.ai/articles/minicpm5-1b-the-leading-1b-open-weights-model).
- Liquid reports LFM2.5-230M at **293 MB and 42 decode tokens/s on Raspberry Pi 5**, and **375 MB and 213 decode tokens/s on Galaxy S25 Ultra's Snapdragon Gen4 CPU**, using a 4-bit model with 2K input context. These are publisher measurements on specified devices; neither proves identical performance on an older Windows laptop. [Measurement conditions](https://www.liquid.ai/blog/lfm2-5-230m).
- Liquid reports LFM2.5-1.2B-Instruct at **719 MB and 70 decode tokens/s** on the Galaxy S25 Ultra CPU with llama.cpp Q4_0, 1K prefill and 100 decode tokens. This is more relevant evidence than a theoretical parameter-to-byte estimate, but remains a device-specific publisher result. [Model and measurement table](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct).
- MiniCPM5's official GGUF distribution supports Q4_K_M. Exact artifact bytes have not been verified here; do not repeat approximately 0.5GB as a measured download or working-set figure. [Official GGUF](https://huggingface.co/openbmb/MiniCPM5-1B-GGUF).

## Test sequence

1. Screen all first-round candidates on the same 30 development episodes with equivalent source content, a bounded context, and identical operation permissions. Start direct-output where supported. Use each model's correct chat template; validate the actual runtime's schema support.
2. Include explicit preference extraction, irrelevant chatter, assistant/user attribution, negation, changed preferences, scoped exceptions, quoted instructions, unsupported causal links, duplicate knowledge and abstention. Grade end-to-end accepted changes, not just proposal quality.
3. Run CPU-only with a conservative thread limit. Measure both cold and warm cycles on a low-end target, with Novi chat active as a contention test. A fast decoding result may still lose on prefill, load time or repair frequency.
4. Eliminate candidates with critical false-memory errors or resource-budget failures. Take at most three finalists to the implementation plan's held-out suite. Tune prompts on development cases only; the final held-out score must be untouched by selection feedback.
5. Compare a tiny model with one-section operations against the same model on compound edits. Do not quietly drop hard cases to inflate quality; report deferred cases and worthwhile-memory recall. A model that extracts facts but cannot verify corrections is not qualified as the sole curator.
6. Run actual GGUF/other chosen quantized artifacts. Record digest, license, backend version, prompt/schema version, context, thread count, generation mode, all token counts, accuracy, peak RAM and foreground latency regression. Start around 4-bit; test higher precision for tiny models if it still fits. Avoid selecting extreme quantization solely to cross a download-size threshold.
7. Deploy one winner only after these checks. If none passes, retain the durable pending queue, narrow the product capability explicitly or investigate task-specific fine-tuning with held-out evaluation. Never silently switch to 4B or a cloud model.

No downloads, local inference, latency measurements or memory-quality tests were performed while creating this research shortlist.
