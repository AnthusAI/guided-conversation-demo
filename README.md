# Guided conversation demo

Demonstrates a multi-turn intake chat where **procedure state** drives `{state.still_needed}` / `{state.collected_summary}` in the agent system prompt, plus **CLI HITL** via `Human.input` between assistant turns.

The agent uses **`tool_choice = required`** with **`record_field`**, **`chat_only`**, and **`done`** so every turn must commit to either saving data, pure conversation, or finishing—avoiding “helpful” replies that never call tools and never update form state.

Intake is **not** a fixed script: the model can **infer** things like the **purpose of the session** from tangents and record fields **out of order** (for example, goal before name), then **follow up** until every required slot is filled.

The procedure **does not exit on `done` alone** if required fields are still missing (the model sometimes calls `done` too early). It **nudges** the model in that case, **nudges** again when all fields are saved but `done` was not called, and finally calls the **`done` tool from orchestration** if the model still omits it, so the workflow can finish reliably.

## Example session (what you see in the CLI)

Run from the repo root (with `OPENAI_API_KEY` set):

```bash
tactus guided_form.tac
```

(`tactus run guided_form.tac` is equivalent.)

Each round prints **`[Form state]`** first. That block is the same information the agent receives in its system prompt: what is **still to collect** and what is **already collected**. Then **`[User]`** shows the line you typed (long lines may be summarized for display; the model still receives the full text). Then **`[Assistant]`** shows the user-visible message from the last tool call (`reply` / `note_to_user`). A horizontal rule separates rounds.

Below is an **abbreviated** transcript from a real run: the user jokes about rabbits (“George”) before giving a full name or email. The agent **does not insist on a fixed questionnaire order**—it can **infer and record** a plausible “goal” from those diversions, then **follow up** on whatever is still missing (here, name and email). When the user clarifies what they meant by “goal,” the agent **updates** the stored goal and continues. Required items can arrive **out of order**; the checklist in `[Form state]` always shows what remains.

```
[Form state]
  Still to collect: Full name (given + family name); Email address; Primary goal for this session
  Already collected: (nothing yet)

[User]
Hello — I'd like to complete the intake form. Please walk me through it.

[Assistant]
Absolutely — I’ll help you step by step. First, please share your full name, including both your given and family name.

────────────────────────────────────────────────

[Form state]
  Still to collect: Full name (given + family name); Email address; Primary goal for this session
  Already collected: (nothing yet)

[User]
Hi George!  Do you like rabbits, George?  I love them rabbits.

[Assistant]
Rabbits sound like a fun topic. I’ve noted that as your goal for this session. Next, please send your full name — your first and last name.

────────────────────────────────────────────────

[Form state]
  Still to collect: Full name (given + family name); Email address
  Already collected: Primary goal for this session: likes rabbits and is interested in them

[User]
My goal?  I'm talking with you, George.

[Assistant]
Got it — I’ve updated your goal to chatting with me. Next I still need your full name, with both first and last name.

… (name and email collected across several turns; full name must include at least two words) …

[Assistant]
Perfect — I’ve recorded your email address. That completes the form, and you’re all set.

[Assistant · completion nudge]
(Assistant produced no user-visible text; check tool args — reply/note_to_user should be set.)
```

What that illustrates:

