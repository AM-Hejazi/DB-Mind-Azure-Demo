from pathlib import Path
import json
import os
import argparse
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect
from synthetic_demo.questions import reference_cases
parser=argparse.ArgumentParser(description='Loopback mock UI verification; blocks third-party browser requests.')
parser.add_argument('--base-url',default='http://127.0.0.1:7860')
args=parser.parse_args()
if urlsplit(args.base_url).hostname not in {'127.0.0.1','localhost'}:parser.error('Use a loopback fixture preview')
username=os.environ['APP_USERNAME']
password=os.environ['APP_PASSWORD']
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True)
 context=browser.new_context(http_credentials={'username':username,'password':password},viewport={'width':1280,'height':1000})
 page=context.new_page();errors=[];requests=[]
 page.on('pageerror',lambda error:errors.append(str(error)))
 # No browser network leaves loopback; abort any third-party request.
 def route(r):
  if r.request.url.startswith((args.base_url.rstrip('/')+'/','data:','blob:')):r.continue_()
  else:requests.append(urlsplit(r.request.url).netloc);r.abort()
 page.route('**/*',route)
 page.goto(args.base_url+'/',wait_until='networkidle')
 page.get_by_text('Offline fixture responses (no LLM)',exact=False).wait_for(timeout=10000)
 logo=page.get_by_role('img',name='DB-Mind',exact=True)
 expect(logo).to_be_visible()
 page.wait_for_function("document.querySelector('.dbmind-header img')?.naturalWidth > 0")
 assert logo.get_attribute('src').startswith('data:image/png;base64,')
 desktop_logo=logo.evaluate('(img) => ({height: img.height, width: img.width, naturalWidth: img.naturalWidth, naturalHeight: img.naturalHeight})')
 assert desktop_logo['height']==48,desktop_logo
 assert abs(desktop_logo['width']/desktop_logo['height']-desktop_logo['naturalWidth']/desktop_logo['naturalHeight'])<.03,desktop_logo
 page.get_by_text(reference_cases()[0]['question_en'],exact=True).click()
 expect(page.get_by_role('textbox',name='Question / Frage')).to_have_value(reference_cases()[0]['question_en'])
 page.get_by_role('button',name='Send',exact=True).click()
 page.get_by_text('Offline fixture response: status_counts.',exact=False).wait_for(timeout=20000)
 page.get_by_text('Executed SQL / Ausgeführtes SQL',exact=True).click()
 page.screenshot(path='docs/images/demo-desktop.png',full_page=True)
 page.get_by_role('button',name='New question / Neue Frage',exact=True).click()
 page.get_by_text('Ready / Bereit',exact=True).wait_for(timeout=10000)
 page.get_by_role('textbox',name='Question / Frage').fill(reference_cases()[9]['question_de'])
 page.get_by_role('button',name='Send',exact=True).click()
 page.get_by_text('No matching rows',exact=False).wait_for(timeout=20000)
 page.set_viewport_size({'width':390,'height':844})
 page.wait_for_function('document.documentElement.scrollWidth <= window.innerWidth',timeout=10000)
 mobile_logo=logo.evaluate('(img) => ({height: img.height, width: img.width})')
 assert mobile_logo['height']==36,mobile_logo
 assert abs(mobile_logo['width']/mobile_logo['height']-desktop_logo['naturalWidth']/desktop_logo['naturalHeight'])<.03,mobile_logo
 page.screenshot(path='docs/images/demo-mobile.png',full_page=True)
 print(json.dumps({'javascript_errors':errors,'blocked_third_party_requests':requests,'mobile_overflow':page.evaluate('document.documentElement.scrollWidth > window.innerWidth'),'desktop_logo':desktop_logo,'mobile_logo':mobile_logo,'verified':['embedded trusted logo','responsive header','authenticated desktop fixture query','reset','German empty result','mobile layout','expandable SQL']},indent=2))
 assert not errors,errors
 assert not requests,requests
 browser.close()
