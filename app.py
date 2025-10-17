# from streamlit_folium import st_folium
# from streamlit_js_eval import get_geolocation
import os

from streamlit.components.v1 import html
import folium
import plotly.graph_objects as go
import plotly.express as px
from datetime import date, timedelta
import math
import json

from func import *
from llm import run_pipeline, run_agent

st.set_page_config("유가 조회",
                   page_icon="📊")

# 페이지 레이아웃 설정
# st.markdown("""
#     <style>
#         .block-container {
#             max-width: 900px;
#         }
#     </style>
# """, unsafe_allow_html=True)

st.title("유가 정보 통합조회")


# --------------------------------------------


if "news" not in st.session_state:
    st.session_state["news"] = None
if "news_btn_run_lock" not in st.session_state:
    st.session_state["news_btn_run_lock"] = False

news_btn = st.button("AI뉴스 받아보기", type="primary", disabled=st.session_state["news_btn_run_lock"])
if news_btn and st.session_state["news"] is None:
    with st.spinner("1~2분정도 소요됩니다..."):
        if os.getenv("OPENAI_API_KEY"):
            result = run_pipeline(rss="구글뉴스",
                                  max_items_per_feed=20,
                                  k=8,
                                  lookback_days=5)
            st.session_state["news"] = result
            st.session_state["news_btn_run_lock"] = True
        else:
            st.session_state["news"] = "**OPEN API KEY를 확인해주세요**"
        st.rerun()
if st.session_state["news"]:
    st.markdown(st.session_state["news"])


# --------------------------------------------


st.subheader(f"전국 평균 유가 정보 ({date.today()})")

oil_order = ["휘발유", "경유", "LPG", "고급휘발유", "등유"]
palette = px.colors.qualitative.D3
colors = [palette[i % len(palette)] for i in range(len(oil_order))]

