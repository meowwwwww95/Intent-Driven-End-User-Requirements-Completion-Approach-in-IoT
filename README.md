# Intent-Driven End-User Requirements Completion Approach in IoT

[中文版](README_zh.md)

A multi-agent research prototype for smart-home scenarios. Given user TAP rules (Trigger-Action-Program, in `IF ... THEN ...` form) and an environment device profile (`env_profile`), LLM-driven agents collaborate to derive a **Bidirectional Requirements Traceability tree (BRT)** with levels `ROOT → INT_TYPE → INT → IRP → DSL`. User intents are refined/abstracted layer by layer into executable system rules (DSL, i.e., TAP rules), together with quality assurance (rule conflict detection, satisfaction checking, etc.).

## Core Components

- `Agents/IntentReader.py` — functional intent inference: TAP → context graph → atomic intents (INT) → high-level intents
- `Agents/QualityPlanner.py` — quality planning based on a quality template tree (`config/quality_template_config.py`), covering non-functional requirements
- `Agents/RealizationPlanner.py` — realization planning: INT → IRP → TAP(DSL) expansion with satisfaction reflection and repair
- `Agents/QualityChecker.py` — quality checking: rule conflict detection (built-in z3-based SMT solver, see `tool/HomeGuard/`) and satisfaction checking, with parallel execution
- `model/` — core data models: the BRT, the trace-tree bus (publish/subscribe), context graph, device profiles
- `tool/` — stage-wise transformation and retrieval tools (named by conversion direction, e.g., `SR2CG`, `AtomINT2IRP`, `IRP2TAP`)
- `evaluator/` — evaluation tools: rule-set metrics (intent coverage, implementation diversity, rule completion rate, rule hallucination rate, rule conflict rate), LLM-based rule similarity, batch evaluation
- `webui/` — built-in web demo page showing the live run, agent statuses, and trace-tree updates
- `dataset/` — evaluation dataset (five difficulty levels L1–L5; each case contains `case_input.json` and `case_ground_truth.json`)

Runtime architecture: single process, multi-threaded. `main.py` runs IntentReader and QualityPlanner in parallel; agents also use thread pools for parallel LLM / conflict-detection calls. Global state (trace-tree bus, device profiles) lives in module-level singletons, so **one process runs only one case at a time**; batch runs are isolated via subprocesses (see `batch_run.py`).

## Requirements & Installation

- Python 3.10+
- Install dependencies:

```bash
pip install -r requirements.txt
```

Main dependencies: `openai` (LLM calls), `networkx` (context graph), `z3-solver` (rule conflict detection), `fastapi` + `uvicorn` (web demo), `openpyxl` (evaluation export).

## Model Configuration

The project connects to base models through OpenAI-compatible chat APIs. A `deepseek` preset is built in; any other compatible endpoint can be configured via generic environment variables.

Copy the environment template and fill it in:

```bash
cp .env.example .env
```

Example for DeepSeek:

```env
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=your_deepseek_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_CHAT_MODEL=deepseek-chat
DEEPSEEK_REASONING_MODEL=deepseek-reasoner
```

For other OpenAI-compatible endpoints, use the generic overrides (they take precedence over provider-specific variables):

```env
AI_PROVIDER=deepseek
AI_API_KEY=your_api_key_here
AI_BASE_URL=https://your-compatible-endpoint/v1
AI_CHAT_MODEL=your-chat-model
AI_REASONING_MODEL=your-reasoning-model
```

Notes:

- `AI_PROVIDER` selects a built-in provider preset
- `AI_API_KEY`, `AI_BASE_URL`, `AI_CHAT_MODEL`, `AI_REASONING_MODEL` take precedence over provider-specific variables
- For third-party compatible endpoints, `AI_SUPPORTS_STREAM_OPTIONS` and `AI_JSON_RESPONSE_FORMAT_MODE` override the behavior of `stream_options` and `response_format`

## Input Format & Dataset

Each case is a JSON file with `user_input` (a list of TAP rules) and `env_profile` (the environment device profile):

```json
{
  "user_input": ["IF ... THEN ...", "..."],
  "env_profile": [
    {
      "name": "device name",
      "type": "device type",
      "capabilities": [
        {"name": "capability name", "description": "capability description", "capabilityType": "trigger or action"}
      ]
    }
  ]
}
```

The evaluation dataset lives in `dataset/`, organized into five difficulty levels L1–L5. Each case directory contains:

- `case_input.json` — model input (`user_input` + `env_profile`)
- `case_ground_truth.json` — reference answers for evaluation (standard rules and intents)

## Usage

### Run a Single Case

```bash
python main.py --debug \
  --file dataset/L1/case6/case_input.json \
  --out output/L1/case6/our_tool/case6_res.json
```

Arguments:

- `--file`: path to the input case JSON, containing `user_input` and `env_profile`
- `--out`: path of the output trace-tree JSON; a corresponding `*_dsl.json` is also generated
- `--debug`: enable debug logging
- `--case-id` / `--difficulty` / `--experiment-run-id`: optional identifiers for overhead statistics (`overhead.jsonl`); inferred from paths by default

Abstract device knowledge is automatically summarized from `env_profile`. Logs are written to the directory of `--out`:

```text
output/L1/case6/our_tool/
  case6_res.json      # trace tree
  case6_res_dsl.json  # generated DSL rules
  run.log             # summary log
  debug.log           # detailed debug log
  overhead.jsonl      # overhead records (LLM call / agent run / case run levels)
```

### Batch Run

Configure the case list in `RUN_SPEC` at the top of `batch_run.py`:

```bash
python batch_run.py --input_root dataset --workers 4 --debug   # run with the given parallelism
python batch_run.py --input_root dataset --dry_run             # only list the cases to run
```

Each case runs in an isolated subprocess with logs in its own output directory; afterwards, the per-case `overhead.jsonl` files are merged into `output/overhead_merged.jsonl`.

### Web Demo

```bash
python -m uvicorn webui.app:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser to:

- automatically scan for `case_input.json` files in the project and start a run for a selected case
- watch live statuses of `QualityPlanner`, `IntentReader`, `RealizationPlanner`, and `QualityChecker`
- view trace-tree snapshots, new nodes, the key event stream, and final statistics in real time

Current limitation: live observation of a single case only; no batch-task view.

### Evaluation

Batch-evaluate the output directory of one difficulty level (requires ground truth):

```bash
python evaluator/batch_evaluator.py ./output/L5 --cases "6" --workers 8
```

## License

This project is released under the [MIT License](LICENSE).
