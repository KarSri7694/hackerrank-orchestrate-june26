# Architecture Detail

This document explains the current architecture of the claim-review evaluation pipeline in this repository, with emphasis on how data moves through the program, how the model is called, how outputs are validated, how user-history risk is scored, and how final rows are written to `output.csv`.

## Overview

The current implementation is centered around three Python files in [`code/evaluation`](/abs/path/D:/projects/hackerrank-orchestrate-june26/code/evaluation):

- [main.py](/abs/path/D:/projects/hackerrank-orchestrate-june26/code/evaluation/main.py)
- [llm_parser.py](/abs/path/D:/projects/hackerrank-orchestrate-june26/code/evaluation/llm_parser.py)
- [user_history_risk.py](/abs/path/D:/projects/hackerrank-orchestrate-june26/code/evaluation/user_history_risk.py)

The current end-to-end runtime flow is:

1. Read claim rows from the dataset CSV.
2. Resolve and load local images for each row.
3. Load the system prompt from markdown.
4. Send the claim text plus images to the OpenRouter-backed model.
5. Parse the model response as JSON.
6. Validate the JSON against the allowed output contract.
7. Retry the model call when parsing or validation fails.
8. Compute user-history risk from `dataset/user_history.csv`.
9. Merge history-based risk flags into the visual result.
10. Re-validate the merged final result.
11. Append the final row to `output.csv`.

## Files And Responsibilities

### `code/evaluation/main.py`

This file is the orchestration layer. It owns:

- dataset loading
- output schema definition
- allowed-value definitions
- JSON parsing
- response validation
- retry instruction generation
- model retry loop
- final CSV writing

It does not perform the actual model request itself and it does not calculate history risk itself. Those responsibilities are delegated.

### `code/evaluation/llm_parser.py`

This file is the model interaction layer. It owns:

- OpenAI-compatible client initialization for OpenRouter
- loading the prompt markdown file
- base64 image encoding
- multimodal request construction
- retry-context preservation within a single claim
- clearing context between claims

It is intentionally stateful for one claim and stateless across claims.

### `code/evaluation/user_history_risk.py`

This file is the post-processing and risk-analysis layer. It owns:

- loading `user_history.csv`
- locating the correct user row by `user_id`
- converting user-history fields into a numeric risk score
- mapping the numeric score into `low`, `medium`, or `high`
- merging history-derived risk flags into the model output

## Data Inputs

The program currently reads the following inputs:

- `dataset/sample_claims.csv`
- `dataset/user_history.csv`
- `code/evaluation/system_prompt.md`
- local image files referenced by `image_paths`

### `sample_claims.csv`

The current `main()` implementation reads:

```python
dataset = load_dataset(dataset_dir / "sample_claims.csv")
```

So the evaluation pipeline is currently pointed at the sample dataset, not `dataset/claims.csv`.

Each row contributes:

- `user_id`
- `image_paths`
- `user_claim`
- `claim_object`

### `user_history.csv`

This file provides the non-visual risk context. The current implementation uses:

- `past_claim_count`
- `accept_claim`
- `manual_review_claim`
- `rejected_claim`
- `last_90_days_claim_count`
- `history_flags`

### `system_prompt.md`

This file contains the visual-claim-review instructions given to the model. It includes:

- output schema expectations
- allowed value instructions
- object-specific claim logic
- evidence sufficiency rules derived from `evidence_requirements.csv`
- normalization guidance
- contradiction, insufficient-evidence, and supported examples

### Local image files

Each `image_paths` cell is a semicolon-separated string such as:

```text
images/sample/case_001/img_1.jpg;images/sample/case_001/img_2.jpg
```

The program splits that string, resolves each path under `dataset/`, verifies file existence, and sends the image bytes to the model as base64 `data:` URLs.

## Program Entry And Path Resolution

At startup, `main.py` constructs these important paths:

- `parent_dir`: repo root
- `dataset_dir`: `<repo_root>/dataset`
- `output_csv_path`: `<repo_root>/output.csv`

This means:

- input CSVs and images are expected under `dataset/`
- final results are written to the repo root as `output.csv`

## Detailed Runtime Flow

## 1. Dataset Loading

The helper:

```python
def load_dataset(file_path: Path) -> pd.DataFrame:
    return pd.read_csv(file_path)
```

