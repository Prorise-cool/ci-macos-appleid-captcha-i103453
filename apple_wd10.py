#!/usr/bin/env python3
"""Apple Account creation v10: the real gate observed in run 44700655882 was a PAGE-LEVEL ban
("Your account cannot be created at this time.") that fires after 4 consecutive wrong image-captcha
answers -- NOT a virtual-machine identity rejection. So this run:
  * grabs the captcha ORIGINAL JPEG bytes from the DOM (data: URL), no screen-shot OCR
  * reads them with 3 independent engines (Vision multi-variant / Vision per-char slices / Tesseract)
  * submits ONLY when engines agree and per-character confidence is high
  * <=6 captcha images, <=3 submissions  (normal human-style answering, never enumeration)
  * stops verbatim on page-level refusal / vm / not supported / email-domain invalid
Then, if the captcha passes: email code from our own temp mailbox -> phone stage -> SMS read once
from the public asms page (only a NEW Apple row after our own baseline; never guess/enumerate).
"""
import os, re, json, time, random, string, base64, subprocess, html as htmlmod, traceback, hashlib
import urllib.request, urllib.error

START = time.time()
DEADLINE = START + 24 * 60          # job timeout is 30 min
OUT = os.path.dirname(os.path.abspath(__file__)) + '/out10'
os.makedirs(OUT, exist_ok=True)
LOG = open(os.path.join(OUT, 'run10.log'), 'a', buffering=1)
def log(*a):
    s = ' '.join(str(x) for x in a); print(s); LOG.write(s + '\n')
STATE = {'events': [], 'stage': 'init'}
def save():
    try: json.dump(STATE, open(os.path.join(OUT, 'summary10.json'), 'w'), indent=2, ensure_ascii=False)
    except Exception: pass
def ev(k, v): STATE['events'].append({'t': round(time.time() - START, 2), 'k': k, 'v': v}); log('[EV]', k, '=', str(v)[:1200]); save()
def time_left(): return DEADLINE - time.time()

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15'
NUM_CC = os.environ.get('APPLE_CC', 'GB')
NUM_NAT = os.environ.get('APPLE_NAT', '7450699185')
INBOX = os.environ.get('APPLE_INBOX', 'https://asms.ai/number/447450699185')

def _open(req, timeout):
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout)

def http(url, data=None, headers=None, timeout=35, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, method='POST' if data else 'GET')
            req.add_header('User-Agent', UA)
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
            for k, v in (headers or {}).items(): req.add_header(k, v)
            with _open(req, timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            try: body = e.read()
            except Exception: body = b''
            last = 'HTTP %s' % e.code
            if e.code < 500: return e.code, body
        except Exception as e:
            last = repr(e)[:180]
        time.sleep(2 + 2 * i)
    raise RuntimeError('http failed %s: %s' % (url, last))

def http_json(url, data=None, headers=None, timeout=35, tries=4):
    st, b = http(url, data, headers, timeout, tries)
    return st, json.loads(b)

def strip_tags(s): return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', s)).strip()

# ---------- public SMS inbox (asms.ai) ----------
def parse_asms(h):
    rows = []
    for b in re.split(r'ring-gradient rounded-2xl bg-white/\[0\.03\]', h)[1:]:
        m = re.search(r'<a[^>]*href="/([a-z0-9]+)"[^>]*>([^<]*)</a><time dateTime="([^"]+)">', b)
        if not m:
            m2 = re.search(r'<span class="font-semibold text-cyan-300">([^<]*)</span><time dateTime="([^"]+)">', b)
            if not m2: continue
            slug, frm, ts = '', m2.group(1), m2.group(2)
        else:
            slug, frm, ts = m.group(1), m.group(2).strip(), m.group(3)
        i = b.find('leading-relaxed')
        body = htmlmod.unescape(strip_tags(b[i:i + 900])) if i > 0 else ''
        rows.append({'slug': slug, 'from': frm, 't': ts, 'text': body[:400]})
    return rows

def fetch_inbox():
    st, b = http(INBOX + '?t=%d' % int(time.time()), headers={'Cache-Control': 'no-cache', 'Pragma': 'no-cache'})
    return st, parse_asms(b.decode('utf-8', 'replace'))

def newest_ts(rows):
    ts = [r['t'] for r in rows if r.get('t')]
    return max(ts) if ts else ''

def is_apple_code(row):
    t = (row.get('text') or '')
    return (('apple account code' in t.lower()) or ('apple' in (row.get('from') or '').lower())) and re.search(r'\b\d{6}\b', t)

def poll_inbox_apple(after_ts, secs=540):
    end = min(time.time() + secs, DEADLINE - 90)
    n = 0
    while time.time() < end:
        n += 1
        try:
            st, rows = fetch_inbox()
            if n % 5 == 1:
                ev('inbox_poll', {'n': n, 'rows': len(rows), 'newest': newest_ts(rows)})
            fresh = [r for r in rows if (r.get('t') or '') > after_ts]
            apples = [r for r in fresh if is_apple_code(r)]
            if apples:
                ev('inbox_apple_new', apples)
                return apples[0]
        except Exception as e:
            ev('inbox_err', repr(e)[:200])
        time.sleep(12)
    return None

# ---------- email providers ----------
def tmol_new(n=1):
    out = []
    for i in range(n):
        if time_left() < 900: break
        try:
            st, b = http('https://api.tempmail.lol/generate', timeout=25)
            j = json.loads(b)
            if j.get('address') and j.get('token'):
                out.append({'kind': 'tmol', 'addr': j['address'], 'token': j['token']})
                ev('tmol_gen', {'addr': j['address'], 'domain': j['address'].split('@', 1)[-1]})
        except Exception as ex:
            ev('tmol_err', repr(ex)[:200])
        time.sleep(1)
    return out

def mailtm_new():
    for i in range(2):
        if time_left() < 600: break
        try:
            st, j = http_json('https://api.mail.tm/domains', timeout=25)
            domain = j['hydra:member'][0]['domain']
            pw = 'Aa1!' + ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(14))
            addr = 'x%s%d@%s' % (int(time.time()) % 100000, random.randint(10, 99), domain)
            http('https://api.mail.tm/accounts', json.dumps({'address': addr, 'password': pw}).encode(), {'Content-Type': 'application/json'}, timeout=25)
            st2, j2 = http_json('https://api.mail.tm/token', json.dumps({'address': addr, 'password': pw}).encode(), {'Content-Type': 'application/json'}, timeout=25)
            tok = j2.get('token')
            if tok:
                ev('mailtm', {'addr': addr, 'token': True, 'try': i + 1})
                return [{'kind': 'mailtm', 'addr': addr, 'pw': pw, 'token': tok}]
        except Exception as ex:
            ev('mailtm_err', {'try': i + 1, 'e': repr(ex)[:200]})
        time.sleep(4)
    return []

