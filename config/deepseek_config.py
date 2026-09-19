#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenAI-compatible 模型配置文件

该文件包含 DeepSeek 及其他兼容 OpenAI 聊天接口的模型提供方配置。
当前内置 DeepSeek 预设；其他兼容接口可通过通用环境变量
（AI_API_KEY / AI_BASE_URL / AI_CHAT_MODEL / AI_REASONING_MODEL）接入。
"""

import os
from typing import Any, Dict, Optional
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

PROVIDER_PRESETS: Dict[str, Dict[str, Any]] = {
    "deepseek": {
        "display_name": "DeepSeek",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "chat_model_env": "DEEPSEEK_CHAT_MODEL",
        "reasoning_model_env": "DEEPSEEK_REASONING_MODEL",
        "default_base_url": "https://api.deepseek.com",
        "default_chat_model": "deepseek-chat",
        "default_reasoning_model": "deepseek-reasoner",
        "supports_stream_options": True,
        "json_response_format_mode": "openai_object",
    },
}


def _normalize_provider_name(provider_name: Optional[str]) -> str:
    provider = (provider_name or "deepseek").strip().lower()
    return provider if provider in PROVIDER_PRESETS else "deepseek"


def _parse_bool_env(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _normalize_base_url(base_url: Optional[str]) -> str:
    normalized = (base_url or "").strip().strip("`'\"")
    return normalized.rstrip("/")


def _resolve_provider_settings(provider_name: Optional[str] = None) -> Dict[str, Any]:
    provider = _normalize_provider_name(provider_name or os.getenv("AI_PROVIDER", "deepseek"))
    preset = PROVIDER_PRESETS[provider]

    api_key = os.getenv("AI_API_KEY") or os.getenv(preset["api_key_env"], "")
    generic_base_url = os.getenv("AI_BASE_URL")
    if generic_base_url and generic_base_url.strip():
        base_url = generic_base_url.strip().strip("`'\"")
    else:
        base_url = _normalize_base_url(
            os.getenv(preset["base_url_env"], preset["default_base_url"]),
        )
    chat_model = os.getenv("AI_CHAT_MODEL") or os.getenv(
        preset["chat_model_env"], preset["default_chat_model"]
    )
    reasoning_model = os.getenv("AI_REASONING_MODEL") or os.getenv(
        preset["reasoning_model_env"], preset["default_reasoning_model"]
    )
    reasoning_model = reasoning_model or chat_model

    supports_stream_options = _parse_bool_env(
        os.getenv("AI_SUPPORTS_STREAM_OPTIONS"),
        bool(preset["supports_stream_options"]),
    )
    raw_json_response_format_mode = os.getenv("AI_JSON_RESPONSE_FORMAT_MODE")
    json_response_format_mode = (
        raw_json_response_format_mode.strip().lower()
        if raw_json_response_format_mode and raw_json_response_format_mode.strip()
        else preset["json_response_format_mode"]
    )
    if json_response_format_mode not in {"openai_object", "string"}:
        json_response_format_mode = "openai_object"

    supported_models = {
        chat_model: f'{preset["display_name"]} Chat模型',
        reasoning_model: f'{preset["display_name"]} 推理模型',
    }

    return {
        "provider": provider,
        "display_name": preset["display_name"],
        "api_key": api_key,
        "base_url": base_url,
        "chat_model": chat_model,
        "reasoning_model": reasoning_model,
        "supports_stream_options": supports_stream_options,
        "json_response_format_mode": json_response_format_mode,
        "supported_models": supported_models,
    }


DEFAULT_PROVIDER_SETTINGS = _resolve_provider_settings()
DEFAULT_PROVIDER = DEFAULT_PROVIDER_SETTINGS["provider"]
DEFAULT_DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEFAULT_DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", PROVIDER_PRESETS["deepseek"]["default_base_url"])
SUPPORTED_MODELS = DEFAULT_PROVIDER_SETTINGS["supported_models"]
DEFAULT_CHAT_MODEL = DEFAULT_PROVIDER_SETTINGS["chat_model"]
DEFAULT_REASONING_MODEL = DEFAULT_PROVIDER_SETTINGS["reasoning_model"]


class OpenAICompatibleConfig:
    """
    通用 OpenAI-compatible 配置类

    管理模型提供方的配置信息、能力开关和客户端实例。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        provider: Optional[str] = None,
        chat_model: Optional[str] = None,
        reasoning_model: Optional[str] = None,
        supports_stream_options: Optional[bool] = None,
        json_response_format_mode: Optional[str] = None,
    ):
        """
        初始化模型配置

        Args:
            api_key (str, optional): API密钥
            base_url (str, optional): 基础URL
            provider (str, optional): 提供方标识，例如 deepseek
            chat_model (str, optional): 默认聊天模型
            reasoning_model (str, optional): 默认推理模型
            supports_stream_options (bool, optional): 是否支持 stream_options
            json_response_format_mode (str, optional): JSON响应格式模式
        """
        settings = _resolve_provider_settings(provider)
        self.provider = settings["provider"]
        self.display_name = settings["display_name"]
        self.api_key = api_key if api_key is not None else settings["api_key"]
        self.base_url = base_url if base_url is not None else settings["base_url"]
        self.chat_model = chat_model if chat_model is not None else settings["chat_model"]
        self.reasoning_model = (
            reasoning_model if reasoning_model is not None else settings["reasoning_model"]
        ) or self.chat_model
        self.supports_stream_options = (
            supports_stream_options
            if supports_stream_options is not None
            else settings["supports_stream_options"]
        )
        self.json_response_format_mode = (
            (json_response_format_mode or settings["json_response_format_mode"]).strip().lower()
            or "openai_object"
        )
        self.client = None
        self._initialize_client()

    def _initialize_client(self):
        """初始化 OpenAI 客户端"""
        try:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        except Exception as e:
            raise RuntimeError(f"初始化 {self.display_name} 客户端失败: {e}")
    
    def get_client(self) -> OpenAI:
        """
        获取OpenAI客户端实例
        
        Returns:
            OpenAI: 客户端实例
        """
        if self.client is None:
            self._initialize_client()
        return self.client
    
    def update_config(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        provider: Optional[str] = None,
        chat_model: Optional[str] = None,
        reasoning_model: Optional[str] = None,
        supports_stream_options: Optional[bool] = None,
        json_response_format_mode: Optional[str] = None,
    ):
        """
        更新配置并重新初始化客户端
        
        Args:
            api_key (str, optional): 新的API密钥
            base_url (str, optional): 新的基础URL
            provider (str, optional): 新的提供方
            chat_model (str, optional): 新的聊天模型
            reasoning_model (str, optional): 新的推理模型
            supports_stream_options (bool, optional): 是否支持 stream_options
            json_response_format_mode (str, optional): JSON响应格式模式
        """
        if provider:
            refreshed_settings = _resolve_provider_settings(provider)
            self.provider = refreshed_settings["provider"]
            self.display_name = refreshed_settings["display_name"]
            self.api_key = refreshed_settings["api_key"]
            self.base_url = refreshed_settings["base_url"]
            self.chat_model = refreshed_settings["chat_model"]
            self.reasoning_model = refreshed_settings["reasoning_model"]
            self.supports_stream_options = refreshed_settings["supports_stream_options"]
            self.json_response_format_mode = refreshed_settings["json_response_format_mode"]
        if api_key:
            self.api_key = api_key
        if base_url:
            self.base_url = _normalize_base_url(base_url)
        if chat_model:
            self.chat_model = chat_model
        if reasoning_model:
            self.reasoning_model = reasoning_model
        if supports_stream_options is not None:
            self.supports_stream_options = supports_stream_options
        if json_response_format_mode:
            normalized_mode = json_response_format_mode.strip().lower()
            if normalized_mode in {"openai_object", "string"}:
                self.json_response_format_mode = normalized_mode
        self._initialize_client()
    
    def validate_config(self) -> bool:
        """
        验证配置是否有效
        
        Returns:
            bool: 配置有效返回True，否则返回False
        """
        if not self.api_key or not self.base_url:
            return False
        
        try:
            # 尝试创建客户端来验证配置
            _ = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
            return True
        except Exception:
            return False
    
    def get_model_info(self, model_name: str) -> Optional[str]:
        """
        获取模型信息
        
        Args:
            model_name (str): 模型名称
            
        Returns:
            str: 模型描述，如果模型不支持则返回None
        """
        return self.list_supported_models().get(model_name)
    
    def list_supported_models(self) -> dict:
        """
        获取支持的模型列表
        
        Returns:
            dict: 支持的模型字典
        """
        return {
            self.chat_model: f"{self.display_name} Chat模型",
            self.reasoning_model: f"{self.display_name} 推理模型",
        }

    def get_provider_name(self) -> str:
        """获取当前提供方标识。"""
        return self.provider

    def get_json_response_format_mode(self) -> str:
        """获取 JSON 响应格式模式。"""
        return self.json_response_format_mode

    def supports_stream_usage(self) -> bool:
        """是否支持 stream_options.include_usage。"""
        return bool(self.supports_stream_options)