loads the claims dataset into a pandas DataFrame.

The current code then iterates row-wise using:

```python
for user_id, image_paths_raw, user_prompt, claim_object in zip(...)
```

For each row:

- `user_id` is used for history lookup
- `image_paths_raw` is preserved for output writing
- `user_prompt` is the natural-language claim conversation
- `claim_object` determines object-part validation rules

## 2. Context Reset Per Claim

Before each row is processed, the code calls:

```python
llm_parser.reset_claim_context()
```

This is a critical architectural decision.

The OpenRouter server is stateless, so retrying a malformed answer requires resending context. The code handles that by storing conversation history in memory inside `LLMParser`.

However, that retry context must not leak from one claim to the next. `reset_claim_context()` clears:

- `self.messages`
- `self.last_response`

This guarantees that:

- retries for the same claim keep memory
- a new claim starts with a clean conversation

## 3. Image Path Expansion

For each row:

1. `image_paths_raw` is split on `;`
2. each relative image path is resolved against `dataset_dir`
3. the resulting `Path` objects are collected into `image_path`

If an image file is missing, the code currently prints a warning:

```python
Image file <path> does not exist.
```

The existing implementation does not stop immediately on a missing image. It continues with whatever valid images were found.

## 4. Prompt Loading

For every row, the program loads:

```python
parent_dir / "code" / "evaluation" / "system_prompt.md"
```

via `LLMParser.get_system_prompt()`.

The prompt is plain text and is sent as the `system` message in the chat-completions request.

## 5. Model Request Construction

This occurs in `LLMParser.run_interaction()`.

### Client setup

The parser constructs an OpenAI-compatible client:

```python
self.client = OpenAI(api_key=api_key, base_url=base_url)
```

Currently the caller configures:

- `base_url="https://openrouter.ai/api/v1"`
- `model="google/gemma-4-31b-it:free"`

### Multimodal user content

The initial user message contains:

- zero or more image parts, each encoded as:
  - `"type": "image_url"`
  - `"image_url": {"url": "data:image/png;base64,..."}`
- one text part containing the full claim conversation

So the model sees both:

- the conversation transcript
- the submitted visual evidence

### Streaming response

The code uses streaming:

```python
stream=True
```

and concatenates incremental chunks into one final response string.

## 6. Stateless Server, Stateful Retry Context

The server itself does not remember prior turns. To compensate, `LLMParser` stores a per-claim in-memory message buffer.

### First attempt for a claim

When `self.messages` is empty, `_ensure_claim_context()` initializes:

- one `system` message
- one multimodal `user` message containing images + claim text

### Retry attempt for the same claim

If validation fails later, `run_interaction()` is called again with `retry_instruction`.

In that case the parser appends:

- the previous assistant output as an `assistant` message
- a new `user` message containing the retry instruction

This allows the second request to include:

- what the model was originally asked
- what the model answered last time
- what was wrong with that answer
- what it must fix now

That behavior is essential because OpenRouter does not maintain session state for the client automatically.

## 7. JSON Parsing

After the model returns text, `main.py` calls:

```python
response_to_json(response)
```

This function:

