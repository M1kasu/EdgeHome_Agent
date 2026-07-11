"""Tests for LocalLLMAgent async_process."""

import pytest
from contextlib import contextmanager

from homeassistant.components import conversation
from homeassistant.components.conversation import ConversationInput, SystemContent, AssistantContent, UserContent
from homeassistant.const import CONF_LLM_HASS_API, MATCH_ALL
from homeassistant.exceptions import HomeAssistantError, TemplateError
from homeassistant.helpers import intent

from custom_components.llama_conversation.conversation import LocalLLMAgent
from custom_components.llama_conversation.entity import TextGenerationResult
from custom_components.llama_conversation.const import (
    CONF_CHAT_MODEL,
    CONF_MAX_TOOL_CALL_ITERATIONS,
    CONF_PROMPT,
    CONF_REFRESH_SYSTEM_PROMPT,
    CONF_REMEMBER_NUM_INTERACTIONS,
    CONF_REMEMBER_CONVERSATION,
    CONF_EDGEHOME_ENABLED,
    CONF_EDGEHOME_FAST_PATH_ENABLED,
    CONF_EDGEHOME_PLANNING_ENABLED,
    DEFAULT_PROMPT,
    DOMAIN,
)
from custom_components.llama_conversation.utils import MalformedToolCallException


class DummyClient:
    def __init__(self, hass):
        self.hass = hass
        self.generated_prompts = []
        self.seen_conversations = []

    def _generate_system_prompt(self, prompt_template, llm_api, entity_options):
        self.generated_prompts.append(prompt_template)
        return "rendered-system-prompt"

    async def _async_generate(self, conv, agent_id, chat_log, entity_options):
        self.seen_conversations.append(list(conv))
        async def gen():
            yield AssistantContent(agent_id=agent_id, content="hello from llm")
        return gen()


class DummySubentry:
    def __init__(self, subentry_id="sub1", title="Test Agent", chat_model="model"):
        self.subentry_id = subentry_id
        self.title = title
        self.subentry_type = DOMAIN
        self.data = {CONF_CHAT_MODEL: chat_model}


class DummyEntry:
    def __init__(self, entry_id="entry1", options=None, subentry=None, runtime_data=None):
        self.entry_id = entry_id
        self.options = options or {}
        self.subentries = {subentry.subentry_id: subentry}
        self.runtime_data = runtime_data

    def add_update_listener(self, _cb):
        return lambda: None


class FakeChatLog:
    def __init__(self, content=None):
        self.content = content or []
        self.llm_api = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeChatSession:
    def __enter__(self):
        return {}

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_async_process_generates_response(monkeypatch, hass):
    client = DummyClient(hass)
    subentry = DummySubentry()
    entry = DummyEntry(subentry=subentry, runtime_data=client)

    # Make entry discoverable through hass data as LocalLLMEntity expects.
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry

    @contextmanager
    def fake_chat_session(_hass, _conversation_id):
        yield FakeChatSession()

    @contextmanager
    def fake_chat_log(_hass, _session, _user_input):
        yield FakeChatLog()

    monkeypatch.setattr(
        "custom_components.llama_conversation.conversation.chat_session.async_get_chat_session",
        fake_chat_session,
    )
    monkeypatch.setattr(
        "custom_components.llama_conversation.conversation.conversation.async_get_chat_log",
        fake_chat_log,
    )

    agent = LocalLLMAgent(hass, entry, subentry, client)

    result = await agent.async_process(
        ConversationInput(
            text="turn on the lights",
            context=None,
            conversation_id="conv-id",
            device_id=None,
            satellite_id=None,
            language="en",
            agent_id="agent-1",
        )
    )

    assert result.response.speech["plain"]["speech"] == "hello from llm"
    # System prompt should be rendered once when message history is empty.
    assert client.generated_prompts == [DEFAULT_PROMPT]
    assert agent.supported_languages == MATCH_ALL


