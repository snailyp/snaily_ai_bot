claude code router配置gb示例

```json
{
  "LOG": true,
  "OPENAI_API_KEY": "",
  "OPENAI_BASE_URL": "",
  "OPENAI_MODEL": "",
  "Providers": [
    {
      "name": "gemini",
      "api_base_url": "https://gb地址/v1beta/models/",
      "api_key": "sk-xxxx",
      "models": [
        "gemini-2.5-pro",
		"gemini-2.5-flash"
      ],
	  "transformer": {
        "use": ["gemini"]
      }
    }
  ],
  "Router": {
    "default": "gemini,gemini-2.5-pro",
    "background": "gemini,gemini-2.5-flash",
    "webSearch": "gemini,gemini-2.5-flash"
  }
}
```

如果出现调用工具错误，需要在后台关闭URL_CONTEXT和CODE_EXECUTION。