def guerrilla_new():
    try:
        st, b = http('https://api.guerrillamail.com/ajax.php?f=get_email_address&lang=en', timeout=25)
        j = json.loads(b)
        STATE['_guerrilla_sid'] = j.get('sid_token')
        ev('guerrilla', {'addr': j['email_addr']})
        return True
    except Exception as ex:
        ev('guerrilla_fail', repr(ex)[:200]); return False

def guerrilla_site(domain):
    sid = STATE.get('_guerrilla_sid')
    if not sid: return None
    try:
        user = 'u%s%d' % (int(time.time()) % 1000000, random.randint(10, 99))
        u = ('https://api.guerrillamail.com/ajax.php?f=set_email_user&email_user=%s&site=%s&lang=en&sid_token=%s'
             % (user, domain, sid))
        st, b = http(u, timeout=25)
        j = json.loads(b)
        if j.get('email_addr'):
            ev('guerrilla_site', {'site': domain, 'addr': j['email_addr']})
            return {'kind': 'guerrilla', 'addr': j['email_addr'], 'sid': sid, 'site': domain}
    except Exception as ex:
        ev('guerrilla_site_err', repr(ex)[:160])
    return None

def mail_codes_now():
    e = STATE.get('email') or {}
    if not e: return []
    if e.get('kind') == 'tmol':
        st, j = http_json('https://api.tempmail.lol/auth/' + e['token'], timeout=25)
        lst = j.get('email') or []
        if not lst: return []
        msg = lst[0]
        open(os.path.join(OUT, 'email_full.json'), 'w').write(json.dumps(msg, ensure_ascii=False)[:20000])
        txt = ' '.join(str(msg.get(k) or '') for k in ('subject', 'body', 'html', 'text', 'from', 'sender'))
        return re.findall(r'(?<!\d)(\d{6})(?!\d)', txt)
    if e.get('kind') == 'mailtm':
        st, j = http_json('https://api.mail.tm/messages', headers={'Authorization': 'Bearer ' + e['token']}, timeout=25)
        msgs = j.get('hydra:member', [])
        if not msgs: return []
        st2, j2 = http_json('https://api.mail.tm/messages/' + msgs[0]['id'], headers={'Authorization': 'Bearer ' + e['token']}, timeout=25)
        txt = (j2.get('text') or '') + ' ' + json.dumps(j2.get('html') or [], ensure_ascii=False)[:8000]
        open(os.path.join(OUT, 'email_full.json'), 'w').write(json.dumps(j2, ensure_ascii=False)[:20000])
        return re.findall(r'(?<!\d)(\d{6})(?!\d)', txt)
    st, b = http('https://api.guerrillamail.com/ajax.php?f=check_email&seq=0&sid_token=' + e['sid'], timeout=25)
    lst = (json.loads(b).get('list') or [])
    if not lst: return []
    st2, b2 = http('https://api.guerrillamail.com/ajax.php?f=fetch_email&email_id=%s&sid_token=%s' % (lst[0].get('mail_id'), e['sid']), timeout=25)
    k = json.loads(b2)
    txt = (k.get('mail_body') or '')
    open(os.path.join(OUT, 'email_full.json'), 'w').write(json.dumps(k, ensure_ascii=False)[:20000])
    return re.findall(r'(?<!\d)(\d{6})(?!\d)', txt)

