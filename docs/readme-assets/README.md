# README visual sources

- `hero*.svg`: original editorial artwork. Navy, plum, amber, and ivory come from Aida's existing website palette; serif headings and restrained rules connect it to Stewart Alexander's wider site identity. The connecting lines are illustrative, not a model architecture or a performance graph.
- `memory-story*.svg`: illustrated tell / review / return workflow. It is not a recorded conversation.
- `local-flow*.svg`: schematic of the default local configuration and the optional Perplexity critic. It is based on the tracked `config.yaml`, `llm.py`, `critic.py`, and storage code.
- `url-probe*.png`: Matplotlib charts of previously reported counts from [v2.15.9](../../RELEASE_NOTES_2.15.9.md). They are not product screenshots or new benchmark runs. [Data](url-probe.csv), [generator](generate-evidence.py), and [interpretation](../readme-evidence.md).

Each illustration and chart has a separate phone composition. SVGs are self-contained, use system fonts, and include titles and descriptions. Essential claims are also present as text and image descriptions in the README.

To regenerate the historical charts in an environment with Matplotlib installed:

```bash
python docs/readme-assets/generate-evidence.py
```

The existing `docs/assets/demo.gif` is reused without alteration. It shows the older memory-only recording, not all steps in today's recorder. [Recording limits](../readme-evidence.md#memory-correction-and-quality).
