from pathlib import Path
import requests
from datetime import datetime
from bidict import bidict
import pandas as pd
import streamlit as st

from dotenv import load_dotenv
import os

OPINET_API_BASE_URL = "http://www.opinet.co.kr/api"
KAKAO_API_BASE_URL = "https://dapi.kakao.com/v2/local"

def _require_opinet_key() -> str:
    project_root = Path(__file__).resolve().parent
    env_path = project_root / ".env"
    load_dotenv(dotenv_path=env_path, override=True)
    key = os.getenv("OPINET_API_KEY")
    if not key:
        raise RuntimeError("OPINET API키를 찾을 수 없습니다.")
    return key

def _require_kakao_rest_key() -> str:
    project_root = Path(__file__).resolve().parent
    env_path = project_root / ".env"
    load_dotenv(dotenv_path=env_path, override=True)
    key = os.getenv("KAKAO_REST_KEY")
    if not key:
        raise RuntimeError("KAKAO REST API키를 찾을 수 없습니다.")
    return key

def get_opinet_oil_code() -> dict:
    oil_dict = {
        "B027": "휘발유",
        "D047": "경유",
        "K015": "LPG",
        "B034": "고급휘발유",
        "C004": "등유"
    }
    return oil_dict

def get_opinet_region_info() -> dict:
    region_dict = {
        "서울": "서울특별시",
        "경기": "경기도",
        "강원": "강원도",
        "충북": "충청북도",
        "충남": "충청남도",
        "전북": "전라북도",
        "전남": "전라남도",
        "경북": "경상북도",
        "경남": "경상남도",
        "부산": "부산광역시",
        "제주": "제주특별자치도",
        "대구": "대구광역시",
        "인천": "인천광역시",
        "광주": "광주광역시",
        "대전": "대전광역시",
        "울산": "울산광역시",
        "세종": "세종특별자치시"
    }
    return region_dict

