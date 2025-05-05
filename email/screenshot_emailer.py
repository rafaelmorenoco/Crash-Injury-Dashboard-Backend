# screenshot_emailer.py
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from datetime import datetime
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
        page.goto(url)
        page.wait_for_load_state("networkidle")
        
        # Take a full-page screenshot (the final height is determined automatically).
        page.screenshot(path=filename, full_page=True)
        
        browser.close()

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
    today = datetime.now().strftime('%Y-%m-%d')
    filename1 = f"screenshot1_{today}.png"
    filename2 = f"screenshot2_{today}.png"
    
    # Take screenshots in mobile mode.
    print(f"Taking screenshot of {url1}...")
    take_screenshot(url1, filename1, width)
    print(f"Taking screenshot of {url2}...")
    take_screenshot(url2, filename2, width)
    
    subject = f"Daily Dashboard Screenshots - {today}"
    success = send_email_with_embedded_images(
        gmail_address, app_password, recipient_email, subject, 
        url1, url2, filename1, filename2
    )
    
    if success:
        print("Screenshots taken and emailed successfully with embedded images!")
    else:
        print("Failed to send email.")

if __name__ == "__main__":
    main()
