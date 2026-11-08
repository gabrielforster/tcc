"""Report test coverage of the critical functions, and fail below the target.

The schedule's target is "≥ 80% of the critical functions covered", which is a claim about
functions, not about lines: a module at 90% line coverage can still hide a whole
untested function behind one long, well-tested one. So this reads coverage.py's JSON
report (which carries a per-function breakdown since 7.5) and reports, for each group
of critical modules:

* functions executed -- at least one statement ran under the suite. This is the number
  the target is checked against.
* functions fully covered -- every statement ran.
* line coverage, for comparison with the usual figure.

"Critical" is the list below and nothing else. It is the schedule's list -- parser,
features, rules engine, intent, templates -- plus the orchestrator and the responder,
which every flow passes through, and the two external-service seams (the LLM client and
the speech recogniser), whose failure paths decide whether a customer gets an answer or
a person.

Run it through `make coverage`, which produces the JSON first.
"""

import json
import sys
from pathlib import Path

TARGET = 80.0

CRITICAL: dict[str, list[str]] = {
    "parser / ingest": [
        "data/ingest.py",
        "data/anonymize.py",
        "rag/documents.py",
        "rag/chunking.py",
    ],
    "features": ["features/build.py", "features/preprocess.py"],
    "rules engine": ["rules/policy.py", "rules/window.py"],
    "intent": ["agents/intent.py"],
    "templates + compliance": [
        "outreach/brief.py",
        "outreach/compose.py",
        "outreach/compliance.py",
    ],
    "orchestrator": [
        "agents/orchestrator.py",
        "agents/queue.py",
        "agents/events.py",
        "agents/proactive.py",
    ],
    "responder": ["agents/responsive.py", "llm/responder.py", "llm/prompt.py"],
    "service seams": ["llm/client.py", "voice/transcribe.py", "voice/audio.py"],
}


def _functions(entry: dict) -> list[tuple[str, dict]]:
    # The empty key is the module body (imports, constants), not a function.
    return [(name, f["summary"]) for name, f in entry["functions"].items() if name]


def main(path: str = "coverage.json") -> int:
    files = json.loads(Path(path).read_text())["files"]
    rows = []
    totals = [0, 0, 0]
    untested: list[str] = []
    for group, modules in CRITICAL.items():
        functions, executed, full, statements, covered = 0, 0, 0, 0, 0
        for module in modules:
            entry = files[f"src/collection/{module}"]
            for name, summary in _functions(entry):
                if summary["num_statements"] == 0:
                    continue
                functions += 1
                executed += summary["covered_lines"] > 0
                if summary["covered_lines"] == 0:
                    untested.append(f"{module}:{name}")
                full += summary["covered_lines"] == summary["num_statements"]
            statements += entry["summary"]["num_statements"]
            covered += entry["summary"]["covered_lines"]
        totals[0] += functions
        totals[1] += executed
        totals[2] += full
        rows.append((group, functions, executed, full, 100 * covered / statements))

    print(f"{'group':<24}{'functions':>10}{'executed':>10}{'full':>8}{'lines':>8}")
    for group, functions, executed, full, lines in rows:
        print(
            f"{group:<24}{functions:>10}{100 * executed / functions:>9.1f}%"
            f"{100 * full / functions:>7.1f}%{lines:>7.1f}%"
        )
    functions, executed, full = totals
    share = 100 * executed / functions
    print(f"{'all critical':<24}{functions:>10}{share:>9.1f}%{100 * full / functions:>7.1f}%")
    if untested:
        print("\nnever executed:\n  " + "\n  ".join(untested))
    failing = [group for group, f, e, _, _ in rows if 100 * e / f < TARGET]
    if share < TARGET or failing:
        print(f"below the {TARGET:.0f}% target: {', '.join(failing) or 'all critical'}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
