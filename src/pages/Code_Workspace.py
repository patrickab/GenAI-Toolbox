"""Streamlit tab to tokenize codebases into chunks."""

import ast
from itertools import cycle
import math
from pathlib import Path
import subprocess
from typing import Iterable

import networkx as nx
import polars as pl
from rag_database.rag_config import DatabaseKeys
import streamlit as st
from streamlit_agraph import Config, Edge, Node, agraph


class DatabaseKeysExt(DatabaseKeys):
    """Extended database keys for code chunk metadata."""

    KEY_CALLS = "calls"
    KEY_CALLED_BY = "called_by"
    KEY_MODULE = "module"

def render_call_relations(df: pl.DataFrame, idx: int) -> None:
    """
    Render expandable sections for the selected chunk's custom calls
    and the chunks that call it (called_by). Each related chunk can
    be expanded to inspect its source code.
    """
    if not (0 <= idx < df.height):
        st.warning("Index out of range.")
        return

    row = df.row(idx, named=True)
    name_to_row_index: dict[str, int] = {df.row(i, named=True)[DatabaseKeysExt.KEY_TITLE]: i for i in range(df.height)}

    def _render_group(title: str, names: Iterable[str]) -> None:
        with st.expander(f"{title} ({len(list(names))})", expanded=False):
            for n in sorted(set(names)):
                if n in name_to_row_index:
                    r_idx = name_to_row_index[n]
                    r = df.row(r_idx, named=True)
                    with st.expander(f"{r['kind']} {n}", expanded=False):
                        st.code(r[DatabaseKeysExt.KEY_TXT_RETRIEVAL])
                        st.caption(f"Module: {r[DatabaseKeysExt.KEY_MODULE]}")
                else:
                    st.write(f"{n} (not found)")

    _render_group("Calls", row[DatabaseKeysExt.KEY_CALLS])
    _render_group("Called by", row[DatabaseKeysExt.KEY_CALLED_BY])


def _list_python_files(repo_path: Path) -> list[Path]:
    """List Python files not ignored by .gitignore in *repo_path*."""
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=repo_path, check=True).stdout.splitlines()
    all_files = {Path(f) for f in tracked}
    return [repo_path / f for f in all_files if f.suffix == ".py" and f != "config.py"]


def _find_calls(node: ast.AST, custom_names: set[str]) -> list[str]:
    """Return names of custom functions/classes called within *node*."""

    calls: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, call: ast.Call) -> None:  # noqa: N802 - ast naming
            func = call.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name and name in custom_names:
                calls.add(name)
            self.generic_visit(call)

    Visitor().visit(node)
    return sorted(calls)


def _build_dataframe(repo_path: Path) -> pl.DataFrame:
    """Build a Polars DataFrame of code chunks for *repo_path* with call info."""

    files = _list_python_files(repo_path)

    # Gather names of custom classes and top-level functions in the repo
    custom_names: set[str] = set()
    parsed_files: dict[Path, tuple[ast.Module, list[str]]] = {}
    for file in files:
        text = file.read_text()
        tree = ast.parse(text)
        parsed_files[file] = (tree, text.splitlines())
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                custom_names.add(node.name)

    chunks: list[dict[str, object]] = []
    for file, (tree, lines) in parsed_files.items():
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                start = node.lineno - 1
                end = node.end_lineno
                code = "\n".join(lines[start:end])
                module_path = file.relative_to(repo_path).with_suffix("")
                module = ".".join(module_path.parts)
                full_name = f"{module}.{node.name}"
                # Use DataFrame Keys only for RAG intermodular consistencies - full name, loc, docstring are not relevant for rag
                chunks.append(
                    {
                        DatabaseKeysExt.KEY_MODULE: module,
                        DatabaseKeysExt.KEY_TITLE: node.name,
                        "full_name": full_name,
                        "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                        DatabaseKeysExt.KEY_TXT_RETRIEVAL: code,
                        DatabaseKeysExt.KEY_CALLS: _find_calls(node, custom_names),
                        "loc": end - start,  # type: ignore
                        "docstring": ast.get_docstring(node) or "",
                    }
                )

    # Determine which chunks are called by others
    called_by_map: dict[str, list[str]] = {chunk[DatabaseKeysExt.KEY_TITLE]: [] for chunk in chunks}  # type: ignore
    for chunk in chunks:
        for callee in chunk[DatabaseKeysExt.KEY_CALLS]:  # type: ignore
            if callee in called_by_map:
                called_by_map[callee].append(chunk[DatabaseKeysExt.KEY_TITLE])  # type: ignore

    for chunk in chunks:
        chunk[DatabaseKeysExt.KEY_CALLED_BY] = sorted(called_by_map.get(chunk[DatabaseKeysExt.KEY_TITLE], []))  # type: ignore

    df = pl.DataFrame(chunks)
    return df.select(
        [
            DatabaseKeysExt.KEY_MODULE,
            DatabaseKeysExt.KEY_TITLE,
            "docstring",
            DatabaseKeysExt.KEY_CALLS,
            DatabaseKeysExt.KEY_CALLED_BY,
            "loc",
            "kind",
            DatabaseKeysExt.KEY_TXT_RETRIEVAL,
            "full_name",
        ]
    )


