"""
LLM access via the LiteLLM library. Provider is chosen by the model string.
Credentials come from the runtime config (admin page) and are pushed into the
environment right before each call so LiteLLM picks them up.
"""
import os
import time

import litellm

litellm.suppress_debug_info = True
litellm.drop_params = True  # ignore params a given provider doesn't support


def _apply_env(llm: dict):
    mapping = {
        "OPENAI_API_KEY": llm.get("openai_api_key"),
        "ANTHROPIC_API_KEY": llm.get("anthropic_api_key"),
        "AZURE_API_KEY": llm.get("azure_api_key"),
        "AZURE_API_BASE": llm.get("azure_api_base"),
        "AZURE_API_VERSION": llm.get("azure_api_version"),
        "AWS_ACCESS_KEY_ID": llm.get("aws_access_key_id"),
        "AWS_SECRET_ACCESS_KEY": llm.get("aws_secret_access_key"),
        "AWS_REGION_NAME": llm.get("aws_region"),
        "AWS_DEFAULT_REGION": llm.get("aws_region"),
    }
    for k, v in mapping.items():
        if v:
            os.environ[k] = v
        elif k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY") and k in os.environ and not v:
            # blank in admin => fall back to the container's IAM role / instance creds
            os.environ.pop(k, None)


async def complete(messages: list[dict], cfg: dict) -> dict:
    """
    Returns {"text": str, "model": str, "latency_ms": int, "attempts": [..]}.
    Tries primary model then each fallback in order.
    """
    llm = cfg["llm"]
    _apply_env(llm)
    models = [llm["model"]] + [m for m in llm.get("fallback_models", []) if m and m != llm["model"]]
    attempts = []
    last_err = None
    for model in models:
        t0 = time.perf_counter()
        try:
            kwargs = dict(
                model=model,
                messages=messages,
                temperature=llm.get("temperature", 0.9),
                max_tokens=llm.get("max_tokens", 600),
                timeout=llm.get("timeout", 30),
            )
            if llm.get("api_base"):
                kwargs["api_base"] = llm["api_base"]
            resp = await litellm.acompletion(**kwargs)
            text = (resp.choices[0].message.content or "").strip()
            ms = int((time.perf_counter() - t0) * 1000)
            attempts.append({"model": model, "ok": True, "latency_ms": ms})
            return {"text": text, "model": model, "latency_ms": ms, "attempts": attempts}
        except Exception as e:
            ms = int((time.perf_counter() - t0) * 1000)
            last_err = f"{type(e).__name__}: {str(e)[:300]}"
            attempts.append({"model": model, "ok": False, "latency_ms": ms, "error": last_err})
    raise RuntimeError(f"All models failed. Last error: {last_err}") from None
