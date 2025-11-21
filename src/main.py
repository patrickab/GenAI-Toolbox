import streamlit as st

from src.lib.streamlit_helper import (
    apply_custom_css,
    init_session_state,
)

PAGES = {
    "Select Page": [
        st.Page("pages/Gigachad_Bot.py"),
        st.Page("pages/PDF_Workspace.py"),
    ],
}

def main() -> None:
    """Main function to run the Streamlit app."""

    st.set_page_config(page_title="Gigachad-Bot", page_icon=":robot:", layout="wide")

    apply_custom_css()
    init_session_state()

if __name__ == "__main__":
    pages = st.navigation(pages=PAGES, position="top")
    pages.run()
    main()
