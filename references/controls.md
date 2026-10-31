# Controls: why a negative result needs one

Moved from SKILL.md's Non-negotiables on 2026-10-24 so it stops loading on every
trigger; the rule is unchanged. Read this before writing a new probe, before
reporting any absence, or when `controls.py audit` reports `ok: false`.

---

## The incidents

Twice on the day these tools were built, the instrument was broken and the
finding was pure artefact: `socket.gethostbyaddr` reported **every** Googlebot IP
as spoofed because this container's DNS silently refuses reverse lookups, and a
Common Crawl index answered `504` where an absent domain answers `404`. Both
would have shipped as confident conclusions. `crawllog.py verify` and
`backlinks.py footprint` now refuse to return a verdict when their control
fails, and any new probe must do the same. **"Cannot ask" and "the answer is
no" must never share a code path.** `providers.py` enforces this structurally:
a probe whose control fails is reported `control_failed` and treated as
**unusable**, not as quiet. Two more instruments were caught broken by their own
controls while this was being built — a schema parser that read the wrong key
and so reported every page as having no structured data, and a crt.sh probe
aimed at a domain with no certificates. Both would have shipped as confident
findings about the web rather than bugs in the reader.

---

## It is structural, not a habit

`controls.py` provides the primitive (`Controls`, `refuse()`, `guard_zero()`,
`uniform_verdict()`) and **every one of the 37 instruments carries a control you
can run** — `control` as a subcommand, or `--control` on the five flag-style
ones. `controls.py audit` runs the lot (828 checks; a live source that refuses is
`unreachable`, never `broken`) and reports `ok: false` naming any instrument that
cannot currently prove itself. It was built after seven instruments failed their
controls in a single run on 2026-09-01; each would have shipped as a confident
finding about the site.

Two shapes of failure it now catches by construction:

- **An empty control set is not a pass.** A tool that declares a control and
  registers no checks reads exactly like a healthy one.
- **A uniform verdict across a population IS the tell.** `slop.py scan` returned
  `warn` for 44 of 44 pages, and every individual verdict was plausible; only
  the uniformity showed it was measuring the page TEMPLATE rather than the
  prose. `uniform_verdict()` reports that suspicion with its own falsifier
  attached, and `slop.py corpus` now emits it.

---

## Writing a control

⚠ **A control that agrees with the code proves nothing.** Two written during
this pass had to be corrected because they asserted a value copied out of the
implementation's own docstring, and one because its robots.txt fixture put an
"orphan" directive where it was a legitimate continuation. Derive the expected
value independently, or the control is a mirror.
