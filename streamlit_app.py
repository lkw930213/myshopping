"""쇼핑 리스트 앱 (Streamlit 버전)

웹 버전(index.html / app.js)과 같은 PRD 기능을 Streamlit으로 구현한다.
- 추가, 수정, 삭제, 체크, 완료 항목 일괄 삭제
- 데이터는 같은 폴더의 shopping_list.json 파일에 저장 (웹 버전의 localStorage 역할)

실행: streamlit run streamlit_app.py
"""

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

import streamlit as st

DATA_FILE = Path(__file__).with_name("shopping_list.json")
# 위젯 key와 CSS 클래스에 id를 쓰므로 안전한 문자만 허용한다.
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


# ---------------------------------------------------------
# 저장소 (F-19 ~ F-21)
# 세션마다 목록을 따로 들고 있으면 다른 탭의 변경을 덮어쓰므로,
# 매 실행마다 파일에서 읽고 변경은 잠금 안에서 "읽기 → 수정 → 쓰기"로 처리한다.
# ---------------------------------------------------------
@st.cache_resource
def get_file_lock():
    # 스크립트는 rerun마다 다시 실행되므로 잠금은 cache_resource로 프로세스 전체에서 공유한다.
    return threading.Lock()


def is_valid_item(item):
    return (
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("name"), str)
        and item["name"].strip() != ""
        and isinstance(item.get("checked"), bool)
    )


def read_file():
    """파일을 읽어 정상 항목만 돌려준다. 파일이 없거나 손상되었으면 빈 목록. (F-21)"""
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], False
    if not isinstance(data, list):
        return [], False

    items, seen_ids, repaired = [], set(), False
    for item in data:
        if not is_valid_item(item):
            continue
        item_id = item["id"]
        # 중복이거나 key로 쓸 수 없는 id는 새로 발급한다.
        if item_id in seen_ids or not SAFE_ID.fullmatch(item_id):
            item_id = str(uuid.uuid4())
            repaired = True
        seen_ids.add(item_id)
        created_at = item.get("createdAt")
        items.append(
            {
                "id": item_id,
                "name": item["name"],
                "checked": item["checked"],
                "createdAt": created_at if isinstance(created_at, (int, float)) else int(time.time() * 1000),
            }
        )
    return items, repaired


def write_file(items):
    """임시 파일에 쓴 뒤 교체해서, 쓰는 도중 실패해도 기존 파일이 깨지지 않게 한다."""
    tmp = DATA_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, DATA_FILE)
    except OSError as error:
        st.toast(f"저장하지 못했습니다: {error}", icon="⚠️")


def load_items():
    with get_file_lock():
        items, repaired = read_file()
        if repaired:
            # 새로 발급한 id가 rerun마다 바뀌지 않도록 바로 저장한다.
            write_file(items)
        return items


def mutate(change):
    """최신 파일 내용에 change(items)를 적용하고 저장한다. (F-20)"""
    with get_file_lock():
        items, _ = read_file()
        items = change(items)
        write_file(items)
        return items


# ---------------------------------------------------------
# 상태 변경 함수 (위젯 콜백으로 사용)
# ---------------------------------------------------------
def request_focus(kind, item_id=None):
    st.session_state["focus"] = (kind, item_id)


def add_item():
    """F-01 ~ F-05: 공백 제거, 빈 값 무시, 맨 아래에 추가"""
    name = st.session_state["new_item"].strip()
    if not name:
        return
    new_item = {
        "id": str(uuid.uuid4()),
        "name": name,
        "checked": False,
        "createdAt": int(time.time() * 1000),
    }
    mutate(lambda items: items + [new_item])
    request_focus("add")


def toggle_item(item_id):
    """F-16 ~ F-18"""
    checked = st.session_state[f"check_{item_id}"]

    def change(items):
        for item in items:
            if item["id"] == item_id:
                item["checked"] = checked
        return items

    mutate(change)
    request_focus("check", item_id)


def start_edit(item_id):
    """F-09: 한 번에 하나의 아이템만 편집"""
    st.session_state["editing_id"] = item_id
    request_focus("edit", item_id)


def save_edit(item_id):
    """F-10, F-12: 빈 값이면 원래 이름 유지"""
    new_name = st.session_state.get(f"edit_{item_id}", "").strip()
    if new_name:

        def change(items):
            for item in items:
                if item["id"] == item_id:
                    item["name"] = new_name
            return items

        mutate(change)
    st.session_state["editing_id"] = None
    request_focus("edit_btn", item_id)


