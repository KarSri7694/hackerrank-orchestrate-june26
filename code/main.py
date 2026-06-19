import json

from llm_parser import LLMParser
from pathlib import Path
import pandas as pd
import os
from user_history_risk import UserHistoryRiskAnalyzer

parent_dir = Path(__file__).parent.parent
dataset_dir = parent_dir / "dataset"
default_output_csv_path = parent_dir / "output.csv"
default_system_prompt_path = Path(__file__).parent / "system_prompt.md"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MAX_MODEL_RETRIES = 3
OUTPUT_COLUMNS = [
    "user_id",
    "image_paths",
    "user_claim",
    "claim_object",
    "evidence_standard_met",
    "evidence_standard_met_reason",
    "risk_flags",
    "issue_type",
    "object_part",
    "claim_status",
    "claim_status_justification",
    "supporting_image_ids",
    "valid_image",
    "severity",
]
REQUIRED_MODEL_FIELDS = {
    "evidence_standard_met",
    "evidence_standard_met_reason",
    "risk_flags",
    "issue_type",
    "object_part",
    "claim_status",
    "claim_status_justification",
    "supporting_image_ids",
    "valid_image",
    "severity",
}

ALLOWED_VALUES = {
    "claim_status": {"supported", "contradicted", "not_enough_information"},
    "issue_type": {
        "dent",
        "scratch",
        "crack",
        "glass_shatter",
        "broken_part",
        "missing_part",
        "torn_packaging",
        "crushed_packaging",
        "water_damage",
        "stain",
        "none",
        "unknown",
    },
    "risk_flags": {
        "none",
        "blurry_image",
        "cropped_or_obstructed",
        "low_light_or_glare",
        "wrong_angle",
        "wrong_object",
        "wrong_object_part",
        "damage_not_visible",
        "claim_mismatch",
        "possible_manipulation",
        "non_original_image",
        "text_instruction_present",
        "manual_review_required",
    },
    "severity": {"none", "low", "medium", "high", "unknown"},
}

FINAL_ALLOWED_RISK_FLAGS = ALLOWED_VALUES["risk_flags"] | {"user_history_risk"}

ALLOWED_OBJECT_PARTS = {
    "car": {
        "front_bumper",
        "rear_bumper",
        "door",
        "hood",
        "windshield",
        "side_mirror",
        "headlight",
        "taillight",
        "fender",
        "quarter_panel",
        "body",
        "unknown",
    },
    "laptop": {
        "screen",
        "keyboard",
        "trackpad",
        "hinge",
        "lid",
        "corner",
        "port",
        "base",
        "body",
        "unknown",
    },
    "package": {
        "box",
        "package_corner",
        "package_side",
        "seal",
        "label",
        "contents",
        "item",
        "unknown",
    },
}

def load_dataset(file_path: Path) -> pd.DataFrame:
    return pd.read_csv(file_path)

def append_output_row(output_row: dict, output_csv_path: Path) -> None:
    output_frame = pd.DataFrame([[output_row[column] for column in OUTPUT_COLUMNS]], columns=OUTPUT_COLUMNS)
    write_header = not output_csv_path.exists()
    output_frame.to_csv(output_csv_path, mode="a", header=write_header, index=False)

def response_to_json(response: str) -> dict:
    cleaned_response = response.strip("```json").strip("```").strip()
    return json.loads(cleaned_response)

def validate_supporting_image_ids(value: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, str):
        return [f"supporting_image_ids must be a semicolon-separated string or 'none'. Got: {value!r}"]

    if value == "none":
        return errors

    image_ids = [item.strip() for item in value.split(";") if item.strip()]
    if not image_ids:
        errors.append("supporting_image_ids must contain at least one non-empty image id or 'none'.")
        return errors

    for image_id in image_ids:
        if "/" in image_id or "\\" in image_id or "." in image_id:
            errors.append(
                f"supporting_image_ids must contain image ids only, not paths or filenames. Got: {image_id!r}"
            )
    return errors

