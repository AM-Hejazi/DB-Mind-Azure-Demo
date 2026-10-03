"""Explicit authenticated hosted UI check; --mode live submits one paid question.

Run as a module with the optional Playwright test environment. Private credentials
are read in memory; browser requests are restricted to the selected HTTPS host.
"""
import argparse
from collections import Counter
import csv
import io
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect
from scripts.deploy_azure import private_environment
from src.access import load_access_settings
from synthetic_demo.generator import generate
from synthetic_demo.questions import reference_cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--mode', choices=['mock', 'live'], required=True)
    parser.add_argument('--env-file', type=Path, default=Path.home()/'dbmind-azure.env')
    parser.add_argument('--screenshots', type=Path, default=Path('docs/images'))
    args = parser.parse_args()
    origin = args.url.rstrip('/')
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or
            not parsed.hostname.endswith('.azurecontainerapps.io') or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            parsed.path or parsed.port):
        parser.error('Use the HTTPS Container Apps origin without credentials or a path')
    username, password = load_access_settings(private_environment(args.env_file)).users[0]
    expected = dict(sorted(Counter(row[5] for row in generate()['work_orders']).items()))
    errors, blocked = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(http_credentials={'username': username, 'password': password},
                                      permissions=['clipboard-read', 'clipboard-write'],
                                      viewport={'width': 1280, 'height': 1000})
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(type(error).__name__))
        def route(request):
            if request.request.url.startswith((origin+'/', 'data:', 'blob:')):
                request.continue_()
            else:
                blocked.append(urlsplit(request.request.url).hostname)
                request.abort()
        page.route('**/*', route)
        page.goto(origin+'/', wait_until='networkidle')
        print('Hosted HTML loaded', flush=True)
        page.get_by_text('Offline fixture responses (no LLM)' if args.mode == 'mock' else
                         'DeepSeek ·', exact=False).wait_for(timeout=20000)
        print('Hosted UI initialized', flush=True)
        logo = page.get_by_role('img', name='DB-Mind', exact=True)
        expect(logo).to_be_visible()
        page.wait_for_function("document.querySelector('.dbmind-header img')?.naturalWidth > 0")
        assert logo.get_attribute('src').startswith('data:image/png;base64,')
        dimensions = logo.evaluate('(i)=>({height:i.height,ratio:i.width/i.height,natural:i.naturalWidth/i.naturalHeight})')
        assert dimensions['height'] == 48 and abs(dimensions['ratio']-dimensions['natural']) < .03
        page.get_by_role('textbox', name='Question / Frage').fill(reference_cases()[0]['question_en'])
        page.get_by_role('button', name='Send', exact=True).click()
        print('Submitted one reference question', flush=True)
        # Verify actual rendered values, independent of model column aliases/order.
        followups = []
        for turn in range(3):
            status = page.get_by_text(re.compile('^(Result ready|Clarification needed|Operation stopped|Request stopped)$')).first
            status.wait_for(timeout=330000 if args.mode == 'live' else 45000)
            stage = status.inner_text()
            print('Pipeline state:', stage, flush=True)
            if stage == 'Result ready':
                break
            assert args.mode == 'live' and stage == 'Clarification needed' and turn < 2, stage
            message = ['Use all recorded dates without a date filter.', 'Yes, please start.'][turn]
            followups.append(message)
            page.get_by_role('textbox', name='Question / Frage').fill(message)
            page.get_by_role('button', name='Send', exact=True).click()
            page.get_by_text('Clarification needed', exact=True).wait_for(state='hidden', timeout=10000)
        # Gradio uses a virtualized grid, rather than a native HTML table.
        # Its own Copy action supplies the full rendered result for exact comparison.
        page.get_by_role('button', name='Copy table data', exact=True).click()
        page.get_by_role('button', name='Copied to clipboard', exact=True).wait_for(timeout=10000)
        copied = page.evaluate('navigator.clipboard.readText()')
        rows = list(csv.reader(io.StringIO(copied)))
        observed = {}
        for row in rows:
            statuses = [cell for cell in row if cell in expected]
            if statuses:
                numbers = [int(cell) for cell in row if cell.isdigit()]
                assert len(statuses) == len(numbers) == 1
                assert statuses[0] not in observed
                observed[statuses[0]] = numbers[0]
        assert observed == expected, observed
        page.get_by_text('Executed SQL / Ausgeführtes SQL', exact=True).click()
        args.screenshots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(args.screenshots/'demo-desktop.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.wait_for_function('document.documentElement.scrollWidth <= window.innerWidth', timeout=10000)
        assert logo.evaluate('(i)=>i.height') == 36
        page.screenshot(path=str(args.screenshots/'demo-mobile.png'), full_page=True)
        assert not errors and not blocked
        print(json.dumps({'mode': args.mode, 'questions_submitted': 1,
                          'clarification_followups': len(followups),
                          'status_counts_match': expected, 'javascript_errors': errors,
                          'blocked_third_party_requests': blocked, 'mobile_overflow': False,
                          'desktop_logo_height': 48, 'mobile_logo_height': 36}, indent=2))
        browser.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Avoid dumping driver/browser diagnostics or private HTTP headers.
        print('Hosted UI verification failed:', type(error).__name__)
        raise SystemExit(1)
