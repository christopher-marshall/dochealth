"""Unit tests for the command-line interface — run with: pytest

These pin two things worth not losing.

The first is a DECISION, not a behaviour: `--config` and `--no-config` are
mutually exclusive and one is required. DECISIONS.md says why — building
the docusaurus frame with no config returned a full, healthy-looking DataFrame
whose days_since_update was quietly wrong on most pages, caught only because
describe() happened to show a suspicious median. The rejection tests below are
what stop someone "simplifying" that constraint away later without noticing
they've reopened the hole.

The second is `load_config`, which drives Python's import machinery by hand
because the config path arrives as a runtime string. That is fiddly enough to
deserve pinning, and its failure modes (missing file, no CONFIG) are the ones a
user will actually hit.

main() itself is not tested end to end: it calls extract_docs, which needs a
real git repo with markdown in it, and that is what `check.py` is for. The argv
parameter still exists so the parser can be driven from here without a
subprocess.
"""
import sys
from pathlib import Path

import pytest

from dochealth import cli


# ── the --config / --no-config decision ───────────────────────────────────────

VALID = ["extract", "website", "content/en/docs/concepts", "--out", "x.csv"]


def test_neither_config_flag_is_rejected():
    """Configlessness has to be said out loud — see the module docstring."""
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(VALID)
    assert excinfo.value.code == 2


def test_both_config_flags_are_rejected():
    """Passing both is incoherent: the tool cannot filter and not filter."""
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(VALID + ["--config", "k.py", "--no-config"])
    assert excinfo.value.code == 2


def test_no_config_alone_is_accepted():
    args = cli.build_parser().parse_args(VALID + ["--no-config"])
    assert args.no_config is True
    assert args.config is None


def test_config_alone_is_accepted():
    args = cli.build_parser().parse_args(VALID + ["--config", "kubernetes_config.py"])
    assert args.config == "kubernetes_config.py"
    assert args.no_config is False


# ── the rest of the argument surface ──────────────────────────────────────────

def test_out_is_required():
    """--out has no default on purpose: extraction takes minutes, so neither a
    surprise file nor a terminal full of CSV is an acceptable accident."""
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(
            ["extract", "website", "content/en/docs/concepts", "--no-config"])
    assert excinfo.value.code == 2


def test_docs_dir_is_positional_and_stays_a_string():
    """find_doc_files annotates docs_dir as str and joins it onto repo_path;
    it is a fragment of a path, not a path in its own right."""
    args = cli.build_parser().parse_args(VALID + ["--no-config"])
    assert args.docs_dir == "content/en/docs/concepts"
    assert isinstance(args.docs_dir, str)


def test_subcommand_is_required():
    """`dochealth` with no verb should explain itself, not do something."""
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args([])
    assert excinfo.value.code == 2


# ── load_config ───────────────────────────────────────────────────────────────

def test_load_config_returns_the_config_dict(tmp_path):
    config_file = tmp_path / "some_config.py"
    config_file.write_text("import re\nCONFIG = {'noise_re': re.compile(r'^chore:')}\n")
    config = cli.load_config(str(config_file))
    assert config["noise_re"].pattern == r"^chore:"


def test_load_config_rejects_a_missing_file(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        cli.load_config(str(tmp_path / "nope.py"))
    assert "not found" in str(excinfo.value)


def test_load_config_rejects_a_file_without_a_config_dict(tmp_path):
    """A config file that imports cleanly but defines nothing is the quiet
    failure this whole constraint is about: it would otherwise degrade to
    None and run unfiltered."""
    config_file = tmp_path / "empty_config.py"
    config_file.write_text("NOISE_RE = None\n")
    with pytest.raises(SystemExit) as excinfo:
        cli.load_config(str(config_file))
    assert "no CONFIG" in str(excinfo.value)


def test_load_config_does_not_pollute_sys_modules(tmp_path):
    """spec_from_file_location + exec_module deliberately skips sys.modules, so
    two different configs in one process cannot shadow each other."""
    import sys
    config_file = tmp_path / "another_config.py"
    config_file.write_text("CONFIG = {}\n")
    cli.load_config(str(config_file))
    assert "dochealth_user_config" not in sys.modules


# ── the dashboard subcommand ──────────────────────────────────────────────────

def test_dashboard_takes_no_arguments():
    """It reads every metrics-*.csv in the working directory and switches
    between them in its own sidebar, so there is nothing for a flag to decide."""
    args = cli.build_parser().parse_args(["dashboard"])
    assert args.command == "dashboard"


def test_dashboard_launches_streamlit_on_the_packaged_app(monkeypatch):
    """The app is located relative to __file__ because it ships in the package —
    the CSVs it reads are not.
    sys.executable rather than a bare `streamlit` so PATH cannot substitute a
    different environment's Streamlit for this one's."""
    seen = {}

    def fake_call(cmd):
        seen["cmd"] = cmd
        return 0

    monkeypatch.setattr(cli.subprocess, "call", fake_call)
    assert cli.main(["dashboard"]) == 0
    assert seen["cmd"][:4] == [sys.executable, "-m", "streamlit", "run"]
    assert seen["cmd"][4] == str(Path(cli.__file__).parent / "app.py")


def test_dashboard_propagates_streamlits_exit_code(monkeypatch):
    """Returning a bare 0 would report success to the shell after a failed
    launch, which is how a broken dashboard looks fine in CI."""
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd: 1)
    assert cli.main(["dashboard"]) == 1


def test_dashboard_says_how_to_install_streamlit_when_it_is_missing(monkeypatch):
    """streamlit is an optional extra: `dochealth extract` never imports it, so
    a CSV-only install is legitimate and should get advice, not a traceback."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(SystemExit) as excinfo:
        cli.run_dashboard()
    assert "dashboard" in str(excinfo.value)
