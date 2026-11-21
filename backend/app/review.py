import json
import sys
from pathlib import Path

from backend.app.extract_tasks import extract_tasks


def main(argv):
    if len(argv) not in (2, 3):
        print("usage: python -m app.extract_tasks <plan.md> [out.json]", file=sys.stderr)
        return 2
    result = extract_tasks(Path(argv[1]).read_text())
    output = json.dumps(result, indent=2)
    if len(argv) == 3:
        Path(argv[2]).write_text(output + "\n")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
