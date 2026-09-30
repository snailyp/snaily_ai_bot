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

聊天模型用于普通聊天、私聊自动回复。任务模型用于群聊总结、热点摘要、linux.do 热门帖摘要（需在热点推送页开启）、知识库问答和现有信息查询摘要。任务请求不开放 MCP 工具；群聊记录和新闻文本不会因此触发外部动作。

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
    "max_rounds": 4,
    "max_calls": 8,
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
