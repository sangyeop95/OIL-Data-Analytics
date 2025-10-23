import time
import os.path
import zoneinfo
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta

import feedparser
from urllib.parse import urlparse
from googlenewsdecoder import gnewsdecoder
import trafilatura

from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
import chromadb
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.documents import Document
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.chains.combine_documents import create_stuff_documents_chain

from dotenv import load_dotenv

load_dotenv()

KEYWORDS = [
    "유가", "국제유가", "국제 유가", "원유", "원유 가격",
    "브렌트", "브렌트유", "WTI", "두바이유", "휘발유",
    "경유", "LPG", "정제마진", "유류세", "OPEC",
    "OPEC+", "감산", "증산", "배럴당", "석유",
    "원유 선물", "산유국", "석유수출국", "기름"
] # 키워드 24개

PERSIST_DIRECTORY = "./chroma_db"
COLLECTION_NAME = "oil_news"
LOOKBACK_DAYS = 7
KOR = zoneinfo.ZoneInfo("Asia/Seoul")

openai_model = "gpt-4o-mini"
chunk_size = 1000
chunk_overlap = 150

def _get_vs(persist_dir: str) -> Chroma:
    return Chroma(
        embedding_function=OpenAIEmbeddings(),
        persist_directory=persist_dir,
        collection_name=COLLECTION_NAME,
    )

def _get_rss_feeds():
    query = "".join([word + "+OR+" if i != len(KEYWORDS)-1 else word for i, word in enumerate(KEYWORDS)]).replace(" ", "%20")
    rss_feeds = {
        "구글뉴스": f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko",
        "한국경제_경제": "https://www.hankyung.com/feed/economy",
        "매일경제_경제": "https://www.mk.co.kr/rss/30100041/",
        "아시아경제_경제": "https://www.asiae.co.kr/rss/economy.htm",
        "이투데이_글로벌경제": "https://rss.etoday.co.kr/eto/global_news.xml",
        "이투데이_정치경제": "https://rss.etoday.co.kr/eto/political_economic_news.xml",
        "조선닷컴_경제": "https://www.chosun.com/arc/outboundfeeds/rss/category/economy/?outputType=xml",
        "동아일보_경제": "http://rss.donga.com/economy.xml",
        # "한겨레_경제": "http://www.hani.co.kr/rss/economy/",
        "경향신문_경제": "http://www.khan.co.kr/rss/rssdata/economy.xml",
        "조선비즈_정책금융": "http://biz.chosun.com/site/data/rss/policybank.xml"
    }
    return rss_feeds

def keyword_hit(text: str, keywords: List[str]) -> bool:
    """키워드가 있으면 True 반환"""
    return any(k in text.upper() for k in keywords)

def fetch_articles_from_rss(url: str,
                            max_items_per_feed: int,
                            lookback_days=LOOKBACK_DAYS,
                            min_char=0) -> List[Dict]:
    """
    feedparser(googlenewsdecoder) + trafilatura
    feedparser로 구글뉴스 RSS에서 가져온 link를 디코더해
    원문 링크 변환 후 trafilatura로 기사내용 추출
    중복된 URL의 기사는 제외하고 반환
    """
    articles = []
    seen_urls = set()
    cutoff = datetime.now(tz=KOR) - timedelta(days=lookback_days) # 기본값 7일 (한국)

    rss = feedparser.parse(url)
    for entry in rss.entries[:max_items_per_feed]:
        title = entry.title
        publish = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=KOR)
        publish_ts = int(publish.timestamp())
        link = entry.link
        if "news.google.com" in urlparse(url).netloc:
            link = gnewsdecoder(link)["decoded_url"]
        downloaded = trafilatura.fetch_url(link) # feedparser로 RSS에서 추출한 기사 본문 추출
        content = trafilatura.extract(downloaded,
                                      include_comments=False,
                                      include_tables=False,
                                      favor_recall=True)

        if link in seen_urls: # 봤던 기사의 링크면 스킵
            continue
        if publish < cutoff: # 발행일이 지난(예전) 기사면 스킵
            continue
        if not keyword_hit(title, KEYWORDS): # 기사 제목에 키워드가 없으면(관련성이 없으면) 스킵
            continue
        if not content or len(content.strip()) < min_char: # 본문 내용이없거나 너무 짧으면 스킵 (기본값 0:없음)
            continue

        doc = {
            "link": link,
            "title": title.strip(),
            "content": content.strip(),
            "publish": publish.isoformat(), # Chroma 메타데이터 값으로 datetime을 허용하지않음
            "publish_ts": publish_ts
        }
        seen_urls.add(link)
        articles.append(doc)
    return articles

