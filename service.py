"""ASR provider 注册服务 + ASR 文本转发控制服务。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base.service import BaseService

from .protocol import ASRProvider


logger = get_logger("asr_adapter.service")


_PROVIDERS: dict[str, ASRProvider] = {}
_DEFAULT_PROVIDER: str | None = None


class ASRProviderRegistryService(BaseService):
    """供社区插件注册 ASR provider 的服务。"""

    service_name = "asr_provider_registry"
    service_description = "ASR provider registry"
    version = "1.0.0"

    def register_provider(self, provider: ASRProvider, *, default: bool = False) -> None:
        """注册一个 ASR provider。"""

        global _DEFAULT_PROVIDER
        provider_name = str(getattr(provider, "provider_name", "") or "").strip()
        if not provider_name:
            raise ValueError("ASR provider 必须声明 provider_name")
        _PROVIDERS[provider_name] = provider
        if default or _DEFAULT_PROVIDER is None:
            _DEFAULT_PROVIDER = provider_name

    def unregister_provider(self, provider_name: str) -> bool:
        """注销 provider。"""

        global _DEFAULT_PROVIDER
        removed = _PROVIDERS.pop(provider_name, None) is not None
        if _DEFAULT_PROVIDER == provider_name:
            _DEFAULT_PROVIDER = next(iter(_PROVIDERS), None)
        return removed

    def get_provider(self, provider_name: str | None = None) -> ASRProvider | None:
        """获取指定或默认 provider。"""

        name = provider_name or _DEFAULT_PROVIDER
        if not name:
            return None
        return _PROVIDERS.get(name)

    def list_providers(self) -> dict[str, Any]:
        """列出 provider 注册状态。"""

        return {
            "default_provider": _DEFAULT_PROVIDER,
            "providers": sorted(_PROVIDERS),
        }


class ASRRedirectService(BaseService):
    """ASR 文本转发 + 按需启停 ASR runtime 的控制服务。

    供其他插件（典型场景：``anima_chatter`` 的语音通话功能）：

    1. 临时把 ASR 识别出的文本路由到非默认 stream（``set_target`` / ``clear_target``）；
    2. 即便 ``plugin.enabled=false``（用户没把 ASR 配为常驻），通话开始时也能
       按需启动麦克风采集和识别循环（``start_voice_call_session`` /
       ``end_voice_call_session``）。

    设计：
    - 本服务是无状态门面——所有状态都存在
      :mod:`plugins.asr_adapter.src.runtime` 的模块级变量 + adapter 实例属性。
    - 之所以 expose 成 Service 而不是 Python 函数：插件之间禁止源码级
      互相 import（见 ``AI插件编写规范.md`` 第 11 条），只能通过
      Service / 事件 / 公开 API 通信。
    - ServiceManager 每次 ``get_service()`` 返回新实例（见 plugin 规范 6.1.2），
      所以本类**绝对不能**带实例字段；所有方法都委托到模块级函数 / 通过
      ``adapter_api.get_adapter`` 找到 adapter 实例。
    """

    service_name = "asr_redirect"
    service_description = (
        "ASR 文本转发 + 按需启停 ASR runtime：让其他插件临时把识别文本路由到非默认 stream，"
        "并在 ASR 适配器未常驻时按需启动 / 停止麦克风采集。"
    )
    version = "1.1.0"

    _ADAPTER_SIGNATURE = "asr_adapter_anima:adapter:asr_adapter_anima"

    def set_target(
        self,
        platform: str,
        stream_id: str,
        *,
        user_id: str,
        user_name: str = "",
        group_id: str = "",
    ) -> None:
        """开启转发：让 ASR 识别文本注入到指定 (platform, stream_id) + 冒名真实用户。

        Args:
            platform: 目标平台名（如 ``"qq"``）。
            stream_id: 目标 stream_id。
            user_id: 通话发起方在该平台上的真实 ID（如 QQ 号）——envelope 会
                以这个 ID 作为消息发送方。**不可为空**：用 ASR 适配器自身的
                ``speaker_id`` 在跨平台时会触发"创建新用户 + 创建新 stream"
                的连环错误。
            user_name: 通话发起方在该平台上的昵称。可为空。
            group_id: 群聊通话预留字段；私聊留空。

        Raises:
            ValueError: 关键参数为空。
        """

        from .src.runtime import set_redirect_target

        set_redirect_target(
            platform,
            stream_id,
            user_id=user_id,
            user_name=user_name,
            group_id=group_id,
        )

    def clear_target(self) -> None:
        """关闭转发，恢复默认（发到 ``local_asr`` 平台的 default stream）。"""

        from .src.runtime import clear_redirect_target

        clear_redirect_target()

    def is_redirected(self) -> bool:
        """返回当前是否处于转发状态。"""

        from .src import runtime as _runtime

        return _runtime._redirect_target is not None  # noqa: SLF001 — 受控读

    def _get_adapter(self) -> Any:
        """通过 adapter_api 拿到 asr_adapter 实例。"""

        from src.app.plugin_system.api import adapter_api

        return adapter_api.get_adapter(self._ADAPTER_SIGNATURE)

    async def start_voice_call_session(
        self,
        target_platform: str,
        target_stream_id: str,
        *,
        target_user_id: str,
        target_user_name: str = "",
        target_group_id: str = "",
        force_always_on: bool = True,
    ) -> bool:
        """通话开始时一次性配齐：启动 runtime + 设置 redirect（含真实身份） + 切到 always_on。

        这是 anima_chatter 的 voice_call action 应该调用的"高层"接口——
        把"按需启 ASR、把文本路由到目标 stream + 冒名真实用户、临时切到
        always_on 收音"四步打包。

        Args:
            target_platform: ASR 文本应注入的目标平台。
            target_stream_id: ASR 文本应注入的目标 stream。
            target_user_id: 通话发起方在目标平台上的真实 ID（如 QQ 号）。**必填**。
            target_user_name: 通话发起方的昵称。可为空。
            target_group_id: 群聊通话预留字段；私聊留空。
            force_always_on: 通话期间是否强制 always_on（默认 True）；
                关掉这个开关时保持 config 里的原激活模式。

        Returns:
            bool: True 表示完整启动成功；False 表示 ASR runtime 启动失败
                （此时 redirect 不会被设置）。
        """

        adapter = self._get_adapter()
        if adapter is None:
            logger.error(
                f"未找到 adapter {self._ADAPTER_SIGNATURE}，无法启动通话 ASR 会话"
            )
            return False

        # 1) 按需启动 runtime（如果用户配置 enabled=true 并已经在跑，无操作）
        ok = await adapter.ensure_runtime_started()
        if not ok:
            logger.error("ASR runtime 启动失败，放弃 voice_call 会话")
            return False

        # 2) 按需切到 always_on（让通话期间无需按按键）
        if force_always_on:
            from .src.runtime import set_activation_override

            set_activation_override("always_on")

        # 3) 最后才设 redirect，避免中间任何步骤失败时已有"半启动"状态
        try:
            self.set_target(
                target_platform,
                target_stream_id,
                user_id=target_user_id,
                user_name=target_user_name,
                group_id=target_group_id,
            )
        except Exception:
            # 回滚 always_on 覆写
            from .src.runtime import set_activation_override

            set_activation_override(None)
            raise

        return True

    async def end_voice_call_session(self) -> None:
        """通话结束时一次性清理：清 redirect + 还原激活模式 + 视情况停 runtime。"""

        # 1) 先清 redirect（之后哪怕还有 ASR 在跑也会回到 local_asr 默认路由）
        self.clear_target()

        # 2) 清激活模式覆写
        from .src.runtime import set_activation_override

        set_activation_override(None)

        # 3) 如果是被通话临时启动的（plugin.enabled=false），停 runtime
        adapter = self._get_adapter()
        if adapter is not None and hasattr(adapter, "stop_runtime_if_dynamic"):
            try:
                await adapter.stop_runtime_if_dynamic()
            except Exception as exc:
                logger.warning(f"停止 ASR runtime 失败: {exc}", exc_info=True)


__all__ = ["ASRProviderRegistryService", "ASRRedirectService"]