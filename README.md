# HackerRank Orchestrate

This repository contains a multimodal claim-verification pipeline for the HackerRank Orchestrate challenge. The system reads claim conversations, local image evidence, user claim history, and evidence-review rules, then produces structured claim decisions for `car`, `laptop`, and `package` claims.

The implementation is Python-based and split into:

- `code/` for the main prediction pipeline
- `code/evaluation/` for sample-dataset evaluation
- `dataset/` for the provided inputs and local images

## Approach

The solution uses a model-plus-validation architecture rather than trusting raw model output directly.

At a high level:

1. Read a claim row from CSV.
2. Load the referenced local images.
3. Send the claim conversation plus images to an OpenRouter-hosted multimodal model.
4. Force the model to return a strict JSON object matching the challenge schema.
5. Parse and validate that JSON in Python.
6. Retry the model call when JSON is malformed or contains values outside the allowed lists.
7. Compute a user-history risk score from `dataset/user_history.csv`.
8. Merge history-based flags into `risk_flags` without overriding the visual decision.
9. Re-validate the merged output.
10. Append the final row to CSV.

This gives the model responsibility for visual reasoning and claim interpretation, while Python enforces:

- schema correctness
- allowed values
- retry behavior
- user-history policy
- final CSV structure

## Current Architecture

Core files:

- [code/main.py](/abs/path/D:/projects/hackerrank-orchestrate-june26/code/main.py)
  Main prediction pipeline for `dataset/claims.csv`.
- [code/llm_parser.py](/abs/path/D:/projects/hackerrank-orchestrate-june26/code/llm_parser.py)
  OpenRouter client, image encoding, multimodal request construction, retry-context retention.
- [code/user_history_risk.py](/abs/path/D:/projects/hackerrank-orchestrate-june26/code/user_history_risk.py)
  User-history scoring and post-merge risk flag augmentation.
- [code/system_prompt.md](/abs/path/D:/projects/hackerrank-orchestrate-june26/code/system_prompt.md)
  Model instructions, evidence sufficiency rules, allowed output normalization.
- [code/evaluation/main.py](/abs/path/D:/projects/hackerrank-orchestrate-june26/code/evaluation/main.py)
  Sample-dataset runner that reuses the same core pipeline.

For a full module-by-module explanation, see [architecture_detail.md](/abs/path/D:/projects/hackerrank-orchestrate-june26/architecture_detail.md).

## Setup

## Requirements

- Python 3.11 or newer
- an OpenRouter API key
- internet access for model calls

## Environment setup

Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install pandas openai
```

macOS / Linux:

```bash
python3 -m venv venv
source venv/bin/activate
pip install pandas openai
```

Set the API key:

Windows PowerShell:

```powershell
$env:OPENROUTER_API_KEY="your_api_key_here"
```

macOS / Linux:

```bash
export OPENROUTER_API_KEY="your_api_key_here"
```

## Repository layout

```text
.
├── AGENTS.md
├── README.md
├── architecture_detail.md
├── problem_statement.md
├── output.csv
├── code/
│   ├── main.py
│   ├── llm_parser.py
│   ├── user_history_risk.py
│   ├── system_prompt.md
│   └── evaluation/
│       └── main.py
└── dataset/
    ├── claims.csv
    ├── sample_claims.csv
    ├── user_history.csv
    ├── evidence_requirements.csv
    └── images/
