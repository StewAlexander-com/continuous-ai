# Aida evidence and limits

[← Back to the README](../README.md)

This page separates previously reported model experiments from checks rerun during the README update. None of the figures measures overall accuracy, certifies safety for a particular profession, or proves that Aida cannot invent an answer.

## A harder question changed the result

The historical URL-reading probe asked `llama3.2` about pages it could not access. With the project's prompt guards enabled, it correctly declined to describe those pages at the following rates:

| URL type | Correct refusals |
| --- | --- |
| Placeholder link | 21 / 24, about 88% |
| Real GitHub README link | 8 / 24, about 33% |
| Real source-code link | 4 / 24, about 17% |

These are the chart's three data points. Each URL type used eight phrasings with three runs per phrasing. The full experiment included guards-on and guards-off conditions, totaling 144 calls. Pooled correct refusal was reported as 46% with guards and 24% without. The percentages are rounded; the figure plots the exact fractions.

**Source:** [v2.15.9 release notes](../RELEASE_NOTES_2.15.9.md). **Instrument:** [probe_url_refusal.py](../probe_url_refusal.py). The measurements were previously reported by the project, on one model and one machine; they were not rerun for this README. The chart was made from the reported counts, not a screenshot of a product dashboard.

The result motivated a runtime check in [session.py](../session.py): recognized requests for a URL's contents are refused before model generation. Mentions of a URL, pasted content, and local file paths are handled separately. This deliberately limited pattern matching is not a proof that every paraphrase or indirect request will be intercepted.

## The earlier 0% result

The previous README reported five runs of a nine-case battery: `llama3.2` averaged 20.0% confabulation with guards off (range 0–44%) and 0.0% with guards on; `qwen2.5:14b` with guards also reported 0.0%.

“Confabulation” here refers to invented or unsupported answers as scored by that small battery. The result does not mean 0% of all possible answers are wrong. The harder URL probe above exposed failures that the easier test did not reveal. The archived [README at the documentation baseline](https://github.com/StewAlexander-com/continuous-ai/blob/cdab3ed037af1d4156457e13e8cf20cc0be1a9ab/README.md#does-it-actually-work) retains the original summary and qualifications.

Related files: [eval_confabulation.py](../eval_confabulation.py), [eval_battery.py](../eval_battery.py), and [scorer tests](../test_eval_confab.py). These historical model measurements were not independently regenerated in this documentation pass.

## Checks rerun for this README

On October 16, 2026, the existing deterministic tests were run from an isolated copy of the tracked source with Python 3.12.14, using no live model:

- `test_offline_url_gate.py`: **8 / 8 checks passed**, including refusal of the 24 measured URL/phrasing combinations and tests for legitimate pass-through cases.
- `test_corrigibility.py`: **20 / 20 checks passed**, covering correction metadata, old records, specific reconsideration conditions, and command routing.

These tests exercise code behavior; their success is not a model-quality score. They do not constitute a rerun of the historical 144-call probe or the full test suite.

After installing dependencies, reproduce those checks from the repository root:

```bash
.venv/bin/python test_offline_url_gate.py
.venv/bin/python test_corrigibility.py
```

For model-backed checks, the project also provides:

```bash
bash run.sh smoke
bash run.sh confab-eval
```

These require a running local model and may take time. `confab-eval` runs the smaller battery; inspect the harder probe separately rather than treating one result as comprehensive coverage.

## Memory, correction, and quality

The existing [demo GIF](assets/demo.gif) records a memory-only example: teach a location, close the session, and restore context. It predates the additional steps in the [current demo recorder](../scripts/record_demo.py). It is not evidence that every correction or answer is accurate.

A prior [style experiment](design/l3-eval-results.md) compared two saved-context states over 16 paired answers and found more of the targeted writing-style markers. Its own limitations state that a blind judgment of answer quality was not run. More stylistic conformity is not evidence of more correct or useful answers.

The model-derived saved-conclusion layer remains an experimental design. Its persistence and inspection mechanisms can be tested, while a controlled outcome benefit remains unestablished. The same distinction applies to mutualism: it describes the project's intended relationship, not a measured psychological property of the software.
