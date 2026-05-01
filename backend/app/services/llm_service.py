import uuid
import logging
from collections import defaultdict

import anthropic
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.models.llm_cost_log import LLMCostLog

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

# Cost per token in USD (Sonnet 4.6 pricing)
_INPUT_COST = 0.000003
_OUTPUT_COST = 0.000015


def _strip_code_fences(content: str) -> str:
    """Remove markdown code fences if Claude wrapped the HTML output in one."""
    content = content.strip()
    if content.startswith("```html"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip()


def _build_variables(form_data: dict, tenant_config: dict) -> defaultdict:
    """
    Merge form_data with derived fields so prompt_template.format_map()
    never raises KeyError on a missing optional field.
    """
    variables = defaultdict(lambda: "Not specified")
    variables.update(form_data)

    # first_name from full_name
    full_name = form_data.get("full_name", "")
    variables["first_name"] = full_name.split()[0] if full_name else "there"

    # signature from tenant config
    variables["signature_name"] = tenant_config.get("company", {}).get(
        "signature_name", tenant_config.get("name", "The Team")
    )
    return variables


def generate_email(
    session: Session,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    step_config: dict,
    form_data: dict,
    tenant_config: dict,
) -> tuple[str, str]:
    """
    Render the prompt template, call Claude, write LLMCostLog.
    Returns (subject, html_body).

    This is a SYNC function — called from Celery tasks.
    The session is passed in so the cost log can be written; the caller
    must commit after this returns (or use asyncio.run if async session).
    """
    variables = _build_variables(form_data, tenant_config)

    subject = step_config["subject_template"].format_map(variables)
    prompt = step_config["prompt_template"].format_map(variables)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    message = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    html_body = _strip_code_fences(message.content[0].text)
    input_tokens = message.usage.input_tokens
    output_tokens = message.usage.output_tokens
    cache_read_tokens = getattr(message.usage, "cache_read_input_tokens", 0) or 0

    cost_usd = (input_tokens * _INPUT_COST) + (output_tokens * _OUTPUT_COST)

    logger.info(
        "Claude call complete: step=%s tokens_in=%d tokens_out=%d cost=$%.6f",
        step_config.get("step"),
        input_tokens,
        output_tokens,
        cost_usd,
    )

    # Write cost log — caller must flush/commit the session
    cost_log = LLMCostLog(
        tenant_id=tenant_id,
        lead_id=lead_id,
        model=MODEL,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        estimated_cost_usd=cost_usd,
    )
    # session.add is sync on SQLAlchemy ORM objects — safe to call here
    session.add(cost_log)

    return subject, html_body
