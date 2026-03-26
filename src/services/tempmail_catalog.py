"""临时邮箱供应商目录与配置规范化工具。"""

from typing import Any, Dict, List, Optional

from ..config.constants import (
    TEMPMAIL_DEFAULT_PROVIDER,
    TEMPMAIL_GLOBAL_BUILTIN_KEY,
    TEMPMAIL_PROVIDER_ALIASES,
    TEMPMAIL_PROVIDER_CATALOG,
)


def normalize_tempmail_provider(provider: Optional[str]) -> str:
    """将供应商标识归一化为内部值。"""
    raw = str(provider or "").strip().lower()
    if not raw:
        return TEMPMAIL_DEFAULT_PROVIDER

    normalized = raw.replace("-", "_").replace(" ", "")
    return TEMPMAIL_PROVIDER_ALIASES.get(normalized, TEMPMAIL_DEFAULT_PROVIDER)


def get_tempmail_provider_meta(provider: Optional[str]) -> Dict[str, Any]:
    """获取供应商元信息。"""
    normalized = normalize_tempmail_provider(provider)
    return dict(TEMPMAIL_PROVIDER_CATALOG.get(normalized) or TEMPMAIL_PROVIDER_CATALOG[TEMPMAIL_DEFAULT_PROVIDER])


def list_tempmail_provider_options() -> List[Dict[str, Any]]:
    """返回前端可展示的供应商选项。"""
    options: List[Dict[str, Any]] = []
    for provider, meta in TEMPMAIL_PROVIDER_CATALOG.items():
        options.append({
            "value": provider,
            "label": meta.get("label", provider),
            "description": meta.get("description", ""),
            "call_style": meta.get("call_style", ""),
            "default_base_url": meta.get("default_base_url", ""),
        })
    return options


def build_tempmail_config(
    raw: Optional[Dict[str, Any]],
    settings: Any,
    provider_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """按供应商补齐并标准化临时邮箱配置。"""
    source = dict(raw or {})

    if "api_url" in source and "base_url" not in source:
        source["base_url"] = source.pop("api_url")

    provider = normalize_tempmail_provider(source.get("provider") or provider_hint)
    provider_meta = get_tempmail_provider_meta(provider)

    default_base_url = str(provider_meta.get("default_base_url") or "").strip()
    if provider == TEMPMAIL_DEFAULT_PROVIDER:
        default_base_url = str(getattr(settings, "tempmail_base_url", "") or default_base_url).strip()

    timeout = int(source.get("timeout") or getattr(settings, "tempmail_timeout", 30) or 30)
    max_retries = int(source.get("max_retries") or getattr(settings, "tempmail_max_retries", 3) or 3)

    config: Dict[str, Any] = {
        "provider": provider,
        "base_url": str(source.get("base_url") or default_base_url).strip(),
        "timeout": timeout,
        "max_retries": max_retries,
    }

    optional_text_fields = (
        "address_prefix",
        "preferred_domain",
        "fallback_domain",
        "api_key",
        "auth_style",
        "auth_placement",
        "auth_header_name",
        "api_key_header",
        "auth_query_key",
        "api_key_query_key",
        "auth_scheme",
        "create_method",
        "create_path",
        "domains_path",
        "inbox_path",
        "messages_path",
        "token_path",
    )
    for key in optional_text_fields:
        value = str(source.get(key) or "").strip()
        if value:
            config[key] = value

    return config


def build_tempmail_builtin_specs(settings: Any) -> List[Dict[str, Any]]:
    """构建系统预置临时邮箱服务定义。"""
    return [
        {
            "builtin_key": TEMPMAIL_GLOBAL_BUILTIN_KEY,
            "name": "全局临时邮箱（固定）",
            "provider": TEMPMAIL_DEFAULT_PROVIDER,
            "enabled": bool(getattr(settings, "tempmail_enabled", True)),
            "priority": 0,
            "is_builtin": True,
            "is_immutable": True,
            "config": build_tempmail_config(
                {"provider": TEMPMAIL_DEFAULT_PROVIDER, "base_url": getattr(settings, "tempmail_base_url", "")},
                settings,
            ),
        },
        {
            "builtin_key": "preset_mail_tm",
            "name": "Mail.tm 免费邮箱",
            "provider": "mail_tm",
            "enabled": False,
            "priority": 10,
            "is_builtin": True,
            "is_immutable": False,
            "config": build_tempmail_config({"provider": "mail_tm"}, settings),
        },
        {
            "builtin_key": "preset_mail_gw",
            "name": "Mail.gw 免费邮箱",
            "provider": "mail_gw",
            "enabled": False,
            "priority": 20,
            "is_builtin": True,
            "is_immutable": False,
            "config": build_tempmail_config({"provider": "mail_gw"}, settings),
        },
        {
            "builtin_key": "preset_onesecmail",
            "name": "1secmail 免费邮箱",
            "provider": "onesecmail",
            "enabled": False,
            "priority": 30,
            "is_builtin": True,
            "is_immutable": False,
            "config": build_tempmail_config({"provider": "onesecmail"}, settings),
        },
        {
            "builtin_key": "preset_guerrillamail",
            "name": "GuerrillaMail 免费邮箱",
            "provider": "guerrillamail",
            "enabled": False,
            "priority": 40,
            "is_builtin": True,
            "is_immutable": False,
            "config": build_tempmail_config({"provider": "guerrillamail"}, settings),
        },
    ]
