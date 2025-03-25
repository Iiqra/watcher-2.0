#!/usr/bin/env python3

"""
Usage:
  python3 dom_analysis.py ANY URL

This script:
1. Launches a headless browser via Playwright.
2. Navigates to the given URL, waiting until DOM is loaded (instead of strict network idle).
3. Extracts the final HTML.
4. Optionally cleans it up (removing scripts, styles, etc.).
5. Sends that HTML to Anthropic's Messages API for DOM analysis.
6. Prints Claude's output.

Requires:
  - pip install playwright anthropic
  - playwright install
  - ANTHROPIC_API_KEY set in environment or hard-coded.
"""

import os
import sys
import re
import time
import anthropic
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json
from dotenv import load_dotenv 

load_dotenv()

def normalize_file_name(filename: str):
    return filename.replace("https://", "").replace("http://", "").replace("/", "")

def wait_for_dom_stability(page, check_interval=0.5, stable_iterations=3, timeout=5.0):
    """
    Checks the size of page.content() in a loop until it stops changing
    for `stable_iterations` times in a row, or until `timeout` is reached.
    This helps ensure JS-driven DOM changes have finished.
    """
    max_checks = int(timeout // check_interval)
    last_size = -1
    stable_count = 0

    for _ in range(max_checks):
        html = page.content()
        size = len(html)
        if size == last_size:
            stable_count += 1
            if stable_count >= stable_iterations:
                print("DOM has stabilized.")
                return
        else:
            stable_count = 0
            last_size = size
        time.sleep(check_interval)

    print("Warning: DOM may not be fully stable after timeout.")

def main():
    # 1) Get URL from command-line args (or use a default)
    target_url = sys.argv[1] if len(sys.argv) > 1 else "https://www.grass-direct.co.uk/"
    print("Target URL:", target_url)

    # 1.a) Prepare a folder in the selectors for this client.
    if not os.path.exists("../selectors/{}".format(normalize_file_name(target_url))):
        os.makedirs("../selectors/{}".format(normalize_file_name(target_url)))

    # 2) Set up Anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")

    # 3) Launch Playwright and navigate
    with sync_playwright() as p:
        # Launch headless Chromium
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/128.0.0.0 Safari/537.36"
        )

        print("Navigating to:", target_url)
        # Instead of "networkidle", use "domcontentloaded" plus a larger timeout
        page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)

        # Optional: Extra wait for JS-driven DOM changes
        wait_for_dom_stability(page, check_interval=0.5, stable_iterations=3, timeout=5.0)

        # 4) Extract final HTML
        html = page.content()
        print(f"HTML length: {len(html)}")

        # 5) Optional cleanup with BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # Remove script, style, nav, footer, etc. if desired
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        cleaned_html = str(soup)
        print(f"Cleaned HTML length: {len(cleaned_html)}")

        # Close browser
        browser.close()

    # 6) Provide your "system" instructions as a top-level parameter (NOT in messages)
    system_text = (
        """You are a DOM analyzer specializing in e-commerce element detection. Your task is to analyze the provided HTML and identify precise selectors for tracking critical conversion elements. Do not guess or hallucinate. Base your analysis on the actual DOM structure you see. If you are uncertain or cannot find an element, explicitly say so.

---
Core Responsibilities
1. Given the page’s HTML, identify unique selectors for:
   - Cookie consent components
   - Go to a Product detail page, grab its URL
   - Add to cart or Add to bag button 
   - Any Modal/popup elements that comes before clicking on Add to Cart or Add to bag button 
   - Click on button to add the product in the cart and then get Initiate Checkout or Begin Checkout Button selector
   - Any Modal/popup elements that comes before clicking on Add to Cart or Add to bag button 

2. The path for analysis is:
   - Access Homepage
   - Deal with any cookie banner
   - Locate a product detail page (preferably in-stock, no variations)
   - Add product to cart
   - Begin checkout

3. For each element, output selectors in this structured format:
   {
     "elementType": string,    // e.g., "cookieAccept" or "addToCart"
     "selectors": {
       "primary": string,      // Most reliable
       "secondary": string[],  // Fallback(s)
       "xpath": string         // Final fallback
     },
     "location": string,       // Where on the page
     "priority": number        // 1 (highest) .. 5 (lowest)
   }

---
### Priority for Selector Types
1. data-testid
2. aria-label
3. id
4. Custom data attributes
5. Unique class combos
6. XPath

---
### Cookie Banner Review
- Check if the cookie banner has Accept All and Reject All buttons and identify the selectors.
- Check if the cookie banner allows users to select analytics, marketing, and functional consents separately.

---
### Required Output Format
Return a JSON object:
{
  "url": string,
  "timestamp": string,
  "elements": {
    "hasCookieBanner": boolean, // e.g. Check whether the page shows a cookie banner on first visit.
    "cookies": {
      "accept": SelectorObject,
      "decline": SelectorObject,
      "settings": SelectorObject,
      "acceptAll": SelectorObject, // e.g. Allow All, Accept All
      "rejectAll": SelectorObject   // e.g. Reject All, Deny All, Refuse All
      "marketingChoices": boolean,
      "analyticsChoices": boolean,
      "functionalChoices": boolean
    },
    "product": {
      "url": string,
      "stock": SelectorObject,
      "price": SelectorObject,
      "addToCart": SelectorObject
    },
    "addtocart": {
      "button": SelectorObject,
      "cart": SelectorObject
    },
    "checkout": {
      "button": SelectorObject,
      "cart": SelectorObject
    },
    "popups": PopupObject[]
  },
  "notes": string[]
}

Where each of those “SelectorObject” entries follows the shape:
{
  "elementType": "...",
  "selectors": {
    "primary": "...",
    "secondary": ["..."],
    "xpath": "..."
  },
  "location": "...",
  "priority": ...
}

---
### Example Element Output
{
  "elementType": "cookieAccept",
  "selectors": {
    "primary": "[data-testid=\"cookie-accept\"]",
    "secondary": [
      "[aria-label=\"accept cookies\"]",
      "#cookie-accept-button"
    ],
    "xpath": "//button[contains(text(),\"Accept\")]"
  },
  "location": "Bottom banner fixed position",
  "priority": 1
}

---
### Popup Detection Requirements
- Track appearance timing or triggers
- Note dismissal methods
- Log frequency limits

---
### Error Handling
- If an element cannot be found, or you are unsure, note it under “errors” or in “notes.”
- Suggest fallback detection methods if relevant.

---
### Final Response Format
{
  "status": "success" | "partial" | "failed",
  "elements": ElementObject[],
  "popups": PopupObject[],
  "errors": ErrorObject[],
  "recommendations": string[]
}

---
### Validation Rules
- Verify selector uniqueness
- Check stability
- Confirm visibility
- Validate with dynamic changes

Please analyze the incoming HTML following these guidelines and return a single JSON object in the specified structure. Make sure to disclaim if you cannot be certain of specific details. Please remove any gebrish from the output and provide only valid json object which can be used further
"""
    )

    # Create a client.
    client = anthropic.Anthropic(api_key=api_key)

    # 7) For the actual user message, we only have "user" or "assistant" roles in `messages[]`
    user_message = f"Here is the sanitized HTML for {target_url}:\n\n{cleaned_html}"

    messages = [
        {"role": "user", "content": user_message}
    ]

    #print("Sending HTML to Claude for analysis...")
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",  # or whichever model your account has access to
        system=system_text,
        messages=messages,       # Only user+assistant roles allowed
        max_tokens=2000,
        temperature=0.2,
        stream=False
    )

    # response is a single "Message" object with .content
    #print("\n=== CLAUDE DOM ANALYSIS RESPONSE ===")
    #print(response.content)
    blocks = response.content  # This is a list of TextBlock objects
    combined_text = "\n".join(block.text for block in blocks)
    
    print(type(blocks), len(blocks))
    implicit_json = blocks[0]

    # 3) Look for JSON in triple-backticks
    match = re.search(r"```json(.*?)```", combined_text, flags=re.DOTALL)

    if match:
        json_str = match.group(1).strip()
        try:
            data = json.loads(json_str)
            # Print nicely
            print("=== PARSED JSON ===")
            print(json.dumps(data, indent=2))

            # Write to file.
            file_name = sys.argv[1] if len(sys.argv) > 1 else "www.grass-direct.co.uk.json"
            if file_name != "www.grass-direct.co.uk.json":
                file_name = normalize_file_name(file_name)
            
            print("=== WRITING JSON TO FILE {} ===".format(file_name))
            with open("../selectors/{}/pv_to_atc.json".format(file_name), "w+") as output_file:
                output_file.write(json_str)
        except json.JSONDecodeError:
            print("=== RAW TEXT (JSON decode error) ===")
            print(json_str)
    else:
        # If there's no code block or you prefer to just print everything:
        print("=== RAW TEXT ===")
        print(combined_text)

        print("=== RAW JSON ===")
        print(implicit_json)

if __name__ == "__main__":
    main()