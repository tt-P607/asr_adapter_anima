"""ASR 本机麦克风实时语音识别适配器。"""

from __future__ import annotations

from typing import Any

from mofox_wire import CoreSink

from src.core.components.base import BaseAdapter, BasePlugin
from src.core.components.loader import register_plugin
from src.kernel.concurrency import get_task_manager

from .config import AsrAdapterConfig
from .service import ASRProviderRegistryService, ASRRedirectService
from .src.runtime import AsrAdapterRuntimeMixin


class AsrAdapter(AsrAdapterRuntimeMixin, BaseAdapter):
    """ASR 适配器。"""

    adapter_name = "asr_adapter_anima"
    adapter_version = "1.0.0"
    adapter_description = "基于本机麦克风实时语音识别适配器"
    platform = "local_asr"

    run_in_subprocess = False

    def __init__(
        self,
        core_sink: CoreSink,
        plugin: "AsrAdapterPlugin | None" = None,
        **kwargs: Any,
    ) -> None:
        """初始化 ASR 适配器。"""

        super().__init__(core_sink, plugin=plugin, **kwargs)


@register_plugin
class AsrAdapterPlugin(BasePlugin):
    """可插拔 ASR 适配器插件。"""

    plugin_name = "asr_adapter_anima"
    plugin_version = "1.0.0"
    plugin_author = "言柒 & 拾风"
    plugin_description = "本机麦克风实时语音识别适配器"
    configs = [AsrAdapterConfig]

    def get_components(self) -> list[type]:
        """返回插件包含的组件类。"""

        return [ASRProviderRegistryService, ASRRedirectService, AsrAdapter]
