"""
redeemer.py
-----------
Selenium-based gift code redemption on https://ks-giftcode.centurygame.com/

Returns a dict { player_id: True/False } so the caller knows exactly
which players succeeded and which failed — enabling per-player tracking.
"""

import time
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException

SITE_URL = "https://ks-giftcode.centurygame.com/"

WAIT_TIMEOUT      = 15
POST_LOGIN_WAIT   = 3
POST_CONFIRM_WAIT = 3
BETWEEN_PLAYERS   = 2


def build_driver(headless: bool = True) -> webdriver.Chrome:
    os.environ.setdefault(
        "SE_CACHE_PATH",
        os.path.join(os.path.dirname(__file__), ".selenium_cache")
    )
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--window-size=1280,800")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    chrome_binary = "/usr/bin/google-chrome"
    if os.path.exists(chrome_binary):
        chrome_options.binary_location = chrome_binary

    driver = webdriver.Chrome(options=chrome_options)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def wait_for_element(wait, by, value, description="element"):
    try:
        return wait.until(EC.presence_of_element_located((by, value)))
    except TimeoutException:
        raise TimeoutException(f"Timed out waiting for {description}")


def wait_for_clickable(wait, by, value, description="button"):
    try:
        return wait.until(EC.element_to_be_clickable((by, value)))
    except TimeoutException:
        raise TimeoutException(f"Timed out waiting for clickable {description}")


def get_result_message(driver) -> str:
    selectors = [
        (By.XPATH, '//*[contains(@class,"result") or contains(@class,"toast") '
                   'or contains(@class,"modal") or contains(@class,"tip") '
                   'or contains(@class,"message")]'),
        (By.XPATH, '//div[contains(@class,"popup")]'),
        (By.XPATH, '//div[contains(@class,"dialog")]'),
    ]
    time.sleep(POST_CONFIRM_WAIT)
    for by, xpath in selectors:
        try:
            for el in driver.find_elements(by, xpath):
                text = el.text.strip()
                if text and len(text) > 3:
                    return text
        except Exception:
            continue
    return "(no result message captured)"


def redeem_single(driver, wait, pid: str, name: str, code: str, log) -> bool:
    """
    Attempt to redeem `code` for one player.
    Returns True on apparent success, False on failure.
    """
    log.info(f"  ▶ {name} ({pid})")
    try:
        driver.get(SITE_URL)

        # Enter player ID
        inp = wait_for_element(wait, By.XPATH, '//input[@placeholder="Player ID"]', "Player ID input")
        inp.clear()
        inp.send_keys(pid)

        # Click login
        login_btn = wait_for_clickable(
            wait,
            By.XPATH,
            '//div[contains(@class,"login_btn") and contains(@class,"btn")]',
            "Login button"
        )
        login_btn.click()

        # Wait for loading to finish
        try:
            wait.until(EC.invisibility_of_element_located((By.XPATH, '//*[contains(@class,"loading")]')))
        except TimeoutException:
            pass

        # Confirm login worked — gift code input appears after valid login
        wait_for_element(wait, By.XPATH, '//input[@placeholder="Enter Gift Code"]', "Gift Code input")
        time.sleep(POST_LOGIN_WAIT)
        log.info("    Profile loaded.")

        # Enter code
        code_inp = driver.find_element(By.XPATH, '//input[@placeholder="Enter Gift Code"]')
        code_inp.clear()
        code_inp.send_keys(code)

        # Click confirm
        confirm = wait_for_clickable(
            wait,
            By.XPATH,
            '//div[contains(@class,"exchange_btn") and contains(text(),"Confirm")]',
            "Confirm button"
        )
        driver.execute_script("arguments[0].click();", confirm)

        # Read result
        result = get_result_message(driver)
        log.info(f"    Result: {result}")

        failed_kw = ["expired", "invalid", "error", "fail", "already", "used", "wrong"]
        return not any(kw in result.lower() for kw in failed_kw)

    except TimeoutException as e:
        log.error(f"    [TIMEOUT] {e}")
        _screenshot(driver, pid, name, "timeout")
        return False
    except NoSuchElementException as e:
        log.error(f"    [NOT FOUND] {e}")
        _screenshot(driver, pid, name, "missing_element")
        return False
    except Exception as e:
        log.error(f"    [ERROR] {e}")
        _screenshot(driver, pid, name, "error")
        return False


def _screenshot(driver, pid, name, reason):
    try:
        os.makedirs("screenshots", exist_ok=True)
        driver.save_screenshot(f"screenshots/debug_{pid}_{name}_{reason}.png")
    except Exception:
        pass


def redeem_code_for_players(code: str, players: list, log) -> dict:
    """
    Redeem `code` for each (pid, name) in `players`.

    Returns a dict:  { pid: True/False }
      True  = successfully redeemed
      False = failed (will be retried next check cycle)

    Players are run in a single Chrome session for speed.
    """
    results    = {}
    driver     = build_driver(headless=True)
    wait       = WebDriverWait(driver, WAIT_TIMEOUT)
    start      = time.time()
    success_n  = 0
    fail_n     = 0

    try:
        for pid, name in players:
            ok = redeem_single(driver, wait, pid, name, code, log)
            results[pid] = ok
            if ok:
                log.info(f"    ✅ SUCCESS — {name} ({pid})")
                success_n += 1
            else:
                log.warning(f"    ❌ FAILED  — {name} ({pid})")
                fail_n += 1
            time.sleep(BETWEEN_PLAYERS)
    finally:
        driver.quit()

    elapsed = time.time() - start
    log.info(f"\n  [{code}] ✅ {success_n} succeeded  ❌ {fail_n} failed  ⏱ {elapsed:.1f}s")
    return results