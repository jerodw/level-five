from pathlib import Path

import harness_config


def test_quoted_values_are_unquoted(tmp_path: Path):
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "config.yaml").write_text(
        'project: sample\n'
        'test_command: "echo ok"\n'
        'allowed_tools:\n'
        '  - "Bash(.venv/bin/python:*)"\n'
        "  - 'Bash(chmod:*)'\n"
        '  - Bash(ls:*)\n',
        encoding="utf-8",
    )
    config = harness_config.load_config(tmp_path)
    assert config["test_command"] == "echo ok"
    assert config["allowed_tools"] == [
        "Bash(.venv/bin/python:*)",
        "Bash(chmod:*)",
        "Bash(ls:*)",
    ]
