const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch({
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });
  const page = await browser.newPage();
  
  page.on('console', msg => console.log('PAGE LOG:', msg.text()));
  page.on('pageerror', error => console.error('PAGE ERROR:', error.message));
  page.on('requestfailed', request => console.error('REQUEST FAILED:', request.url(), request.failure()?.errorText || 'Unknown'));

  await page.goto('http://127.0.0.1:8000', { waitUntil: 'networkidle0' });
  
  const content = await page.content();
  console.log('CONTENT LENGTH:', content.length);
  console.log('BODY:', await page.evaluate(() => document.body.innerHTML));
  
  await browser.close();
})();
