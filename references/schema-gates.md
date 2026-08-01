# Retired rich-result types — the gate `pagecheck.py schema` applies

A page can pass structured-data validation perfectly while every type on it is
one Google stopped rendering years ago. The validator has no opinion about
this: the markup is still valid schema.org, so `validator.schema.org` returns
zero errors and says nothing. That silence is what this table is for.

`pagecheck.py schema` reports any of these under `deprecated`, always at
**info** severity. Two reasons it is never a failure:

1. The markup is not wrong. It is valid, it is just inert for Google Search.
2. **A dead rich result is a strong reason not to ADD the type, and only
   sometimes a reason to remove it.** Other consumers — AI answer engines,
   internal tooling, non-Google search — may still read it, and none of them
   published a retirement notice. Each row carries its own guidance.

---

## Retired

| Type | Retired | What actually happened | Guidance |
|---|---|---|---|
| `FAQPage` | **2026-05-07** | Rich results fully retired for **all** sites, superseding the 2023 restriction to gov/health domains. FAQ docs removed 2026-06-15. | Not a defect. Keep it if non-SERP consumers read it; do not add it for search benefit. For genuine user-submitted Q&A use `QAPage`. |
| `HowTo` | 2023-09 | Rich result removed from desktop and mobile. | Vocabulary still valid, no SERP effect. Clear `<h2>` step headings do the comprehension work now. |
| `ClaimReview` | 2025-06-12 | Fact-check rich result retired; Google ignores the markup. Still in schema.org. | No replacement. |
| `VehicleListing` / `Vehicle` | 2025-06-12 | Dealer-inventory rich cards no longer render. | Use `Product` if the vehicle is sold online. |
| `EstimatedSalary` | 2025-06-12 | Salary rich result retired. | `JobPosting` with `baseSalary` still works for specific roles. |
| `OccupationalAggregateRating` | 2025-06-12 | Retired alongside `EstimatedSalary`. | None. |
| `SpecialAnnouncement` | 2025-07-31 | The COVID-era emergency card was deprecated. | `Event` if time-bounded, otherwise `Article`/`WebPage`. |
| `LearningVideo` | 2025-06-12 | Retired. | `VideoObject` still renders. |
| `PracticeProblem` | 2026-01 | Deprecation notice 2025-11-05; Rich Results Test, Search Console rich-result reporting and the appearance filter all dropped support from January 2026. | None. |
| `Course` **carousel** | 2025-06-12 | Only the carousel variant retired. | The single-result `Course` rich card is **still live** — check which one is meant before advising. |

## Explicitly NOT retired — the one people remove by mistake

| Type | Status |
|---|---|
| `Dataset` | **Not discontinued.** Consumed by Dataset Search (live), just not by Google Search rich results — clarified 2025-11-05. Do not advise removing it. |

## Tooling

For `CourseInfo`, `EstimatedSalary`, `LearningVideo`, `SpecialAnnouncement` and
`VehicleListing` the type documentation itself was removed on **2025-09-09**.
Do not send anyone to the Rich Results Test for these or expect Search Console
reporting — those surfaces no longer cover them.

---

## Primary sources

- Simplifying our Search rich results (June 2025 retirements) —
  https://developers.google.com/search/blog/2025/06/simplifying-search-results
- HowTo / FAQ changes (2023) —
  https://developers.google.com/search/blog/2023/08/howto-faq-changes
- FAQPage rich-result retirement (2026-05-07) —
  https://developers.google.com/search/docs/appearance/structured-data/faqpage

## Currency

These dates were compiled on 2026-08-01 from the sources above and have **not**
all been independently re-fetched since. Before telling anyone to change markup
on the strength of a row, open the source link — the whole point of the
`guidance` column is that removal is usually the wrong reflex, and a stale row
is exactly how a wrong reflex gets automated.
