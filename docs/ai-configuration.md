# AI 配置指南

## 升级与迁移

使用 Python 3.10+，在虚拟环境安装完整的 [依赖清单](../requirements.txt)。OpenAI SDK、Telegram SDK、HTTPX 和 AnyIO 必须一起升级；不要将新的 MCP SDK 强行装进旧依赖组合。

旧 `OPENAI_CONFIGS_JSON`、`OPENAI_*` 和 Redis `app_config` 可以继续读取，加载时自动迁移到 `schema_version: 2`。原活动配置成为聊天默认，任务默认跟随聊天。旧的温度和 token 设置保留为显式参数，若模型不支持，请在新页面取消发送。新建模型默认不带这些参数。

旧绘图配置首次迁移时会复制当时文本提供商的连接信息，此后两者完全独立。修改聊天密钥、默认模型或提供商不会再改变绘图。

配置优先级保持 **Redis → 环境变量**。选择从环境重新加载时，`AI_SERVICES_JSON` 优先于旧 AI 环境变量。没有 Redis 时，页面修改仅在内存中有效；需要长期保存的配置应写入部署环境或接入 Redis。请勿把密钥提交到 Git。

## 管理页使用顺序

1. 添加文本提供商：填写名称、Base URL、API Key 和可选 Headers。
2. 添加模型配置：选择提供商、手动填写模型 ID，选择 Chat Completions 或 Responses。
3. 分别选择聊天模型和任务模型；任务也可选择跟随聊天。
4. 在独立绘图区添加提供商和模型，再选择当前绘图模型。
5. 如需外部工具，配置 MCP 服务器、工具许可及模型的工具调用能力，再启用 MCP。
6. 保存配置。切换正在编辑的条目不会自动更换默认模型。

聊天模型用于普通聊天、私聊自动回复。任务模型用于群聊总结、热点摘要、linux.do 热门帖摘要（需在热点推送页开启）、知识库问答和现有信息查询摘要。这些摘要类任务请求不开放 MCP 工具；群聊记录和新闻文本不会因此触发外部动作。

[智能任务](smart-tasks.md)是独立的无人值守用途，可以单独选择支持工具的模型，并调用管理员为该任务明确授权的 MCP 服务器。服务器必须同时启用并开启「允许后台任务使用」（默认关闭）；智能任务不放宽聊天或既有摘要的权限规则。

### 参数与协议

| 设置 | Chat Completions | Responses |
| --- | --- | --- |
| 请求入口 | `/chat/completions` | `/responses` |
| 对话字段 | `messages` | `input` |
| 可选输出上限 | `max_completion_tokens` 或旧版 `max_tokens` | `max_output_tokens` |
| 推理强度 | `reasoning_effort` | `reasoning.effort` |
| 温度等可选参数 | 仅显式启用时发送 | 仅显式启用且该模型支持时发送 |

统一配置中的输出上限名为 `parameters.max_output_tokens`，由所选协议映射到请求字段。不要因为模型名字包含 GPT 就假定它接受所有参数。不需要参数时取消对应开关，而不是填 `0` 或 `null`。`temperature: 0` 是有效的显式设置，不等于关闭。

模型名称不受预设列表限制；中转服务没有模型发现接口时直接输入即可。模型列表获取成功只表示能读取列表，不能证明模型有权限或能够完成请求。文本连接测试会发送一次最小请求，可能产生费用。

### Headers 和凭证

自定义 Headers 适用于真实调用、模型列表和连接测试。大小写不敏感的 `Authorization` 可以覆盖 API Key 生成的鉴权头；Host、Content-Length 等传输控制头以及带换行的值不允许配置。不要在 URL 中携带密码。

页面仅显示“已设置”，不回传已保存的 AI 密钥、Header 值或 MCP 环境变量秘密。空白保留原值；删除凭证须使用明确的清除操作。删除 Header/环境变量的键会移除该项，而保留键且值空白表示沿用原值。

## 独立绘图提供商

