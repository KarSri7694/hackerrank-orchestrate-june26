from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


class UserHistoryRiskAnalyzer:
    def __init__(self, user_history_path: str | Path):
        self.user_history_path = Path(user_history_path)
        self.user_history = pd.read_csv(self.user_history_path)

    def get_user_row(self, user_id: Any) -> pd.Series:
        matches = self.user_history[self.user_history["user_id"] == user_id]
        if matches.empty:
            raise KeyError(f"user_id {user_id!r} not found in {self.user_history_path}")
        return matches.iloc[0]

    def user_history_risk(self, user_row: pd.Series) -> dict[str, Any]:
        past_claim_count = user_row["past_claim_count"]
        accepted_claims = user_row["accept_claim"]
        manual_review_claims = user_row["manual_review_claim"]
        rejected_claims = user_row["rejected_claim"]
        recent_claims = user_row["last_90_days_claim_count"]
        history_flags = str(user_row["history_flags"]).lower()

        smoothed_accept_rate = (accepted_claims + 1) / (past_claim_count + 3)
        smoothed_reject_rate = (rejected_claims + 1) / (past_claim_count + 3)
        smoothed_manual_rate = (manual_review_claims + 1) / (past_claim_count + 3)

        risk_score = 0
        reasons: list[str] = []

        risk_score -= smoothed_accept_rate * 25
        risk_score += smoothed_reject_rate * 45
        risk_score += smoothed_manual_rate * 25

        if recent_claims >= 5:
            risk_score += 35
            reasons.append("very_high_recent_claim_activity")
        elif recent_claims >= 3:
            risk_score += 20
            reasons.append("elevated_recent_claim_activity")

        if "user_history_risk" in history_flags:
            risk_score += 40
            reasons.append("user_history_risk")

        if "manual_review_required" in history_flags:
            risk_score += 25
            reasons.append("manual_review_required")

        if risk_score < 15:
            level = "low"
        elif risk_score < 45:
            level = "medium"
        else:
            level = "high"

        return {
            "history_risk_level": level,
            "history_risk_score": round(risk_score, 2),
            "history_risk_reasons": reasons,
        }

    def evaluate_user(self, user_id: Any) -> dict[str, Any]:
        return self.user_history_risk(self.get_user_row(user_id))

    def combine_visual_and_history(
        self, visual_result: dict[str, Any], history_result: dict[str, Any]
    ) -> dict[str, Any]:
        result = dict(visual_result)

        flags: list[str] = []
        existing_flags = result.get("risk_flags", "none")
        if existing_flags != "none":
            flags.extend(str(existing_flags).split(";"))

        history_level = history_result["history_risk_level"]

        if history_level == "medium":
            flags.append("user_history_risk")
        elif history_level == "high":
            flags.append("user_history_risk")
            flags.append("manual_review_required")

        # Do not override clear visual evidence. Only add risk context.
        result["risk_flags"] = ";".join(sorted(set(flags))) if flags else "none"
        return result

    def evaluate_and_combine(
        self, user_id: Any, visual_result: dict[str, Any]
    ) -> dict[str, Any]:
        history_result = self.evaluate_user(user_id)
        return self.combine_visual_and_history(visual_result, history_result)
