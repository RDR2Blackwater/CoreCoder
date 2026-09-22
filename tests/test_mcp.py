"""MCP stdio servers: handshake, tool registration, calls, dying servers.

The fake server is a stdlib-only Python script run via sys.executable, so
these tests need no shell and run the same on Windows.
"""

import json
import logging
import sys
import time

import pytest

from corecoder import mcp
from corecoder.agent import Agent
from corecoder.demo import ScriptedLLM
from corecoder.hooks import Hooks
from corecoder.llm import LLMResponse, ToolCall
from corecoder.mcp import load_mcp_tools, shutdown_mcp_clients
from corecoder.permissions import Permission

FAKE_SERVER = """
import json, os, sys, time

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8", newline="\\n")

TOOLS = [
    {"name": "echo", "description": "Echo text back",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "crash", "description": "Take the server down mid-call",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "stall", "description": "Answer too slowly",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "fail", "description": "Report a tool-level error",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "structured", "description": "Return text plus structured data",
     "inputSchema": {"type": "object", "properties": {}}},
]

for line in sys.stdin:
    try:
        req = json.loads(line)
    except json.JSONDecodeError:
        continue
    if "id" not in req:
        continue  # notification, nothing to answer
    if "method" not in req:
        continue  # response to a server-initiated request
    method, params = req.get("method"), req.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18",
                  "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                  "serverInfo": {"name": "fake", "version": "0.1"}}
    elif method == "tools/list":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/message", "params": {}}) + "\\n")
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "method": "ping", "params": {}}) + "\\n")
        result = {"tools": TOOLS[:2], "nextCursor": "tools-2"} if not params.get("cursor") else {"tools": TOOLS[2:]}
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name == "crash":
            os._exit(1)
        if name == "stall":
            time.sleep(30)
        if name == "structured":
            result = {"content": [{"type": "text", "text": "plain"}],
                      "structuredContent": {"answer": 42}}
        else:
            content = [{"type": "text", "text": "echo: " + args["text"] if name == "echo" else "bad input near 42"}]
            result = {"content": content, "isError": name == "fail"}
    elif method == "resources/list":
        result = ({"resources": [{"uri": "file:///one.txt", "name": "one", "mimeType": "text/plain"}],
                   "nextCursor": "resources-2"} if not params.get("cursor") else
                  {"resources": [{"uri": "asset:///image.png", "name": "image", "mimeType": "image/png"}]})
    elif method == "resources/templates/list":
        result = {"resourceTemplates": [{"uriTemplate": "file:///{path}", "name": "file"}]}
    elif method == "resources/read":
        if params.get("uri") == "file:///one.txt":
            result = {"contents": [{"uri": "file:///one.txt", "mimeType": "text/plain", "text": "hello resource"}]}
        else:
            result = {"contents": [{"uri": params.get("uri"), "mimeType": "image/png", "blob": "YWJj"}]}
    elif method == "prompts/list":
        result = {"prompts": [{"name": "review", "description": "Review code",
                                "arguments": [{"name": "code", "required": True}]}]}
    elif method == "prompts/get":
        result = {"description": "Review the supplied code",
                  "messages": [{"role": "user", "content": {"type": "text", "text": "Review: " + params.get("arguments", {}).get("code", "")}}]}
    else:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32601, "message": "no such method"}}) + "\\n")
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}) + "\\n")
    sys.stdout.flush()
"""

BLOCKED_WRITER_SERVER = """
import json, sys, time
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8", newline="\\n")
marker = sys.argv[1]
for line in sys.stdin:
    req = json.loads(line)
    if "id" not in req:
        if req.get("method") == "notifications/cancelled":
            with open(marker, "a", encoding="utf-8") as f:
                f.write("cancel:" + str(req.get("params", {}).get("requestId")) + "\\n")
        continue
    if req.get("method") == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "blocked", "version": "0.1"}}
    elif req.get("method") == "tools/list":
        result = {"tools": [{"name": "sink", "inputSchema": {"type": "object",
                  "properties": {"payload": {"type": "string"}}, "required": ["payload"]}}]}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}) + "\\n")
        sys.stdout.flush()
        time.sleep(1)
        continue
    elif req.get("method") == "tools/call":
        with open(marker, "a", encoding="utf-8") as f:
            f.write(req.get("params", {}).get("arguments", {}).get("payload", "")[:5] + "\\n")
        result = {"content": [{"type": "text", "text": "accepted"}]}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}) + "\\n")
    sys.stdout.flush()
"""


@pytest.fixture
def server_script(tmp_path):
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    return script


@pytest.fixture
def mcp_config(tmp_path, server_script):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"fake": {
        "command": sys.executable, "args": [str(server_script)]}}}), encoding="utf-8")
    return cfg


