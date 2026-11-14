"""Print a SARIF report's findings by level, and fail if any is at the blocking level.

    python3 scripts/sarif_gate.py <tool> <report.sarif>

The scanners that rate severity do not agree on a vocabulary (Semgrep says ERROR, Trivy
says HIGH/CRITICAL, zizmor says high), but each maps its own scale onto SARIF's `level`,
and they agree there: the top of the scale is `error`. So the gate is one rule for every
tool -- an `error` result fails the run, `warning` and `note` are printed and uploaded to
code scanning, nothing more -- instead of a severity flag per tool that each spells
differently. Results suppressed in the source (a SARIF `suppressions` entry) are left out.

Standard library only: CI runs it on the runner's own Python, before any project install.
"""

import json
import sys
from collections import Counter

BLOCKING = "error"


def main(tool: str, path: str) -> int:
    try:
        with open(path, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        # No report means the scanner itself failed; that is not a pass.
        print(f"{tool}: no readable SARIF report at {path} ({error})", file=sys.stderr)
        return 1

    levels: Counter[str] = Counter()
    blocking = []
    for run in report.get("runs", []):
        rules = {
            rule.get("id"): rule for rule in run.get("tool", {}).get("driver", {}).get("rules", [])
        }
        for result in run.get("results", []):
            if result.get("suppressions"):
                continue
            rule = rules.get(result.get("ruleId"), {})
            # A result without a level inherits its rule's default, and SARIF's own default.
            level = result.get("level") or rule.get("defaultConfiguration", {}).get(
                "level", "warning"
            )
            levels[level] += 1
            if level == BLOCKING:
                # The rule's one-line title where there is one: Trivy's per-result message
                # opens with the file name, which the location already gives.
                title = rule.get("shortDescription", {}).get("text") or result.get(
                    "message", {}
                ).get("text", "")
                blocking.append((result, title))

    summary = ", ".join(f"{levels[k]} {k}" for k in ("error", "warning", "note", "none"))
    print(f"{tool}: {summary}")
    for result, title in blocking:
        location = result.get("locations", [{}])[0].get("physicalLocation", {})
        uri = location.get("artifactLocation", {}).get("uri", "?")
        line = location.get("region", {}).get("startLine", "?")
        print(f"  {result.get('ruleId')} {uri}:{line} {title.strip()[:160]}")
    return 1 if blocking else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
