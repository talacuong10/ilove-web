#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HDBank iLove Assistant — ban web (Streamlit).

Quy trinh:
  1. Dang nhap portal (sidebar) -> Quet Active Posts (engine da chay on)
  2. Mo bai goc tren Facebook, Like/Comment/Share THU CONG
     (tool khong tu dong cham vao Facebook)
  3. Luu link bai da Share -> Nop link len iLove
  4. Quet lai de kiem tra diem

Chay local:  streamlit run app.py
"""

import csv
import io
import sqlite3
from datetime import datetime

import streamlit as st
from playwright.sync_api import sync_playwright

import engine

DB_PATH = "tasks_v2.db"

COMMENT_TEMPLATES = [
    "HDBank mãi đỉnh ❤️",
    "Ưu đãi hấp dẫn quá luôn",
    "Tuyệt vời quá HDBank ơi",
    "Deal ngon thế này phải mở thẻ ngay thôi",
    "HDBank lúc nào cũng chu đáo với khách hàng",
    "Chương trình hay quá đi",
    "10 điểm cho HDBank luôn ạ",
    "Tin vui cho mọi người đây rồi",
    "HDBank ngày càng đỉnh thật",
    "Thích quá, chia sẻ cho bạn bè cùng biết mới được",
]
SHARE_DEFAULT = "HDBank cam kết lợi ích cao nhất."

PENDING_SQL = """((like_total>0 AND like_done>=0 AND like_done<like_total)
    OR (comment_total>0 AND comment_done>=0 AND comment_done<comment_total))
    AND status NOT IN ('Đã xác nhận điểm','iLove đã ghi đủ điểm',
        'Đã Like/Comment - chờ iLove', 'Đã Share và Like/Comment - chờ iLove')"""


# ------------------------------------------------------------ database --
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    c = db()
    c.execute('''CREATE TABLE IF NOT EXISTS tasks(
        id INTEGER PRIMARY KEY, title TEXT, original_url TEXT UNIQUE,
        shared_url TEXT DEFAULT "", status TEXT DEFAULT "Chưa xử lý",
        updated TEXT DEFAULT "", post_group TEXT DEFAULT 'Chưa phân loại',
        like_done INTEGER DEFAULT -1, like_total INTEGER DEFAULT -1,
        comment_done INTEGER DEFAULT -1, comment_total INTEGER DEFAULT -1,
        points_earned INTEGER DEFAULT -1, points_total INTEGER DEFAULT -1)''')
    c.commit()
    c.close()


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def upsert_scan(data):
    """Luu ket qua quet vao DB. Tra ve (so_bai_moi, tong_so_bai)."""
    c = db()
    added = 0
    for x in data:
        if not engine.valid(x.get('url', ''), 'facebook.com'):
            continue
        cur = c.execute(
            'INSERT OR IGNORE INTO tasks(title,original_url,updated) '
            'VALUES(?,?,?)', (x['title'], x['url'], now()))
        added += cur.rowcount
        vals = [int(x.get(k, -1)) for k in (
            'like_done', 'like_total', 'comment_done',
            'comment_total', 'points_earned', 'points_total')]
        c.execute('''UPDATE tasks SET post_group=?, like_done=?, like_total=?,
                     comment_done=?, comment_total=?, points_earned=?,
                     points_total=? WHERE original_url=?''',
                  (x.get('group', 'Chưa phân loại'), *vals, x['url']))
        if vals[5] > 0 and vals[4] >= vals[5]:
            c.execute("UPDATE tasks SET status='iLove đã ghi đủ điểm' "
                      "WHERE original_url=? AND status NOT IN ('Đã xác nhận điểm')",
                      (x['url'],))
        if x.get('processing'):
            c.execute("UPDATE tasks SET status='Link đang xử lý' "
                      "WHERE original_url=? AND status IN ('Chưa xử lý','Link đang xử lý')",
                      (x['url'],))
    c.commit()
    c.close()
    return added, len(data)


def get_tasks(filt):
    c = db()
    q = 'SELECT * FROM tasks'
    params = []
    if filt == 'Cần Like/Comment':
        q += ' WHERE ' + PENDING_SQL
    elif filt != 'Tất cả':
        q += ' WHERE post_group=?'
        params = [filt]
    rows = c.execute(q + ' ORDER BY id DESC', params).fetchall()
    c.close()
    return rows


def get_task(tid):
    c = db()
    r = c.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
    c.close()
    return r


def set_task(tid, **kw):
    cols = ', '.join(f"{k}=?" for k in kw)
    c = db()
    c.execute(f"UPDATE tasks SET {cols}, updated=? WHERE id=?",
              (*kw.values(), now(), tid))
    c.commit()
    c.close()


def pending_count():
    c = db()
    n = c.execute(f"SELECT COUNT(*) FROM tasks WHERE {PENDING_SQL}").fetchone()[0]
    c.close()
    return n


# ------------------------------------------------------------- browser --
def run_browser(fn, *args):
    """Mo trinh duyet headless moi, chay fn(page, ...). Tra ve (ket_qua, logs)."""
    logs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            return fn(page, *args, lambda m: logs.append(m)), logs
        finally:
            browser.close()


def need_login():
    email = st.session_state.get("email", "")
    pw = st.session_state.get("pw", "")
    if not email or not pw:
        st.error("Nhập email/mật khẩu portal ở thanh bên trái trước.")
        return None
    return email, pw


# ------------------------------------------------------------------ UI --
st.set_page_config(page_title="HDBank iLove Assistant", layout="wide")
init_db()

st.title("🏦 HDBank iLove Assistant — bản web")
st.caption("Quét Active Posts • Mở Facebook làm tay • Lưu link Share • Nộp lên iLove")

# ============================ SIDEBAR ============================
with st.sidebar:
    st.header("🔑 Đăng nhập portal")
    st.text_input("Email HDBank", key="email")
    st.text_input("Mật khẩu", type="password", key="pw")
    if st.button("🔄 Đăng nhập & Quét Active Posts", type="primary",
                 use_container_width=True):
        creds = need_login()
        if creds:
            email, pw = creds
            with st.spinner("Đang đăng nhập và quét..."):
                try:
                    def _job(pg, log):
                        engine.portal_login(pg, email, pw, log)
                        return engine.scan_posts(pg, log)
                    data, logs = run_browser(_job)
                    added, total = upsert_scan(data)
                    st.success(f"Quét {total} bài Facebook, thêm mới {added}. "
                               f"Còn {pending_count()} bài thiếu Like/Comment.")
                    with st.expander("📋 Nhật ký"):
                        for line in logs:
                            st.text(line)
                except Exception as e:  # noqa: BLE001
                    st.error(f"Lỗi: {e}")
    st.caption("Mật khẩu chỉ dùng trong phiên này, không lưu ra file.")
    st.divider()
    st.caption("⚠️ Like/Comment/Share làm **thủ công** trên Facebook "
               "(bấm link bài gốc). Tool **không** tự động chạm vào Facebook.")

# ============================ MAIN ============================
tab_main, tab_templates = st.tabs(["📌 Chiến dịch", "💬 Mẫu bình luận"])

with tab_main:
    filt = st.radio("Hiển thị", ["Cần Like/Comment", "Tất cả",
                                 "Có Update share", "Không có Update share"],
                    horizontal=True)
    tasks = get_tasks(filt)

    m1, m2, m3 = st.columns(3)
    m1.metric("Số bài đang hiển thị", len(tasks))
    m2.metric("Còn thiếu Like/Comment", pending_count())
    c = db()
    done_pts = c.execute(
        "SELECT COUNT(*) FROM tasks WHERE status IN "
        "('Đã xác nhận điểm','iLove đã ghi đủ điểm')").fetchone()[0]
    c.close()
    m3.metric("Đã ghi điểm", done_pts)

    if not tasks:
        st.info("Chưa có dữ liệu. Đăng nhập & quét ở thanh bên trái.")
    else:
        import pandas as pd

        def _score(a, b):
            return f"{a}/{b}" if a is not None and a >= 0 and b >= 0 else "—"

        df = pd.DataFrame([{
            "ID": t["id"], "Tên bài": t["title"], "Nhóm": t["post_group"],
            "Like": _score(t["like_done"], t["like_total"]),
            "Comment": _score(t["comment_done"], t["comment_total"]),
            "Điểm": _score(t["points_earned"], t["points_total"]),
            "Trạng thái": t["status"]} for t in tasks])
        st.dataframe(df, use_container_width=True, hide_index=True)

        id2label = {t["id"]: f"{t['id']} — {t['title'][:70]}" for t in tasks}
        if st.button("⏭ Bài tiếp theo (còn thiếu Like/Comment)"):
            c = db()
            r = c.execute(f"SELECT id FROM tasks WHERE {PENDING_SQL} "
                          "ORDER BY id DESC LIMIT 1").fetchone()
            c.close()
            if r and r[0] in id2label:
                st.session_state["sel_post"] = r[0]
                st.rerun()
            else:
                st.info("Không còn bài thiếu Like/Comment. Bấm quét lại để cập nhật.")
        if st.session_state.get("sel_post") not in id2label:
            st.session_state["sel_post"] = list(id2label.keys())[0]
        sel_id = st.selectbox("Chọn bài để xử lý", list(id2label.keys()),
                              format_func=lambda i: id2label[i], key="sel_post")
        t = get_task(sel_id)

        with st.container(border=True):
            st.markdown(f"### [{t['post_group']}] {t['title']}")
            st.caption(
                f"Like {_score(t['like_done'], t['like_total'])} • "
                f"Comment {_score(t['comment_done'], t['comment_total'])} • "
                f"Điểm {_score(t['points_earned'], t['points_total'])} • "
                f"Trạng thái: **{t['status']}**")
            st.link_button("🔗 Mở bài gốc trên Facebook", t["original_url"])

            tmpl = st.selectbox("Mẫu bình luận (bấm icon copy ở góc phải)",
                                COMMENT_TEMPLATES, key=f"tmpl_{t['id']}")
            st.code(tmpl)
            st.caption("Mỗi bài dùng 1 mẫu khác nhau, giãn cách vài phút mỗi bình luận.")
            st.text_input("Nội dung Share", value=SHARE_DEFAULT, key=f"sh_{t['id']}")

            cc = st.checkbox(
                "Tôi đã thực sự Like và ĐĂNG Comment bài này trên Facebook",
                key=f"cf_{t['id']}")
            if st.button("✅ Đã làm Like + Comment", key=f"mk_{t['id']}",
                         disabled=not cc):
                new = ('Đã Share và Like/Comment - chờ iLove' if t['shared_url']
                       else 'Đã Like/Comment - chờ iLove')
                set_task(t['id'], status=new)
                st.success("Đã lưu xác nhận (chờ iLove ghi điểm sau khi quét lại).")
                st.rerun()

            link = st.text_input("Link bài đã Share", value=t['shared_url'] or "",
                                 key=f"lk_{t['id']}",
                                 placeholder="https://www.facebook.com/.../posts/...")
            if st.button("💾 Lưu link", key=f"sv_{t['id']}"):
                link = link.strip()
                if not engine.valid(link, 'facebook.com'):
                    st.error("Link phải là URL https://facebook.com/... hợp lệ.")
                elif link.rstrip('/') == (t['original_url'] or '').rstrip('/'):
                    st.error("Link Share không được trùng link bài gốc.")
                else:
                    set_task(t['id'], shared_url=link, status='Có link Share - chưa nộp')
                    st.success("Đã lưu link.")
                    st.rerun()

            cs = st.checkbox("Tôi xác nhận nộp link này lên iLove",
                             key=f"cs_{t['id']}")
            t = get_task(sel_id)  # doc lai sau khi co the da luu link
            can_submit = cs and (t['shared_url'] or '').strip()
            if st.button("📤 Nộp link Share", key=f"sb_{t['id']}",
                         disabled=not can_submit):
                if t['post_group'] == 'Không có Update share':
                    st.warning("Bài này không có nút Update share trên iLove.")
                else:
                    creds = need_login()
                    if creds:
                        email, pw = creds
                        with st.spinner("Đang nộp link lên iLove..."):
                            try:
                                def _sj(pg, log):
                                    engine.portal_login(pg, email, pw, log)
                                    return engine.submit_share_link(
                                        pg, t['original_url'],
                                        t['shared_url'].strip(), log)
                                (ok, msg), logs = run_browser(_sj)
                            except Exception as e:  # noqa: BLE001
                                ok, msg, logs = False, f"Lỗi: {e}", []
                        if ok:
                            set_task(t['id'], status='Đã nộp link - chờ duyệt')
                            st.success("Đã nộp ✓ — chờ ~5 phút rồi quét lại để kiểm tra điểm.")
                        else:
                            st.error(msg)
                        with st.expander("📋 Nhật ký"):
                            for line in logs:
                                st.text(line)
                        st.rerun()

            if st.button("🏁 Đã được ghi điểm", key=f"dn_{t['id']}"):
                set_task(t['id'], status='Đã xác nhận điểm')
                st.rerun()

    st.divider()
    if st.button("⬇️ Xuất CSV"):
        c = db()
        rows = c.execute('SELECT id,title,original_url,shared_url,status,updated,'
                         'post_group FROM tasks ORDER BY id').fetchall()
        c.close()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['ID', 'Tên bài', 'Link gốc', 'Link Share', 'Trạng thái',
                    'Cập nhật', 'Nhóm'])
        w.writerows([tuple(r) for r in rows])
        st.download_button("Tải file CSV", buf.getvalue().encode('utf-8-sig'),
                           "ilove_tasks.csv", "text/csv")

with tab_templates:
    st.caption("Mỗi bài dùng **1 mẫu khác nhau**, giãn cách vài phút mỗi bình luận "
               "để tránh bị Facebook đánh dấu spam.")
    for tmpl in COMMENT_TEMPLATES:
        st.code(tmpl)
