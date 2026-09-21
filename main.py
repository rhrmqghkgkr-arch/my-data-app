import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ──────────────────────────────────────────────
# 기본 설정
# ──────────────────────────────────────────────
st.set_page_config(page_title="날짜별 박스오피스", page_icon="🎬", layout="wide")

API_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json"

# 한국 시간(KST) 기준의 '오늘'과 '어제' 날짜를 계산합니다.
# 배포 서버의 시계가 한국 시간이 아닐 수 있으므로, 반드시 시간대를 명시해서 계산합니다.
def get_today_kst_date():
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def get_yesterday_kst_date():
    return get_today_kst_date() - timedelta(days=1)


# 누적관객 100만 명 이상이면 영화명 옆에 붙일 트로피 이모지 기준값
TROPHY_THRESHOLD = 1_000_000


def format_rank_change(inten):
    """rankInten(전날 대비 순위 증감)을 색깔이 있는 화살표 HTML로 바꿔줍니다.
    양수(순위 상승) → 빨간 위 화살표, 음수(순위 하락) → 파란 아래 화살표, 0 → 변동 없음."""
    if pd.isna(inten) or int(inten) == 0:
        return '<span style="color:#888;">-</span>'
    inten = int(inten)
    if inten > 0:
        return f'<span style="color:red;">▲{inten}</span>'
    return f'<span style="color:blue;">▼{abs(inten)}</span>'


