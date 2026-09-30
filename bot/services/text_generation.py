"""文本生成协议：请求参数、工具循环和协议状态在此集中处理。"""

import asyncio
import json
from typing import Any, Awaitable, Callable, Optional

import httpx
import openai
from jsonschema import ValidationError, validate


class TextGenerationError(ValueError):
    """可以直接展示、且不包含上游凭证的生成错误。"""


def client_options(provider: dict) -> dict:
    headers = {key.title(): value for key, value in provider.get("headers", {}).items()}
    # 自定义 Authorization 优先；无密钥的本地接口不发送虚构的 Bearer 凭证。
    if not provider.get("api_key") and "Authorization" not in headers:
        headers["Authorization"] = openai.Omit()
    return {
        "api_key": provider.get("api_key") or "unused",
        "base_url": provider["api_base_url"].rstrip("/") + "/",
        "default_headers": headers,
        "timeout": provider.get("timeout", 60),
        "max_retries": 0,
    }


def request_parameters(model: dict) -> dict:
    """未设置的参数绝不补默认值；统一的输出上限只映射到一个原生字段。"""
    parameters = {key: value for key, value in model.get("parameters", {}).items() if value is not None}
    output_limit = parameters.pop("max_output_tokens", None)
    if output_limit is not None:
        field = "max_output_tokens" if model["api_type"] == "responses" else model.get("token_limit_field", "max_completion_tokens")
        parameters[field] = output_limit
    effort = parameters.pop("reasoning_effort", None)
    if effort is not None:
        if model["api_type"] == "responses":
            parameters["reasoning"] = {"effort": effort}
        else:
            parameters["reasoning_effort"] = effort
    return parameters


def _as_dict(item: Any) -> dict:
    return item if isinstance(item, dict) else item.model_dump(exclude_none=True)


def _api_error_field(exc: Exception, field: str) -> bool:
    """Return whether an API status error explicitly rejects one request field."""
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return False
    if body.get("param") == field:
        return True
    error = body.get("error")
    return isinstance(error, dict) and error.get("param") == field


def _api_error_hint(exc: Exception) -> str:
    """Expose only bounded protocol metadata; never copy an upstream message."""
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    if not isinstance(status, int):
        return ""
    code = body.get("code") if isinstance(body, dict) else None
    param = body.get("param") if isinstance(body, dict) else None
    if not code and isinstance(body, dict) and isinstance(body.get("error"), dict):
        code = body["error"].get("code")
        param = param or body["error"].get("param")
    parts = [f"HTTP {status}"]
    for label, value in (("code", code), ("param", param)):
        if isinstance(value, str) and len(value) <= 64 and value.replace("_", "").replace("-", "").isalnum():
            parts.append(f"{label}={value}")
    return f"（{', '.join(parts)}）"


def _api_error_code(exc: Exception) -> Any:
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return None
    code = body.get("code")
    if not code and isinstance(body.get("error"), dict):
        code = body["error"].get("code")
    return code


def _response_from_sse(body: str) -> dict:
    """部分兼容接口即使未请求流式也返回 SSE；只接收完整终态，不拼接增量。"""
    response = None
    event_type = ""
    data = []
    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n") + [""]:
        if line:
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                event_type = value
            elif field == "data":
                data.append(value)
            continue
        payload = "\n".join(data)
        kind = event_type
        data, event_type = [], ""
        if not payload:
            continue
        if payload == "[DONE]":
            break
        try:
            event = json.loads(payload)
        except ValueError:
            raise TextGenerationError("模型返回的 Responses 事件数据格式无效，请检查接口协议。") from None
        if not isinstance(event, dict):
            raise TextGenerationError("模型返回的 Responses 事件数据格式无效，请检查接口协议。")
        kind = event.get("type") or kind
        if kind in ("error", "response.failed"):
            raise TextGenerationError("模型返回 Responses 失败事件，请检查模型及提供商配置。")
        if kind == "response.incomplete":
            raise TextGenerationError("模型回复未完成，可能达到输出额度上限。")
        if kind == "response.completed":
            response = event.get("response")
            if not isinstance(response, dict):
                raise TextGenerationError("模型返回的 Responses 终态数据格式无效。")
    if response is None:
        raise TextGenerationError("模型未返回完整的 Responses 结果，请检查接口协议或事件流是否中断。")
    return response


