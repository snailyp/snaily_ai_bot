# HuggingFace部署数据库版本 🚀
> [!CAUTION]
>
> 不推荐huggingface部署了，现在风控很严重，并且openai格式的请求头也会被删

## 一、准备工作 准备好了吗？清单在这里！📋

- mysql数据库服务
- huggingface账号
- gemini_api_key

## 二、创建MySQL数据库 💾

### 1. 注册 [aiven](https://console.aiven.io/) 账号 📝

### 2. 登录并选择个人计划，地区选择美国（与 Hugging Face IP 保持一致）🇺🇸

![alt text](image.png)

![alt text](image-1.png)

### 3. 创建 MySQL 服务，选择免费计划 🆓

![alt text](image-2.png)

![alt text](image-3.png)

### 4. 记录下数据库配置信息（后续部署服务时需要用到），这些信息对应 Gemini Balance 服务配置中的以下项（见图示）👇

![alt text](image-4.png)

## 三、Hugging Face 部署服务 ☁️

### 1. 注册或登录 Hugging Face 账号 🔑

### 2. 创建 Space，空间名可以随意填写（尽量独一无二），选择 Docker 环境。🐳

![alt text](image-5.png)

![alt text](image-6.png)

![alt text](image-7.png)

### 3. 选择 Files 选项卡，创建一个名为 `Dockerfile` 的文件。📄

![alt text](image-8.png)

填入下面内容，点击页面最下方的`commit new file to main`按钮

```bash
FROM ghcr.io/snailyp/gemini-balance:latest
EXPOSE 7860
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
```

![alt text](image-9.png)

### 4. 添加必要的环境变量 📝

选择settings，往下拉到Variables and secrets，new variavble新建公开变量，new secret新建私密变量（比如gemini密钥）

![alt text](image-10.png)

这些变量是必填的，正确填完之后，服务就会正常运行了
按顺序一个一个添加环境变量，先API 相关配置，再时区配置，最后数据库配置

| 配置项                       | 说明                                                     | 示例 |
| :--------------------------- | :------------------------------------------------------- | :--|
| **API 相关配置**             |                                                          | |
| `API_KEYS`                   | 必填，Gemini API 密钥列表可以配置一个值，批量设置可以在web端进行（比较方便），用于负载均衡                        | `["gemini_key"]` 数组，一定要包含中括号和引号 |
| `ALLOWED_TOKENS`             | 必填，允许访问的 Token 列表，该值为自定义值，可用于api调用，分享给朋友使用                                    | `["allowed_key1"]` 数组，一定要包含中括号和引号|
| `AUTH_TOKEN`                 | 可选，超级管理员token，具有所有权限也可以用于api调用，不填默认使用 ALLOWED_TOKENS 的第一个，web端登录需要用这个值 | `sk-123456` |
| **时区配置**             |                                                          | |
| `TZ`                 | 时区设置，建议值 `Asia/Shanghai` | |
| **数据库配置**               |                                                          | |
| `MYSQL_HOST`                 | 必填，MySQL 数据库主机地址                               |    `xxxxxxxx.h.aivencloud.com` |
| `MYSQL_PORT`                 | 必填，MySQL 数据库端口                                   | `100000` |
| `MYSQL_USER`                 | 必填，MySQL 数据库用户名                                 | `adafd` |
| `MYSQL_PASSWORD`             | 必填，MySQL 数据库密码                                   | `123456` |
| `MYSQL_DATABASE`             | 必填，MySQL 数据库名称                                   | `defaultdb` |

![alt text](image-11.png)

![alt text](image-12.png)

## 四、访问 Web 端 🌐

按照下面的步骤复制出请求 URL（这将是您在各种客户端中填写的 `base_url`），然后在浏览器（推荐使用 Chrome 浏览器）中打开该 URL。输入您配置的 `AUTH_TOKEN` 值，即可进入管理后台。💻
![alt text](image-13.png)

![alt text](image-14.png)

在这里，您可以方便地批量添加密钥，支持任意格式（系统会基于正则表达式进行匹配并自动去重）。最后，请务必点击页面下方的“保存”按钮，您的配置才会生效。💾
![alt text](image-15.png)
