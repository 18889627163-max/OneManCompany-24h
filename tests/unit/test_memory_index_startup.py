from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from onemancompany.main import _prepare_memory_index


def _settings(**overrides):
    values = {
        "omc_memory_enabled": True,
        "omc_memory_embedding_base_url": "https://embedding.example.test/v1",
        "omc_memory_embedding_api_key": "secret-key-never-persist",
        "omc_memory_embedding_model": "embedding-model-v1",
        "omc_memory_embedding_dimensions": 4,
        "omc_memory_index_version": "v1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_prepare_memory_index_degrades_when_configuration_is_incomplete():
    index, embedding_status, vector_status = await _prepare_memory_index(
        _settings(omc_memory_embedding_api_key="")
    )

    assert index is None
    assert embedding_status == "degraded"
    assert vector_status == "unavailable"


@pytest.mark.asyncio
async def test_prepare_memory_index_probes_dimensions_and_records_safe_identity(monkeypatch):
    created = []

    class FakeOpenAIEmbeddings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(kwargs)

        async def aembed_query(self, text):
            return [0.0, 0.0, 0.0, 0.0]

    monkeypatch.setattr("langchain_openai.OpenAIEmbeddings", FakeOpenAIEmbeddings)
    settings = _settings()

    index, embedding_status, vector_status = await _prepare_memory_index(settings)

    assert embedding_status == "healthy"
    assert vector_status == "healthy"
    assert index["dims"] == 4
    assert index["embedding_model"] == "embedding-model-v1"
    assert created[0]["check_embedding_ctx_length"] is False
    assert index["provider_fingerprint"] == hashlib.sha256(
        b"https://embedding.example.test/v1"
    ).hexdigest()
    assert settings.omc_memory_embedding_api_key not in repr(index)


@pytest.mark.asyncio
async def test_prepare_memory_index_degrades_when_probe_fails(monkeypatch):
    class FailingOpenAIEmbeddings:
        def __init__(self, **kwargs):
            pass

        async def aembed_query(self, text):
            raise RuntimeError("provider body with secret")

    monkeypatch.setattr("langchain_openai.OpenAIEmbeddings", FailingOpenAIEmbeddings)

    index, embedding_status, vector_status = await _prepare_memory_index(_settings())

    assert index is None
    assert embedding_status == "degraded"
    assert vector_status == "unavailable"


@pytest.mark.asyncio
async def test_prepare_memory_index_rejects_probe_dimension_mismatch(monkeypatch):
    class WrongSizeOpenAIEmbeddings:
        def __init__(self, **kwargs):
            pass

        async def aembed_query(self, text):
            return [0.0, 0.0, 0.0]

    monkeypatch.setattr("langchain_openai.OpenAIEmbeddings", WrongSizeOpenAIEmbeddings)

    with pytest.raises(ValueError, match="configured=4, actual=3"):
        await _prepare_memory_index(_settings())
