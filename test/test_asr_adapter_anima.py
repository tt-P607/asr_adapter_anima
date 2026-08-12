"""ASR Adapter Registry、重定向与运行时失败路径测试。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from plugins.asr_adapter_anima.config import AsrAdapterConfig  # noqa: E402
from plugins.asr_adapter_anima.service import ASRProviderRegistryService  # noqa: E402
from plugins.asr_adapter_anima.src import runtime as runtime_module  # noqa: E402
from plugins.asr_adapter_anima.src.runtime import AsrAdapterRuntimeMixin  # noqa: E402


class _AudioSource:
    """支持配置启动异常的测试音频源。"""

    def __init__(self, *, start_error: Exception | None = None) -> None:
        """保存启动异常并初始化调用状态。"""

        self.start_error = start_error
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        """启动音频源或抛出配置异常。"""

        self.started = True
        if self.start_error is not None:
            raise self.start_error

    async def stop(self) -> None:
        """记录停止操作。"""

        self.stopped = True

    async def read(self) -> Any:
        """返回空测试音频。"""

        return []

    def drain_pending(self) -> list[Any]:
        """返回空待处理队列。"""

        return []

    def is_running(self) -> bool:
        """返回音频源是否已启动且未停止。"""

        return self.started and not self.stopped

    def queue_capacity(self) -> int:
        """返回测试队列容量。"""

        return 1

    def queue_size(self) -> int:
        """返回测试队列大小。"""

        return 0


class _Recognizer:
    """返回固定文本与置信度的测试识别器。"""

    def __init__(
        self,
        *,
        text: str = "你好",
        avg_confidence: float | None = -0.5,
        token_confidences: list[float] | None = None,
    ) -> None:
        """保存识别结果。"""

        self.text = text
        self.avg_confidence = avg_confidence
        self.token_confidences = token_confidences or []

    def get_text(self) -> str:
        """返回固定文本。"""

        return self.text

    def get_avg_confidence(self) -> float | None:
        """返回平均置信度。"""

        return self.avg_confidence

    def get_token_confidences(self) -> list[float]:
        """返回 token 置信度。"""

        return list(self.token_confidences)

    def accept_waveform(self, samples: Any) -> None:
        """接收测试音频。"""

        _ = samples

    def decode(self) -> None:
        """执行空解码。"""

    def is_endpoint(self) -> bool:
        """返回未到端点。"""

        return False

    def has_audio(self) -> bool:
        """返回存在测试音频。"""

        return True

    def mark_endpoint(self) -> None:
        """标记测试端点。"""

    def new_stream(self) -> None:
        """重置测试流。"""


class _Provider:
    """创建固定音频源与识别器的测试 Provider。"""

    provider_name = "test"

    def __init__(self, audio_source: _AudioSource, recognizer: _Recognizer) -> None:
        """保存测试资源。"""

        self.audio_source = audio_source
        self.recognizer = recognizer
        self.validated = False

    def validate_config(self, config: Any) -> None:
        """记录配置校验。"""

        _ = config
        self.validated = True

    def create_audio_source(self, config: Any) -> _AudioSource:
        """返回测试音频源。"""

        _ = config
        return self.audio_source

    def create_recognizer(self, config: Any) -> _Recognizer:
        """返回测试识别器。"""

        _ = config
        return self.recognizer


@pytest.fixture(autouse=True)
def _reset_asr_module_state() -> Iterator[None]:
    """在每个测试前后清理 ASR Registry 与重定向状态。"""

    from plugins.asr_adapter_anima import service as service_module

    service_module._PROVIDERS.clear()
    service_module._DEFAULT_PROVIDER = None
    runtime_module.clear_redirect_target()
    runtime_module.set_activation_override(None)
    yield
    service_module._PROVIDERS.clear()
    service_module._DEFAULT_PROVIDER = None
    runtime_module.clear_redirect_target()
    runtime_module.set_activation_override(None)


def test_asr_registry_replaces_and_rotates_default() -> None:
    """ASR Registry 应替换同名实例并在注销后切换默认项。"""

    registry = ASRProviderRegistryService(plugin=cast(Any, object()))
    first = _Provider(_AudioSource(), _Recognizer())
    first.provider_name = "first"
    second = _Provider(_AudioSource(), _Recognizer())
    second.provider_name = "second"
    replacement = _Provider(_AudioSource(), _Recognizer())
    replacement.provider_name = "first"

    registry.register_provider(first)
    registry.register_provider(second)
    registry.register_provider(replacement)

    assert registry.get_provider() is replacement
    assert registry.list_providers() == {
        "default_provider": "first",
        "providers": ["first", "second"],
    }
    assert registry.unregister_provider("first") is True
    assert registry.get_provider() is second
    assert registry.unregister_provider("missing") is False


def test_redirect_target_requires_real_identity_and_can_be_cleared() -> None:
    """重定向必须携带目标与真实用户身份，并支持完整清理。"""

    with pytest.raises(ValueError, match="platform 和 stream_id"):
        runtime_module.set_redirect_target("", "stream", user_id="user")
    with pytest.raises(ValueError, match="redirect 必须带 user_id"):
        runtime_module.set_redirect_target("qq", "stream", user_id="")

    runtime_module.set_redirect_target(
        "qq",
        "stream-1",
        user_id="10001",
        user_name="Tester",
    )
    target = runtime_module.get_redirect_target()
    assert target is not None
    assert target.platform == "qq"
    assert target.stream_id == "stream-1"
    assert target.user_id == "10001"

    runtime_module.clear_redirect_target()
    assert runtime_module.get_redirect_target() is None


@pytest.mark.asyncio
async def test_runtime_start_failure_rolls_back_created_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """音频源启动失败时，运行时应停止资源并清空全部状态。"""

    config = AsrAdapterConfig()
    audio_source = _AudioSource(start_error=RuntimeError("microphone unavailable"))
    recognizer = _Recognizer()
    provider = _Provider(audio_source, recognizer)
    runtime = AsrAdapterRuntimeMixin()
    runtime.plugin = SimpleNamespace(config=config)
    monkeypatch.setattr(runtime, "_resolve_provider", lambda _config: provider)

    with pytest.raises(RuntimeError, match="microphone unavailable"):
        await runtime._start_runtime()

    assert provider.validated is True
    assert audio_source.started is True
    assert audio_source.stopped is True
    assert runtime._recognition_running is False
    assert runtime._audio_source is None
    assert runtime._recognizer is None
    assert runtime._provider is None
    assert runtime._recognition_task_info is None


@pytest.mark.asyncio
async def test_negative_token_confidence_threshold_filters_final_text() -> None:
    """负数 token 阈值也必须生效，低于阈值的结果不能注入核心。"""

    config = AsrAdapterConfig()
    config.message.enable_quality_filter = False
    config.message.enable_confidence_filter = True
    config.message.min_avg_confidence = -0.6
    config.message.min_token_confidence = -3.0

    runtime = AsrAdapterRuntimeMixin()
    runtime.plugin = SimpleNamespace(config=config)
    runtime._send_text_to_core = AsyncMock()  # type: ignore[method-assign]
    runtime._recognizer = _Recognizer(
        avg_confidence=-0.5,
        token_confidences=[-0.4, -3.5],
    )

    await runtime._emit_final_text()

    runtime._send_text_to_core.assert_not_awaited()


@pytest.mark.asyncio
async def test_acceptable_token_confidence_emits_final_text() -> None:
    """达到平均值和 token 阈值的正常文本应被提交。"""

    config = AsrAdapterConfig()
    config.message.enable_quality_filter = False
    config.message.enable_confidence_filter = True
    config.message.min_avg_confidence = -0.6
    config.message.min_token_confidence = -3.0

    runtime = AsrAdapterRuntimeMixin()
    runtime.plugin = SimpleNamespace(config=config)
    runtime._send_text_to_core = AsyncMock()  # type: ignore[method-assign]
    runtime._recognizer = _Recognizer(
        avg_confidence=-0.5,
        token_confidences=[-0.4, -2.5],
    )

    await runtime._emit_final_text()

    runtime._send_text_to_core.assert_awaited_once_with("你好", is_final=True)