| 类型 | 示例 Base URL | 模型示例 | 说明 |
| --- | --- | --- | --- |
| OpenAI Images | `https://api.openai.com/v1` | `gpt-image-1` | 支持 Base64 图片，也兼容旧 DALL·E URL 返回 |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta` | `gemini-2.5-flash-image` | Nano Banana 原生 `generateContent` 接口，不是 OpenAI 协议 |
| 火山方舟 Seedream | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seedream-4-0-250828` | 使用方舟 API Key，不是即梦 AK/SK 异步接口 |

以上仅为可编辑示例；以账户实际开通的模型版本/接入点 ID 为准。代理地址应包含其要求的版本路径，不需要填写最终 `/images/generations` 或 `:generateContent` 路径。

- GPT Image 不使用 DALL·E 的 `standard/hd` 默认质量，也不强制要求 URL 返回。
- Gemini 配置宽高比和输出图像大小；图像以 `inlineData` 返回。
- Seedream 的尺寸、输出格式、水印等按方舟协议传递。
- 图片可以通过 URL 或内存字节发送至 Telegram。本次提供 `/draw <描述>` 文生图，不包含图像编辑或多轮改图。
- 绘图的每日额度保留原有配置项；原项目尚未实现实际额度计数，不应把它视为已生效的计费保护。

## MCP：开关、许可与运行环境

MCP 是机器人连接外部工具的**客户端**能力，不是将本机器人开放成 MCP 服务器。

- 默认总开关关闭，新服务器也默认关闭；关闭不会删除配置。
- 支持 `stdio`、`streamable_http`、`sse`。HTTP 传输可配置鉴权 Headers；stdio 使用独立 command 和 args 数组，不通过 shell 执行。
- **stdio 会在机器人所在的机器或容器中运行配置命令。** 只使用可信程序，运行账号应具备最小权限；不要把不可信用户提供的命令直接填入后台。
- 默认只有 Telegram 管理员能在交互聊天中使用 MCP。开放给其他人时，还必须明确设置用户或群许可清单。
- 服务器 `allowed_tools: []` 表示不授权任何工具；填写精确工具名授权，`["*"]` 才表示允许该服务器全部工具。
- 所选聊天模型还需开启“支持工具调用”。MCP 总开关、服务器开关、用户许可、工具许可和模型能力缺一不可。
- 连接测试要求先保存并启用总开关及该服务器，只发现工具，不执行工具。stdio 测试仍会启动命令。
- 运行时工具名添加服务器命名空间，以防同名工具混淆。
- 可设置工具轮数、总次数、超时和结果长度上限；失败不会自动重试可能有副作用的工具。
- 配置变更在机器人自己的事件循环内应用。停用时停止接受新调用并释放连接/子进程；已被远端执行的操作不能由开关撤销。
- 单个服务器连接失败不阻止普通聊天。连接失败后可修改配置或停用后重新启用以重连。

仅运行 Web 控制面板时不会拥有机器人运行连接。页面显示“已配置”不等于“已连接”。MCP 工具返回内容属于不可信数据；不要将会读取秘密或执行高权限写操作的工具开放给不可信聊天用户。

## 环境变量完整结构示例

将下面对象压缩为 JSON 后填入 `AI_SERVICES_JSON`，或通过管理页填写。各模型的 `id` 是本地配置 ID，`model` 才是发送给提供商的模型名。

```json
{
  "schema_version": 2,
  "text": {
    "providers": [
      {
        "id": "main-text",
        "name": "文本提供商",
        "api_base_url": "https://api.openai.com/v1",
        "api_key": "REPLACE_ME",
        "headers": {},
        "timeout": 60
      }
    ],
    "models": [
      {
        "id": "conversation",
        "name": "聊天",
        "provider_id": "main-text",
        "model": "YOUR_TEXT_MODEL",
        "api_type": "responses",
        "token_limit_field": "max_completion_tokens",
        "supports_tools": false,
        "parameters": {}
      }
    ],
    "chat_model_id": "conversation",
    "task_model_id": ""
  },
  "drawing": {
    "providers": [
      {
        "id": "image-google",
        "name": "Nano Banana",
        "type": "gemini",
        "api_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key": "REPLACE_SEPARATELY",
        "headers": {},
        "timeout": 120
      }
    ],
    "models": [
      {
        "id": "banana",
        "name": "Nano Banana",
        "provider_id": "image-google",
        "model": "gemini-2.5-flash-image",
        "parameters": {"aspect_ratio": "1:1"}
      }
    ],
    "active_model_id": "banana"
  },
  "mcp": {
    "enabled": false,
    "admin_only": true,
    "allowed_user_ids": [],
    "allowed_chat_ids": [],
    "max_rounds": 40,
    "max_calls": 50,
    "timeout": 30,
    "max_result_chars": 12000,
    "servers": []
  }
}
```