def validate_response(
    parsed_response: dict,
    claim_object: str,
    *,
    allow_user_history_risk: bool,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(parsed_response, dict):
        return [f"response must be a JSON object. Got: {type(parsed_response).__name__}"]

    missing_fields = sorted(REQUIRED_MODEL_FIELDS - set(parsed_response.keys()))
    if missing_fields:
        errors.append(f"missing required fields: {missing_fields}")

    extra_fields = sorted(set(parsed_response.keys()) - REQUIRED_MODEL_FIELDS)
    if extra_fields:
        errors.append(f"unexpected extra fields: {extra_fields}")

    for boolean_field in ("evidence_standard_met", "valid_image"):
        value = parsed_response.get(boolean_field)
        if not isinstance(value, bool):
            errors.append(f"{boolean_field} must be boolean true or false. Got: {value!r}")

    for string_field in (
        "evidence_standard_met_reason",
        "claim_status_justification",
    ):
        value = parsed_response.get(string_field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{string_field} must be a non-empty string. Got: {value!r}")

    for field in ("claim_status", "issue_type", "severity"):
        value = parsed_response.get(field)
        if value not in ALLOWED_VALUES[field]:
            errors.append(
                f"{field} must be one of: {sorted(ALLOWED_VALUES[field])}. Got: {value!r}"
            )

    object_part = parsed_response.get("object_part")
    allowed_object_parts = ALLOWED_OBJECT_PARTS.get(str(claim_object).lower())
    if allowed_object_parts is None:
        errors.append(f"Unsupported claim_object for validation: {claim_object!r}")
    elif object_part not in allowed_object_parts:
        errors.append(
            f"object_part must be one of: {sorted(allowed_object_parts)}. Got: {object_part!r}"
        )

    risk_flags = parsed_response.get("risk_flags")
    allowed_risk_flags = (
        FINAL_ALLOWED_RISK_FLAGS if allow_user_history_risk else ALLOWED_VALUES["risk_flags"]
    )
    if not isinstance(risk_flags, str):
        errors.append(f"risk_flags must be a semicolon-separated string. Got: {risk_flags!r}")
    else:
        split_flags = [flag.strip() for flag in risk_flags.split(";") if flag.strip()]
        if not split_flags:
            errors.append("risk_flags must be 'none' or a semicolon-separated list of flags.")
        invalid_flags = [
            flag for flag in split_flags if flag not in allowed_risk_flags
        ]
        if invalid_flags:
            errors.append(
                "risk_flags contains invalid values. "
                f"Allowed values: {sorted(allowed_risk_flags)}. "
                f"Got invalid values: {invalid_flags}"
            )
        if "none" in split_flags and len(split_flags) > 1:
            errors.append("risk_flags cannot contain 'none' with other flags.")

    errors.extend(validate_supporting_image_ids(parsed_response.get("supporting_image_ids")))

    return errors

def build_retry_instruction(response: str, failure_reason: str, validation_errors: list[str]) -> str:
    lines = [
        "Your previous output was invalid. Retry now.",
        f"Failure reason: {failure_reason}",
    ]
    if response:
        lines.append("Previous output:")
        lines.append(response)
    if validation_errors:
        lines.append("Validation errors:")
        lines.extend(f"- {error}" for error in validation_errors)
    lines.append("Output valid JSON only.")
    return "\n".join(lines)

def get_validated_response(
    llm_parser: LLMParser,
    system_prompt: str,
    user_prompt: str,
    image_paths: list[Path],
    claim_object: str,
) -> dict:
    retry_instruction: str | None = None
    last_failure = "Unknown validation failure."

    for attempt in range(MAX_MODEL_RETRIES):
        response = llm_parser.run_interaction(
            system_prompt,
            user_prompt,
            image_paths,
            retry_instruction=retry_instruction,
        )
        try:
            parsed_response = response_to_json(response)
        except json.JSONDecodeError as exc:
            last_failure = f"JSONDecodeError: {exc}"
            retry_instruction = build_retry_instruction(response, last_failure, [])
            continue

        validation_errors = validate_response(
            parsed_response,
            claim_object,
            allow_user_history_risk=False,
        )
        if not validation_errors:
            return parsed_response

        last_failure = "Response contained values outside the allowed lists."
        retry_instruction = build_retry_instruction(
            response, last_failure, validation_errors
        )

        if attempt < MAX_MODEL_RETRIES - 1:
            continue

    raise ValueError(f"Model failed validation after {MAX_MODEL_RETRIES} attempts: {last_failure}")

def run_dataset(
    input_csv_path: Path,
    output_csv_path: Path,
    *,
    truncate_output: bool = True,
) -> None:
    llm_parser = LLMParser(base_url="https://openrouter.ai/api/v1", model="google/gemma-4-31b-it:free", api_key=OPENROUTER_API_KEY)
    dataset = load_dataset(input_csv_path)
    user_history_risk_analyzer = UserHistoryRiskAnalyzer(dataset_dir / "user_history.csv")

    if truncate_output and output_csv_path.exists():
        output_csv_path.unlink()

    for user_id, image_paths_raw, user_prompt, claim_object in zip(
        dataset["user_id"],
        dataset["image_paths"],
        dataset["user_claim"],
        dataset["claim_object"],
    ):
        llm_parser.reset_claim_context()
        images = image_paths_raw.split(';')  
        image_path = []
        for image in images:
            image_file_path = dataset_dir / image.strip()
            if image_file_path.exists():
                image_path.append(image_file_path)
            else:
                print(f"Image file {image_file_path} does not exist.")
        system_prompt = llm_parser.get_system_prompt(default_system_prompt_path)
        parsed_response = get_validated_response(
            llm_parser,
            system_prompt,
            user_prompt,
            image_path,
            claim_object,
        )
        risk_data = user_history_risk_analyzer.evaluate_user(user_id)
        combined_response = user_history_risk_analyzer.combine_visual_and_history(
            parsed_response, risk_data
        )
        final_validation_errors = validate_response(
            combined_response,
            claim_object,
            allow_user_history_risk=True,
        )
        if final_validation_errors:
            raise ValueError(
                "Combined output failed final validation: "
                + "; ".join(final_validation_errors)
            )
        output_row = {
            "user_id": user_id,
            "image_paths": image_paths_raw,
            "user_claim": user_prompt,
            "claim_object": claim_object,
            **combined_response,
        }
        append_output_row(output_row, output_csv_path)
        print(
            f"\nUser ID: {user_id},\nRisk Data: {risk_data},\nCombined Response: {combined_response},\nWritten To: {output_csv_path}"
        )

def main():
    run_dataset(dataset_dir / "claims.csv", default_output_csv_path)

if __name__ == "__main__":
    main()
    
