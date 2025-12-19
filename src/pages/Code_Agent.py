from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import List

import streamlit as st

from lib.streamlit_helper import model_selector
from lib.utils.logger import get_logger

logger = get_logger()

@dataclass
class Command:
    name: str
    args: List[str]

class CodeAgent:
    """Generic base class for Code Agents."""
    def __init__(self, repo_url: str, branch: str) -> None:
        self.repo_url = repo_url
        self.branch = branch
        self.path_agent_workspace = self._setup_workspace(repo_url, branch)

    def _setup_workspace(self, repo_url: str, branch: str) -> Path:
        """Setup agent workspace - clone, branch, etc."""
        # TODO: Implement workspace setup using gitpython library. For now undockerized repository clone to operate on.
        # Clone to ~/agent_sandbox/<repo_name> using appropriate git clone command.
        # Handle both HTTPS and SSH URLs. Create/switch to branch as needed. User must be able to git push from agent workspace if SSH selected.
        # Try dependency installation if requirements.txt or pyproject.toml found. If fails, pass silently & let agent handle it.
        return Path.home() / "agent_sandbox" / <repo_name>  # Placeholder repo name must be inferred from git repository

    def define_command(self) -> Command:
        """Return Streamlit UI for definition of agent-specific command."""
        raise NotImplementedError # unimplemented baseclass method

    def _execute_agent_command(self, command: Command) -> None:
        """Execute the agent command in its workspace using subprocess."""
        # NOTE: Using st.spinner direct the user to commandline or agent web UI instead of streaming output in Streamlit
        subprocess.run([command.name, *command.args],capture_output=False, cwd=self.path_agent_workspace)

    def run(self, task: str, command: Command) -> None:
        """Appends the task to the command according to agent command syntax."""
        raise NotImplementedError  # unimplemented baseclass method

    def get_diff(self, workspace: Path) -> str:
        # TODO: get git diff from the agent workspace & return as string
        diff = subprocess.run(["git", "diff"], cwd=workspace, capture_output=True, text=True)
        return diff.stdout


class AiderCodeAgent(CodeAgent):
    """Autonomous Aider Code Agent."""

    def run(self, task: str, command: Command) -> None:
        """Executes the agent loop, yielding UI events."""
        # TODO: prepare environment variables eg OLLAMA_API_BASE=https://ollama.com:443
        # add task to command depending on agent type
        command.args.extend(["--message", task])
        self._execute_agent_command(command)

    def define_command(self) -> Command:
        """Return Streamlit UI for definition of agent-specific command."""
        st.markdown("## Select Architect Model")
        st.session_state.model_architect = model_selector(key="code_agent_architect")
        st.markdown("## Select Editor Model")
        st.session_state.model_editor = model_selector(key="code_agent_editor")
        st.markdown("---")

        st.markdown("## Select Flags for Aider Command")

        # TODO: Create user-friendly Streamlit controls for common aider flags:
        # - Selectbox for enum flags (--edit-format: diff/whole/udiff)
        # - Selectbox for (map-tokens: 1024-8192 in multiples of powers of 2)

        # TODO: use st.multiselect for --no-commit, --browser, --no-stream, --edit-format diff
        # TODO: think about +5 most relevant flags for aider command & add them to multiselect

        return Command(
            name="aider",
            args=[
                "--architect",
                st.session_state.model_architect,
                "--editor-model",
                st.session_state.model_editor,
                "--repo",
                st.session_state.repo_url,
                "--branch",
                st.session_state.branch,
                # add other flags from multiselect here
            ],
        ) 


def main() -> None:
    st.set_page_config(page_title="Agent-in-a-Box", layout="wide")

    with st.sidebar:

        st.markdown("## Select Repository to work on")
        st.session_state.repo_url = st.text_input("GitHub Repository URL")
        st.session_state.branch = st.text_input("Branch", value="main")
        st.markdown("---")
        if not st.session_state.repo_url or not st.session_state.branch:
            st.warning("Please provide both Repository URL and Branch to proceed.")
            st.stop()

        # Get subclasses and their names
        subclasses = CodeAgent.__subclasses__()
        subclass_dict = {cls.__name__: cls for cls in subclasses}  # Map name -> class
        subclass_names = list(subclass_dict.keys())

        # Store selected class name
        st.session_state.selected_agent = st.selectbox(
            "Select Code Agent",
            options=subclass_names,
            key="code_agent_selector"
        )

        # Retrieve and instantiate the actual class
        selected_agent: type[CodeAgent] = subclass_dict[st.session_state.selected_agent]
        selected_agent = selected_agent(
            repo_url=st.session_state.repo_url,
            branch=st.session_state.branch,
        )
        command = selected_agent.define_command()

    with st._bottom:
        task = st.chat_input("Assign a task to the agent...")

    if task:
        with st.chat_message("user"):
            st.markdown(task)

        with st.chat_message("assistant"):
            selected_agent.run(task=task, command=command)
            # wait for completion without streaming output
            diff = selected_agent.get_diff()
            st.markdown(diff, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
