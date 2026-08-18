"""Command-line interface.

    dochealth extract <repo> <docs_dir> (--config FILE | --no-config) --out FILE

This module is what the installed `dochealth` executable imports. That file is
a short shim pip generates from the `[project.scripts]` line in pyproject.toml;
it calls main() and exits with whatever it returns.
"""
import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

from dochealth import extract


def load_config(path: str) -> dict:
    """Import a CONFIG dict from a .py file whose path is only known at runtime.

    A plain `import kubernetes_config` cannot work here: import statements are
    resolved against sys.path at compile time, and this path arrives as a string
    when the user types it. So we drive the import machinery by hand — the three
    steps `import` normally does for you:

      1. spec_from_file_location  — describe the module: a name, and where it lives
      2. module_from_spec         — build the empty module object
      3. spec.loader.exec_module  — execute the file's code inside it

    After step 3 the module's globals hold whatever the file defined, so
    CONFIG is an ordinary attribute lookup.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise SystemExit(f"config file not found: {config_path}")

    spec = importlib.util.spec_from_file_location("dochealth_user_config", config_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"not an importable Python file: {config_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    config = getattr(module, "CONFIG", None)
    if config is None:
        raise SystemExit(f"{config_path} defines no CONFIG dict")
    return config


def build_parser() -> argparse.ArgumentParser:
    """Describe the command line. Builds the parser; main() does the parsing."""
    parser = argparse.ArgumentParser(prog="dochealth", description="Documentation health metrics CSV generator.")
    subs = parser.add_subparsers(dest="command", required=True)
    extract_parser = subs.add_parser("extract", help="Extract the documentation.")
    extract_parser.add_argument("repo", type=Path, help="The repo folder.")
    extract_parser.add_argument("docs_dir", help="The documentation folder.")
    group = extract_parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--config", help="The location of the config file.")
    group.add_argument("--no-config", action="store_true", help="Choose not to pass a config file.")
    extract_parser.add_argument("--out", required=True, type=Path, metavar="FILE", help="Name your output CSV file to store the metrics.")
    # No arguments: the dashboard reads every metrics-*.csv in the working
    # directory and switches between them in its own sidebar, so there is
    # nothing left for a flag to decide.
    subs.add_parser("dashboard", help="Open the dashboard on the metrics-*.csv "
                                      "files in the current directory.")
    return parser


def run_extract(args: argparse.Namespace) -> int:
    """Extract metrics for one corpus and write them to args.out."""
    config = load_config(args.config) if args.config else None
    if args.no_config:
        print("Noise filtering is disabled. Noisy commits might affect your metrics.", file=sys.stderr)
    df = extract.extract_docs(args.repo, args.docs_dir, config)
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df)} rows to {args.out}.", file=sys.stderr)
    return 0


def run_dashboard() -> int:
    """Launch the Streamlit dashboard on the packaged app.py.

    `sys.executable -m streamlit` rather than a bare `streamlit`: the bare name
    resolves through PATH and could start some other environment's Streamlit
    against this environment's code. sys.executable is by definition the
    interpreter running this command.

    app.py IS resolved relative to __file__ — unlike the metrics CSVs it reads.
    The app ships inside the package; the CSVs are user data that live wherever
    `--out` put them. The subprocess inherits this process's working directory,
    which is exactly how the app finds them.
    """
    if importlib.util.find_spec("streamlit") is None:
        raise SystemExit(
            "streamlit is not installed. It is an optional dependency, because "
            "`dochealth extract` has no use for it:\n\n"
            "    pip install -e '.[dashboard]'"
        )
    app_path = Path(__file__).parent / "app.py"
    # Streamlit's own exit code is returned rather than a bare 0, so Ctrl-C or a
    # failed launch does not report success to the shell.
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app_path)])


def main(argv: list[str] | None = None) -> int:
    """Parse, dispatch, return an exit code.

    argv defaults to None so argparse reads sys.argv itself in real use, while
    tests can pass a list of strings directly. Return 0 for success; anything
    non-zero tells the shell the command failed.
    """
    args = build_parser().parse_args(argv)
    if args.command == "dashboard":
        return run_dashboard()
    return run_extract(args)