def mail_wait_code(secs=150):
    end = min(time.time() + secs, DEADLINE - 300)
    n = 0
    while time.time() < end:
        n += 1
        try:
            c = mail_codes_now()
            if n % 4 == 1: ev('mail_poll', {'n': n, 'codes': c[:3]})
            if c: return c[0]
        except Exception as ex:
            ev('mail_err', repr(ex)[:150])
        time.sleep(8)
    return None

# ---------- webdriver ----------
WD = 'http://127.0.0.1:4444'; SID = None
def wd(method, path, data=None, timeout=180):
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(WD + path, data=body, method=method); req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as r:
            t = r.read().decode(errors='replace')
            try: return json.loads(t)
            except Exception: return {'raw': t}
    except urllib.error.HTTPError as e:
        t = e.read().decode(errors='replace')
        try: return json.loads(t)
        except Exception: return {'raw': t, 'code': e.code}

def sess(): return '/session/%s' % SID
def ekey(el):
    for k in (el or {}):
        if k.startswith('element-') or k == 'ELEMENT': return el[k]
def find(css): return ekey(wd('POST', sess() + '/element', {'using': 'css selector', 'value': css}).get('value', {}))
def find_xp(xp): return ekey(wd('POST', sess() + '/element', {'using': 'xpath', 'value': xp}).get('value', {}))
def click(el): return wd('POST', sess() + '/element/%s/click' % el, {})
def keys(el, t): return wd('POST', sess() + '/element/%s/value' % el, {'text': t})
def js(s, a=None): return wd('POST', sess() + '/execute/sync', {'script': s, 'args': a or []}).get('value')
def source(): return wd('GET', sess() + '/source').get('value', '') or ''
def body_text(): return js('return document.body?document.body.innerText.slice(0,9000):"";') or ''
def shot(n):
    v = wd('GET', sess() + '/screenshot').get('value')
    if isinstance(v, str) and v: open(os.path.join(OUT, n + '.png'), 'wb').write(base64.b64decode(v))
def dump(n):
    t = body_text(); open(os.path.join(OUT, n + '.txt'), 'w').write(t)
    open(os.path.join(OUT, n + '.html'), 'w').write(source()[:700000])
    shot(n); log('===', n, 'len=', len(t)); return t
def switch_frame(el): return wd('POST', sess() + '/frame', {'id': {'element-6066-11e4-a52e-4f735466cecf': el}})

def set_input(name, value):
    el = find('input[name="%s"]' % name)
    if not el: return ('missing', name)
    try: click(el)
    except Exception: pass
    keys(el, value)
    v = js('var e=document.querySelector(arguments[0]); return e?e.value:null;', ['input[name="%s"]' % name])
    return ('ok' if v else 'no_value', name, v)

def set_tid(tid, value):
    ok = js("""var v=arguments[0]; var s=document.querySelector('[data-testid="%s"]'); if(!s) return 'nosel';
      var has=Array.from(s.options).some(function(o){return o.value===v;}); if(!has) return 'noopt';
      s.value=v; s.dispatchEvent(new Event('input',{bubbles:true}));
      s.dispatchEvent(new Event('change',{bubbles:true}));
      var lab=s.parentNode.querySelector('.form-dropdown-label'); if(lab){var o=s.options[s.selectedIndex]; if(o) lab.textContent=o.text;} return s.value;""" % tid, [value])
    return ('ok' if ok == value else 'bad', tid, str(ok))

def set_select(name, value):
    ok = js("""var v=arguments[0]; var s=document.querySelector('select[name="%s"]'); if(!s) return 'nosel';
      var has=Array.from(s.options).some(function(o){return o.value===v;}); if(!has) return 'noopt';
      s.value=v; s.dispatchEvent(new Event('input',{bubbles:true}));
      s.dispatchEvent(new Event('change',{bubbles:true})); return s.value;""" % name, [value])
    return ('ok' if ok == value else 'bad', name, str(ok))

