from __future__ import annotations

import time
import asyncio
from dataclasses import asdict

from ..context.manager import ContextManager
from ..models.recovery import ModelRecovery
from ..schemas import Message, ToolResult


class AgentLoop:
    """One LCC-style loop; optional services attach through Runtime and Hooks."""
    def __init__(self, runtime):
        self.runtime = runtime
        self.context = ContextManager(soft_limit_chars=runtime.config.context_soft_limit_chars)
        self.recovery = ModelRecovery(runtime.model, fallback=runtime.agent.fallback_model,
                                      attempts=runtime.config.model_attempts, timeout=runtime.config.model_timeout)

    async def run(self, state):
        rt, rec = self.runtime, self.runtime.recorder
        messages = state.messages
        for step in range(1, rt.config.max_steps + 1):
            await asyncio.sleep(0)
            state.step = step
            stop = await rt.before_step(state)
            if stop:
                return "terminated", stop, None
            if rt.compact_requested is not None or (
                    rt.config.profile == "full" and self.context._size(messages) > self.context.soft_limit_chars):
                try:
                    prepared = await self.context.compact(messages, rt.model, rec, rt.compact_requested or "")
                    rec.event("compact", step=step, before=len(messages), after=len(prepared))
                    messages[:] = prepared
                except Exception as exc:
                    rec.event("compact_error", error=str(exc))
                    prepared = self.context.prepare(messages)
                rt.compact_requested = None
            else:
                prepared = self.context.prepare(messages)
            prompt = rt.prompt.build(rt, step)
            model_started = time.perf_counter()
            rec.event("model_start", step=step, context_messages=len(prepared))
            try:
                payload = {"runtime": rt, "messages": prepared, "system_prompt": prompt}
                await rt.hooks.emit("before_model", payload)
                response, model_name = await self.recovery.complete(
                    system_prompt=payload["system_prompt"], messages=payload["messages"],
                    tools=[t.definition() for t in rt.tools], context=self.context, recorder=rec)
                await rt.hooks.emit("after_model", {"runtime": rt, "response": response})
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                rec.event("error", step=step, error=error)
                await rt.hooks.emit("error", {"runtime": rt, "error": exc})
                return "error", "model_error", error
            latency = time.perf_counter() - model_started
            state.input_tokens += response.usage.input_tokens
            state.output_tokens += response.usage.output_tokens
            messages.append(Message(role="assistant", content=response.content, tool_calls=response.tool_calls))
            record = dict(step=step, assistant_response=response.content, model=model_name,
                          stop_reason=response.stop_reason, latency=latency,
                          token_usage=asdict(response.usage) | {"total_tokens": response.usage.total_tokens}, tool_calls=[])
            rec.event("assistant", step=step, content=response.content, response=asdict(response), latency=latency)
            if not response.tool_calls:
                rec.add_step(record)
                if rt.background.pending or (not rt.parent and rt.teams.pending):
                    messages.append(Message(role="user", content="Required work is still running. Use wait tools to collect results."))
                    continue
                return "terminated", "model_stopped_without_finish", None
            finished = False
            for call in response.tool_calls:
                state.total_tool_calls += 1
                started = time.perf_counter()
                rec.event("tool_start", step=step, tool_call_id=call.id, tool_name=call.name, arguments=call.arguments)
                try:
                    if finished:
                        result = ToolResult(False, "Not executed: finish was already accepted")
                    else:
                        result = await rt.dispatch(call.name, call.arguments)
                except Exception as exc:
                    result = ToolResult(False, f"{type(exc).__name__}: {exc}")
                visible, artifact_id = rec.observation(result.output, rt.config.tool_output_limit_chars)
                observation = dict(tool_call_id=call.id, tool_name=call.name, arguments=call.arguments,
                                   observation=result.output, model_observation=visible, artifact_id=artifact_id,
                                   ok=result.ok, latency=time.perf_counter() - started, metadata=result.metadata)
                record["tool_calls"].append(observation)
                messages.append(Message(role="tool", content=visible, tool_call_id=call.id,
                                        tool_name=call.name, is_error=not result.ok))
                rec.event("tool_result", step=step, **observation)
                if call.name == "TodoWrite" and result.ok:
                    rt.todos.last_update = step
                    rec.event("todo", step=step, data=rt.todos.items)
                finished = finished or (result.finished and result.ok)
            rec.add_step(record)
            if finished:
                return "success", "finish_tool", None
        return "terminated", "max_steps", None
