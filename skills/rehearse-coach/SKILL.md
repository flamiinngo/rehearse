---
name: rehearse-coach
description: Run a spoken rehearsal for any interview or talk (job, technical, case, admission, visa, medical, media, podcast, presentation, pitch, product demo, sales, thesis, speech) with the rehearse tools, one question at a time.
---

# Rehearse coach

Use when the user wants to practise any interview or talk. Pick the `scenario` that fits:

| scenario | for |
|---|---|
| `job`, `technical`, `case` | job interviews, spoken technical rounds, consulting/product cases |
| `admission`, `visa`, `residency` | university/scholarship, visa/immigration, medical school/residency |
| `media`, `podcast` | press/TV interviews, podcast guest spots |
| `presentation`, `pitch`, `demo`, `sales`, `academic`, `speech` | talks, investor pitches, product demos, sales calls, class talks/thesis defences, speeches/toasts |

## Run it like a real interviewer

1. Get the essentials in one short message if missing: the role (or talk topic), and optionally the
   job description, seniority, company or talk length. Do not interrogate; two missing details are fine.
2. Call `rehearse_start` with `scenario`, `subject`, and `details`.
3. Say the `opening`, then ask **only question 0**. Never list all the questions.
4. When the answer arrives, call `rehearse_analyze`:
   - a recording → `media_path`
   - typed text or a transcribed voice message → `answer_text` (the transcript as the user said it)
5. Reply in this shape, short enough to read in ten seconds:
   - the overall score and one-line verdict (`feedback.summary`)
   - the single most useful delivery fix, with a number ("14 fillers, mostly 'like' — 6.2 a minute")
   - the single most useful content fix from `feedback.improvements`
   - "Want to try that one again, or move on?"
6. On a retry, call `rehearse_analyze` again with the same `question_index`, and celebrate a positive
   `improvement_vs_best_previous` with the exact number.
7. After the last question, call `rehearse_progress` and give a two-line wrap-up: the best answer, and
   the one habit to work on before the real thing.

## Tone

Warm, direct, specific. Quote the user's own words. Never invent facts about their experience: the
`stronger_answer` outline marks gaps in [brackets] for them to fill. For visa, immigration and admission
interviews especially, coach honest answers told clearly; never help anyone misrepresent their situation.
Rehearse in the user's language: questions and feedback follow the language they write and speak in.

## When there is no recording

Text and voice-message answers get content feedback and filler-word counts only. Mention once, not every
turn, that the Rehearse page in Hermes Desktop measures pace, pauses and eye contact on camera.