def build_vectorstore(docs: List[Dict],
                      persist_dir=PERSIST_DIRECTORY) -> tuple[Chroma, List[Document]]:
    """Document객체로 변환 => 청크 => 벡터DB화해서 ./chroma_db 폴더에 저장"""
    if not docs:
        return _get_vs(persist_dir), []

    lang_docs: List[Document] = []
    for d in docs:
        meta = {
            "link": d["link"],
            "title": d["title"],
            "publish": d["publish"],
            "publish_ts": d["publish_ts"]
        }
        lang_docs.append(Document(page_content=d["content"], metadata=meta))

    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks = splitter.split_documents(lang_docs)

    vs = Chroma.from_documents(
        documents=chunks,
        embedding=OpenAIEmbeddings(),
        persist_directory=persist_dir,
        collection_name=COLLECTION_NAME
    )
    return vs, chunks

def check_vectorstore(docs: List[Dict],
                      persist_dir=PERSIST_DIRECTORY,
                      lookback_days=LOOKBACK_DAYS) -> tuple[Chroma, List[Document], int]:
    """오래된 기사 삭제 새로운 URL의 기사 DB에 추가"""
    client = chromadb.PersistentClient(path=persist_dir)
    collection = client.get_collection(COLLECTION_NAME)

    cutoff_ts = int((datetime.now(tz=KOR) - timedelta(days=lookback_days)).timestamp())
    old_chunks_num = len(collection.get(
        where={"publish_ts": {"$lt": cutoff_ts}}
    )["metadatas"])
    collection.delete( # 발행일이 기준일수보다 지난 데이터 삭제
        where={"publish_ts": {"$lt": cutoff_ts}}
    )

    metas = collection.get()
    exist_link = set([meta["link"] for meta in metas["metadatas"]])

    lang_docs: list[Document] = []
    for d in docs:
        if d["link"] not in exist_link:
            meta = {
                "link": d["link"],
                "title": d["title"],
                "publish": d["publish"],
                "publish_ts": d["publish_ts"]
            }
            lang_docs.append(Document(page_content=d["content"], metadata=meta))

    if not lang_docs:
        return _get_vs(persist_dir), [], old_chunks_num

    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    new_chunks = splitter.split_documents(lang_docs)

    vs = Chroma.from_documents(
        documents=new_chunks,
        embedding=OpenAIEmbeddings(),
        persist_directory=persist_dir,
        collection_name=COLLECTION_NAME,
    )
    return vs, new_chunks, old_chunks_num

def build_llm():
    llm = ChatOpenAI(model=openai_model, temperature=0.1)
    prompt = ChatPromptTemplate.from_template(
        """
        당신은 경제 신문 기자입니다. 제공된 컨텍스트만을 사용해 한국 독자를 대상으로
        유가 관련 핵심 이슈를 간결하고 유용하게 요약하세요.

        요구사항:
        - 불필요한 수사 없이 사실 위주로.
        - 기사에 적힌 숫자/날짜/단위를 구체적으로.
        - 영향 분석: 국내 휘발유/경유 가격, 환율/물류/항공유/유류세 등 파급효과를 짧게.
        - 중복/동어반복 제거.
        - 뉴스 기사처럼 작성.

        <사용자 질의>
        {question}

        <참고 컨텍스트>
        {context}
        """
    )
    return create_stuff_documents_chain(llm, prompt)