DeepSeekConfig = OpenAICompatibleConfig


# ==================== 全局配置实例 ====================

# 创建默认配置实例
_default_config = OpenAICompatibleConfig()


def get_default_client() -> OpenAI:
    """
    获取默认的DeepSeek客户端
    
    Returns:
        OpenAI: 默认客户端实例
    """
    return _default_config.get_client()


def get_default_config() -> OpenAICompatibleConfig:
    """
    获取默认配置实例
    
    Returns:
        OpenAICompatibleConfig: 默认配置实例
    """
    return _default_config


def update_default_config(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    provider: Optional[str] = None,
    chat_model: Optional[str] = None,
    reasoning_model: Optional[str] = None,
    supports_stream_options: Optional[bool] = None,
    json_response_format_mode: Optional[str] = None,
):
    """
    更新默认配置
    
    Args:
        api_key (str, optional): 新的API密钥
        base_url (str, optional): 新的基础URL
        provider (str, optional): 新的提供方
        chat_model (str, optional): 新的聊天模型
        reasoning_model (str, optional): 新的推理模型
        supports_stream_options (bool, optional): 是否支持 stream_options
        json_response_format_mode (str, optional): JSON响应格式模式
    """
    _default_config.update_config(
        api_key=api_key,
        base_url=base_url,
        provider=provider,
        chat_model=chat_model,
        reasoning_model=reasoning_model,
        supports_stream_options=supports_stream_options,
        json_response_format_mode=json_response_format_mode,
    )