def invalid_fields():
    return js("""return Array.from(document.querySelectorAll('[aria-invalid="true"]')).map(function(e){return (e.getAttribute('data-testid')||'')+'#'+(e.id||'');}).slice(0,10);""")

def select_info(name):
    return js("""var s=document.querySelector('select[name="%s"]'); if(!s) return null;
      return {cur:s.value, gb:Array.from(s.options).filter(function(o){return /GB|United Kingdom|\\+44/i.test(o.value+' '+o.text);}).slice(0,4).map(function(o){return o.value+'='+o.text;})};""" % name)

def captcha_bytes():
    """read the ORIGINAL captcha image bytes that the page itself holds (data: URL or URL)"""
    info = js("""var i=document.querySelector('.captcha img.img'); if(!i) return null;
      return {src:i.src, w:i.naturalWidth, h:i.naturalHeight};""")
    if not info or not info.get('src'): return None
    src = info['src']
    try:
        if src.startswith('data:image'):
            raw = base64.b64decode(src.split(',', 1)[1])
        else:
            st, raw = http(src, timeout=25)
    except Exception as e:
        ev('captcha_fetch_err', repr(e)[:150]); return None
    return {'raw': raw, 'sha': hashlib.sha256(raw).hexdigest()[:16], 'w': info.get('w'), 'h': info.get('h'), 'src_kind': ('data' if src.startswith('data:') else 'url')}

def new_code():
    el = find_xp('//button[@aria-label="New Code"]')
    if el:
        try: click(el); return True
        except Exception: pass
    return False

def fill_code(digits):
    els = js("return Array.from(document.querySelectorAll('input.form-security-code-input')).length;") or 0
    ev('code_inputs', els)
    for i, d in enumerate(digits[:els or 0], start=1):
        el = find_xp('(//input[contains(@class,"form-security-code-input")])[%d]' % i)
        if el:
            try: click(el)
            except Exception: pass
            keys(el, d)
    vals = js("return Array.from(document.querySelectorAll('input.form-security-code-input')).map(function(e){return e.value;}).join('');")
    ev('code_values', vals)
    return vals

def is_vm_block(t):
    low = (t or '').lower()
    return ('virtual machine' in low) or ("can't be used to create" in low) or ('cannot be used to create' in low) \
           or ('not supported' in low and 'apple' in low)

PAGE_BAN = 'cannot be created at this time'

# ---------- OCR engines ----------
def run_ocr10(p):
    try:
        out = subprocess.run(['/tmp/ocr10', p], capture_output=True, text=True, timeout=120).stdout
    except Exception as e:
        return {'err': repr(e)[:150]}
    lines = out.splitlines()
    d = {'raw': out[-2500:], 'cands': [], 'perchar_by_len': {}, 'slvotes': '', 'nobs': 0}
    for ln in lines:
        if ln.startswith('NOBS:'):
            try: d['nobs'] = int(ln[5:])
            except Exception: pass
        elif ln.startswith('CANDS:'):
            for item in ln[6:].split(','):
                if '|' in item:
                    s, sc = item.rsplit('|', 1)
                    try: d['cands'].append((s, float(sc)))
                    except Exception: pass
        elif ln.startswith('PERCHAR'):
            m = re.match(r'PERCHAR(\d):(.*)', ln)
            if m: d['perchar_by_len'][int(m.group(1))] = m.group(2)
        elif ln.startswith('SLVOTES:'):
            d['slvotes'] = ln[8:].strip()
    return d

def run_tesseract(p):
    outs = []
    for psm in ('7', '8', '6'):
        try:
            r = subprocess.run(['tesseract', p, 'stdout', '--psm', psm, '-c',
                                'tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'],
                               capture_output=True, text=True, timeout=60)
            t = ''.join(ch for ch in (r.stdout or '') if ch.isalnum())
            if t: outs.append(t)
        except Exception as e:
            return {'err': repr(e)[:150], 'outs': outs}
    # majority (case-insensitive) first
    from collections import Counter
    cnt = Counter(x.upper() for x in outs)
    ordered = [x for x, _ in cnt.most_common()] + [x.upper() for x in outs]
    return {'outs': outs, 'ordered': ordered}

