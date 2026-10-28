"""Export the actual API without reading or creating user data."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from worklog.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/openapi.json")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="worklog-schema-") as directory:
        schema = create_app(directory, testing=True).openapi()
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Exported Worklog API to {path}")


if __name__ == "__main__":
    main()
