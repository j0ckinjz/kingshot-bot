"""
redeemer.py
-----------
Selenium-based gift code redemption on https://ks-giftcode.centurygame.com/

Returns a dict keyed by player ID. Each value is a structured result:
{
  "status": "redeemed" | "already_claimed" | "same_type_once" |
            "expired" | "not_found" | "claim_limit_reached" |
            "server_busy" | "not_logged_in" | "invalid_player" | "unknown",
  "message": "<exact .message_modal .msg text or diagnostic>",
  "terminal": bool,
  "success": bool,
}

Design notes:
  - Success-like statuses (redeemed / already_claimed / same_type_once) are done.
  - Terminal failures (expired / not_found / invalid_player) are also done and should not retry.
  - Retryable failures (server_busy / not_logged_in / unknown / claim_limit_reached) remain pending.
  - A single Chrome session handles all players for speed.
"""

import os
import time
from typing import Any, Dict, List

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

SITE_URL = "https://ks-giftcode.centurygame.com/"
WAIT_TIMEOUT = 15
POST_LOGIN_WAIT = 2
POST_CONFIRM_WAIT = 1
BETWEEN_PLAYERS = 2
SERVER_BUSY_RETRIES = 2
SERVER_BUSY_DELAY = 4
CODE_INPUT_RETRIES = 3
CODE_INPUT_DELAY = 0.35

RESULT_MODAL_CSS = ".message_modal .msg"
ROLE_INFO_CSS = ".roleInfo_con"
CODE_INPUT_XPATH = '//input[@placeholder="Enter Gift Code"]'
CONFIRM_BUTTON_XPATH = (
    '//div[contains(@class,"exchange_btn") and contains(normalize-space(),"Confirm")]'
)

EXACT_STATUS_MAP = {
    "redeemed, please claim the rewards in your mail!": "redeemed",
    "already claimed, unable to claim again.": "already_claimed",
    "the same gift code type can only be redeemed once!": "same_type_once",
    "expired, unable to claim.": "expired",
    "claim limit reached, unable to claim.": "claim_limit_reached",
    "gift code not found, this is case-sensitive!": "not_found",
    "server busy. please try again later.": "server_busy",
    "please log in to relevant character before redemption": "not_logged_in",
    "your account does not satisfy the redemption requirements, please consult the customer service if you have any questions.": "requirements_not_met",
    "(no result message captured)": "unknown",
}

SUCCESS_STATUSES = {"redeemed", "already_claimed", "same_type_once"}
TERMINAL_STATUSES = SUCCESS_STATUSES | {"expired", "not_found", "invalid_player", "requirements_not_met"}

ResultDict = Dict[str, Any]


def build_driver(headless: bool = True) -> webdriver.Chrome:
    """Build a stealthy headless Chrome driver."""
    os.environ.setdefault(
        "SE_CACHE_PATH",
        os.path.join(os.path.dirname(__file__), ".selenium_cache"),
    )
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--window-size=1280,800")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    chrome_binary = "/usr/bin/google-chrome"
    if os.path.exists(chrome_binary):
        opts.binary_location = chrome_binary

    driver = webdriver.Chrome(options=opts)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def wait_for_element(wait: WebDriverWait, by: By, value: str, description: str = "element"):
    try:
        return wait.until(EC.presence_of_element_located((by, value)))
    except TimeoutException as exc:
        raise TimeoutException(f"Timed out waiting for {description}") from exc


def wait_for_clickable(wait: WebDriverWait, by: By, value: str, description: str = "button"):
    try:
        return wait.until(EC.element_to_be_clickable((by, value)))
    except TimeoutException as exc:
        raise TimeoutException(f"Timed out waiting for clickable {description}") from exc


def normalize_message(text: str) -> str:
    return " ".join(text.strip().split())


def classify_result_status(result: str) -> str:
    text = normalize_message(result).lower()
    if text in EXACT_STATUS_MAP:
        return EXACT_STATUS_MAP[text]
    if text.startswith("redeemed, please claim"):
        return "redeemed"
    if "already claimed" in text:
        return "already_claimed"
    if "can only be redeemed once" in text:
        return "same_type_once"
    if "expired" in text:
        return "expired"
    if "claim limit reached" in text:
        return "claim_limit_reached"
    if (
        ("invalid" in text and any(token in text for token in ("player", "character", "id")))
        or ("not found" in text and any(token in text for token in ("player", "character", "role")))
        or ("does not exist" in text and any(token in text for token in ("player", "character", "role")))
    ):
        return "invalid_player"
    if "not found" in text:
        return "not_found"
    if "server busy" in text:
        return "server_busy"
    if "please log in" in text:
        return "not_logged_in"
    if "does not satisfy the redemption requirements" in text:
        return "requirements_not_met"
    return "unknown"


def is_terminal_status(status: str) -> bool:
    return status in TERMINAL_STATUSES


