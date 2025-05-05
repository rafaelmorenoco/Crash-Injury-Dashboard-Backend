# screenshot_emailer.py
import os
import smtplib
import re
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  # Python 3.9+ timezone module
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
        
        # Navigate to the URL and wait for it to fully load
        page.goto(url)
        page.wait_for_load_state("networkidle")
        
        # Add a delay to ensure dynamic content has time to load
        page.wait_for_timeout(2000)  # 2 seconds
        
        # Take a full-page screenshot (the final height is determined automatically).
        page.screenshot(path=filename, full_page=True)
        
        # Get the content before closing the browser
        content = page.content()
        
        # Write content to debug file (will be stored as an artifact in GitHub Actions)
        with open(f"debug_{os.path.basename(filename).replace('.png', '.html')}", "w", encoding="utf-8") as f:
            f.write(content)
            
        # Close everything explicitly
        page.close()
        context.close()
        browser.close()
        
        return content  # Return just the content, not the browser objects

def check_date_current(page_content, url):
    """
    Check if the date in the content is current (today or yesterday).
    Uses multiple regex patterns to try to find date references.
    
    Returns:
    - tuple: (is_valid, message)
      - is_valid: bool - True if date is current, False otherwise
      - message: str - Reason if invalid, or confirmation if valid
    """
    # Write the first 1000 characters to a debug file for inspection
    with open(f"date_debug_{url.replace('://', '_').replace('/', '_')}.txt", "w", encoding="utf-8") as f:
        f.write(page_content[:10000])
    
    # Multiple patterns to try
    date_patterns = [
        r"data was last updated on (\d{2}/\d{2}/\d{2})",  # Original pattern
        r"data was last updated on (\d{1,2}/\d{1,2}/\d{2})",  # More flexible spacing
        r"[Dd]ata\s+was\s+last\s+updated\s+on\s+(\d{1,2}/\d{1,2}/\d{2,4})",  # Case insensitive with flexible whitespace
        r"[Ll]ast\s+[Uu]pdated\s*:?\s*(\d{1,2}/\d{1,2}/\d{2,4})",  # "Last Updated: MM/DD/YY"
        r"[Ll]ast\s+[Uu]pdated\s*:?\s*(\w+\s+\d{1,2},?\s*\d{4})",  # "Last Updated: Month Day, Year"
        r"[Uu]pdated\s*:?\s*(\d{1,2}/\d{1,2}/\d{2,4})",  # "Updated: MM/DD/YY"
        r"[Dd]ate\s*:?\s*(\d{1,2}/\d{1,2}/\d{2,4})",  # "Date: MM/DD/YY"
    ]
    
    # Try each pattern
    for pattern in date_patterns:
        date_match = re.search(pattern, page_content)
        if date_match:
            latest_date_str = date_match.group(1)
            print(f"Found date '{latest_date_str}' using pattern '{pattern}'")
            
            try:
                # Try multiple date formats
                date_formats = [
                    "%m/%d/%y",      # 05/04/23
                    "%m/%d/%Y",      # 05/04/2023
                    "%B %d, %Y",     # May 4, 2023
                    "%B %d %Y"       # May 4 2023
                ]
                
                parsed_date = None
                for date_format in date_formats:
                    try:
                        parsed_date = datetime.strptime(latest_date_str, date_format)
                        print(f"Successfully parsed date using format: {date_format}")
                        break
                    except ValueError:
                        continue
                
                if not parsed_date:
                    print(f"Could not parse date: {latest_date_str}")
                    continue
                
                # Get today's date and yesterday's date in Eastern Time (EST/EDT)
                eastern_tz = ZoneInfo("America/New_York")
                today = datetime.now(eastern_tz)
                yesterday = today - timedelta(days=1)
                two_days_ago = today - timedelta(days=2)  # Added more flexibility
                
                # Format dates for comparison (removing time)
                latest_date = parsed_date.replace(hour=0, minute=0, second=0, microsecond=0)
                today = today.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
                yesterday = yesterday.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
                two_days_ago = two_days_ago.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
                
                # Check if the latest date is within the acceptable range
                if latest_date >= two_days_ago:  # Allow up to 2 days ago for more flexibility
                    return True, f"Last updated date ({latest_date_str}) is current"
                else:
                    print(f"Date not current: {latest_date_str} vs today {today}")
                    return False, f"Last updated date ({latest_date_str}) is not current in {url}"
                    
            except Exception as e:
                print(f"Error parsing date: {e}")
                continue
    
    # If we have a URL with a specific known format but no date found, bypass the check
    # This is a temporary solution that you may want to make configurable
    if "bypass_date_check" in os.environ.get('WEBSITE_URL_FLAGS', '').lower() and url == os.environ.get('WEBSITE_URL'):
        print(f"WARNING: Bypassing date check for {url} as specified in WEBSITE_URL_FLAGS")
        return True, f"Date check bypassed for {url}"
    
    if "bypass_date_check" in os.environ.get('WEBSITE_URL2_FLAGS', '').lower() and url == os.environ.get('WEBSITE_URL_2'):
        print(f"WARNING: Bypassing date check for {url} as specified in WEBSITE_URL2_FLAGS")
        return True, f"Date check bypassed for {url}"
    
    # If we get here, no valid date was found with any pattern
    return False, f"Could not find date information in {url}. Try adding 'bypass_date_check' to the URL_FLAGS environment variable if needed."

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
    
    # Format today's date in Eastern Time
    eastern_tz = ZoneInfo("America/New_York")
    today = datetime.now(eastern_tz).strftime('%Y-%m-%d')
    
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

