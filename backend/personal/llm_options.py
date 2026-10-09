"""Provider-specific request options; credentials are supplied separately."""


def completion_options(api_style="openai"):
    if api_style == "deepseek":
        # Preserve the existing voice thinking setting.
        # Generous safety budget lets the model vary length by question complexity.
        # The spoken policy controls normal answers; this is not a character cap.
        return {"extra_body": {"thinking": {"type": "disabled"}, "max_tokens": 1024}}
    if api_style == "openai":
        return {"reasoning_effort": "none", "max_completion_tokens": 1024}
    raise ValueError("Unsupported LLM_API_STYLE")
