"""Phrase lexicons shared by the renderer and the verifier (research/27 §4.6 rules 2, 3, 4, 6, 7).

All matching is case-insensitive on word boundaries. Lists are deliberately small and explicit: the
verifier's strictness is part of the eval protocol (EVAL_PROTOCOL_AGENT.md), so changes are versioned.
"""
import re

LEXICON_VERSION = "agent-lex-v5"

# ---- rule 2: phrase classes. Each class: trigger regex -> allowed (kind, pts-subkind or None) of a slot.
PHRASE_CLASSES = [
    # name, regex, allowed kinds
    ("win_chance", r"\bwin(?:ning)?\s+(?:chances?|probabilit(?:y|ies)|odds)\b|\bchances?\s+(?:of|to)\s+win(?:ning)?\b"
                   r"|\bodds\s+of\s+winning\b|\bwin\s+rate\b", {("wp", None)}),
    ("start", r"\bstart(?:ed|ing|s)?\b", {("wp", None)}),
    ("percentile", r"\bpercentile\b", {("percentile", None)}),
    ("chance", r"\bchances?\b|\bprobabilit(?:y|ies)\b|\bodds\b|\blikel(?:y|ihood)\b|\bunlikely\b",
     {("prob", None)}),
    # "swung"/"swing" are neutral verbs ("choices swung {c}"); chance-event words name luck
    ("luck", r"\blu(?:ck|cky)\b|\bunlucky\b|\bhax\b|\bdice\b|\bcrit(?:s|ical(?:\s+hits?)?)?\b|\bmiss(?:es|ed)?\b"
             r"|\bflinch(?:ed|es)?\b|\bfull(?:y)?\s+paraly[sz]\w*|\bdamage\s+rolls?\b|\bspeed\s+ties?\b",
     {("pts", "luck")}),
    ("prefer", r"\bprefer(?:s|red|ring)?\b|\bdisagree(?:s|d|ment|ments)?\b|\bgap\b|\bmargin\b",
     {("pts", "gap")}),
    ("choices", r"\bchoices\b|\bdecisions\b|\bskill\b", {("pts", "choices")}),
    ("verdict", r"\bverdict\b", {("verdict", None)}),
    # verdict margins (get_verdict): "X was better by {m} under your read (three runs: ...)"
    ("verdict_runs", r"\bbetter\s+by\b|\bruns?\b|\bunder\s+(?:your\s+read|the\s+reference)\b", {("pts", "run")}),
]
PHRASE_RE = [(n, re.compile(rx, re.I), allowed) for n, rx, allowed in PHRASE_CLASSES]
TURN_ADJ_RE = re.compile(r"\bturns?\s*$", re.I)   # "turn {t}": the placeholder must be a turn number
NUMERIC_KINDS = {"pts", "wp", "prob", "percentile", "turn"}
# "your choices cost you {c}": choices_pts is both players' choices together, never "yours"
YOUR_CHOICES_RE = re.compile(r"\byour\s+(?:own\s+)?(?:choices|decisions|play|mistakes|moves)\b", re.I)
# choices_pts must be described as both players' choices (research/28 §B: "Choices, yours and your opponent's")
BOTH_PLAYERS_RE = re.compile(r"\bboth\b|\byours\s+and\s+(?:your\s+)?(?:opponent|rival)'?s\b|\byou\s+and\s+your\s+"
                             r"(?:opponent|rival)\b|\beach\s+side\b|\beither\s+side\b|\bboth\s+players\b", re.I)

# ---- rule 8: never-say wording (research/28 §B): cost framing, coach/analyzer framing, summed disagreements,
# and claims the product must not make
NEVER_SAY = [
    ("cost framing", r"\bcost(?:s|ly|ing)?\b|\bthrew\s+away\b|\blost\s+you\b"),
    ("coach/analyzer framing", r"\bcoach\w*|\btrainer\w*|\btutor\w*|\banaly[sz]er\w*|\bsports?\b"),
    ("summed disagreements", r"\bin\s+total\b|\btotal(?:ed|led|s)?\b|\baltogether\b|\bsum(?:med|s)?\b|\bsum\s+of\b"
                             r"|\badded\s+up\b|\badds\s+up\b|\bcumulative\w*"),
    # the bridge's choices are both players': the result is never attributed to the player's own choices
    ("result attributed to your choices",
     r"\b(?:driven|decided|caused|determined|explained|won|lost|settled)\s+(?:mostly\s+|mainly\s+|largely\s+)?"
     r"(?:by|through|on)\s+your\s+(?:own\s+)?(?:choices|decisions|play|moves|mistakes)\b"
     r"|\b(?:due|thanks|owing)\s+to\s+your\s+(?:own\s+)?(?:choices|decisions|play|moves|mistakes)\b"
     r"|\byour\s+(?:own\s+)?(?:choices|decisions|play|moves)\s+(?:won|lost|decided|drove|caused|determined|"
     r"settled)\b"),
    ("forbidden claim", r"\bdeterministic\w*|\bfine[- ]?tun\w*|\bverifi(?:ed|able)\b|\bSOTA\b|\bstate[- ]of[- ]the[- ]art\b"
                        r"|\bstrongest\b"),
]
NEVER_SAY_RE = [(n, re.compile(rx, re.I)) for n, rx in NEVER_SAY]

