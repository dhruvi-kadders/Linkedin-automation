from __future__ import annotations

import random
import time
from typing import Any


def random_int(minimum: int, maximum: int) -> int:
    return random.randint(minimum, maximum)


def random_float(minimum: float, maximum: float) -> float:
    return random.uniform(minimum, maximum)


def sleep(ms: int) -> None:
    time.sleep(ms / 1000)


def human_delay(speed: str = "normal") -> None:
    ranges = {
        "fast": (300, 800),
        "normal": (800, 2000),
        "slow": (2000, 4500),
    }
    minimum, maximum = ranges.get(speed, ranges["normal"])
    sleep(random_int(minimum, maximum))


def human_type(page: Any, selector: str, text: str, clear: bool = True, speed: str = "normal") -> None:
    try:
        element = page.wait_for_selector(selector, state="visible", timeout=8000)
        if clear:
            element.click(click_count=3)
            sleep(random_int(50, 150))
            page.keyboard.press("Backspace")
        for char in str(text):
            element.type(char, delay=random_int(35, 120))
            if random.random() < 0.04:
                sleep(random_int(200, 600))
        if speed != "fast":
            sleep(random_int(150, 400))
    except Exception:
        try:
            element = page.query_selector(selector)
            if element:
                element.fill(str(text))
        except Exception:
            pass


def human_click(page: Any, selector: str, timeout: int = 12000) -> None:
    try:
        element = page.wait_for_selector(selector, state="visible", timeout=timeout)
        box = element.bounding_box()
        if box:
            x = box["x"] + box["width"] * random_float(0.3, 0.7)
            y = box["y"] + box["height"] * random_float(0.3, 0.7)
            page.mouse.move(x, y, steps=random_int(4, 12))
            sleep(random_int(40, 180))
            page.mouse.click(x, y)
        else:
            element.click()
        sleep(random_int(80, 300))
    except Exception as err:
        raise RuntimeError(f'human_click failed on "{selector}": {err}') from err


def human_scroll(page: Any, direction: str = "down", amount: int = 300) -> None:
    steps = random_int(3, 8)
    step_size = amount / steps
    delta = step_size if direction == "down" else -step_size
    for _ in range(steps):
        page.mouse.wheel(0, delta)
        sleep(random_int(40, 180))


def human_select(page: Any, selector: str, value: str) -> None:
    page.wait_for_selector(selector, state="visible", timeout=8000)
    sleep(random_int(100, 300))
    try:
        page.select_option(selector, label=value)
    except Exception:
        try:
            page.select_option(selector, value=value)
        except Exception:
            page.select_option(selector, index=1)
    sleep(random_int(150, 400))


def human_navigate(page: Any, url: str) -> None:
    sleep(random_int(400, 1200))
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    sleep(random_int(900, 2000))


def answer_question(page: Any, field_info: dict[str, Any], qa_templates: list[dict[str, Any]]) -> bool:
    selector = field_info.get("selector")
    if not selector:
        return False

    label = str(field_info.get("label", "")).lower()
    template = next(
        (item for item in qa_templates if label and item.get("question_pattern", "").lower() in label),
        None,
    )
    if not template:
        return False

    field_type = field_info.get("type")
    answer = str(template.get("answer", ""))

    try:
        if field_type in {"text", "textarea", "number", "email"}:
            human_type(page, selector, answer, clear=True, speed="fast")
        elif field_type == "select":
            human_select(page, selector, answer)
        elif field_type in {"radio", "checkbox"}:
            options = page.query_selector_all(selector)
            for option in options:
                label_text = option.evaluate(
                    """(el) => {
                      const label = el.closest('label') || document.querySelector(`label[for="${el.id}"]`);
                      return label ? label.textContent.trim().toLowerCase() : '';
                    }"""
                )
                if answer.lower() in label_text or label_text in answer.lower():
                    option.click()
                    break
        return True
    except Exception:
        return False


def get_browser_config() -> dict[str, Any]:
    from ..config import HEADLESS

    return {
        "headless": HEADLESS,
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--window-size=1440,900",
        ],
        "viewport": {"width": 1440, "height": 900},
    }