@pytest.fixture(autouse=True)
def _close_clients():
    yield
    shutdown_mcp_clients()


def _echo_call(call_id="c1", text="hi"):
    return ToolCall(id=call_id, name="mcp__fake__echo", arguments={"text": text})


def test_resolve_command_uses_platform_path_resolution(monkeypatch):
    seen = {}

    def fake_which(command, path=None):
        seen["path"] = path
        return f"resolved/{command}.cmd"

    monkeypatch.setattr(mcp.shutil, "which", fake_which)

    assert mcp._resolve_command("npx", "server-specific-path") == "resolved/npx.cmd"
    assert seen["path"] == "server-specific-path"


def _agent(script, tools, **kwargs):
    return Agent(llm=ScriptedLLM(script), tools=tools, **kwargs)


def test_handshake_registers_each_remote_tool(mcp_config):
    tools = load_mcp_tools(mcp_config)
    assert {t.name for t in tools} == {
        "mcp__fake__echo", "mcp__fake__crash", "mcp__fake__stall", "mcp__fake__fail",
        "mcp__fake__structured", "mcp__fake__resources_list",
        "mcp__fake__resource_templates_list", "mcp__fake__resource_read",
        "mcp__fake__prompts_list", "mcp__fake__prompt_get"}
    echo = next(t for t in tools if t.name == "mcp__fake__echo")
    assert echo.description == "Echo text back"
    assert echo.schema()["function"]["parameters"]["properties"]["text"] == {"type": "string"}


def test_call_round_trip_returns_text_content(mcp_config):
    agent = _agent(
        [LLMResponse(tool_calls=[_echo_call()]), LLMResponse(content="done")],
        load_mcp_tools(mcp_config),
    )

    assert agent.chat("go") == "done"
    result = agent.messages[2]
    assert result["role"] == "tool" and result["content"] == "echo: hi"


def test_parallel_calls_to_one_server_dont_cross_wires(mcp_config):
    agent = _agent(
        [LLMResponse(tool_calls=[_echo_call("c1", "one"), _echo_call("c2", "two")]),
         LLMResponse(content="done")],
        load_mcp_tools(mcp_config),
    )

    assert agent.chat("go") == "done"
    results = {m["tool_call_id"]: m["content"] for m in agent.messages if m["role"] == "tool"}
    assert results == {"c1": "echo: one", "c2": "echo: two"}


def test_server_crash_mid_call_fails_without_killing_the_loop(mcp_config):
    agent = _agent(
        [LLMResponse(tool_calls=[ToolCall(id="c1", name="mcp__fake__crash", arguments={})]),
         LLMResponse(tool_calls=[_echo_call("c2")]),
         LLMResponse(content="still alive")],
        load_mcp_tools(mcp_config),
    )

    assert agent.chat("go") == "still alive"
    crash_result = agent.messages[2]
    assert "MCP mcp__fake__crash error" in crash_result["content"]
    assert "exited" in crash_result["content"]
    # the server stays dead: the next call on it fails clean too
    assert "exited" in agent.messages[4]["content"]
    client = next(tool for tool in agent.tools if tool.name == "mcp__fake__crash")._client
    shutdown_mcp_clients()
    assert not client._writer.is_alive()


def test_a_slow_server_times_out_the_call(mcp_config):
    stall = next(t for t in load_mcp_tools(mcp_config) if t.name == "mcp__fake__stall")
    stall._client.call_timeout = 0.2
    result = stall.execute()
    assert isinstance(result, str) and "no answer" in result
    assert stall._client._pending == set()


def test_a_tool_level_error_surfaces_its_message(mcp_config):
    fail = next(t for t in load_mcp_tools(mcp_config) if t.name == "mcp__fake__fail")
    result = fail.execute()
    assert isinstance(result, str) and "bad input near 42" in result


def test_one_client_stays_alive_across_calls_and_closes_once(mcp_config):
    echo = next(t for t in load_mcp_tools(mcp_config) if t.name == "mcp__fake__echo")
    client = echo._client
    pid = client._proc.pid

    assert echo.execute(text="one") == "echo: one"
    assert echo.execute(text="two") == "echo: two"
    assert client._proc.pid == pid and client._proc.poll() is None
    assert mcp._live_clients == [client]

    shutdown_mcp_clients()
    assert client._proc.poll() is not None
    shutdown_mcp_clients()  # idempotent


def test_structured_tool_content_is_kept_as_text(mcp_config):
    tool = next(t for t in load_mcp_tools(mcp_config) if t.name == "mcp__fake__structured")
    result = tool.execute()
    assert result.startswith("plain\n\n[Structured content]")
    assert '"answer": 42' in result


