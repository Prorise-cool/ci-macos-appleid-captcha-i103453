#!/usr/bin/env python3
"""redact credentials out of out10 before artifact upload (values never leave the runner)"""
import os, re, json, glob
OUT = os.path.dirname(os.path.abspath(__file__)) + '/out10'
secrets = []
try:
    d = json.load(open(os.path.join(OUT, 'summary10.json')))
    for k in ('password', 'email_addr', 'email_code', 'sms_code'):
        v = d.get(k)
        if isinstance(v, str) and len(v) >= 4: secrets.append(v)
    for e in d.get('events', []):
        if e.get('k') == 'email_chosen' and isinstance(e.get('v'), dict):
            a = e['v'].get('addr')
            if a: secrets.append(a)
except Exception as ex:
    print('summary read err', ex)
secrets = sorted(set(secrets), key=lambda s: -len(s))
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
def scrub(t):
    for s in secrets:
        t = t.replace(s, '<REDACTED>')
    t = EMAIL_RE.sub('<MAILBOX_REDACTED>', t)
    t = re.sub(r'("(?:password|sid_token|token|pw)"\s*:\s*")[^"]+(")', r'\1<REDACTED>\2', t)
    t = re.sub(r'(sid_token=)[A-Za-z0-9]+', r'\1<REDACTED>', t)
    return t
n = 0
for f in glob.glob(os.path.join(OUT, '**', '*'), recursive=True):
    if not os.path.isfile(f): continue
    if f.endswith(('.png', '.jpg', '.jpeg')): continue
    try:
        s = open(f, encoding='utf-8', errors='replace').read()
    except Exception:
        continue
    t = scrub(s)
    if t != s:
        open(f, 'w', encoding='utf-8').write(t); n += 1
print('redacted files:', n, 'secret_values:', len(secrets))
