import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ──────────────────────────────────────────────
# 기본 설정
# ──────────────────────────────────────────────
st.set_page_config(page_title="어제의 박스오피스", page_icon="🎬", layout="wide")

API_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json"

# 한국 시간(KST) 기준으로 '어제' 날짜를 계산합니다.
# 배포 서버의 시계가 한국 시간이 아닐 수 있으므로, 반드시 시간대를 명시해서 계산합니다.
def get_yesterday_kst() -> str:
    kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
    yesterday = kst_now - timedelta(days=1)
    return yesterday.strftime("%Y%m%d")  # yyyymmdd 형식


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
        return False, (
            "해당 날짜의 박스오피스 데이터가 없습니다. "
            "아직 집계가 완료되지 않았을 수 있으니 잠시 후 다시 시도해 주세요."
        )

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
st.title("🎬 어제의 박스오피스")

target_dt = get_yesterday_kst()
display_date = f"{target_dt[:4]}년 {target_dt[4:6]}월 {target_dt[6:]}일"
st.caption(f"조회 날짜: {display_date} (한국 시간 기준 어제)")

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
    st.subheader("📋 전체 순위")
    table_df = df[["rank", "movieNm", "openDt", "audiCnt", "audiAcc", "scrnCnt"]].copy()
    table_df.columns = ["순위", "영화명", "개봉일", "관객수", "누적관객", "스크린수"]
    st.dataframe(table_df, use_container_width=True, hide_index=True)

    st.divider()

    # ── 관객수 상위 5편 막대그래프 ──
    st.subheader("📊 관객수 상위 5편")
    top5 = df.sort_values("audiCnt", ascending=False).head(5)
    chart_data = top5.set_index("movieNm")["audiCnt"]
    st.bar_chart(chart_data)