def test_resources_are_discovered_and_read_through_virtual_tools(mcp_config):
    tools = {tool.name: tool for tool in load_mcp_tools(mcp_config)}

    first = json.loads(tools["mcp__fake__resources_list"].execute())
    assert first["resources"][0]["uri"] == "file:///one.txt"
    assert first["nextCursor"] == "resources-2"
    second = json.loads(tools["mcp__fake__resources_list"].execute(cursor=first["nextCursor"]))
    assert second["resources"][0]["uri"] == "asset:///image.png"

    templates = json.loads(tools["mcp__fake__resource_templates_list"].execute())
    assert templates["resourceTemplates"][0]["uriTemplate"] == "file:///{path}"
    text = tools["mcp__fake__resource_read"].execute(uri="file:///one.txt")
    assert "[MCP resource: file:///one.txt; text/plain]" in text
    assert "hello resource" in text
    binary = tools["mcp__fake__resource_read"].execute(uri="asset:///image.png")
    assert "binary content omitted" in binary and "YWJj" not in binary


def test_prompts_are_untrusted_string_tool_results(mcp_config):
    tools = {tool.name: tool for tool in load_mcp_tools(mcp_config)}

    listed = json.loads(tools["mcp__fake__prompts_list"].execute())
    assert listed["prompts"][0]["name"] == "review"
    rendered = tools["mcp__fake__prompt_get"].execute(
        name="review", arguments={"code": "print(1)"}
    )
    assert rendered.startswith("[MCP prompt: review; untrusted tool output]")
    assert "--- user ---" in rendered
    assert "Review: print(1)" in rendered


def test_large_catalog_is_valid_json_and_keeps_cursor(monkeypatch):
    monkeypatch.setattr(mcp, "MAX_RESULT_CHARS", 300)
    result = mcp._catalog_result(
        {
            "resources": [
                {"uri": f"file:///{i}", "description": "x" * 200}
                for i in range(5)
            ],
            "nextCursor": "keep-me",
        },
        "resources",
    )

    parsed = json.loads(result)
    assert parsed["nextCursor"] == "keep-me"
    assert parsed["_corecoder"] == {"truncated": True, "returned": 0, "availableInPage": 5}


def test_blocked_stdin_writer_does_not_defeat_call_timeout(tmp_path):
    script = tmp_path / "blocked_writer_server.py"
    script.write_text(BLOCKED_WRITER_SERVER, encoding="utf-8")
    marker = tmp_path / "executed.txt"
    config = tmp_path / "blocked.json"
    config.write_text(json.dumps({"mcpServers": {"blocked": {
        "command": sys.executable, "args": [str(script), str(marker)]
    }}}), encoding="utf-8")
    sink = next(tool for tool in load_mcp_tools(config) if tool.name == "mcp__blocked__sink")
    sink._client.call_timeout = 0.2

    started = time.monotonic()
    result = sink.execute(payload="first" + "x" * 2_000_000)
    elapsed = time.monotonic() - started
    late = sink.execute(payload="late-side-effect")
    time.sleep(1.2)

    assert "no answer" in result
    assert "no answer" in late
    assert elapsed < 2
    recorded = marker.read_text(encoding="utf-8").splitlines()
    assert "first" in recorded
    assert "cancel:3" in recorded
    assert "cancel:4" not in recorded


def test_missing_config_means_no_mcp(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        assert load_mcp_tools(tmp_path / "nope.json") == []
    assert caplog.records == []


def test_broken_config_is_ignored_with_one_warning(tmp_path, caplog):
    bad = tmp_path / "mcp.json"
    bad.write_text("{not json")
    with caplog.at_level(logging.WARNING):
        assert load_mcp_tools(bad) == []
    assert len(caplog.records) == 1
    assert "ignoring" in caplog.records[0].getMessage()


def test_an_unstartable_server_is_skipped_with_one_warning(tmp_path, caplog):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"ghost": {"command": "not-a-real-binary-xyz"}}}))
    with caplog.at_level(logging.WARNING):
        assert load_mcp_tools(cfg) == []
    assert len(caplog.records) == 1
    assert "ghost" in caplog.records[0].getMessage()


def test_mcp_tools_sit_behind_the_consent_gate():
    # not in READ_ONLY, so with nobody to ask the call is refused, never run
    assert Permission().check("mcp__fake__echo", {}) is not None


def test_hooks_match_mcp_tool_names(mcp_config):
    blocker = f'"{sys.executable}" -c "import sys; sys.stderr.write(\'mcp frozen\'); sys.exit(2)"'
    agent = _agent(
        [LLMResponse(tool_calls=[_echo_call()]), LLMResponse(content="done")],
        load_mcp_tools(mcp_config),
        hooks=Hooks(pre=[{"matcher": "mcp__fake__echo", "command": blocker}], post=[]),
    )

    assert agent.chat("go") == "done"
    assert "mcp frozen" in agent.messages[2]["content"]