def render_codebase_tokenizer() -> None:
    """Render the Codebase Tokenizer tab."""
    st.subheader("Codebase Tokenizer")

    def _find_git_repos(base: Path) -> list[Path]:
        """Return directories in *base* that contain a .git folder."""
        return [p for p in base.iterdir() if (p / ".git").exists() and p.is_dir()]

    repos = _find_git_repos(Path.home())
    if repos:
        repo = st.selectbox(
            "Repository",
            repos,
            format_func=lambda p: p.name,
            index=None,
            placeholder="Select a repository",
        )
        if repo is not None:
            selected = str(repo)
            if st.session_state.get("selected_repo") != selected:
                st.session_state.selected_repo = selected
    else:
        st.sidebar.info("No Git repositories found")

    repo_path_str = st.session_state.get("selected_repo")
    if not repo_path_str:
        st.info("Select a repository from the sidebar.")
        return
    repo = Path(repo_path_str)

    if "code_chunks_repo" not in st.session_state or st.session_state.code_chunks_repo != str(repo):
        st.session_state.code_chunks = _build_dataframe(repo)
        st.session_state.code_chunks_repo = str(repo)

    df = st.session_state.code_chunks
    display_df = df.select(
        [DatabaseKeysExt.KEY_MODULE, DatabaseKeysExt.KEY_TITLE, "docstring", DatabaseKeysExt.KEY_CALLS, DatabaseKeysExt.KEY_CALLED_BY, "loc"]
    )
    st.dataframe(display_df.to_pandas())

    with st.expander("Display chunk by index"):
        if df.height:
            idx = st.number_input("Chunk index", min_value=0, max_value=df.height - 1, step=1)
            render_call_relations(df, idx)
            st.code(df[idx, DatabaseKeysExt.KEY_TXT_RETRIEVAL])


