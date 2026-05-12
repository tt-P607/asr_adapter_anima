"""ASR provider 协议定义。"""

from __future__ import annotations

from typing import Any, Protocol


class ASRAudioSource(Protocol):
    """ASR 音频源协议。"""

    async def start(self) -> None:
        """启动音频采集。"""

        ...

    async def stop(self) -> None:
        """停止音频采集。"""

        ...

    async def read(self) -> Any:
        """读取下一段音频。"""

        ...

    def drain_pending(self) -> list[Any]:
        """非阻塞取出当前已积压的音频块。"""

        ...

    def is_running(self) -> bool:
        """返回音频源是否正在运行。"""

        ...

    def queue_capacity(self) -> int:
        """返回内部队列容量。"""

        ...

    def queue_size(self) -> int:
        """返回当前积压队列大小。"""

        ...


class ASRRecognizer(Protocol):
    """流式 ASR 识别器协议。"""

    def accept_waveform(self, samples: Any) -> None:
        """写入一段音频。"""

        ...

    def decode(self) -> None:
        """执行一次中间推理。"""

        ...

    def get_text(self) -> str:
        """获取当前文本。"""

        ...

    def get_token_confidences(self) -> list[float]:
        """获取 token 置信度。"""

        ...

    def get_avg_confidence(self) -> float | None:
        """获取平均置信度。"""

        ...

    def is_endpoint(self) -> bool:
        """返回是否到达端点。"""

        ...

    def has_audio(self) -> bool:
        """返回是否已有缓存音频。"""

        ...

    def mark_endpoint(self) -> None:
        """强制标记当前段结束。"""

        ...

    def new_stream(self) -> None:
        """切换到新的识别段。"""

        ...


class ASRProvider(Protocol):
    """社区 ASR 插件需要实现的 provider 协议。"""

    provider_name: str

    def validate_config(self, config: Any) -> None:
        """校验当前 provider 所需配置。"""

        ...

    def create_audio_source(self, config: Any) -> ASRAudioSource:
        """创建音频源。"""

        ...

    def create_recognizer(self, config: Any) -> ASRRecognizer:
        """创建流式识别器。"""

        ...


__all__ = ["ASRAudioSource", "ASRProvider", "ASRRecognizer"]