def _responses_output(response: Any) -> list[dict]:
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            response = _response_from_sse(response)
    if isinstance(response, dict):
        output, status, error = response.get("output"), response.get("status"), response.get("error")
    else:
        output = getattr(response, "output", None)
        status, error = getattr(response, "status", None), getattr(response, "error", None)
    if status == "incomplete":
        raise TextGenerationError("模型回复未完成，可能达到输出额度上限。")
    if error or status not in (None, "completed"):
        raise TextGenerationError("模型未成功完成 Responses 请求，请检查模型及提供商配置。")
    if not isinstance(output, list):
        raise TextGenerationError("模型返回的 Responses 数据缺少有效的 output，请检查接口协议。")
    try:
        output = [_as_dict(item) for item in output]
    except (AttributeError, TypeError, ValueError):
        raise TextGenerationError("模型返回的 Responses output 数据格式无效。") from None
    for item in output:
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise TextGenerationError("模型返回的 Responses output 数据格式无效。")
        if item["type"] == "message":
            content = item.get("content")
            if not isinstance(content, list) or any(
                not isinstance(part, dict) or not isinstance(part.get("type"), str) or
                (part["type"] == "output_text" and not isinstance(part.get("text"), str))
                for part in content
            ):
                raise TextGenerationError("模型返回的 Responses 文本数据格式无效。")
        if item["type"] == "function_call" and any(
            not isinstance(item.get(key), str) or not item[key] for key in ("name", "call_id")
        ):
            raise TextGenerationError("模型返回的 Responses 工具调用数据格式无效。")
    return output


