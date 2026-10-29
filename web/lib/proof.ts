// Numbers for the proof screen, copied from the eval reports. The cloud cost is not here: it is read from the hero
// campaign's own bundle at runtime.

/** evals/REPORT.md (2026-10-29T16:17, `python -m evals.run` on Linux): Translucent 38/38, plain pipeline 1/38. */
export const FAULTS = { planted: 38, caught: 38, plainCaught: 1 };

/**
 * evals/CRITIC.md + evals/results/critic-real-20261028.json: Muse Spark Open, 16 cases x 3 repeats = 48 real runs;
 * 33 of those runs showed it a wrong or unreadable finding, and it agreed with 0 of them (false_agree 0.0).
 */
export const SECOND_OPINION = { wrongShown: 33, agreed: 0, runs: 48 };

/**
 * evals/LLM_JUDGE.md + evals/results/llm-judge-real-20261029.json: Muse Spark Open asked to double-check each finding
 * the planted mistakes produced, 3 repeats, 120 real calls. Given the finding and its numbers it let 5 of 18 through;
 * given the full evidence record it caught all 18.
 */
export const DOUBLE_CHECK = { judged: 18, letThrough: 5, fullCaught: 18 };