def create_custom_client(api_key: str, base_url: Optional[str] = None) -> OpenAI:
    """
    创建自定义客户端
    
    Args:
        api_key (str): API密钥
        base_url (str, optional): 基础URL，默认使用默认值
        
    Returns:
        OpenAI: 自定义客户端实例
    """
    config = OpenAICompatibleConfig(api_key=api_key, base_url=base_url)
    return config.get_client()


# ==================== 便捷函数 ====================

def get_chat_model() -> str:
    """获取默认聊天模型名称"""
    return _default_config.chat_model


def get_reasoning_model() -> str:
    """获取默认推理模型名称"""
    return _default_config.reasoning_model


def get_provider_name() -> str:
    """获取默认提供方名称。"""
    return _default_config.get_provider_name()


def supports_stream_options() -> bool:
    """获取当前提供方是否支持 stream_options.include_usage。"""
    return _default_config.supports_stream_usage()


def get_json_response_format_mode() -> str:
    """获取当前 JSON 响应格式模式。"""
    return _default_config.get_json_response_format_mode()


def is_model_supported(model_name: str) -> bool:
    """
    检查模型是否支持
    
    Args:
        model_name (str): 模型名称
        
    Returns:
        bool: 支持返回True，否则返回False
    """
    return model_name in _default_config.list_supported_models()


def validate_api_key(api_key: str) -> bool:
    """
    验证API密钥格式
    
    Args:
        api_key (str): 要验证的API密钥
        
    Returns:
        bool: 格式正确返回True，否则返回False
    """
    if not api_key or not isinstance(api_key, str):
        return False
    
    return len(api_key.strip()) >= 8
