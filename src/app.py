import os
from urllib.parse import urlparse, quote
import streamlit as st
from dotenv import load_dotenv
from openai import AzureOpenAI
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.core.credentials import AzureKeyCredential
import re
import datetime


load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"), override=True)

_p = urlparse(os.getenv("AZURE_OPENAI_ENDPOINT", ""))
OPENAI_ENDPOINT = f"{_p.scheme}://{_p.netloc}/"

@st.cache_resource
def init_clients():
    openai_client = AzureOpenAI(
        azure_endpoint=OPENAI_ENDPOINT,
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version="2025-01-01-preview"
    )
    search_client = SearchClient(
        endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
        index_name="korean-novels",
        credential=AzureKeyCredential(os.getenv("AZURE_SEARCH_ADMIN_KEY"))
    )
    return openai_client, search_client

openai_client, search_client = init_clients()

EMBED_DEPLOY = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
CHAT_DEPLOY  = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")

GENRES = {
    "판타지":    "판타지 마법 이세계 모험",
    "스릴러":    "스릴러 범죄 추리 긴장감 반전",
    "로맨스":    "로맨스 사랑 연애 설렘",
    "역사소설":  "역사 시대 조선 근현대사 대하소설",
    "성장소설":  "성장 청소년 자아 변화 우정",
    "가족드라마": "가족 갈등 관계 세대 부모",
    "사회비판":  "사회비판 현실 차별 부조리",
    "힐링":      "힐링 따뜻함 치유 위로 일상",
    "SF":        "SF 과학 미래 우주 기술",
    "공포":      "공포 호러 심리 불안",
}

def get_embedding(text: str) -> list:
    text = str(text).strip() or "내용 없음"
    text = text[:3000]
    return openai_client.embeddings.create(
        input=text, model=EMBED_DEPLOY
    ).data[0].embedding

def hybrid_search(query: str, top_k: int = 5, min_year: str = None) -> list:
    vector_query = VectorizedQuery(
        vector=get_embedding(query),
        k_nearest_neighbors=top_k,
        fields="summary_vector",
        weight=0.7  # 0~1, 높을수록 벡터 검색 비중 높음
    )
    filter_expr = f"pub_year ge '{min_year}'" if min_year else None

    results = search_client.search(
        search_text=query,
        vector_queries=[vector_query],
        select=["title", "author", "pub_year", "category", "summary", "page_count", "img_url"],
        filter=filter_expr,
        top=top_k
    )
    return [dict(r) for r in results]

def get_recent_history(n: int = 3) -> list:
    history = [m for m in st.session_state.messages if m["role"] != "system"]
    return history[-n*2:]

def stream_recommendation(query: str, novels: list):
    context = ""
    for i, n in enumerate(novels, 1):
        context += f"{i}. {n['title']} - {n['author']} ({n['pub_year']})\n"
        context += f"   줄거리: {n['summary']}\n\n"

    history = get_recent_history(3)
    messages = [
        {
            "role": "system",
            "content": (
                "당신은 한국 소설 추천 전문가입니다.\n"
                "아래 [추천 소설] 목록을 보고 사용자의 키워드와 관련 있는 소설만 추천하세요.\n"
                "목록의 소설이 키워드와 완전히 무관할 때만 '관련 소설 없음'이라고 답하세요.\n"
                "조금이라도 관련 있으면 전부 RELEVANT에 포함하세요.\n"
                "관련 있는 소설이 있으면:\n"
                "- 답변 첫 줄에 반드시 'RELEVANT:1,2,3' 형식으로 관련 소설 번호만 적으세요. (예: RELEVANT:1,3)\n"
                "- 그 다음 줄부터 추천 이유를 작성하세요.\n"
                "- 소설 제목은 **굵게** 표시\n"
                "- 각 소설마다 한 줄 띄워서 가독성 있게\n"
                "- 전체 300자 내외"
            )
        }
    ]
    messages += history
    messages.append({
        "role": "user",
        "content": f"키워드: {query}\n\n[추천 소설]\n{context}"
    })

    return openai_client.chat.completions.create(
        model=CHAT_DEPLOY,
        messages=messages,
        max_tokens=600,
        temperature=0.7,
        stream=True
    )

def render_novel_card(n: dict):
    aladin_url = f"https://www.aladin.co.kr/search/wsearchresult.aspx?SearchTarget=Book&SearchWord={quote(n['title'])}"
    img_url = n.get("img_url", "")

    col1, col2 = st.columns([1, 4])
    with col1:
        if img_url:
            st.image(img_url, width=80)
        else:
            st.markdown("📗")
    with col2:
        st.markdown(f"#### [{n['title']}]({aladin_url})")
        st.markdown(f"**저자** {n['author']}　**출판** {n.get('pub_year', '-')}년"
                    + (f"　**분량** {n['page_count']}p" if n.get("page_count") else ""))
        st.markdown(f"<small>{n.get('summary', '')[:120]}...</small>", unsafe_allow_html=True)

# ── UI
st.set_page_config(page_title="한국 소설 추천", page_icon="📚", layout="centered")
st.title("📚 한국 소설 추천")
st.caption("장르를 선택하거나 키워드로 소설을 추천받으세요")

if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "안녕하세요! 원하는 소설 분위기나 키워드를 알려주세요. 아래 장르 버튼을 눌러도 돼요 😊"}
    ]

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("예: 가족 갈등 다룬 현대소설 추천해줘")
if not prompt and "auto_query" in st.session_state:
    prompt = st.session_state.pop("auto_query")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 쿼리에서 연도 추출
    year_match = re.search(r'(\d+)년', prompt)
    min_year = None
    if year_match:
        n_years = int(year_match.group(1))
        min_year = str(datetime.datetime.now().year - n_years)

    with st.spinner("소설 검색 중..."):
        novels = hybrid_search(prompt, top_k=5, min_year=min_year)

        if not novels:
            response = "입력하신 키워드와 관련된 소설을 찾지 못했어요. 좀 더 구체적인 키워드를 입력해보세요."
            st.markdown(response)
        else:
            stream = stream_recommendation(prompt, novels)

            # 스트리밍 수집
            collected = []
            placeholder = st.empty()
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    collected.append(chunk.choices[0].delta.content)
                    placeholder.markdown("".join(collected))
            response = "".join(collected)

            # RELEVANT 파싱
            relevant_novels = []
            lines = response.splitlines()
            if lines and lines[0].startswith("RELEVANT:"):
                try:
                    idxs = [int(x.strip()) - 1 for x in lines[0].replace("RELEVANT:", "").split(",")]
                    relevant_novels = [novels[i] for i in idxs if 0 <= i < len(novels)]
                except Exception:
                    relevant_novels = novels
                response = "\n".join(lines[1:]).strip()
                placeholder.markdown(response)

            # 관련 소설 있을 때만 카드 표시
            if relevant_novels and "관련 소설 없음" not in response:
                with st.expander("📖 검색된 소설", expanded=True):
                    for i, n in enumerate(relevant_novels):
                        render_novel_card(n)
                        if i < len(relevant_novels) - 1:
                            st.divider()

        st.session_state.messages.append({"role": "assistant", "content": response})

st.divider()

# ── 장르 버튼
cols = st.columns(5)
for i, label in enumerate(GENRES):
    with cols[i % 5]:
        if st.button(label, use_container_width=True, key=f"genre_{label}"):
            st.session_state["auto_query"] = f"{label} 장르 소설 추천해줘"
            st.rerun()