import os
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pymongo import MongoClient


def main():
    load_dotenv()
    uri = os.getenv("MONGO_URI")
    if not uri:
        print("[err] MONGO_URI not set in environment/.env")
        return 1

    client = MongoClient(uri)
    db = client.get_default_database()
    if db is None:
        db = client["login"]
    users = db["users"]
    posts = db["posts"]

    try:
        posts.create_index([("user_id", 1), ("created_at", -1)])
        posts.create_index([("title", "text"), ("contents", "text")])
    except Exception as e:
        print("[warn] index ensure failed:", e)

    categories = [
        "사회", "경제", "과학", "문화", "기술", "환경", "스포츠", "생활", "역사", "철학", "기타",
    ]

    sample_titles = [
        "AI가 바꾸는 일의 미래",
        "금리 인상과 주택 시장의 변화",
        "양자 컴퓨팅의 현재와 미래",
        "지역 축제의 새로운 트렌드",
        "웹 성능 최적화 10가지 팁",
        "기후 위기와 우리의 선택",
        "파리 올림픽 종합 리포트",
        "하루 10분 루틴으로 삶의 질 높이기",
        "고려 시대 무역과 문화 교류",
        "칸트 철학 쉽게 이해하기",
        "사이드 프로젝트로 커리어 업그레이드",
        "스타트업 초기 자금 조달 가이드",
        "우주 망원경이 포착한 새로운 이미지",
        "전통 시장의 디지털 전환 사례",
        "오픈소스 기여 시작하기",
    ]

    sample_sentences = [
        "이 글에서는 핵심 개념과 실제 사례를 중심으로 풀어봅니다.",
        "데이터를 기반으로 현재 동향을 분석해 보았습니다.",
        "초보자도 이해할 수 있도록 단계별로 설명합니다.",
        "관련 연구와 보고서를 참고하여 정리했습니다.",
        "현장에서 얻은 인사이트를 공유합니다.",
        "간단한 체크리스트와 함께 적용 방법을 제시합니다.",
        "앞으로의 방향성과 과제를 함께 생각해봅니다.",
    ]

    urls = [
        "https://news.ycombinator.com/",
        "https://techcrunch.com/",
        "https://arxiv.org/",
        "",
        "",
    ]

    now = datetime.now(timezone.utc)

    count_per_user = int(os.getenv("SEED_COUNT", "30"))
    force = os.getenv("SEED_FORCE", "0") in ("1", "true", "True")

    total_added = 0
    for u in users.find({}):
        uid = u.get("_id")
        name = u.get("name") or u.get("email")
        existing_seeded = posts.count_documents({"user_id": uid, "seed": True})
        if existing_seeded and not force:
            print(f"- Skip {name}: already has {existing_seeded} seeded posts (set SEED_FORCE=1 to add more)")
            continue

        docs = []
        for i in range(count_per_user):
            cat = random.choice(categories)
            title = random.choice(sample_titles)
            url = random.choice(urls)
            # 2~4 문장으로 본문 구성
            contents = " ".join(random.choices(sample_sentences, k=random.randint(2, 4)))
            # 최근 120일 이내 랜덤 시각
            delta_days = random.randint(0, 120)
            delta_minutes = random.randint(0, 24 * 60)
            dt = now - timedelta(days=delta_days, minutes=delta_minutes)
            docs.append({
                "user_id": uid,
                "category": cat,
                "title": title,
                "url": url,
                "contents": contents,
                "created_at": dt,
                "seed": True,
            })

        if docs:
            res = posts.insert_many(docs)
            print(f"+ Inserted {len(res.inserted_ids)} posts for {name}")
            total_added += len(res.inserted_ids)

    print(f"Done. Total inserted: {total_added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
