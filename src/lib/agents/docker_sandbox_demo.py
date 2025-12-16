# %% [markdown]
# # Docker Sandbox Verification
import os
import sys

# Adjust import based on where you saved the class
# from src.lib.agents.docker_sandbox import DockerSandbox
from docker_sandbox import DockerSandbox

# %%
# 1. Initialize the Sandbox
# This will:
# a) Clone 'gigachad-bot' to ./agent_workspaces/gigachad-bot on your host
# b) Build the 'agent-worker-uv' image (if missing)
# c) Start the container with the repo mounted to /app/workspace
try:
    repo_url = "https://github.com/patrickab/gigachad-bot.git"
    sandbox = DockerSandbox(repo_url=repo_url, branch="main")
    print(f"✅ Sandbox initialized. Container ID: {sandbox.container_id}")
    print(f"📂 Host Repository Path: {sandbox.host_repo_path}")
except Exception as e:
    print(f"❌ Initialization failed: {e}")
    sys.exit(1)

# %%
# 2. Verify Repository Mount
# Let's check if the repo files actually exist inside the container
print("--- Checking Container Workspace ---")
ls_result = sandbox.commands.run("ls -la /app/workspace")
print(ls_result.stdout)

# %%
# 3. Define the Fibonacci Task
# We calculate f_0 through f_10 and print them to stdout.
# We also write the result to a file in the shared workspace to test persistence.

fib_script = """
import os

def get_fibonacci_sequence(n):
    sequence = []
    a, b = 0, 1
    for _ in range(n):
        sequence.append(a)
        a, b = b, a + b
    return sequence

# Calculate f_0 -> f_10 (11 numbers)
count = 11
fib_seq = get_fibonacci_sequence(count)

# 1. Print to STDOUT (captured by logs)
print(f"Fibonacci Sequence (f0-f10): {fib_seq}")

# 2. Write to Workspace (captured by host volume)
# Note: /app/workspace maps to your host's ./agent_workspaces/gigachad-bot
output_path = "/app/workspace/fib_output.txt"
with open(output_path, "w") as f:
    f.write(str(fib_seq))
    
print(f"Saved result to {output_path}")
"""

# %%
# 4. Execute the Code
print("\n⏳ Executing Fibonacci script inside container...")
execution = sandbox.run_code(fib_script)

# %%
# 5. Verify Results (Logs)
print("--- Execution Logs ---")
if execution.logs.stderr:
    print(f"❌ STDERR:\n{execution.logs.stderr}")
else:
    print(f"✅ STDOUT:\n{execution.logs.stdout}")

# %%
# 6. Verify Persistence (Host Volume)
# The container wrote to /app/workspace/fib_output.txt
# We check the specific repo folder on the host.

host_file_path = os.path.join(sandbox.host_repo_path, "fib_output.txt")

if os.path.exists(host_file_path):
    with open(host_file_path, "r") as f:
        content = f.read()
    print(f"✅ Host File Check: Found 'fib_output.txt' at {host_file_path}")
    print(f"📂 File Content: {content}")
else:
    print(f"❌ Host File Check: File not found at {host_file_path}")

# %%
# 7. Cleanup
# Stop and remove the container.
sandbox.stop()
print("🛑 Sandbox stopped.")
