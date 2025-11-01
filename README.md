# Study Assistant

A study assistant chatbot with flexible, customizable behavior.
Use your own chatbot on localhost via API-Calls.
Permanently adjust knowledge level to your background - no more answers, that are too complex/trivial
The sidebar allows you to select pre-defined system prompts and swap models on the fly, giving you fine-grained control over the assistant.

Features
- Clean markdown/LaTeX formatting.
- Adjust the length, complexity of the answer.
- Manage your own prompt library & flexibly adjust chatbot behavior as you talk.
- Store responses from the website directly in your [Obsidian](https://obsidian.com) vault
- Cloud-sync integration with Obsidian.

Work in Progress
- Explore repositories visually and enrich your LLM-queries with context-aware code snippets by using Retrieval Augmented Generation (RAG).
- Keep your conversations tidy with a compact, expandable history
- Flexibly finetune LLM-behavior for brainstorming, generation of markdown learning material (markdown & Jupyter notebook) & explanations directly in your browser.

## Development Setup
- Store your `OPENAI_API_KEY` or `GEMINI_API_KEY` as environment variable  (e.g. in `~/.bashrc`).
- Create a virtual environment using `uv sync`.
- Activate your virtual environment using `source .venv/bin/activate`
- Use `./run.sh` to start the application
