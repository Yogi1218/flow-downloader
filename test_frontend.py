from playwright.sync_api import sync_playwright
import os

def run_cuj(page):
    page.goto("http://localhost:8080")
    page.wait_for_timeout(1000)

    # Navigate to Settings
    page.get_by_role("button", name="Settings").click()
    page.wait_for_timeout(1000)

    # Click the general tab just in case
    page.get_by_role("button", name="General & Saving").click()
    page.wait_for_timeout(500)

    # Check Download Subtitles
    page.locator("#settingDownloadSubtitles").check(force=True)
    page.wait_for_timeout(500)

    # Check Download Metadata
    page.locator("#settingDownloadMetadata").check(force=True)
    page.wait_for_timeout(500)

    # Click Save Changes
    page.get_by_role("button", name="Save Changes").click()
    page.wait_for_timeout(1000)

    # Navigate back to Home
    page.get_by_role("button", name="Home").first.click()
    page.wait_for_timeout(1000)

    os.makedirs("/tmp/verification/screenshots", exist_ok=True)
    page.screenshot(path="/tmp/verification/screenshots/verification.png")

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            record_video_dir="/tmp/verification/videos"
        )
        page = context.new_page()
        try:
            run_cuj(page)
        finally:
            context.close()
            browser.close()
