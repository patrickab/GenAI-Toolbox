import os
import subprocess
import tarfile
import tempfile
import time
from typing import List, Optional

import docker
from docker.errors import NotFound


class DockerExecutionLogs:
    def __init__(self, stdout: str = "", stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr


class DockerExecutionResult:
    def __init__(self, png: Optional[bytes] = None) -> None:
        self.png = png


class DockerExecution:
    def __init__(self, logs: DockerExecutionLogs, results: List[DockerExecutionResult]) -> None:
        self.logs = logs
        self.results = results


class DockerProcess:
    def __init__(self, stdout: str = "", stderr: str = "", exit_code: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class _DockerFiles:
    def __init__(self, sandbox_instance: "DockerSandbox") -> None:
        self._sandbox = sandbox_instance

    def read(self, path: str) -> str:
        cmd = f"cat {path}"
        proc = self._sandbox._run_in_container(cmd)
        if proc.exit_code != 0:
            raise Exception(f"Failed to read file {path}: {proc.stderr}")
        return proc.stdout

    def write(self, path: str, content: str) -> None:
        if not self._sandbox.container_id:
            raise RuntimeError("Docker sandbox container is not running.")

        container = self._sandbox.docker_client.containers.get(self._sandbox.container_id)
        container_dir = os.path.dirname(path)
        container_filename = os.path.basename(path)

        if container_dir and container_dir != "/":
            mkdir_proc = self._sandbox._run_in_container(f"mkdir -p {container_dir}")
            if mkdir_proc.exit_code != 0:
                raise Exception(f"Failed to create directory {container_dir}: {mkdir_proc.stderr}")

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_file_path = os.path.join(temp_dir, container_filename)
                with open(temp_file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                tar_path = os.path.join(temp_dir, "temp.tar")
                with tarfile.open(tar_path, "w") as tar:
                    tar.add(temp_file_path, arcname=container_filename)
                with open(tar_path, "rb") as f:
                    tar_data = f.read()
                target_path = container_dir if container_dir else "/"
                success = container.put_archive(target_path, tar_data)
                if not success:
                    raise Exception(f"Docker put_archive failed to write file to {path}.")
        except Exception as e:
            raise Exception(f"Docker error during file write: {e}")


class _DockerCommands:
    def __init__(self, sandbox_instance: "DockerSandbox") -> None:
        self._sandbox = sandbox_instance

    def run(self, command: str) -> DockerProcess:
        return self._sandbox._run_in_container(command)


# ---------------------------------------------------------
# Main Sandbox Class
# ---------------------------------------------------------


class DockerSandbox:
    def __init__(self, repo_url: Optional[str] = None, branch: str = "main", image_name: str = "agent-worker-uv") -> None:
        self.docker_client = docker.from_env()
        self.image_name = image_name
        self.container_id: Optional[str] = None
        self.container_name = f"aci-sandbox-{os.urandom(4).hex()}"

        # 1. Prepare Host Workspace
        # We clone the repo to the HOST first.
        # This solves authentication and allows you to see changes locally.
        self.host_workspace_root = os.path.abspath(os.path.expanduser("~/agent_workspaces"))
        self.repo_name = repo_url.split("/")[-1].replace(".git", "") if repo_url else "scratchpad"
        self.host_repo_path = os.path.join(self.host_workspace_root, self.repo_name)

        if repo_url:
            self._prepare_repository(repo_url, branch)
        else:
            os.makedirs(self.host_repo_path, exist_ok=True)

        self.files = _DockerFiles(self)
        self.commands = _DockerCommands(self)

        self._ensure_image_and_container()

        # 2. Install Dependencies (if repo has requirements.txt)
        if repo_url:
            self._install_dependencies()

    def _prepare_repository(self, repo_url: str, branch: str) -> None:
        """Clones or pulls the repository on the HOST machine."""
        print(f"📂 Preparing repository: {self.repo_name} ({branch})...")

        if os.path.exists(self.host_repo_path):
            # Simple check: if it exists, we assume it's valid.
            # In production, you might want to git pull here.
            print(f"   -> Found existing at {self.host_repo_path}")
        else:
            print(f"   -> Cloning from {repo_url}...")
            try:
                subprocess.run(["git", "clone", "-b", branch, repo_url, self.host_repo_path], check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                raise Exception(f"Git clone failed: {e.stderr.decode()}")

    def _ensure_image_and_container(self) -> None:
        # 1. Build Image with UV (Fastest Installer)
        try:
            self.docker_client.images.get(self.image_name)
        except docker.errors.ImageNotFound:
            print(f"🏗️  Building optimized image '{self.image_name}'...")
            dockerfile_content = """
FROM python:3.11-slim

# 1. Install uv (The fastest package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# 2. Configure uv globals
ENV UV_CACHE_DIR="/uv_cache"
ENV UV_SYSTEM_PYTHON=1

# 3. Install System Tools
RUN apt-get update && apt-get install -y git curl grep procps build-essential && rm -rf /var/lib/apt/lists/*

# 4. Install Basic Agent Deps
RUN uv pip install requests openai pydantic

WORKDIR /app/workspace
CMD ["tail", "-f", "/dev/null"]
"""
            with tempfile.TemporaryDirectory() as temp_dir:
                with open(os.path.join(temp_dir, "Dockerfile"), "w") as f:
                    f.write(dockerfile_content)
                self.docker_client.images.build(path=temp_dir, tag=self.image_name, rm=True)

        # 2. Ensure Cache Volume Exists
        try:
            self.docker_client.volumes.get("agent_uv_cache")
        except NotFound:
            self.docker_client.volumes.create("agent_uv_cache")

        # 3. Start Container
        try:
            container = self.docker_client.containers.get(self.container_name)
            if container.status != "running":
                container.start()
            self.container_id = container.id
        except NotFound:
            print(f"🚀 Starting container '{self.container_name}'...")

            container = self.docker_client.containers.run(
                self.image_name,
                name=self.container_name,
                detach=True,
                # Networking: Allow access to Host LLM
                extra_hosts={"host.docker.internal": "host-gateway"},
                environment={
                    "LLM_API_BASE": "http://host.docker.internal:4000",
                },
                volumes={
                    # A. Mount the Code (Read/Write)
                    self.host_repo_path: {"bind": "/app/workspace", "mode": "rw"},
                    # B. Mount the Cache (Speed)
                    "agent_uv_cache": {"bind": "/uv_cache", "mode": "rw"},
                    # C. Dummy Mount for Venv (Safety)
                    # Hides the host's .venv/venv folder from the container to prevent crashes
                    "/dummy_venv_mount": {"bind": "/app/workspace/.venv", "mode": "rw"},
                    "/dummy_venv_mount_2": {"bind": "/app/workspace/venv", "mode": "rw"},
                },
                remove=False,
            )
            self.container_id = container.id
            time.sleep(1)  # Warmup

    def _install_dependencies(self) -> None:
        """Checks for requirements.txt and installs using uv."""
        req_path = os.path.join(self.host_repo_path, "requirements.txt")
        if os.path.exists(req_path):
            print("📦 Installing dependencies with uv (Cached)...")
            # We use --system because the container IS the venv
            proc = self._run_in_container("uv pip install --system -r requirements.txt")
            if proc.exit_code != 0:
                print(f"⚠️  Dependency installation warning: {proc.stderr}")
            else:
                print("✅ Dependencies installed.")

    def _run_in_container(self, command: str) -> DockerProcess:
        if not self.container_id:
            raise RuntimeError("Docker sandbox container is not running.")

        container = self.docker_client.containers.get(self.container_id)
        try:
            exec_result = container.exec_run(
                cmd=f"/bin/bash -c '{command}'",  # Wrap in bash for pipes/env vars
                workdir="/app/workspace",
                stream=False,
                demux=True,
            )
            stdout = exec_result.output[0].decode("utf-8") if exec_result.output[0] else ""
            stderr = exec_result.output[1].decode("utf-8") if exec_result.output[1] else ""
            return DockerProcess(stdout=stdout, stderr=stderr, exit_code=exec_result.exit_code)
        except Exception as e:
            return DockerProcess(stderr=f"Docker exec error: {e}", exit_code=1)

    def run_code(self, code: str) -> DockerExecution:
        """Runs a standalone python script inside the repo context."""
        script_name = f"temp_script_{os.urandom(4).hex()}.py"
        script_path = f"/app/workspace/{script_name}"  # Save in workspace

        try:
            self.files.write(script_path, code)
            proc = self._run_in_container(f"python3 {script_name}")
            self._run_in_container(f"rm {script_name}")

            results = []
            logs = DockerExecutionLogs(stdout=proc.stdout, stderr=proc.stderr)
            return DockerExecution(logs=logs, results=results)
        except Exception as e:
            return DockerExecution(logs=DockerExecutionLogs(stderr=f"Error executing code: {e}"), results=[])

    def stop(self) -> None:
        if self.container_id:
            try:
                container = self.docker_client.containers.get(self.container_id)
                container.stop()
                container.remove()
                print(f"🛑 Container '{self.container_name}' stopped.")
            except NotFound:
                pass
            self.container_id = None

    def __del__(self) -> None:
        self.stop()
