"""Unit tests for agents/recruitment.py — candidate search and shortlist."""

from __future__ import annotations

import asyncio
import json
import random
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.state import CompanyState


# ---------------------------------------------------------------------------
# _talent_to_candidate (migrated from test_hr_agent.py)
# ---------------------------------------------------------------------------

class TestTalentToCandidate:
    def test_basic_conversion(self, monkeypatch):
        from onemancompany.agents import recruitment
        from onemancompany.core import config as config_mod
        import onemancompany.core.model_costs as mc

        monkeypatch.setattr(config_mod, "load_talent_skills", lambda tid: ["# Python\nPython skill content"])
        monkeypatch.setattr(config_mod, "load_talent_tools", lambda tid: ["sandbox_execute_code"])
        monkeypatch.setattr(mc, "compute_salary", lambda m: 5.0)

        talent = {
            "id": "test_talent",
            "name": "Test Dev",
            "role": "Engineer",
            "skills": ["python"],
            "personality_tags": ["creative"],
            "system_prompt_template": "You are a dev",
            "llm_model": "test-model",
            "api_provider": "openrouter",
            "temperature": 0.6,
            "hosting": "company",
            "auth_method": "api_key",
            "hiring_fee": 1.5,
        }

        random.seed(42)
        candidate = recruitment._talent_to_candidate(talent)

        assert candidate["id"] == "test_talent"
        assert candidate["name"] == "Test Dev"
        assert candidate["role"] == "Engineer"
        assert len(candidate["skill_set"]) == 1
        assert candidate["skill_set"][0]["name"] == "python"
        assert len(candidate["tool_set"]) == 1
        assert candidate["cost_per_1m_tokens"] == 5.0
        assert candidate["hiring_fee"] == 1.5
        assert candidate["jd_relevance"] == 1.0

    def test_non_openrouter_has_zero_cost(self, monkeypatch):
        from onemancompany.agents import recruitment
        from onemancompany.core import config as config_mod

        monkeypatch.setattr(config_mod, "load_talent_skills", lambda tid: [])
        monkeypatch.setattr(config_mod, "load_talent_tools", lambda tid: [])

        talent = {
            "id": "anthropic_talent",
            "api_provider": "anthropic",
            "llm_model": "claude-sonnet",
        }

        candidate = recruitment._talent_to_candidate(talent)
        assert candidate["cost_per_1m_tokens"] == 0.0


# ---------------------------------------------------------------------------
# TalentMarketClient
# ---------------------------------------------------------------------------

