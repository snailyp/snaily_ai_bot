"""Pure schema, migration, validation and secret handling for AI settings.

This module deliberately imports no application settings, SDKs, or network clients.
Public errors describe schema fields, never submitted values or credentials.
"""

from copy import deepcopy
import math
import re
from urllib.parse import urlsplit

__all__ = [
    "normalize_ai_config", "validate_ai_config", "redact_ai_config",
    "merge_ai_secrets", "resolve_text_model", "resolve_image_model",
    "validate_headers",
]

_TEXT_PARAMETERS = {
    "temperature", "top_p", "presence_penalty", "frequency_penalty",
    "max_output_tokens", "reasoning_effort",
}
_IMAGE_PARAMETERS = {
    "openai_images": {"size", "quality", "output_format", "background", "response_format"},
    "gemini": {"aspect_ratio", "image_size"},
    "seedream": {"size", "watermark", "response_format", "seed"},
}
_SEARCH_TYPES = {"exa", "tavily", "firecrawl"}
_SEARCH_PROVIDER_FIELDS = {"id", "name", "type", "enabled", "api_key", "api_base_url", "headers", "timeout"}
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_BLOCKED_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}
_PUBLIC_FIELDS = {"api_key_set", "clear_api_key", "headers_set", "env_set"}


def _fail(path, message):
    raise ValueError(f"{path}: {message}")


def _object(value, path):
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        _fail(path, "must be an object with string keys")
    return value


def _string(value, path, required=False):
    if not isinstance(value, str):
        _fail(path, "must be a string")
    if required and not value.strip():
        _fail(path, "must not be blank")
    return value


def _boolean(value, path):
    if not isinstance(value, bool):
        _fail(path, "must be a boolean")


def _number(value, path, minimum=None, maximum=None, integer=False):
    if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)):
        _fail(path, "must be an integer" if integer else "must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        _fail(path, "must be finite")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        _fail(path, "is outside the allowed range")


def _enum(value, choices, path):
    _string(value, path)
    if value not in choices:
        _fail(path, "is not a supported option")


def _list(value, path):
    if not isinstance(value, list):
        _fail(path, "must be an array")
    return value


def _strings(value, path):
    for entry in _list(value, path):
        _string(entry, path, required=True)


def _json_value(value, path):
    """Retained extension/search settings must still be safe JSON values."""
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)):
        _number(value, path)
    elif isinstance(value, list):
        for item in value:
            _json_value(item, path)
    elif isinstance(value, dict):
        _object(value, path)
        for item in value.values():
            _json_value(item, path)
    else:
        _fail(path, "must contain JSON values")


def _url(value, path, required=False):
    _string(value, path, required=required)
    if not value:
        return
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        valid = valid and parsed.username is None and parsed.password is None
        valid = valid and not parsed.fragment and parsed.port != 0
    except ValueError:
        valid = False
    if not valid or any(char.isspace() or ord(char) < 32 for char in value):
        _fail(path, "must be an HTTP(S) URL without embedded credentials or fragments")


def validate_headers(headers):
    """Validate HTTP headers; Authorization is explicitly permitted."""
    _object(headers, "headers")
    seen = set()
    for name, value in headers.items():
        if not _HEADER_NAME.fullmatch(name):
            _fail("headers", "contains an invalid header name")
        lowered = name.lower()
        if lowered in seen:
            _fail("headers", "contains case-insensitive duplicate names")
        if lowered in _BLOCKED_HEADERS:
            _fail("headers", "contains a forbidden transport header")
        _string(value, "headers.value")
        if any(ord(char) < 32 and char != "\t" or ord(char) == 127 for char in value):
            _fail("headers.value", "must not contain control characters")
        seen.add(lowered)
    return deepcopy(headers)


def _env_map(value):
    _object(value, "mcp.servers.env")
    for name, item in value.items():
        if not name or "=" in name or "\x00" in name:
            _fail("mcp.servers.env", "contains an invalid variable name")
        _string(item, "mcp.servers.env.value")
        if "\x00" in item:
            _fail("mcp.servers.env.value", "must not contain NUL")


def _defaults():
    return {
        "schema_version": 2,
        "text": {"providers": [], "models": [], "chat_model_id": "", "task_model_id": ""},
        "drawing": {"providers": [], "models": [], "active_model_id": ""},
        "mcp": {
            "enabled": False, "admin_only": True,
            "allowed_user_ids": [], "allowed_chat_ids": [],
            "max_rounds": 4, "max_calls": 8, "timeout": 30,
            "max_result_chars": 12000, "servers": [],
        },
        "search": {
            "enabled": True, "max_results": 5, "summarize": True, "fallback": True,
            "active_provider_id": "", "providers": [],
        },
    }


