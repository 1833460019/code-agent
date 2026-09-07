from __future__ import annotations

import asyncio


class ModelRecovery:
    """Bounded retries, reactive compaction, output-budget upgrade and fallback."""
    def __init__(self, model, *, fallback=None, attempts=3, timeout=120, backoff=0.5):
        self.model, self.fallback = model, fallback
        self.attempts, self.timeout, self.backoff = attempts, timeout, backoff

    async def complete(self, *, system_prompt, messages, tools, context, recorder):
        selected = self.model
        last_error = None
        for attempt in range(self.attempts):
            recorder.event("model_request", attempt=attempt + 1, model=selected.name,
                           system_prompt=system_prompt, messages=[_message(m) for m in messages], tools=tools)
            try:
                response = await asyncio.wait_for(selected.complete(
                    system_prompt=system_prompt, messages=messages, tools=tools), timeout=self.timeout)
                if response.stop_reason in {"max_tokens", "length"}:
                    recorder.auxiliary("truncated_response", response)
                    if hasattr(selected, "max_tokens") and selected.max_tokens < 16384:
                        selected.max_tokens = min(16384, selected.max_tokens * 2)
                        recorder.event("recovery", action="increase_max_tokens", attempt=attempt + 1)
                        continue
                    raise RuntimeError("Model response truncated at output limit")
                return response, selected.name
            except Exception as exc:
                last_error = exc
                status = getattr(exc, "status_code", None)
                text = str(exc).lower()
                context_error = any(x in text for x in ("context length", "prompt is too long", "context_length", "too many tokens"))
                transient = status in {408, 409, 429, 500, 502, 503, 504, 529} or isinstance(
                    exc, (TimeoutError, ConnectionError)) or "connection" in type(exc).__name__.lower()
                recorder.event("model_attempt_error", attempt=attempt + 1, error=f"{type(exc).__name__}: {exc}")
                if context_error:
                    from ..context.manager import ContextManager
                    reduced = ContextManager(soft_limit_chars=max(512, context._size(messages) // 2))
                    messages = reduced.prepare(messages)
                    recorder.event("recovery", action="reactive_compact", attempt=attempt + 1)
                elif self.fallback is not None and (not transient or attempt == self.attempts - 2):
                    selected, self.fallback = self.fallback, None
                    recorder.event("recovery", action="fallback_model", model=selected.name)
                elif not transient:
                    raise
                if attempt + 1 < self.attempts:
                    await asyncio.sleep(min(8, self.backoff * 2 ** attempt))
        raise RuntimeError(f"Model recovery exhausted: {last_error or 'truncated responses'}") from last_error


def _message(message):
    from dataclasses import asdict
    return asdict(message)