@pytest.mark.asyncio
async def test_async_process_uses_opt_in_edgehome_fast_path_before_model(monkeypatch, hass):
    client = DummyClient(hass)
    subentry = DummySubentry()
    subentry.data.update(
        {
            CONF_EDGEHOME_ENABLED: True,
            CONF_EDGEHOME_FAST_PATH_ENABLED: True,
        }
    )
    entry = DummyEntry(subentry=subentry, runtime_data=client)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})

    async def turn_on(call):
        hass.states.async_set(call.data["entity_id"], "on", context=call.context)

    hass.services.async_register("light", "turn_on", turn_on)
    monkeypatch.setattr(
        "custom_components.llama_conversation.edgehome.entity_resolver.async_should_expose",
        lambda _hass, _domain, _entity_id: True,
    )

    agent = LocalLLMAgent(hass, entry, subentry, client)
    result = await agent.async_process(
        ConversationInput(
            text="打开客厅灯",
            context=None,
            conversation_id="conv-id",
            device_id=None,
            satellite_id=None,
            language="zh-Hans",
            agent_id="agent-1",
        )
    )

    assert result.response.speech["plain"]["speech"] == "已打开客厅灯。"
    assert hass.states.get("light.living").state == "on"
    assert client.generated_prompts == []
    assert client.seen_conversations == []


@pytest.mark.asyncio
async def test_edgehome_generates_a_safe_conversation_id_for_one_shot_requests(monkeypatch, hass):
    client = DummyClient(hass)
    subentry = DummySubentry()
    subentry.data.update(
        {
            CONF_EDGEHOME_ENABLED: True,
            CONF_EDGEHOME_FAST_PATH_ENABLED: True,
        }
    )
    entry = DummyEntry(subentry=subentry, runtime_data=client)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})

    async def turn_on(call):
        hass.states.async_set(call.data["entity_id"], "on", context=call.context)

    hass.services.async_register("light", "turn_on", turn_on)
    monkeypatch.setattr(
        "custom_components.llama_conversation.edgehome.entity_resolver.async_should_expose",
        lambda _hass, _domain, _entity_id: True,
    )

    result = await LocalLLMAgent(hass, entry, subentry, client).async_process(
        ConversationInput(
            text="打开客厅灯",
            context=None,
            conversation_id=None,
            device_id=None,
            satellite_id=None,
            language="zh-Hans",
            agent_id="agent-1",
        )
    )

    assert result.conversation_id is not None
    assert result.conversation_id.startswith("edgehome-")


@pytest.mark.asyncio
async def test_async_process_executes_valid_complex_edgehome_plan_before_legacy_model(monkeypatch, hass):
    class PlanningClient(DummyClient):
        async def _generate(self, _messages, _llm_api, _agent_id, _options):
            return TextGenerationResult(
                response='{"goal":"打开客厅灯然后关闭卧室灯","steps":[{"id":"one","tool":"turn_on_device","arguments":{"entity_id":"light.living"},"verify":{"entity_id":"light.living","state":"on"}},{"id":"two","tool":"turn_off_device","arguments":{"entity_id":"switch.bedroom"},"verify":{"entity_id":"switch.bedroom","state":"off"}}]}'
            )

    client = PlanningClient(hass)
    subentry = DummySubentry()
    subentry.data.update(
        {
            CONF_EDGEHOME_ENABLED: True,
            CONF_EDGEHOME_FAST_PATH_ENABLED: True,
            CONF_EDGEHOME_PLANNING_ENABLED: True,
        }
    )
    entry = DummyEntry(subentry=subentry, runtime_data=client)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})
    hass.states.async_set("switch.bedroom", "on", {"friendly_name": "卧室灯"})

    async def turn_on(call):
        hass.states.async_set(call.data["entity_id"], "on", context=call.context)

    async def turn_off(call):
        hass.states.async_set(call.data["entity_id"], "off", context=call.context)

    hass.services.async_register("light", "turn_on", turn_on)
    hass.services.async_register("switch", "turn_off", turn_off)
    monkeypatch.setattr(
        "custom_components.llama_conversation.edgehome.entity_resolver.async_should_expose",
        lambda _hass, _domain, _entity_id: True,
    )

    agent = LocalLLMAgent(hass, entry, subentry, client)
    result = await agent.async_process(
        ConversationInput(
            text="打开客厅灯然后关闭卧室灯",
            context=None,
            conversation_id="conv-id",
            device_id=None,
            satellite_id=None,
            language="zh-Hans",
            agent_id="agent-1",
        )
    )

    assert result.response.speech["plain"]["speech"] == "已完成任务：打开客厅灯然后关闭卧室灯。"
    assert hass.states.get("light.living").state == "on"
    assert hass.states.get("switch.bedroom").state == "off"
    assert client.generated_prompts == []


