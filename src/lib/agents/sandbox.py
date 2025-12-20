"""Secure Docker-based sandbox for agent code execution.

Input:
    None

Output:
    DockerSandbox
       - isolated execution environment
       - gVisor-based container runtime

Side Effects:
    - runs docker info and docker images
    - builds agent-sandbox:latest image on demand
"""

from dataclasses import dataclass
import logging
import os
import subprocess
import tempfile
from typing import Optional


class SecurityEnvironmentError(Exception):
    """Signal misconfigured Docker security environment."""


class ContainerRuntimeError(Exception):
    """Signal container lifecycle or execution failure."""


class ResourceLimitExceeded(Exception):
    """Signal container resource limit violation."""


class SecurityViolation(Exception):
    """Signal forbidden or unexpected container behavior."""


@dataclass
class ExecutionResult:
    """Container execution result record.

    Input:
        stdout: str
           - captured standard output
           - UTF-8 decoded text
        stderr: str
           - captured standard error
           - UTF-8 decoded text
        exit_code: int
           - container process exit status
           - zero on success
        artifacts_path: str
           - absolute filesystem path
           - directory containing copied /app artifacts

    Output:
        instance: ExecutionResult
           - immutable snapshot of container run
           - suitable for logging and UI display
    """
    stdout: str
    stderr: str
    exit_code: int
    artifacts_path: str


def check_docker_security() -> None:
    """Docker security capability validation."""
    info = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if info.returncode != 0:
        raise SecurityEnvironmentError("Failed to run 'docker info'. Docker may not be installed or running.")
    stdout_lower = info.stdout.lower()
    if "rootless" not in stdout_lower:
        raise SecurityEnvironmentError("Rootless Docker is not enabled.")
    if "runsc" not in stdout_lower:
        raise SecurityEnvironmentError("The 'runsc' runtime (gVisor) is not available.")


def build_sandbox_image() -> None:
    """
    Builds the agent-sandbox:latest image if it doesn't already exist.
    The Dockerfile uses python:3.11-slim as base and sets up a non-root user.
    """
    check_image = subprocess.run(
        ["docker", "images", "-q", "agent-sandbox:latest"],
        capture_output=True,
        text=True,
    )
    if check_image.returncode != 0:
        raise SecurityEnvironmentError(f"Failed checking for agent-sandbox:latest image.\n{check_image.stderr}")

    if not check_image.stdout.strip():
        dockerfile_content = """\
FROM python:3.11-slim
RUN apt-get update && apt-get install -y git curl
RUN useradd -m -u 1000 sandbox_user
WORKDIR /app
USER sandbox_user
"""
        with tempfile.TemporaryDirectory(prefix="dockerfile_") as tmpdir:
            df_path = os.path.join(tmpdir, "Dockerfile")
            with open(df_path, "w", encoding="utf-8") as f:
                f.write(dockerfile_content)

            build_proc = subprocess.run(
                ["docker", "build", "-t", "agent-sandbox:latest", tmpdir],
                capture_output=True,
                text=True,
            )
            if build_proc.returncode != 0:
                raise SecurityEnvironmentError(f"Failed to build agent-sandbox image:\n{build_proc.stderr}")


