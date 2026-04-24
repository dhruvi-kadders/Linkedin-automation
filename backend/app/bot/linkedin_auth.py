from __future__ import annotations

from typing import Any

from playwright.sync_api import sync_playwright

from ..db import accounts
from ..utils.humanize import get_browser_config, human_click, human_delay, human_type, sleep


LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed"


def launch_browser(session_data: dict[str, Any] | None = None) -> dict[str, Any]:
    config = get_browser_config()
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=config["headless"], args=config["args"])

    user_agent = next(
        (
            arg.split("=", 1)[1]
            for arg in config["args"]
            if arg.startswith("--user-agent=") and "=" in arg
        ),
        None,
    )

    context_options: dict[str, Any] = {
        "viewport": config["viewport"],
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
    }
    if user_agent:
        context_options["user_agent"] = user_agent
    if session_data:
        context_options["storage_state"] = session_data

    context = browser.new_context(**context_options)
    context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """
    )

    return {"playwright": playwright, "browser": browser, "context": context}


def is_logged_in(page: Any) -> bool:
    try:
        page.goto(LINKEDIN_FEED_URL, wait_until="domcontentloaded", timeout=15000)
        sleep(2000)
        url = page.url
        return "/feed" in url or "/mynetwork" in url
    except Exception:
        return False


def login(page: Any, email: str, password: str, logger: Any) -> bool:
    logger.info(f"Logging in as {email}")

    page.goto(LINKEDIN_LOGIN_URL, wait_until="domcontentloaded", timeout=20000)
    sleep(2000)

    if "/feed" in page.url:
        logger.info("Already logged in via session")
        return True

    try:
        human_type(page, "#username", email, clear=True, speed="normal")
        human_delay("fast")
        human_type(page, "#password", password, clear=True, speed="normal")
        human_delay("fast")
        human_click(page, '[data-litms-control-urn="login-submit"]')

        page.wait_for_url(
            lambda url: "/login" not in str(url) and "/checkpoint" not in str(url),
            timeout=30000,
        )
        sleep(3000)

        current_url = page.url
        if "/checkpoint" in current_url or "/challenge" in current_url:
            logger.warn("Security checkpoint detected - manual intervention may be required")
            page.wait_for_url(lambda url: "/checkpoint" not in str(url), timeout=60000)

        if "/feed" in page.url or "/mynetwork" in page.url:
            logger.info("Login successful")
            return True

        logger.error(f"Login failed - unexpected URL: {page.url}")
        return False
    except Exception as err:
        logger.error("Login error", {"error": str(err)})
        return False


def create_session(account: dict[str, Any], logger: Any) -> dict[str, Any]:
    saved_session = accounts.get_session(account["id"])

    session = launch_browser(saved_session)
    context = session["context"]
    page = context.new_page()

    logged_in = False

    if saved_session:
        logged_in = is_logged_in(page)
        if logged_in:
            logger.info("Session restored from saved state")
        else:
            logger.info("Saved session expired, performing fresh login")

    if not logged_in:
        logged_in = login(page, account["email"], account["password"], logger)
        if logged_in:
            state = context.storage_state()
            accounts.save_session(account["id"], state)
            logger.info("Session saved to database")

    session["page"] = page
    session["logged_in"] = logged_in
    return session