- **Procedure-owned state**: The Lua procedure updates `state.form_name`, `state.form_email`, and `state.form_goal`, then `refresh_summaries()`. The checklist in **`[Form state]`** stays consistent with the placeholders `{state.still_needed}` and `{state.collected_summary}` in [`guided_form.tac`](guided_form.tac).
- **Out-of-order intake**: The form definition lists name → email → goal, but the user may supply material in any order. The agent can **capture whatever fits** (for example, the **purpose** of the session from an early tangent) **before** the user sends canonical fields like full name or email, then **pursue what is still empty** on later turns.
- **Inference and follow-up**: Tangents and jokes are not dead ends—the model can **interpret** them as partial answers, **record** them when appropriate, and **follow up** with varied, concrete prompts (e.g. insisting on two-word legal names, asking again for email when the user is vague) until each slot is filled or clarified.
- **Tools on every turn**: Off-topic or playful messages still get a short answer, then the model steers back using **`chat_only`** or records structured data with **`record_field`**. The “goal” field is allowed to be informal and can be **updated** when the user clarifies (rabbits → talking with the assistant).
- **Validation**: Full name must be at least two words; the model keeps asking until `record_field` for `name` succeeds.
- **Completion**: After the last field is stored, the model may say the form is done but **omit the `done` tool**. The procedure then injects a **completion nudge** (the `[Assistant · completion nudge]` line). You may see the placeholder reminder if that turn only performed `done` without user-facing text in the tool args; orchestration still records **`done`** when needed.
- **Result**: The procedure returns structured output, for example `completed: true`, `name`, `email`, `goal`, and `turns`. In this session, `goal` ended up as something like **“talking with the assistant”** after the user’s clarification.

See [`guided_form.tac`](guided_form.tac) for the full control flow (including premature-`done` handling and the completion nudge).

## Setup

Install Tactus from the sibling checkout (or PyPI):

```bash
pip install -e ../Tactus
pip install -e .
```

Configure API access (required for a real LLM run):

- **Local `.env` (gitignored):** copy from `../Plexus/.env` or set `OPENAI_API_KEY` yourself, then load before running:
  ```bash
  set -a && source .env && set +a
  tactus run guided_form.tac
  ```
