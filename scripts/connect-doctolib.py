#!/usr/bin/env python3
"""Interactive one-time Doctolib login. No permanent local service or saved profile.

Run on your own machine. Password/2FA are entered only on Doctolib's own page. Only
Doctolib cookies are transferred over HTTPS to the fixed private ChangePoint Worker,
which checks the session and stores it encrypted. Nothing prints cookies or tokens.
"""
import getpass
import json
import os
import tempfile
import time
import urllib.request
import urllib.error
from playwright.sync_api import sync_playwright

ENDPOINT='https://doctolib-mcp-changepoint.changepoint-eu.workers.dev/setup/session'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise RuntimeError('Redirect rejected; credentials were not forwarded')

def main():
    token=os.environ.get('DOCTOLIB_MCP_TOKEN') or getpass.getpass('Persönlichen ChangePoint-MCP-Token eingeben: ')
    with tempfile.TemporaryDirectory(prefix='doctolib-login-') as profile:
        with sync_playwright() as p:
            context=p.chromium.launch_persistent_context(profile,headless=False)
            page=context.pages[0] if context.pages else context.new_page()
            page.goto('https://www.doctolib.de/account/appointments',wait_until='domcontentloaded')
            print('Bitte im Doctolib-Fenster anmelden und gegebenenfalls den Bestätigungscode eingeben.')
            input('Nach erfolgreicher Anmeldung hier Enter drücken: ')
            probe=context.request.get('https://www.doctolib.de/account/master_patients.json',max_redirects=0,timeout=20000)
            if probe.status!=200:
                context.close()
                raise RuntimeError('Anmeldung nicht bestätigt; Sitzung wurde nicht übertragen.')
            try:probe.json()
            except ValueError:
                context.close();raise RuntimeError('Ungültige Anmeldeantwort; Sitzung wurde nicht übertragen.')
            cookies=context.cookies('https://www.doctolib.de')
            cookie='; '.join(c['name']+'='+c['value'] for c in cookies if c.get('domain','').lstrip('.') in ('doctolib.de','www.doctolib.de'))
            context.close()
    if not cookie:raise RuntimeError('Keine gültige Sitzung gefunden.')
    request=urllib.request.Request(ENDPOINT,method='POST',data=json.dumps({'cookie':cookie}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','User-Agent':'changepoint-doctolib-connector/0.3'})
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=40) as response:
            result=json.loads(response.read(4096))
    except urllib.error.HTTPError as error:
        raise RuntimeError('Worker hat die Sitzung abgelehnt (HTTP '+str(error.code)+'). Keine automatische Wiederholung.') from None
    if not result.get('ok'):raise RuntimeError('Sitzung wurde nicht bestätigt.')
    print('Doctolib-Sitzung verschlüsselt im ChangePoint-Worker hinterlegt. Kein lokaler Dienst erforderlich.')

if __name__=='__main__':
    try:main()
    except (RuntimeError,KeyboardInterrupt) as error:
        print(str(error) if isinstance(error,RuntimeError) else 'Abgebrochen.')
        raise SystemExit(1)
