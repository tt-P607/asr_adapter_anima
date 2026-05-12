# asr_adapter

## 概述

`asr_adapter` 是本机麦克风实时语音识别适配器。

它本身不内置具体识别模型，而是提供一个本地麦克风 Adapter 和一个 ASR provider registry，具体识别后端由外部 provider 插件注册后供它消费。

当前默认联动方式通常是：

- `asr_adapter` 负责采集麦克风、激活逻辑、识别结果注入核心、播放核心回传语音
- `funasr_asr_provider` 负责提供 `funasr` 识别后端
- `voice_chatter` 负责把语音输入接进对话流程

## 提供的组件

- `asr_adapter:service:asr_provider_registry`
- `asr_adapter:adapter:asr_adapter`

其中：

- registry service 用于给外部 ASR provider 插件注册识别后端
- adapter 负责本地音频采集、识别循环、文本提交和 TTS 回放

## 依赖

插件自身没有声明插件级依赖，但运行时依赖以下 Python 包：

- `sounddevice`
- `numpy`
- `keyboard`

如果没有任何 provider 注册到 registry，adapter 将无法创建具体识别器。

## 配置

配置文件路径：

- `config/plugins/asr_adapter/config.toml`

该插件现在只保留 adapter 自己的通用配置，主要包括：

- `plugin`：启用状态、配置版本
- `bot`：注入核心时使用的 Bot / 说话人身份
- `audio`：麦克风采样率、通道数、输入设备、块大小、队列大小
- `activation`：VAD / 按键激活 / 常驻监听等监听激活方式
- `asr.provider`：选择要使用的 provider 名称
- `message`：文本过滤、partial 提交、置信度过滤等消息提交策略
- `playback`：核心回传语音的本地播放设置

具体模型参数不应再写在这里，而应放在具体 provider 插件自己的配置文件中。

## 典型联动

1. 外部 provider 插件在加载时向 `asr_provider_registry` 注册 provider。
2. `asr_adapter` 启动时根据 `config.asr.provider` 解析 provider。
3. adapter 使用 provider 创建音频源或识别器，并启动识别循环。
4. 识别文本按 adapter 配置过滤后注入核心消息流。
5. 当核心回传 voice/TTS 消息时，adapter 负责本地播放。

## 面向插件作者

如果你要接入新的 ASR 后端，推荐做法不是修改本插件，而是新建独立 provider 插件，并在加载时通过 `asr_adapter:service:asr_provider_registry` 注册。

## 相关插件

- `plugins/funasr_asr_provider`
- `plugins/voice_chatter`