def cancel_edit(item_id):
    """F-11"""
    st.session_state["editing_id"] = None
    request_focus("edit_btn", item_id)


def delete_item(item_id):
    """F-13: 삭제 후 포커스는 다음(없으면 이전) 아이템의 삭제 버튼, 목록이 비면 입력창으로"""
    if st.session_state["editing_id"] == item_id:
        st.session_state["editing_id"] = None

    neighbor = {}

    def change(items):
        ids = [item["id"] for item in items]
        if item_id in ids:
            index = ids.index(item_id)
            rest = ids[:index] + ids[index + 1 :]
            if rest:
                neighbor["id"] = rest[min(index, len(rest) - 1)]
        return [item for item in items if item["id"] != item_id]

    mutate(change)
    if "id" in neighbor:
        request_focus("delete_btn", neighbor["id"])
    else:
        request_focus("add")


def clear_checked():
    """F-14"""
    mutate(lambda items: [item for item in items if not item["checked"]])
    request_focus("add")


# ---------------------------------------------------------
# 스타일 / 스크립트
# ---------------------------------------------------------
STYLE = """
<style>
h1 { word-break: keep-all; }
@media (max-width: 480px) {
  h1 { font-size: 1.75rem !important; }
}

/* 터치 영역 44px 이상 */
.stButton button,
[data-testid="stFormSubmitButton"] button { min-height: 44px; }
[data-baseweb="input"] { min-height: 44px; }
[class*="st-key-row-"] [data-testid="stCheckbox"] label {
  min-width: 44px;
  min-height: 44px;
  justify-content: center;
  align-items: center;
  margin: 0;
}

/* 아이템 행은 좁은 화면에서도 한 줄로 유지하고, 남는 폭은 이름(편집창)이 차지한다 */
[class*="st-key-row-"] [data-testid="stHorizontalBlock"] { flex-wrap: nowrap; }
[class*="st-key-row-"] [data-testid="stHorizontalBlock"] > [data-testid="stLayoutWrapper"],
[class*="st-key-row-"] [data-testid="stHorizontalBlock"] > [class*="st-key-edit_"]:not([class*="st-key-edit_btn_"]) {
  flex: 1 1 0;
  min-width: 0;
}

/* 아이템 이름: 마크다운 해석 없이 일반 글꼴로, 긴 텍스트는 줄바꿈 */
[class*="st-key-name_"] [data-testid="stText"],
[class*="st-key-name_"] [data-testid="stText"] * {
  font-family: inherit;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
[class*="st-key-name_"] { cursor: default; user-select: none; }

/* 체크된 아이템: 취소선 + 흐린 색상 (F-17) */
[class*="st-key-row-done-"] [data-testid="stText"] {
  text-decoration: line-through;
  color: rgba(49, 51, 63, 0.6);
}
</style>
"""

# 더블클릭으로 편집(F-09), Esc로 편집 취소(F-11). 문서 전체에 한 번만 등록한다.
GLOBAL_SCRIPT = """
<script>
(() => {
  if (window.__shoppingListBound) return;
  window.__shoppingListBound = true;

  const idFrom = (el, prefix) => {
    const cls = [...el.classList].find((c) => c.startsWith(prefix));
    return cls ? cls.slice(prefix.length) : null;
  };
  const clickButton = (selector) => {
    const button = document.querySelector(selector);
    if (button && !button.disabled) button.click();
  };

  document.addEventListener('dblclick', (event) => {
    const name = event.target.closest('[class*="st-key-name_"]');
    if (!name) return;
    const id = idFrom(name, 'st-key-name_');
    if (id) clickButton(`.st-key-edit_btn_${id} button`);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || event.isComposing) return;
    const field = event.target.closest('[class*="st-key-edit_"]');
    if (!field) return;
    const id = idFrom(field, 'st-key-edit_');
    if (!id) return;
    event.preventDefault();
    clickButton(`.st-key-cancel_${id} button`);
  }, true);
})();
</script>
"""

FOCUS_SELECTORS = {
    "add": ".st-key-new_item input",
    "check": ".st-key-check_{id} input",
    "edit": ".st-key-edit_{id} input",
    "edit_btn": ".st-key-edit_btn_{id} button",
    "delete_btn": ".st-key-delete_btn_{id} button",
}


