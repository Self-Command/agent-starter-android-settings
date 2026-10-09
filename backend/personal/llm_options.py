"""Provider-specific request options; credentials are supplied separately."""


def completion_options(api_style="openai"):
    if api_style == "deepseek":
        # Preserve the existing voice thinking setting.
        # Leave output length unspecified; the provider applies its own defaults.
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    if api_style == "openai":
        return {"reasoning_effort": "none"}
    raise ValueError("Unsupported LLM_API_STYLE")
