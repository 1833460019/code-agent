"""Provider creation for Web; execution uses the shared Model interface."""
from repo_agent.models.base import Model
from repo_agent.models.factory import create_model
from repo_agent.schemas import ModelResponse


class MockModelAdapter(Model):
    @property
    def name(self):
        return "unconfigured"

    async def complete(self, *, system_prompt, messages, tools):
        return ModelResponse(content="Set ANTHROPIC_API_KEY and MODEL_ID in backend/.env to use a real model.")


def create_model_adapter(settings):
    if not (settings.anthropic_api_key or settings.anthropic_base_url):
        return MockModelAdapter()
    return create_model(provider="anthropic", model=settings.model_id,
                        api_key=settings.anthropic_api_key, base_url=settings.anthropic_base_url,
                        max_tokens=settings.max_tokens)