```

## How To Run

## Final prediction run

This runs the main pipeline on `dataset/claims.csv` and writes results to the repo-root `output.csv`.

```powershell
python code\main.py
```

## Evaluation run

This runs the same pipeline on `dataset/sample_claims.csv` and writes results to `code/evaluation/output.csv`.

```powershell
python code\evaluation\main.py
```

## Output schema

The pipeline writes the required fields in this order:

- `user_id`
- `image_paths`
- `user_claim`
- `claim_object`
- `evidence_standard_met`
- `evidence_standard_met_reason`
- `risk_flags`
- `issue_type`
- `object_part`
- `claim_status`
- `claim_status_justification`
- `supporting_image_ids`
- `valid_image`
- `severity`

The allowed values are enforced in code based on `problem_statement.md`.

## Detailed Flow

## 1. Data ingestion

`code/main.py` reads the chosen dataset CSV into a pandas DataFrame. For each row it extracts:

- `user_id`
- `image_paths`
- `user_claim`
- `claim_object`

The `image_paths` string is split on `;`, and each path is resolved under `dataset/`.

## 2. Prompting the model

The prompt is loaded from `code/system_prompt.md`.

The model receives:

- the system prompt
- all claim images as base64 `data:` URLs
- the full claim conversation text

The current configured model path is:

- base URL: `https://openrouter.ai/api/v1`
- model: `google/gemma-4-31b-it:free`

## 3. Retry-aware model interaction

OpenRouter is stateless, so retries must resend prior context manually.

`LLMParser` handles this by:

- starting each claim with a fresh in-memory message buffer
- keeping the original multimodal request in memory
- appending the last assistant output and retry instruction when validation fails
- clearing all context before the next claim

This prevents cross-claim contamination while allowing corrective retries inside one claim.

## 4. Output parsing and validation

The model is required to produce JSON only. The code then validates:

- required keys
- no unexpected extra keys
- boolean types for `evidence_standard_met` and `valid_image`
- non-empty justification fields
- allowed values for:
  - `claim_status`
  - `issue_type`
  - `severity`
  - `object_part`
  - `risk_flags`
- correct formatting for `supporting_image_ids`

If JSON parsing fails or any field is invalid:

- the failure reason is converted into a retry instruction
- the model is called again
- the loop repeats up to `MAX_MODEL_RETRIES = 3`

If the model still fails after the retry limit, the program raises an error rather than writing invalid output.

## 5. User-history risk scoring

The user-history layer reads `dataset/user_history.csv` and computes:

- a smoothed accept rate
- a smoothed reject rate
- a smoothed manual-review rate
- recent-claim activity penalties
- explicit history flag penalties

Current risk formula:

```python
risk_score -= smoothed_accept_rate * 25
risk_score += smoothed_reject_rate * 45
risk_score += smoothed_manual_rate * 25
```

Additional score increases:

- `+35` for `last_90_days_claim_count >= 5`
- `+20` for `last_90_days_claim_count >= 3`
- `+40` if history flags contain `user_history_risk`
- `+25` if history flags contain `manual_review_required`

Risk level mapping:

- `< 15` -> `low`
- `< 45` -> `medium`
- `>= 45` -> `high`

## 6. Merging visual output with history risk

The model is not allowed to emit `user_history_risk` itself.

That flag is appended in Python after the visual output is validated:

- `medium` history risk -> append `user_history_risk`
- `high` history risk -> append `user_history_risk` and `manual_review_required`

This merge only affects `risk_flags`. It does not modify:

- `claim_status`
- `issue_type`
- `object_part`
- `severity`
- evidence sufficiency decisions

That rule preserves visual evidence as the primary source of truth.

## 7. Final output writing

After history merge, the combined output is validated again. Only then is it written to CSV.

The final row includes:

- the original input fields
- the validated and merged structured result

Rows are appended in the required column order.

## Why This Approach

This design was chosen to solve the two main failure modes of multimodal structured output systems:

1. The model can reason well but produce malformed or off-schema JSON.
2. User history is important but should not be allowed to override clear image evidence.

The current architecture addresses both:

- Python enforces the output contract
- the model is retried with explicit correction instructions
- user history is merged as policy-controlled metadata, not as direct override logic

## Evaluation

The evaluation runner is:

- [code/evaluation/main.py](/abs/path/D:/projects/hackerrank-orchestrate-june26/code/evaluation/main.py)

It calls the same underlying pipeline as `code/main.py`, but uses:

- input: `dataset/sample_claims.csv`
- output: `code/evaluation/output.csv`

This keeps evaluation separate from final prediction output.
