"""What recognition error costs the rest of the pipeline, measured one rate at a time.

Every other evaluation in this project compares two arms on the same input. This one
cannot: the control transcriber returns the reference transcript, so it scores perfectly
by construction, and a real recogniser needs audio this repository does not and must not
contain. So the question is turned around. Instead of asking how often recognition is
wrong, the sweep fixes the error rate and asks what survives it:

* **word error rate** -- the standard measure, reported so the corruption rate can be read
  against published Whisper figures for pt-BR rather than taken on faith.
* **intent agreement** -- how often the degraded transcript classifies the same way the
  clean one does. Agreement rather than accuracy against the label, deliberately: the
  lexical classifier is not perfect on clean text either, and charging its own errors to
  the recogniser would overstate the damage. Agreement isolates what transcription broke.
* **figure survival** -- the share of the amounts and instalment counts the customer
  actually said that are still present and unaltered. This is the number the design turns
  on.
* **confirmation rate** -- how often the transcript carries a figure at all, and therefore
  how often the read-back rule in `transcribe.needs_confirmation` would fire.

The finding the sweep produces is the gap between the middle two columns. Intent is
redundantly encoded -- a request to negotiate says so several ways in one sentence -- so it
survives a great deal of corruption. A figure is encoded once, and when it breaks nothing
downstream can tell. That asymmetry is why the agent confirms spoken amounts instead of
acting on them.
"""

import json
import random
import statistics
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from collection.agents.intent import Intent, IntentClassifier
from collection.config import settings
from collection.voice.degrade import degrade
from collection.voice.transcribe import spoken_figures

#: Corruption rates to sweep. The top of the range is well past any usable recogniser; it
#: is there so the curve shows where each measure finally breaks, not just that it holds.
RATES = (0.0, 0.05, 0.10, 0.20, 0.30, 0.40)

#: Degraded variants per message per rate. The corruption is random, so a single draw
#: would report the luck of one seed; five is enough to stabilise the means on a set this
#: size without the sweep becoming slow enough that nobody runs it.
REPEATS = 5


@dataclass(frozen=True)
class VoiceMessage:
    id: str
    text: str
    intent: str
    figures: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    note: str = ""


def load_messages(path: Path | str | None = None) -> list[VoiceMessage]:
    path = Path(path) if path else settings.dir_knowledge / "voice-messages.json"
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        VoiceMessage(
            id=str(row["id"]),
            text=str(row["text"]),
            intent=str(row.get("intent", "unknown")),
            figures=[str(figure) for figure in row.get("figures", [])],
            duration_s=float(row.get("duration_s", 0.0)),
            note=str(row.get("note", "")),
        )
        for row in payload
    ]


def _fold(text: str) -> str:
    lowered = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in lowered if unicodedata.category(c) != "Mn")