@pytest.mark.asyncio
async def test_async_process_returns_error_when_llm_api_lookup_fails(monkeypatch, hass):
    client = DummyClient(hass)
    subentry = DummySubentry()
    subentry.data[CONF_LLM_HASS_API] = "missing-api"
    entry = DummyEntry(subentry=subentry, runtime_data=client)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry

    @contextmanager
    def fake_chat_session(_hass, _conversation_id):
        yield FakeChatSession()

    @contextmanager
    def fake_chat_log(_hass, _session, _user_input):
        yield FakeChatLog()

    async def fake_get_api(*_args, **_kwargs):
        raise HomeAssistantError("bad api")

    monkeypatch.setattr(
        "custom_components.llama_conversation.conversation.chat_session.async_get_chat_session",
        fake_chat_session,
    )
    monkeypatch.setattr(
        "custom_components.llama_conversation.conversation.conversation.async_get_chat_log",
        fake_chat_log,
    )
    monkeypatch.setattr(
        "custom_components.llama_conversation.conversation.llm.async_get_api",
        fake_get_api,
    )

    agent = LocalLLMAgent(hass, entry, subentry, client)

    result = await agent.async_process(
        ConversationInput(
            text="turn on the lights",
            context=None,
            conversation_id="conv-id",
            device_id=None,
            satellite_id=None,
            language="en",
            agent_id="agent-1",
        )
    )

    payload = result.response.as_dict()
    assert payload["response_type"] == intent.IntentResponseType.ERROR.value
    assert payload["data"]["code"] == intent.IntentResponseErrorCode.UNKNOWN.value
    assert payload["speech"]["plain"]["speech"] == "Error preparing LLM API: bad api"


@pytest.mark.asyncio
async def test_async_process_returns_error_when_prompt_rendering_fails(monkeypatch, hass):
    client = DummyClient(hass)

    def raise_template_error(_prompt_template, _llm_api, _entity_options):
        raise TemplateError("bad template")

    client._generate_system_prompt = raise_template_error
    subentry = DummySubentry()
    entry = DummyEntry(subentry=subentry, runtime_data=client)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry

    @contextmanager
    def fake_chat_session(_hass, _conversation_id):
        yield FakeChatSession()

    @contextmanager
    def fake_chat_log(_hass, _session, _user_input):
        yield FakeChatLog()

    monkeypatch.setattr(
        "custom_components.llama_conversation.conversation.chat_session.async_get_chat_session",
        fake_chat_session,
    )
    monkeypatch.setattr(
        "custom_components.llama_conversation.conversation.conversation.async_get_chat_log",
        fake_chat_log,
    )

    agent = LocalLLMAgent(hass, entry, subentry, client)

    result = await agent.async_process(
        ConversationInput(
            text="turn on the lights",
            context=None,
            conversation_id="conv-id",
            device_id=None,
            satellite_id=None,
            language="en",
            agent_id="agent-1",
        )
    )

    payload = result.response.as_dict()
    assert payload["response_type"] == intent.IntentResponseType.ERROR.value
    assert payload["speech"]["plain"]["speech"] == "Sorry, I had a problem with my template: bad template"


