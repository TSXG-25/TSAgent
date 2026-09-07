"""Offline security boundary tests for command, Python, and file execution."""

import agent.sandbox as sandbox


def test_local_execution_is_fail_closed_without_docker(monkeypatch):
    monkeypatch.setattr(sandbox, "_check_docker", lambda: False)
    monkeypatch.delenv("TSAGENT_ALLOW_LOCAL_EXECUTION", raising=False)

    result = sandbox.run_in_sandbox("echo should-not-run")

    assert "本地执行默认关闭" in result


def test_local_execution_timeout_terminates_process_group(monkeypatch):
    monkeypatch.setattr(sandbox, "_check_docker", lambda: False)
    monkeypatch.setenv("TSAGENT_ALLOW_LOCAL_EXECUTION", "1")

    result = sandbox.run_in_sandbox(
        "python3 -c 'import time; time.sleep(2)'", timeout=0.1
    )

    assert "超时" in result


def test_python_tool_rejects_file_and_process_escape(monkeypatch):
    monkeypatch.setattr(sandbox, "_check_docker", lambda: False)
    monkeypatch.setenv("TSAGENT_ALLOW_LOCAL_EXECUTION", "1")

    from tools.python import run_python

    assert "禁止导入" in run_python("import os")
    assert "禁止调用" in run_python("open('outside.txt', 'w')")


def test_filesystem_tools_reject_paths_outside_workspace():
    from tools.filesystem import list_directory, read_file, write_file

    assert "超出 workspace" in read_file("../outside.txt")
    assert "超出 workspace" in write_file("../outside.txt", "blocked")
    assert "超出 workspace" in list_directory("../")


def test_sensitive_files_are_not_read_or_written():
    from tools.filesystem import read_file, write_file

    assert "敏感文件" in read_file(".env")
    assert "敏感文件" in write_file(".env", "OPENAI_API_KEY=should-not-write")


def test_sensitive_command_is_blocked_before_sandbox(monkeypatch):
    monkeypatch.setattr(sandbox, "_check_docker", lambda: False)
    monkeypatch.setenv("TSAGENT_ALLOW_LOCAL_EXECUTION", "1")

    result = sandbox.run_in_sandbox("printf '%s' \"$OPENAI_API_KEY\"")

    assert "敏感信息访问" in result


def test_sensitive_values_are_redacted():
    from agent.security import redact_sensitive_text

    text = "OPENAI_API_KEY=sk-proj-12345678901234567890"
    redacted = redact_sensitive_text(text)

    assert "1234567890" not in redacted
    assert "REDACTED" in redacted


def test_short_term_storage_redacts_sensitive_user_and_assistant_text(
    monkeypatch, tmp_path
):
    import json

    from agent.memory import short_term

    monkeypatch.setattr(short_term, "ST_DIR", tmp_path)
    secret = "sk-test-human-acceptance-123"

    short_term.add_exchange(
        "security-short-term",
        f"我的 API key 是 {secret}",
        f"我不会保存 {secret}",
    )

    record = json.loads(
        (tmp_path / "security-short-term.json").read_text(encoding="utf-8")
    )[0]
    assert secret not in json.dumps(record, ensure_ascii=False)
    assert "[REDACTED]" in record["user"]
    assert "[REDACTED]" in record["assistant"]
    assert secret not in short_term.get_history("security-short-term")


def test_office_binary_writes_are_explicitly_rejected():
    from tools.filesystem import write_file

    result = write_file("output/report.xlsx", "not-a-real-xlsx")

    assert "Office 二进制" in result