# ──────────────────────────────────────────────
# KOBIS API 호출 함수
# 같은 날짜(target_dt)로 다시 요청하면 1시간(3600초) 동안은
# API를 다시 호출하지 않고 캐시된 결과를 그대로 사용합니다.
# ──────────────────────────────────────────────
@st.cache_data(ttl=3600)
def fetch_box_office(target_dt: str):
    """
    성공 시: (True, DataFrame)
    실패 시: (False, "사용자에게 보여줄 한국어 안내 메시지")
    """
    # 인증키는 코드에 직접 쓰지 않고, Streamlit의 비밀 금고(secrets)에서 불러옵니다.
    # secrets 자체가 비어 있는 경우와, KOBIS_KEY 항목만 없는 경우를 구분해서 안내합니다.
    if "KOBIS_KEY" not in st.secrets:
        return False, (
            "인증키(KOBIS_KEY)를 찾을 수 없습니다. Streamlit Cloud 앱 화면 오른쪽 아래 "
            "'Manage app' → 점 세 개 메뉴 → 'Settings' → 'Secrets'로 들어가서 다음과 같이 "
            "정확히 등록했는지 확인해 주세요 (따옴표 포함):\n\n"
            'KOBIS_KEY = "발급받은_인증키"\n\n'
            "등록/수정 후에는 저장 버튼을 누르고 앱이 자동으로 재시작될 때까지 잠시 기다려 주세요. "
            "재시작이 안 되면 'Manage app' 메뉴에서 'Reboot app'을 눌러 주세요."
        )

    api_key = st.secrets["KOBIS_KEY"]
    if not str(api_key).strip():
        return False, "KOBIS_KEY 값이 비어 있습니다. Secrets 메뉴에서 인증키 값을 다시 입력해 주세요."

    params = {"key": api_key, "targetDt": target_dt}

    # 1) 네트워크 요청 자체가 실패하는 경우 (인터넷 문제, 서버 다운 등)
    try:
        response = requests.get(API_URL, params=params, timeout=10)
    except requests.exceptions.RequestException as e:
        return False, f"박스오피스 정보를 요청하는 중 오류가 발생했습니다. 인터넷 연결 상태를 확인해 주세요. (상세: {e})"

    # 2) 상태 코드가 200이 아닌 경우 (서버 오류 등)
    if response.status_code != 200:
        return False, f"KOBIS 서버가 정상적으로 응답하지 않았습니다. (상태 코드: {response.status_code}) 잠시 후 다시 시도해 주세요."

    # 3) 응답이 JSON 형식이 아닌 경우
    try:
        data = response.json()
    except ValueError:
        return False, "서버 응답을 해석할 수 없습니다. KOBIS 서버 상태를 확인하거나 잠시 후 다시 시도해 주세요."

    # 4) 인증키가 틀린 경우: 상태코드는 200이지만 faultInfo 상자가 옵니다.
    if "faultInfo" in data:
        message = data["faultInfo"].get("message", "알 수 없는 오류")
        return False, (
            f"KOBIS API에서 오류를 반환했습니다: {message}\n"
            "인증키(KOBIS_KEY)가 올바른지, 그리고 하루 호출 한도를 넘기지 않았는지 확인해 주세요."
        )

    # 5) 정상 응답이지만 필요한 구조가 없는 경우
    box_office_result = data.get("boxOfficeResult")
    if not box_office_result:
        return False, "응답 형식이 예상과 다릅니다. targetDt 값(8자리 날짜)이 올바른지 확인해 주세요."

    movie_list = box_office_result.get("dailyBoxOfficeList")

    # 6) 영화 목록이 비어 있는 경우 (예: 아직 집계되지 않은 날짜를 조회한 경우)
    if not movie_list:
        return False, "그날은 아직 집계 전입니다."

    df = pd.DataFrame(movie_list)

    # 숫자 값이 전부 문자열로 오기 때문에, 정렬과 그래프에 쓰기 위해 숫자로 변환합니다.
    numeric_cols = ["rank", "rankInten", "audiCnt", "audiAcc", "scrnCnt", "showCnt"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values("rank").reset_index(drop=True)
    return True, df


# ──────────────────────────────────────────────
# 화면 구성
# ──────────────────────────────────────────────
st.title("🎬 날짜별 박스오피스")

yesterday_kst = get_yesterday_kst_date()

# 달력에서 날짜를 고를 수 있게 합니다. 오늘 건 아직 집계 전이므로
# 고를 수 있는 가장 늦은 날짜는 '어제(한국 시간 기준)'로 제한합니다.
selected_date = st.date_input(
    "조회할 날짜를 선택하세요",
    value=yesterday_kst,
    max_value=yesterday_kst,
)

target_dt = selected_date.strftime("%Y%m%d")
display_date = f"{target_dt[:4]}년 {target_dt[4:6]}월 {target_dt[6:]}일"
st.caption(f"조회 날짜: {display_date}")

ok, result = fetch_box_office(target_dt)

if not ok:
    # 실패/오류/빈 목록인 경우: 빈 화면 대신 무엇을 확인해야 하는지 안내합니다.
    st.error(result)
else:
    df = result

    # ── 1위 영화: 지표 카드 세 장으로 크게 표시 ──
    top_movie = df.iloc[0]
    st.subheader(f"🥇 1위: {top_movie['movieNm']}")

    col1, col2, col3 = st.columns(3)
    col1.metric("오늘 관객수", f"{int(top_movie['audiCnt']):,}명")
    col2.metric("누적 관객수", f"{int(top_movie['audiAcc']):,}명")
    col3.metric("스크린수", f"{int(top_movie['scrnCnt']):,}개")

    st.divider()

    # ── 전체 순위 표 ──
    # 색깔 화살표와 트로피 이모지를 붙이기 위해 직접 HTML 표로 만듭니다.
    st.subheader("📋 전체 순위")

    rows_html = ""
    for _, row in df.iterrows():
        movie_name = row["movieNm"]
        # 누적관객 100만 명 이상이면 영화명 옆에 트로피 이모지를 붙입니다.
        if pd.notna(row.get("audiAcc")) and row["audiAcc"] >= TROPHY_THRESHOLD:
            movie_name = f"{movie_name} 🏆"

        rank_change = format_rank_change(row.get("rankInten"))

        rows_html += (
            "<tr style='border-bottom:1px solid #eee;'>"
            f"<td style='text-align:center; padding:6px;'>{int(row['rank'])}</td>"
            f"<td style='padding:6px;'>{movie_name}</td>"
            f"<td style='text-align:center; padding:6px;'>{rank_change}</td>"
            f"<td style='text-align:center; padding:6px;'>{row.get('openDt', '')}</td>"
            f"<td style='text-align:right; padding:6px;'>{int(row['audiCnt']):,}</td>"
            f"<td style='text-align:right; padding:6px;'>{int(row['audiAcc']):,}</td>"
            f"<td style='text-align:right; padding:6px;'>{int(row['scrnCnt']):,}</td>"
            "</tr>"
        )

    table_html = f"""
    <table style="width:100%; border-collapse:collapse;">
        <thead>
            <tr style="border-bottom:2px solid #ddd;">
                <th style="text-align:center; padding:6px;">순위</th>
                <th style="text-align:left; padding:6px;">영화명</th>
                <th style="text-align:center; padding:6px;">전일대비</th>
                <th style="text-align:center; padding:6px;">개봉일</th>
                <th style="text-align:right; padding:6px;">관객수</th>
                <th style="text-align:right; padding:6px;">누적관객</th>
                <th style="text-align:right; padding:6px;">스크린수</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    """
    st.markdown(table_html, unsafe_allow_html=True)
    st.caption("전일대비: 🔺빨강 = 순위 상승 · 🔻파랑 = 순위 하락 · 🏆 = 누적관객 100만 명 이상")

    st.divider()

    # ── 관객수 상위 5편 막대그래프 ──
    st.subheader("📊 관객수 상위 5편")
    top5 = df.sort_values("audiCnt", ascending=False).head(5)
    chart_data = top5.set_index("movieNm")["audiCnt"]
    st.bar_chart(chart_data)
