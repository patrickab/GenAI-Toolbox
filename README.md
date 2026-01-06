# GenAI Swiss Army Knife

A collection of GenAI utilities for personal use. Created to make my workflows easier & more efficient.

## Core Features
- Customizable library of system prompts for recurring tasks.
- Unified access to 2100+ models from 100+ providers using [LiteLLM](https://models.litellm.ai/) 
- CPU/GPU local inference using [vLLM](https://docs.vllm.ai/en/latest/) and [Ollama](https://ollama.com/).
- Automatic VRAM ressource & localhost server management.
- Retrieval Augmented Generation (RAG) chatbot.
- RAG data pipeline. 
  - Visual Language Model (VLM) PDF-2-Markdown conversion using [MinerU](https://github.com/opendatalab/MinerU)
  - Markdown Post-Processing using RegEx & LLMs to guarantee RAG-compatible format.
  - Ingestion of RAG payloads into a lightweight RAG Database [see here](https://github.com/patrickab/rag-database/tree/main).
- Optical Character Recognition (OCR) for Screenshot-to-LaTeX extraction - including web-based editor.

## Further Features
- Token-efficient image compression for fast local inference.
- Store responses directly into [Obsidian](https://obsidian.md/) vault (with automatically generated YAML-headers).
- Chat History Management & Storage on local disk.

## Demo

https://github.com/user-attachments/assets/c21ec4b0-2c92-419c-a46e-36453efce309

## Installation & Setup
- Clone the repo.
- Store your `OPENAI_API_KEY` or `GEMINI_API_KEY` as environment variable  (e.g. in `~/.bashrc`).
- Create a virtual environment using `uv sync`.
- Activate your virtual environment using `source .venv/bin/activate`
- Use `./run.sh` to start the application

