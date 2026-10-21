"""A model of how speech recognition gets pt-BR wrong, so the damage can be measured.

The point of this module is not to estimate a word error rate -- a real recogniser on real
audio does that, and `WhisperTranscriber` is there for it. The point is to answer a
question the error rate alone cannot: *when recognition is wrong, what breaks?* Those are
different questions, and only the second one changes the design. A transcript with one
word in five wrong may still route to the right intent and still carry the right amount,
or it may route correctly and quietly corrupt the amount -- and the second case is the one
that sends a customer the wrong debt.

Measuring that needs transcripts at a controlled error rate, which is what `degrade`
produces: the labelled reference, corrupted word by word with the mistakes a recogniser
actually makes in this domain, deterministically for a given seed. The corruptions are
drawn from five families, each observed in Whisper output on Brazilian collection audio:

* **confusion** -- a word swapped for one that sounds like it. The table below is
  domain-specific on purpose; `boleto`/`boletos` is harmless, `juros`/`jurus` is not.
* **unaccented** -- accents lost, which is the single most common surface error and almost
  always harmless, because the intent classifier folds accents anyway.
* **numeral** -- a digit changed or a spoken number swapped for its neighbour. Rare per
  word and ruinous when it lands, which is the asymmetry the sweep exists to show.
* **deletion** -- a short function word dropped.
* **filler** -- a hesitation transcribed as a word.

A corruption rate here is the probability that any given word is touched, so it is an
upper bound on the resulting word error rate rather than the rate itself.
"""

import random
import re
import unicodedata

#: Words a recogniser confuses in this domain, both directions. Collected from Whisper
#: `small` output on Portuguese collection audio; the entries that matter are the ones
#: that change meaning rather than spelling.
CONFUSIONS: dict[str, str] = {
    "boleto": "boleta",
    "juros": "jurus",
    "parcelar": "parcela",
    "parcelamento": "parcelamentos",
    "desconto": "descontos",
    "dívida": "divida",
    "débito": "devido",
    "vencimento": "vencimentos",
    "atraso": "atrás",
    "pagar": "pagá",
    "pagamento": "pagamentos",
    "acordo": "a corda",
    "entrada": "em trada",
    "negociar": "negociei",
    "quitar": "quitá",
    "fatura": "faturas",
    "cobrança": "cobrando",
    "segunda": "segundo",
    "cancelar": "cancelei",
    "comprovante": "comprovantes",
}

#: Spoken numbers a recogniser swaps for a neighbour. The pairs are chosen for acoustic
#: similarity, not numeric distance, which is exactly why the result is dangerous.
NUMBER_CONFUSIONS: dict[str, str] = {
    "dois": "doze",
    "três": "treze",
    "quatro": "quatorze",
    "seis": "dez",
    "sete": "sessenta",
    "oito": "oitenta",
    "dez": "seis",
    "doze": "dois",
    "quinze": "cinquenta",
    "vinte": "trinta",
    "trinta": "quarenta",
    "cinquenta": "sessenta",
    "sessenta": "setenta",
    "cem": "sem",
    "mil": "meu",
    "duzentos": "trezentos",
}

#: Hesitations a recogniser emits as words.
FILLERS = ("é", "ahn", "tipo", "assim", "então")

#: Short words a recogniser drops without the sentence becoming unreadable.
DROPPABLE = frozenset({"a", "o", "as", "os", "de", "da", "do", "em", "um", "uma", "e", "que"})

#: Relative weight of each corruption family, given that a word is being corrupted at all.
#: Unaccented dominates because it dominates in real output; numeral is low because a
#: numeral has to be present for it to apply, and the sweep is about what happens when it
#: does, not about how often.
WEIGHTS: dict[str, float] = {
    "unaccented": 0.40,
    "confusion": 0.25,
    "deletion": 0.15,
    "filler": 0.12,
    "numeral": 0.08,
}

_DIGITS = re.compile(r"\d")
_WORD = re.compile(r"\w+", re.UNICODE)


def strip_accents(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", word) if unicodedata.category(c) != "Mn")


def _corrupt_numeral(word: str, rng: random.Random) -> str:
    lowered = word.lower()
    if lowered in NUMBER_CONFUSIONS:
        return NUMBER_CONFUSIONS[lowered]
    digits = [match.start() for match in _DIGITS.finditer(word)]
    if not digits:
        return word
    position = rng.choice(digits)
    replacement = str((int(word[position]) + rng.choice([-1, 1])) % 10)
    return word[:position] + replacement + word[position + 1 :]


#: Vowel substitutions a recogniser makes on a word it does not know. Portuguese vowel
#: reduction in unstressed syllables is the usual cause, and the result is a word that is
#: wrong but still plausible -- which is what makes it hard to detect downstream.
_VOWEL_SHIFTS = {"a": "á", "e": "i", "i": "e", "o": "u", "u": "o", "á": "a", "ê": "e"}


def _mishear(word: str, rng: random.Random) -> str:
    """A word not in the confusion table, misheard generically.

    Without this the confusion family would collapse into accent-stripping for every word
    the table does not list, and since the measures fold accents, most of the corruption
    would cost nothing and the sweep would quietly flatter the system.
    """
    letters = list(word)
    positions = [i for i, letter in enumerate(letters) if letter.lower() in _VOWEL_SHIFTS]
    if positions:
        position = rng.choice(positions)
        letters[position] = _VOWEL_SHIFTS[letters[position].lower()]
        return "".join(letters)
    if len(letters) > 3:
        del letters[rng.randrange(len(letters))]
    return "".join(letters)


def _is_numeral(word: str) -> bool:
    return bool(_DIGITS.search(word)) or word.lower() in NUMBER_CONFUSIONS


def degrade(text: str, rate: float, rng: random.Random | None = None) -> str:
    """The reference transcript as a recogniser with this error rate might have heard it.

    `rate` is the per-word probability of a corruption, 0.0 meaning a perfect recogniser.
    The result is deterministic for a given `rng`, because an evaluation that moves
    between runs cannot be reported.
    """
    if rate <= 0:
        return text
    rng = rng or random.Random(0)

    families = list(WEIGHTS)
    weights = [WEIGHTS[family] for family in families]
    out: list[str] = []
    for word in text.split():
        if rng.random() >= rate:
            out.append(word)
            continue

        # A numeral is corrupted as a numeral when it is picked at all: substituting
        # "mil" for "meu" is a different error from dropping its accent, and lumping them
        # together would hide the asymmetry the whole sweep is meant to expose.
        family = rng.choices(families, weights=weights, k=1)[0]
        if _is_numeral(word) and family in {"confusion", "unaccented"}:
            family = "numeral"

        if family == "unaccented":
            out.append(strip_accents(word))
        elif family == "confusion":
            bare = _WORD.search(word)
            key = bare.group(0).lower() if bare else ""
            out.append(CONFUSIONS[key] if key in CONFUSIONS else _mishear(word, rng))
        elif family == "numeral":
            out.append(_corrupt_numeral(word, rng))
        elif family == "deletion":
            if word.lower().strip(".,?!") not in DROPPABLE:
                out.append(word)
        elif family == "filler":
            out.append(rng.choice(FILLERS))
            out.append(word)
    return " ".join(out)
