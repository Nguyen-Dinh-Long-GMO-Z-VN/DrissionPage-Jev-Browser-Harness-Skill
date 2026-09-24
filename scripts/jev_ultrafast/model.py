"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import os
import threading
import time

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_TEXT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TEXT_MODEL = "inception/mercury-2.5"
# Observed control states the model sees on elements and target criteria.
STATES = ("checked", "selected", "expanded", "pressed")


class MissingValue(ValueError):
    """The goal does not contain a value for the selected field. Nothing is typed or guessed."""


def warm_connections():
    """Open the TLS/HTTP2 connections before the first decision needs them.

    A cold first TypeSafe request measured ~1.2 s against ~0.3 s once warm. The probe is a bodyless HEAD,
    so it never reaches a model; any response or error is ignored.
    """

    def probe():
        urls = [TYPESAFE_URL]
        if os.environ.get("TEXT_MODEL_API_KEY"):
            urls.append(os.environ.get("TEXT_MODEL_BASE_URL", DEFAULT_TEXT_BASE_URL).rstrip("/") + "/models")
        for url in urls:
            try:
                CLIENT.head(url, timeout=5)
            except httpx.HTTPError:
                pass

    threading.Thread(target=probe, daemon=True).start()


def error_detail(response):
    """The provider's message, short enough to read. Quota and rate-limit reasons sit at the end, so keep both ends."""
    try:
        error = response.json()
        error = error[0] if isinstance(error, list) else error
        text = error["error"]["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        text = response.text
    text = " ".join(str(text).split())
    return text if len(text) <= 300 else text[:100] + " ... " + text[-200:]


def post_json(url, key, body):
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError):
            # The request never reached the provider (or its connection dropped); no browser action is involved.
            if attempt < 2:
                time.sleep(0.5 * 2**attempt)
                continue
            raise RuntimeError("Model connection failed; no action executed.") from None
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(
                f"Model provider returned HTTP {response.status_code}: {error_detail(response)}; no action executed."
            )
        return response.json()
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", *STATES) if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def choose(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", *STATES) if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                # `typed: unverified` tells the model a field may not hold the text it was sent.
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} | (
                    {"typed": h["typed"]} if h.get("typed") else {}
                )
                for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise ValueError("Choosing an action needs TYPESAFE_API_KEY; set it in .env. No action executed.")
    result = post_json(TYPESAFE_URL, key, body)
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(result["answers"].get(operation.lower() + "_target", {}), targets[operation])
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        # `format` is present only for date/time controls, which accept nothing but that ISO shape.
        "field": {k: action[k] for k in ("label", "role", "value", "format") if k in action},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def field_text(context):
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", DEFAULT_TEXT_BASE_URL).rstrip("/")
    model = os.environ.get("TEXT_MODEL", DEFAULT_TEXT_MODEL)
    if "api.deepseek.com/" in base:
        reasoning = {"thinking": {"type": "disabled"}}
    elif "generativelanguage.googleapis.com/" in base:
        lowest = "minimal" if model.startswith("gemini-3") else "none"
        reasoning = {"reasoning_effort": lowest if os.environ.get("TEXT_MODEL_REASONING") == "none" else "low"}
    else:
        reasoning = {"reasoning": {"effort": "low"}}
        if os.environ.get("TEXT_MODEL_REASONING") == "none":
            reasoning = {"reasoning": {"enabled": False}}
    started = time.perf_counter()
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            **reasoning,
            "messages": [
                {"role": "system", "content": TEXT_VALUE},
                {
                    "role": "user",
                    "content": json.dumps(context),
                },
            ],
        },
    )
    try:
        output = json.loads(result["choices"][0]["message"]["content"])
        value = output["text"]
        if output == {"text": None}:
            # The instructions ask for null when the goal lacks the value. That is a clear stop, not a bad answer.
            label = context.get("field", {}).get("label")
            raise MissingValue(f"The goal has no value for field {label!r}; nothing typed.")
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except MissingValue:
        raise
    except (ValueError, KeyError, TypeError):
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    return value, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }
