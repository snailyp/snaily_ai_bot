# 环境变量说明 ⚙️

| 配置项                       | 说明                                                     | 默认值                             |
| :--------------------------- | :------------------------------------------------------- | :---------------------------------------------------- |
| **数据库配置** 💾              |                                                          |                                                       |
| `MYSQL_HOST`                 | 必填，MySQL 数据库主机地址                               |`xxxxxxxx.h.aivencloud.com`                                            |
| `MYSQL_PORT`                 | 必填，MySQL 数据库端口                                   |`100000`                                                |
| `MYSQL_USER`                 | 必填，MySQL 数据库用户名                                 |`adafd`                                         |
| `MYSQL_PASSWORD`             | 必填，MySQL 数据库密码                                   | `123456`                                     |
| `MYSQL_DATABASE`             | 必填，MySQL 数据库名称                                   |`defaultdb`                                    |
| **API 相关配置** 🔑            |                                                          |                                                       |
| `API_KEYS`                   | 必填，Gemini API 密钥列表，用于负载均衡                        | `["gemini_key1","gemini_key2"]`  |
| `ALLOWED_TOKENS`             | 必填，允许访问的 Token 列表                                    |   `["allowed_key1","allowed_key2"]`    |
| `AUTH_TOKEN`                 | 可选，超级管理员token，具有所有权限，不填默认使用 ALLOWED_TOKENS 的第一个 |                   `sk-123456`                                |
| `TEST_MODEL`                 | 可选，用于测试密钥是否可用的模型名                             | `gemini-1.5-flash`                                    |
| `IMAGE_MODELS`               | 可选，支持绘图功能的模型列表                                   | `["gemini-2.0-flash-exp"]`                            |
| `SEARCH_MODELS`              | 可选，支持搜索功能的模型列表                                   | `["gemini-2.0-flash-exp"]`                            |
| `FILTERED_MODELS`            | 可选，被禁用的模型列表                                         | `["gemini-1.0-pro-vision-latest", ...]`               |
| `TOOLS_CODE_EXECUTION_ENABLED` | 可选，是否启用代码执行工具                                     | `false`                                               |
| `SHOW_SEARCH_LINK`           | 可选，是否在响应中显示搜索结果链接                             | `true`                                                |
| `SHOW_THINKING_PROCESS`      | 可选，是否显示模型思考过程                                     | `true`                                                |
| `THINKING_MODELS`            | 可选，支持思考功能的模型列表                                   | `[]`                                                  |
| `THINKING_BUDGET_MAP`        | 可选，思考功能预算映射 (模型名:预算值)                         | `{}`                                                  |
| `BASE_URL`                   | 可选，Gemini API 基础 URL，默认无需修改                        | `https://generativelanguage.googleapis.com/v1beta`    |
| `MAX_FAILURES`               | 可选，允许单个key失败的次数                                    | `3`                                                   |
| `MAX_RETRIES`                | 可选，API 请求失败时的最大重试次数                             | `3`                                                   |
| `CHECK_INTERVAL_HOURS`       | 可选，检查禁用 Key 是否恢复的时间间隔 (小时)                   | `1`                                                   |
| `TIMEZONE`                   | 可选，应用程序使用的时区                                       | `Asia/Shanghai`                                       |
| `TIME_OUT`                   | 可选，请求超时时间 (秒)                                        | `300`                                                 |
| `LOG_LEVEL`                  | 可选，日志级别，例如 DEBUG, INFO, WARNING, ERROR, CRITICAL     | `INFO`                                                |
| **图像生成相关** 🖼️            |                                                          |                                                       |
| `PAID_KEY`                   | 可选，付费版API Key，用于图片生成等高级功能                    | `your-paid-api-key`                                   |
| `CREATE_IMAGE_MODEL`         | 可选，图片生成模型                                             | `imagen-3.0-generate-002`                             |
| `UPLOAD_PROVIDER`            | 可选，图片上传提供商: `smms`, `picgo`, `cloudflare_imgbed`     | `smms`                                                |
| `SMMS_SECRET_TOKEN`          | 可选，SM.MS图床的API Token                                     | `your-smms-token`                                     |
| `PICGO_API_KEY`              | 可选，[PicoGo](https://www.picgo.net/)图床的API Key                                      | `your-picogo-apikey`                                  |
| `CLOUDFLARE_IMGBED_URL`      | 可选，[CloudFlare](https://github.com/MarSeventh/CloudFlare-ImgBed) 图床上传地址                                  | `https://xxxxxxx.pages.dev/upload`                    |
| `CLOUDFLARE_IMGBED_AUTH_CODE`| 可选，CloudFlare图床的鉴权key                                  | `your-cloudflare-imgber-auth-code`                    |
| **流式优化器相关** 💨          |                                                          |                                                       |
| `STREAM_OPTIMIZER_ENABLED`   | 可选，是否启用流式输出优化                                     | `false`                                               |
| `STREAM_MIN_DELAY`           | 可选，流式输出最小延迟                                         | `0.016`                                               |
| `STREAM_MAX_DELAY`           | 可选，流式输出最大延迟                                         | `0.024`                                               |
| `STREAM_SHORT_TEXT_THRESHOLD`| 可选，短文本阈值                                               | `10`                                                  |
| `STREAM_LONG_TEXT_THRESHOLD` | 可选，长文本阈值                                               | `50`                                                  |
| `STREAM_CHUNK_SIZE`          | 可选，流式输出块大小                                           | `5`                                                   |