@pytest.mark.asyncio
async def test_async_process_handles_backend_exception_before_stream_iteration(monkeypatch, hass):
    class FailingClient(DummyClient):
        async def _async_generate(self, conv, agent_id, chat_log, entity_options):
            self.seen_conversations.append(list(conv))
            raise RuntimeError("backend offline")

    client = FailingClient(hass)
    subentry = DummySubentry()
    entry = DummyEntry(subentry=subentry, runtime_data=client)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry

    @contextmanager
    def fake_chat_session(_hass, _conversation_id):
        yield FakeChatSession()

    @contextmanager
    def fake_chat_log(_hass, _session, _user_input):
        yield FakeChatLog()

    monkeypatch.setattr(
        "custom_components.llama_conversation.conversation.chat_session.async_get_chat_session",
        fake_chat_session,
    )
    monkeypatch.setattr(
        "custom_components.llama_conversation.conversation.conversation.async_get_chat_log",
        fake_chat_log,
    )

    agent = LocalLLMAgent(hass, entry, subentry, client)

    result = await agent.async_process(
        ConversationInput(
            text="turn on the lights",
            context=None,
            conversation_id="conv-id",
            device_id=None,
            satellite_id=None,
            language="en",
            agent_id="agent-1",
        )
    )

    payload = result.response.as_dict()
    assert payload["response_type"] == intent.IntentResponseType.ERROR.value
    assert payload["data"]["code"] == intent.IntentResponseErrorCode.FAILED_TO_HANDLE.value
    assert "backend offline" in payload["speech"]["plain"]["speech"]


@pytest.mark.asyncio
async def test_async_process_recovers_from_malformed_tool_call(monkeypatch, hass):
    class RecoveringClient(DummyClient):
        def __init__(self, hass):
            super().__init__(hass)
            self.call_count = 0

        async def _async_generate(self, conv, agent_id, chat_log, entity_options):
            self.call_count += 1
            self.seen_conversations.append(list(conv))

            async def gen():
                if self.call_count == 1:
                    raise MalformedToolCallException(agent_id, "", "unknown", "{bad", "bad json")
                yield AssistantContent(agent_id=agent_id, content="recovered response")

            return gen()

    client = RecoveringClient(hass)
    subentry = DummySubentry()
    subentry.data[CONF_MAX_TOOL_CALL_ITERATIONS] = 2
    entry = DummyEntry(subentry=subentry, runtime_data=client)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry

    @contextmanager
    def fake_chat_session(_hass, _conversation_id):
        yield FakeChatSession()

    @contextmanager
    def fake_chat_log(_hass, _session, _user_input):
        yield FakeChatLog()

    monkeypatch.setattr(
        "custom_components.llama_conversation.conversation.chat_session.async_get_chat_session",
        fake_chat_session,
    )
    monkeypatch.setattr(
        "custom_components.llama_conversation.conversation.conversation.async_get_chat_log",
        fake_chat_log,
    )

    agent = LocalLLMAgent(hass, entry, subentry, client)

    result = await agent.async_process(
        ConversationInput(
            text="turn on the lights",
            context=None,
            conversation_id="conv-id",
            device_id=None,
            satellite_id=None,
            language="en",
            agent_id="agent-1",
        )
    )

    assert result.response.speech["plain"]["speech"] == "recovered response"
    assert client.call_count == 2


