import os
from urllib.parse import urlparse
import streamlit as st
from dotenv import load_dotenv
from openai import AzureOpenAI
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.core.credentials import AzureKeyCredential

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"), override=True)

# AzureOpenAI는 경로 없는 base URL을 요구하므로 scheme + netloc만 추출
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
    "판타지":   "판타지 마법 이세계 모험",
    "스릴러":   "스릴러 범죄 추리 긴장감 반전",
    "로맨스":   "로맨스 사랑 연애 설렘",
    "역사소설": "역사 시대 조선 근현대사 대하소설",
    "성장소설": "성장 청소년 자아 변화 우정",
    "가족드라마":"가족 갈등 관계 세대 부모",
    "사회비판": "사회비판 현실 차별 부조리",
    "힐링":     "힐링 따뜻함 치유 위로 일상",
    "SF":       "SF 과학 미래 우주 기술",
    "공포":     "공포 호러 심리 불안",
}

def get_embedding(text: str) -> list:
    text = str(text).strip() or "내용 없음"
    text = text[:3000]  # 토큰 초과 방지
    return openai_client.embeddings.create(
        input=text, model=EMBED_DEPLOY
    ).data[0].embedding

def hybrid_search(query: str, top_k: int = 3) -> list:
    vector_query = VectorizedQuery(
        vector=get_embedding(query),
        k_nearest_neighbors=top_k,
        fields="summary_vector"
    )
    results = search_client.search(
        search_text=query,
        vector_queries=[vector_query],
        select=["title", "author", "pub_year", "category", "summary", "page_count", "img_url"],
        top=top_k
    )
    return [dict(r) for r in results]

def stream_recommendation(query: str, novels: list):
    context = ""
    for i, n in enumerate(novels, 1):
        context += f"{i}. {n['title']} - {n['author']} ({n['pub_year']})\n"
        context += f"   줄거리: {n['summary']}\n\n"

    return openai_client.chat.completions.create(
        model=CHAT_DEPLOY,
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 한국 소설 추천 전문가입니다. "
                    "반드시 아래 [추천 소설] 목록에 있는 소설만 언급하세요. "
                    "목록에 없는 소설은 절대 추천하지 마세요. "
                    "각 소설이 사용자의 키워드와 어떻게 연결되는지 구체적으로 설명하세요. "
                    "300자 내외로 답변하세요."
                )
            },
            {"role": "user", "content": f"키워드: {query}\n\n[추천 소설]\n{context}"}
        ],
        max_tokens=600,
        temperature=0.7,
        stream=True
    )

# ── UI
st.set_page_config(page_title="한국 소설 추천", page_icon="📚", layout="centered")
st.title("📚 한국 소설 추천")
st.caption("장르를 선택하거나 키워드로 소설을 추천받으세요")

# ── 챗봇 (위)
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

    with st.chat_message("assistant"):
        with st.spinner("소설 검색 중..."):
            novels = hybrid_search(prompt, top_k=3)

        if not novels:
            response = "입력하신 키워드와 관련된 소설을 찾지 못했어요. 좀 더 구체적인 키워드를 입력해보세요. (예: '가족 갈등', '70년대 역사', '청소년 성장 우정')"
            st.markdown(response)
        else:
            with st.expander("📖 검색된 소설", expanded=True):
                for n in novels:
                    st.markdown(f"**{n['title']}** · {n['author']} ({n['pub_year']})")
                    st.caption(n.get("summary", "")[:100] + "...")

            stream = stream_recommendation(prompt, novels)
            response = st.write_stream(
                chunk.choices[0].delta.content
                for chunk in stream
                if chunk.choices and chunk.choices[0].delta.content
            )

        st.session_state.messages.append({"role": "assistant", "content": response})

st.divider()

# ── 장르 버튼 (아래)
cols = st.columns(5)
for i, label in enumerate(GENRES):
    with cols[i % 5]:
        if st.button(label, use_container_width=True, key=f"genre_{label}"):
            st.session_state["auto_query"] = f"{label} 장르 소설 추천해줘"
            st.rerun()