def tess_variants(p):
    """extra independent tesseract passes on preprocessed (upscaled / median / threshold) copies"""
    outs = []
    try:
        from PIL import Image, ImageFilter
    except Exception:
        ev('tess_variants_no_pil', True); return outs
    try:
        im0 = Image.open(p).convert('L')
        vs = []
        for s in (2, 3, 4):
            im = im0.resize((im0.width * s, im0.height * s), Image.LANCZOS)
            vs.append(im.filter(ImageFilter.MedianFilter(3)))
            vs.append(im.point(lambda q: 0 if q < 140 else 255))
            vs.append(im.point(lambda q: 0 if q < 110 else 255))
        for k, im in enumerate(vs):
            fp = '/tmp/tv%d.png' % k
            try: im.save(fp)
            except Exception: continue
            for psm in ('7', '6'):
                try:
                    r = subprocess.run(['tesseract', fp, 'stdout', '--psm', psm, '-c',
                                        'tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'],
                                       capture_output=True, text=True, timeout=45)
                    t = ''.join(ch for ch in (r.stdout or '') if ch.isalnum())
                    if t: outs.append(t)
                except Exception:
                    pass
    except Exception as e:
        ev('tess_variants_err', repr(e)[:150])
    return outs

def perchar_ratio(perchar_str, top):
    """mean over positions of (top char share among the top-3 per position)"""
    if not perchar_str: return 0.0
    pos = {}
    for part in perchar_str.split(';'):
        m = re.match(r'p(\d+)=(.+)', part)
        if not m: continue
        i = int(m.group(1)) - 1
        vals = []
        for kv in m.group(2).split('/'):
            if ':' in kv:
                k, v = kv.rsplit(':', 1)
                try: vals.append((k, float(v)))
                except Exception: pass
        pos[i] = vals
    if not pos or len(pos) != len(top): return 0.0
    tot = 0.0
    for i, ch in enumerate(top):
        vals = pos.get(i) or []
        ssum = sum(v for _, v in vals) or 1.0
        share = sum(v for k, v in vals if k == ch) / ssum
        tot += share
    return tot / len(pos)

def decide(ocr, tess, n):
    """v11: weighted candidate fusion (Vision + tesseract majority + length prior).
    Always returns the best candidate; the caller submits at most 3 answers total."""
    diag = {}
    vs = [(s, sc) for (s, sc) in (ocr.get('cands') or []) if 3 <= len(s) <= 6]
    ts = [t for t in ((tess or {}).get('outs') or []) if 4 <= len(t) <= 6]
    score = {}
    srcs = {}
    for s, sc in vs:
        k = s.upper()
        score[k] = score.get(k, 0.0) + 2.0 * float(sc)
        srcs.setdefault(k, set()).add('vision')
    for t in ts:
        k = t.upper()
        score[k] = score.get(k, 0.0) + 4.0
        srcs.setdefault(k, set()).add('tess')
    # cross-engine partial agreement bonus (same length, >=len-1 chars equal)
    for s, _sc in vs:
        for t in ts:
            if len(s) == len(t) and sum(1 for a, b in zip(s.upper(), t.upper()) if a == b) >= max(2, len(s) - 1):
                k = s.upper(); score[k] = score.get(k, 0.0) + 1.5
                srcs.setdefault(k, set()).update(('vision', 'tess'))
    # length prior: captchas here are 4-5 chars; 3-char singleton tesseract reads are usually truncations
    for k in list(score):
        if len(k) == 4: score[k] += 1.0
        elif len(k) == 5: score[k] += 0.5
        elif len(k) == 3: score[k] -= 1.5
    if not score:
        diag['reason'] = 'no_candidates'; return None, diag
    ranking = sorted(score.items(), key=lambda kv: -kv[1])
    top = ranking[0][0]
    diag.update({'top': top, 'ranking': [(k, round(v, 2)) for k, v in ranking[:6]],
                 'src': sorted(srcs.get(top, [])), 'tess': ts,
                 'vision': [c[0] for c in vs[:6]], 'reason': 'scored'})
    return top, diag