@pytest.mark.asyncio
async def test_async_process_trims_remembered_history_before_generation(monkeypatch, hass):
    client = DummyClient(hass)
    subentry = DummySubentry()
    subentry.data[CONF_REMEMBER_NUM_INTERACTIONS] = 1
    subentry.data[CONF_REFRESH_SYSTEM_PROMPT] = False
    entry = DummyEntry(subentry=subentry, runtime_data=client)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry

    @contextmanager
    def fake_chat_session(_hass, _conversation_id):
        yield FakeChatSession()

    @contextmanager
    def fake_chat_log(_hass, _session, _user_input):
        yield FakeChatLog(
            content=[
                SystemContent(content="existing-system"),
                conversation.UserContent(content="u1"),
                AssistantContent(agent_id="agent-1", content="a1"),
                conversation.UserContent(content="u2"),
                AssistantContent(agent_id="agent-1", content="a2"),
            ]
        )

    monkeypatch.setattr(
        "custom_components.llama_conversation.conversation.chat_session.async_get_chat_session",
        fake_chat_session,
    )
    monkeypatch.setattr(
        "custom_components.llama_conversation.conversation.conversation.async_get_chat_log",
        fake_chat_log,
    )

    agent = LocalLLMAgent(hass, entry, subentry, client)

    await agent.async_process(
        ConversationInput(
            text="turn on the lights",
            context=None,
            conversation_id="conv-id",
            device_id=None,
            satellite_id=None,
            language="en",
            agent_id="agent-1",
        )
    )

    seen = client.seen_conversations[0]
    assert len(seen) == 3
    assert isinstance(seen[0], SystemContent)
    assert seen[1].content == "u2"
    assert seen[2].content == "a2"


def _make_user_input(text="turn on the lights"):
    return ConversationInput(
        text=text, context=None, conversation_id="conv-id",
        device_id=None, satellite_id=None, language="en", agent_id="agent-1",
    )


def _patch_chat(monkeypatch, chat_log):
    """Patch chat_session and chat_log context managers for agent tests."""
    @contextmanager
    def fake_chat_session(_hass, _conversation_id):
        yield FakeChatSession()

    @contextmanager
    def fake_chat_log_cm(_hass, _session, _user_input):
        yield chat_log

    monkeypatch.setattr(
        "custom_components.llama_conversation.conversation.chat_session.async_get_chat_session",
        fake_chat_session,
    )
    monkeypatch.setattr(
        "custom_components.llama_conversation.conversation.conversation.async_get_chat_log",
        fake_chat_log_cm,
    )


def _make_agent(hass, subentry_data_overrides=None):
    client = DummyClient(hass)
    subentry = DummySubentry()
    if subentry_data_overrides:
        subentry.data = {**subentry.data, **subentry_data_overrides}
    entry = DummyEntry(subentry=subentry, runtime_data=client)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry
    return LocalLLMAgent(hass, entry, subentry, client), client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refresh, initial_content, expect_generated, expect_first_content",
    [
        pytest.param(
            False,
            [UserContent(content="turn on the lights")],
            1,
            "rendered-system-prompt",
            id="first_turn_no_system_prompt",
        ),
        pytest.param(
            True,
            [SystemContent(content="old-system-prompt"), UserContent(content="turn on the lights")],
            1,
            "rendered-system-prompt",
            id="refresh_replaces_existing",
        ),
        pytest.param(
            False,
            [SystemContent(content="existing-system-prompt"), UserContent(content="turn on the lights")],
            0,
            "existing-system-prompt",
            id="no_refresh_keeps_existing",
        ),
    ],
)
async def test_system_prompt_injection(
    monkeypatch, hass, refresh, initial_content, expect_generated, expect_first_content,
):
    """Verify system prompt generation/replacement based on refresh setting
    and whether a SystemContent already exists in chat history."""
    agent, client = _make_agent(hass, {
        CONF_REFRESH_SYSTEM_PROMPT: refresh, CONF_REMEMBER_CONVERSATION: True,
    })
    chat_log = FakeChatLog()
    chat_log.content.extend(initial_content)
    _patch_chat(monkeypatch, chat_log)

    result = await agent.async_process(_make_user_input())

    assert result.response.speech["plain"]["speech"] == "hello from llm"
    assert len(client.generated_prompts) == expect_generated
    assert isinstance(chat_log.content[0], SystemContent)
    assert chat_log.content[0].content == expect_first_content