def summarize_oil_news(vs: Chroma,
                       question: str,
                       k: int) -> str:
    """llm모델에 로컬DB에서 상위 k개의 관련성 있는 기사를 context로 주면서 질문"""
    if vs is None:
        return "벡터 스토어가 비어 있습니다. 먼저 RSS 수집을 실행해주세요."

    question = f"유가 관련 핵심 이슈만 요약. 겹치는 내용은 하나로 병합.\n\n원문 요청: {question}"
    retriever = vs.as_retriever(search_kwargs={"k": k})
    k_context = retriever.invoke(question)
    llm = build_llm()
    return llm.invoke({"question": question, "context": k_context})

def run_pipeline(rss: str,
                 max_items_per_feed: int,
                 k: int,
                 lookback_days=LOOKBACK_DAYS) -> str:
    """최종 실행"""
    print("1) RSS 수집 및 본문 추출 중...")
    articles = fetch_articles_from_rss(_get_rss_feeds().get(rss),
                                       max_items_per_feed=max_items_per_feed,
                                       lookback_days=lookback_days)
    print(f" - 수집 성공 : {len(articles)}건")

    if not os.path.exists(PERSIST_DIRECTORY):
        print("2) 임베딩 및 벡터DB 구축 중...")
        vs, chunks = build_vectorstore(articles)
        print(f" - 청크 저장 : {len(chunks)}개")
    else:
        print("2) 로컬 벡터DB에서 기준일수가 지난 데이터 삭제 및 신규 RSS 임베딩")
        vs, new_chunks, delete_chunks = check_vectorstore(articles, lookback_days=lookback_days)
        print(f" - 새로운 청크 저장 : {len(new_chunks)}개")
        print(f" - 삭제한 청크 개수 : {delete_chunks}개")
        print(f" - 벡터DB 청크 개수 : {vs._collection.count() + delete_chunks - len(new_chunks)} -> {vs._collection.count()}")

    print("3) 요약 실행 중...")
    answer = summarize_oil_news(vs,
                                question=f"지난 {lookback_days}일간 국제유가 등락 요인과 국내 유가의 시사점은?",
                                k=k)
    print("\n===== 요약 결과 =====\n")
    print(answer)
    return answer


# --------------------------------------------


from func import station_info_search

@tool("station_info_search", return_direct=False)
def station_info_search_tool(station_id: str) -> List[Dict]: # AI사용 사용자 정의 함수
    """station_id로 주유소의 상세정보를 조회합니다."""
    return station_info_search(station_id)

def run_agent(stations: List[Dict],
              weight_price: float,
              weight_distance: float,
              topk: int) -> Tuple[Optional[str], Optional[List[Dict]], str]:
    if not stations:
        return None, None, "주유소 데이터가 없습니다."
    top_list = stations[:topk]

    sys = (f"당신은 합리적인 의사결정을 돕는 어시스턴트입니다.\n "
              f"- 가격 가중치: {weight_price:.2f}, 거리 가중치: {weight_distance:.2f}\n"
              f"JSON을 검토하고 최적의 station_id를 선택한 후 반드시 `station_info_search`를 1회 호출하여 상세정보를 얻으세요.\n"
              f"`station_info_search`함수를 호출하여 얻은 주유소의 상세정보를 토대로 한글로 주소, 상표, 기름가격, 전화번호, 세차장 유무를 불릿형태로 작성하세요.\n"
              f"제목 양식을 준수하세요.\n"
              f"추천드리는 주유소는 **OOO**입니다.")
    human = ("(JSON): {top_list}\n"
             "최적의 주유소 한 곳을 골라 `station_info_search`를 1회만 호출하세요.")

    llm = ChatOpenAI(model=openai_model, temperature=0.1)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", sys),
            ("human", human),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )

    tools = [station_info_search_tool]
    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, verbose=True) # verbose 실행과정 출력유무
    result = executor.invoke({"top_list": top_list})
    return result.get("output", "추천드릴 만한 주유소가 없습니다")