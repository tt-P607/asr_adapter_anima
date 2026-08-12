"""ASR 本机麦克风实时语音识别适配器。"""

from __future__ import annotations

from typing import Any

from mofox_wire import CoreSink

from src.app.plugin_system.base import BaseAdapter, BasePlugin, register_plugin

from .config import AsrAdapterConfig
from .service import ASRProviderRegistryService, ASRRedirectService
from .src.runtime import AsrAdapterRuntimeMixin


class AsrAdapter(AsrAdapterRuntimeMixin, BaseAdapter):
    """ASR 适配器。"""

    name = "asr_adapter_anima"
    description = "采集本机麦克风并通过可插拔 Provider 提交实时语音识别结果"
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
    """装配 ASR Provider Registry、通话重定向服务和麦克风适配器。"""

    plugin_name = "asr_adapter_anima"
    configs = [AsrAdapterConfig]

    def get_components(self) -> list[type]:
        """返回插件包含的组件类。"""

        return [ASRProviderRegistryService, ASRRedirectService, AsrAdapter]
