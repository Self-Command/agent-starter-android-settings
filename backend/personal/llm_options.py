"""Provider-specific request options; credentials are supplied separately."""


def completion_options(api_style="openai"):
    if api_style == "deepseek":
        # DeepSeek enables thinking by default. Explicitly disable it for voice,
        # and use its documented token limit rather than max_completion_tokens.
        return {"extra_body": {"thinking": {"type": "disabled"}, "max_tokens": 256}}
    if api_style == "openai":
        return {"reasoning_effort": "none", "max_completion_tokens": 256}
    raise ValueError("Unsupported LLM_API_STYLE")