@st.cache_data(show_spinner=False)
def get_opinet_region_code() -> dict:
    """
    지역 코드 반환
    ex) 01: 서울 => 01: 서울특별시
    """
    url = f"{OPINET_API_BASE_URL}/areaCode.do"
    params = {
        "out": "json",
        "code": _require_opinet_key()
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise("get_opinet_region_code() ERROR: ", e)

    region_code_dict = {}
    oils = response.json()["RESULT"]["OIL"]
    for area in oils:
        region_code_dict[area["AREA_CD"]] = get_opinet_region_info().get(area["AREA_NM"])
    return region_code_dict

def get_opinet_station_code() -> dict:
    station_dict = {
        "SKE": "SK에너지",
        "GSC": "GS칼텍스",
        "HDO": "현대오일뱅크",
        "SOL": "S-OIL",
        "RTO": "알뜰주유소(전체)",
        "RTE": "알뜰주유소(자영)",
        "RTX": "알뜰주유소(고속)",
        "NHO": "알뜰주유소(농협)",
        "ETC": "자가상표",
        "E1G": "E1",
        "SKG": "SK가스"
    }
    return station_dict

@st.cache_data(show_spinner=False)
def avg_price_all() -> list[dict]:
    """전국 주유소 평균가격 조회"""
    url = f"{OPINET_API_BASE_URL}/avgAllPrice.do"
    params = {
        "out": "json",
        "code": _require_opinet_key()
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()  # 상태코드 200대가 아니면 HTTPError 발생
    except requests.exceptions.RequestException as e:
        raise("avg_price_all() ERROR: ", e)

    oils = response.json()["RESULT"]["OIL"]
    for oil in oils:
        oil["PRODNM"] = get_opinet_oil_code().get((oil["PRODCD"]))
        oil["PRICE"] = float(oil["PRICE"])
        oil["DIFF"] = float(oil["DIFF"])
        oil["TRADE_DT"] = datetime.strptime(oil["TRADE_DT"], "%Y%m%d").date()
    return oils

@st.cache_data(show_spinner=False)
def avg_price_sido() -> list[dict]:
    """시도별 주유소 평균가격 조회"""
    url = f"{OPINET_API_BASE_URL}/avgSidoPrice.do"
    params = {
        "out": "json",
        "code": _require_opinet_key()
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()  # 상태코드 200대가 아니면 HTTPError 발생
    except requests.exceptions.RequestException as e:
        raise("avg_price_sido() ERROR: ", e)

    with open("./gisdata/ctprvn_centers.csv", "r", encoding="utf-8") as f:
        df = pd.read_csv(f)

    oils = response.json()["RESULT"]["OIL"]
    for oil in oils:
        oil["PRODCD"] = get_opinet_oil_code().get(oil["PRODCD"])
        oil["PRICE"] = float(oil["PRICE"])
        oil["DIFF"] = float(oil["DIFF"])
        oil["SIDONM"] = get_opinet_region_info().get(oil["SIDONM"], oil["SIDONM"])
        match = df[df["CTP_KOR_NM"] == str(oil["SIDONM"])] # 데이터프레임
        if not match.empty:
            oil["lon"] = float(match["lon"].values[0])
            oil["lat"] = float(match["lat"].values[0])
        else:
            oil["lon"] = 0
            oil["lat"] = 0
    return oils

@st.cache_data(show_spinner=False)
def avg_price_sido_period_search(region: str,
                                 oil: str,
                                 day: datetime) -> list[dict]:
    """기준일(작일부터 조회가능)로부터 이전 7일간 지역별 주유소 평균가격 조회"""
    r = bidict(get_opinet_region_code())
    o = bidict(get_opinet_oil_code())
    url = f"{OPINET_API_BASE_URL}/dateAreaAvgRecentPrice.do"
    params = {
        "out": "json",
        "code": _require_opinet_key(),
        "area": r.inv[region],
        "prodcd": o.inv[oil],
        "date": day
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()  # 상태코드 200대가 아니면 HTTPError 발생
    except requests.exceptions.RequestException as e:
        raise("avg_price_sido_period_search() ERROR: ", e)

    oils = response.json()["RESULT"]["OIL"]
    for area in oils:
        area["AREA_NM"] = get_opinet_region_info().get(area["AREA_NM"])
        area["PRODCD"] = get_opinet_oil_code().get(area["PRODCD"])
    return oils

@st.cache_data(show_spinner=False)
def avg_price_all_period_search(oil: str, day: datetime) -> list[dict]:
    """기준일(작일부터 조회가능)로부터 이전 7일간 전국 주유소 평균가격 조회"""
    o = bidict(get_opinet_oil_code())
    url = f"{OPINET_API_BASE_URL}/dateAvgRecentPrice.do"
    params = {
        "out": "json",
        "code": _require_opinet_key(),
        "prodcd": o.inv[oil],
        "date": day
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()  # 상태코드 200대가 아니면 HTTPError 발생
    except requests.exceptions.RequestException as e:
        raise("avg_price_all_period_search() ERROR: ", e)

    oils = response.json()["RESULT"]["OIL"]
    for area in oils:
        area["AREA_NM"] = "전국"
        area["PRODCD"] = get_opinet_oil_code().get(area["PRODCD"])
    return oils

@st.cache_data(show_spinner=False)
def katec_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """(카카오맵 API) 좌표계 변환 KATEC => WGS84"""
    url = f"{KAKAO_API_BASE_URL}/geo/transcoord.json"
    headers = {"Authorization": "KakaoAK " + _require_kakao_rest_key()}
    params = {
        "x": x,
        "y": y,
        "input_coord": "KTM",
        "output_coord": "WGS84"
    }
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise("katec_to_wgs84() ERROR: ", e)

    gis = response.json().get("documents", [])
    if gis:
        return gis[0]["x"], gis[0]["y"]
    else:
        return None

@st.cache_data(show_spinner=False)
def wgs84_to_katec(x: float, y: float) -> tuple[float, float]:
    """(카카오맵 API) 좌표계 변환 WGS84 => KATEC"""
    url = f"{KAKAO_API_BASE_URL}/geo/transcoord.json"
    headers = {"Authorization": "KakaoAK " + _require_kakao_rest_key()}
    params = {
        "x": x,
        "y": y,
        "input_coord": "WGS84",
        "output_coord": "KTM"
    }
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise("wgs84_to_katec() ERROR: ", e)

    gis = response.json().get("documents", [])
    if gis:
        return gis[0]["x"], gis[0]["y"]
    else:
        return None

@st.cache_data(show_spinner=False)
def around_station_search(lon: float,
                          lat: float,
                          radius: int,
                          oil_type: str,
                          sort: int) -> tuple[list[dict], bool]:
    """
    위치 반경내 주유소 검색
    카카오맵 API로 좌표게 변환 WGS84 => KATEC
    KATEC으로 변환된 좌표계로 오피넷 API '내 주변 주유소 검색'
    반환된 경도, 위도 좌표값 (KATEC)
    카카오맵 API로 좌표계 변환 KATEC => WGS84
    반경내 검색된 주유소가 있으면 데이터와 True 반환하고 없으면 False 반환
    """
    o = bidict(get_opinet_oil_code())
    k_lon, k_lat = wgs84_to_katec(lon, lat)
    url = f"{OPINET_API_BASE_URL}/aroundAll.do"
    params = {
        "out": "json",
        "code": _require_opinet_key(),
        "x": k_lon,
        "y": k_lat,
        "radius": radius,
        "prodcd": o.inv[oil_type],
        "sort": sort
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise ("around_station_search() ERROR: ", e)

    oils = response.json()["RESULT"]["OIL"]
    if oils:
        for oil in oils:
            g_lon, g_lat = katec_to_wgs84(oil["GIS_X_COOR"], oil["GIS_Y_COOR"])
            oil["LON_WGS84"] = float(g_lon)
            oil["LAT_WGS84"] = float(g_lat)
            oil["POLL_DIV_CD"] = get_opinet_station_code().get(oil["POLL_DIV_CD"], oil["POLL_DIV_CD"])
            oil["PRODCD"] = oil_type
        return oils, True
    else:
        return oils, False

@st.cache_data(show_spinner=False)
def address_to_gis(addr: str) -> tuple[float, float]:
    """(카카오맵 API) 주소로 WGS84 좌표계 반환 잘못된 주소로 인해 좌표값이 없을 경우 None 반환"""
    url = f"{KAKAO_API_BASE_URL}/search/address.json"
    headers = {"Authorization": "KakaoAK " + _require_kakao_rest_key()}
    params = {
        "query": addr
    }
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise("addr_to_gis() ERROR: ", e)

    gis = response.json().get("documents", [])
    if gis:
        return gis[0]["x"], gis[0]["y"]
    else:
        return None

@st.cache_data(show_spinner=False)
def station_info_search(station_id: str) -> list[dict]:
    """
    (AI가 사용할 함수) 주유소 ID로 주유소 상세 검색
    주소, 기름가격, 전화번호, 세차장, 편의점, 경정비 시설 유무 등등...
    """
    print("##### AI 함수 호출 #####")
    url = f"{OPINET_API_BASE_URL}/detailById.do"
    params = {
        "out": "json",
        "code": _require_opinet_key(),
        "id": station_id
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise("station_info_search() ERROR: ", e)

    oils = response.json()["RESULT"]["OIL"]
    for station in oils:
        station["POLL_DIV_CO"] = get_opinet_station_code().get(station["POLL_DIV_CO"], station["POLL_DIV_CO"])
        station["GPOLL_DIV_CO"] = get_opinet_station_code().get(station["GPOLL_DIV_CO"], station["GPOLL_DIV_CO"])
        for oil in station["OIL_PRICE"]:
            oil["PRODCD"] = get_opinet_oil_code().get(oil["PRODCD"], oil["PRODCD"])
    return oils

# tools = [
#     {"type": "function",
#         "function": {
#             "name": "station_info_search",
#             "description": "해당 주유소의 주소, 기름가격, 전화번호, 세차장, 편의점, 경정비 시설 유무 등등을 반환합니다",
#             "parameters": {
#                 "type": "object",
#                 "properties": {
#                     "station_id": {
#                         "type": "string",
#                         "description": "주유소의 아이디를 입력하세요. 예) A0011826"
#                     }
#                 },
#                 "required": ["station_id"]
#             }
#         }
#     }
# ]