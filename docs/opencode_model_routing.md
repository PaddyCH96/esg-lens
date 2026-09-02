# OpenCode Free-Model Routing Plan

How to build ESG Lens with OpenCode's free tiers: which model gets which task, and how to
structure the work so cheap models succeed.

> ⚠️ **The free lineup rotates monthly.** Names below were current as of September 2026.
> Run `opencode models` and check <https://opencode.ai/docs/zen/> before starting each phase —
> yesterday's free flagship becomes paid the same week a new one lands free. Also note that
> during free periods, prompts may be used for training: **do not paste anything private.**
> (Nothing in this project is sensitive — it's all public data and open-source code.)

---

## 1. The free options available

### A. OpenCode Zen (`provider: opencode`)
OpenCode's own gateway. No API key of your own, no local download. As of Sept 2026 the free
tier includes **Grok Code Fast 1**, **GLM**, **MiniMax**, and rotating stealth/community models
(*Big Pickle*, *DeepSeek V4 Flash*, *MiMo-V2.5*, *Nemotron 3 Ultra*, *North Mini Code*).

- ✅ Fast, generous, zero setup, tuned for coding.
- ⚠️ Quotas change without notice; models disappear; quality varies enormously between them.

### B. Bring-your-own free API keys
Google AI Studio (Gemini free tier), Groq, Cerebras, OpenRouter's `:free` models — all
configurable in `opencode.json` as standard providers.
- ✅ Independent of Zen's rotation; Gemini's long context is genuinely useful for large files.
- ⚠️ Daily request caps; needs key management.

### C. Local models via Ollama
`qwen2.5-coder:7b`, `deepseek-coder-v2:16b`, `codellama` behind Ollama's OpenAI-compatible endpoint.
- ✅ Unlimited, private, offline.
- ⚠️ Needs ~16 GB RAM; noticeably weaker; and you'll be running FinBERT on the same CPU.

**Recommended mix:** Zen free models as the workhorse → a BYO key (Gemini) for
long-context/reasoning tasks → Ollama only as an offline fallback.

---

## 2. Task → model routing

The rule: **cheap models are excellent at filling in a well-specified shape and bad at deciding
what the shape should be.** These planning docs exist precisely so that most of the build is
shape-filling.

| Work item | Route to | Why |
|---|---|---|
| Package scaffold, `pyproject.toml`, `.gitignore` | 🟢 Free (any) | Zero ambiguity |
| `schema.sql` from `data_model.md` | 🟢 Free (any) | Transcription — the DDL is already written |
| Pydantic models from the schema | 🟢 Free (any) | Mechanical mapping |
| Repository CRUD methods | 🟢 Free (fast) | Boilerplate SQL, heavily repetitive |
| FastAPI routes + response schemas | 🟢 Free (fast) | Contract fully specified in `api_design.md` |
| `config/*.yaml` files from the doc tables | 🟢 Free (any) | Transcription |
| Unit tests for pure scoring functions | 🟢 Free (fast) | Given expected values, easy |
| Collector implementations (GDELT, EDGAR) | 🟡 Free (strong) | Real API quirks; needs care |
| HTTP client: rate limit + retry + cache | 🟡 Free (strong) | Concurrency correctness matters |
| 10-K section-splitting logic | 🟡 Free (strong) + review | Messy real-world HTML |
| NLP pipeline wiring & batching | 🟡 Free (strong) | Performance-sensitive |
| **Scoring engine (`scoring/`)** | 🔴 **Best model you have** | The product *is* this code; subtle numerics |
| **Job state machine / concurrency** | 🔴 **Best model you have** | Race conditions are expensive to debug |
| Debugging anything that fails twice | 🔴 **Escalate** | Cheap models loop on hard bugs |
| Reviewing a free model's diff before commit | 🔴 **Best model** | Cheapest possible use of a strong model |

🟢 ≈ 60% of the work · 🟡 ≈ 30% · 🔴 ≈ 10%

---

## 3. `opencode.json` starting point

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "model": "opencode/grok-code-fast-1",
  "small_model": "opencode/grok-code-fast-1",
  "provider": {
    "google": {
      "options": { "apiKey": "{env:GOOGLE_API_KEY}" }
    },
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://localhost:11434/v1" },
      "models": { "qwen2.5-coder:7b": { "name": "Qwen 2.5 Coder 7B (local)" } }
    }
  },
  "instructions": [
    "docs/handoff_to_backend.md",
    "docs/architecture.md",
    "docs/scoring_methodology.md"
  ]
}
```
Verify field names against the current OpenCode config schema — it moves.

Switch models mid-session with `/models`. Keep the phase you are on in the prompt so the
model knows which doc governs.

---

## 4. The handoff workflow that actually works

### 4.1 One phase, one session
Phases in `handoff_to_backend.md` are sized to fit a cheap model's context and attention.
**Start a fresh OpenCode session per phase.** A 200-message session with a small model produces
worse code than message 3 of a fresh one.

### 4.2 The prompt template
```
Read docs/architecture.md §4 and docs/data_model.md.