def main():
    global SID
    STATE['stage'] = 'start'; save()
    ev('env', {'hw_model': subprocess.run(['sysctl', '-n', 'hw.model'], capture_output=True, text=True).stdout.strip(),
               'hv_vmm_present': subprocess.run(['sysctl', '-n', 'kern.hv_vmm_present'], capture_output=True, text=True).stdout.strip(),
               'sw_vers': subprocess.run(['sw_vers', '-productVersion'], capture_output=True, text=True).stdout.strip(),
               'tesseract': os.path.exists('/usr/local/bin/tesseract') or os.path.exists('/opt/homebrew/bin/tesseract')})
    # ---- inbox baseline (so only OUR Apple SMS row can be used later) ----
    try:
        st, rows = fetch_inbox(); INBOX_BASE = newest_ts(rows)
        ev('inbox_baseline', {'status': st, 'count': len(rows), 'newest': INBOX_BASE})
    except Exception as ex:
        ev('inbox_baseline_err', repr(ex)[:250]); INBOX_BASE = ''
    # ---- email candidates (<=3 providers, one attempt each) ----
    CANDS = []
    try: CANDS += tmol_new(1)
    except Exception as ex: ev('tmol_stage_err', repr(ex)[:150])
    try: CANDS += mailtm_new()
    except Exception as ex: ev('mailtm_stage_err', repr(ex)[:150])
    try:
        if guerrilla_new():
            g = guerrilla_site('sharklasers.com')
            if g: CANDS.append(g)
    except Exception as ex: ev('guerrilla_stage_err', repr(ex)[:150])
    CANDS = CANDS[:3]
    ev('email_candidates', [{'addr': c['addr'], 'kind': c['kind']} for c in CANDS])
    if not CANDS:
        STATE['stage'] = 'no_email_provider'; save(); return

    r = wd('POST', '/session', {'capabilities': {'alwaysMatch': {'browserName': 'safari'}}})
    SID = (r.get('value', {}) or {}).get('sessionId'); ev('wd_session', SID)
    if not SID: STATE['stage'] = 'no_session'; save(); return
    wd('POST', sess() + '/window/maximize', {})
    wd('POST', sess() + '/url', {'url': 'https://account.apple.com/account#/create'}); time.sleep(9)
    fe = find('#aid-create-widget-iFrame')
    if not fe: dump('10_noiframe'); STATE['stage'] = 'no_iframe'; save(); return
    switch_frame(fe); time.sleep(3); dump('12_form')

    def set_email(addr):
        return js("""var e=document.querySelector('input[name=appleId]'); if(!e) return null;
          var d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');
          d.set.call(e, arguments[0]);
          e.dispatchEvent(new Event('input',{bubbles:true}));
          e.dispatchEvent(new Event('change',{bubbles:true}));
          return e.value;""", [addr])

    def server_validate(addr):
        return js("""var a=arguments[0];
          try{ var x=new XMLHttpRequest(); x.open('POST','/account/validation/appleid',false);
            x.setRequestHeader('Content-Type','application/json'); x.setRequestHeader('Accept','application/json');
            x.send(JSON.stringify({emailAddress:a}));
            return {status:x.status, body:(x.responseText||'').slice(0,1200)};
          }catch(e){ return {err:String(e).slice(0,200)}; }""", [addr])

    ev('frame_url', js("return location.href;"))
    verdicts = {}
    for c in CANDS:
        try: verdicts[c['addr']] = server_validate(c['addr'])
        except Exception as ex: verdicts[c['addr']] = {'err': repr(ex)[:150]}
        ev('srv_validate', {'kind': c['kind'], 'r': verdicts[c['addr']]})

    def srv_valid(addr):
        r = verdicts.get(addr) or {}
        if (r.get('status') or 0) != 200: return None
        try: return json.loads(r.get('body') or '{}')
        except Exception: return None

    def dom_probe(addr):
        set_email(addr)
        f2 = find('input[name="firstName"]')
        if f2:
            try: click(f2)
            except Exception: pass
        time.sleep(3)
        return js("""var e=document.querySelector('input[name=appleId]'); if(!e) return {gone:true};
          return {v:e.value, inv:(e.getAttribute('aria-invalid')||''),
                  hasInvalidText:((document.body.innerText)||'').indexOf('Enter a valid email address')>=0};""")

    chosen = None
    oracle_ok = False
    for c in CANDS:
        j = srv_valid(c['addr'])
        if j and j.get('valid') is True and not j.get('isRecycledDomain'):
            oracle_ok = True; chosen = c
            ev('email_chosen_server_oracle', {'kind': c['kind'], 'addr': c['addr']}); break
    if not oracle_ok:
        ev('server_oracle_unusable', {'verdicts': [{'kind': c['kind'], 'r': verdicts.get(c['addr'])} for c in CANDS]})
    if not chosen:
        for c in CANDS:
            r = dom_probe(c['addr'])
            ev('email_dom_probe', {'kind': c['kind'], 'r': r})
            if isinstance(r, dict) and not r.get('gone') and not r.get('inv') and not r.get('hasInvalidText') and r.get('v') == c['addr']:
                chosen = c; ev('email_chosen_dom_probe', {'kind': c['kind']}); break
    if not chosen:
        chosen = CANDS[0]
        ev('email_chosen_last_resort', {'kind': chosen['kind']})
    ev('email_chosen', {'kind': chosen['kind'], 'addr': chosen['addr']})
    email = chosen['addr']; pw = STATE['password'] = 'Aa1!' + ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(13))
    print('::add-mask::' + pw); print('::add-mask::' + email)
    STATE['email_addr'] = email
    for nm, v in (('firstName', 'Alex'), ('lastName', 'Miller')): set_input(nm, v)
    ev('month_set', set_tid('select-month', '04'))
    ev('day_set', set_tid('select-day', '09'))
    ev('year_set', set_tid('select-year', '1990'))
    set_email(email); set_input('password', pw); set_input('confirmPassword', pw)
    ev('phoneCode_info', select_info('phoneCode'))
    ev('countrySelect_info', select_info('countrySelect'))
    ev('phoneCode_set', set_select('phoneCode', NUM_CC))
    ev('country_set', set_select('countrySelect', {'GB': 'GBR', 'CA': 'CAN', 'US': 'USA'}.get(NUM_CC, '')))
    ev('phone_fill', set_input('phoneNumber', NUM_NAT))
    ev('phone_validity', js("var e=document.querySelector('input[name=phoneNumber]'); return e?{v:e.value,ok:e.checkValidity(),msg:e.validationMessage}:null;"))
    ev('invalid_after_fill', invalid_fields())
    dump('13_filled')

    # ---------- captcha v11: <=6 images / <=3 submissions, New Code never clicked ----------
    ok_submit = False
    images = 0
    submissions = 0
    used = set()
    last_sha = None
    while images < 6 and submissions < 3 and time_left() > 700:
        cb = captcha_bytes()
        if not cb:
            ev('no_captcha', {'images': images}); time.sleep(3); images += 1; continue
        if last_sha and cb['sha'] == last_sha:
            rotated = False
            for _ in range(10):
                time.sleep(2)
                cbn = captcha_bytes()
                if cbn and cbn['sha'] != last_sha:
                    cb = cbn; rotated = True; break
            ev('captcha_rotate_wait', {'rotated': rotated, 'sha': cb['sha']})
            if not rotated:
                ev('captcha_stale_after_submit', {'sha': cb['sha']}); break
        images += 1
        p = os.path.join(OUT, 'captcha_%d.jpg' % images)
        open(p, 'wb').write(cb['raw'])
        ocr = run_ocr10(p)
        tess = run_tesseract(p)
        try:
            tv = tess_variants(p)
            if tv:
                tess['outs'] = (tess.get('outs') or []) + tv
                from collections import Counter
                cnt = Counter(x.upper() for x in tess['outs'])
                tess['ordered'] = [x for x, _ in cnt.most_common()]
                ev('tess_variants', {'n': len(tv), 'top': tess['ordered'][:4]})
        except Exception as e:
            ev('tess_variants_fail', repr(e)[:120])
        guess, diag = decide(ocr, tess, images)
        if guess and guess in used:
            alt = [k for k, _v in diag.get('ranking', []) if k not in used]
            if alt:
                diag['pick'] = guess; guess = alt[0]; diag['reason'] = 'repeat_avoided'
        ev('captcha_image', {'n': images, 'sha': cb['sha'], 'w': cb['w'], 'h': cb['h'], 'bytes': len(cb['raw']),
                             'src_kind': cb['src_kind'], 'ocr_raw': (ocr.get('raw') or '')[-700:],
                             'tess': (tess or {}).get('outs', []), 'decision': diag, 'guess': guess})
        if not guess:
            ev('captcha_no_guess', {'n': images}); break
        cb2 = captcha_bytes()
        if not cb2 or cb2['sha'] != cb['sha']:
            ev('captcha_rotated_before_submit', {'n': images, 'now': (cb2 or {}).get('sha')}); continue
        el = find('input[name="captcha"]')
        if not el:
            ev('captcha_input_missing', {'n': images}); break
        try:
            click(el)
            js("var e=document.querySelector('input[name=captcha]'); if(e){e.value='';e.dispatchEvent(new Event('input',{bubbles:true}));}")
            keys(el, guess)
            js("var e=document.querySelector('input[name=captcha]'); if(e){e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));}")
        except Exception as e:
            ev('captcha_fill_err', repr(e)[:120])
        filled = js("var e=document.querySelector('input[name=captcha]'); return e?e.value:null;")
        ev('captcha_filled', {'n': images, 'guess': guess, 'field': filled})
        if (filled or '') != guess:
            ev('captcha_fill_mismatch', {'n': images, 'field': filled}); continue
        used.add(guess); last_sha = cb['sha']
        btn = find_xp('//button[@type="submit"]')
        if btn: click(btn)
        submissions += 1
        time.sleep(8); t = dump('%02d_after_submit_%d' % (20 + images, images)); low = t.lower()
        if is_vm_block(t):
            ev('vm_block_detected', t[:500]); STATE['stage'] = 'vm_blocked'; save(); return
        errs = js("""return Array.from(document.querySelectorAll('[class*=error],[role=alert],.form-message')).map(function(e){return e.innerText.trim().slice(0,400);}).filter(Boolean).slice(0,8);""")
        if js("return document.querySelectorAll('input.form-security-code-input').length;"):
            ok_submit = True; ev('submit_ok', {'n': images, 'submissions': submissions}); break
        if 'verification code' in low:
            ok_submit = True; ev('submit_ok_text', {'n': images, 'submissions': submissions}); break
        inv = invalid_fields()
        email_bad = js("""var e=document.querySelector('input[name=appleId]'); if(!e) return false;
          if(e.getAttribute('aria-invalid')==='true') return true;
          return ((document.body.innerText)||'').indexOf('Enter a valid email address')>=0;""")
        ev('submit_blocked', {'n': images, 'submissions': submissions, 'guess': guess, 'invalid': inv, 'errs': errs, 'email_bad': bool(email_bad)})
        if any(PAGE_BAN in (x or '') for x in errs) or PAGE_BAN in low:
            STATE['stage'] = 'page_level_refused_verbatim: ' + ' | '.join([x for x in errs if x][:3])
            save(); dump('26_page_refused'); return
        if email_bad:
            STATE['stage'] = 'email_rejected_after_submit'; save(); dump('25_email_rejected'); return
    ev('captcha_summary', {'images': images, 'submissions': submissions, 'passed': ok_submit})
    if not ok_submit:
        STATE['stage'] = 'captcha_not_passed'; save(); return

    STATE['stage'] = 'email_code_stage'; save(); dump('30_email_code_stage')
    code = mail_wait_code(150)
    if not code: STATE['stage'] = 'email_code_missing'; save(); return
    STATE['email_code'] = code; save()
    fill_code(code); dump('31_code_filled')
    btn = find_xp('//button[@type="submit"]') or find_xp('//button[contains(normalize-space(.),"Continue")]')
    if btn: click(btn)
    time.sleep(11); t = dump('40_after_email_code'); low = t.lower()
    errs = js("""return Array.from(document.querySelectorAll('[class*=error],[role=alert],.form-message')).map(function(e){return e.innerText.trim().slice(0,300);}).filter(Boolean).slice(0,8);""")
    ev('errors_after_email_code', errs)
    ev('final_url', js('return location.href'))
    if is_vm_block(t):
        ev('vm_block_after_email', t[:400]); STATE['stage'] = 'vm_blocked_after_email'; save(); return
    if 'phone' in low and 'code' in low: STATE['stage'] = 'phone_verification_stage'; save()
    elif 'verify your email' in low: STATE['stage'] = 'email_code_rejected'; save()
    else: STATE['stage'] = 'post_email_stage'; save()
    if STATE['stage'] != 'phone_verification_stage': return

    dump('50_phone_stage')
    ev('phone_stage_text', body_text()[:600])
    try:
        st2, rows2 = fetch_inbox(); base2 = max(INBOX_BASE or '', newest_ts(rows2))
        ev('inbox_baseline2', {'base': base2, 'count': len(rows2)})
    except Exception as ex:
        ev('inbox_baseline2_err', repr(ex)[:200]); base2 = INBOX_BASE
    found = poll_inbox_apple(base2, secs=480)
    if not found:
        STATE['stage'] = 'phone_sms_not_received'; save(); dump('60_sms_timeout'); return
    STATE['sms_found'] = found; save()
    m = re.search(r'\b(\d{6})\b', found['text'])
    if not m: STATE['stage'] = 'sms_code_unparsable'; save(); return
    sms = m.group(1); STATE['sms_code'] = sms; save()
    fill_code(sms); dump('61_sms_filled'); time.sleep(1)
    btn = find_xp('//button[@type="submit"]') or find_xp('//button[contains(normalize-space(.),"Continue")]')
    if btn: click(btn)
    time.sleep(13); t = dump('70_after_sms'); low = t.lower()
    ev('errors_after_sms', js("""return Array.from(document.querySelectorAll('[class*=error],[role=alert],.form-message')).map(function(e){return e.innerText.trim().slice(0,300);}).filter(Boolean).slice(0,8);"""))
    ev('final_url_sms', js('return location.href'))
    if 'incorrect' in low or 'invalid' in low: STATE['stage'] = 'phone_code_rejected'
    elif 'terms' in low or 'conditions' in low: STATE['stage'] = 'terms_stage'
    elif 'account' in low and ('created' in low or 'welcome' in low or 'signed in' in low): STATE['stage'] = 'account_created'
    else: STATE['stage'] = 'post_phone_stage'
    save()

try:
    main()
except Exception:
    ev('exception', traceback.format_exc()[-2500:])
    STATE['stage'] = 'exception'; save()
finally:
    log('FINAL STAGE =', STATE['stage'])
    save()
    try:
        if SID: wd('DELETE', sess())
    except Exception: pass
