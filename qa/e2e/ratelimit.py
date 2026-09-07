"""nginx limits /api/auth/login to 10 requests a minute per IP (burst 20). The suites log in far
more often than a person would, so every login waits out a 429 instead of failing."""
import time

RATE_LIMIT_TEXT = "Too many login attempts"
ATTEMPTS = 12
WAIT_S = 7


def _status(response):
    return response.status_code if hasattr(response, "status_code") else response.status


def login_with_retry(attempt, attempts=ATTEMPTS, wait=WAIT_S):
    """attempt() performs one login request (requests or Playwright APIRequestContext); returns the first non-429 response."""
    response = attempt()
    for _ in range(attempts):
        if _status(response) != 429:
            break
        time.sleep(wait)
        response = attempt()
    return response


def submit_login(page, navigate=True, attempts=ATTEMPTS, wait=WAIT_S):
    """Click #login-btn; when the page shows the rate-limit message, wait and click again.
    navigate=True expects the login to succeed and the page to leave login.html."""
    from playwright.sync_api import TimeoutError as PwTimeout

    for _ in range(attempts):
        if navigate:
            try:
                with page.expect_navigation(timeout=8000):
                    page.click("#login-btn")
                return
            except PwTimeout:
                pass
        else:
            page.click("#login-btn")
            page.wait_for_timeout(600)
        err = page.locator("#login-error")
        text = err.inner_text() if err.count() else ""
        if RATE_LIMIT_TEXT in text:
            time.sleep(wait)
            continue
        if navigate:
            raise AssertionError(f"login did not navigate: {text or page.url}")
        return
    raise AssertionError("login stayed rate-limited")


def press_enter_login(page, attempts=ATTEMPTS, wait=WAIT_S):
    """Submit the login form with Enter in #password; wait out the rate limit like submit_login."""
    from playwright.sync_api import TimeoutError as PwTimeout

    for _ in range(attempts):
        page.press("#password", "Enter")
        try:
            page.wait_for_url(lambda url: "/login.html" not in url, timeout=8000)
            return
        except PwTimeout:
            pass
        err = page.locator("#login-error")
        text = err.inner_text() if err.count() else ""
        if RATE_LIMIT_TEXT in text:
            time.sleep(wait)
            continue
        raise AssertionError(f"login did not navigate: {text or page.url}")
    raise AssertionError("login stayed rate-limited")