- Or export `OPENAI_API_KEY` in your shell, or add `openai_api_key` in `.tactus/config.yml` (see [Tactus configuration](https://github.com/AnthusAI/Tactus/blob/main/docs/CONFIGURATION.md)).

Recent Tactus CLI builds can auto-load a `.env` in the working directory; otherwise `source .env` or use `direnv`.

The agent defaults to **OpenAI `gpt-5.4-mini`** (see [`guided_form.tac`](guided_form.tac) and [`guided_form.tac.yml`](guided_form.tac.yml)). Override with `default_model` / `default_provider` in config or the agent block if your account uses a different model.

## Sandbox

By default, Tactus uses **Docker** for sandboxed runs when configured to do so. The [`guided_form.tac.yml`](guided_form.tac.yml) sidecar does **not** disable the sandbox; it only sets defaults like `default_model`. To run **without** Docker for this checkout, uncomment the `sandbox.enabled: false` block in that sidecar, set sandbox off in your global Tactus config, or pass:

```bash
tactus run guided_form.tac --no-sandbox
```

## Run (interactive CLI)

```bash
cd /path/to/guided-conversation-demo
tactus guided_form.tac
```

Reply at each `›:` prompt. The assistant asks for **name**, **email**, and **goal**, using tools to record values. The **[Example session](#example-session-what-you-see-in-the-cli)** section above walks through a real transcript.

## Run with canned user lines (real API, no typing)

Useful for smoke-testing the full stack with a real key:

```bash
tactus run guided_form.tac \
  --param skip_hitl=true \
  --param mock_user_replies='["Alex Doe","alex@example.com","Ship the guided demo"]'
```

(Adjust the list to match how many back-and-forth turns your model takes.)

### Real model vs mocked model (important)

| What you want | User input | LLM / agent |
|---------------|------------|-------------|
| **Interactive CLI** | You type at `›:` | Real API |
| **Integration / smoke** | **Scripted** (`skip_hitl` + `mock_user_replies`) | Real API |
| **BDD (`tactus test --mock`)** | N/A (procedure expectations only) | **Mocked** (no API) |

**“Mock user” here means automation, not a fake person.** Setting `skip_hitl=true` and passing `mock_user_replies` tells the procedure to ** dequeue fixed strings** instead of blocking on `Human.input`. The **guided agent still calls the real model** and real tools; only the human side is scripted so tests and CI can finish without a person at the keyboard. That is the same mechanism as `MockHITLHandler` in [`tests/test_guided_form_harness.py`](tests/test_guided_form_harness.py).

By contrast, `tactus test guided_form.tac --mock` replaces the **LLM** with the scenario’s mocked tool calls (fast, deterministic, no key).

## BDD specification

At the bottom of [`guided_form.tac`](guided_form.tac), a **`Specification([[ ... ]])`** block holds **Gherkin-style** text (`Feature`, `Scenario`, `Given` / `When` / `Then`). Tactus turns that into **Behave** tests when you run:

```bash
tactus test guided_form.tac --mock
```

Those scenarios describe **expected agent tool usage** (for example, `the agent "guide" calls tool "guide_record_field" with args {...}`) while **`mock_all_agents`** supplies deterministic “assistant” behavior—**no real LLM**. That is separate from pytest integration tests, which **do** call the API.

The Feature narrative documents behavior (state-driven checklist, out-of-order fields, orchestration around `done`); the two mocked scenarios are minimal **smoke** checks that the procedure completes when the mocked agent emits the right tool sequence.

## Programmatic tests (pytest, real API)

Script the same user lines as `skip_hitl` + `mock_user_replies` and assert on `state._assistant_transcript` (and outputs):

```bash
pip install -e ../Tactus
pip install -e ".[dev]"
export OPENAI_API_KEY=...   # required for integration tests
pytest tests/ -m integration
```

Without a key, integration tests are skipped; the import smoke test still runs:

```bash
pytest tests/ -q
```

## Tests (mocked BDD, no API calls)

Runs the **`Specification`** at the end of [`guided_form.tac`](guided_form.tac) (see **[BDD specification](#bdd-specification)** above):

```bash
tactus test guided_form.tac --mock
```

Validate only:

```bash
tactus validate guided_form.tac
```

## Reliability experiments (static vs dynamic system prompt, quantified)

**What you are comparing (two arms, same everything else):**

| Arm | Behavior |
|-----|----------|
| **Static** | **`BASE_SYSTEM_PROMPT` only** — one large fixed system prompt on the agent; each turn calls `guide({ message = ... })` with **no** per-turn system override. User lines are normal chat text (no state prefix). [`complex_form_static.tac`](complex_form_static.tac) |
| **Dynamic** | **Same `BASE_SYSTEM_PROMPT`** as static on the agent, plus an **ephemeral orchestrator suffix** each turn: `guide({ message = ..., system_prompt_suffix = hint })` (Tactus appends the hint after the rendered base system prompt; it is not stored as chat history like user/assistant turns). Procedure nudges and user lines match static. [`complex_form_dynamic.tac`](complex_form_dynamic.tac) |

The API receives a **single** system string per request (base + appended hint for the dynamic arm), matching common chat providers while preserving the “ephemeral hint, normal user content” design.

Same model, tools, validators, and procedure orchestration. The three **personas** in [`tests/personas.py`](tests/personas.py) are different **simulated users** (stress styles), not extra “methods.”

**Metric:** *strict success* — procedure completes and every ground-truth field matches (see [`tests/test_complex_form_reliability.py`](tests/test_complex_form_reliability.py)). The harness also records **completion** (procedure finished), **infra failures** (execute/API errors, not form mistakes), and **strict success excluding infra** for comparing prompt strategies when the stack misbehaves.

**Interpreting results**

- **Strict success (all runs)** — headline rate including infra failures (a failed execute counts as not strict-ok).
- **Strict success excluding infra** — strict matches divided by runs that completed execute successfully; use this to compare **static vs dynamic prompt strategy** when LiteLLM/async or API ordering errors appear in the logs.
- **Completion rate** — fraction of runs where the procedure reported `completed` (orthogonal to field correctness).
- **Infra failure rate** — fraction of runs where `execute` failed (exceptions, bad responses). High or **asymmetric** infra between arms usually indicates **stack or concurrency bias**, not that one prompt is worse.

For **stable numbers**, keep the default **`RELIABILITY_CONCURRENCY=1`**. Raising concurrency speeds the suite but can trigger asyncio/LiteLLM flakiness.

**Tool / `tool_calls` API errors:** Older Tactus builds could send chat histories where a `tool` message did not match the prior assistant `tool_calls` (duplicate synthetic ids or orphan tool rows). Current Tactus fixes this by **preserving real tool call ids** when the provider supplies them, otherwise issuing **unique ids**, and by **dropping orphan `tool` messages** before each LM call (`tactus/dspy/agent.py`, `tactus/dspy/module.py`). Use an up-to-date Tactus checkout if you still see `Invalid parameter: messages with role 'tool'…`.

Optional: **`RELIABILITY_RETRY_INFRA=1`** re-runs a failed execute once per index (extra API cost).

Optional: **`RELIABILITY_USER_TEMP=0.2`** lowers simulated-user sampling temperature (default `0.7`) for less variance.

Optional: **`RELIABILITY_PAIR_USER_SIM=1`** passes a deterministic OpenAI **`seed`** to the user simulator derived from persona + run index so static and dynamic runs with the same index see the same user-side randomness (when the model supports `seed`).

### 1) Run evaluations (same `RELIABILITY_RUNS` for both arms)

The dynamic procedure uses Tactus **`system_prompt_suffix`** (appends the orchestrator hint to the agent base prompt). Use a Tactus install that includes that API (for example `pip install -e ../Tactus` from a current checkout).

One pytest session runs **both** static and dynamic **×** each persona, so the arms stay comparable:

```bash
pip install -e ".[dev]"
export OPENAI_API_KEY=...
# Same N for every static/dynamic cell (default 20). Default concurrency is 1; raise only if you accept flakiness tradeoffs.
RELIABILITY_RUNS=20 pytest tests/test_complex_form_reliability.py -m reliability -v -s
# Optional: RELIABILITY_CONCURRENCY=8  # faster, less stable
```

Artifacts: `tests/results_static_<persona>.json` and `tests/results_dynamic_<persona>.json`.

### 2) Quantify the difference between the two arms

```bash
python scripts/compare_reliability.py
python scripts/compare_reliability.py --json   # also writes tests/reliability_comparison_summary.json
```

This prints **strict success (all runs)**, **strict success excluding infra** (prompt-strategy headline when infra is present), **completion**, **infra rate**, and **Δ (dynamic − static)** per table. Check stderr for **warnings** when infra is high or asymmetric between arms.

**`tactus test … --mock`** only checks BDD wiring (mocked agent); it does **not** measure real-model reliability.

Wiring-only (no API cost):

```bash
tactus test complex_form_static.tac --mock --param skip_hitl=true
tactus test complex_form_dynamic.tac --mock --param skip_hitl=true
```

### Support flow A/B (disclosures, branching, approval)

Paired procedures model a **support-style** conversation with **control flow**:

- **Issue category** branches: `general`, `billing`, or `technical` (extra fields per branch).
- **Compliance:** `record_compliance` with **`note_to_user`** (full disclosure text the user heard) for **recording/privacy** (before account email) and **fee terms** (billing path only, before billing acknowledgment).
- **Approval:** `plan_approval` must be recorded as `yes` after the agent explains the resolution plan.
- Lua enforces ordering (e.g. no `account_email` until recording disclosure is logged; billing fee disclosure before `billing_charge_acknowledged`).

| File | Role |
|------|------|
| [`support_flow_static.tac`](support_flow_static.tac) | Static `BASE_SYSTEM_PROMPT` only |
| [`support_flow_dynamic.tac`](support_flow_dynamic.tac) | Same base + ephemeral **`system_prompt_suffix`** (**Next suggested action**, compliance flags, still-to-collect) |

The **programmatic** arm (`support_flow_programmatic.tac`) additionally enables **Agent-level retry** (configured on the `guide = Agent { ... }` block via `retry = { ... }`). This retries a single agent turn on infra failures and, optionally, validation failures, truncating the agent’s history back to the start of the attempted turn.

Personas and ground truth: [`tests/support_personas.py`](tests/support_personas.py) (`support_rambler` → general, `support_billing` → billing, `support_technical` → technical).

Run reliability (same env vars as complex form; default procedure/test cap is **58** user rounds — override with `SUPPORT_RELIABILITY_MAX_TURNS` if needed). **`test_support_flow_reliability` defaults `RELIABILITY_CONCURRENCY` to 20** (all runs in a cell in parallel); set `RELIABILITY_CONCURRENCY=1` if you need the slower, more rate-limit-friendly sequential mode. If completion is still noisy, try calibrating on **general + billing** personas first (`support_rambler`, `support_billing`) before leaning on `support_technical`.

Each reliability **run** uses **`asyncio.run` in a worker thread** (`asyncio.to_thread`) so procedure execution does not share pytest’s asyncio loop—this reduces teardown flakiness when `RELIABILITY_CONCURRENCY` is high.

Optional **`SUPPORT_RELIABILITY_AGENT_MODEL`** overrides the guide’s OpenAI model in the `.tac` source at runtime (default matches `support_flow_*.tac`: `gpt-5.4-mini`). Non-default models write artifacts with a suffix, e.g. `gpt-5-nano` → `tests/results_support_static_<persona>_gpt_5_nano.json`.

```bash
RELIABILITY_RUNS=20 pytest tests/test_support_flow_reliability.py -m support_reliability -v -s
SUPPORT_RELIABILITY_AGENT_MODEL=gpt-5-nano RELIABILITY_RUNS=20 pytest tests/test_support_flow_reliability.py -m support_reliability -v -s
```

**Larger runs (e.g. N=100):** Set `RELIABILITY_RUNS=100`. For **fair static vs dynamic comparison**, enable **`RELIABILITY_PAIR_USER_SIM=1`** so the user simulator uses a **deterministic OpenAI `seed`** per persona and run index (same index → same user-side randomness across arms). If you hit **rate limits**, lower `RELIABILITY_CONCURRENCY` (e.g. `12`) or set **`RELIABILITY_RETRY_INFRA=1`** for one retry on failed execute. With **more than 10 runs**, pytest stdout **omits per-run lines and the long `Detail` blob** unless you set **`SUPPORT_RELIABILITY_VERBOSE_DETAIL=1`** (full detail remains in the JSON artifacts).

Example 100-run paired session:

```bash
export RELIABILITY_RUNS=100
export RELIABILITY_PAIR_USER_SIM=1
pytest tests/test_support_flow_reliability.py -m support_reliability -v --tb=short
```

Artifacts: `tests/results_support_static_<persona>.json` and `tests/results_support_dynamic_<persona>.json` (plus optional `_<model_slug>` suffix when using `SUPPORT_RELIABILITY_AGENT_MODEL`).

Compare:

```bash
python scripts/compare_reliability.py --experiment support_flow
python scripts/compare_reliability.py --experiment support_flow --results-suffix _gpt_5_nano
python scripts/compare_reliability.py --experiment support_flow --no-ci   # hide Wilson 95% intervals
python scripts/compare_reliability.py --experiment support_flow --json   # tests/support_reliability_comparison_summary.json
```

### Experiment writeup (LaTeX + GraphViz)

A draft **research-style paper** (support-flow static vs dynamic reliability) lives under [`docs/paper/`](docs/paper/). Build PDF + diagrams:

```bash
python scripts/build_paper.py
```

Watch mode (after `pip install -e ".[docs]"`): `python scripts/build_paper.py --watch`. See [`docs/paper/README.md`](docs/paper/README.md) for prerequisites (`pdflatex`, `dot`).

## License

This project is licensed under the MIT License. See [`LICENSE`](LICENSE).
