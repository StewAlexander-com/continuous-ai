<picture>
  <source media="(max-width: 600px)" srcset="docs/readme-assets/hero-mobile.svg">
  <img src="docs/readme-assets/hero.svg" alt="Aida — A conversation you can build on. Remember useful context. Correct what changes. Keep memory on your computer." width="1000">
</picture>

# Aida · Continuous-AI

**A local AI assistant with memory you can inspect and correct.**

You should not have to explain the same preferences every time you start a conversation. Aida can keep useful context between sessions, accept corrections, and work with files you choose to share. A local language model generates replies on your computer.

**Aida** is the assistant. **Continuous-AI** is the project that provides its memory, reply checks, and chat interface.

[See how it works](#a-conversation-that-can-continue) · [Try it](#try-aida) · [Privacy](#what-stays-on-your-computer) · [Evidence](#what-the-tests-show) · [Website](https://www.honest-aida.ai/)

**Aida is experimental.** You use it through a terminal, an app where you type commands. Getting started takes some installation and setup. Aida checks for errors.

## A conversation that can continue<a name="what-aida-does"></a><a name="architecture"></a>

<picture>
  <source media="(max-width: 600px)" srcset="docs/readme-assets/memory-story-mobile.svg">
  <img src="docs/readme-assets/memory-story.svg" alt="Tell Aida something worth remembering. Inspect or correct what it saved. Return in a later session with that context available. This is an illustrated workflow, not a recorded conversation." width="1000">
</picture>

| What you want | What Aida provides |
| --- | --- |
| Less repetition | Saved facts and preferences that can carry into later sessions |
| A way to fix mistakes | Plain-language corrections and commands to inspect saved memory |
| Help with your own files | Support for text, PDF, and DOCX files you choose to attach |
| A way to question saved conclusions | Records that can explain a conclusion, objections to it, and reasons to reconsider it |
| Chat on your own computer | Local models for replies and background review by default |

Aida saves information for later conversations without retraining the model. Model training is available as a separate advanced feature.

### See a real session<a name="mutualism--the-third-option"></a>

<details>
<summary><strong>Watch Aida remember something between sessions</strong></summary>

[![Recorded terminal session: a user asks Aida to remember a location, closes the session, and asks about it again after context is restored.](docs/assets/demo.gif)](docs/assets/demo.gif)

This recording shows Aida saving a fact and recalling it after a restart, using `llama3.2` and a temporary database. Open it at full size to read the terminal text. The recording predates some current features. The [demo script](scripts/record_demo.py) now includes additional steps, including a correction, that are not shown here.

</details>

**Aida is built around mutualism:** you contribute context and judgment, and Aida contributes suggestions you can question and correct. It can also ask for your help when a conversation gets difficult. The aim is for both contributions to improve the work. That benefit has not yet been measured, and the design makes no claim about AI consciousness.

## Try Aida<a name="quickstart"></a><a name="platform-support"></a>

You need **Python 3.11–3.13**, [Ollama](https://ollama.com/download), and a terminal. Ollama runs language models on your computer. Development happens mainly on Apple Silicon Macs. Linux and Windows are also supported. On Windows, run the shell scripts through WSL or Git Bash.

**Before installing:** setup downloads dependencies and the configured model. The current default chat model is **`qwen3:30b-a3b`**, with **`gemma3:4b`** for background review. These models need substantial memory and storage. Speed depends on your hardware. For a smaller first run, use the [lightweight setup](docs/readme-guide.md#start-with-a-smaller-model) before running the default installer.

Install and start Ollama, then run these commands. The separate `ollama pull` line installs the default review model:

```bash
git clone https://github.com/StewAlexander-com/continuous-ai.git
cd continuous-ai
bash setup.sh
ollama pull gemma3:4b
bash run.sh
```

When chat opens, try these one line at a time:

```text
Remember that I prefer short explanations with one example.
Explain what a local AI model is.
:help
exit
```

Restart with `bash run.sh` and ask Aida what preference it remembers. After exiting, run `bash run.sh forget` to list saved facts about you. Despite the name, this command only lists those facts unless you specify which one to delete. Aida may still miss a preference in its replies.

<a name="commands"></a>

<details>
<summary><strong>A few useful commands</strong></summary>

| In the conversation | Purpose |
| --- | --- |
| `:help` | List available commands |
| `:setup` | Check model and file-reader status |
| `:read ~/notes.txt` | Attach a local file you choose |
| `:more` | Read the next section of a long attachment |
| `:why` | Inspect saved model conclusions, when available |
| `:theme dark` | Change the terminal appearance |
| `exit` | End the session |

To correct a saved fact, tell Aida what is wrong and what it should remember instead. If it shows a numbered list, choose the fact you want to correct. See the [practical guide](docs/readme-guide.md) for more on setup, memory, voice, and commands.

</details>

## What stays on your computer<a name="configuration"></a>

<picture>
  <source media="(max-width: 600px)" srcset="docs/readme-assets/local-flow-mobile.svg">
  <img src="docs/readme-assets/local-flow.svg" alt="Default path: your messages and selected files go to Aida and a local model, with memory stored on your computer. Setup downloads require internet access. An optional Perplexity critic sends the question and answer to that external service if enabled." width="1000">
</picture>

By default, chat, saved memory, and background reply checks stay on your computer after setup. Downloads, updates, and optional voice setup can use the internet. Protect saved conversations and backups as you would other personal files.

**Cloud review is optional and off by default.** If you enable the Perplexity critic, Aida sends your question and its reply to Perplexity for review. The chat model still runs locally: Aida rejects connections to model servers on other machines.

Aida does not browse websites. For a web page, paste the relevant text or save it locally and attach the file. Aida blocks requests it recognizes as asking it to read a URL. That check does not catch every unsupported claim.

## What the tests show<a name="does-it-actually-work"></a><a name="why-its-different"></a>

In an earlier test, a small model invented the contents of web pages it could not open, even when instructed not to. The chart shows how often it correctly refused those requests.

<picture>
  <source media="(max-width: 600px)" srcset="docs/readme-assets/url-probe-mobile.png">
  <img src="docs/readme-assets/url-probe.png" alt="Historical prompt-only test on llama3.2: correct refusals were 21 of 24 for a placeholder link, 8 of 24 for a real README link, and 4 of 24 for a real source-code link. These are earlier project results, not current overall accuracy scores." width="900">
</picture>

Those results led to a check in the code that blocks recognized requests to read a URL before they reach the model. It passes all **24 request combinations** in the automated test. This tests the URL check, not the accuracy of other replies.

An earlier nine-case test reported **20% invented-answer failures without guard instructions and 0% with them** across five runs of `llama3.2`. The harder link tests above show why **0% on a small test does not mean “never wrong.”** It is not yet clear whether saving model conclusions improves later answers. [Evidence, methods, and limits](docs/readme-evidence.md).

## Explore further<a name="recent"></a><a name="deep-dives"></a><a name="contributing"></a>

| If you want to… | Start here |
| --- | --- |
| Set up a smaller model or find a command | [Practical guide](docs/readme-guide.md) |
| Understand what is saved and how it changes | [Memory design](docs/design/memory-layering.md) · [Correction design](docs/corrigibility.md) |
| Inspect tests and their limits | [Evidence guide](docs/readme-evidence.md) |
| Follow development | [Releases](https://github.com/StewAlexander-com/continuous-ai/releases) |
| Report an issue or contribute | [Contributing](CONTRIBUTING.md) · [Issues](https://github.com/StewAlexander-com/continuous-ai/issues) |

## License and author<a name="license"></a>

[MIT License](LICENSE) · Copyright © 2026 Stewart Alexander.

Created by [Stewart Alexander](https://www.stewalexander.com/). Project website: [honest-aida.ai](https://www.honest-aida.ai/).
