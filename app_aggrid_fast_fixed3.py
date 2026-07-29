import json
import hashlib
from pathlib import Path
from datetime import date, datetime, timedelta, time
import requests
import base64
import pandas as pd
import streamlit as st

APP_TITLE = "外線対応 共有ボード"
DATA_FILE = Path("availability_data.json")
DEFAULT_MEMBERS = []
START_HOUR = 8
END_HOUR = 18
SLOT_MINUTES = 30
WORKDAY_COUNT = 6
GREEN = "#00a000"
WHITE = "#ffffff"
HEADER_BG = "#1f2430"
TEXT = "#f5f5f5"

IMPORT_ERROR = None
try:
    from st_aggrid import AgGrid, GridOptionsBuilder, JsCode
    from st_aggrid import GridUpdateMode, DataReturnMode
except Exception as e:
    IMPORT_ERROR = e
    AgGrid = None
    GridOptionsBuilder = None
    JsCode = None
    GridUpdateMode = None
    DataReturnMode = None


def make_time_slots(start_hour=8, end_hour=18, step_minutes=30):
    slots = []
    dt = datetime.combine(date.today(), time(start_hour, 0))
    end = datetime.combine(date.today(), time(end_hour, 0))
    while dt < end:
        slots.append(dt.strftime("%H:%M"))
        dt += timedelta(minutes=step_minutes)
    return slots


def get_workdays(start: date, n: int = 6):
    days = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def date_key(d: date):
    return d.isoformat()


def jp_weekday(d: date):
    return "月火水木金土日"[d.weekday()]


def date_label(d: date):
    return f"{d.month}/{d.day}({jp_weekday(d)})"