def is_success_status(status: str) -> bool:
    return status in SUCCESS_STATUSES


def build_result(status: str, message: str) -> ResultDict:
    return {
        "status": status,
        "message": message,
        "terminal": is_terminal_status(status),
        "success": is_success_status(status),
    }


def _screenshot(driver: webdriver.Chrome, pid: str, name: str, reason: str):
    """Save a debug screenshot on error. Never raises."""
    try:
        os.makedirs("screenshots", exist_ok=True)
        ts = int(time.time())
        safe_name = "".join(ch for ch in name if ch.isalnum() or ch in ("-", "_")) or "player"
        filename = f"screenshots/{ts}_{pid}_{safe_name}_{reason}.png"
        driver.save_screenshot(filename)
    except Exception:
        pass


def get_visible_modal_message(driver: webdriver.Chrome) -> str:
    try:
        elements = driver.find_elements(By.CSS_SELECTOR, RESULT_MODAL_CSS)
        for element in elements:
            if element.is_displayed():
                text = normalize_message(element.text)
                if text:
                    return text
    except Exception:
        pass
    return ""


def dismiss_existing_modal(driver: webdriver.Chrome):
    """Close a visible result modal if one is lingering from a previous action."""
    try:
        buttons = driver.find_elements(By.CSS_SELECTOR, ".message_modal .confirm_btn, .message_modal .close_btn")
        for button in buttons:
            if button.is_displayed():
                driver.execute_script("arguments[0].click();", button)
                break
        WebDriverWait(driver, 2).until_not(
            EC.visibility_of_element_located((By.CSS_SELECTOR, RESULT_MODAL_CSS))
        )
    except Exception:
        pass


def wait_for_login_ready(driver: webdriver.Chrome, wait: WebDriverWait):
    """Require actual character context and an interactable code field."""
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ROLE_INFO_CSS)))
        wait_for_element(wait, By.XPATH, CODE_INPUT_XPATH, "Gift Code input")

        def input_ready(drv: webdriver.Chrome):
            try:
                code_input = drv.find_element(By.XPATH, CODE_INPUT_XPATH)
                if not code_input.is_displayed() or not code_input.is_enabled():
                    return False
                return code_input
            except Exception:
                return False

        wait.until(input_ready)
    except TimeoutException as exc:
        modal_message = get_visible_modal_message(driver)
        if modal_message:
            raise TimeoutException(f"Login did not reach code entry: {modal_message}") from exc

        role_present = bool(driver.find_elements(By.CSS_SELECTOR, ROLE_INFO_CSS))
        code_present = bool(driver.find_elements(By.XPATH, CODE_INPUT_XPATH))
        raise TimeoutException(
            "Login did not reach code entry "
            f"(role_present={role_present}, code_present={code_present})"
        ) from exc


def get_result_message(driver: webdriver.Chrome, previous_message: str = "") -> str:
    """Read the site's actual redemption modal message, preferring a fresh one."""
    time.sleep(POST_CONFIRM_WAIT)
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    def fresh_message(drv: webdriver.Chrome):
        text = get_visible_modal_message(drv)
        if not text:
            return False
        if previous_message and text == previous_message:
            return False
        return text

    try:
        text = wait.until(fresh_message)
        return normalize_message(text)
    except TimeoutException:
        text = get_visible_modal_message(driver)
        if text:
            return text
        return "(no result message captured)"


def set_code_input_value(driver: webdriver.Chrome, wait: WebDriverWait, code: str, log):
    """Set the code input and verify the typed value actually sticks."""
    last_value = ""
    for attempt in range(1, CODE_INPUT_RETRIES + 1):
        code_input = wait_for_element(wait, By.XPATH, CODE_INPUT_XPATH, "Gift Code input")
        code_input.clear()
        time.sleep(0.1)
        code_input.send_keys(code)
        time.sleep(CODE_INPUT_DELAY)
        last_value = (code_input.get_attribute("value") or "").strip()
        log.info("    Code input attempt %s/%s value=%r", attempt, CODE_INPUT_RETRIES, last_value)
        if last_value == code:
            return code_input
    raise TimeoutException(
        f"Gift code input did not retain value: expected {code!r}, got {last_value!r}"
    )


def wait_for_confirm_enabled(driver: webdriver.Chrome, wait: WebDriverWait, log):
    """Wait for the Confirm button to become enabled and clickable."""
    last_classes = ""

    def confirm_ready(drv: webdriver.Chrome):
        nonlocal last_classes
        try:
            button = drv.find_element(By.XPATH, CONFIRM_BUTTON_XPATH)
            last_classes = (button.get_attribute("class") or "").strip()
            disabled = "disabled" in last_classes.lower()
            if disabled or not button.is_displayed() or not button.is_enabled():
                return False
            return button
        except Exception:
            return False

    try:
        return wait.until(confirm_ready)
    except TimeoutException as exc:
        log.warning("    Confirm button did not enable. last_class=%r", last_classes)
        raise TimeoutException(f"Confirm button stayed disabled (class={last_classes!r})") from exc


