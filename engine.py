#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Engine Playwright cho iLove bản web.

Ke thua logic DA CHAY ON tu ban desktop (ilove_v2):
  - Quet Active Posts bang selector that: #posts-list-container .post-item
  - Doc chi so Like/Comment/Diem ngay tren the bai
  - Nop link Update share theo quy trinh defensive (khop URL chuan hoa,
    bat dung 1 nut, kiem tra lai o nhap, cho popup dong)
Chi khac: dang nhap bang form (de chay headless tren server) thay vi
dang nhap tay tren Edge nhu ban desktop.
"""

import re
from urllib.parse import urlparse, urlsplit, parse_qsl, urlencode

HOME = 'https://ilove.hdbank.com.vn/employees'
LOGIN_URL = 'https://ilove.hdbank.com.vn/login'

# JS quet the bai — nguyen van tu ban desktop chay on.
SCAN_JS = r'''cards => cards.map(card => {
    const anchors = [...card.querySelectorAll('a[href]')];
    const a = anchors.find(el => { try { return /(^|\.)facebook\.com$/i.test(new URL(el.href).hostname); } catch(e) { return false; } });
    if (!a) return null;
    // Bieu tuong FB thuong la <i class="fab fa-facebook-square"> hoac SVG.
    const icon = card.querySelector('i[class*="facebook" i], svg[class*="facebook" i], [class*="facebook-square" i], [class*="fa-facebook" i], img[alt*="facebook" i], [aria-label*="facebook" i]');
    // Mot so ban giao dien dat logo duoi dang SVG ben canh duong dan bai.
    const adjacentIcon = a.parentElement?.querySelector('svg, i, img') || a.previousElementSibling?.matches?.('svg,i,img');
    if (!icon && !adjacentIcon) return null;
    const title=(a.getAttribute('title') || a.textContent || '').trim().slice(0,180);
    if (!title) return null;
    const texts=[...card.querySelectorAll('button,a,[role="button"],span')].filter(el=>el.children.length===0).map(el=>(el.textContent||'').trim());
    const hasUpdate=texts.some(t=>/^Update share$/i.test(t));
    const processing=texts.some(t=>/View submitted link processing/i.test(t));
    // Footer hien thi counter theo thu tu: Like 0/1, Comment 0/1,
    // co the Share 0/1, sau do tong 0/2 pts hoac 0/3 pts.
    const bodyText=card.innerText||'';
    const afterPoints=(bodyText.match(/\bPoints\b([\s\S]*)/i)||[])[1]||'';
    const scoreMatch=afterPoints.match(/(\d+)\s*\/\s*(\d+)\s*pts\b/i);
    const prefix=scoreMatch ? afterPoints.slice(0,scoreMatch.index) : afterPoints;
    const ratios=[...prefix.matchAll(/\b(\d+)\s*\/\s*(\d+)\b/g)]
      .map(m=>[Number(m[1]),Number(m[2])]);
    const get=(i)=> ratios.length>=2 && ratios[i] && ratios[i][1]===1
      ? ratios[i] : [-1,-1];
    const like=get(0), comment=get(1);
    const total=scoreMatch ? [Number(scoreMatch[1]),Number(scoreMatch[2])] : [-1,-1];
    return {title, url:a.href,
        group:hasUpdate?'Có Update share':'Không có Update share', processing,
        like_done:like[0],like_total:like[1],
        comment_done:comment[0],comment_total:comment[1],
        points_earned:total[0], points_total:total[1]};
}).filter(Boolean)'''


def valid(url, domain):
    """Chi chap nhan URL https thuoc domain."""
    try:
        p = urlparse(url.strip())
        return p.scheme == 'https' and (
            p.hostname == domain or (p.hostname or '').endswith('.' + domain))
    except Exception:
        return False


def portal_login(page, email, password, log=print):
    """Dang nhap portal bang form. Raise RuntimeError neu that bai."""
    page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=45000)
    page.wait_for_timeout(1500)
    email_el = page.locator("input[type='email']").first
    if email_el.count() == 0:
        email_el = page.locator("input[type='text']").first
    email_el.fill(email)
    pw_els = page.locator("input[type='password']")
    if pw_els.count() > 0:
        pw_els.first.fill(password)
    else:
        page.locator("input[type='text']").nth(1).fill(password)
    btn = page.get_by_role(
        "button", name=re.compile(r"login|sign in|đăng nhập", re.I)).first
    if btn.count() == 0:
        btn = page.locator("button[type='submit']").first
    btn.click()
    try:
        page.wait_for_url("**/employees**", timeout=25000)
    except Exception:
        page.wait_for_timeout(3000)
    if "/employees" not in page.url:
        raise RuntimeError(
            "Đăng nhập thất bại (không vào được /employees). "
            "Kiểm tra lại email/mật khẩu.")
    log("Đăng nhập portal thành công.")


def _load_all(page, log=print, max_clicks=15):
    """Bam Load More cho den khi het (gioi han so lan)."""
    for _ in range(max_clicks):
        btn = page.get_by_role(
            "button", name=re.compile(r"load more", re.I)).first
        try:
            if btn.count() == 0 or not btn.is_visible():
                break
            btn.click()
            page.wait_for_timeout(2500)
        except Exception:
            break


def scan_posts(page, log=print):
    """Quet Active Posts. Tra ve list dict (title, url, group, processing,
    like_done/total, comment_done/total, points_earned/total)."""
    page.goto(HOME, wait_until='domcontentloaded', timeout=45000)
    try:
        page.locator('#posts-list-container .post-item').first.wait_for(
            timeout=30000)
    except Exception:
        raise RuntimeError(
            "Không thấy danh sách Active Posts "
            "(#posts-list-container .post-item). Có thể phiên đăng nhập hết hạn "
            "hoặc trang đổi giao diện.")
    _load_all(page, log)
    data = page.locator('#posts-list-container .post-item').evaluate_all(SCAN_JS)
    log(f"Quét được {len(data)} bài Facebook.")
    return data


def _canonical(raw):
    u = urlsplit(raw)
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(u.query) if k != 'fbclid'))
    return ((u.hostname or '').removeprefix('www.') + u.path.rstrip('/')
            + ('?' + query if query else ''))


def submit_share_link(page, original_url, shared_url, log=print):
    """Nop link Share len iLove. Tra ve (ok: bool, msg: str).
    Quy trinh defensive tu ban desktop: sai buoc nao dung buoc do, khong gui bua."""
    if not valid(original_url, 'facebook.com') or not valid(shared_url, 'facebook.com'):
        return False, "Link Facebook không hợp lệ (phải là https://facebook.com/...)."
    page.goto(HOME, wait_until='domcontentloaded', timeout=45000)
    try:
        page.locator('#posts-list-container .post-item').first.wait_for(
            timeout=30000)
    except Exception:
        return False, "Không thấy danh sách bài. Hãy quét lại."
    _load_all(page, log)

    # Tim dung 1 the bai khop link goc (chuan hoa, bo fbclid).
    cards = page.locator('#posts-list-container .post-item')
    matches = []
    for i in range(cards.count()):
        card = cards.nth(i)
        links = card.locator('a[href*="facebook.com"]')
        for j in range(links.count()):
            try:
                href = links.nth(j).get_attribute('href') or ''
            except Exception:
                continue
            if _canonical(href) == _canonical(original_url):
                matches.append(i)
                break
    if len(matches) != 1:
        return False, (f"Có {len(matches)} bài khớp link gốc (cần đúng 1). "
                       "Chưa gửi.")
    card = cards.nth(matches[0])

    # Bat dung 1 nut Update share dang hien thi trong the bai.
    buttons = card.get_by_text(re.compile(r'^\s*Update share\s*$', re.I))
    visible = [buttons.nth(i) for i in range(buttons.count())
               if buttons.nth(i).is_visible()]
    if len(visible) != 1:
        return False, "Không tìm thấy duy nhất nút Update share của bài này. Chưa gửi."
    visible[0].click()

    # Doi popup, tim dung 1 o Shared link.
    from playwright.sync_api import TimeoutError as PWTimeout
    heading = page.get_by_text('Update shared link', exact=True)
    try:
        heading.first.wait_for(state='visible', timeout=10000)
    except PWTimeout:
        return False, "Không thấy popup 'Update shared link' sau khi bấm. Chưa gửi."
    field_candidates = page.locator('input:not([type="hidden"])')
    matching = []
    for idx in range(field_candidates.count()):
        item = field_candidates.nth(idx)
        if not item.is_visible():
            continue
        details = item.evaluate("""el => {
            const wrap = el.closest('[role="dialog"], .modal, .modal-content');
            const near = el.parentElement?.parentElement;
            return {dialog: !!wrap, nearby: (near?.innerText || '').slice(0, 300)};
        }""")
        if details['dialog'] or 'Shared link' in details['nearby']:
            matching.append(item)
    if len(matching) != 1:
        return False, (f"Tìm thấy {len(matching)} ô Shared link phù hợp "
                       "(cần đúng 1). Chưa điền.")
    field = matching[0]
    field.fill(shared_url)
    if field.input_value().strip() != shared_url:
        return False, "Link trong ô Shared link không khớp sau khi điền. Chưa gửi."

    # Tim dung 1 nut Submit dang hien thi.
    submit_buttons = page.locator(
        'button, [role="button"], input[type="submit"]').filter(
            has_text=re.compile(r'^\s*Submit\s*$', re.I))
    visible_submit = [submit_buttons.nth(i)
                      for i in range(submit_buttons.count())
                      if submit_buttons.nth(i).is_visible()]
    if len(visible_submit) != 1:
        return False, (f"Tìm thấy {len(visible_submit)} nút Submit "
                       "(cần đúng 1). Chưa gửi.")
    button = visible_submit[0]
    try:
        button.scroll_into_view_if_needed(timeout=5000)
        button.click(timeout=8000)
    except Exception as exc:
        return False, f"Không bấm được Submit: {str(exc)[:200]}"

    # Chi bao thanh cong khi popup da dong.
    try:
        heading.first.wait_for(state='hidden', timeout=10000)
    except PWTimeout:
        return False, ("Đã bấm Submit nhưng popup vẫn mở — có thể iLove đang báo "
                       "lỗi xác thực/link. Hãy kiểm tra popup.")
    return True, "OK"
