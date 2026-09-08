import json
from types import SimpleNamespace

from repo_agent.models.openai_model import OpenAIModel, _to_openai_messages, _to_openai_tool
from repo_agent.schemas import Message, ToolCall


class FakeCompletions:
    def __init__(self):
        self.request = None

    async def create(self, **kwargs):
        self.request = kwargs
        function = SimpleNamespace(name="write_file", arguments=json.dumps({"path": "ok.txt", "content": "ok"}))
        message = SimpleNamespace(content="working", tool_calls=[SimpleNamespace(id="call-1", function=function)])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7),
        )


class OpenAIModelTests(__import__("unittest").TestCase):
    def test_message_and_tool_conversion_preserve_protocol(self):
        messages = [
            Message(role="user", content="fix"),
            Message(role="assistant", content="", tool_calls=[ToolCall("c1", "read_file", {"path": "a.py"})]),
            Message(role="tool", content="body", tool_call_id="c1", tool_name="read_file"),
            Message(role="summary", content="older work summarized"),
        ]
        converted = _to_openai_messages(messages)
        self.assertEqual(converted[1]["tool_calls"][0]["function"]["name"], "read_file")
        self.assertEqual(converted[2], {"role": "tool", "tool_call_id": "c1", "content": "body"})
        self.assertEqual(converted[3]["role"], "user")
        tool = _to_openai_tool({"name": "read_file", "description": "read", "input_schema": {"type": "object"}})
        self.assertEqual(tool["function"]["parameters"], {"type": "object"})

    def test_complete_returns_native_tool_call_and_usage(self):
        async def run():
            model = OpenAIModel("test", api_key="dummy", base_url="https://example.invalid/v1", max_tokens=123)
            completions = FakeCompletions()
            model.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
            result = await model.complete(
                system_prompt="system",
                messages=[Message(role="user", content="fix")],
                tools=[{"name": "write_file", "description": "write", "input_schema": {"type": "object"}}],
            )
            self.assertEqual(result.tool_calls[0].arguments["path"], "ok.txt")
            self.assertEqual(result.usage.total_tokens, 19)
            self.assertEqual(completions.request["max_tokens"], 123)
            self.assertEqual(completions.request["tools"][0]["type"], "function")

        __import__("asyncio").run(run())