class TestTalentMarketClient:
    def test_initial_state(self):
        from onemancompany.agents.recruitment import TalentMarketClient
        client = TalentMarketClient()
        assert not client.connected
        assert client._session is None
        assert client._api_key == ""

    @pytest.mark.asyncio
    async def test_connect(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        mock_session = AsyncMock()

        class Context:
            def __init__(self, value):
                self.value = value

            async def __aenter__(self):
                return self.value

            async def __aexit__(self, exc_type, exc, tb):
                return None

        with patch(
            "mcp.client.sse.sse_client",
            return_value=Context((AsyncMock(), AsyncMock())),
        ), patch(
            "onemancompany.agents.recruitment.ClientSession",
            return_value=Context(mock_session),
        ):
            await client.connect("http://test/sse", "test-key")
            assert client.connected
            assert client._api_key == "test-key"
            mock_session.initialize.assert_awaited_once()
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_connect_already_connected_is_noop(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        client._session = AsyncMock()  # Simulate already connected

        # Should not raise or change anything
        await client.connect("http://test/sse", "new-key")
        assert client._api_key == ""  # Unchanged — early return

    @pytest.mark.asyncio
    async def test_disconnect(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        mock_stack = AsyncMock()
        mock_stack.aclose = AsyncMock()

        client._session = AsyncMock()
        client._stack = mock_stack
        client._api_key = "test-key"

        await client.disconnect()

        assert not client.connected
        assert client._api_key == ""
        mock_stack.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_no_session(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        await client.disconnect()  # Should be a noop
        assert not client.connected

    @pytest.mark.asyncio
    async def test_sse_context_is_entered_and_exited_by_same_owner_task(self):
        """Regression: AnyIO cancel scopes cannot be closed by another task."""
        from onemancompany.agents.recruitment import TalentMarketClient

        class TaskBoundContext:
            def __init__(self, value):
                self.value = value
                self.owner = None

            async def __aenter__(self):
                self.owner = asyncio.current_task()
                return self.value

            async def __aexit__(self, exc_type, exc, tb):
                if asyncio.current_task() is not self.owner:
                    raise RuntimeError(
                        "Attempted to exit cancel scope in a different task than it was entered in"
                    )

        class FakeSession:
            def __init__(self):
                self.call_task = None
                self.ping_task = None

            async def initialize(self):
                return None

            async def call_tool(self, tool_name, arguments):
                self.call_task = asyncio.current_task()
                return MagicMock(content=[])

            async def send_ping(self):
                self.ping_task = asyncio.current_task()

        client = TalentMarketClient()
        session = FakeSession()
        streams = (AsyncMock(), AsyncMock())
        stream_context = TaskBoundContext(streams)
        session_context = TaskBoundContext(session)

        with patch(
            "mcp.client.sse.sse_client",
            return_value=stream_context,
        ), patch(
            "onemancompany.agents.recruitment.ClientSession",
            return_value=session_context,
        ):
            # Startup and shutdown intentionally happen in different tasks,
            # matching FastAPI startup/reconnect/shutdown behaviour.
            connect_task = asyncio.create_task(
                client.connect("http://test/sse", "test-key")
            )
            await connect_task
            await client._call("test_tool", foo="bar")
            await client.ping()
            await client.disconnect()

        assert session_context.owner is stream_context.owner
        assert session.call_task is session_context.owner
        assert session.ping_task is session_context.owner
        assert not client.connected

    @pytest.mark.asyncio
    async def test_cancelled_connect_stops_owner_and_closes_entered_context(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        session_enter_started = asyncio.Event()
        never_ready = asyncio.Event()

        class OuterContext:
            def __init__(self):
                self.owner = None
                self.exit_task = None

            async def __aenter__(self):
                self.owner = asyncio.current_task()
                return AsyncMock(), AsyncMock()

            async def __aexit__(self, exc_type, exc, tb):
                self.exit_task = asyncio.current_task()

        class BlockingSessionContext:
            async def __aenter__(self):
                session_enter_started.set()
                await never_ready.wait()

            async def __aexit__(self, exc_type, exc, tb):
                return None

        client = TalentMarketClient()
        outer_context = OuterContext()
        with patch(
            "mcp.client.sse.sse_client",
            return_value=outer_context,
        ), patch(
            "onemancompany.agents.recruitment.ClientSession",
            return_value=BlockingSessionContext(),
        ):
            connect_task = asyncio.create_task(
                client.connect("http://test/sse", "test-key")
            )
            await session_enter_started.wait()
            connect_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await connect_task

        assert outer_context.exit_task is outer_context.owner
        assert client._owner_task is None
        assert not client.connected

    @pytest.mark.asyncio
    async def test_call_not_connected(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        with pytest.raises(RuntimeError, match="Not connected"):
            await client._call("some_tool")

    @pytest.mark.asyncio
    async def test_call_parses_json(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        client._api_key = "test-key"

        mock_item = MagicMock()
        mock_item.text = json.dumps({"status": "ok", "data": [1, 2, 3]})

        mock_result = MagicMock()
        mock_result.content = [mock_item]

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        client._session = mock_session

        result = await client._call("test_tool", foo="bar")
        assert result == {"status": "ok", "data": [1, 2, 3]}
        mock_session.call_tool.assert_awaited_once_with(
            "test_tool", arguments={"foo": "bar", "api_key": "test-key"}
        )

    @pytest.mark.asyncio
    async def test_call_returns_empty_on_no_dict(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        client._api_key = "key"

        mock_item = MagicMock()
        mock_item.text = "not json"

        mock_result = MagicMock()
        mock_result.content = [mock_item]

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        client._session = mock_session

        result = await client._call("test_tool")
        assert result == {}

    @pytest.mark.asyncio
    async def test_search(self, monkeypatch):
        from onemancompany.agents import recruitment
        from onemancompany.agents.recruitment import TalentMarketClient

        monkeypatch.setattr(recruitment, "load_app_config", lambda: {"talent_market": {}})
        client = TalentMarketClient()
        client._call = AsyncMock(return_value={"roles": []})

        result = await client.search("python dev")
        client._call.assert_awaited_once_with("search_candidates", job_description="python dev", use_ai=False)
        assert result == {"roles": []}

    @pytest.mark.asyncio
    async def test_hire(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        client._call = AsyncMock(return_value={"hired": True})

        result = await client.hire(["t1", "t2"], session_id="s1")
        client._call.assert_awaited_once_with("hire_talents", talent_ids=["t1", "t2"], session_id="s1")
        assert result == {"hired": True}

    @pytest.mark.asyncio
    async def test_hire_no_session_id(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        client._call = AsyncMock(return_value={"hired": True})

        await client.hire(["t1"])
        client._call.assert_awaited_once_with("hire_talents", talent_ids=["t1"])

    @pytest.mark.asyncio
    async def test_onboard(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        client._call = AsyncMock(return_value={"onboarded": True})

        result = await client.onboard("t1")
        client._call.assert_awaited_once_with("onboard_talent", talent_id="t1")
        assert result == {"onboarded": True}

    @pytest.mark.asyncio
    async def test_list_my_talents(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        client._call = AsyncMock(return_value={"talents": []})

        result = await client.list_my_talents()
        client._call.assert_awaited_once_with("list_my_talents")
        assert result == {"talents": []}

    @pytest.mark.asyncio
    async def test_list_available(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        client._call = AsyncMock(return_value={"talents": []})

        result = await client.list_available(role="Engineer", skills="python", page=2)
        client._call.assert_awaited_once_with(
            "list_available_talents", role="Engineer", skills="python", page=2, page_size=20
        )

    @pytest.mark.asyncio
    async def test_get_info(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        client._call = AsyncMock(return_value={"name": "Test"})

        result = await client.get_info("t1")
        client._call.assert_awaited_once_with("get_talent_info", talent_id="t1")

    @pytest.mark.asyncio
    async def test_get_cv(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        client._call = AsyncMock(return_value={"cv": "..."})

        result = await client.get_cv("t1")
        client._call.assert_awaited_once_with("get_talent_cv", talent_id="t1")


# ---------------------------------------------------------------------------
# start_talent_market / stop_talent_market
# ---------------------------------------------------------------------------

class TestStartStopTalentMarket:
    @pytest.mark.asyncio
    async def test_start_no_api_key(self, monkeypatch):
        """start_talent_market with no key configured should skip."""
        from onemancompany.agents import recruitment

        monkeypatch.setattr(
            recruitment, "load_app_config",
            lambda: {"talent_market": {"url": "http://test", "api_key": ""}},
        )
        # Reset singleton state
        recruitment.talent_market._session = None

        await recruitment.start_talent_market()
        assert not recruitment.talent_market.connected

    @pytest.mark.asyncio
    async def test_stop_delegates_to_disconnect(self, monkeypatch):
        """stop_talent_market should call talent_market.disconnect()."""
        from onemancompany.agents import recruitment

        mock_disconnect = AsyncMock()
        monkeypatch.setattr(recruitment.talent_market, "disconnect", mock_disconnect)

        await recruitment.stop_talent_market()
        mock_disconnect.assert_awaited_once()


# ---------------------------------------------------------------------------
# search_candidates
# ---------------------------------------------------------------------------

class TestSearchCandidates:
    @pytest.mark.asyncio
    async def test_returns_candidates_from_talent_market(self, monkeypatch):
        from onemancompany.agents import recruitment

        monkeypatch.setattr(
            recruitment, "load_app_config",
            lambda: {"talent_market": {"mode": "remote"}},
        )

        fake_result = {
            "type": "individual",
            "summary": "Test",
            "roles": [
                {
                    "role": "Engineer",
                    "description": "python dev",
                    "candidates": [
                        {"id": "c1", "name": "Candidate 1", "talent_id": "c1"},
                        {"id": "c2", "name": "Candidate 2", "talent_id": "c2"},
                    ],
                }
            ],
        }
        monkeypatch.setattr(recruitment.talent_market, "search", AsyncMock(return_value=fake_result))
        monkeypatch.setattr(recruitment.talent_market, "_session", True)  # Make connected=True

        result = await recruitment.search_candidates.ainvoke({"job_description": "python dev"})

        assert isinstance(result, dict)
        assert len(result["roles"]) == 1
        assert len(result["roles"][0]["candidates"]) == 2
        assert result["roles"][0]["candidates"][0]["name"] == "Candidate 1"
        assert "c1" in recruitment._last_search_results

        # Cleanup
        recruitment.talent_market._session = None

    @pytest.mark.asyncio
    async def test_fallback_to_local_talents_when_disconnected(self, monkeypatch):
        from onemancompany.agents import recruitment
        from onemancompany.core import config as config_mod

        # Ensure disconnected
        recruitment.talent_market._session = None

        monkeypatch.setattr(config_mod, "list_available_talents", lambda: [{"id": "local1"}])
        monkeypatch.setattr(
            config_mod, "load_talent_profile",
            lambda tid: {"id": "local1", "name": "Local Dev", "skills": [], "api_provider": "openrouter"},
        )
        monkeypatch.setattr(config_mod, "load_talent_skills", lambda tid: [])
        monkeypatch.setattr(config_mod, "load_talent_tools", lambda tid: [])

        result = await recruitment.search_candidates.ainvoke({"job_description": "any dev"})

        assert isinstance(result, dict)
        assert len(result["roles"]) >= 1
        assert len(result["roles"][0]["candidates"]) >= 1
        assert result["roles"][0]["candidates"][0]["id"] == "local1"

    @pytest.mark.asyncio
    async def test_fallback_on_talent_market_error(self, monkeypatch):
        from onemancompany.agents import recruitment
        from onemancompany.core import config as config_mod

        # Connected but search fails
        recruitment.talent_market._session = True  # Make connected=True
        monkeypatch.setattr(
            recruitment.talent_market, "search",
            AsyncMock(side_effect=RuntimeError("network error")),
        )

        monkeypatch.setattr(config_mod, "list_available_talents", lambda: [{"id": "local1"}])
        monkeypatch.setattr(
            config_mod, "load_talent_profile",
            lambda tid: {"id": "local1", "name": "Fallback Dev", "skills": [], "api_provider": "openrouter"},
        )
        monkeypatch.setattr(config_mod, "load_talent_skills", lambda tid: [])
        monkeypatch.setattr(config_mod, "load_talent_tools", lambda tid: [])

        result = await recruitment.search_candidates.ainvoke({"job_description": "any dev"})

        assert isinstance(result, dict)
        assert result["roles"][0]["candidates"][0]["id"] == "local1"

        # Cleanup
        recruitment.talent_market._session = None


# ---------------------------------------------------------------------------
# pending_candidates
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# session_id tracking
# ---------------------------------------------------------------------------

class TestSessionIdTracking:
    @pytest.mark.asyncio
    async def test_session_id_stashed_from_search(self, monkeypatch):
        """search_candidates stashes session_id from API response."""
        from onemancompany.agents import recruitment

        monkeypatch.setattr(
            recruitment, "load_app_config",
            lambda: {"talent_market": {"mode": "remote"}},
        )

        fake_result = {
            "type": "individual",
            "summary": "Test",
            "session_id": "ses_abc123",
            "roles": [{"role": "Dev", "description": "test", "candidates": [{"id": "c1"}]}],
        }
        monkeypatch.setattr(recruitment.talent_market, "_session", MagicMock())
        recruitment.talent_market.search = AsyncMock(return_value=fake_result)

        await recruitment.search_candidates.ainvoke({"job_description": "test"})
        assert recruitment._last_session_id == "ses_abc123"

    @pytest.mark.asyncio
    async def test_session_id_cleared_on_local_fallback(self, monkeypatch):
        """Local fallback clears session_id."""
        from onemancompany.agents import recruitment
        from onemancompany.core import config as config_mod

        recruitment._last_session_id = "old_session"
        monkeypatch.setattr(recruitment.talent_market, "_session", None)
        monkeypatch.setattr(config_mod, "list_available_talents", lambda: [])

        await recruitment.search_candidates.ainvoke({"job_description": "test"})
        assert recruitment._last_session_id == ""

    @pytest.mark.asyncio
    async def test_session_id_stored_in_project_ctx(self, monkeypatch):
        """submit_shortlist stores session_id in _pending_project_ctx."""
        from onemancompany.agents import recruitment

        recruitment._last_session_id = "ses_test123"
        recruitment._last_search_results["c1"] = {"id": "c1", "name": "Test"}
        recruitment.pending_candidates.clear()  # ensure guard doesn't block

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        monkeypatch.setattr("onemancompany.agents.recruitment.event_bus", mock_bus, raising=False)
        with patch("onemancompany.core.events.event_bus", mock_bus):
            await recruitment.submit_shortlist.ainvoke({
                "jd": "test jd",
                "candidate_ids": ["c1"],
            })

        # Find the batch_id that was created
        batch_ids = list(recruitment.pending_candidates.keys())
        assert len(batch_ids) >= 1
        bid = batch_ids[-1]
        assert recruitment._pending_project_ctx[bid]["session_id"] == "ses_test123"

        # Cleanup
        recruitment.pending_candidates.pop(bid, None)
        recruitment._pending_project_ctx.pop(bid, None)
        recruitment._last_search_results.clear()


# ---------------------------------------------------------------------------
# pending_candidates
# ---------------------------------------------------------------------------

class TestPendingCandidates:
    def test_store_and_retrieve(self):
        from onemancompany.agents.recruitment import pending_candidates

        pending_candidates.clear()
        batch_id = "test_batch"
        candidates = [{"id": "c1", "name": "Test"}]
        pending_candidates[batch_id] = candidates

        assert batch_id in pending_candidates
        assert pending_candidates[batch_id] == candidates

        # Cleanup
        pending_candidates.clear()


class TestSearchPassesUseAi:
    """TalentMarketClient.search() reads use_ai_search from config and passes it."""

    @pytest.mark.asyncio
    async def test_search_passes_use_ai_true(self, monkeypatch):
        from onemancompany.agents import recruitment

        captured_kwargs = {}

        async def fake_call(self, tool_name, _retry=True, **kwargs):
            captured_kwargs.update(kwargs)
            return {"roles": [], "session_id": ""}

        monkeypatch.setattr(recruitment.TalentMarketClient, "_call", fake_call)
        monkeypatch.setattr(
            "onemancompany.agents.recruitment.load_app_config",
            lambda: {"talent_market": {"use_ai_search": True}},
        )

        client = recruitment.TalentMarketClient()
        await client.search("need a python dev")

        assert captured_kwargs.get("use_ai") is True

    @pytest.mark.asyncio
    async def test_search_passes_use_ai_false_by_default(self, monkeypatch):
        from onemancompany.agents import recruitment

        captured_kwargs = {}

        async def fake_call(self, tool_name, _retry=True, **kwargs):
            captured_kwargs.update(kwargs)
            return {"roles": [], "session_id": ""}

        monkeypatch.setattr(recruitment.TalentMarketClient, "_call", fake_call)
        monkeypatch.setattr(
            "onemancompany.agents.recruitment.load_app_config",
            lambda: {"talent_market": {}},
        )

        client = recruitment.TalentMarketClient()
        await client.search("need a python dev")

        assert captured_kwargs.get("use_ai") is False
