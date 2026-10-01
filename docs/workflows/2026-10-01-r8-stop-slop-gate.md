# R8 Stop-Slop Copy Gate

Owner requirement: run the installed `stop-slop` skill anywhere it materially applies to human-facing prose, with one bounded pass per completed text surface and one final cross-surface consistency pass. Do not repeatedly rewrite unchanged copy.

## Mandatory surfaces
- Public growth/SEO site: hero, section copy, CTAs, feature explanations, viewer/streamer pages, help/FAQ, pricing/plan wording if present, error/empty states, accessibility labels that are prose.
- SEO prose: title/description where naturalness matters, page introductions, article/help copy, image alt text when descriptive, schema descriptions/FAQ answers. Preserve factual search intent, required keywords, canonical/technical metadata, and valid structured-data syntax.
- Telegram Mini App, streamer cabinet and owner panel: visible headings, descriptions, buttons where wording is prose, empty/error/success messages, onboarding/help.
- Telegram bot: user-facing replies, command descriptions, notifications and setup/help text changed by this roadmap. Preserve variables, placeholders, Telegram formatting and factual meaning.
- Remotion: storyboard, on-screen text, captions, voice-over/script and marketing descriptions.
- Human-facing README/help/runbook sections intended for users/operators. Internal terse engineering logs, code comments and test fixture strings only when they are actual product copy.

## Pass rules
1. Read `.agents/skills/stop-slop/SKILL.md` (or the verified installed global copy if project-local copy is absent) before the pass.
2. Never alter claims, prices, product availability, legal meaning, identifiers, URLs, code, SQL, JSON keys or test semantics just to improve style.
3. SEO wins do not come from keyword stuffing. Keep the user's actual query language where it reads naturally; do not replace precise terms with vague synonyms merely to satisfy a style score.
4. Preserve established product voice across Russian and English. Translate meaning, not sentence shape.
5. Score revised prose using the skill rubric. Below 35/50 requires one revision. Do not loop endlessly above the threshold.
6. After changing product copy, run the affected tests/snapshots and inspect the rendered surface where practical. Stop-Slop is a prose review, not functional or visual QA.
7. At R8 final acceptance run one repository-scoped inventory of human-facing copy and report reviewed surfaces plus explicit exclusions. Do not claim `stop-slop everywhere` without this evidence.