def focus_script():
    """rerun으로 다시 그려진 뒤 지정한 요소에 포커스를 준다."""
    target = st.session_state.pop("focus", None)
    if not target:
        return ""
    kind, item_id = target
    selector = FOCUS_SELECTORS[kind].format(id=item_id or "")
    nonce = uuid.uuid4().hex  # 내용이 매번 달라야 스크립트가 다시 실행된다.
    return f"""
<script data-nonce="{nonce}">
(() => {{
  const selector = {json.dumps(selector)};
  const deadline = Date.now() + 3000;
  const tick = () => {{
    const el = document.querySelector(selector);
    if (el && !el.disabled) {{
      el.focus();
      if (el.type === 'text') el.select();
    }} else if (Date.now() < deadline) {{
      setTimeout(tick, 50);
    }}
  }};
  setTimeout(tick, 100);
}})();
</script>
"""


# ---------------------------------------------------------
# 화면
# ---------------------------------------------------------
@st.dialog("완료 항목 삭제")
def confirm_clear_dialog(count):
    """F-15: 일괄 삭제 전 확인"""
    st.write(f"완료한 항목 {count}개를 삭제할까요?")
    with st.container(horizontal=True):
        if st.button("취소", width="stretch"):
            st.rerun()
        if st.button("삭제", type="primary", width="stretch"):
            clear_checked()
            st.rerun()


def render_item(item, editing_id):
    item_id = item["id"]

    if editing_id == item_id:
        with st.container(border=True, key=f"row-edit-{item_id}"):
            # form 안의 입력창이라 Enter로 저장된다.
            with st.form(key=f"edit_form_{item_id}", border=False):
                with st.container(horizontal=True, vertical_alignment="center"):
                    st.text_input(
                        f"{item['name']} 이름 수정",
                        value=item["name"],
                        key=f"edit_{item_id}",
                        label_visibility="collapsed",
                        width="stretch",
                    )
                    st.form_submit_button(
                        "저장", type="primary", key=f"save_{item_id}", on_click=save_edit, args=(item_id,)
                    )
                    st.form_submit_button("취소", key=f"cancel_{item_id}", on_click=cancel_edit, args=(item_id,))
        return

    state = "done" if item["checked"] else "todo"
    with st.container(border=True, key=f"row-{state}-{item_id}"):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            # 체크박스 위젯 상태를 항상 파일 데이터와 맞춘다.
            st.session_state[f"check_{item_id}"] = item["checked"]
            st.checkbox(
                f"{item['name']} 구매 완료",
                key=f"check_{item_id}",
                on_change=toggle_item,
                args=(item_id,),
                label_visibility="collapsed",
            )
            # 이름은 마크다운으로 해석하지 않는 st.text로 표시한다.
            with st.container(key=f"name_{item_id}", width="stretch"):
                st.text(item["name"], width="stretch")
            # 다른 아이템을 편집 중일 때는 수정 버튼을 막아 편집 내용이 사라지지 않게 한다.
            st.button(
                "수정",
                key=f"edit_btn_{item_id}",
                on_click=start_edit,
                args=(item_id,),
                disabled=editing_id is not None,
            )
            st.button("삭제", key=f"delete_btn_{item_id}", on_click=delete_item, args=(item_id,))


def main():
    st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒", layout="centered")
    st.html(STYLE)
    st.html(GLOBAL_SCRIPT, unsafe_allow_javascript=True)

    st.session_state.setdefault("editing_id", None)
    items = load_items()
    if st.session_state["editing_id"] not in {item["id"] for item in items}:
        # 편집 중이던 아이템이 다른 탭에서 삭제된 경우
        st.session_state["editing_id"] = None
    editing_id = st.session_state["editing_id"]

    st.title("🛒 쇼핑 리스트")

    # 추가 영역: form이라 Enter로도 제출되고, 제출 후 입력창이 비워진다. (F-01, F-02)
    with st.form("add_form", clear_on_submit=True, border=False):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.text_input(
                "추가할 아이템",
                key="new_item",
                placeholder="아이템 입력...",
                label_visibility="collapsed",
                width="stretch",
            )
            st.form_submit_button("추가", type="primary", on_click=add_item)

    if not items:
        st.info("📝 쇼핑 리스트가 비어 있어요")  # F-07
    else:
        for item in items:
            render_item(item, editing_id)

    # 푸터: 남은 개수 + 완료 항목 삭제 (F-08, F-14)
    remaining = sum(1 for item in items if not item["checked"])
    checked_count = len(items) - remaining
    with st.container(horizontal=True, vertical_alignment="center", horizontal_alignment="distribute"):
        st.caption(f"{len(items)}개 중 {remaining}개 남음")
        if st.button("완료 항목 삭제", disabled=checked_count == 0):
            confirm_clear_dialog(checked_count)

    script = focus_script()
    if script:
        st.html(script, unsafe_allow_javascript=True)


main()
