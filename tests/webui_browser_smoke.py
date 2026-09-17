"""Headless browser smoke check for the built desktop WebUI shell."""

from playwright.sync_api import sync_playwright


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://127.0.0.1:5173")
    page.wait_for_load_state("networkidle")
    assert page.locator("#root").is_visible()
    assert page.locator("body").inner_text().strip()
    browser.close()