def _fill_defaults(value, defaults):
    if not isinstance(value, dict):
        return
    for name, default in defaults.items():
        if name not in value:
            value[name] = deepcopy(default)
        elif isinstance(default, dict):
            _fill_defaults(value[name], default)


def _legacy_parameters(entry):
    # Legacy UI stored these knobs as flat values; absent/blank means omitted.
    parameters = {}
    for name in ("max_tokens", "temperature"):
        value = entry.get(name)
        if value is not None and value != "":
            parameters["max_output_tokens" if name == "max_tokens" else name] = deepcopy(value)
    return parameters


def _migrate_legacy(value):
    configs = value.pop("openai_configs", [])
    if not configs and isinstance(value.get("openai"), dict):
        configs = [value["openai"]]
    value.pop("openai", None)
    active = value.pop("active_openai_config_index", 0)
    _list(configs, "openai_configs")
    text = {"providers": [], "models": [], "chat_model_id": "", "task_model_id": ""}
    for index, entry in enumerate(configs):
        _object(entry, "openai_configs")
        provider_id = f"legacy-text-provider-{index + 1}"
        model_id = f"legacy-text-model-{index + 1}"
        text["providers"].append({
            "id": provider_id, "name": entry.get("name", f"Provider {index + 1}"),
            "api_base_url": entry.get("api_base_url", "https://api.openai.com/v1"),
            "api_key": entry.get("api_key", ""),
            "headers": deepcopy(entry.get("headers", {})), "timeout": entry.get("timeout", 60),
        })
        text["models"].append({
            "id": model_id, "name": entry.get("name", entry.get("model", "")),
            "provider_id": provider_id, "model": entry.get("model", ""),
            "api_type": "chat_completions", "token_limit_field": "max_tokens",
            "supports_tools": False, "parameters": _legacy_parameters(entry),
        })
    if type(active) is int and 0 <= active < len(configs):
        text["chat_model_id"] = text["models"][active]["id"]
    value.setdefault("text", text)
    drawing = value.get("drawing")
    if isinstance(drawing, dict) and not any(k in drawing for k in ("providers", "models", "active_model_id")):
        migrated = {"providers": [], "models": [], "active_model_id": ""}
        provider = deepcopy(text["providers"][active]) if text["chat_model_id"] else {
            "name": "Drawing", "api_base_url": "", "api_key": "", "headers": {}, "timeout": 60,
        }
        provider.update(id="legacy-image-provider-1", type="openai_images")
        parameters = {k: deepcopy(v) for k, v in drawing.items() if k in _IMAGE_PARAMETERS["openai_images"] and v is not None and v != ""}
        migrated["providers"].append(provider)
        migrated["models"].append({
            "id": "legacy-image-model-1", "name": drawing.get("model", "Drawing"),
            "provider_id": provider["id"], "model": drawing.get("model", ""),
            "parameters": parameters,
        })
        if text["chat_model_id"] and drawing.get("model"):
            migrated["active_model_id"] = "legacy-image-model-1"
        value["drawing"] = migrated
    value["schema_version"] = 2


def normalize_ai_config(raw):
    """Return an independent v2 snapshot, migrating legacy data exactly once.

    Missing schema fields receive defaults. Existing values are not coerced, so
    validation still rejects malformed types instead of silently repairing them.
    """
    if raw is None:
        raw = {}
    _object(raw, "ai_services")
    value = deepcopy(raw)
    version = value.get("schema_version")
    if version is not None and (type(version) is not int or version not in {1, 2}):
        _fail("schema_version", "is not supported")
    if version != 2:
        _migrate_legacy(value)
    _fill_defaults(value, _defaults())
    for section_name in ("text", "drawing"):
        section = value.get(section_name)
        if not isinstance(section, dict):
            continue
        providers = section.get("providers")
        if isinstance(providers, list):
            for provider in providers:
                defaults = {"name": "", "api_base_url": "", "api_key": "", "headers": {}, "timeout": 60}
                if section_name == "drawing":
                    defaults["type"] = "openai_images"
                _fill_defaults(provider, defaults)
        models = section.get("models")
        if isinstance(models, list):
            for model in models:
                defaults = {"name": "", "provider_id": "", "model": "", "parameters": {}}
                if section_name == "text":
                    defaults.update(api_type="chat_completions", token_limit_field="max_completion_tokens", supports_tools=False)
                _fill_defaults(model, defaults)
    mcp = value.get("mcp")
    if isinstance(mcp, dict) and isinstance(mcp.get("servers"), list):
        for server in mcp["servers"]:
            _fill_defaults(server, {
                "name": "", "enabled": False, "transport": "stdio", "url": "", "command": "",
                "args": [], "env": {}, "headers": {}, "allowed_tools": [], "timeout": 30,
            })
    search = value.get("search")
    if isinstance(search, dict) and isinstance(search.get("providers"), list):
        for provider in search["providers"]:
            _fill_defaults(provider, {
                "name": "", "type": "exa", "enabled": True, "api_key": "",
                "api_base_url": "", "headers": {}, "timeout": 30,
            })
    return value