1. strips optional code fences such as ```json
2. calls `json.loads(...)`

If parsing fails, a `json.JSONDecodeError` is raised.

## 8. Validation Layer

This is one of the core architectural protections in the program.

The model is not trusted to emit valid schema-compliant output even if prompted correctly. The code enforces correctness using explicit in-code validation.

### Required fields

The model response must contain all fields in `REQUIRED_MODEL_FIELDS`:

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

Missing fields are rejected.

Extra fields are also rejected.

### Boolean validation

These fields must be actual JSON booleans:

- `evidence_standard_met`
- `valid_image`

Strings like `"true"` or `"false"` are rejected.

### String validation

These must be non-empty strings:

- `evidence_standard_met_reason`
- `claim_status_justification`

### Allowed-value validation

The validator enforces exact allowed values from `problem_statement.md`.

#### `claim_status`

Allowed:

- `supported`
- `contradicted`
- `not_enough_information`

#### `issue_type`

Allowed:

- `dent`
- `scratch`
- `crack`
- `glass_shatter`
- `broken_part`
- `missing_part`
- `torn_packaging`
- `crushed_packaging`
- `water_damage`
- `stain`
- `none`
- `unknown`

#### `severity`

Allowed:

- `none`
- `low`
- `medium`
- `high`
- `unknown`

### Object-part validation by claim type

`object_part` is validated against a different allowed set depending on `claim_object`.

#### Car parts

- `front_bumper`
- `rear_bumper`
- `door`
- `hood`
- `windshield`
- `side_mirror`
- `headlight`
- `taillight`
- `fender`
- `quarter_panel`
- `body`
- `unknown`

#### Laptop parts

- `screen`
- `keyboard`
- `trackpad`
- `hinge`
- `lid`
- `corner`
- `port`
- `base`
- `body`
- `unknown`

#### Package parts

- `box`
- `package_corner`
- `package_side`
- `seal`
- `label`
- `contents`
- `item`
- `unknown`

### Risk-flag validation

Before history merge, the model is only allowed to emit:

- `none`
- `blurry_image`
- `cropped_or_obstructed`
- `low_light_or_glare`
- `wrong_angle`
- `wrong_object`
- `wrong_object_part`
- `damage_not_visible`
- `claim_mismatch`
- `possible_manipulation`
- `non_original_image`
- `text_instruction_present`
- `manual_review_required`

The model is explicitly not allowed to emit `user_history_risk`. That is appended manually later.

The validator also enforces:

- `risk_flags` must be a string
- values are split on `;`
- all flags must be allowed
- `none` cannot appear alongside other flags

### Supporting-image validation

`supporting_image_ids` must be:

- `none`, or
- a semicolon-separated list of image IDs

The validator rejects path-like or filename-like values. In practice that means:

- no `/`
- no `\`
- no `.`

So `img_1` is valid, while `images/test/case_001/img_1.jpg` is invalid.

## 9. Retry Loop

The retry loop lives in:

```python
get_validated_response(...)
```

The loop runs up to:

```python
MAX_MODEL_RETRIES = 3
```

### Retry condition 1: JSON parse failure

If `json.loads(...)` fails:

- the failure reason is captured
- a retry instruction is built
- the model is called again

### Retry condition 2: schema or allowed-value failure

If parsing succeeds but validation returns errors:

- the code builds a structured retry instruction
- the retry instruction includes:
  - a failure summary
  - the previous output
  - field-specific validation errors
  - the allowed values for the invalid field(s)
- the model is called again with that correction request added to the in-memory message history

### Failure after max retries

If all attempts fail, the code raises:

```python
ValueError("Model failed validation after ...")
```

This is fail-fast behavior. The code does not silently continue with invalid model output.

## 10. User-History Risk Scoring

This logic lives in `UserHistoryRiskAnalyzer.user_history_risk()`.

### Input row

The analyzer first fetches the user row from `user_history.csv` using `user_id`.

If the `user_id` is not found, it raises `KeyError`.

### Raw inputs used

The scoring formula uses:

- `past_claim_count`
- `accept_claim`
- `manual_review_claim`
- `rejected_claim`
- `last_90_days_claim_count`
- `history_flags`

### Smoothing

To avoid unstable rates for small user histories, the code uses additive smoothing:

```python
smoothed_accept_rate = (accepted_claims + 1) / (past_claim_count + 3)
smoothed_reject_rate = (rejected_claims + 1) / (past_claim_count + 3)
smoothed_manual_rate = (manual_review_claims + 1) / (past_claim_count + 3)
```

This prevents zero-history users from producing extreme 0 or 1 rates.

### Risk score formula

The numeric score starts at `0` and is adjusted as follows:

```python
risk_score -= smoothed_accept_rate * 25
risk_score += smoothed_reject_rate * 45
risk_score += smoothed_manual_rate * 25
```

Interpretation:

- accepted claims reduce suspicion
- rejected claims increase suspicion more strongly
- manual-review claims also increase suspicion

### Recent-activity adjustments

If the user has many recent claims:

- `recent_claims >= 5`
  - add `35`
  - add reason `very_high_recent_claim_activity`
- `recent_claims >= 3`
  - add `20`
  - add reason `elevated_recent_claim_activity`

### History-flag adjustments

If `history_flags` contains `user_history_risk`:

- add `40`
- add reason `user_history_risk`

If `history_flags` contains `manual_review_required`:

- add `25`
- add reason `manual_review_required`

### Risk level mapping

The numeric score is converted into a discrete level:

- `< 15` -> `low`
- `< 45` -> `medium`
- `>= 45` -> `high`

### Returned structure

The analyzer returns:

- `history_risk_level`
- `history_risk_score`
- `history_risk_reasons`

## 11. Merging Visual Output With History Risk

This is handled by:

```python
combine_visual_and_history(visual_result, history_result)
```

### Merge policy

The merge is intentionally narrow.

The code does not change:

- `claim_status`
- `issue_type`
- `object_part`
- `severity`
- evidence sufficiency decisions

It only modifies:

- `risk_flags`

This preserves the design rule that user history must not override clear visual evidence.

### Merge thresholds

If `history_risk_level == "medium"`:

- append `user_history_risk`

If `history_risk_level == "high"`:

- append `user_history_risk`
- append `manual_review_required`

### De-duplication and formatting

The merged flags are:

- collected into a list
- converted to a set
- sorted
- joined with `;`

If no flags remain, the result becomes `none`.

## 12. Final Validation After Merge

After history merge, the code validates the final output again:

```python
validate_response(..., allow_user_history_risk=True)
```

This second validation is important because:

- pre-merge validation checks what the model emitted
- post-merge validation checks what will actually be written to `output.csv`

In the final validation pass, `risk_flags` is allowed to include:

- all model-level flags
- `user_history_risk`

If the final merged result is invalid, the code raises a `ValueError` and does not write a row.

## 13. Writing To `output.csv`

The final row is assembled as:

- original input columns from the dataset row:
  - `user_id`
  - `image_paths`
  - `user_claim`
  - `claim_object`
- merged output fields from the validated result

The output order is controlled by `OUTPUT_COLUMNS`, which matches the required order in `problem_statement.md`.

### Append behavior

Rows are appended one at a time using:

```python
to_csv(mode="a", header=write_header, index=False)
```

Behavior:

- if `output.csv` does not exist, the header row is written
- if it already exists, new rows are appended without rewriting prior rows

This means repeated runs can continue appending unless `output.csv` is removed beforehand.

## 14. Current Dataflow Summary

The complete dataflow for one claim is:

1. Read one row from `sample_claims.csv`
2. Extract `user_id`, `image_paths`, `user_claim`, `claim_object`
3. Reset the model conversation state
4. Resolve local image file paths
5. Load system prompt text
6. Build the multimodal model request
7. Call the model through OpenRouter
8. Parse returned JSON
9. Validate required fields, types, and allowed values
10. If invalid, build retry instruction and re-call the model with preserved per-claim context
11. Once valid, read the user-history row for the same `user_id`
12. Compute smoothed risk rates and numeric risk score
13. Convert the numeric score into `low`, `medium`, or `high`
14. Append history-derived risk flags to the visual result
15. Validate the merged final result
16. Build the final output row in contract order
17. Append the row to `output.csv`

## 15. Current Architectural Strengths

- The model is not trusted blindly; outputs are validated in code.
- The allowed value sets are hardcoded to the problem contract.
- Retry logic is structured and bounded.
- Retry prompts include concrete failure reasons.
- OpenRouter statelessness is handled with explicit per-claim message history.
- User-history risk is kept separate from visual reasoning and merged conservatively.
- Final CSV writing follows the required output-column order.

## 16. Current Architectural Limitations

- `main.py` currently reads `sample_claims.csv`, not `claims.csv`.
- `output.csv` is append-only within the current implementation; rerunning without deleting the file may duplicate rows.
- Missing images only trigger a print warning; they do not currently hard-fail the row.
- `evidence_requirements.csv` is not parsed directly in code. Its rules are currently embedded into `system_prompt.md`.
- There is no explicit batching or concurrency; processing is row-by-row and model-call-by-model-call.
- There is no caching of model outputs or image encodings across runs.

## 17. Practical Interpretation

At a high level, the architecture is a controlled multimodal inference loop with deterministic validation and rule-based post-processing:

- the LLM performs visual claim interpretation
- code-level validators enforce schema and vocabulary correctness
- a deterministic scoring function adds user-history risk context
- only the validated, merged result is allowed into `output.csv`

That separation is the core design principle of the current program:

- model for perception and structured claim judgment
- Python for contract enforcement and risk-policy control