class TextGenerator:
    def __init__(self, client_factory=None):
        self.client_factory = client_factory or openai.AsyncOpenAI

    async def complete(
        self,
        provider: dict,
        model: dict,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_caller: Optional[Callable[[str, dict], Awaitable[str]]] = None,
        limits: Optional[dict] = None,
    ) -> str:
        limits = limits or {}
        tools = tools if model.get("supports_tools") and tool_caller else []
        tools_by_name = {tool["name"]: tool for tool in tools or []}
        max_rounds = limits.get("max_rounds", 4)
        max_calls = limits.get("max_calls", 8)
        max_result_chars = limits.get("max_result_chars", 12000)
        used_calls = 0
        state = [dict(message) for message in messages]
        is_responses = model["api_type"] == "responses"
        if is_responses:
            for message in state:
                if isinstance(message.get("content"), list):
                    message["content"] = [
                        {"type": "input_image", "image_url": part["image_url"]["url"]}
                        if part.get("type") == "image_url" else
                        {"type": "input_text", "text": part["text"]}
                        for part in message["content"]
                    ]
        parameters = request_parameters(model)

        try:
            # 请求级客户端不跨 Flask/机器人循环共享；整个工具循环使用同一配置快照。
            async with self.client_factory(**client_options(provider)) as client:
                for round_index in range(max_rounds + 1):
                    if is_responses:
                        kwargs = {
                            "model": model["model"], "input": state, "store": False,
                            **parameters,
                        }
                        if tools_by_name:
                            kwargs["tools"] = [
                                {"type": "function", "name": tool["name"], "description": tool.get("description", ""),
                                 "parameters": tool["parameters"], "strict": False}
                                for tool in tools_by_name.values()
                            ]
                            kwargs["include"] = ["reasoning.encrypted_content"]
                        create = getattr(getattr(client, "responses", None), "create", None)
                        if not callable(create):
                            raise TextGenerationError("当前 OpenAI SDK 不支持 Responses，请按 requirements.txt 更新依赖并重启服务。")
                        try:
                            response = await create(**kwargs)
                        except openai.APIStatusError as exc:
                            # A number of Responses-compatible gateways reject
                            # OpenAI's optional `store` flag. It is safe to retry
                            # once because the first request was rejected during
                            # parameter validation, before generation started.
                            if kwargs.get("store") is False and _api_error_field(exc, "store"):
                                fallback_kwargs = dict(kwargs)
                                fallback_kwargs.pop("store", None)
                                response = await create(**fallback_kwargs)
                            else:
                                raise
                        output = _responses_output(response)
                        calls = [item for item in output if item.get("type") == "function_call"]
                        text = "\n".join(
                            part.get("text", "") for item in output if item.get("type") == "message"
                            for part in item.get("content", []) if part.get("type") == "output_text"
                        ).strip()
                        # reasoning（含加密内容）和 function_call 必须按原顺序回传。
                        state.extend(output)
                    else:
                        kwargs = {"model": model["model"], "messages": state, **parameters}
                        if tools_by_name:
                            kwargs["tools"] = [
                                {"type": "function", "function": {"name": tool["name"],
                                 "description": tool.get("description", ""), "parameters": tool["parameters"]}}
                                for tool in tools_by_name.values()
                            ]
                        response = await client.chat.completions.create(**kwargs)
                        if not response.choices:
                            raise TextGenerationError("模型未返回任何回复。")
                        message = _as_dict(response.choices[0].message)
                        text = (message.get("content") or "").strip()
                        calls = [
                            {"name": call["function"]["name"], "arguments": call["function"]["arguments"], "call_id": call["id"]}
                            for call in message.get("tool_calls", [])
                        ]
                        state.append({key: message[key] for key in ("role", "content", "tool_calls") if key in message})

                    if not calls:
                        if not text:
                            raise TextGenerationError("模型未返回文本，可能拒绝了请求或输出额度不足。")
                        return text
                    if not tools_by_name or tool_caller is None:
                        raise TextGenerationError("模型请求了未启用的工具。")
                    if round_index >= max_rounds or used_calls + len(calls) > max_calls:
                        raise TextGenerationError("工具调用已达到本次请求上限，请缩小问题范围。")

                    for call in calls:
                        used_calls += 1
                        result = await self._execute_tool(call, tools_by_name, tool_caller, limits.get("timeout", 30))
                        if len(result) > max_result_chars:
                            result = result[:max_result_chars] + "\n[工具结果已截断]"
                        if is_responses:
                            state.append({"type": "function_call_output", "call_id": call["call_id"], "output": result})
                        else:
                            state.append({"role": "tool", "tool_call_id": call["call_id"], "content": result})
        except TextGenerationError:
            raise
        except openai.AuthenticationError:
            raise TextGenerationError("模型提供商认证失败，请检查密钥和 Headers。") from None
        except openai.RateLimitError:
            raise TextGenerationError("模型提供商请求过多或额度不足，请稍后再试。") from None
        except (openai.APITimeoutError, httpx.TimeoutException, asyncio.TimeoutError):
            raise TextGenerationError("模型请求超时，请稍后再试。") from None
        except (openai.APIError, httpx.HTTPError) as exc:
            hint = _api_error_hint(exc)
            if _api_error_code(exc) == "bad_response_body":
                raise TextGenerationError(
                    f"中转网关返回了无效的 Responses 响应{hint}，请确认该网关和模型支持 Responses 协议。"
                ) from None
            raise TextGenerationError(f"模型请求失败{hint}，请检查接口类型、模型名称及已启用的参数。") from None

    @staticmethod
    async def _execute_tool(call: dict, tools: dict, caller: Callable, timeout: float) -> str:
        name = call.get("name")
        if name not in tools:
            return json.dumps({"error": "工具未获许可或不存在"}, ensure_ascii=False)
        try:
            raw = call.get("arguments", "{}")
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > 32768:
                return '{"error":"工具参数过大或格式错误"}'
            arguments = json.loads(raw)
            if not isinstance(arguments, dict):
                return '{"error":"工具参数必须是对象"}'
            validate(arguments, tools[name]["parameters"])
        except (ValueError, ValidationError):
            return '{"error":"工具参数不符合声明的结构"}'
        try:
            return str(await asyncio.wait_for(caller(name, arguments), timeout=timeout))
        except asyncio.TimeoutError:
            return '{"error":"工具调用超时，未自动重试"}'
        except Exception:
            # MCP 上游错误可能包含 URL、请求头或工具参数，不能原样送回模型/日志。
            return '{"error":"工具调用失败或已停用，未自动重试"}'

    async def aclose(self):
        """请求级客户端在 complete 的 async with 中释放。"""