def _metadata(row, path):
    for name in ("api_key_set", "clear_api_key"):
        if name in row:
            _boolean(row[name], path + "." + name)
    for name in ("headers_set", "env_set"):
        if name in row:
            _strings(row[name], path + "." + name)
    for name in _PUBLIC_FIELDS:
        row.pop(name, None)


def _only_fields(value, allowed, path):
    if value.keys() - set(allowed):
        _fail(path, "contains an unsupported field")


def _rows(value, path):
    rows = {}
    for row in _list(value, path):
        _object(row, path)
        identifier = _string(row.get("id"), path + ".id", required=True)
        if identifier in rows:
            _fail(path + ".id", "must be unique")
        rows[identifier] = row
        _metadata(row, path)
    return rows


def _text_parameters(parameters, path):
    _object(parameters, path)
    if parameters.keys() - _TEXT_PARAMETERS:
        _fail(path, "contains an unsupported parameter")
    for name, value in parameters.items():
        field = path + "." + name
        if name == "max_output_tokens":
            _number(value, field, minimum=1, integer=True)
        elif name == "reasoning_effort":
            _string(value, field, required=True)
        elif name in {"presence_penalty", "frequency_penalty"}:
            _number(value, field, minimum=-2, maximum=2)
        else:
            _number(value, field, minimum=0, maximum=1 if name == "top_p" else 2)


def _image_parameters(parameters, provider_type, path):
    _object(parameters, path)
    allowed = _IMAGE_PARAMETERS.get(provider_type, set().union(*_IMAGE_PARAMETERS.values()))
    if parameters.keys() - allowed:
        _fail(path, "contains a parameter unsupported by the provider protocol")
    for name, value in parameters.items():
        field = path + "." + name
        if name == "watermark":
            _boolean(value, field)
        elif name == "seed":
            _number(value, field, minimum=-1, maximum=2147483647, integer=True)
        else:
            _string(value, field, required=True)
        if name == "output_format":
            _enum(value, {"png", "jpeg", "webp"}, field)
        elif name == "background":
            _enum(value, {"auto", "transparent", "opaque"}, field)
        elif name == "response_format":
            _enum(value, {"url", "b64_json"}, field)
        elif name == "quality":
            _enum(value, {"auto", "standard", "hd", "low", "medium", "high"}, field)
        elif name == "aspect_ratio" and not re.fullmatch(r"[1-9]\d*:[1-9]\d*", value):
            _fail(field, "must be a positive width:height ratio")
        elif name == "size" and not re.fullmatch(r"auto|[1-9]\d*x[1-9]\d*|[1-9]\d*K", value):
            _fail(field, "must be auto, WIDTHxHEIGHT, or a K resolution")
        elif name == "image_size" and not re.fullmatch(r"[1-9]\d*K", value):
            _fail(field, "must be a K resolution")


def _resolve(section, selection, path):
    selected = section.get(selection, "")
    model = next((row for row in section["models"] if row["id"] == selected), None)
    if model is None:
        _fail(path, "must select an existing model")
    provider = next((row for row in section["providers"] if row["id"] == model["provider_id"]), None)
    if provider is None:
        _fail(path, "selected model must reference an existing provider")
    _string(model["model"], path + ".model", required=True)
    _url(provider["api_base_url"], path + ".provider.api_base_url", required=True)
    return provider, model


