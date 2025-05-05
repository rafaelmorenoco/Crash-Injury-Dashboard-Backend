# screenshot_emailer.py
import os
import sys
import smtplib
import pytz
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from datetime import datetime
import re
from playwright.sync_api import sync_playwright

def take_screenshot(url, filename, width=400):
    """
    Take a screenshot of the specified URL using Playwright with a mobile viewport.
    
    The viewport is set to a mobile-like width (default 400 pixels). The height is 
    arbitrarily set (here 800) because we use full_page=True; that ensures the final 
    image captures the entire page regardless of the initial viewport height.
    
    The mobile emulation (is_mobile=True) is enabled so that the website renders as it would on a mobile device.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # Create a browser context with mobile emulation.
        context = browser.new_context(
            viewport={'width': width, 'height': 800},  # height here is a placeholder for initial rendering
            is_mobile=True  # Enable mobile emulation (touch events, mobile user agent, etc.)
        )
        page = context.new_page()
        
        # Navigate to the URL.
        print(f"DEBUG: Navigating to {url}")
        try:
            page.goto(url, timeout=60000)  # Increased timeout to 60 seconds
            page.wait_for_load_state("networkidle", timeout=60000)
            
            # Wait a bit longer to ensure dynamic content is loaded
            page.wait_for_timeout(5000)  # Wait 5 seconds
            
            # Try to find the note element that contains the date info
            note_element = page.query_selector('text="latest crash record"')
            if note_element:
                print(f"DEBUG: Found note element with date info: {note_element.inner_text()}")
            else:
                print("DEBUG: Could not find note element with date info")
                
            # Take a full-page screenshot (the final height is determined automatically).
            page.screenshot(path=filename, full_page=True)
            
            # Return the page content for parsing if needed
            content = page.content()
            print(f"DEBUG: Page content length: {len(content)} characters")
            return content
            
        except Exception as e:
            print(f"DEBUG: Error during page loading: {e}")
            # Take screenshot of whatever is loaded anyway
            try:
                page.screenshot(path=filename, full_page=True)
            except:
                pass
            return page.content()

def extract_latest_update_date(html_content):
    """
    Extract the latest update date from the dashboard page HTML content.
    Looking for a pattern like: "data was last updated on MM/DD/YY HH:MM."
    """
    # Print a small sample of the HTML content for debugging
    sample = html_content[:10000] if len(html_content) > 10000 else html_content
    print(f"DEBUG: Searching for date pattern in HTML content sample:\n{sample[:1000]}...")
    
    # Try several patterns to handle different possible formats
    patterns = [
        r"data was last updated on (\d{2}/\d{2}/\d{2})",  # Original pattern
        r"last updated on (\d{2}/\d{2}/\d{2})",           # Without "data was"
        r"updated on (\d{2}/\d{2}/\d{2})",                # Just "updated on"
        r"update.*?(\d{2}/\d{2}/\d{2})",                  # Any text with "update" followed by date
        r">(\d{2}/\d{2}/\d{2})</span>",                   # Date in a span
        r"(\d{2}/\d{2}/\d{2})\s+\d{2}:\d{2}"              # Date followed by time
    ]
    
    for pattern in patterns:
        print(f"DEBUG: Trying pattern: {pattern}")
        match = re.search(pattern, html_content, re.IGNORECASE)
        if match:
            date = match.group(1)
            print(f"DEBUG: Found date: {date}")
            return date
    
    print("DEBUG: No date pattern matched in the HTML content")
    return None

def take_screenshot_and_extract_date(url, filename, width=400):
    """
    Take a screenshot of the URL and extract the latest update date directly from the page.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={'width': width, 'height': 800},
            is_mobile=True
        )
        page = context.new_page()
        
        try:
            page.goto(url, timeout=60000)
            page.wait_for_load_state("networkidle", timeout=60000)
            
            # Wait for the page to fully render
            page.wait_for_timeout(5000)
            
            # Take screenshot
            page.screenshot(path=filename, full_page=True)
            
            # Direct JavaScript extraction of the date from DOM
            # This looks for the Note element with text about data updates
            # and extracts just the date part in MM/DD/YY format
            date_text = page.evaluate("""() => {
                // Look for the note element with text about latest update
                const noteElements = Array.from(document.querySelectorAll('div, p, span'));
                const updateElement = noteElements.find(el => 
                    el.textContent && el.textContent.includes('last updated on'));
                
                if (updateElement) {
                    // Extract just the date part (MM/DD/YY)
                    const dateMatch = updateElement.textContent.match(/last updated on (\\d{2}\\/\\d{2}\\/\\d{2})/i);
                    if (dateMatch && dateMatch[1]) {
                        return dateMatch[1];
                    }
                }
                return null;
            }""")
            
            print(f"DEBUG: Direct DOM extraction found date: {date_text}")
            
            # Get page content as fallback
            html_content = page.content()
            
            return html_content, date_text
            
        except Exception as e:
            print(f"DEBUG: Error during page loading: {e}")
            try:
                page.screenshot(path=filename, full_page=True)
            except:
                pass
            return page.content(), None