## 联网搜索：Exa、Tavily、Firecrawl

`/search <关键词>` 先调用首选搜索服务，再由任务模型整理答案，并在末尾附上带链接的来源。

| 服务 | 默认地址 | 认证方式 |
| --- | --- | --- |
| Exa | `https://api.exa.ai` | `x-api-key` 请求头 |
| Tavily | `https://api.tavily.com` | `Authorization: Bearer tvly-…` |
| Firecrawl | `https://api.firecrawl.dev/v2` | `Authorization: Bearer fc-…` |

- 在管理页「联网搜索」中添加服务、填写密钥并选择首选服务。Base URL 留空表示使用官方地址。
- 开启「失败时自动切换服务」后，首选服务出错或没有结果时会依次尝试其他已启用的服务。
- 关闭「用任务模型整理结果」后，只列出搜索结果与摘要，不调用模型。
- 没有可用服务时，`/search` 退回 AI 知识库回答，并明确提示内容可能不是最新信息。
- 也可以用环境变量 `EXA_API_KEY`、`TAVILY_API_KEY`、`FIRECRAWL_API_KEY` 和 `SEARCH_PROVIDER` 配置。它们只在尚未保存任何搜索服务时生效，不会覆盖面板中的配置。

搜索结果来自第三方网页，按不可信数据处理。模型提示词要求只依据结果回答，不执行网页中的指令。搜索请求会把查询内容发送给所选服务商。

## Telegram 消息格式

处理器只编写普通 Markdown，例如 `**粗体**`、`` `代码` `` 和 `[标题](URL)`。发送前由 `bot.utils.helpers.reply_markdown` 统一转换为 MarkdownV2，并且只转换一次。转换两次会让用户看到 `\.`、`\-` 这类多余的反斜杠。Telegram 仍然拒绝解析时，消息会自动以纯文本重发。

## 语音识别、语音回复与图片理解

三项能力独立配置、默认关闭，不改变文字、任务或绘画模型。管理页中分别进入「语音识别」「语音回复」「图片理解」，添加服务商和模型、选择使用模型，再开启并保存。模型列表只做发现，不证明媒体能力可用；保存和发现不会自动执行付费媒体测试。实际测试请在知悉费用后主动向机器人发送媒体。

| 能力 | 服务商协议 | 配置分区 |
| --- | --- | --- |
| 语音识别 | OpenAI-compatible `POST /audio/transcriptions`，multipart WAV，JSON `text` | `asr` |
| 语音回复 | OpenAI-compatible `POST /audio/speech`，`voice` 音色，请求 WAV 后本地转为 Opus 语音 | `tts` |
| 图片理解 | Chat Completions 的 `image_url` 或 Responses 的 `input_image` | `vision` |

Base URL 填到 API 版本根路径（例如 `/v1`），不要包含上表中的最终端点。ASR/TTS 服务必须支持这些协议，不能仅凭 `/models` 成功判断。视觉模型必须支持图片输入；携带仍有效图片的后续聊天也使用视觉模型，沿用当前系统提示词且不调用 MCP。图片过期后只保留明确占位说明，并恢复普通文字模型。不包含文档图片、图片编辑或视频理解。

### 使用与限制