def _validate_search(search):
    """Search keeps unknown extension keys; provider rows are strict like other sections."""
    for name in ("enabled", "summarize", "fallback"):
        _boolean(search[name], "search." + name)
    _number(search["max_results"], "search.max_results", minimum=1, maximum=20, integer=True)
    providers = _rows(search["providers"], "search.providers")
    for provider in providers.values():
        path = "search.providers"
        _only_fields(provider, _SEARCH_PROVIDER_FIELDS, path)
        _string(provider["name"], path + ".name")
        _enum(provider["type"], _SEARCH_TYPES, path + ".type")
        _boolean(provider["enabled"], path + ".enabled")
        _string(provider["api_key"], path + ".api_key")
        # Blank means the provider's official endpoint.
        _url(provider["api_base_url"], path + ".api_base_url")
        _number(provider["timeout"], path + ".timeout", minimum=0.001)
        provider["headers"] = validate_headers(provider["headers"])
    _string(search["active_provider_id"], "search.active_provider_id")
    if search["active_provider_id"] and search["active_provider_id"] not in providers:
        _fail("search.active_provider_id", "must select an existing search provider")


def validate_ai_config(config):
    """Return canonical validated v2 settings, or a credential-safe ValueError."""
    _object(config, "ai_services")
    value = normalize_ai_config(config)
    if any(k in value for k in ("openai", "openai_configs", "active_openai_config_index")):
        _fail("ai_services", "legacy fields cannot be updated after migration")
    for section_name in ("text", "drawing"):
        section = _object(value[section_name], section_name)
        _only_fields(section, _defaults()[section_name], section_name)
        providers = _rows(section["providers"], section_name + ".providers")
        models = _rows(section["models"], section_name + ".models")
        for provider in providers.values():
            path = section_name + ".providers"
            allowed = {"id", "name", "api_key", "api_base_url", "headers", "timeout"}
            if section_name == "drawing":
                allowed.add("type")
            _only_fields(provider, allowed, path)
            _string(provider["name"], path + ".name")
            _string(provider["api_key"], path + ".api_key")
            _url(provider["api_base_url"], path + ".api_base_url")
            _number(provider["timeout"], path + ".timeout", minimum=0.001)
            provider["headers"] = validate_headers(provider["headers"])
            if section_name == "drawing":
                _enum(provider["type"], _IMAGE_PARAMETERS, path + ".type")
        for model in models.values():
            path = section_name + ".models"
            allowed = {"id", "name", "provider_id", "model", "parameters"}
            if section_name == "text":
                allowed.update({"api_type", "token_limit_field", "supports_tools"})
            _only_fields(model, allowed, path)
            for name in ("name", "provider_id", "model"):
                _string(model[name], path + "." + name)
            if section_name == "text":
                _enum(model["api_type"], {"chat_completions", "responses"}, path + ".api_type")
                _enum(model["token_limit_field"], {"max_completion_tokens", "max_tokens"}, path + ".token_limit_field")
                _boolean(model["supports_tools"], path + ".supports_tools")
                _text_parameters(model["parameters"], path + ".parameters")
                if model["api_type"] == "responses" and {"presence_penalty", "frequency_penalty"} & model["parameters"].keys():
                    _fail(path + ".parameters", "penalty parameters are not supported by Responses")
            else:
                provider_type = providers.get(model["provider_id"], {}).get("type")
                _image_parameters(model["parameters"], provider_type, path + ".parameters")
        selections = ("chat_model_id", "task_model_id") if section_name == "text" else ("active_model_id",)
        for selection in selections:
            _string(section[selection], section_name + "." + selection)
            if section[selection]:
                _resolve(section, selection, section_name + "." + selection)
    mcp = _object(value["mcp"], "mcp")
    _only_fields(mcp, _defaults()["mcp"], "mcp")
    for name in ("enabled", "admin_only"):
        _boolean(mcp[name], "mcp." + name)
    for name in ("allowed_user_ids", "allowed_chat_ids"):
        for identifier in _list(mcp[name], "mcp." + name):
            _number(identifier, "mcp." + name, integer=True)
    for name in ("max_rounds", "max_calls", "max_result_chars"):
        _number(mcp[name], "mcp." + name, minimum=1, integer=True)
    _number(mcp["timeout"], "mcp.timeout", minimum=0.001)
    for server in _rows(mcp["servers"], "mcp.servers").values():
        path = "mcp.servers"
        _only_fields(server, {"id", "name", "enabled", "transport", "url", "command", "args", "env", "headers", "allowed_tools", "timeout"}, path)
        _string(server["name"], path + ".name")
        _boolean(server["enabled"], path + ".enabled")
        _enum(server["transport"], {"stdio", "streamable_http", "sse"}, path + ".transport")
        _url(server["url"], path + ".url", required=server["enabled"] and server["transport"] != "stdio")
        _string(server["command"], path + ".command", required=server["enabled"] and server["transport"] == "stdio")
        for argument in _list(server["args"], path + ".args"):
            _string(argument, path + ".args")
        _env_map(server["env"])
        server["headers"] = validate_headers(server["headers"])
        _strings(server["allowed_tools"], path + ".allowed_tools")
        _number(server["timeout"], path + ".timeout", minimum=0.001)
    _validate_search(_object(value["search"], "search"))
    _json_value(value, "ai_services")
    return value