def _edits(reference: list[str], hypothesis: list[str]) -> int:
    """Levenshtein distance, the usual two-row dynamic program."""
    if not reference:
        return len(hypothesis)
    previous = list(range(len(reference) + 1))
    for i, right in enumerate(hypothesis, start=1):
        current = [i]
        for j, left in enumerate(reference, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (left != right))
            )
        previous = current
    return previous[-1]


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate, accent-folded.

    Folded because the downstream classifier and retriever both fold: counting `divida`
    against `dívida` as an error would report a failure the system does not actually
    experience.
    """
    words = _fold(reference).split()
    if not words:
        return 0.0
    return _edits(words, _fold(hypothesis).split()) / len(words)


def cer(reference: str, hypothesis: str) -> float:
    reference_chars = list(_fold(reference).replace(" ", ""))
    if not reference_chars:
        return 0.0
    return _edits(reference_chars, list(_fold(hypothesis).replace(" ", ""))) / len(reference_chars)


def figure_survival(expected: list[str], hypothesis: str) -> float:
    """Share of the figures the customer actually said that are still intact.

    A figure counts as surviving only if it appears as a whole token in the transcript.
    Substring matching would score `dez` as surviving inside `dezoito`, which is precisely
    the confusion the measure is meant to catch.
    """
    if not expected:
        return 1.0
    heard = set(_fold(hypothesis).split())
    return sum(1 for figure in expected if _fold(figure) in heard) / len(expected)


@dataclass
class RateScores:
    rate: float
    samples: int
    wer: float
    cer: float
    intent_agreement: float
    intent_accuracy: float
    figure_survival: float
    figures_total: int
    confirmation_rate: float
    unknown_rate: float

    def as_row(self) -> dict[str, object]:
        return {
            "corruption": self.rate,
            "wer": round(self.wer, 3),
            "cer": round(self.cer, 3),
            "intent_agreement": round(self.intent_agreement, 3),
            "intent_accuracy": round(self.intent_accuracy, 3),
            "figures_intact": round(self.figure_survival, 3),
            "unknown": round(self.unknown_rate, 3),
            "confirm": round(self.confirmation_rate, 3),
            "samples": self.samples,
        }


def run(
    messages: list[VoiceMessage],
    rate: float,
    repeats: int = REPEATS,
    seed: int = 42,
    classifier: IntentClassifier | None = None,
) -> RateScores:
    """Degrade every message `repeats` times at this rate and score what came through."""
    classifier = classifier or IntentClassifier()
    clean = {message.id: classifier.classify(message.text).intent for message in messages}

    word_errors: list[float] = []
    char_errors: list[float] = []
    agreements: list[float] = []
    accuracies: list[float] = []
    survivals: list[float] = []
    unknowns: list[float] = []
    confirmations: list[float] = []
    figures_total = 0

    draws = 1 if rate <= 0 else repeats
    for message in messages:
        for draw in range(draws):
            # Seeded per message and per draw: the same sweep run twice reports the same
            # numbers, which is the minimum for a figure that goes in the paper.
            rng = random.Random(f"{seed}:{message.id}:{rate}:{draw}")
            heard = degrade(message.text, rate, rng)
            intent = classifier.classify(heard).intent

            word_errors.append(wer(message.text, heard))
            char_errors.append(cer(message.text, heard))
            agreements.append(float(intent == clean[message.id]))
            accuracies.append(float(str(intent) == message.intent))
            unknowns.append(float(intent is Intent.UNKNOWN))
            # Only messages that carry a figure count towards survival. Including the
            # ones that do not would score them a trivial 1.0 and dilute the measure
            # towards the figure the design is trying not to be reassured by.
            if message.figures:
                survivals.append(figure_survival(message.figures, heard))
            confirmations.append(float(bool(spoken_figures(heard))))
            figures_total += len(message.figures)

    return RateScores(
        rate=rate,
        samples=len(word_errors),
        wer=statistics.fmean(word_errors),
        cer=statistics.fmean(char_errors),
        intent_agreement=statistics.fmean(agreements),
        intent_accuracy=statistics.fmean(accuracies),
        figure_survival=statistics.fmean(survivals) if survivals else 1.0,
        figures_total=figures_total,
        confirmation_rate=statistics.fmean(confirmations),
        unknown_rate=statistics.fmean(unknowns),
    )


def sweep(
    messages: list[VoiceMessage],
    rates: tuple[float, ...] = RATES,
    repeats: int = REPEATS,
) -> tuple[pd.DataFrame, list[RateScores]]:
    classifier = IntentClassifier()
    results = [run(messages, rate, repeats=repeats, classifier=classifier) for rate in rates]
    return pd.DataFrame([result.as_row() for result in results]), results


def write_report(table: pd.DataFrame, results: list[RateScores], path: Path | str) -> str:
    """Write the sweep, then say plainly what it implies for the agent."""
    path = Path(path)
    control = results[0]
    worst = results[-1]
    # The row a usable recogniser actually sits near: published pt-BR word error rates for
    # a small Whisper checkpoint are in this region, so it is the honest place to read the
    # gap between what survives and what does not.
    realistic = min(results[1:], key=lambda r: abs(r.wer - 0.10)) if len(results) > 1 else control
    lines = [
        "# Voice transcription: what recognition error costs downstream",
        "",
        "Generated by `make voice`. The transcripts are the labelled references in",
        "`knowledge/voice-messages.json`, corrupted at a controlled rate by the error model",
        "in `voice/degrade.py`. No audio and no model are involved, which is why this runs",
        "in CI; a real recogniser is measured separately with `collection voice --audio`.",
        "",
        "## Sweep",
        "",
        table.to_markdown(index=False),
        "",
        "`intent_agreement` is against the clean transcript's own classification, not the",
        "label, so the lexical classifier's own errors are not charged to recognition.",
        "`figures_intact` counts the amounts and instalment counts the customer actually",
        f"said -- {control.figures_total} of them across the set -- that survive as whole tokens.",
        "",
        "## What it says",
        "",
        f"- At zero corruption the control reproduces the reference exactly: WER "
        f"{control.wer:.3f}, intent agreement {control.intent_agreement:.3f}, figures intact "
        f"{control.figure_survival:.3f}. A control arm that cannot fail is the point of it.",
        f"- At a word error rate of {realistic.wer:.3f} -- around what a small Whisper "
        f"checkpoint reports for pt-BR, and a transcript that reads as fine -- intent still "
        f"agrees {realistic.intent_agreement:.0%} of the time, but only "
        f"{realistic.figure_survival:.0%} of spoken figures are intact. Roughly one figure "
        f"in {max(1, round(1 / max(1e-6, 1 - realistic.figure_survival)))} is already wrong "
        "at an error rate nobody would call a problem.",
        f"- At the worst rate swept ({worst.rate:.0%}, WER {worst.wer:.3f}) intent agreement "
        f"is still {worst.intent_agreement:.3f} while figures intact has fallen to "
        f"{worst.figure_survival:.3f}.",
        "- Intent is redundantly encoded across a sentence and survives; a figure is stated",
        "  once, and when recognition breaks it nothing downstream can tell. That gap, not",
        "  the word error rate, is what the design has to answer for.",
        "",
        "## What the agent does about it",
        "",
        "A figure heard in audio is a proposal to confirm, never an instruction to execute.",
        "`transcribe.needs_confirmation` detects one and the responsive agent reads it back",
        f"before acting -- which on this set fires on {control.confirmation_rate:.0%} of",
        "messages. Transcripts below `MIN_CONFIDENCE` are not answered at all.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def load(path: Path | str | None = None) -> list[VoiceMessage]:
    return load_messages(path)