def main():
    try:
        # Get environment variables.
        url1 = os.environ.get('WEBSITE_URL')
        url2 = os.environ.get('WEBSITE_URL_2')
        gmail_address = os.environ.get('GMAIL_ADDRESS')
        app_password = os.environ.get('GMAIL_APP_PASSWORD')
        recipient_email = os.environ.get('RECIPIENT_EMAIL')
        
        # Use MOBILE default width of 400 (ignoring any environment-provided height).
        width = int(os.environ.get('SCREENSHOT_WIDTH', 400))
        
        # Generate filenames with current date (in Eastern Time).
        eastern_tz = ZoneInfo("America/New_York")
        today = datetime.now(eastern_tz).strftime('%Y-%m-%d')
        filename1 = f"screenshot1_{today}.png"
        filename2 = f"screenshot2_{today}.png"
        
        # Take screenshots and get page content
        print(f"Taking screenshot of {url1}...")
        content1 = take_screenshot(url1, filename1, width)
        
        # Check if the first page has a current date
        is_valid1, message1 = check_date_current(content1, url1)
        
        if not is_valid1:
            print(f"Date validation failed for URL1: {message1}")
            # Upload debug files as artifacts before exiting
            sys.exit(1)  # Exit with error code 1 to mark the GitHub Action as failed
        
        print(f"URL1 date validation passed: {message1}")
        
        print(f"Taking screenshot of {url2}...")
        content2 = take_screenshot(url2, filename2, width)
        
        # Check if the second page has a current date
        is_valid2, message2 = check_date_current(content2, url2)
        
        if not is_valid2:
            print(f"Date validation failed for URL2: {message2}")
            sys.exit(1)  # Exit with error code 1 to mark the GitHub Action as failed
        
        print(f"URL2 date validation passed: {message2}")
        
        # If both dates are current, send the email
        subject = f"Daily Dashboard Screenshots - {today}"
        print("Both validations passed, sending email...")
        success = send_email_with_embedded_images(
            gmail_address, app_password, recipient_email, subject, 
            url1, url2, filename1, filename2
        )
        
        if success:
            print("Screenshots taken and emailed successfully with embedded images!")
            sys.exit(0)  # Exit with success code 0
        else:
            print("Failed to send email.")
            sys.exit(1)  # Exit with error code 1 if email sending fails
    
    except Exception as e:
        print(f"An error occurred during execution: {e}")
        sys.exit(1)  # Exit with error code 1 for any unhandled exceptions

if __name__ == "__main__":
    main()