def _secret_rows(config):
    for section in ("text", "drawing"):
        for row in config[section]["providers"]:
            yield row, True
    for row in config["mcp"]["servers"]:
        yield row, False
    for row in config["search"]["providers"]:
        yield row, True


def redact_ai_config(config):
    """Return a public snapshot; never mutate stored secrets."""
    value = normalize_ai_config(config)
    for row, provider in _secret_rows(value):
        if provider:
            row["api_key_set"] = bool(row.get("api_key"))
            row["api_key"] = ""
        row.pop("clear_api_key", None)
        for name in ("headers",) if provider else ("headers", "env"):
            mapping = row.get(name, {})
            row[name + "_set"] = [key for key, item in mapping.items() if item]
            row[name] = {key: "" for key in mapping}
    return value


def _merge_objects(current, incoming):
    result = deepcopy(current)
    for key, item in incoming.items():
        if isinstance(item, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_objects(result[key], item)
        else:
            result[key] = deepcopy(item)
    return result


def _merge_secret_map(old, row, name, headers=False):
    if name not in row:
        row[name] = deepcopy(old.get(name, {}))
        return
    supplied = row[name]
    if headers:
        validate_headers(supplied)
    else:
        _env_map(supplied)
    previous = old.get(name, {})
    if headers:
        previous = {key.lower(): item for key, item in previous.items()}
    row[name] = {
        key: item if item else previous.get(key.lower() if headers else key, "")
        for key, item in supplied.items()
    }


def merge_ai_secrets(current, incoming):
    """Merge a v2 update, retaining blank/omitted secrets by scoped stable ID.

    Lists replace lists. Submitted header/env maps replace their key sets; blank
    values preserve matching old secrets. Omitting the whole map preserves it.
    """
    old = normalize_ai_config(current)
    _object(incoming, "ai_services")
    update = deepcopy(incoming)
    for section_name, collection, provider in (
        ("text", "providers", True), ("drawing", "providers", True), ("mcp", "servers", False),
        ("search", "providers", True),
    ):
        section = update.get(section_name)
        if not isinstance(section, dict) or collection not in section:
            continue
        rows = _list(section[collection], section_name + "." + collection)
        previous = {row["id"]: row for row in old[section_name][collection]}
        for row in rows:
            _object(row, section_name + "." + collection)
            identifier = _string(row.get("id"), section_name + "." + collection + ".id", required=True)
            prior = previous.get(identifier, {})
            if provider:
                if "clear_api_key" in row:
                    _boolean(row["clear_api_key"], section_name + ".providers.clear_api_key")
                if row.get("clear_api_key", False):
                    row["api_key"] = ""
                elif "api_key" not in row or row["api_key"] == "":
                    row["api_key"] = prior.get("api_key", "")
            _merge_secret_map(prior, row, "headers", headers=True)
            if not provider:
                _merge_secret_map(prior, row, "env")
            _metadata(row, section_name + "." + collection)
    return _merge_objects(old, update)


def resolve_text_model(ai_config, role="chat"):
    """Resolve a validated (provider, model) snapshot; task defaults to chat."""
    if role not in {"chat", "task"}:
        _fail("text.role", "must be chat or task")
    value = validate_ai_config(ai_config)
    selection = "task_model_id" if role == "task" and value["text"]["task_model_id"] else "chat_model_id"
    return _resolve(value["text"], selection, "text." + selection)


def resolve_image_model(ai_config):
    """Resolve the independently configured active image model."""
    value = validate_ai_config(ai_config)
    return _resolve(value["drawing"], "active_model_id", "drawing.active_model_id")
