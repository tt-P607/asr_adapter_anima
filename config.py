"""原生 FunASR 实时语音识别适配器配置。"""

from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class AsrAdapterConfig(BaseConfig):
    """ASR 适配器配置。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "ASR 实时语音识别适配器配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        """插件基本配置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用 ASR 适配器",
            label="启用适配器",
            tag="plugin",
        )

    @config_section("bot", title="说话人配置", tag="user")
    class BotSection(SectionBase):
        """本地语音输入与外发 Bot 身份配置。"""

        bot_id: str = Field(
            default="local_asr_bot",
            description="核心外发消息写入历史时使用的 Bot ID",
            label="Bot ID",
            tag="user",
        )
        bot_name: str = Field(
            default="MoFox",
            description="核心外发消息写入历史时使用的 Bot 名称",
            label="Bot 名称",
            tag="user",
        )

        speaker_id: str = Field(
            default="local_microphone",
            description="注入核心消息时使用的本地说话人 ID",
            label="说话人 ID",
            tag="user",
        )
        speaker_name: str = Field(
            default="Local Microphone",
            description="注入核心消息时使用的本地说话人名称",
            label="说话人名称",
            tag="user",
        )

    @config_section("audio", title="音频采集", tag="performance")
    class AudioSection(SectionBase):
        """麦克风音频采集配置。"""

        sample_rate: int = Field(
            default=16000,
            description="麦克风采样率，需与模型期望采样率一致",
            label="采样率",
            ge=8000,
            le=48000,
            tag="performance",
        )
        channels: int = Field(
            default=1,
            description="输入通道数，当前适配器会转换为单声道",
            label="通道数",
            ge=1,
            le=2,
            tag="performance",
        )
        device: str = Field(
            default="",
            description="sounddevice 输入设备名称或索引，留空使用系统默认输入设备",
            label="输入设备",
            tag="performance",
        )
        block_size: int = Field(
            default=8000,
            description="每次音频回调的采样点数，8000 约等于 500ms@16kHz",
            label="块大小",
            ge=160,
            le=16000,
            tag="performance",
        )
        queue_max_chunks: int = Field(
            default=80,
            description="音频队列最大块数，满时丢弃旧块以保持实时性",
            label="队列大小",
            ge=1,
            le=200,
            tag="performance",
        )

    @config_section("activation", title="监听激活", tag="performance")
    class ActivationSection(SectionBase):
        """麦克风监听激活方式配置。"""

        mode: str = Field(
            default="vad",
            description="监听激活方式：vad 自动按音量激活，push_to_talk 按住热键激活，toggle_key 按键切换激活状态",
            label="激活方式",
            input_type="select",
            choices=["vad", "push_to_talk", "toggle_key", "always_on"],
            tag="performance",
        )
        hotkey: str = Field(
            default="space",
            description="按键激活使用的热键名称，支持 keyboard 库格式，如 space、ctrl+space、alt",
            label="激活热键",
            tag="performance",
        )
        vad_threshold: float = Field(
            default=0.003,
            description="VAD 自动激活的 RMS 音量阈值，低于阈值且尚未进入语音段时不送入识别；0 表示关闭",
            label="VAD 阈值",
            ge=0.0,
            le=1.0,
            tag="performance",
        )
        toggle_initially_active: bool = Field(
            default=False,
            description="toggle_key 模式启动时是否默认处于激活监听状态",
            label="切换模式默认激活",
            tag="performance",
        )
        preroll_ms: int = Field(
            default=500,
            description="VAD 触发后补送的前置音频时长，避免句首被截断",
            label="前置音频(ms)",
            ge=0,
            le=2000,
            tag="performance",
        )

    @config_section("asr", title="识别模型", tag="ai")
    class AsrSection(SectionBase):
        """ASR provider 选择配置。"""

        provider: str = Field(
            default="funasr",
            description="ASR provider 名称；asr_adapter 会从 provider registry 中查找，社区插件可注册自定义 provider",
            label="ASR Provider",
            tag="ai",
        )

    @config_section("message", title="消息提交", tag="text")
    class MessageSection(SectionBase):
        """识别结果注入核心的策略。"""

        min_text_length: int = Field(
            default=2,
            description="提交到核心的最短文本长度",
            label="最短文本长度",
            ge=1,
            le=100,
            tag="text",
        )
        commit_partial_results: bool = Field(
            default=False,
            description="是否把流式 partial 结果也提交到核心，默认关闭以避免多轮触发",
            label="提交中间结果",
            tag="text",
        )
        partial_emit_interval: float = Field(
            default=1.0,
            description="中间结果最小提交间隔秒数",
            label="中间结果间隔",
            ge=0.1,
            le=10.0,
            tag="text",
        )
        enable_quality_filter: bool = Field(
            default=False,
            description="是否启用 ASR 文本质量过滤，明显不像正常中文语句的结果会被丢弃",
            label="启用质量过滤",
            tag="text",
        )
        enable_confidence_filter: bool = Field(
            default=True,
            description="是否启用 token 置信度过滤；FunASR AutoModel 无置信度数据时不会丢弃",
            label="启用置信度过滤",
            tag="text",
        )
        min_avg_confidence: float = Field(
            default=-0.6,
            description="平均 token 置信度下限；后端未提供置信度时不会丢弃",
            label="平均置信度下限",
            ge=-20.0,
            le=1.0,
            tag="text",
            depends_on="enable_confidence_filter",
            depends_value=True,
        )
        min_token_confidence: float = Field(
            default=-3.0,
            description="单 token 置信度下限；后端未提供置信度时不会丢弃；-20 表示基本关闭",
            label="单 token 置信度下限",
            ge=-20.0,
            le=1.0,
            tag="text",
            depends_on="enable_confidence_filter",
            depends_value=True,
        )
        max_ascii_ratio: float = Field(
            default=0.2,
            description="允许的英文字母占比上限，用于过滤混入异常英文 token 的结果",
            label="英文字母占比上限",
            ge=0.0,
            le=1.0,
            tag="text",
            depends_on="enable_quality_filter",
            depends_value=True,
        )
        min_cjk_ratio: float = Field(
            default=0.65,
            description="中文字符占比下限，用于过滤非中文或乱码结果",
            label="中文占比下限",
            ge=0.0,
            le=1.0,
            tag="text",
            depends_on="enable_quality_filter",
            depends_value=True,
        )
        min_common_cjk_ratio: float = Field(
            default=0.45,
            description="常用汉字占比下限，用于过滤生僻字比例异常高的幻听结果",
            label="常用汉字占比下限",
            ge=0.0,
            le=1.0,
            tag="text",
            depends_on="enable_quality_filter",
            depends_value=True,
        )

    @config_section("playback", title="语音播放", tag="performance")
    class PlaybackSection(SectionBase):
        """核心外发语音的本地播放配置。"""

        enabled: bool = Field(
            default=True,
            description="是否播放核心发回的 voice/TTS 音频",
            label="启用播放",
            tag="performance",
        )
        output_device: str = Field(
            default="",
            description="sounddevice 输出设备名称或索引，留空使用系统默认输出设备",
            label="输出设备",
            tag="performance",
        )
        blocking: bool = Field(
            default=True,
            description="播放时是否阻塞直到本段音频结束；开启后可保持多段语音顺序播放",
            label="阻塞播放",
            tag="performance",
        )
        fallback_sample_rate: int = Field(
            default=24000,
            description="非 WAV 原始 PCM 数据的回退采样率",
            label="回退采样率",
            ge=8000,
            le=48000,
            tag="performance",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    bot: BotSection = Field(default_factory=BotSection)
    audio: AudioSection = Field(default_factory=AudioSection)
    activation: ActivationSection = Field(default_factory=ActivationSection)
    asr: AsrSection = Field(default_factory=AsrSection)
    message: MessageSection = Field(default_factory=MessageSection)
    playback: PlaybackSection = Field(default_factory=PlaybackSection)