if oils_today := avg_price_all():
    oils_today.sort(key = lambda x: oil_order.index(x["PRODNM"]))
    df = pd.DataFrame(oils_today)
    df["DIFF_fmt"] = df["DIFF"].astype(float).map(lambda x: f"{x:+.2f}")

    cols = st.columns(3)
    for i, oil in enumerate(oils_today):
        with cols[i % 3]:
            st.metric(
                label=oil["PRODNM"],
                value=f'{oil["PRICE"]:,}원',
                delta=f'{oil["DIFF"]:+.2f}원',
                delta_color="inverse"
            )

    fig = go.Figure(go.Bar( # 오늘 전국 평균 유가 (막대 그래프)
        x=df["PRODNM"],
        y=df["PRICE"],
        text=df["PRICE"].astype(int),
        texttemplate="%{text:.0f}원",
        customdata=df[["DIFF_fmt"]],
        hovertemplate=
            "<b>%{x}</b><br>" +
            "%{y:.2f}원<br>" +
            "(%{customdata:+.2f}원)<extra></extra>",
        marker_color = colors
    ))
    fig.update_layout(
        title="전국 평균",
        xaxis_title="유종",
        yaxis_title="가격",
        bargap = 0.4
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.warning("API로부터 데이터를 불러 올 수 없습니다.")


st.divider() # --------------------------------------------


st.subheader("시도별 평균 가격")

@st.fragment
# 해당 함수 부분만 rerun() 되게 함으로서
# selectbox에서 값 선택시 사이드바에서 검색한 그래프에 영향을 주지 않음
def show_choropleth():
    selected_oil = st.selectbox("유종을 선택해주세요", oil_order, index=0)

    if oils_sido := avg_price_sido():
        oils_sido = [oil for oil in oils_sido if oil["PRODCD"]==selected_oil]
        df = pd.DataFrame(oils_sido)

        with open("./gisdata/TL_SCCO_CTPRVN.json", "r", encoding="utf-8") as f:
            geo = json.load(f)

        m = folium.Map(location=[36.5, 127.8], zoom_start=7) # 지도맵 보여지는 시작 위치 : 대한민국 한반도 좌표값
        folium.Choropleth(
            geo_data=geo,
            data=df,
            columns=["SIDONM", "PRICE"],
            key_on="feature.properties.CTP_KOR_NM",
            fill_color="YlOrRd",
            legend_name=f"{selected_oil}"
        ).add_to(m)

        for i, row in df.iterrows(): # 가격 값을 마커로 지도 위에 표시
            folium.Marker(
                location=[row["lat"], row["lon"]],
                icon=folium.DivIcon(html=f"""<div style="font-size: 9pt; color: black; font-weight:bold">{row['PRICE']}</div>""")
            ).add_to(m)
        html(f"""
            <div style="display:flex; justify-content:center; width:100%;">
                <div style="width:100%;">{m._repr_html_()}</div>
            </div>
            """, height=435)
        # st_folium(m, height=600)
        # st.components.v1.html(m._repr_html_(), height=600)
        df = df.loc[1:, ["SIDONM", "PRICE", "DIFF", "PRODCD"]]
        df.columns = ["지역", "가격", "전일대비", "유종"]
        st.dataframe(df, width="stretch")
    else:
        st.warning("API로부터 데이터를 불러 올 수 없습니다.")
show_choropleth()


# --------------------------------------------
# --------------화면 좌측 패널 시작--------------
# --------------------------------------------


max_region = 2
max_period = 30
max_day = date.today() - timedelta(days=1) # 작일까지 API에서 데이터 조회가능

if "period_search_state" not in st.session_state: # 세션 초기화 (유가 변동 검색)
    st.session_state["period_search_state"] = {
        "submit": False,
        "regions": [],
        "oil": None,
        "start_date": None,
        "end_date": None,
        "dataframe": None,
    }

with st.sidebar.form("period_search_sidebar"): # 화면 좌측 패널 (유가 변동)
    st.subheader("📈 유가 변동 조회")

    selected_regions = st.multiselect(f"지역 (최대 {max_region}개)",
                                     ["전국"] + list(get_opinet_region_info().values()),
                                     placeholder="지역 선택",
                                     max_selections=max_region,
                                     key="regions_box_period")
    selected_oil_period = st.selectbox("유종",
                                        oil_order,
                                        placeholder="유종을 선택해주세요.",
                                        key="oil_box_period")
    start_date_btn = st.date_input(label=f"조회 기간 (최대 {max_period}일)",
                                   value=max_day-timedelta(days=6),
                                   max_value=max_day,
                                   key="start_date_btn")
    end_date_btn = st.date_input(label="조회 기간",
                                 label_visibility="collapsed",
                                 value=max_day,
                                 max_value=max_day,
                                 key="end_date_btn")
    period_btn = st.form_submit_button("검색", type="primary", key="period_search_btn")
    # st.caption("※작일까지 조회가능하며 조회결과는 페이지 하단을 참조하세요.")
    st.markdown(
        """
        <p style="font-size:12px; color:#31333f99; font-weight:500;">
        작일까지 조회가능하며 조회결과는 페이지 하단을 참조하세요.
        </p>
        """, unsafe_allow_html=True
    )


# --------------------------------------------


# if "client_loc" not in st.session_state:
#     st.session_state["client_loc"] = None
#
# if st.session_state["client_loc"] is None: # 세션 초기화 (주유소 검색을 위한 위치 권한 묻기)
#     loc = get_geolocation()
#     if loc and isinstance(loc, dict) and "coords" in loc:
#         st.session_state["client_loc"] = {
#             "lon": loc["coords"]["longitude"],
#             "lat": loc["coords"]["latitude"]
#         }

if "station_search_state" not in st.session_state: # 세션 초기화 (주유소 검색)
    st.session_state["station_search_state"] = {
        "submit": False,
        "lon": None,
        "lat": None,
        "radius": None,
        "oil": None,
        "sort": None,
        "dataframe": None
    }

with st.sidebar.form("station_search_sidebar"):  # 화면 좌측 패널 (주유소)
    st.subheader("⛽ 주변 주유소 검색")

    st.markdown(
        """
        <style>
        div[data-testid="stTextInput"] input, input::placeholder {
            font-size: 13px;
        }
        </style>
        """, unsafe_allow_html=True
    )
    addr_text = st.text_input(label="주소", placeholder="도로명 또는 지번 주소로 검색")
    radius_slider = st.slider("반경(m)", 100, 5000, 2000, 100)
    selected_oil_station = st.selectbox("유종",
                                        oil_order,
                                        placeholder="유종을 선택해주세요.",
                                        key="oil_box_station_search")
    sort_radio = st.radio("정렬", [1, 2], format_func=lambda x: "가격순" if x == 1 else "거리순")
    station_addr_btn = st.form_submit_button("검색", type="primary", key="station_addr_btn")
    # station_nearby_btn = st.form_submit_button("내 주변 검색", type="primary", key="station_nearby_btn")


# --------------------------------------------
# --------------화면 좌측 패널 끝남--------------
# --------------------------------------------


if period_btn: # 유가 변동 "검색" 버튼 눌렀을 때
    st.session_state["period_search_state"]["dataframe"] = None
    if not selected_regions:
        st.warning("지역을 선택해주세요.")
    elif not selected_oil_period:
        st.warning("유종을 선택해주세요.")
    elif start_date_btn > end_date_btn or start_date_btn == end_date_btn:
        st.warning("조회하려는 날짜를 다시 한번 확인해주세요.")
    elif (end_date_btn - start_date_btn).days + 1 > max_period:
        st.warning(f"기간 조회는 최대 {max_period}일입니다.")
    else: # 검색 조건 충족시
        st.session_state["period_search_state"] = {
            "submit": True,
            "regions": selected_regions,
            "oil": selected_oil_period,
            "start_date": start_date_btn,
            "end_date": end_date_btn,
        }
        with st.spinner("유가 정보 조회중..."):
            api_once_call_limit = 7 # 오피넷API 호출시 최대 7일까지 조회가능
            period = (end_date_btn - start_date_btn).days + 1
            repeat = math.ceil(period / api_once_call_limit)

            n = 0
            rows: list[dict] = []
            while n < repeat: # 조회기간이 7일 이상일시 7일단위로 나눠서 API호출
                search_day = end_date_btn - timedelta(days=n*api_once_call_limit) # 검색날짜기준 이전 7일까지 조회가능
                for region in selected_regions:
                    if region == "전국":
                        row = avg_price_all_period_search(selected_oil_period, search_day)
                    else:
                        row = avg_price_sido_period_search(region, selected_oil_period, search_day)
                    rows.extend(row)
                n += 1

            df = pd.DataFrame(rows)
            df["DATE"] = pd.to_datetime(df["DATE"].astype(str), format="%Y%m%d") # 데이터프레임 타입 변환 obj => datetime
            df = df.loc[df["DATE"].dt.date.between(start_date_btn, end_date_btn)]
            df = df.sort_values(by=["DATE", "AREA_NM"])
            df["DATE"] = df["DATE"].dt.strftime("%Y-%m-%d") # 출력 포맷 변환
            st.session_state["period_search_state"]["dataframe"] = df


# --------------------------------------------


session = st.session_state["period_search_state"] # 유가 변동 그래프 출력
if session["submit"] and session["dataframe"] is not None:
    # streamlit rerun시 그래프 사라짐 문제 방지 (*세션에서 값을 가져와서 그래프 재출력)
    st.divider()  # --------------------------------------------

    oil = session["oil"]
    start_date = session["start_date"]
    end_date = session["end_date"]
    df = session["dataframe"]
    df.rename(columns={
        "DATE": "날짜",
        "AREA_NM": "지역",
        "PRICE": "가격",
        "PRODCD": "유종"
    }, inplace=True)
    df = df[["날짜", "지역", "가격", "유종"]]

    st.subheader(oil + " 평균가격 변동 추이")
    st.text("조회 기간 : " + str(start_date) + " ~ " + str(end_date))

    fig = px.line(
        df.sort_values("날짜"),
        x="날짜",
        y="가격",
        color="지역",
        custom_data=["지역"],
        labels={"지역": ''},
        markers=True
    )
    fig.update_traces(hovertemplate="%{y:.2f}원 (%{customdata[0]})<extra></extra>")
    # fig.update_traces(marker=dict(size=6, symbol="circle"))
    fig.update_xaxes(tickformat="%m-%d (%a)")
    fig.update_layout(xaxis_title=None, yaxis_title=None)

    st.plotly_chart(fig, use_container_width=True)
    df.index = pd.RangeIndex(1, len(df)+1)
    st.dataframe(df)


# --------------------------------------------


if station_addr_btn: # 주유소 검색 "검색" 버튼 눌렀을 때
    st.session_state["station_search_state"]["submit"] = False
    if not addr_text or not addr_text.strip():
        st.warning("주소를 입력해주세요.")
    else:
        gis = address_to_gis(addr_text)
        if not gis:
            st.warning("입력하신 주소가 유효하지 않습니다. 주소를 다시 입력해주세요.")
        else: # 검색 조건 충족시
            lon, lat = gis
            st.session_state["station_search_state"] = {
                "submit": True,
                "lon": lon,
                "lat": lat,
                "radius": radius_slider,
                "oil": selected_oil_station,
                "sort": sort_radio
            }
            with st.spinner("검색한 주소 반경내 주유소 조회중..."):
                station, result = around_station_search(lon,
                                                        lat,
                                                        radius_slider,
                                                        selected_oil_station,
                                                        sort_radio)
                if result:
                    st.session_state["station_search_state"]["dataframe"] = pd.DataFrame(station)
                else:
                    st.session_state["station_search_state"]["dataframe"] = None

# if station_nearby_btn: # 주유소 검색 "내 주변 검색" 버튼 눌렀을 때
#     st.session_state["station_search_state"]["submit"] = False
#     if not st.session_state.get("client_loc"):
#         st.warning("브라우저 위치 권한을 허용해주세요. 허용 후 버튼을 다시 눌러주세요.")
#     else: # 검색 조건 충족시
#         st.session_state["station_search_state"] = {
#             "submit": True,
#             "lon": st.session_state["client_loc"]["lon"],
#             "lat": st.session_state["client_loc"]["lat"],
#             "radius": radius_slider,
#             "oil": selected_oil_station,
#             "sort": sort_radio
#         }
#         with st.spinner("내 주변 반경내 주유소 조회중..."):
#             station, result = around_station_search(st.session_state["client_loc"]["lon"],
#                                                     st.session_state["client_loc"]["lat"],
#                                                     radius_slider,
#                                                     selected_oil_station,
#                                                     sort_radio)
#             if result:
#                 st.session_state["station_search_state"]["dataframe"] = pd.DataFrame(station)
#             else:
#                 st.session_state["station_search_state"]["dataframe"] = None


# --------------------------------------------


session = st.session_state["station_search_state"] # 주유소 검색 카카오 지도맵 출력
if "rec" not in session:
    session["rec"] = None
if "rec_btn_run_lock" not in session:
    session["rec_btn_run_lock"] = False
if session["submit"] and session["dataframe"] is not None:
    # streamlit rerun시 그래프 사라짐 문제 방지 (*세션에서 값을 가져와서 그래프 재출력)
    st.divider()  # --------------------------------------------

    lon, lat = session["lon"], session["lat"]
    radius = session["radius"]
    sort = session["sort"]
    df = session["dataframe"]
    data_json = df.to_json(orient="records", force_ascii=False)

    st.subheader("반경 " + str(radius) + "m 주유소 조회")

    project_root = Path(__file__).resolve().parent
    env_path = project_root / ".env"
    load_dotenv(dotenv_path=env_path, override=True)
    kakao_key = os.getenv("KAKAO_JS_KEY")

    if not kakao_key:
        st.warning("KAKAOMAP JS API KEY를 확인해주세요.")
    else:
        html(f"""
            <!doctype html>
            <html>
            <head>
                <meta charset="utf-8" />
                <meta name="viewport" content="initial-scale=1, width=device-width" />
                <style>
                    body {{ margin:0; padding:0; }}
                    #wrap {{ width:100%; height:660px; }}
                    #map {{ width:100%; height:100%; margin:0 auto; }}
                    .label {{ padding:4px 8px; background:#fff; font-size:14px; }}
                </style>
                <script src="https://dapi.kakao.com/v2/maps/sdk.js?appkey={kakao_key}&autoload=false"></script>
            </head>
            <body>
                <div id="wrap">
                <div id="map"></div>
                </div>

                <script>
                    const stations = {json.dumps(data_json, ensure_ascii=False)}; 
                    const data = JSON.parse(stations); 
                    // const data = {json.dumps(df.to_dict('records'), ensure_ascii=False)};

                    // 카카오 지도맵 초기위치 및 줌 레벨 설정
                    kakao.maps.load(function() {{
                        const container = document.getElementById('map');
                        const options = {{
                            center: new kakao.maps.LatLng({lat}, {lon}),
                            level: 6
                    }};
                    const map = new kakao.maps.Map(container, options);

                    // 인포윈도우
                    const iwContent = '<div style="padding:5px;">현위치</div>', 
                    iwPosition = new kakao.maps.LatLng({lat}, {lon}), 
                    iwRemoveable = false; 
                    var infowindow = new kakao.maps.InfoWindow({{
                        map: map, 
                        position: iwPosition,
                        content: iwContent,
                        removable: iwRemoveable
                    }});

                    // 지도에 표시할 원(반경 범위)
                    var circle = new kakao.maps.Circle({{
                        center: new kakao.maps.LatLng({lat}, {lon}),
                        radius: {radius},
                        strokeWeight: 3,
                        strokeColor: '#75B8FA',
                        strokeOpacity: 1,
                        strokeStyle: 'dashed',
                        fillColor: '#CFE7FF',
                        fillOpacity: 0.5
                    }}); 
                    circle.setMap(map); 

                    // 마커 + 인포윈도우
                    data.forEach((d) => {{
                        const pos = new kakao.maps.LatLng(d.LAT_WGS84, d.LON_WGS84);
                        const marker = new kakao.maps.Marker({{ position: pos, map }});
                        const parts = d.OS_NM.trim().split(/\s+/);
                        const nameHtml = parts.map(p => `<div>${{p}}</div>`).join("");
                        const content = 
                        `<div class="label">
                            <div style="text-align:center;">
                            <div><b>${{nameHtml}}</b></div>
                            <div>${{d.PRICE.toLocaleString()}}원</div>
                        </div>`;
                        const infowindow = new kakao.maps.InfoWindow({{ content }});

                        kakao.maps.event.addListener(marker, 'mouseover', () => infowindow.open(map, marker));
                        kakao.maps.event.addListener(marker, 'mouseout',  () => infowindow.close());
                    }});
                }});
              </script>
            </body>
            </html>
            """, height=500)

    df["PRICE"] = pd.to_numeric(df["PRICE"]).map(lambda x: int(x))  # 형변환
    df["DISTANCE"] = pd.to_numeric(df["DISTANCE"]).map(lambda x: int(x))  # 형변환
    if sort == 1:  # 정렬기준이 가격순일때 같은 가격이라면 가까운 거리순으로 정렬
        df = df.sort_values(by=["PRICE", "DISTANCE"])

    df.rename(columns={
        "UNI_ID": "station_id",
        "POLL_DIV_CD": "상표",
        "OS_NM": "주유소명",
        "PRICE": "가격",
        "DISTANCE": "거리",
        "PRODCD": "유종"
    }, inplace=True)
    df["가격"] = df["가격"].map(lambda x: f"{x:,}원")
    df["거리"] = df["거리"].map(lambda x: f"{x:,}m")
    df = df[["station_id", "상표", "주유소명", "가격", "거리", "유종"]]

    @st.fragment
    def show_ai_recommend():
        if st.button("AI추천 주유소", type="primary", disabled=session["rec_btn_run_lock"]):
            with st.spinner("추천 중입니다..."):
                if os.getenv("OPENAI_API_KEY"):
                    result = run_agent(
                        stations=df.to_dict("records"),
                        weight_price=0.5,
                        weight_distance=0.5,
                        topk=10,
                    )
                    session["rec"] = result
                    session["rec_btn_run_lock"] = True
                else:
                    session["rec"] = "**OPEN API KEY를 확인해주세요**"
                st.rerun()
    show_ai_recommend()
    if session["rec"]:
        st.markdown((session["rec"]))

    df.index = pd.RangeIndex(1, len(df)+1)
    st.dataframe(df.iloc[:, 1:])
elif session["submit"] and session["dataframe"] is None:
    st.info("반경내 주유소가 없습니다.")