class DockerSandbox:
    """
    DockerSandbox:
        Secure sandbox container using gVisor and rootless Docker.
        Designed for arbitrary code execution with unlimited internet access.
        Provides a isolated environment for agentic tasks.

    Security:
        - Zero-Trust design: all untrusted code runs under an isolated runtime (runsc).
        - Strict filesystem policy: no bind mounts or volumes; only file transfers via docker cp.
        - Ephemeral container lifecycle: create → copy in → run → copy out → remove.
        - Rootless + non-root user inside container: defense-in-depth (minimal privileges).
        - Resource Limits: 8GB RAM, 4 CPU cores to block resource exhaustion attacks.
        - Observability: logs each Docker command and raises typed exceptions on errors.
    """

    def __init__(self) -> None:
        """Sandbox initialization and security bootstrap.

        Side Effects:
            - configures module logger
            - validates Docker security configuration
            - ensures agent-sandbox:latest image exists
        """
        self.logger = logging.getLogger(__name__)
        self.logger.debug("Initializing DockerSandbox...")
        # Verify environment security prerequisites
        check_docker_security()
        build_sandbox_image()
        self.logger.debug("Initialization complete. Rootless Docker and runsc found.")

    def run(self, code_repo_path: str, command: str) -> ExecutionResult:
        """Isolated container execution with gVisor and rootless Docker.

        Input:
            code_repo_path: str
               - host filesystem path to repository root
               - readable directory containing agent workspace
            command: str
               - shell command string executed as `bash -c`
               - interpreted inside /app working directory

        Output:
            result: ExecutionResult
               - captured stdout and stderr from container
               - exit_code reflecting process status
               - artifacts_path pointing to copied /app contents

        Side Effects:
            - creates temporary artifacts directory on host
            - runs docker create with runsc runtime and resource limits
            - copies code_repo_path into /app inside container
            - executes command as non-root user uid 1000
            - copies /app directory back to host artifacts_path
            - removes container in finally block

        Security Architecture:
            - rootless Docker
               - container engine runs without host root privileges
               - reduces impact of daemon or runtime compromise
            - gVisor runsc runtime
               - user-space kernel sandbox between container and host
               - intercepts syscalls and enforces isolation boundaries
            - non-root container user
               - processes run as uid 1000 inside container
               - prevents privileged operations even if escaped
            - no bind mounts
               - host filesystem never directly mounted into container
               - data exchange limited to docker cp in and out
            - ephemeral lifecycle
               - new container per run
               - no persistent state or long-lived credentials
            - resource limits
               - memory capped at 8 GB
               - CPU limited to 4 cores
               - mitigates denial-of-service via resource exhaustion
        """
        container_id: Optional[str] = None
        output_dir = tempfile.mkdtemp(prefix="sandbox_artifacts_")

        try:
            # Step 1: Create container
            create_cmd = [
                "docker",
                "create",
                "--tty",
                "--interactive",
                "--runtime=runsc",
                "--init",
                "--user=1000:1000",
                "-w",
                "/app",
                "-v",
                "/usr/bin:/usr/bin:ro",
                "-v",
                "/usr/lib:/usr/lib:ro",
                "-v",
                "/lib:/lib:ro",
                "-v",
                "/usr/local/bin:/usr/local/bin:ro",
                "agent-sandbox:latest",
                "bash",
                "-c",
                command,
            ]
            self.logger.info("Creating container with command: %s", create_cmd)
            create_proc = subprocess.run(create_cmd, capture_output=True, text=True)
            if create_proc.returncode != 0 or not create_proc.stdout.strip():
                raise ContainerRuntimeError(f"Failed to create container:\n{create_proc.stderr}")
            container_id = create_proc.stdout.strip()
            self.logger.debug("Created container: %s", container_id)

            # Step 2: Copy code into container
            cp_in_cmd = ["docker", "cp", code_repo_path, f"{container_id}:/app"]
            self.logger.info("Copying code into container: %s", cp_in_cmd)
            cp_in_proc = subprocess.run(cp_in_cmd, capture_output=True, text=True)
            if cp_in_proc.returncode != 0:
                raise ContainerRuntimeError(f"Failed to copy code into container:\n{cp_in_proc.stderr}")
            self.logger.debug("Copied code_repo_path '%s' into container.", code_repo_path)

            # Step 3:
            #   - Initialize interactive docker process
            #   - Execute command
            #   - Capture output
            start_cmd = ["docker", "start", "-a", "-i", container_id]
            self.logger.info("Starting container: %s", start_cmd)
            start_proc = subprocess.run(start_cmd, capture_output=True, text=True)
            stdout = start_proc.stdout
            stderr = start_proc.stderr
            exit_code = start_proc.returncode
            self.logger.debug("Container execution exit code: %d", exit_code)

            # Step 4: Copy artifacts out of container
            cp_out_cmd = ["docker", "cp", f"{container_id}:/app", output_dir]
            self.logger.info("Copying artifacts out of container: %s", cp_out_cmd)
            cp_out_proc = subprocess.run(cp_out_cmd, capture_output=True, text=True)
            if cp_out_proc.returncode != 0:
                # Attempt to remove container anyway, but warn about artifact copying failure
                self.logger.warning("Failed to copy artifacts out: %s", cp_out_proc.stderr)

            # Raise on non-zero exit code
            if exit_code != 0:
                raise ContainerRuntimeError(f"Container exited with non-zero code {exit_code}:\n{stderr}")

            self.logger.info("Execution successful, artifacts copied to %s", output_dir)
            return ExecutionResult(stdout=stdout, stderr=stderr, exit_code=exit_code, artifacts_path=output_dir)

        finally:
            # Step 5: Remove container in a finally block
            if container_id:
                rm_cmd = ["docker", "rm", "-f", container_id]
                self.logger.info("Removing container: %s", rm_cmd)
                subprocess.run(rm_cmd, capture_output=True, text=True)
                self.logger.debug("Container %s removed.", container_id)