def send_email_with_embedded_images(gmail_address, app_password, recipient_email, subject, 
                                   url1, url2, image_path1, image_path2):
    """Send an email with two screenshots embedded side by side in the body using Gmail."""
    msg = MIMEMultipart('related')
    msg['From'] = gmail_address
    msg['To'] = recipient_email
    msg['Subject'] = subject
    
    with open(image_path1, 'rb') as f:
        img_data1 = f.read()
    
    with open(image_path2, 'rb') as f:
        img_data2 = f.read()
    
    today = datetime.now().strftime('%Y-%m-%d')
    # Modified HTML: the second image now appears first.
    html = f"""
    <html>
      <body>
        <h2>Daily Crash Injury Dashboard Screenshots</h2>
        <div style="display: flex; flex-wrap: wrap; gap: 20px;">
          <div style="flex: 1; min-width: 300px;">
            <p>Screenshot of <a href="{url2}">{url2}</a> taken on {today}:</p>
            <img src="cid:screenshot2" style="max-width:100%; height:auto;">
          </div>
          <div style="flex: 1; min-width: 300px;">
            <p>Screenshot of <a href="{url1}">{url1}</a> taken on {today}:</p>
            <img src="cid:screenshot1" style="max-width:100%; height:auto;">
          </div>
        </div>
        <p>This is an automated email sent from GitHub Actions.</p>
      </body>
    </html>
    """
    
    msg_alternative = MIMEMultipart('alternative')
    msg_alternative.attach(MIMEText(html, 'html'))
    msg.attach(msg_alternative)
    
    # Attach the first image (corresponding to url1)
    image1 = MIMEImage(img_data1)
    image1.add_header('Content-ID', '<screenshot1>')
    image1.add_header('Content-Disposition', 'inline')
    msg.attach(image1)
    
    # Attach the second image (corresponding to url2)
    image2 = MIMEImage(img_data2)
    image2.add_header('Content-ID', '<screenshot2>')
    image2.add_header('Content-Disposition', 'inline')
    msg.attach(image2)
    
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(gmail_address, app_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False

def get_today_date_est():
    """Get today's date in MM/DD/YY format in Eastern Time (EST/EDT)"""
    eastern = pytz.timezone('US/Eastern')
    today = datetime.now(eastern)
    return today.strftime('%m/%d/%y')

def main():
    # Get environment variables.
    url1 = os.environ.get('WEBSITE_URL')
    url2 = os.environ.get('WEBSITE_URL_2')
    gmail_address = os.environ.get('GMAIL_ADDRESS')
    app_password = os.environ.get('GMAIL_APP_PASSWORD')
    recipient_email = os.environ.get('RECIPIENT_EMAIL')
    
    # Use MOBILE default width of 400 (ignoring any environment-provided height).
    width = int(os.environ.get('SCREENSHOT_WIDTH', 400))
    
    # Generate filenames with current date.
    eastern = pytz.timezone('US/Eastern')
    today = datetime.now(eastern)
    date_str = today.strftime('%Y-%m-%d')
    filename1 = f"screenshot1_{date_str}.png"
    filename2 = f"screenshot2_{date_str}.png"
    
    # Take screenshot of the first URL (the dashboard with date info) and extract date directly
    print(f"Taking screenshot of {url1}...")
    html_content, latest_update_date = take_screenshot_and_extract_date(url1, filename1, width)
    
    # If direct extraction failed, try regex parsing
    if not latest_update_date:
        print("DEBUG: Direct extraction failed, falling back to regex")
        latest_update_date = extract_latest_update_date(html_content)
    
    # If still no date found, write the HTML for debugging
    if not latest_update_date:
        print("WARNING: Could not find the latest update date in the dashboard page.")
        print("DEBUG: Will proceed with screenshots and email anyway for debugging purposes.")
        # Write HTML content to a file for debugging
        with open("debug_html.txt", "w", encoding="utf-8") as f:
            f.write(html_content)
        print("DEBUG: Wrote HTML content to debug_html.txt")
        
        # Instead of exiting, let's assign today's date temporarily for debugging
        latest_update_date = get_today_date_est()
        print(f"DEBUG: Temporarily using today's date: {latest_update_date}")
    else:
        # Get today's date in Eastern Time
        today_date_est = get_today_date_est()
        
        print(f"Latest update date from dashboard: {latest_update_date}")
        print(f"Today's date (EST/EDT): {today_date_est}")
        
        # Compare dates
        if latest_update_date != today_date_est:
            print(f"Data is not updated today. Latest update date ({latest_update_date}) doesn't match today's date ({today_date_est}).")
            print("Exiting without sending email.")
            sys.exit(1)
    
    # If we get here, dates match or we're debugging, so take the second screenshot and send email
    print(f"Taking screenshot of {url2}...")
    take_screenshot(url2, filename2, width)
    
    subject = f"Daily Dashboard Screenshots - {date_str}"
    success = send_email_with_embedded_images(
        gmail_address, app_password, recipient_email, subject, 
        url1, url2, filename1, filename2
    )
    
    if success:
        print("Screenshots taken and emailed successfully with embedded images!")
    else:
        print("Failed to send email.")
        sys.exit(1)

if __name__ == "__main__":
    main()
