import os
from pathlib import Path
from typing import Optional

import polars as pl
from rag_database.dataclasses import RAGIngestionPayload, RAGQuery
from rag_database.rag_config import MODEL_CONFIG, DatabaseKeys
from rag_database.rag_database import RagDatabase
import streamlit as st

from src.config import (
    DEFAULT_EMBEDDING_MODEL,
    DIRECTORY_EMBEDDINGS,
    DIRECTORY_RAG_INPUT,
)
from src.lib.streamlit_helper import nyan_cat_spinner

DATABASE_LABAL_OBSIDIAN = "obsidian"

def init_rag_workspace() -> None:
    """Initialize RAG workspace session state variables."""
    if "rag_databases" not in st.session_state:
        st.session_state.rag_databases = {}

def rag_sidebar() -> None:
    """RAG Workspace Sidebar for RAG Database selection & initialization."""

    with st.sidebar:

        st.session_state.selected_embedding_model = st.selectbox(
            "Select Embedding Model",
            options=list(MODEL_CONFIG.keys()),
            index=list(MODEL_CONFIG.keys()).index(DEFAULT_EMBEDDING_MODEL),
        )

        available_database_payloads = os.listdir(DIRECTORY_RAG_INPUT)
        available_database_embeddings = os.listdir(DIRECTORY_EMBEDDINGS)

        # for all available database embeddings check for all embedding models if they are contained as string & try to trunctate _<embedding_model>.parquet to receive the label # noqa
        unique_database_labels = set()
        for embedding_file in available_database_embeddings:
            for model_name in MODEL_CONFIG:
                suffix = f"_{model_name}.parquet"
                if embedding_file.endswith(suffix):
                    unique_database_labels.add(embedding_file.removesuffix(suffix))
                    break

        available_database_embeddings = sorted(unique_database_labels)
        options = set(available_database_payloads + available_database_embeddings)

        st.session_state.selected_rag_database = st.selectbox(
            "Select RAG Database",
            options=options,
            index=0,
        )
        if st.button("Initialize RAG Database", key="load_rag_db"):
            with nyan_cat_spinner():
                selection = st.session_state.selected_rag_database
                model = st.session_state.selected_embedding_model

                payload_path = Path(f"{DIRECTORY_RAG_INPUT}/{selection}/{selection}_ingestion_payload.parquet")
                embedding_path = Path(f"{DIRECTORY_EMBEDDINGS}/{selection}/{selection}_embeddings.parquet")

                if payload_path.exists() and not embedding_path.exists():
                    payload = RAGIngestionPayload.from_parquet(payload_path)
                    rag_db = generate_rag_database(selected_model=model,selected_database=selection, payload=payload)
                else:
                    rag_db = load_rag_database(model=model, label=selection)

            # Create nested dictionary structure to allow different embeddings for the same documents - will be used for benchmarking
            st.session_state.rag_databases.setdefault(selection, {})
            st.session_state.rag_databases[selection][model] = rag_db

        with st.expander("RAG Databases in Memory", expanded=True):

            for label, models_dict in st.session_state.rag_databases.items():
                with st.expander(f"**Label**:{label}", expanded=False):
                    for model, rag_db in models_dict.items():
                        with st.expander(f"**Model**:{model}", expanded=False), st.expander("Inspect Database", expanded=False):
                            st.dataframe(rag_db.vector_db.database)
                            if st.button("Store Database", key=f"store_rag_db_{label}_{model}"):
                                parquet_embeddings = f"{DIRECTORY_EMBEDDINGS}/{selection}.parquet"
                                rag_db.vector_db.database.write_parquet(parquet_embeddings) # noqa
                                st.success(f"Stored RAG Database '{label}' to {parquet_embeddings}")

@st.cache_resource
def load_rag_database(model: str, label: str, embedding_dimensions: Optional[int]=None) -> RagDatabase:
    """
    Initialize RAG Database with .md documents.
    Loads existing embeddings if available.
    Embed new documents & update database accordingly.
    """
    if embedding_dimensions is None:
        embedding_dimensions = MODEL_CONFIG[model]["dimensions"]

    parquet_embeddings = f"{DIRECTORY_EMBEDDINGS}/{label}_{model}.parquet"
    if os.path.exists(parquet_embeddings):
        # Load existing RAG database
        rag_dataframe = pl.read_parquet(parquet_embeddings)
        rag_db = RagDatabase(model=model, database=rag_dataframe)
    else:
        raise FileNotFoundError(f"RAG database parquet file not found at {parquet_embeddings}. Please create the RAG database first.")

    return rag_db

@st.cache_data
def generate_rag_database(
    selected_model: str,
    selected_database: str,
    payload_path: Optional[str]=None,
    payload: Optional[RAGIngestionPayload]=None
    ) -> RagDatabase:
    """Generate a RAG database from a payload parquet file or RAGIngestionPayload."""

    if payload_path is None and payload is None:
        raise ValueError("Either payload_path or payload must be provided.")

    if payload is None and payload_path is not None:
        payload = RAGIngestionPayload.from_parquet(payload_path)

    embedding_dimensions = MODEL_CONFIG[selected_model]["dimensions"]

    embedding_path = Path(f"{DIRECTORY_EMBEDDINGS}/{selected_database}_{selected_model}_embeddings.parquet")
    if embedding_path.exists():
        rag_db = RagDatabase.from_parquet(embedding_path)
    else:
        embedding_dimensions = MODEL_CONFIG[selected_model]["dimensions"]
        rag_db = RagDatabase(model=selected_model, embedding_dimensions=embedding_dimensions)

    rag_db.add_documents(
        payload=payload,
        task_type="RETRIEVAL_DOCUMENT",
    )

    return rag_db

def rag_workspace() -> None:
    """RAG Workspace main function."""
    st.title("RAG Workspace")
    with st._bottom:
        prompt = st.chat_input("Send a message", key="chat_input")

    if prompt:
        rag_db: RagDatabase = st.session_state.rag_databases[st.session_state.selected_rag_database][st.session_state.selected_embedding_model] # noqa
        query = RAGQuery(query=prompt, k_documents=5)
        rag_response = rag_db.rag_process_query(rag_query=query)
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            documents = rag_response.to_polars()
            for doc in documents.iter_rows(named=True):
                with st.expander(f"**Similarity**: {doc[DatabaseKeys.KEY_SIMILARITIES]:.2f}   -  **Title**: {doc[DatabaseKeys.KEY_TITLE]}"): # noqa
                    st.markdown(doc[DatabaseKeys.KEY_TXT_RETRIEVAL])

if __name__ == "__main__":
    st.set_page_config(page_title="RAG Workspace", page_icon=":robot:", layout="wide")
    init_rag_workspace()
    rag_sidebar()
    rag_workspace()
