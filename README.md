# asr_adapter (言柒 Fork 版)

> [!IMPORTANT]
> **关键依赖声明**：本插件是言柒针对 `tt-P607/anima_chatter` 定制的专用版本。如果您要使用言柒版本的 `anima_chatter` 插件的实时语音通话功能，**必须**搭配使用本仓库（`tt-P607/asr_adapter`）作为适配器。原作者拾风的 ASR 适配器缺少 `asr_redirect` 服务和按需启停等关键重定向接口，两者无法兼容。

## 概述

`asr_adapter` 是本机麦克风实时语音识别适配器。

它本身不内置具体识别模型，而是提供一个本地麦克风 Adapter 和一个 ASR provider registry，具体识别后端由外部 provider 插件注册后供它消费。

当前默认联动方式通常是：

- `asr_adapter` 负责采集麦克风、激活逻辑、识别结果注入核心、播放核心回传语音
- `funasr_asr_provider` 负责提供 `funasr` 识别后端
- `anima_chatter` 负责把语音输入接进对话流程（包含双向语音通话逻辑）

## 提供的组件

- `asr_adapter:service:asr_provider_registry`
- `asr_adapter:service:asr_redirect`
- `asr_adapter:adapter:asr_adapter`

其中：

- `asr_provider_registry` 用于给外部 ASR provider 插件注册识别后端。
- `asr_redirect` 供 `anima_chatter` 等需要实时通话的插件调用，用于动态转发 ASR 文本流到特定的平台与 StreamID，并在通话开始和结束时按需启停 ASR 运行时（无需常驻）。
- `asr_adapter` 负责本地音频采集、识别循环、文本提交和 TTS 回放。

## 与 `anima_chatter` 联动说明

本插件与 `anima_chatter` 紧密配合：
1. **自动按需启停**：在 `config.toml` 中，即使将 `plugin.enabled` 设置为 `false`（不让 ASR 适配器一直占用麦克风并常驻后台），当在 `anima_chatter` 中开启语音通话时，`anima_chatter` 仍会通过 `asr_redirect` 服务强行启动 ASR 服务，并在通话挂断时自动停用并释放麦克风。
2. **文本动态路由**：通话状态下，本插件会自动劫持 ASR 输出，以呼叫方的账号身份将文字定向发送至触发通话的 Stream，避免消息错乱。

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
- `plugins/anima_chatter`
