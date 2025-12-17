import json
import shlex
from typing import Any, Dict

from llm_baseclient.client import LLMClient
import streamlit as st

from lib.agents.tools import get_aci_tools

# --- CORE LOGIC (Decoupled from UI) ---
from lib.utils.logger import get_logger
from src.lib.agents.docker_sandbox import DockerSandbox

logger = get_logger()


class CodeAgentTools:
    """
    The Runtime implementation of the Agent-Computer Interface (ACI).
    Maps abstract Pydantic tools to concrete DockerSandbox commands.
    """

    def __init__(self, sandbox: DockerSandbox) -> None:
        self.sandbox = sandbox
        logger.debug("CodeAgentTools initialized with sandbox: %s", sandbox)

    def get_definitions(self) -> list[dict]:
        """Exposes the schemas to the LLM Client."""
        logger.debug("Fetching tool definitions")
        tools = get_aci_tools()
        logger.info("Retrieved %d tool definitions", len(tools))
        return tools

    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        Dispatcher: Routes the tool name to the actual Python method.
        """
        logger.info("Executing tool '%s' with args: %s", tool_name, arguments)
        method = getattr(self, f"_{tool_name}", None)
        if not method:
            error_msg = f"Tool '{tool_name}' not found"
            logger.warning(error_msg)
            return f"Error: {error_msg}"

        try:
            result = method(**arguments)
            logger.debug("Tool '%s' executed successfully", tool_name)
            return result
        except Exception as e:
            error_msg = f"Error executing {tool_name}: {e!s}"
            logger.error(error_msg, exc_info=True)
            return error_msg

    # --- Tool Implementations ---

    def _read_file(self, path: str, start_line: int = 1, end_line: int = 100) -> str:
        logger.debug("Reading file '%s' lines %d-%d", path, start_line, end_line)
        try:
            content = self.sandbox.files.read(path)
            lines = content.splitlines()

            # Handle 1-based indexing
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines), end_line)

            selected_lines = lines[start_idx:end_idx]

            # Add line numbers for the LLM
            output = []
            for i, line in enumerate(selected_lines):
                output.append(f"{start_idx + i + 1}: {line}")

            if not output:
                logger.info("File read returned empty content for '%s'", path)
                return "File is empty or range is invalid."

            logger.debug("Successfully read %d lines from '%s'", len(output), path)
            return "\n".join(output)
        except Exception as e:
            error_msg = f"Error reading file: {e}"
            logger.error(error_msg, exc_info=True)
            return error_msg

    def _edit_file(self, path: str, start_line: int, end_line: int, new_content: str) -> str:
        logger.info("Editing file '%s' from line %d to %d", path, start_line, end_line)
        # TODO: Safety checks - "expected_start_line=code[start_line] expected_end_line=code[end_line]"
        try:
            # 1. Read original
            content = self.sandbox.files.read(path)
            lines = content.splitlines()

            # 2. Prepare Slicing (1-based to 0-based)
            start_idx = max(0, start_line - 1)
            end_idx = end_line  # Python slice end is exclusive, which matches 1-based inclusive logic perfectly

            # 3. Apply Edit in Memory
            new_lines_list = new_content.splitlines()

            # Safety Check: Are we extending the file?
            if start_idx > len(lines):
                error_msg = f"Start line {start_line} is beyond end of file ({len(lines)} lines)"
                logger.warning(error_msg)
                return f"Error: {error_msg}"

            # Reconstruct content
            final_lines = lines[:start_idx] + new_lines_list + lines[end_idx:]
            final_content = "\n".join(final_lines)

            # 4. Write to Temp File
            temp_path = f"{path}.temp_lint"
            self.sandbox.files.write(temp_path, final_content)
            logger.debug("Wrote temp file for linting: %s", temp_path)

            # 5. Auto-Lint (Syntax Check)
            # We use py_compile to check for syntax errors before overwriting
            lint_cmd = f"python3 -m py_compile {shlex.quote(temp_path)}"
            proc = self.sandbox.commands.run(lint_cmd)

            if proc.exit_code != 0:
                # Cleanup and Fail
                self.sandbox.commands.run(f"rm {temp_path}")
                error_msg = f"Edit Rejected: Syntax Error in generated code.\n{proc.stderr}"
                logger.warning(error_msg)
                return f"❌ {error_msg}"

            # 6. Commit Change
            self.sandbox.commands.run(f"mv {temp_path} {path}")
            logger.info("Successfully edited file '%s'", path)
            return "✅ Success: File edited and syntax verified."

        except Exception as e:
            error_msg = f"Error editing file: {e}"
            logger.error(error_msg, exc_info=True)
            return error_msg

    def _search_code(self, query: str, dir: str = ".") -> str:
        logger.debug("Searching for '%s' in directory '%s'", query, dir)
        # Implements the robust grep logic ignoring venv/git
        # -r: recursive
        # -n: line numbers
        # -H: print filename
        # -I: ignore binary files
        # --exclude-dir: ignore noise

        ignore_dirs = "{.git,.venv,venv,__pycache__,node_modules,.mypy_cache}"

        cmd = (
            f"grep -rnH -I "
            f"--exclude-dir={ignore_dirs} "
            f"{shlex.quote(query)} {shlex.quote(dir)} "
            f"| head -n 200"
        )

        proc = self.sandbox.commands.run(cmd)

        if proc.exit_code != 0 and not proc.stdout:
            logger.info("No matches found for query '%s'", query)
            return "No matches found."

        logger.debug("Search returned %d characters", len(proc.stdout))
        return proc.stdout

    def _list_dir(self, path: str = ".") -> str:
        logger.debug("Listing directory contents for '%s'", path)
        # -F adds trailing / to dirs
        proc = self.sandbox.commands.run(f"ls -F {shlex.quote(path)}")
        if proc.exit_code != 0:
            error_msg = f"Error listing directory: {proc.stderr}"
            logger.warning(error_msg)
            return f"Error: {error_msg}"

        lines = proc.stdout.splitlines()

        # Filter out hidden noise if needed, or just truncate
        filtered = [line for line in lines if not line.startswith("__") and not line.startswith(".git")]

        if len(filtered) > 50:
            return "\n".join(filtered[:50]) + "\n... (Output truncated)"
        return "\n".join(filtered)

    def _run_shell(self, command: str) -> str:
        proc = self.sandbox.commands.run(command)
        return f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}\nExit Code: {proc.exit_code}"


SYSTEM_PROMPT = (
    "You are an expert Python coder using an ACI (Agent Compute Interface). "
    "You have access to a live Linux sandbox.\n\n"
    "GUIDELINES:\n"
    "1. EXPLORATION: Use `list_dir` to see file structure and `search_code` to find logic.\n"
    "2. INSPECTION: Use `read_file` to inspect code with line numbers before editing.\n"
    "3. EDITING: Use `edit_file` to modify code. It auto-checks syntax.\n"
    "4. EXECUTION: Use `run_shell` to run tests or `execute_python` for scratchpad calculations.\n"
)

# --- STREAMLIT UI ---


def main_streamlit() -> None:
    st.set_page_config(page_title="Autonomous Coding Agent", layout="wide")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    if "runtime" not in st.session_state:
        with st.sidebar:
            st.subheader("🔧 Sandbox Configuration")
            github_url = st.text_input("GitHub Repository URL")

            if not github_url:
                st.warning("Please enter a GitHub repository URL to proceed.")
            else:
                st.session_state.runtime = ACIRuntime(github_url=github_url)
                st.toast("🐳 Docker Sandbox Ready", icon="✅")
    
    if "runtime" not in st.session_state:
        st.stop()

    if "llm_client" not in st.session_state:
        st.session_state.llm_client = LLMClient()

    runtime = st.session_state.runtime

    with st.sidebar:
        st.header("📂 Sandbox Files")
        uploaded_file = st.file_uploader("Upload to Sandbox")
        if uploaded_file:
            runtime.sandbox.files.write(uploaded_file.name, uploaded_file.read().decode())
            st.success(f"Uploaded {uploaded_file.name}")

        if st.button("Refresh File List"):
            st.code(runtime.sandbox.commands.run("ls -F").stdout)

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt_text := st.chat_input("Task for Agent:"):
        st.session_state.messages.append({"role": "user", "content": prompt_text})
        with st.chat_message("user"):
            st.markdown(prompt_text)

        with st.chat_message("assistant"):
            with st.status("Agent Workflow", expanded=True) as status:
                try:
                    client = st.session_state.llm_client
                    client.messages = st.session_state.messages.copy()
                    final_response = None

                    for turn in range(10):
                        response = client.chat(
                            model="gpt-4-turbo",
                            user_msg=prompt_text if turn == 0 else None,
                            system_prompt=SYSTEM_PROMPT,
                            tools=runtime.tools_schema,
                            stream=False,
                        )

                        msg = response.choices[0].message
                        if msg.content:
                            st.markdown(msg.content)

                        if msg.tool_calls:
                            for tc in msg.tool_calls:
                                tool_func = runtime.tools_map.get(tc.function.name)
                                if tool_func:
                                    args = json.loads(tc.function.arguments)
                                    result = tool_func.invoke(args)
                                    with st.expander(tc.function.name):
                                        st.code(result)
                                    client.messages.append(
                                        {
                                            "role": "tool",
                                            "tool_call_id": tc.id,
                                            "name": tc.function.name,
                                            "content": str(result),
                                        }
                                    )
                        else:
                            final_response = msg.content
                            break

                    status.update(label="Complete", state="complete", expanded=False)
                except Exception as e:
                    final_response = f"Error: {e}"
                    status.update(label="Failed", state="error")

            st.session_state.messages.append(
                {"role": "assistant", "content": final_response or "Task completed."}
            )


if __name__ == "__main__":
    try:
        main_streamlit()
    finally:
        runtime = st.session_state.get("runtime")
        if runtime:
            runtime.sandbox.stop()