def github_headers():
    return {
        "Authorization": f"Bearer {st.secrets['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def github_file_api_url():
    owner = st.secrets["GITHUB_OWNER"]
    repo = st.secrets["GITHUB_REPO"]
    path = st.secrets.get("GITHUB_DATA_FILE", "availability_data.json")
    return f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"


def empty_data():
    return {"members": DEFAULT_MEMBERS, "availability": {}}


def load_data():
    try:
        url = github_file_api_url()
        branch = st.secrets.get("GITHUB_BRANCH", "main")

        r = requests.get(
            url,
            headers=github_headers(),
            params={"ref": branch},
            timeout=10,
        )

        if r.status_code == 404:
            return empty_data()

        r.raise_for_status()

        content = r.json().get("content", "")
        raw = base64.b64decode(content).decode("utf-8")

        if not raw.strip():
            return empty_data()

        data = json.loads(raw)
        data.setdefault("members", DEFAULT_MEMBERS)
        data.setdefault("availability", {})
        return data

    except Exception as e:
        st.error("GitHubからデータを読み込めませんでした。")
        st.code(str(e))
        return empty_data()


def save_data(data):
    try:
        url = github_file_api_url()
        branch = st.secrets.get("GITHUB_BRANCH", "main")

        # GitHub上の現在のファイル情報を取得する
        r = requests.get(
            url,
            headers=github_headers(),
            params={"ref": branch},
            timeout=10,
        )

        sha = None
        if r.status_code == 200:
            sha = r.json().get("sha")
        elif r.status_code != 404:
            r.raise_for_status()

        json_text = json.dumps(data, ensure_ascii=False, indent=2)
        encoded = base64.b64encode(json_text.encode("utf-8")).decode("utf-8")

        payload = {
            "message": "Update availability data from Streamlit app",
            "content": encoded,
            "branch": branch,
        }

        if sha:
            payload["sha"] = sha

        res = requests.put(
            url,
            headers=github_headers(),
            json=payload,
            timeout=10,
        )
        res.raise_for_status()

    except Exception as e:
        st.error("GitHubへデータを保存できませんでした。")
        st.code(str(e))

def ensure_day_member(data, d: date, member: str, slots):
    dk = date_key(d)
    data["availability"].setdefault(dk, {})
    data["availability"][dk].setdefault(member, {})
    for s in slots:
        data["availability"][dk][member].setdefault(s, False)


def build_df(data, d: date, members, slots):
    rows = []
    dk = date_key(d)
    for m in members:
        ensure_day_member(data, d, m, slots)
        row = {"担当者": m}
        for s in slots:
            row[s] = bool(data["availability"][dk][m].get(s, False))
        row["午前"] = "午前OK"
        row["午後"] = "午後OK"
        row["終日"] = "終日OK"
        row["消去"] = "クリア"
        rows.append(row)
    return pd.DataFrame(rows)


def apply_grid_to_data(data, d: date, grid_df: pd.DataFrame, slots):
    dk = date_key(d)
    data["availability"].setdefault(dk, {})
    for _, row in grid_df.iterrows():
        member = str(row["担当者"])
        data["availability"][dk].setdefault(member, {})
        for s in slots:
            data["availability"][dk][member][s] = bool(row.get(s, False))


def members_signature(members):
    return hashlib.md5("|".join(map(str, members)).encode("utf-8")).hexdigest()[:8]


def make_cell_renderer():
    return JsCode(f"""
class ToggleCellRenderer {{
  init(params) {{
    this.params = params;
    this.eGui = document.createElement('div');
    this.eGui.style.width = '100%';
    this.eGui.style.height = '100%';
    this.eGui.style.cursor = 'pointer';
    this.eGui.style.boxSizing = 'border-box';
    this.eGui.onclick = () => {{
      const current = !!this.params.value;
      this.params.node.setDataValue(this.params.colDef.field, !current);
    }};
    this.refresh(params);
  }}
  refresh(params) {{
    this.params = params;
    const on = !!params.value;
    this.eGui.style.backgroundColor = on ? '{GREEN}' : '{WHITE}';
    this.eGui.style.border = '1px solid #2f3542';
    this.eGui.innerHTML = '';
    return true;
  }}
  getGui() {{ return this.eGui; }}
}}
""")


def make_action_renderer():
    return JsCode(r"""
class ActionButtonRenderer {
  init(params) {
    this.params = params;
    this.eGui = document.createElement('button');
    this.eGui.innerText = params.value;
    this.eGui.style.width = '100%';
    this.eGui.style.height = '26px';
    this.eGui.style.borderRadius = '6px';
    this.eGui.style.border = '1px solid #475569';
    this.eGui.style.background = '#222733';
    this.eGui.style.color = '#f5f5f5';
    this.eGui.style.fontWeight = '700';
    this.eGui.style.cursor = 'pointer';
    this.eGui.onclick = () => {
      const field = params.colDef.field;
      const cols = params.api.getColumnDefs().map(c => c.field);
      const slots = cols.filter(c => /^\d{2}:\d{2}$/.test(c));
      // 午前: 12:15まで。30分単位なので12:00開始セルまでON、12:30セルは含めない。
      // 午後: 13:00から。12:30セルは含めない。
      let targetSlots = [];
      if (field === '午前') targetSlots = slots.filter(s => s < '12:15');
      if (field === '午後') targetSlots = slots.filter(s => s >= '13:00');
      if (field === '終日') targetSlots = slots;
      if (field === '消去') targetSlots = slots;
      targetSlots.forEach(s => params.node.setDataValue(s, field === '消去' ? false : true));
    };
  }
  getGui() { return this.eGui; }
}
""")


def render_grid_for_day(data, d, members, slots, grid_version):
    df = build_df(data, d, members, slots)
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(resizable=True, sortable=False, filter=False, editable=False)
    gb.configure_column("担当者", pinned="left", width=90, editable=False,
                        cellStyle={"fontWeight": "bold", "backgroundColor": HEADER_BG, "color": TEXT})
    cell_renderer = make_cell_renderer()
    action_renderer = make_action_renderer()
    for s in slots:
        gb.configure_column(s, width=80, editable=False, cellRenderer=cell_renderer, cellStyle={"padding": "0px"})
    for c in ["午前", "午後", "終日", "消去"]:
        gb.configure_column(c, width=78, pinned="right", editable=False, cellRenderer=action_renderer)
    grid_options = gb.build()
    grid_options["rowHeight"] = 30
    grid_options["headerHeight"] = 30
    grid_options["suppressMovableColumns"] = True
    grid_options["suppressRowClickSelection"] = True
    grid_options["domLayout"] = "normal"
    grid_options["getRowId"] = JsCode("function(params) { return params.data.担当者; }")
    key = f"grid_{date_key(d)}_{members_signature(members)}_{grid_version}"
    grid_response = AgGrid(
        df,
        gridOptions=grid_options,
        height=150 + max(0, len(members) - 3) * 32,
        fit_columns_on_grid_load=False,
        allow_unsafe_jscode=True,
        data_return_mode=DataReturnMode.AS_INPUT,
        update_mode=GridUpdateMode.MANUAL,
        reload_data=True,
        theme="streamlit",
        key=key,
    )
    return pd.DataFrame(grid_response["data"])


st.set_page_config(page_title=APP_TITLE, layout="wide")

if IMPORT_ERROR is not None:
    st.error("streamlit-aggrid の読み込みに失敗しました。")
    st.code(str(IMPORT_ERROR))
    st.info("対処：`uv pip install streamlit-aggrid` または `python -m pip install streamlit-aggrid --proxy http://172.17.20.158:3128` を実行してください。")
    st.stop()

if "data" not in st.session_state:
    st.session_state.data = load_data()
if "grid_version" not in st.session_state:
    st.session_state.grid_version = 0

data = st.session_state.data
actual_today = date.today()
if st.session_state.get("_actual_today") != actual_today:
    st.session_state["_actual_today"] = actual_today
    st.session_state["start_day"] = actual_today

slots = make_time_slots(START_HOUR, END_HOUR, SLOT_MINUTES)

st.title(APP_TITLE)

with st.sidebar:
    st.header("設定")
    st.subheader("担当者")
    members_text = st.text_area("1行に1人ずつ入力", value="\n".join(data.get("members", DEFAULT_MEMBERS)), height=120)
    if st.button("担当者リストを保存", type="primary"):
        new_members = [x.strip() for x in members_text.splitlines() if x.strip()]
        if new_members:
            data["members"] = new_members
            save_data(data)
            st.session_state.grid_version += 1
            st.success("担当者リストを保存しました。表を再読み込みしました。")
            st.rerun()
        else:
            st.warning("担当者を1人以上入力してください。")

    st.divider()
    st.subheader("表示設定")
    start_day = st.date_input("開始日", key="start_day")
    st.caption("土日は自動で除外します。日付が変わった後、画面更新または操作時に今日へ切り替わります。")

members = data.get("members", DEFAULT_MEMBERS)
workdays = get_workdays(start_day, WORKDAY_COUNT)

st.info("セルを編集したら最後に保存してください。")

updated_by_day = {}
for d in workdays:
    st.subheader(date_label(d))
    updated_by_day[date_key(d)] = render_grid_for_day(data, d, members, slots, st.session_state.grid_version)

if st.button("全日程をJSONへ保存", type="primary"):
    for d in workdays:
        apply_grid_to_data(data, d, updated_by_day[date_key(d)], slots)
    save_data(data)
    st.success("保存しました。")
    st.rerun()

st.caption("データ保存先：GitHub上の availability_data.json")

