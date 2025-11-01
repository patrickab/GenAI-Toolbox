from __future__ import annotations
import streamlit as st

from src.lib.streamlit_helper import application_side_bar, chat_interface, init_session_state


def main() -> None:
    """Main function to run the Streamlit app."""

    st.set_page_config(page_title="Learning Assistant", page_icon=":robot:", layout="wide", initial_sidebar_state="collapsed")

    init_session_state()
    application_side_bar()
    chat_interface()


if __name__ == "__main__":
    main()
