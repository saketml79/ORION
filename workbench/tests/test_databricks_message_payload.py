"""Offline wire-only normalization, no provider calls or checkpoint writes."""
import asyncio
from copy import deepcopy
import json
import socket
from types import SimpleNamespace
from unittest.mock import patch

import httpx2
import openai
import pytest
from langchain_core.messages import AIMessage, ChatMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from openai.types.chat import (
    ChatCompletionSystemMessageParam, ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)

from workbench import databricks_models as dm


HOST = "https://workspace.example.com"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    original_connect = socket.socket.connect
    def blocked(*args, **kwargs):
        raise AssertionError("Network forbidden in payload checks")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    return original_connect


def conversation():
    return [
        SystemMessage(content="Source policy", name="policy"),
        HumanMessage(content="Source request", name="requester"),
        AIMessage(content="", name="artifact", tool_calls=[
            {"name": "read_file", "args": {"file_path": "/CONTEXT.md"},
             "id": "call_exact_1", "type": "tool_call"},
            {"name": "lookup_evidence", "args": {"offset": 0},
             "id": "call_exact_2", "type": "tool_call"},
        ]),
        ToolMessage(content="Exact Unicode evidence: Ω\r\n", name="read_file",
                    tool_call_id="call_exact_1", artifact={"source": "ledger"}),
        ToolMessage(content='{"accepted":false,"reason":"source gap"}',
                    additional_kwargs={"name": "lookup_evidence"},
                    tool_call_id="call_exact_2", status="error"),
    ]


def make_model(cls: type[ChatOpenAI] = dm._DatabricksChatOpenAI, **kwargs) -> ChatOpenAI:
    return cls(model="databricks-claude-sonnet-4-6", api_key=lambda: "offline-placeholder",
               base_url=HOST + "/serving-endpoints", use_responses_api=False,
               max_retries=0, stream_usage=False, **kwargs)


def test_installed_schema_and_typed_payload_preserve_source_provenance():
    assert set(ChatCompletionToolMessageParam.__annotations__) == {"content", "role", "tool_call_id"}
    assert "name" in ChatCompletionSystemMessageParam.__annotations__
    assert "name" in ChatCompletionUserMessageParam.__annotations__
    messages = conversation()
    before = [m.model_dump() for m in messages]
    model = make_model()
    payload = model._get_request_payload(messages, stop=["END"])
    expected = make_model(ChatOpenAI)._get_request_payload(messages, stop=["END"])
    expected["messages"] = [
        {key: value for key, value in message.items() if key != "name"}
        if message.get("role") in {"assistant", "tool"} else message
        for message in expected["messages"]
    ]
    assert payload == expected
    assert [m["role"] for m in payload["messages"]] == ["system", "user", "assistant", "tool", "tool"]
    assert "name" not in payload["messages"][2]
    assert [c["function"]["name"] for c in payload["messages"][2]["tool_calls"]] == ["read_file", "lookup_evidence"]
    assert payload["messages"][3] == {"role": "tool", "tool_call_id": "call_exact_1",
                                      "content": "Exact Unicode evidence: Ω\r\n"}
    assert all("name" not in m for m in payload["messages"] if m["role"] == "tool")
    assert [m.model_dump() for m in messages] == before
    assert messages[3].name == "read_file" and messages[3].artifact == {"source": "ledger"}
    assert messages[4].additional_kwargs == {"name": "lookup_evidence"}
    # Guard the generic role path too, without changing ordinary OpenAI clients.
    generic = [ChatMessage(content="evidence", role="tool", name="read_file")]
    assert "name" in make_model(ChatOpenAI)._get_request_payload(generic)["messages"][0]
    assert "name" not in model._get_request_payload(generic)["messages"][0]


def test_shared_parent_payload_copy_strips_assistant_and_tool_names_only():
    payload = make_model(ChatOpenAI)._get_request_payload(conversation())
    for message in payload["messages"]:
        if message["role"] == "assistant":
            message["name"] = "spark_codegen"
        elif message["role"] == "tool":
            message["name"] = "historical_tool_name"
    before = deepcopy(payload)
    expected = deepcopy(payload)
    for message in expected["messages"]:
        if message["role"] in {"assistant", "tool"}:
            del message["name"]
    model = make_model()
    with patch.object(ChatOpenAI, "_get_request_payload", return_value=payload):
        actual = model._get_request_payload("unused")
        assert actual == expected
        assert actual is not payload and actual["messages"] is not payload["messages"]
        assistant = next(m for m in actual["messages"] if m["role"] == "assistant")
        assert [call["function"]["name"] for call in assistant["tool_calls"]] == ["read_file", "lookup_evidence"]
        assert model._get_request_payload("unused") == expected
    assert payload == before
    # Responses payloads, if supplied by a parent, are not rewritten.
    responses = {"input": [{"type": "function_call", "name": "read_file"}]}
    with patch.object(ChatOpenAI, "_get_request_payload", return_value=responses):
        assert model._get_request_payload("unused") is responses