Implement Phase 1 (Collectors) from docs/handoff_to_backend.md ONLY.
Do not touch src/esg_lens/scoring/ or src/esg_lens/api/.

Constraints:
- Collectors must never raise; return [] and log.
- All HTTP through collectors/http.py.
- No live network calls in tests; use respx with fixtures in tests/fixtures/.

Definition of done is stated in the handoff doc for this phase. Stop when it is met.
```
Three things make this work: **a bounded scope**, **an explicit do-not-touch list**, and
**a stated definition of done**. Without the third, cheap models keep going and start
refactoring things you did not ask about.

### 4.3 Test-first as the quality gate
For the scoring engine especially: have a *strong* model write the test asserting the worked
example from `scoring_methodology.md` §8, **then** let a free model implement until it passes.
This converts "did the cheap model understand the methodology?" — unanswerable — into
"does the test pass?" — mechanical. Do the same everywhere you can.

### 4.4 Escalation rule
> If a free model fails the same task **twice**, switch models — do not prompt a third time.

A cheap model that has misunderstood something rarely recovers; it accumulates wrong context.
Escalate, or start a fresh session with a narrower prompt.

### 4.5 Review before commit
After each phase, run a review pass with the strongest model available:
```
Review the diff for Phase 1 against docs/architecture.md §5.1 and the traps in
docs/handoff_to_backend.md §4. List violations only. Do not rewrite.
```
"List violations only, do not rewrite" is important — otherwise the reviewer starts rebuilding
and you lose the working code.

---

## 5. Quota management
- **Free tiers are request-capped, not token-capped.** Fewer, larger prompts beat many small ones.
  Ask for a whole module, not a function at a time.
- **Keep the model out of `data/`.** Filings and news dumps will burn context fast; put
  `data/`, `*.db`, `docs/../fixtures/*.json` in the ignore config.
- **Use `instructions` in `opencode.json`** rather than re-pasting docs each session.
- **When you hit a cap mid-phase**, switch to a different free provider rather than waiting —
  that is the whole point of configuring three.
- **Keep Ollama installed** as the offline fallback for a day when everything else is capped.

---

## 6. Phase → suggested model

| Phase | Suggested | Fallback |
|---|---|---|
| 0 Scaffold | Any free Zen model | Ollama |
| 1 Collectors | Strongest free Zen model | Gemini free tier |
| 2 NLP pipeline | Strongest free Zen model | Gemini free tier |
| 3 **Scoring** | **Best available (paid if you have it)** | Strongest free + strict test-first |
| 4 API + jobs | Strong free for routes; **best for the job runner** | — |
| 5 Validation | Any free model | Ollama |
| Review passes | Best available, every phase | — |

If you only ever pay for one thing on this project, make it **Phase 3 and the review passes.**
Everything else is genuinely fine on free tiers, because these documents have already made
the hard decisions.

## Sources
- [Zen | OpenCode docs](https://opencode.ai/docs/zen/)
- [OpenCode Zen Free Models 2026](https://www.maximalstudio.in/blog/opencode-zen-free-models)
- [How to Use OpenCode for Free in 2026](https://towardsai.com/p/machine-learning/how-to-use-opencode-for-free-in-2026)
- [OpenCode Zen free tier — freellm.net](https://freellm.net/providers/opencode)