def redeem_single(
    driver: webdriver.Chrome,
    wait: WebDriverWait,
    pid: str,
    name: str,
    code: str,
    log,
) -> ResultDict:
    """Attempt to redeem one code for one player."""
    log.info("  ▶ %s (%s)", name, pid)
    try:
        driver.get(SITE_URL)
        dismiss_existing_modal(driver)

        player_input = wait_for_element(
            wait,
            By.XPATH,
            '//input[@placeholder="Player ID"]',
            "Player ID input",
        )
        player_input.clear()
        player_input.send_keys(pid)

        login_btn = wait_for_clickable(
            wait,
            By.XPATH,
            '//div[contains(@class,"login_btn") and contains(@class,"btn")]',
            "Login button",
        )
        login_btn.click()

        try:
            wait.until(
                EC.invisibility_of_element_located(
                    (By.XPATH, '//*[contains(@class,"loading")]')
                )
            )
        except TimeoutException:
            pass

        wait_for_login_ready(driver, wait)
        time.sleep(POST_LOGIN_WAIT)
        log.info("    ✓ Profile loaded.")

        previous_message = get_visible_modal_message(driver)
        if previous_message:
            log.info("    Clearing stale modal before redeem: %s", previous_message)
            dismiss_existing_modal(driver)
            previous_message = ""

        last_result: ResultDict = build_result("unknown", "(no result message captured)")

        for attempt in range(1, SERVER_BUSY_RETRIES + 2):
            set_code_input_value(driver, wait, code, log)
            confirm = wait_for_confirm_enabled(driver, wait, log)
            confirm_classes = (confirm.get_attribute("class") or "").strip()
            code_value = (driver.find_element(By.XPATH, CODE_INPUT_XPATH).get_attribute("value") or "").strip()
            log.info("    Ready to submit: code_value=%r confirm_class=%r", code_value, confirm_classes)

            driver.execute_script("arguments[0].click();", confirm)

            result_message = get_result_message(driver, previous_message)
            status = classify_result_status(result_message)
            result = build_result(status, result_message)
            last_result = result

            log.info("    Modal message: %s", result_message)
            log.info(
                "    Classified: status=%s terminal=%s success=%s",
                result["status"],
                result["terminal"],
                result["success"],
            )

            if status != "server_busy":
                return result

            if attempt > SERVER_BUSY_RETRIES:
                log.warning("    Server busy persisted after %s attempt(s).", attempt)
                return result

            log.warning(
                "    Server busy on attempt %s/%s — retrying in %ss",
                attempt,
                SERVER_BUSY_RETRIES + 1,
                SERVER_BUSY_DELAY,
            )
            dismiss_existing_modal(driver)
            previous_message = result_message
            time.sleep(SERVER_BUSY_DELAY)

        return last_result

    except TimeoutException as exc:
        log.error("    [TIMEOUT] %s", exc)
        try:
            code_value = (driver.find_element(By.XPATH, CODE_INPUT_XPATH).get_attribute("value") or "").strip()
            log.warning("    Debug timeout state: code_value=%r", code_value)
        except Exception:
            pass
        _screenshot(driver, pid, name, "timeout")
        message = f"timeout: {exc}"
        return build_result(classify_result_status(message), message)
    except NoSuchElementException as exc:
        log.error("    [NOT FOUND] %s", exc)
        _screenshot(driver, pid, name, "missing_element")
        return build_result("unknown", f"missing element: {exc}")
    except Exception as exc:
        log.error("    [ERROR] %s", exc, exc_info=True)
        _screenshot(driver, pid, name, "error")
        return build_result("unknown", f"error: {exc}")


def redeem_code_for_players(code: str, players: List, log) -> Dict[str, ResultDict]:
    """Redeem one code for all requested players in a single browser session."""
    results: Dict[str, ResultDict] = {}
    ok_n, fail_n = 0, 0
    start = time.time()

    driver = build_driver(headless=True)
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    try:
        for pid, name in players:
            result = redeem_single(driver, wait, pid, name, code, log)
            results[pid] = result

            if result["success"]:
                log.info("    ✅ SATISFIED — %s (%s) [%s]", name, pid, result["status"])
                ok_n += 1
            else:
                retry_note = "no retry" if result["terminal"] else "will retry"
                log.warning(
                    "    ❌ FAILED — %s (%s) [%s; %s]",
                    name,
                    pid,
                    result["status"],
                    retry_note,
                )
                fail_n += 1

            time.sleep(BETWEEN_PLAYERS)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    elapsed = time.time() - start
    log.info("  [%s] ✅ %s satisfied  ❌ %s failed  ⏱ %.1fs", code, ok_n, fail_n, elapsed)
    return results
