from pathlib import Path
import sys


evaluation_dir = Path(__file__).resolve().parent
code_dir = evaluation_dir.parent
repo_root = code_dir.parent

if str(code_dir) not in sys.path:
    sys.path.insert(0, str(code_dir))

import main as claim_pipeline


def main() -> None:
    claim_pipeline.run_dataset(
        repo_root / "dataset" / "sample_claims.csv",
        evaluation_dir / "output.csv",
    )


if __name__ == "__main__":
    main()