- 私聊沿用「私聊自动回复」开关，群聊须精确提及本机器人或回复本机器人消息；群会话仍按 chat ID 共享。总「AI 对话」开关同时控制媒体问答。
- 发送语音消息或常见音频文件（OGG/Opus、MP3、WAV、FLAC、AAC、M4A、WebM）。元数据和实际下载双重限制 **20 MB / 5 分钟**；本地验证实际时长和格式，先展示转写，再进入当前对话。空转写不会伪造结果。
- 发送单张照片或最多 **4 张**的相册；无附言描述图片，有附言回答问题。相册短暂收集、按消息顺序合并附言、重复项只处理一次；收集窗口内超过 4 张整组拒绝。Telegram 不提供相册结束信号，收集窗口结束后的同组更新忽略，请将延迟或丢失的图片作为新组重发。
- `/voice on|off|status` 按 chat ID 设置朗读偏好，默认关闭；群内修改需群管理员或配置的机器人管理员。总 TTS 开关还必须开启。始终先发完整文字，朗读跳过代码块、表格及原始链接；清理后的内容超过 **2,000 字符**时仅发送文字并说明。TTS 失败不影响已发文字，错误提示不朗读。
- 媒体下载及服务响应均有大小上限，HTTP 不跟随重定向；仅在 429/503 拒绝时最多重试一次，不跨服务商切换。处理队列、音频转换并发和子进程时间均有上限。当前只支持官方 Telegram HTTPS 文件地址，不支持本地 Bot API 文件路径。

### 依赖、存储与隐私

Docker 镜像已安装 `ffmpeg`（包含 `ffprobe`）；非容器部署需自行安装并加入 PATH，例如 Debian/Ubuntu 使用 `apt-get install ffmpeg`。缺失依赖只影响新语音功能，不影响文字聊天。音频用临时目录转换，成功、异常或取消时清理；禁止网络协议及播放列表解析，子进程不通过 Shell 执行。

首次使用媒体会提示：媒体和对话将提交给管理员配置的模型服务，服务商自身的数据留存由其政策决定，本地删除无法撤回已经发出的请求。原始音频不进入历史；图片保存为本地随机 ID，历史 JSON 仅记录引用，不存 Base64。图片在 `/reset`、历史淘汰、24 小时到期时清理；启动清理孤立/过期文件，运行中每 10 分钟回收，过期图片在回收前也不会再提交模型。转写和文字问答沿用原有历史规则（本地最多 100 条）。重置版本检查防止进行中的媒体任务重新写入已清空的历史。

`data/chat_settings.json` 独立保存语音偏好及隐私提示状态，重启保留，`/reset` 不重置偏好。请持久挂载容器 `/app/data` 并限制文件读取权限；此文件不含 API 密钥。模型配置仍遵循 Redis → 环境变量优先级，无 Redis 时管理页保存仍仅在内存生效，不会伪装成持久化。

环境初始化可用 `.env.example` 中独立的 `ASR_*`、`TTS_*`、`VISION_*` 变量。对应 `*_MODEL` 为空时不创建配置；也可在 `AI_SERVICES_JSON` 中填写完整分区（已指定分区优先于这些变量）：

```json
{
  "tts": {
    "enabled": false,
    "providers": [{"id": "speech-provider", "api_base_url": "https://api.openai.com/v1", "api_key": "REPLACE_ME", "headers": {}, "timeout": 60}],
    "models": [{"id": "speech-model", "provider_id": "speech-provider", "model": "YOUR_TTS_MODEL", "voice": "alloy", "parameters": {}}],
    "active_model_id": "speech-model"
  }
}
```

ASR 使用相同结构但模型无 `voice`；vision 模型使用文本模型的 `api_type`、`token_limit_field`、`parameters`，不开放工具。以上仅是合并到完整 AI 配置的分区示例。各分区密钥互不继承；空值保留、显式清除及自定义敏感 Headers 规则与文本服务商相同。

## 本地验证

纯配置、HTTP mock 和 MCP fake 测试不需要真实凭证或外部服务。安装依赖后：

```bash
python -m pip check
python -m unittest discover -s tests -p 'test_ai_*.py' -v
python -m unittest discover -s tests -p 'test_text_generation.py' -v
python -m unittest discover -s tests -p 'test_image_generation.py' -v
python -m unittest discover -s tests -p 'test_mcp*.py' -v
```

MCP transport 测试会启动仓库内无害的测试服务器，只使用本地 stdio 和 `127.0.0.1`，不会连接生产服务。

浏览器测试需要 Playwright 和本地 Chrome；在具有这两个依赖的环境中运行 `python tests/test_frontend.py`。真实模型、绘图或生产 MCP 的冒烟测试应另外明确开启，以免产生费用、启动未授权程序或读取真实数据。
