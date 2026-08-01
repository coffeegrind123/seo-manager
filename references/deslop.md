# AI writing tells — the catalog behind `slop.py`

The sameness gate catches a draft too similar to the corpus. This catches a
draft that reads as **machine-written** even when it is entirely unique. Same
root cause, different symptom, and the one a model cannot self-assess: these
are precisely the patterns it reaches for without noticing.

Run it, do not just read it:

```bash
SEO=~/.claude/skills/seo-manager/scripts
python3 $SEO/slop.py scan draft.md          # located hits, with the fix for each
python3 $SEO/slop.py diff old.md new.md     # did the rewrite actually remove them
python3 $SEO/slop.py rules                  # the catalog, with tolerances
```

**Why a script and not a checklist.** Every other SEO tool ships these as prose
for the model to bear in mind while writing. A model bearing its own tells in
mind is the same instrument grading itself, and it scores well. A regex does
not have that problem: `binary_contrast` either matched on line 42 or it did
not, and the answer is the same on every run.

---

## How to read the output

**`flagged`** is the work list — rules that exceeded their tolerance.
**`within_tolerance`** is shown too, so a rule that fired once is visible
without being treated as a defect. Several of these patterns are perfectly good
*once*: one binary contrast is rhetoric, ten is a fingerprint. That is what
`tolerance` encodes.

**No score is emitted, on purpose.** A "slop score" gets optimised instead of
the prose. The output is located hits and the reason each is a tell; the rewrite
is a human act performed with that list open.

**Code is excluded.** Fenced blocks, inline code, indented blocks and link
targets are masked before scanning — a `--leverage` CLI flag or a URL containing
`landscape` is not a writing tell, and flagging it teaches the writer to ignore
the report. Line numbers survive the masking.

---

## The rules that matter most

**`binary_contrast` — "It's not X. It's Y."** The single most-cited AI tell, and
the highest-risk rule in the catalog in both directions. Before LLMs people did
not write this way at scale. The fix is always the same: **state Y and drop the
negation.** Both sides are pinned in `test_slop.py` — six genuine forms must
match, six ordinary negations ("The engine is not documented. Its behaviour is
odd.") must not, because a false positive here would get the whole tool ignored.

**`vague_attribution` — "Experts say…"** This one fails two gates at once. It is
a writing tell *and* a straight breach of the information-gain requirement: a
citation-shaped phrase with no citation in it. If you cannot name the source,
you do not have one. Delete the claim or go and find it.

**`ai_vocabulary`** — delve, tapestry, leverage (verb), utilize, streamline,
robust, harness, ever-evolving, cutting-edge. Plain words exist: use, simplify,
strong, field.

**`throat_clearing`** — "Here's the thing:", "The truth is,", "It turns out".
Any "here's what/why/where" construction is a runway before the point. Cut the
runway.

**`stakes_inflation`** — "revolutionises", "transforms the way", "a new era".
Grandiosity standing where a measurement belongs. Replace it with the number.

**`bold_first_bullet`** — every list item opening with a bolded keyword. A
strong formatting tell, and usually a sign the list wanted to be a table or a
paragraph.

---

## The principles underneath

1. **Cut filler** — throat-clearing openers, emphasis crutches ("Let that sink
   in"), pedagogical hand-holding ("Let's break this down"), meta-commentary.
2. **Break formulaic structures** — binary contrasts, negative listings
   ("Not a bug. Not a feature. A design flaw."), dramatic fragmentation, self-posed
   rhetorical questions, anaphora.
3. **Active voice with human subjects.** "The team fixed it", not "The complaint
   becomes a fix".
4. **Be specific.** No vague declaratives, no absolutes doing vague work, no
   unnamed experts.
5. **Vary rhythm.** Mix sentence lengths. Two items often beat three.
6. **Trust the reader.** No fractal summaries — telling them what you will say,
   saying it, then summarising it.
7. **One point per section.** Do not restate the same argument ten ways.

Domain terminology is not slop. "Weighted interval score" is precise language.
The target is business buzzwords and AI vocabulary leaking into technical prose,
not technical prose itself.

---

## Where it runs

In the **build-guide** workflow, after the draft and before the sameness gate —
a rewrite for slop changes the text, so running sameness first wastes the check.
Unlike the sameness gate, this one is **advisory**: it does not block a ship on
its own. It is a work list, and a `fail` verdict on a draft nobody has revised
is expected rather than alarming.

It is also worth running over **generated** copy. A template that emits the same
tell on every page emits it thousands of times, and that is a corpus-level
signal no per-page review would ever surface.

