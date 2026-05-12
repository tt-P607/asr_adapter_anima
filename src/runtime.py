"""ASR adapter 运行时实现。"""

from __future__ import annotations

import asyncio
import base64
from collections import deque
import io
from pathlib import Path
import time
import uuid
import wave
from typing import Any, cast

from mofox_wire import MessageEnvelope

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.service_api import get_service

from ..config import AsrAdapterConfig
from ..protocol import ASRAudioSource, ASRProvider, ASRRecognizer
from ..service import ASRProviderRegistryService
from .text_quality import is_likely_normal_utterance

logger = get_logger("asr_adapter")


class AsrAdapterRuntimeMixin:
    """封装 ASR adapter 的识别与回放运行时。"""

    plugin: Any
    core_sink: Any
    platform: str

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """初始化运行时状态。"""

        super().__init__(*args, **kwargs)
        self._audio_source: ASRAudioSource | None = None
        self._recognizer: ASRRecognizer | None = None
        self._recognition_task_info: Any | None = None
        self._recognition_running = False
        self._last_partial_text = ""
        self._last_partial_emit_at = 0.0
        self._in_speech = False
        self._keyboard: Any | None = None
        self._toggle_active = False
        self._toggle_key_was_pressed = False
        self._playback_until = 0.0
        self._preroll_chunks: deque[Any] = deque()
        self._provider: ASRProvider | None = None

    @staticmethod
    def _get_task_manager() -> Any:
        """通过 plugin 模块暴露的 seam 获取 task manager。"""

        from .. import plugin as plugin_module

        return plugin_module.get_task_manager()

    async def on_adapter_loaded(self) -> None:
        """启动麦克风采集和流式识别后台任务。"""

        if not self.plugin or not self.plugin.config:
            raise RuntimeError("ASR 适配器启动失败：缺少插件配置")

        config = cast(AsrAdapterConfig, self.plugin.config)
        if not config.plugin.enabled:
            logger.info("ASR 适配器配置为禁用，跳过启动")
            return

        self._prepare_activation(config)
        logger.info(
            "ASR 监听激活方式: "
            f"mode={self._activation_mode(config)}, hotkey={config.activation.hotkey}"
        )
        self._provider = self._resolve_provider(config)
        self._provider.validate_config(config)
        self._recognizer = self._provider.create_recognizer(config)
        self._audio_source = self._provider.create_audio_source(config)
        await self._audio_source.start()

        self._recognition_running = True
        tm = self._get_task_manager()
        self._recognition_task_info = tm.create_task(
            self._recognition_loop(),
            name="asr_loop",
            daemon=True,
        )
        logger.info("ASR 适配器已启动")

    async def on_adapter_unloaded(self) -> None:
        """停止后台识别任务并释放音频资源。"""

        self._recognition_running = False
        if self._recognition_task_info:
            tm = self._get_task_manager()
            tm.cancel_task(self._recognition_task_info.task_id)
            self._recognition_task_info = None

        if self._audio_source is not None:
            await self._audio_source.stop()
            self._audio_source = None

        self._recognizer = None
        self._last_partial_text = ""
        self._last_partial_emit_at = 0.0
        self._in_speech = False
        self._keyboard = None
        self._toggle_active = False
        self._toggle_key_was_pressed = False
        self._playback_until = 0.0
        self._preroll_chunks.clear()
        self._provider = None
        logger.info("ASR 适配器已关闭")

    @staticmethod
    def _provider_name(config: AsrAdapterConfig) -> str | None:
        """返回当前配置请求的 provider 名称。"""

        provider_name = str(getattr(config.asr, "provider", "") or "").strip()
        return provider_name or None

    def _resolve_provider(self, config: AsrAdapterConfig) -> ASRProvider:
        """从 registry 中解析当前 adapter 使用的 provider。"""

        registry_service = get_service("asr_adapter:service:asr_provider_registry")
        if registry_service is None or not hasattr(registry_service, "get_provider"):
            raise RuntimeError("无法获取 asr_adapter provider registry service")
        registry = cast(ASRProviderRegistryService, registry_service)

        provider_name = self._provider_name(config)
        provider = registry.get_provider(provider_name)
        if provider is None:
            registered = []
            if hasattr(registry, "list_providers"):
                registered = list(registry.list_providers().get("providers", []))
            raise RuntimeError(
                "未找到可用的 ASR provider: "
                f"requested={provider_name or '<default>'}, registered={registered}"
            )
        return cast(ASRProvider, provider)

    async def from_platform_message(self, raw: Any) -> MessageEnvelope | None:  # type: ignore[override]
        """本地麦克风适配器不接收外部平台原始消息。"""

        _ = raw
        return None

    async def _send_platform_message(self, envelope: MessageEnvelope) -> None:  # type: ignore[override]
        """处理核心发回的外发消息。"""

        message_id = envelope.get("message_info", {}).get("message_id", "unknown")
        if await self._try_play_tts_envelope(envelope):
            logger.debug(f"已播放 local_asr 外发语音消息: {message_id}")
            return
        logger.debug(f"忽略发送到 local_asr 的非语音外发消息: {message_id}")

    def is_connected(self) -> bool:
        """返回麦克风采集和识别循环是否处于运行状态。"""

        return bool(
            self._recognition_running
            and self._audio_source is not None
            and self._audio_source.is_running()
            and self._recognizer is not None
        )

    async def health_check(self) -> bool:
        """检查 ASR 适配器健康状态。"""

        if self.plugin and self.plugin.config:
            config = cast(AsrAdapterConfig, self.plugin.config)
            if not config.plugin.enabled:
                return True
        return self.is_connected()

    async def get_bot_info(self) -> dict[str, Any]:  # type: ignore[override]
        """获取核心外发消息使用的 Bot 身份信息。"""

        if not self.plugin or not self.plugin.config:
            return {}
        config = cast(AsrAdapterConfig, self.plugin.config)
        return {
            "bot_id": config.bot.bot_id,
            "bot_name": config.bot.bot_name,
            "platform": self.platform,
        }

    async def _try_play_tts_envelope(self, envelope: MessageEnvelope) -> bool:
        """尝试从 outgoing envelope 中提取 voice/TTS 音频并播放。"""

        if not self.plugin or not self.plugin.config:
            return False
        config = cast(AsrAdapterConfig, self.plugin.config)
        if not config.playback.enabled:
            return False

        segments = envelope.get("message_segment") or []
        if isinstance(segments, dict):
            segments = [segments]
        if not isinstance(segments, list):
            return False

        tts_meta = self._extract_tts_meta(envelope)
        for segment in segments:
            if not isinstance(segment, dict) or segment.get("type") != "voice":
                continue
            audio_data = self._extract_audio_data(segment.get("data"), tts_meta)
            if audio_data is None:
                text = tts_meta.get("text") if isinstance(tts_meta, dict) else None
                logger.warning(
                    "收到 TTS voice 消息但缺少可播放音频数据"
                    + (f": {text}" if text else "")
                )
                return True
            await self._play_audio_bytes(audio_data, config)
            return True
        return False

    @staticmethod
    def _extract_tts_meta(envelope: MessageEnvelope) -> dict[str, Any]:
        """从 envelope extra 或 voice segment data 中提取 TTS 元数据。"""

        message_info = envelope.get("message_info") or {}
        extra = message_info.get("extra") if isinstance(message_info, dict) else None
        if isinstance(extra, dict) and isinstance(extra.get("tts"), dict):
            return dict(extra["tts"])

        segments = envelope.get("message_segment") or []
        if isinstance(segments, dict):
            segments = [segments]
        if isinstance(segments, list):
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                data = segment.get("data")
                tts_meta = data.get("tts") if isinstance(data, dict) else None
                if isinstance(tts_meta, dict):
                    return dict(tts_meta)
        return {}

    def _extract_audio_data(self, data: Any, tts_meta: dict[str, Any]) -> bytes | None:
        """从 voice segment 或 TTS 元数据中提取音频 bytes。"""

        if isinstance(data, bytes):
            return data
        if isinstance(data, dict):
            for key in ("audio_base64", "data"):
                value = data.get(key)
                if isinstance(value, str):
                    decoded = self._decode_audio_string(value)
                    if decoded is not None:
                        return decoded
            for key in ("path", "artifact_path"):
                value = data.get(key)
                if isinstance(value, str):
                    loaded = self._load_audio_file(value)
                    if loaded is not None:
                        return loaded
        if isinstance(data, str):
            decoded = self._decode_audio_string(data)
            if decoded is not None:
                return decoded
            loaded = self._load_audio_file(data)
            if loaded is not None:
                return loaded

        value = tts_meta.get("audio_base64")
        if isinstance(value, str):
            return self._decode_audio_string(value)
        value = tts_meta.get("artifact_path")
        if isinstance(value, str):
            return self._load_audio_file(value)
        return None

    @staticmethod
    def _decode_audio_string(value: str) -> bytes | None:
        """解码 base64 音频字符串。"""

        if not value.strip():
            return None
        try:
            return base64.b64decode(value, validate=True)
        except Exception:
            return None

    @staticmethod
    def _load_audio_file(path_value: str) -> bytes | None:
        """读取本地音频文件。"""

        path = Path(path_value)
        if not path.is_file():
            return None
        try:
            return path.read_bytes()
        except OSError as exc:
            logger.warning(f"读取 TTS 音频文件失败: {path} ({exc})")
            return None

    async def _play_audio_bytes(
        self,
        audio_data: bytes,
        config: AsrAdapterConfig,
    ) -> None:
        """使用 sounddevice 播放 WAV 或 float32 PCM 音频。"""

        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("缺少依赖 sounddevice，无法播放 TTS 音频") from exc

        samples, sample_rate = self._decode_audio_samples(audio_data, config)
        if samples.size == 0:
            return

        device = self._normalize_output_device(config.playback.output_device)
        duration = self._estimate_audio_duration(samples, sample_rate)
        self._playback_until = max(self._playback_until, time.monotonic() + duration + 0.3)
        sd.play(samples, samplerate=sample_rate, device=device)
        if config.playback.blocking:
            sd.wait()
            self._playback_until = max(self._playback_until, time.monotonic() + 0.3)

    @staticmethod
    def _estimate_audio_duration(samples: Any, sample_rate: int) -> float:
        """估算音频播放时长，用于短暂抑制麦克风回采。"""

        if sample_rate <= 0:
            return 0.0
        try:
            length = samples.shape[0]
        except AttributeError:
            length = len(samples)
        return float(length) / float(sample_rate)

    @staticmethod
    def _decode_audio_samples(
        audio_data: bytes,
        config: AsrAdapterConfig,
    ) -> tuple[Any, int]:
        """把 WAV 或原始 float32 PCM bytes 解码为 sounddevice 可播放数组。"""

        import numpy as np

        try:
            with wave.open(io.BytesIO(audio_data), "rb") as wav_file:
                sample_rate = wav_file.getframerate()
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                frames = wav_file.readframes(wav_file.getnframes())
        except wave.Error:
            return np.frombuffer(audio_data, dtype=np.float32), config.playback.fallback_sample_rate

        if sample_width == 1:
            samples = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif sample_width == 2:
            samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        elif sample_width == 4:
            samples = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
        else:
            raise ValueError(f"不支持的 WAV sample width: {sample_width}")

        if channels > 1:
            samples = samples.reshape(-1, channels)
        return samples, sample_rate

    @staticmethod
    def _normalize_output_device(device: str) -> str | int | None:
        """将配置中的输出设备转换为 sounddevice 可接受的类型。"""

        value = str(device or "").strip()
        if not value:
            return None
        if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
            return int(value)
        return value

    async def _recognition_loop(self) -> None:
        """持续读取麦克风音频并把识别文本提交到核心。"""

        while self._recognition_running:
            try:
                if self._audio_source is None or self._recognizer is None:
                    await asyncio.sleep(0.1)
                    continue

                config = cast(AsrAdapterConfig, self.plugin.config)
                pending_samples = [await self._audio_source.read()]
                pending_samples.extend(self._audio_source.drain_pending())

                if time.monotonic() < self._playback_until:
                    if self._recognizer is not None:
                        self._recognizer.new_stream()
                    self._in_speech = False
                    self._preroll_chunks.clear()
                    continue

                for samples in pending_samples:
                    accepted_chunks = self._select_audio_chunks(samples, config)
                    if not accepted_chunks:
                        if self._recognizer.has_audio() and self._is_explicit_activation_mode(config):
                            self._recognizer.mark_endpoint()
                            await self._emit_final_text()
                            self._recognizer.new_stream()
                        continue

                    for chunk in accepted_chunks:
                        await asyncio.to_thread(self._recognizer.accept_waveform, chunk)

                if config.message.commit_partial_results and self._can_run_partial_decode():
                    await asyncio.to_thread(self._recognizer.decode)
                    await self._maybe_emit_partial()

                if self._recognizer.is_endpoint():
                    await asyncio.to_thread(self._recognizer.decode)
                    await self._emit_final_text()
                    self._recognizer.new_stream()
                    self._in_speech = False
                    self._preroll_chunks.clear()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"ASR 识别循环异常: {exc}", exc_info=True)
                await asyncio.sleep(1.0)

    @staticmethod
    def _is_loud_enough(samples: Any, threshold: float) -> bool:
        """按 RMS 判断音频块是否超过能量门控阈值。"""

        if threshold <= 0:
            return True
        try:
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("缺少依赖 numpy，请先安装插件依赖") from exc

        array = np.asarray(samples, dtype=np.float32)
        if array.size == 0:
            return False
        rms = float(np.sqrt(np.mean(np.square(array))))
        return rms >= threshold

    def _prepare_activation(self, config: AsrAdapterConfig) -> None:
        """初始化监听激活状态。"""

        mode = self._activation_mode(config)
        self._toggle_active = bool(config.activation.toggle_initially_active)
        self._toggle_key_was_pressed = False
        if mode in {"push_to_talk", "toggle_key"}:
            self._keyboard = self._load_keyboard_backend()
        else:
            self._keyboard = None

    @staticmethod
    def _activation_mode(config: AsrAdapterConfig) -> str:
        """返回规范化的监听激活模式。"""

        mode = str(config.activation.mode or "vad").strip().lower()
        aliases = {
            "ptt": "push_to_talk",
            "push-to-talk": "push_to_talk",
            "push to talk": "push_to_talk",
            "key_hold": "push_to_talk",
            "hold_key": "push_to_talk",
            "toggle": "toggle_key",
            "key_toggle": "toggle_key",
            "always": "always_on",
        }
        mode = aliases.get(mode, mode)
        if mode not in {"vad", "push_to_talk", "toggle_key", "always_on"}:
            logger.warning(f"未知 ASR 激活方式 {mode!r}，将关闭音频识别以避免误实时监听")
            return "disabled"
        return mode

    def _is_explicit_activation_mode(self, config: AsrAdapterConfig) -> bool:
        """返回是否由按键/切换显式控制一段语音边界。"""

        return self._activation_mode(config) in {"push_to_talk", "toggle_key"}

    @staticmethod
    def _load_keyboard_backend() -> Any:
        """加载按键监听后端。"""

        try:
            import keyboard
        except ImportError as exc:
            raise RuntimeError("按键激活需要安装 keyboard 依赖：uv add keyboard") from exc
        return keyboard

    def _should_accept_audio_chunk(
        self,
        samples: Any,
        config: AsrAdapterConfig,
    ) -> bool:
        """根据激活模式判断当前音频块是否进入 ASR。"""

        mode = self._activation_mode(config)
        if mode == "always_on":
            self._in_speech = True
            return True
        if mode == "push_to_talk":
            active = self._is_hotkey_pressed(config)
            self._in_speech = active
            return active
        if mode == "toggle_key":
            self._update_toggle_activation(config)
            self._in_speech = self._toggle_active
            return self._toggle_active
        if mode == "disabled":
            self._in_speech = False
            return False

        threshold = self._activation_vad_threshold(config)
        loud_enough = self._is_loud_enough(samples, threshold)
        if not self._in_speech and not loud_enough:
            return False
        if loud_enough:
            self._in_speech = True
        return True

    def _select_audio_chunks(
        self,
        samples: Any,
        config: AsrAdapterConfig,
    ) -> list[Any]:
        """返回需要送入 ASR 的音频块，VAD 首次触发时补上前置音频。"""

        was_in_speech = self._in_speech
        if not self._should_accept_audio_chunk(samples, config):
            self._push_preroll_chunk(samples, config)
            return []

        if was_in_speech:
            return [samples]

        chunks = list(self._preroll_chunks)
        chunks.append(samples)
        self._preroll_chunks.clear()
        return chunks

    def _push_preroll_chunk(self, samples: Any, config: AsrAdapterConfig) -> None:
        """缓存短前置音频，降低 VAD 截断句首的概率。"""

        max_chunks = self._preroll_chunk_limit(config)
        if max_chunks <= 0:
            self._preroll_chunks.clear()
            return
        self._preroll_chunks.append(samples)
        while len(self._preroll_chunks) > max_chunks:
            self._preroll_chunks.popleft()

    @staticmethod
    def _preroll_chunk_limit(config: AsrAdapterConfig) -> int:
        """根据配置计算前置音频块数量。"""

        block_size = max(1, int(config.audio.block_size))
        sample_rate = max(1, int(config.audio.sample_rate))
        preroll_samples = int(sample_rate * max(0, config.activation.preroll_ms) / 1000)
        if preroll_samples <= 0:
            return 0
        return max(1, (preroll_samples + block_size - 1) // block_size)

    @staticmethod
    def _activation_vad_threshold(config: AsrAdapterConfig) -> float:
        """返回当前配置的 VAD 激活阈值。"""

        return float(config.activation.vad_threshold)

    def _is_hotkey_pressed(self, config: AsrAdapterConfig) -> bool:
        """返回热键当前是否按下。"""

        keyboard = self._keyboard or self._load_keyboard_backend()
        self._keyboard = keyboard
        hotkey = str(config.activation.hotkey or "space").strip() or "space"
        return bool(keyboard.is_pressed(hotkey))

    def _update_toggle_activation(self, config: AsrAdapterConfig) -> None:
        """根据热键边沿切换激活状态。"""

        pressed = self._is_hotkey_pressed(config)
        if pressed and not self._toggle_key_was_pressed:
            self._toggle_active = not self._toggle_active
            logger.info(f"ASR 按键切换监听: {'开启' if self._toggle_active else '关闭'}")
        self._toggle_key_was_pressed = pressed

    async def _maybe_emit_partial(self) -> None:
        """按配置提交流式中间识别结果。"""

        if not self.plugin or not self.plugin.config or self._recognizer is None:
            return
        config = cast(AsrAdapterConfig, self.plugin.config)
        if not config.message.commit_partial_results:
            return

        text = self._recognizer.get_text()
        if len(text) < config.message.min_text_length or text == self._last_partial_text:
            return
        if not self._should_accept_text(text, config):
            return

        now = time.monotonic()
        if now - self._last_partial_emit_at < config.message.partial_emit_interval:
            return

        self._last_partial_text = text
        self._last_partial_emit_at = now
        await self._send_text_to_core(text, is_final=False)

    def _can_run_partial_decode(self) -> bool:
        """采集积压时跳过高成本 partial 推理，优先保证最终整段识别稳定。"""

        if self._audio_source is None:
            return False
        capacity = self._audio_source.queue_capacity()
        if capacity <= 0:
            return True
        return self._audio_source.queue_size() <= max(1, capacity // 4)

    async def _emit_final_text(self) -> None:
        """提交端点检测后的最终识别结果。"""

        if not self.plugin or not self.plugin.config or self._recognizer is None:
            return
        config = cast(AsrAdapterConfig, self.plugin.config)
        text = self._recognizer.get_text()
        if len(text) < config.message.min_text_length:
            return
        if config.message.enable_confidence_filter:
            avg_confidence = self._recognizer.get_avg_confidence()
            token_confidences = self._recognizer.get_token_confidences()
            if avg_confidence is not None:
                if avg_confidence < config.message.min_avg_confidence:
                    logger.debug(
                        "丢弃低置信度 ASR 结果: "
                        f"avg={avg_confidence:.3f}, text={text}"
                    )
                    return
                if (
                    config.message.min_token_confidence > 0
                    and token_confidences
                    and min(token_confidences) < config.message.min_token_confidence
                ):
                    logger.debug(
                        "丢弃存在低置信度 token 的 ASR 结果: "
                        f"min={min(token_confidences):.3f}, text={text}"
                    )
                    return
        if not self._should_accept_text(text, config):
            return
        await self._send_text_to_core(text, is_final=True)
        self._last_partial_text = ""

    @staticmethod
    def _should_accept_text(text: str, config: AsrAdapterConfig) -> bool:
        """判断文本是否适合注入核心。"""

        normalized = text.strip()
        if not normalized:
            return False
        if normalized.startswith("/"):
            logger.debug(f"丢弃疑似误识别命令的 ASR 结果: text={normalized}")
            return False
        if AsrAdapterRuntimeMixin._is_obvious_noise_text(normalized):
            logger.debug(f"丢弃明显噪声 ASR 结果: text={normalized}")
            return False
        if config.message.enable_quality_filter:
            decision = is_likely_normal_utterance(
                normalized,
                min_chars=config.message.min_text_length,
                max_ascii_ratio=config.message.max_ascii_ratio,
                min_cjk_ratio=config.message.min_cjk_ratio,
                min_common_cjk_ratio=config.message.min_common_cjk_ratio,
            )
            if not decision.accepted:
                logger.debug(f"丢弃低质量 ASR 结果: reason={decision.reason}, text={normalized}")
                return False
        return True

    @staticmethod
    def _is_obvious_noise_text(text: str) -> bool:
        """过滤不需要配置开关的明显幻听文本。"""

        content = "".join(
            ch
            for ch in text.strip()
            if not ch.isspace() and ch not in "，。！？、；：,.!?;:()（）[]【】'\"“”‘’《》<>-—_"
        )
        if content in {"啊", "嗯", "呃", "额", "唔"}:
            return True
        if len(content) < 4:
            return False

        if len(set(content)) == 1:
            return True

        previous = ""
        run = 0
        max_run = 0
        for ch in content:
            if ch == previous:
                run += 1
            else:
                previous = ch
                run = 1
            max_run = max(max_run, run)

        return max_run >= 6 and max_run * 2 >= len(content) and len(set(content)) <= 3

    async def _send_text_to_core(self, text: str, *, is_final: bool) -> None:
        """将识别文本封装为标准 incoming 文本消息并发送到核心。"""

        text = text.strip()
        if not text or not self.core_sink:
            return
        if not self.plugin or not self.plugin.config:
            return

        config = cast(AsrAdapterConfig, self.plugin.config)
        envelope = {
            "direction": "incoming",
            "message_info": {
                "platform": self.platform,
                "message_id": f"asr-{uuid.uuid4()}",
                "message_type": "private",
                "time": time.time(),
                "user_info": {
                    "platform": self.platform,
                    "user_id": config.bot.speaker_id,
                    "user_nickname": config.bot.speaker_name,
                },
                "extra": {
                    "asr": {
                        "engine": "funasr",
                        "is_final": is_final,
                    }
                },
            },
            "message_segment": [{"type": "text", "data": text}],
            "raw_message": {
                "text": text,
                "source": "microphone",
                "engine": "funasr",
                "is_final": is_final,
            },
        }
        await self.core_sink.send(cast(MessageEnvelope, envelope))


__all__ = ["AsrAdapterRuntimeMixin"]