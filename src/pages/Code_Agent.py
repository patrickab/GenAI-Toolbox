import json
import shlex
from typing import Any, Dict, Generator, List

from llm_baseclient.client import LLMClient
from pydantic import BaseModel, Field
import streamlit as st

from lib.streamlit_helper import model_selector
from lib.utils.logger import get_logger
from src.lib.agents.docker_sandbox import DockerSandbox

logger = get_logger()


class AgentTool(BaseModel):
    """Base class that unifies schema definition and execution logic."""

    @classmethod
    def definition(cls) -> Dict[str, Any]:
        """Auto-generates OpenAI tool definition from Pydantic schema."""
        schema = cls.model_json_schema()
        return {
            "type": "function",
            "function": {
                "name": cls.__name__,
                "description": cls.__doc__.strip() if cls.__doc__ else "",
                "parameters": {
                    "type": "object",
                    "properties": schema.get("properties", {}),
                    "required": schema.get("required", []),
                },
            },
        }

    def run(self, sandbox: DockerSandbox) -> str:
        """Abstract method to execute the tool logic."""
        raise NotImplementedError


# --- CONCRETE TOOLS (Schema + Logic) ---


class ReadFile(AgentTool):
    """
    Reads a specific section of a file. Returns content with line numbers.
    Use this to inspect code before editing.
    """

    path: str = Field(..., description="The relative path to the file")
    start_line: int = Field(1, description="The line number to start reading from (1-based)")
    end_line: int = Field(100, description="The line number to end reading at (1-based)")

    def run(self, sandbox: DockerSandbox) -> str:
        logger.debug("ACI Tools: Reading file '%s' lines %d-%d", self.path, self.start_line, self.end_line)
        try:
            content = sandbox.files.read(self.path)
            lines = content.splitlines()
            start_idx = max(0, self.start_line - 1)
            end_idx = min(len(lines), self.end_line)

            output = [f"{start_idx + i + 1}: {line}" for i, line in enumerate(lines[start_idx:end_idx])]

            if not output:
                return "File is empty or range is invalid."
            return "\n".join(output)
        except Exception as e:
            logger.error(f"Error reading file: {e}", exc_info=True)
            return str(e)


class EditFile(AgentTool):
    """
    Replaces lines in a file with new content.
    Auto-lints before saving to prevent syntax errors.
    """

    path: str = Field(..., description="The relative path to the file")
    start_line: int = Field(..., description="The line number to start replacing (1-based)")
    end_line: int = Field(..., description="The line number to end replacing (1-based, inclusive)")
    new_content: str = Field(..., description="The new code to insert (can be multiple lines)")

    def run(self, sandbox: DockerSandbox) -> str:
        logger.info("ACI Tools: Editing file '%s' lines %d-%d", self.path, self.start_line, self.end_line)
        try:
            content = sandbox.files.read(self.path)
            lines = content.splitlines()
            start_idx = max(0, self.start_line - 1)

            if start_idx > len(lines):
                return f"Error: Start line {self.start_line} is beyond end of file ({len(lines)} lines)"

            final_lines = lines[:start_idx] + self.new_content.splitlines() + lines[self.end_line :]
            final_content = "\n".join(final_lines)

            # Write temp and lint
            temp_path = f"{self.path}.temp_lint"
            sandbox.files.write(temp_path, final_content)

            proc = sandbox.commands.run(f"python3 -m py_compile {shlex.quote(temp_path)}")
            if proc.exit_code != 0:
                sandbox.commands.run(f"rm {temp_path}")
                return f"❌ Edit Rejected: Syntax Error.\n{proc.stderr}"

            sandbox.commands.run(f"mv {temp_path} {self.path}")
            return "✅ Success: File edited and syntax verified."
        except Exception as e:
            logger.error(f"Error editing file: {e}", exc_info=True)
            return str(e)


class RunShell(AgentTool):
    """
    Executes a shell command in the sandbox.
    """

    command: str = Field(..., description="The bash command to run")

    def run(self, sandbox: DockerSandbox) -> str:
        logger.info("ACI Tools: Running shell: %s", self.command)
        proc = sandbox.commands.run(self.command)
        return f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}\nExit Code: {proc.exit_code}"


