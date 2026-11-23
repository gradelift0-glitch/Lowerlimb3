import html
import random
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Gradelift Practice", page_icon="🦴", layout="centered")

TZ = ZoneInfo("Africa/Lagos")
LOCAL_FILE = "question_bank.xlsx"      # only used when no SHEET_URL secret is set
LETTERS = ["A", "B", "C", "D"]
REQUIRED = ["ID", "Tag", "Module", "Use", "Status", "Question", "Option A", "Option B",
            "Option C", "Option D", "Correct (letter)", "Explanation"]
SESSION_SIZES = [5, 10, 15, 20]
NEW_DAYS = 7
REVISION = "__revision__"


# ------------------------------------------------------------------ helpers
def secret(key, default=""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


def today():
    return datetime.now(TZ).date()


def now_utc():
    return datetime.now(timezone.utc)


@st.cache_resource
def get_db():
    from supabase import create_client
    return create_client(secret("SUPABASE_URL"), secret("SUPABASE_KEY"))


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


# ------------------------------------------------------------------ questions
@st.cache_data(ttl=300, show_spinner="Loading questions...")
def load_questions(sheet_url: str):
    if sheet_url:
        m = re.search(r"/d/([a-zA-Z0-9_-]+)", sheet_url)
        if m:
            csv_url = f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
            g = re.search(r"gid=(\d+)", sheet_url)
            if g:
                csv_url += f"&gid={g.group(1)}"
        else:
            csv_url = sheet_url
        df = pd.read_csv(csv_url, dtype=str)
    else:
        df = pd.read_excel(LOCAL_FILE, sheet_name="Questions", dtype=str)

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns in the question bank: {', '.join(missing)}")
    for opt in ["Concept", "Source note", "Difficulty", "Release date", "Access", "Section"]:
        if opt not in df.columns:
            df[opt] = ""
    for c in df.columns:
        df[c] = df[c].fillna("").astype(str).str.strip()

    df = df[(df["Status"].str.lower() == "reviewed") & (df["Use"].str.lower() == "practice")]
    df["Correct (letter)"] = df["Correct (letter)"].str.upper()
    df = df[(df["Question"] != "") & (df["Module"] != "") & (df["Tag"] != "") & (df["ID"] != "")
            & df["Correct (letter)"].isin(LETTERS)]
    df = df[df.apply(lambda r: r[f"Option {r['Correct (letter)']}"] != "", axis=1)]
    df = df.drop_duplicates(subset="ID", keep="first")

    out = []
    for _, r in df.iterrows():
        rel = r["Release date"][:10]
        if rel == "":
            release = ""
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", rel):
            try:
                date.fromisoformat(rel)
                release = rel
            except ValueError:
                release = "BAD"
        else:
            release = "BAD"
        out.append({
            "id": r["ID"], "section": r["Section"], "tag": r["Tag"], "module": r["Module"],
            "question": r["Question"],
            "options": [r[f"Option {l}"] for l in LETTERS if r[f"Option {l}"]],
            "correct": r[f"Option {r['Correct (letter)']}"],
            "explanation": r["Explanation"], "concept": r["Concept"], "source": r["Source note"],
            "release": release,
            "access": "free" if r["Access"].lower() == "free" else "paid",
        })
    return out


def visible_questions(all_q, guest):
    t = today()
    out = []
    for q in all_q:
        if guest and q["access"] != "free":
            continue
        if q["release"] == "BAD":
            continue
        if q["release"] and date.fromisoformat(q["release"]) > t:
            continue
        out.append(q)
    return out


def structure(vis):
    """[(section, [(tag, [(module, [questions])])])] in order of first appearance."""
    sections = {}
    for q in vis:
        sections.setdefault(q["section"], {}).setdefault(q["tag"], {}).setdefault(q["module"], []).append(q)
    out = []
    for sec, tags in sections.items():
        tag_list = []
        for tag, mods in tags.items():
            mod_list = sorted(mods.items(), key=lambda kv: natural_key(kv[0]))
            tag_list.append((tag, mod_list))
        out.append((sec, tag_list))
    return out


# ------------------------------------------------------------------ progress
def last_results(rows, valid_ids):
    last = {}
    for r in sorted(rows, key=lambda r: r.get("id") or 0):
        if r["question_id"] in valid_ids:
            last[r["question_id"]] = bool(r["is_correct"])
    return last


def stats(qs, last):
    tried = sum(1 for q in qs if q["id"] in last)
    right = sum(1 for q in qs if last.get(q["id"]) is True)
    retry = sum(1 for q in qs if last.get(q["id"]) is False)
    return {"total": len(qs), "tried": tried, "right": right, "retry": retry, "new": len(qs) - tried}


def status_label(s):
    if s["tried"] < 3:
        return "⚪", "Just started"
    pct = 100 * s["right"] / s["tried"]
    if pct >= 75:
        return "🟢", "Strong"
    if pct >= 50:
        return "🟡", "Revise"
    return "🔴", "Needs work"


def fetch_answers(code, bank):
    out, start = [], 0
    while True:
        res = (get_db().table("answers").select("id,attempt_id,question_id,tag,module,is_correct,created_at")
               .eq("code", code).eq("bank", bank).order("id").range(start, start + 999).execute().data)
        out += res
        if len(res) < 1000:
            break
        start += 1000
    return out


def parse_ts(v):
    try:
        t = pd.to_datetime(v, utc=True)
        return t.to_pydatetime()
    except Exception:
        return None



def concept_of(q):
    return q["concept"] or q["tag"]


def perf_icon(pct):
    if pct >= 75:
        return "🟢"
    if pct >= 50:
        return "🟠"
    return "🔴"


CSS = """
<style>
.block-container,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]{padding-top:4.6rem !important;padding-bottom:4rem !important}
.gl-hero{background:linear-gradient(135deg,#4f46e5 0%,#7c3aed 60%,#c026d3 130%);color:#fff;border-radius:20px;
 padding:16px 16px 14px;box-shadow:0 8px 24px rgba(79,70,229,.28);margin:2px 0 12px}
.gl-hero-top{display:flex;align-items:center;justify-content:space-between;gap:12px}
.gl-hi{font-size:1.35rem;font-weight:800;line-height:1.2}
.gl-sub{font-size:.85rem;opacity:.92;margin-top:3px}
.gl-ring{width:62px;height:62px;border-radius:50%;display:grid;place-items:center;flex:none}
.gl-ring span{width:46px;height:46px;border-radius:50%;background:#5b42e0;display:grid;place-items:center;font-weight:800;font-size:.85rem}
.gl-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:12px}
.gl-stat{background:rgba(255,255,255,.17);border-radius:12px;padding:8px 2px;text-align:center}
.gl-stat b{display:block;font-size:1.25rem;line-height:1.1}
.gl-stat small{font-size:.66rem;opacity:.95}
.gl-card{background:rgba(128,128,128,.12);border:1px solid rgba(128,128,128,.18);border-radius:16px;padding:11px 14px;margin:8px 0}
.gl-card h4{margin:0 0 6px;font-size:.72rem;letter-spacing:.07em;text-transform:uppercase;opacity:.75;font-weight:700}
.gl-alert{background:rgba(245,158,11,.16);border:1px solid rgba(245,158,11,.4);border-radius:14px;padding:9px 12px;margin:8px 0;font-size:.92rem}
.gl-week{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;text-align:center}
.gl-week small{display:block;font-size:.62rem;opacity:.7}
.gl-week span{font-size:1.1rem}
.gl-section{font-size:1.05rem;font-weight:800;margin:18px 0 4px}
.gl-bar{height:8px;border-radius:99px;background:rgba(128,128,128,.25);overflow:hidden}
.gl-bar>div{height:100%;border-radius:99px;background:linear-gradient(90deg,#22c55e,#14b8a6)}
.gl-mini{background:rgba(128,128,128,.13);border-radius:12px;padding:8px 4px;text-align:center}
.gl-mini b{display:block;font-size:1.2rem;line-height:1.15}
.gl-mini small{font-size:.66rem;opacity:.75}
div[data-testid="stExpander"]{border-radius:14px !important;border:1px solid rgba(128,128,128,.25) !important}
.stButton>button{border-radius:12px;font-weight:600}
.stButton>button[kind="primary"],button[data-testid="stBaseButton-primary"]{
 background:linear-gradient(135deg,#4f46e5,#7c3aed) !important;border:none !important;color:#fff !important}
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def esc(x):
    return html.escape(str(x))


def card_grid(items):
    cols = len(items)
    cells = "".join(f'<div class="gl-mini"><b>{esc(v)}</b><small>{esc(l)}</small></div>' for l, v in items)
    st.markdown(f'<div style="display:grid;grid-template-columns:repeat({cols},1fr);gap:6px;margin:6px 0 10px">'
                f'{cells}</div>', unsafe_allow_html=True)


def stacked_bar(right, retry, new):
    total = (right + retry + new) or 1
    seg = lambda n, c: f'<div style="width:{100 * n / total:.1f}%;background:{c};"></div>'
    st.markdown('<div style="display:flex;height:12px;border-radius:6px;overflow:hidden;'
                'background:rgba(128,128,128,0.25);">'
                + seg(right, "#2e7d32") + seg(retry, "#d32f2f") + seg(new, "rgba(128,128,128,0.4)")
                + '</div>', unsafe_allow_html=True)
    st.caption(f"{right} right · {retry} to retry · {new} not yet tried")


def week_dots(rows, ids):
    t = today()
    monday = t - timedelta(days=t.weekday())
    active = set()
    for r in rows:
        if r["question_id"] in ids:
            ts = parse_ts(r.get("created_at"))
            if ts:
                active.add(ts.astimezone(TZ).date())
    cells = ""
    for i, name in enumerate(["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]):
        d = monday + timedelta(days=i)
        dot = "●" if d in active else "○"
        style = "font-weight:700;" if d == t else "opacity:0.8;"
        cells += (f'<div style="text-align:center;{style}"><div style="font-size:0.7rem;">{name}</div>'
                  f'<div style="font-size:1.15rem;">{dot}</div></div>')
    st.markdown('<div style="display:grid;grid-template-columns:repeat(7,1fr);gap:4px;">' + cells + '</div>',
                unsafe_allow_html=True)


def module_history(qs):
    ids = {q["id"] for q in qs}
    groups = {}
    for r in st.session_state.rows:
        aid = r.get("attempt_id")
        if r["question_id"] in ids and aid is not None:
            g = groups.setdefault(aid, {"n": 0, "ok": 0, "ts": None})
            g["n"] += 1
            g["ok"] += int(bool(r["is_correct"]))
            t = parse_ts(r.get("created_at"))
            if t and (g["ts"] is None or t < g["ts"]):
                g["ts"] = t
    floor = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(groups.values(), key=lambda g: g["ts"] or floor)


# ------------------------------------------------------------------ state
def init_state():
    defaults = {"stage": "login", "guest": False, "name": "", "code": "", "rows": [],
                "sel": None, "practice": None, "summary": None, "save_error": False,
                "next_id": 1}
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


def logout():
    for k in ["stage", "guest", "name", "code", "rows", "sel", "practice", "summary", "save_error"]:
        st.session_state.pop(k, None)


def contact_button(label="Get the full question bank"):
    url = str(secret("CONTACT_URL"))
    if url:
        st.link_button(label, url, type="primary")
    else:
        st.caption("Message your tutor to get your personal code.")


# ------------------------------------------------------------------ login
def check_code(raw):
    code = raw.strip().upper()
    if not code:
        return None, "Please enter your code."
    rows = get_db().table("codes").select("*").eq("code", code).execute().data
    if not rows:
        return None, "That code was not found. Check it and try again."
    row = rows[0]
    if not row.get("active", True):
        return None, "This code has been switched off. Please contact your tutor."
    exp = row.get("expires_on")
    if exp and date.fromisoformat(str(exp)[:10]) < date.today():
        return None, "This code has expired. Please contact your tutor."
    allowed = [a.strip().lower() for a in str(row.get("access", "all")).split(",")]
    if "all" not in allowed and str(secret("BANK_NAME", "lower-limb")).lower() not in allowed:
        return None, "This code does not include this question bank."
    return row, None


def page_login():
    st.markdown('<div class="gl-hero"><div class="gl-hi">🦴 Gradelift Practice</div>'
                '<div class="gl-sub">Practise anatomy questions, see where you are strong, '
                'and know what to study next.</div></div>', unsafe_allow_html=True)
    code = st.text_input("Your personal code", placeholder="e.g. GL-AB123")
    if st.button("Continue", type="primary"):
        try:
            row, err = check_code(code)
        except Exception:
            st.error("Could not reach the server. Please try again in a moment.")
            return
        if err:
            st.error(err)
            return
        try:
            rows = fetch_answers(row["code"], BANK)
        except Exception:
            st.error("Could not load your progress. Please try again in a moment.")
            return
        st.session_state.update({"stage": "home", "guest": False, "name": row["student_name"],
                                 "code": row["code"], "rows": rows,
                                 "next_id": max([int(r.get("id") or 0) for r in rows], default=0) + 1})
        st.rerun()
    st.divider()
    st.markdown("**No code yet?**")
    gname = st.text_input("Your first name (optional)", key="gname")
    if st.button("Try the free questions"):
        st.session_state.update({"stage": "home", "guest": True, "name": gname.strip(),
                                 "code": "", "rows": []})
        st.rerun()
    if st.query_params.get("tutor") == "1":
        with st.expander("Tutor access", expanded=True):
            acode = st.text_input("Tutor code", type="password", key="acode")
            if st.button("Open tutor panel"):
                if secret("ADMIN_CODE") and acode.strip() == str(secret("ADMIN_CODE")).strip():
                    st.session_state.stage = "admin"
                    st.rerun()
                else:
                    st.error("That tutor code is not correct.")


# ------------------------------------------------------------------ home / dashboard
def week_html(rows, ids):
    t = today()
    monday = t - timedelta(days=t.weekday())
    active = set()
    for r in rows:
        if r["question_id"] in ids:
            ts = parse_ts(r.get("created_at"))
            if ts:
                active.add(ts.astimezone(TZ).date())
    cells = ""
    for i, name in enumerate(["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]):
        d = monday + timedelta(days=i)
        dot = "●" if d in active else "○"
        weight = "font-weight:800;" if d == t else ""
        cells += f'<div style="{weight}"><small>{name}</small><span>{dot}</span></div>'
    return f'<div class="gl-week">{cells}</div>'


def weekly_block(vis, last, rows):
    now = now_utc()
    ids = {q["id"] for q in vis}
    wk = prev = 0
    for r in rows:
        if r["question_id"] not in ids:
            continue
        t = parse_ts(r.get("created_at"))
        if not t:
            continue
        if t >= now - timedelta(days=7):
            wk += 1
        elif t >= now - timedelta(days=14):
            prev += 1
    cutoff = today() - timedelta(days=NEW_DAYS - 1)
    new_released = sum(1 for q in vis if q["release"] and date.fromisoformat(q["release"]) >= cutoff
                       and q["id"] not in last)
    msg = f"<b>{wk}</b> question(s) practised in the last 7 days"
    if prev:
        msg += f" · {prev} the week before"
    extra = f'<div style="margin-top:6px">🆕 <b>{new_released}</b> new question(s) released this week</div>' if new_released else ""
    st.markdown(f'<div class="gl-card"><h4>📅 This week</h4>{week_html(rows, ids)}'
                f'<div style="font-size:.85rem;margin-top:6px;opacity:.9">{msg}</div>{extra}</div>',
                unsafe_allow_html=True)


def hero(name, s):
    pct = round(100 * s["right"] / s["total"]) if s["total"] else 0
    acc = f"{round(100 * s['right'] / s['tried'])}%" if s["tried"] else "-"
    hi = f"Hi {esc(name)} 👋" if name else "Hi 👋"
    sub = (f"{s['right']} of {s['total']} questions cleared" if s["tried"]
           else "Pick a topic below and start practising")
    stats_html = "".join(f'<div class="gl-stat"><b>{esc(v)}</b><small>{esc(l)}</small></div>'
                         for l, v in [("Attempted", s["tried"]), ("Accuracy", acc),
                                      ("Correct", s["right"]), ("To review", s["retry"])])
    st.markdown(
        f'<div class="gl-hero"><div class="gl-hero-top"><div><div class="gl-hi">{hi}</div>'
        f'<div class="gl-sub">{sub}</div></div>'
        f'<div class="gl-ring" style="background:conic-gradient(#86efac {pct}%,rgba(255,255,255,.28) 0)">'
        f'<span>{pct}%</span></div></div><div class="gl-stats">{stats_html}</div></div>',
        unsafe_allow_html=True)


def page_home(all_q):
    guest = st.session_state.guest
    vis = visible_questions(all_q, guest)
    name = st.session_state.name
    if guest:
        st.info("You are using the **free starter questions**. Your progress is not saved.")
        contact_button()
    if not vis:
        st.markdown(f'<div class="gl-hero"><div class="gl-hi">Hi {esc(name)} 👋</div></div>' if name else "",
                    unsafe_allow_html=True)
        st.warning("No questions are available yet. New questions are released every week.")
        footer()
        return
    last = last_results(st.session_state.rows, {q["id"] for q in vis})
    s = stats(vis, last)
    hero(name, s)

    weak = Counter(concept_of(q) for q in vis if last.get(q["id"]) is False)
    if weak:
        top, n = weak.most_common(1)[0]
        st.markdown(f'<div class="gl-alert">⚠️ <b>Your weakest topic: {esc(top)}</b> · {n} question(s) to review</div>',
                    unsafe_allow_html=True)

    if s["retry"]:
        st.markdown(f'<div class="gl-card"><h4>🔁 Revision centre</h4>'
                    f'<b>{s["retry"]}</b> question(s) you missed are waiting for revision.</div>',
                    unsafe_allow_html=True)
        if st.button("Revise my missed questions", type="primary"):
            st.session_state.sel = (REVISION, "All missed questions")
            st.session_state.stage = "setup"
            st.rerun()

    weekly_block(vis, last, st.session_state.rows)

    struct = structure(vis)
    cutoff = today() - timedelta(days=NEW_DAYS - 1)
    for sec, tags in struct:
        if sec:
            secq = [q for _, mods in tags for _, m in mods for q in m]
            ss = stats(secq, last)
            p = round(100 * ss["right"] / ss["total"]) if ss["total"] else 0
            st.markdown(f'<div class="gl-section">{esc(sec)}</div><div class="gl-bar"><div style="width:{p}%"></div></div>'
                        f'<div style="font-size:.78rem;opacity:.7;margin:3px 0 8px">{ss["right"]} of {ss["total"]} right</div>',
                        unsafe_allow_html=True)
        else:
            st.markdown('<div class="gl-section">Topics</div>', unsafe_allow_html=True)
        for tag, mods in tags:
            tq = [q for _, m in mods for q in m]
            ts = stats(tq, last)
            has_new = any(q["release"] and date.fromisoformat(q["release"]) >= cutoff and q["id"] not in last for q in tq)
            icon, label = status_label(ts)
            title = f"{icon} {tag}  ·  {ts['right']}/{ts['total']}" + ("  🆕" if has_new else "")
            with st.expander(title, expanded=has_new):
                stacked_bar(ts["right"], ts["retry"], ts["new"])
                for mod, qs in mods:
                    ms = stats(qs, last)
                    mi, ml = status_label(ms)
                    st.markdown(f"**{mod}**  {mi} {ml}")
                    stacked_bar(ms["right"], ms["retry"], ms["new"])
                    b1, b2 = st.columns(2)
                    key = f"{tag}|{mod}"
                    if b1.button("Practise", key=f"p_{key}", use_container_width=True):
                        st.session_state.sel = (tag, mod)
                        st.session_state.stage = "setup"
                        st.rerun()
                    if ms["tried"] > 0 and b2.button("Review", key=f"r_{key}", use_container_width=True):
                        st.session_state.sel = (tag, mod)
                        st.session_state.stage = "review"
                        st.rerun()
                    st.divider()
    footer()


def footer():
    st.divider()
    if st.button("Log out"):
        logout()
        st.rerun()


# ------------------------------------------------------------------ practice
def module_questions(all_q):
    sel = st.session_state.sel
    vis = visible_questions(all_q, st.session_state.guest)
    if sel[0] == REVISION:
        last = last_results(st.session_state.rows, {q["id"] for q in vis})
        return vis, [q for q in vis if last.get(q["id"]) is False]
    return vis, [q for q in vis if q["tag"] == sel[0] and q["module"] == sel[1]]


def page_setup(all_q):
    sel = st.session_state.sel
    vis, qs = module_questions(all_q)
    last = last_results(st.session_state.rows, {q["id"] for q in vis})
    s = stats(qs, last)
    if sel[0] == REVISION:
        st.title("🔁 Revision centre")
        if not qs:
            st.success("Nothing to revise right now. Well done!")
            if st.button("← Back"):
                st.session_state.stage = "home"
                st.rerun()
            return
        st.write(f"**{len(qs)}** question(s) you missed earlier, from across your topics.")
        pool, again = len(qs), False
    else:
        tag, mod = sel[:2]
        st.title(f"{tag}")
        st.subheader(mod)
        stacked_bar(s["right"], s["retry"], s["new"])
        pool = s["retry"] + s["new"]
        again = False
        if pool == 0:
            st.success("✅ You have cleared every question released so far in this module.")
            st.write("You can practise them again, or come back when new questions are released.")
            pool = s["total"]
            again = True
        else:
            st.write("Questions you got wrong come first, then new ones.")
    sizes = sorted(set([n for n in SESSION_SIZES if n < pool] + [pool]))
    n = st.selectbox("How many questions?", sizes, index=min(1, len(sizes) - 1),
                     format_func=lambda x: f"All {x}" if x == pool else str(x))
    if st.button("Start" if not again else "Practise again", type="primary"):
        start_session(qs, last, n, again)
        st.rerun()
    if st.button("← Back"):
        st.session_state.stage = "home"
        st.rerun()


def start_session(qs, last, n, again):
    retry = [q for q in qs if last.get(q["id"]) is False]
    new = [q for q in qs if q["id"] not in last]
    random.shuffle(retry)
    random.shuffle(new)
    pool = retry + new
    if not pool or again:
        pool = qs[:]
        random.shuffle(pool)
    picked = pool[:n]
    items = []
    for q in picked:
        opts = q["options"][:]
        random.shuffle(opts)
        items.append({**q, "options": opts})
    attempt_id = None
    st.session_state.save_error = False
    if not st.session_state.guest:
        try:
            attempt_id = get_db().table("attempts").insert({
                "code": st.session_state.code, "bank": BANK, "mode": "practice",
                "started_at": now_utc().isoformat()}).execute().data[0]["id"]
        except Exception:
            st.session_state.save_error = True
    st.session_state.practice = {"items": items, "idx": 0, "submitted": False, "chosen": None,
                                 "correct": 0, "attempt_id": attempt_id, "answered": [],
                                 "local_id": f"local-{now_utc().timestamp()}"}
    st.session_state.stage = "practice"


def start_concept_session(qs, last, tag, mod, concept):
    cq = [q for q in qs if concept_of(q) == concept and last.get(q["id"]) is False]
    if not cq:
        return
    st.session_state.sel = (tag, mod, concept)
    start_session(cq, last, len(cq), False)


def save_answer(item, chosen, ok):
    p = st.session_state.practice
    row = {"id": st.session_state.next_id,
           "attempt_id": p["attempt_id"] if p["attempt_id"] is not None else p["local_id"],
           "question_id": item["id"], "tag": item["tag"],
           "module": item["module"], "is_correct": ok, "created_at": now_utc().isoformat()}
    st.session_state.next_id += 1
    st.session_state.rows.append(row)
    if st.session_state.guest or p["attempt_id"] is None:
        return
    try:
        get_db().table("answers").insert({
            "attempt_id": p["attempt_id"], "code": st.session_state.code, "bank": BANK,
            "question_id": item["id"], "tag": item["tag"], "module": item["module"],
            "chosen": chosen, "correct": item["correct"], "is_correct": ok, "mode": "practice"}).execute()
    except Exception:
        st.session_state.save_error = True


def page_practice():
    p = st.session_state.practice
    items, idx = p["items"], p["idx"]
    item = items[idx]
    st.progress((idx + 1) / len(items))
    st.caption(f"Question {idx + 1} of {len(items)}")
    st.markdown(f"**{item['question']}**")
    if not p["submitted"]:
        choice = st.radio("Choose one", item["options"], index=None, key=f"c_{idx}",
                          label_visibility="collapsed")
        if st.button("Submit answer", type="primary", disabled=choice is None):
            ok = choice == item["correct"]
            p["chosen"], p["submitted"] = choice, True
            p["correct"] += int(ok)
            p["answered"].append((item, choice, ok))
            save_answer(item, choice, ok)
            st.rerun()
    else:
        if p["chosen"] == item["correct"]:
            st.success(f"Correct: {item['correct']}")
        else:
            st.error(f"Not quite. You chose: {p['chosen']}")
            st.info(f"Correct answer: {item['correct']}")
        if item["explanation"]:
            st.write(item["explanation"])
        last_q = idx == len(items) - 1
        if st.button("Finish" if last_q else "Next question", type="primary"):
            p["submitted"] = False
            if last_q:
                finish_session()
            else:
                p["idx"] += 1
            st.rerun()
    if st.session_state.save_error:
        st.caption("⚠️ Your last answer could not be saved. Check your connection.")


def finish_session():
    p = st.session_state.practice
    if p["attempt_id"] is not None and not st.session_state.guest:
        try:
            get_db().table("attempts").update({
                "submitted_at": now_utc().isoformat(), "score": p["correct"],
                "total": len(p["items"])}).eq("id", p["attempt_id"]).execute()
        except Exception:
            pass
    sel = st.session_state.sel
    concept = sel[2] if len(sel) == 3 else None
    if concept:
        st.session_state.sel = sel[:2]
    st.session_state.summary = {"correct": p["correct"], "total": len(p["items"]),
                                "answered": p["answered"], "concept": concept}
    st.session_state.stage = "summary"


def page_summary(all_q):
    sm = st.session_state.summary
    sel = st.session_state.sel
    vis, qs = module_questions(all_q)
    last = last_results(st.session_state.rows, {q["id"] for q in vis})
    s = stats(qs, last)
    st.title("Session complete")
    st.subheader(f"{sm['correct']} out of {sm['total']} right")
    if st.session_state.save_error:
        st.warning("Some answers could not be saved. Please check your connection.")
    elif not st.session_state.guest:
        st.caption("✅ Your progress has been saved.")
    wrong = [(it, ch) for it, ch, ok in sm["answered"] if not ok]
    if wrong:
        st.write(f"**{len(wrong)} question(s) will come back** next time you practise.")
    elif sm["total"]:
        st.success("You got them all right. Nicely done!")
    if sel[0] == REVISION:
        st.caption(f"{s['retry']} missed question(s) still waiting for revision.")
    else:
        tag, mod = sel[:2]
        if sm.get("concept"):
            st.caption(f"Focused practice: {sm['concept']}")
        if s["new"] == 0 and s["tried"] > 0:
            st.success("🎉 You have tried every question released so far in this module. Here is your review.")
            render_review(qs, last, tag, mod, "sum")
        else:
            st.info(f"Your module review unlocks when you have tried every released question "
                    f"({s['new']} left). You can also open it now with the Review button on your dashboard.")
    if st.session_state.guest:
        contact_button("Unlock the full question bank")
    c1, c2 = st.columns(2)
    if c1.button("Practise more", use_container_width=True):
        st.session_state.stage = "setup"
        st.rerun()
    if c2.button("Dashboard", use_container_width=True):
        st.session_state.stage = "home"
        st.rerun()


# ------------------------------------------------------------------ review
def render_review(qs, last, tag, mod, ctx):
    s = stats(qs, last)
    icon, label = status_label(s)
    st.markdown(f"### {icon} {tag}: {mod}")
    mastery = f"{round(100 * s['right'] / s['tried'])}%" if s["tried"] else "-"
    card_grid([("Mastery", mastery), ("Tried", f"{s['tried']}/{s['total']}"),
               ("Correct answers", s["right"]), ("Questions to review", s["retry"])])
    stacked_bar(s["right"], s["retry"], s["new"])
    st.write(f"**{label}**")

    seen, cleared, retry = Counter(), Counter(), Counter()
    notes = {}
    for q in qs:
        if q["id"] in last:
            c = concept_of(q)
            seen[c] += 1
            if last[q["id"]]:
                cleared[c] += 1
            else:
                retry[c] += 1
                if q["source"]:
                    notes.setdefault(c, Counter())[q["source"]] += 1
    pct = lambda c: round(100 * cleared[c] / seen[c])
    gaps = sorted([c for c in seen if retry[c]], key=lambda c: (pct(c), -retry[c]))
    good = sorted([c for c in seen if not retry[c]])

    if gaps:
        st.markdown("**🎯 Your knowledge gaps**")
        for c in gaps:
            st.write(f"{perf_icon(pct(c))} **{c}**: {retry[c]} question(s) to review ({pct(c)}% right)")
            if st.button(f"Review {c} →", key=f"cr_{ctx}_{tag}|{mod}|{c}"):
                start_concept_session(qs, last, tag, mod, c)
                st.rerun()
        st.markdown("**📚 Study recommendations**")
        for i, c in enumerate(gaps[:3], 1):
            src = notes.get(c)
            where = src.most_common(1)[0][0] if src else tag
            st.write(f"{i}. **{c}**: revise from *{where}*")
    elif s["tried"]:
        st.success("Nothing to retry right now. Well done.")
    if good:
        st.markdown("**✅ Going well**")
        st.write(", ".join(f"{c} ({pct(c)}%)" for c in good))

    hist = module_history(qs)
    if hist:
        st.markdown("**📈 Practice history**")
        for i, g in enumerate(hist[-6:], max(1, len(hist) - 5)):
            when = g["ts"].astimezone(TZ).strftime("%d %b") if g["ts"] else ""
            st.write(f"Session {i} ({when}): {g['ok']}/{g['n']} right ({round(100 * g['ok'] / g['n'])}%)")
    if s["new"]:
        st.caption(f"{s['new']} released question(s) not yet tried, so this review is partial.")


def page_review(all_q):
    tag, mod = st.session_state.sel[:2]
    vis, qs = module_questions(all_q)
    last = last_results(st.session_state.rows, {q["id"] for q in vis})
    st.title("Module review")
    render_review(qs, last, tag, mod, "rev")
    c1, c2 = st.columns(2)
    if c1.button("Practise this module", use_container_width=True):
        st.session_state.stage = "setup"
        st.rerun()
    if c2.button("Dashboard", use_container_width=True):
        st.session_state.stage = "home"
        st.rerun()


# ------------------------------------------------------------------ tutor panel
def make_code():
    return "GL-" + "".join(random.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(5))


def page_admin(all_q):
    st.title("Tutor panel")
    t1, t2, t3 = st.tabs(["New code", "Codes", "Content"])
    with t1:
        name = st.text_input("Student name", key="adm_name")
        access = st.selectbox("Access", ["all", "lower-limb", "upper-limb"], key="adm_access")
        exp = st.date_input("Expires on", value=today() + timedelta(days=150), key="adm_exp")
        if st.button("Create code", type="primary"):
            if not name.strip():
                st.error("Please enter the student's name.")
            else:
                try:
                    db = get_db()
                    code = make_code()
                    for _ in range(10):
                        if not db.table("codes").select("code").eq("code", code).execute().data:
                            break
                        code = make_code()
                    db.table("codes").insert({"code": code, "student_name": name.strip(), "access": access,
                                              "expires_on": exp.isoformat(), "active": True}).execute()
                    st.success("Code created. Send it to the student:")
                    st.code(code)
                except Exception as e:
                    st.error(f"Could not create the code: {e}")
    with t2:
        try:
            rows = get_db().table("codes").select("*").order("created_at", desc=True).limit(60).execute().data
        except Exception as e:
            rows = []
            st.error(f"Could not load codes: {e}")
        if rows:
            st.dataframe(pd.DataFrame(rows)[["code", "student_name", "access", "expires_on", "active"]],
                         hide_index=True)
            pick = st.selectbox("Switch a code on or off", [r["code"] for r in rows], key="adm_pick")
            cur = next(r for r in rows if r["code"] == pick)
            label = "Switch OFF" if cur.get("active", True) else "Switch ON"
            if st.button(label):
                get_db().table("codes").update({"active": not cur.get("active", True)}).eq("code", pick).execute()
                st.rerun()
    with t3:
        vis = visible_questions(all_q, False)
        bad = [q["id"] for q in all_q if q["release"] == "BAD"]
        free = sum(1 for q in vis if q["access"] == "free")
        scheduled = sum(1 for q in all_q if q["release"] not in ("", "BAD") and date.fromisoformat(q["release"]) > today())
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Live now", len(vis))
        c2.metric("Free", free)
        c3.metric("Scheduled", scheduled)
        c4.metric("Bad dates", len(bad))
        if bad:
            st.warning("These questions have a release date I cannot read, so they are hidden. "
                       "Use the format YYYY-MM-DD: " + ", ".join(bad[:20]))
        tab = Counter((q["tag"], q["module"]) for q in vis)
        if tab:
            df = pd.DataFrame([{"Topic": t, "Module": m, "Live questions": n} for (t, m), n in tab.items()])
            st.dataframe(df, hide_index=True)
        st.caption("Only questions with Status = Reviewed and Use = Practice are served.")
    if st.button("Log out"):
        logout()
        st.rerun()


# ------------------------------------------------------------------ main
BANK = str(secret("BANK_NAME", "lower-limb"))
inject_css()
init_state()
stage = st.session_state.stage
if stage == "login":
    page_login()
else:
    try:
        ALL_Q = load_questions(str(secret("SHEET_URL")))
    except Exception as e:
        st.error("Could not load the questions.")
        st.caption(str(e))
        st.stop()
    if stage == "admin":
        page_admin(ALL_Q)
    elif stage == "home":
        page_home(ALL_Q)
    elif stage == "setup" and st.session_state.sel:
        page_setup(ALL_Q)
    elif stage == "practice" and st.session_state.practice:
        page_practice()
    elif stage == "summary" and st.session_state.summary:
        page_summary(ALL_Q)
    elif stage == "review" and st.session_state.sel:
        page_review(ALL_Q)
    else:
        st.session_state.stage = "home"
        st.rerun()
