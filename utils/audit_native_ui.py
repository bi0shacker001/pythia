"""List backend operations referenced by native screens, and the migration gaps.

Source references establish wiring, not usability or runtime success. This reads only
the checked schema and Kotlin sources; it imports neither Qt nor Pythia's backend.
Run from any directory with Python 3.10 or newer. JSON output is suitable for keeping
successive audit snapshots outside the source tree.
"""

import json
import re
from pathlib import Path


def audit(root: Path) -> dict:
    schema = json.loads((root / "schema/backend-ipc.json").read_text(encoding="utf-8"))
    sources = []
    for base in ("ui-native/src", "ui-native/core/src/commonMain"):
        for path in sorted((root / base).rglob("*.kt")):
            sources.append((path.relative_to(root).as_posix(), path.read_text(encoding="utf-8")))
    operations = []
    for method in schema["methods"]:
        name = method["name"]
        parts = name.split("_")
        kotlin = parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])
        pattern = re.compile(r"\b" + re.escape(kotlin) + r"\s*\(")
        screens, infrastructure = [], []
        for source_path, text in sources:
            for number, line in enumerate(text.splitlines(), 1):
                # Comments explain an operation but are not a call path.
                if line.lstrip().startswith(("//", "*")) or not pattern.search(line):
                    continue
                target = screens if source_path.startswith("ui-native/src/") else infrastructure
                target.append(f"{source_path}:{number}")
        operations.append({"operation": name, "screens": screens, "infrastructure": infrastructure})
    return {
        "evidence": "Static call references only; reachability, state handling and runtime behaviour need review.",
        "total": len(operations),
        "screen_references": sum(bool(row["screens"]) for row in operations),
        "operations": operations,
    }


if __name__ == "__main__":
    print(json.dumps(audit(Path(__file__).resolve().parents[1]), indent=2))