class SearchCode(AgentTool):
    """Searches for a string pattern in the codebase."""

    query: str = Field(..., description="The string to search for")
    dir: str = Field(".", description="The directory to search in")

    def run(self, sandbox: DockerSandbox) -> str:
        ignore = "{.git,.venv,venv,__pycache__,node_modules,.mypy_cache}"
        cmd = f"grep -rnH -I --exclude-dir={ignore} {shlex.quote(self.query)} {shlex.quote(self.dir)} | head -n 200"
        proc = sandbox.commands.run(cmd)
        return proc.stdout or "No matches found."


class ListDir(AgentTool):
    """Lists files in a directory."""

    path: str = Field(".", description="The directory path to list")

    def run(self, sandbox: DockerSandbox) -> str:
        proc = sandbox.commands.run(f"ls -F {shlex.quote(self.path)}")
        if proc.exit_code != 0:
            return f"Error: {proc.stderr}"

        lines = [l for l in proc.stdout.splitlines() if not l.startswith((".", "__"))]
        return "\n".join(lines[:50]) + ("\n... (Truncated)" if len(lines) > 50 else "")


class CodeAgentTools:
    """
    The Runtime implementation of the Agent-Computer Interface (ACI).
    Dispatches Pydantic tools to the DockerSandbox.
    """

    def __init__(self, sandbox: DockerSandbox) -> None:
        self.sandbox = sandbox
        # Registry is now a simple list of classes
        self.registry = {t.__name__: t for t in [ReadFile, EditFile, RunShell, SearchCode, ListDir]}
        logger.debug("ACI Tools: Initialized with %d tools", len(self.registry))

    def get_definitions(self) -> List[Dict[str, Any]]:
        """Exposes the schemas to the LLM Client in OpenAI format."""
        return [t.definition() for t in self.registry.values()]

    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        Dispatcher: Instantiates the tool model and runs it.
        """
        tool_cls = self.registry.get(tool_name)
        if not tool_cls:
            return f"Error: Tool '{tool_name}' not found"

        try:
            # Validate args & Instantiate
            tool = tool_cls(**arguments)
            # Execute
            return tool.run(self.sandbox)
        except Exception as e:
            error_msg = f"Error executing {tool_name}: {e}"
            logger.error(error_msg, exc_info=True)
            return error_msg


# --- AGENT LOGIC ---


class CodeAgent:
    """
    Autonomous Agent that uses LLMClient and CodeAgentTools to solve tasks.
    Implements the Think -> Act -> Observe loop with UI feedback.
    """

    def __init__(self, repo_url: str, branch: str = "main") -> None:
        # 1. Initialize Components
        self.client = LLMClient()
        self.sandbox = DockerSandbox(repo_url=repo_url, branch=branch)
        self.tools = CodeAgentTools(self.sandbox)
        self.tool_definitions = self.tools.get_definitions()

        # 2. ACI-Specific System Prompt (The "Brain" Logic)
        self.system_prompt = (
            "You are an expert software engineer working in a sandboxed environment.\n"
            "TOOLS: You have access to 'read_file', 'edit_file', 'run_shell', etc.\n"
            "PROTOCOL:\n"
            "1. EXPLORE: Always list_dir and read_file before editing.\n"
            "2. VERIFY: Always create a reproduction script or test case before fixing.\n"
            "3. EDIT: Use edit_file with precise line numbers (derived from read_file).\n"
            "4. TEST: Run your test script to confirm the fix.\n"
            "5. DONE: When the test passes, output 'TASK_COMPLETE'."
        )

        # 3. Inject System Prompt if history is empty
        if not self.client.messages:
            self.client.messages.append({"role": "system", "content": self.system_prompt})

    def run(self, task: str, model: str, max_steps: int = 15) -> Generator[Dict[str, Any], None, None]:
        """
        Executes the agent loop, yielding events for the UI.
        Yields: {'type': str, 'content': str, 'name': str, 'args': str}
        """

        # 1. Initial Call to LLM
        # We pass the user task here.
        response = self.client.chat(
            model=model,  # Fallback default
            user_msg=task,
            tools=self.tool_definitions,
            stream=False,
        )

        step = 0
        while step < max_steps:
            step += 1
            message = response.choices[0].message

            # --- CASE A: Tool Call (The Agent wants to act) ---
            if message.tool_calls:
                # Yield status update to UI
                yield {"type": "status", "content": f"Step {step}: Agent is using tools..."}

                for tool_call in message.tool_calls:
                    func_name = tool_call.function.name
                    args_str = tool_call.function.arguments

                    # Yield the intent (Show user what tool is being called)
                    yield {"type": "tool_call", "name": func_name, "args": args_str}

                    try:
                        # Parse Arguments
                        args = json.loads(args_str)

                        # Execute Tool (ACI Runtime)
                        result = self.tools.execute(func_name, args)
                    except Exception as e:
                        result = f"Error executing tool: {e!s}"
                        yield {"type": "error", "content": result}

                    # Yield the result (Show user the output)
                    yield {"type": "tool_result", "content": result}

                    # Update LLM History (Critical for State)
                    self.client.add_tool_result(tool_call_id=tool_call.id, output=result)

                # Recursion: Call LLM again with the tool outputs
                # user_msg is None because we are continuing the existing thread
                response = self.client.chat(model=model, user_msg=None, tools=self.tool_definitions, stream=False)

            # --- CASE B: Text Response (The Agent wants to talk or is done) ---
            else:
                content = message.content
                yield {"type": "response", "content": content}

                # Stop Condition 1: Explicit Token
                if "TASK_COMPLETE" in content:
                    break

                # Stop Condition 2: Model yielded text instead of tools (HITL)
                # We break to allow the user to reply.
                break


# --- STREAMLIT INTERFACE ---


def main() -> None:
    st.set_page_config(page_title="Agent-in-a-Box", layout="wide")

    with st.sidebar:
        model_selector(key="code_agent")
        st.subheader("🔧 Sandbox Configuration")
        repo_url = st.text_input("GitHub Repository URL")

        if not repo_url:
            st.warning("Provide a valid GitHub URL to initialize the agent.")
            return

        if repo_url and "code_agent" not in st.session_state:
            st.session_state.code_agent = CodeAgent(repo_url=repo_url, branch="main")
            st.success("Agent Initialized")
            st.rerun()

        debug_mode = st.toggle("Debug Mode", value=False)
        if st.button("Reset Agent"):
            st.session_state.pop("agent", None)
            st.session_state.messages = []
            st.rerun()

    with st._bottom:
        prompt = st.chat_input("Assign a task to the agent...")

    if prompt:
        # Show User Message
        with st.chat_message("user"):
            st.markdown(prompt)
        # (Optional) Add to UI history
        # st.session_state.messages.append({"role": "user", "content": prompt})

        # Run Agent
        with st.chat_message("assistant"):
            container = st.container()
            status_container = container.empty()
            response_placeholder = container.empty()
            full_response = ""

            # Stream agent steps
            code_agent: CodeAgent = st.session_state.code_agent

            try:
                for event in code_agent.run(task=prompt, model=st.session_state.selected_model):
                    # A. Status Update (Spinner logic)
                    if event["type"] == "status":
                        status_container.status(event["content"], state="running")

                    # B. Tool Call (Expandable details)
                    elif event["type"] == "tool_call":
                        with container.expander(f"🛠️ Executing: {event['name']}"):
                            st.code(event["args"], language="json")

                    # C. Tool Result (Output)
                    elif event["type"] == "tool_result":
                        with container.expander("📄 Result", expanded=debug_mode):
                            st.code(event["content"])

                    # D. Final/Text Response
                    elif event["type"] == "response":
                        status_container.empty()  # Remove status spinner
                        full_response = event["content"]
                        response_placeholder.markdown(full_response)

                    # E. Error
                    elif event["type"] == "error":
                        container.error(event["content"])

                # (Optional) Add to UI history
                # st.session_state.messages.append({"role": "assistant", "content": full_response})

            except Exception as e:
                st.error(f"Runtime Error: {e}")


if __name__ == "__main__":
    main()
