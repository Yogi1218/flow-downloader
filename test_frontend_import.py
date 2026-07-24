from playwright.sync_api import sync_playwright
import os

def run_cuj(page):
    page.goto("http://localhost:8080")
    page.wait_for_timeout(1000)

    # Create a dummy urls.txt file
    with open("urls.txt", "w") as f:
        f.write("https://www.youtube.com/watch?v=BaW_jenozKc\nhttps://www.youtube.com/watch?v=kJQP7kiw5Fk")

    # Set the file in the hidden input using page.set_input_files
    page.set_input_files("#bulkImportInput", "urls.txt")
    page.wait_for_timeout(2000)

    os.makedirs("/tmp/verification/screenshots", exist_ok=True)
    page.screenshot(path="/tmp/verification/screenshots/verification2.png")

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