def render_code_graph() -> None:
    """Render a graph view of the codebase using agraph."""

    st.markdown("<h1 class='graph-title'>Codebase Graph</h1>", unsafe_allow_html=True)

    repo_path_str = st.session_state.get("selected_repo")
    if not repo_path_str:
        st.markdown(
            "<div class='graph-card'><h2>No repo selected</h2><p>Select a repository from the sidebar to see the graph.</p></div>",
            unsafe_allow_html=True,
        )
        return
    repo = Path(repo_path_str)

    if "code_chunks_repo" not in st.session_state or st.session_state.code_chunks_repo != str(repo):
        st.session_state.code_chunks = _build_dataframe(repo)
        st.session_state.code_chunks_repo = str(repo)

    df = st.session_state.code_chunks
    graph_type = st.radio("Graph type", ["Hierarchy", "Louvain"], horizontal=True)

    cache_key = (str(repo), graph_type)
    cache = st.session_state.get("graph_cache")
    needs_build = st.session_state.get("graph_cache_key") != cache_key
    placeholder = st.empty()

    if needs_build:
        G = nx.DiGraph()
        for row in df.iter_rows(named=True):
            src = row["full_name"]
            G.add_node(src)
            for callee in row[DatabaseKeysExt.KEY_CALLS]:
                for m in df.filter(pl.col(DatabaseKeysExt.KEY_TITLE) == callee).iter_rows(named=True):
                    G.add_edge(src, m["full_name"])

        communities = list(nx.algorithms.community.louvain_communities(G.to_undirected()))
        community_map = {n: idx for idx, comm in enumerate(communities) for n in comm}

        palette = [
            "#ff6b6b",
            "#ffd93d",
            "#6bcb77",
            "#4d96ff",
            "#f06595",
            "#f8961e",
        ]
        color_cycle = cycle(palette)
        module_colors = {m: next(color_cycle) for m in df[DatabaseKeysExt.KEY_MODULE].unique().to_list()}
        community_colors = {i: next(color_cycle) for i in range(len(communities))}

        nodes: list[Node] = []
        edges: list[Edge] = []
        for row in df.iter_rows(named=True):
            full_id = row["full_name"]
            color = (
                module_colors[row[DatabaseKeysExt.KEY_MODULE]]
                if graph_type == "Hierarchy"
                else community_colors.get(community_map.get(full_id, 0), "#AEC6CF")
            )
            title = f"{row[DatabaseKeysExt.KEY_TITLE]}\n{row['docstring']}\nModule: {row[DatabaseKeysExt.KEY_MODULE]}\nLOC: {row['loc']}"
            nodes.append(
                Node(
                    id=full_id,
                    label=row[DatabaseKeysExt.KEY_TITLE],
                    size=max(int(math.log1p(row["loc"]) * 10), 10),
                    color=color,
                    title=title,
                    font={"size": 12},
                    borderWidth=1,
                    borderWidthSelected=3,
                )
            )
            for callee in row[DatabaseKeysExt.KEY_CALLS]:
                for m in df.filter(pl.col(DatabaseKeysExt.KEY_TITLE) == callee).iter_rows(named=True):
                    edges.append(
                        Edge(
                            source=full_id,
                            target=m["full_name"],
                            color="rgba(255,255,255,0.25)",
                            smooth=True,
                        )
                    )

        full_name_to_idx = {df.row(i, named=True)["full_name"]: i for i in range(df.height)}
        st.session_state.graph_cache = {
            "nodes": nodes,
            "edges": edges,
            "full_name_to_idx": full_name_to_idx,
        }
        st.session_state.graph_cache_key = cache_key
    else:
        nodes = cache["nodes"]  # type: ignore
        edges = cache["edges"]  # type: ignore
        full_name_to_idx = cache["full_name_to_idx"]  # type: ignore

    config = Config(
        width="100%",
        height=600,
        directed=True,
        hierarchical=graph_type == "Hierarchy",
        physics=graph_type != "Hierarchy",
        backgroundColor="#1a1a1a",
        interaction={"hover": True, "navigationButtons": True, "zoomView": True}
    )

    placeholder.empty()

    selected = agraph(nodes=nodes, edges=edges, config=config)
    with st.expander("Details", expanded=True):
        key = selected or st.session_state.get("last_selected")
        if key and key in full_name_to_idx:
            st.session_state.last_selected = key
            idx = full_name_to_idx[key]
            d = df.row(idx, named=True)
            st.write(f"**Name:** {d[DatabaseKeysExt.KEY_TITLE]}")
            st.write(f"Module: {d[DatabaseKeysExt.KEY_MODULE]}")
            st.write(f"LOC: {d['loc']}")
            render_call_relations(df, idx)
            st.code(d[DatabaseKeysExt.KEY_TXT_RETRIEVAL])
        else:
            st.write("Select a node to see details.")

if __name__ == "__main__":
    render_codebase_tokenizer()
    st.markdown("---")
    render_code_graph()