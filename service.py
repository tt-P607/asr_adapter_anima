"""ASR provider 注册服务。"""

from __future__ import annotations

from typing import Any

from src.core.components.base.service import BaseService

from .protocol import ASRProvider


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


__all__ = ["ASRProviderRegistryService"]