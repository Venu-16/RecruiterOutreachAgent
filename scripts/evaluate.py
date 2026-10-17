from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.evaluation import qualification_metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic qualification against manually labeled JSON")
    parser.add_argument("labels", type=Path)
    args = parser.parse_args()
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    print(json.dumps(qualification_metrics(labels), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())