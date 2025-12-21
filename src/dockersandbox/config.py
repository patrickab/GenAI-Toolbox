import os

PROJECT_ROOT = os.path.abspath(".")
DOCKERFILES_PATH = os.path.join(PROJECT_ROOT, "src", "dockersandbox", "dockerfiles")
DOCKERFILES_PYTHON_VERSION = "3.11-slim"

DOCKERTAG_BASE = "sandbox-base"
DOCKERTAG_AIDER = "sandbox-aider"
UNPRIVILEGED_USERNAME = "sandbox_user"

DOCKERFILE_BASE = f"""
FROM python:{DOCKERFILES_PYTHON_VERSION}

# System Layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

# Tooling Layer (uv)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Security Layer (Non-root)
RUN useradd -m -u 1000 {UNPRIVILEGED_USERNAME}
WORKDIR /workspace
RUN chown {UNPRIVILEGED_USERNAME}:{UNPRIVILEGED_USERNAME} /workspace

# Environment
ENV UV_SYSTEM_PYTHON=1
ENV PATH="/home/{UNPRIVILEGED_USERNAME}/.local/bin:$PATH"
"""

DOCKERFILE_DEFINITIONS = {
    f"{DOCKERTAG_AIDER}": f"""
FROM {DOCKERTAG_BASE}:latest
RUN uv pip install aider-chat
# Switch to non-root after installation
USER {UNPRIVILEGED_USERNAME}
""",
}
