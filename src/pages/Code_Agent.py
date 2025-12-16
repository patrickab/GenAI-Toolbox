import json
import shlex
from typing import List

from langchain_core.tools import StructuredTool, tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from llm_baseclient.client import LLMClient
import streamlit as st

from src.lib.agents.docker_sandbox import DockerSandbox

# --- CORE LOGIC (Decoupled from UI) ---


class ACIRuntime:
    """
    Autonomous Coding Interface Runtime.
    Manages the Sandbox and Tool definitions independent of the UI.
    This class can be instantiated in a CLI script or a Web App.
    """

    def __init__(self, github_url: str) -> None:
        """
        Initialize the ACI runtime with a Docker-backed sandbox.
        """
        self.sandbox = DockerSandbox(repo_url=github_url)
        self.tools = self._init_tools()
        self.tools_map = {t.name: t for t in self.tools}
        self.tools_schema = [convert_to_openai_tool(t) for t in self.tools]

    def _init_tools(self) -> List[StructuredTool]:
        """Initialize tools with access to the specific sandbox instance."""

        @tool
        def execute_python(code: str) -> str:
            """
            Executes Python code in a secure Docker sandbox.
            Returns stdout, stderr, and handles image artifacts.
            """
            execution = self.sandbox.run_code(code)
            output = ""
            if execution.logs.stdout:
                output += f"Output:\n{execution.logs.stdout}\n"
            if execution.logs.stderr:
                output += f"Error:\n{execution.logs.stderr}\n"

            for result in execution.results:
                if hasattr(result, "png") and result.png:
                    output += "[Image generated]"

            return output if output else "Code executed successfully."

        @tool
        def read_file(path: str, start_line: int = 1, end_line: int = 100) -> str:
            """Reads a file from start_line to end_line with line numbers."""
            try:
                content = self.sandbox.files.read(path)
                lines = content.splitlines()
                start_idx = max(0, start_line - 1)
                end_idx = min(len(lines), end_line)

                selected_lines = lines[start_idx:end_idx]
                output = []
                for i, line in enumerate(selected_lines):
                    output.append(f"{start_idx + i + 1}: {line}")
                return "\n".join(output)
            except Exception as e:
                return f"Error reading file: {e}"

        @tool
        def edit_file(path: str, start_line: int, end_line: int, new_content: str) -> str:
            """Replaces lines [start_line, end_line] with new_content and validates syntax."""
            try:
                content = self.sandbox.files.read(path)
                lines = content.splitlines()

                new_lines = new_content.splitlines()
                start_idx = max(0, start_line - 1)

                candidate_lines = lines[:start_idx] + new_lines + lines[end_line:]
                candidate_content = "\n".join(candidate_lines)

                temp_path = f"{path}.temp"
                self.sandbox.files.write(temp_path, candidate_content)

                proc = self.sandbox.commands.run(
                    f"python3 -m py_compile {shlex.quote(temp_path)}"
                )

                if proc.exit_code != 0:
                    self.sandbox.commands.run(f"rm {temp_path}")
                    return f"Error: Syntax Error: {proc.stderr}"

                self.sandbox.files.write(path, candidate_content)
                self.sandbox.commands.run(f"rm {temp_path}")
                return "Success"
            except Exception as e:
                return f"Error editing file: {e}"

        @tool
        def list_dir(path: str = ".") -> str:
            """Lists files in a directory."""
            proc = self.sandbox.commands.run(f"ls -F {shlex.quote(path)}")
            if proc.exit_code != 0:
                return f"Error: {proc.stderr}"

            lines = proc.stdout.splitlines()
            if len(lines) > 50:
                return "\n".join(lines[:50]) + "\n... Output truncated."
            return proc.stdout

        @tool
        def search_code(query: str, dir: str = ".") -> str:
            """Searches for a string in files."""
            proc = self.sandbox.commands.run(
                f"grep -rn {shlex.quote(query)} {shlex.quote(dir)} | head -n 20"
            )
            return proc.stdout if proc.stdout else "No matches found."

        @tool
        def run_shell(command: str) -> str:
            """Executes a shell command inside the Docker sandbox."""
            proc = self.sandbox.commands.run(command)
            return (
                f"STDOUT:\n{proc.stdout}\n"
                f"STDERR:\n{proc.stderr}\n"
                f"Exit Code: {proc.exit_code}"
            )

        return [
            execute_python,
            read_file,
            edit_file,
            list_dir,
            search_code,
            run_shell,
        ]


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
