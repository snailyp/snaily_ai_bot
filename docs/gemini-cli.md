添加对 gemini cli 支持

使用方法

启动 balance 服务
```shell
uvicorn app.main:app --host 0.0.0.0 --port 19222 --reload
```

配置gemini cli （以下配置在win11 环境，其他环境请参考官方文档）
```json
$env:GOOGLE_GEMINI_BASE_URL="http://localhost:19222"
$env:GEMINI_API_KEY = "sk-123456" 
```

启动gemini cli
`gemini`
or
`gemini -m gemini-2.5-flash`