def test_configured_factory_uses_only_databricks_adapter_and_keeps_auth_lazy():
    calls = []
    def authenticate():
        calls.append(1)
        return {"Authorization": "Bearer offline-identity"}
    who = dm.WorkspaceIdentity("obo", HOST, "offline-user", SimpleNamespace(
        config=SimpleNamespace(host=HOST, authenticate=authenticate)))
    routes = {role: {"endpoint": "chat", "purpose": dm.ROLE_PURPOSES[role],
                    "tool_support_evidence": "Operator reviewed DOC-42",
                    "tool_support_status": "declared_not_runtime_verified",
                    "tool_calling_verified": False}
              for role in dm.ROLE_PURPOSES}
    models = dm.build_chat_models(who, routes)
    assert all(type(m) is dm._DatabricksChatOpenAI for m in models.values())
    assert calls == []
    assert all(m.max_retries == 0 and m.use_responses_api is False for m in models.values())
    generic = [ChatMessage(content="evidence", role="tool", name="read_file")]
    assert make_model(ChatOpenAI)._get_request_payload(generic)["messages"][0]["name"] == "read_file"


@pytest.mark.parametrize("fail", [False, True])
def test_exact_sdk_http_body_and_sanitized_error_context(fail, offline):
    messages = conversation()
    before = [m.model_dump() for m in messages]
    original = ChatOpenAI._get_request_payload
    captured = []
    # Emulate an older/provider parent serializer retaining tool names. This
    # proves the boundary rather than relying on today's typed converter.
    def parent(self, input_, **kwargs):
        payload = original(self, input_, **kwargs)
        for m in payload["messages"]:
            if m["role"] in {"assistant", "tool"}:
                m["name"] = "historical_tool_name"
        return payload
    tools = [{"type": "function", "function": {"name": "read_file",
              "parameters": {"type": "object", "properties": {}}}}]
    expected = {**original(make_model(ChatOpenAI), messages), "tools": tools}
    expected["messages"] = [
        {key: value for key, value in message.items() if key != "name"}
        if message.get("role") in {"assistant", "tool"} else message
        for message in expected["messages"]
    ]
    def respond(request):
        assert str(request.url) == HOST + "/serving-endpoints/chat/completions"
        body = json.loads(request.content)
        captured.append(body)  # Deliberately never capture authentication headers.
        assert body == expected
        assert all("name" not in m for m in body["messages"]
               if m["role"] in {"assistant", "tool"})
        assert all("name" not in m for m in body["messages"] if m["role"] == "tool")
        if fail:
            return httpx2.Response(400, json={"error": {"message":
                "messages.415.name Extra inputs not permitted password=PRIVATE_CONTEXT",
                "type": "invalid_request_error"}})
        return httpx2.Response(200, json={"id": "offline", "object": "chat.completion",
            "created": 0, "model": "databricks-claude-sonnet-4-6", "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "offline"},
                 "finish_reason": "stop"}]})
    http = openai.DefaultHttpxClient(transport=httpx2.MockTransport(respond), trust_env=False)
    async_http = openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(respond), trust_env=False)
    model = make_model(http_client=http, http_async_client=async_http)
    request_model = model.bind_tools(tools)
    async def async_check():
        try:
            assert (await request_model.ainvoke(messages)).content == "offline"
        finally:
            await async_http.aclose()
    try:
        with patch.object(ChatOpenAI, "_get_request_payload", parent):
            if fail:
                with pytest.raises(openai.BadRequestError) as error:
                    request_model.invoke(messages)
                diagnostic = dm.safe_failure_diagnostic(error.value, "writer_model_handler")
                public = json.dumps(diagnostic)
                assert "PRIVATE_CONTEXT" not in public and "messages.415" not in public
                assert "offline-placeholder" not in public and "Authorization" not in public
                assert diagnostic["exceptions"]
            else:
                assert request_model.invoke(messages).content == "offline"
                # Only allow Windows' internal loop socketpair construction,
                # before executing any async client code under the network guard.
                with patch.object(socket.socket, "connect", offline):
                    loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(async_check())
                finally:
                    loop.close()
    finally:
        http.close()
        if fail:
            with patch.object(socket.socket, "connect", offline):
                loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(async_http.aclose())
            finally:
                loop.close()
    assert len(captured) == (1 if fail else 2)  # No retry of HTTP 400.
    assert [m.model_dump() for m in messages] == before
    assert "PRIVATE_CONTEXT" not in json.dumps(captured)