# ---- rule 3: number words (digits are checked separately)
NUMBER_WORDS = [
    "zero", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
    "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety", "hundred", "hundreds", "thousand", "thousands", "million", "billion",
    "dozen", "half", "halves", "quarter", "quarters", "twice", "thrice", "third", "fourth", "fifth", "sixth",
    "seventh", "eighth", "ninth", "tenth", "percent", "per cent",
]
NUMBER_WORD_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in NUMBER_WORDS) + r")\b", re.I)
# "one" only when it counts something ("one turn", "one point", "one in ten"), not the pronoun ("the safer one")
ONE_COUNT_RE = re.compile(r"\bone\s+(?:turn|turns|point|points|percent|crit|critical|hit|hits|miss|misses|ko|game|"
                          r"more|less|extra|in)\b", re.I)
DIGIT_RE = re.compile(r"\d")

# ---- rule 4: polarity. "strong" phrases claim a direction and must bind a signed value in the sentence;
# "weak" words are checked only when a signed slot is present.
POL_STRONG_POS = [r"toward(?:s)?\s+you\b(?!r)", r"in\s+your\s+favou?r", r"helped\s+you", r"went\s+your\s+way",
                  r"\blucky\b", r"worked\s+for\s+you", r"benefit(?:ed|ted)?\s+you"]
POL_STRONG_NEG = [r"against\s+you", r"toward(?:s)?\s+(?:your|the)\s+opponent", r"in\s+(?:your|the)\s+opponent'?s\s+favou?r",
                  r"hurt\s+you", r"went\s+their\s+way", r"\bunlucky\b", r"worked\s+against\s+you"]
POL_WEAK_POS = [r"\bhelped\b", r"\bgained?\b", r"\bbenefit(?:ed|ted)?\b"]
POL_WEAK_NEG = [r"\bhurt\b", r"\bdropped\b"]  # "cost" is never said (rule 8)


def _compile(lst):
    return [re.compile(x, re.I) for x in lst]


POL = [(+1, True, r) for r in _compile(POL_STRONG_POS)] + [(-1, True, r) for r in _compile(POL_STRONG_NEG)] + \
      [(+1, False, r) for r in _compile(POL_WEAK_POS)] + [(-1, False, r) for r in _compile(POL_WEAK_NEG)]

# ---- rule 6: verdict words (and therefore their negations) only via a {verdict} slot bound to get_verdict
VERDICT_RE = re.compile(
    r"\bmisplay(?:s|ed)?\b|\bmisread(?:s|ing)?\b|\bdefensible\b|\bwithin\s+noise\b|\bmistakes?\b|\bblunder(?:s|ed)?\b"
    r"|\berrors?\b|\bwrong\b|\bcorrect(?:ly)?\b|\bright\s+call\b|\b(?:bad|good|poor|great)\s+(?:move|play|call|"
    r"decision|choice|read)\b|\binaccura(?:te|cy|cies)\b|\bsuboptimal\b|\boptimal(?:ly)?\b|\bmisclick(?:ed)?\b",
    re.I)

# ---- rule 7: probabilities below one half are stated with hedges, never with certainty
HEDGE_RE = re.compile(r"\bchances?\b|\bprobabilit(?:y|ies)\b|\bodds\b|\blikel(?:y|ihood)\b|\bunlikely\b|\bmight\b|"
                      r"\bmay\b|\bcould\b|\bestimat(?:e|ed|es)\b|\bexpected\b|\bgave\b|\bput\b|\bonly\b|\babout\b",
                      re.I)
CERTAIN_RE = re.compile(r"\bcertain(?:ly)?\b|\bdefinitely\b|\bsurely\b|\bguarantee(?:d|s)?\b|\balways\b|\bfor\s+sure\b|"
                        r"\bclearly\b|\bobviously\b|\bknew\b|\binevitabl[ey]\b|\bbound\s+to\b", re.I)
WEAK_CERTAIN_RE = re.compile(r"\bhad\b|\bwas\b|\bwere\b|\bwill\b|\bwould\b", re.I)

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
# a unit right after a placeholder is not a number word ("{x} percent", "{x}%")
UNIT_AFTER_PLACEHOLDER_RE = re.compile(r"(\{[A-Za-z_][A-Za-z0-9_]*\})\s*(?:%|percent\b|per\s+cent\b)", re.I)


def polarity_spans(text):
    """[(sign, strong, start, end, phrase)] for the polarity phrases of a sentence (longest phrases first)."""
    taken, out = [], []
    for sgn, strong, rx in POL:
        for m in rx.finditer(text):
            if any(m.start() < te and ts < m.end() for ts, te in taken):
                continue
            taken.append((m.start(), m.end()))
            out.append((sgn, strong, m.start(), m.end(), m.group(0)))
    return out


def nearest_slot(span_start, span_end, slots):
    """The slot (name, start, end, ...) nearest to a phrase; the same association for renderer and verifier."""
    def dist(x):
        return span_start - x[2] if x[2] <= span_start else (x[1] - span_end if x[1] >= span_end else 0)
    return min(slots, key=dist) if slots else None
