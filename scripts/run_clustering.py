"""
scripts/run_clustering.py — Colab 노트북(customer_persona_clustering_colab.ipynb)의
임베딩·클러스터링 로직을 GitHub Actions에서 무인 실행할 수 있도록 옮긴 배치 스크립트.

이 스크립트가 하는 일:
  1) data/customer_embedding_training_data_2000_segmented.xlsx 를 읽어
  2) 노트북과 동일한 방식으로 이벤트 로그 집계 → PCA 임베딩 → Dense Fusion → K-means 클러스터링
  3) 결과를 data/segment_summary.json 에 저장 (백엔드가 이 파일을 그대로 서빙)
  4) 이번 실행 결과를 data/segment_snapshots/{YYYY-MM}.json 에 스냅샷으로 남기고,
     쌓인 스냅샷들을 비교해 data/segment_trends.json / data/segment_migration.json 을 갱신

노트북과의 차이점:
  - Colab 전용 코드(파일 업로드 위젯, !pip 매직 커맨드 등) 제거
  - k값을 세그먼트 이름(growth/stable/dormant)에 자동으로 매핑
    (매출_증감률_pct 평균이 가장 높은 클러스터 = growth, 가장 낮은 클러스터 = dormant)
  - 시각화(차트) 코드는 배치 실행에 필요 없어 제외

로컬에서 테스트: python scripts/run_clustering.py
"""

import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.compose import ColumnTransformer
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import OneHotEncoder, StandardScaler

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")
XLSX_PATH = os.path.join(DATA_DIR, "customer_embedding_training_data_2000_segmented.xlsx")
SNAPSHOT_DIR = os.path.join(DATA_DIR, "segment_snapshots")

NOW = datetime.now(timezone.utc)
RUN_MONTH = NOW.strftime("%Y-%m")

# xlsx의 '구매이력' 시트(최근_구매시점, 월매출 12개월치)는 2026-07-03을 기준일로 생성된
# 정적 합성 데이터입니다. 최근성(며칠 전에 구매했는지) 계산은 스크립트를 실제로 실행하는
# 시점(NOW)이 아니라 이 데이터 생성 기준일에 맞춰야, 매번 실행할 때마다 "오늘"이 밀리면서
# 최근성 값이 데이터의 실제 의미와 어긋나는 문제가 생기지 않습니다.
# (RUN_MONTH는 그대로 NOW를 씁니다 — 이건 "배치를 실제로 언제 실행했는지"를 남기는
#  스냅샷 파일명이라 실제 실행 시점을 반영하는 게 맞습니다.)
DATA_REFERENCE_DATE = datetime(2026, 7, 3, tzinfo=timezone.utc)

EVENT_TYPES = ["홈_피드_클릭", "카테고리_이동", "검색", "상품_상세조회", "찜하기",
               "장바구니_담기", "장바구니_이탈", "결제페이지_진입", "구매완료"]

SEGMENT_NAME_KO = {"growth": "성장형 핵심 고객", "stable": "안정형 주력 고객", "dormant": "이탈위험 휴면 고객"}


def build_click_features(df_session, df_event, customer_ids):
    event_counts = df_event.pivot_table(index="고객ID", columns="이벤트_유형", aggfunc="size", fill_value=0)
    event_counts = event_counts.reindex(customer_ids, fill_value=0)
    for et in EVENT_TYPES:
        if et not in event_counts.columns:
            event_counts[et] = 0

    dwell_mean = df_event.groupby("고객ID")["체류_시간_초"].mean()
    scroll_mean = df_event.groupby("고객ID")["스크롤_깊이_pct"].mean()
    session_agg = df_session.groupby("고객ID").agg(
        세션수_30일=("세션ID", "count"),
        평균_세션당이벤트수=("총_이벤트수", "mean"),
        구매전환_세션비율=("구매_전환_여부", "mean"),
    )
    mode_or_default = lambda s, d: s.value_counts().index[0] if len(s) else d
    exit_point = df_session.groupby("고객ID")["최종_이벤트_유형"].agg(lambda s: mode_or_default(s, "활동없음"))
    device = df_session.groupby("고객ID")["디바이스_유형"].agg(lambda s: mode_or_default(s, "활동없음"))
    cat_dwell = (df_event.dropna(subset=["카테고리"])
                 .groupby(["고객ID", "카테고리"])["체류_시간_초"].sum().reset_index())
    top_category = (cat_dwell.sort_values("체류_시간_초", ascending=False)
                     .drop_duplicates("고객ID").set_index("고객ID")["카테고리"])

    df = pd.DataFrame(index=customer_ids)
    for et in EVENT_TYPES:
        df[et + "_수"] = event_counts[et].reindex(customer_ids).fillna(0).astype(int)
    df["평균_체류시간_초"] = dwell_mean.reindex(customer_ids)
    df["평균_스크롤_깊이_pct"] = scroll_mean.reindex(customer_ids)
    df["세션수_30일"] = session_agg["세션수_30일"].reindex(customer_ids).fillna(0).astype(int)
    df["평균_세션당이벤트수"] = session_agg["평균_세션당이벤트수"].reindex(customer_ids)
    df["구매전환_세션비율"] = session_agg["구매전환_세션비율"].reindex(customer_ids)
    df["최다_관심_카테고리"] = top_category.reindex(customer_ids)
    df["주요_이탈_지점"] = exit_point.reindex(customer_ids)
    df["디바이스_유형"] = device.reindex(customer_ids)
    df["장바구니_구매전환율"] = df["구매완료_수"] / (df["장바구니_담기_수"] + 1)

    num_fill_cols = ["평균_체류시간_초", "평균_스크롤_깊이_pct", "평균_세션당이벤트수", "구매전환_세션비율"]
    df[num_fill_cols] = df[num_fill_cols].fillna(0)
    df["최다_관심_카테고리"] = df["최다_관심_카테고리"].fillna("활동없음")

    return df.reset_index().rename(columns={"index": "고객ID"})


def build_embedding(df, num_cols, cat_cols, n_components):
    transformers = []
    if num_cols:
        transformers.append(("num", StandardScaler(), num_cols))
    if cat_cols:
        transformers.append(("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), cat_cols))
    X = ColumnTransformer(transformers).fit_transform(df[num_cols + cat_cols])
    n_components = min(n_components, X.shape[1] - 1, X.shape[0] - 1)
    return PCA(n_components=n_components, random_state=42).fit_transform(X)


def mode_pct(series):
    vc = series.value_counts(normalize=True)
    if len(vc) == 0:
        return "N/A", 0.0
    return vc.index[0], round(float(vc.iloc[0]) * 100, 1)


def top_n_pct(series, n=2):
    """상위 n개 값을 '값1 xx.x% · 값2 yy.y%' 형태의 문자열로 반환합니다."""
    vc = series.value_counts(normalize=True) * 100
    items = list(vc.head(n).items())
    if not items:
        return "N/A"
    return " · ".join(f"{val} {pct:.1f}%" for val, pct in items)


ONE_LINER_TEMPLATE = {
    "growth": "브랜드에 충성하며 정가로 자주 구매하고, {channels}에서 인플루언서 콘텐츠에 반응하는 고관여·고성장 고객군",
    "stable": "완만한 성장세를 유지하며 {channels}에서 트렌드 콘텐츠에 고르게 반응하는 주력 고객군",
    "dormant": "세일 캠페인에만 반응하고 재구매 주기가 긴, {channels} 위주로 활동하는 이탈위험 휴면 고객군",
}


def build_one_liner(seg_id, sns_):
    channels = [v for v, _ in sns_["채널"].value_counts(normalize=True).head(3).items()]
    channels_str = "·".join(channels) if channels else "SNS"
    template = ONE_LINER_TEMPLATE.get(
        seg_id, "{channels}에서 활동하는 고객군 — 배치 재계산 결과"
    )
    return template.format(channels=channels_str)


# 광고 플랫폼(1:1 발송 불가, 광고 관리자에서 집행)과 실제 발송 게이트웨이를 구분합니다.
AD_PLATFORM_CHANNELS = {"Instagram", "YouTube", "TikTok", "Facebook"}


def gateway_for_channel(channel):
    if any(k in channel for k in ("카카오", "네이버")):
        return "kakao"
    if channel in AD_PLATFORM_CHANNELS:
        return None
    return None


def build_campaign_messages(seg_id, sns_):
    """세그먼트의 실제 채널·캠페인·해시태그 반응 데이터를 바탕으로 캠페인 메시지 후보를 생성합니다.
    형식: [채널 라벨, 콘텐츠 포맷/설명, 카피 문구, 게이트웨이(kakao/push/None)]
    """
    channels = [v for v, _ in sns_["채널"].value_counts(normalize=True).head(3).items()]
    campaigns = [v for v, _ in sns_["캠페인_유형"].value_counts(normalize=True).head(2).items()]
    hashtags = [v for v, _ in sns_["주요_해시태그"].value_counts(normalize=True).head(2).items()]
    ad_copies = [v for v, _ in sns_["주요_반응_광고카피"].value_counts(normalize=True).head(1).items()]

    messages = []
    if channels:
        ch = channels[0]
        fmt = f"{campaigns[0]} 콘텐츠" if campaigns else "콘텐츠 캠페인"
        copy = f"{hashtags[0]} 트렌드, 지금 확인해보세요" if hashtags else "지금 신상품을 확인해보세요"
        messages.append([ch, fmt, copy, gateway_for_channel(ch)])
    if len(channels) > 1:
        ch2 = channels[1]
        copy2 = ad_copies[0] if ad_copies else "놓치지 말고 확인해보세요"
        messages.append([ch2, "리타겟팅 메시지", copy2, gateway_for_channel(ch2)])

    # 상위 채널 중 실제로 발송 가능한 게이트웨이(카카오/푸시)가 하나도 없으면 하나 보강
    if not any(gateway_for_channel(c) for c in channels):
        if seg_id == "dormant":
            messages.append(["카카오 알림톡", "윈백 캠페인", "오랜만이에요 — 지금 할인 혜택으로 다시 만나요", "kakao"])
        else:
            messages.append(["앱 푸시", "개인화 추천", "고객님을 위한 추천 상품이 도착했어요", "push"])
    return messages


def run_pipeline():
    sheets = pd.read_excel(XLSX_PATH, sheet_name=None)
    df_session = sheets["세션_마스터"]
    df_event = sheets["이벤트_로그"]
    df_purchase = sheets["구매이력"].copy()
    df_sns = sheets["SNS광고반응"].copy()
    df_profile = sheets["고객프로필(인구통계_취향)"].copy()

    for df in (df_purchase, df_sns, df_profile):
        df.sort_values("고객ID", inplace=True)
        df.reset_index(drop=True, inplace=True)
    customer_ids = df_purchase["고객ID"].values

    today_ts = pd.Timestamp(DATA_REFERENCE_DATE.date())
    df_purchase["최근성_일"] = (today_ts - pd.to_datetime(df_purchase["최근_구매시점"])).dt.days

    df_click_agg = build_click_features(df_session, df_event, customer_ids)

    click_num_cols = [et + "_수" for et in EVENT_TYPES] + [
        "평균_체류시간_초", "평균_스크롤_깊이_pct", "세션수_30일",
        "평균_세션당이벤트수", "구매전환_세션비율", "장바구니_구매전환율",
    ]
    click_cat_cols = ["최다_관심_카테고리", "주요_이탈_지점", "디바이스_유형"]
    purchase_num_cols = [
        "구매_빈도_월평균", "구매_금액_총합_원", "평균_객단가_AOV_원", "정상가_구매_비중_pct",
        "할인상품_구매_비중_pct", "브랜드_집중도", "카테고리_분산도", "재구매_주기_일",
        "반품률_pct", "쿠폰_사용_비율_pct", "시즌별_구매_편중도", "최근성_일",
        "최근3개월_평균매출_원", "이전3개월_평균매출_원", "매출_증감률_pct",
    ]
    sns_num_cols = [
        "댓글_작성수", "저장_코멘트_수", "룩북_이미지_조회수", "숏폼_광고_시청수",
        "인플루언서_콘텐츠_반응수", "좋아요_수", "저장_수", "공유_수", "클릭_수",
        "3초이상_체류_횟수", "즉시이탈_횟수",
    ]
    sns_cat_cols = ["주요_반응_광고카피", "주요_해시태그", "채널", "시간대", "캠페인_유형", "디바이스_유형"]
    profile_cat_cols = [
        "연령대", "성별", "거주지역", "직업", "라이프스타일_관심사",
        "선호_스타일", "선호_컬러", "소비성향_유형", "패션_관여도",
    ]

    e1 = build_embedding(df_click_agg, click_num_cols, click_cat_cols, 6)
    e2 = build_embedding(df_purchase, purchase_num_cols, [], 8)
    e3 = build_embedding(df_sns, sns_num_cols, sns_cat_cols, 8)
    e4 = build_embedding(df_profile, [], profile_cat_cols, 8)

    fused = np.hstack([StandardScaler().fit_transform(e) for e in (e1, e2, e3, e4)])
    cust_emb = PCA(n_components=12, random_state=42).fit_transform(fused)

    sils = []
    for k in range(2, 11):
        labels = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(cust_emb)
        sils.append(silhouette_score(cust_emb, labels))
    best_k = list(range(2, 11))[int(np.argmax(sils))]

    km = KMeans(n_clusters=best_k, random_state=42, n_init=10)
    labels = km.fit_predict(cust_emb)
    sil_score = float(silhouette_score(cust_emb, labels))

    # 클러스터 -> 세그먼트 이름 자동 매핑 (매출 증감률 평균 내림차순 = growth > stable > dormant)
    growth_by_cluster = {c: df_purchase.loc[labels == c, "매출_증감률_pct"].mean() for c in range(best_k)}
    ranked = sorted(growth_by_cluster, key=lambda c: -growth_by_cluster[c])
    if best_k == 3:
        id_map = {ranked[0]: "growth", ranked[1]: "stable", ranked[2]: "dormant"}
    else:
        # k가 3이 아니면(데이터 특성이 바뀌면 발생 가능) 일반화된 이름으로 대체
        id_map = {c: f"segment_{i+1}" for i, c in enumerate(ranked)}

    segments = []
    customer_segment_map = {}
    for cluster_id in ranked:
        seg_id = id_map[cluster_id]
        idx = np.where(labels == cluster_id)[0]
        n = len(idx)
        pct = n / len(labels) * 100
        prof, purch, click = df_profile.loc[idx], df_purchase.loc[idx], df_click_agg.loc[idx]
        sns_ = df_sns.loc[idx]

        for cid in df_purchase.loc[idx, "고객ID"]:
            customer_segment_map[str(cid)] = seg_id

        avg_growth = float(purch["매출_증감률_pct"].mean())
        age, age_pct = mode_pct(prof["연령대"])
        gender, gender_pct = mode_pct(prof["성별"])
        region, _ = mode_pct(prof["거주지역"])
        job, job_pct = mode_pct(prof["직업"])
        color, color_pct = mode_pct(prof["선호_컬러"])

        segments.append({
            "id": seg_id,
            "n": int(n),
            "pct": f"{pct:.1f}%",
            "growth": f"{avg_growth:+.1f}%",
            "dir": "up" if avg_growth >= 0 else "down",
            "oneLiner": build_one_liner(seg_id, sns_),
            "profile": [
                ["연령대", age, f"{age_pct}%"], ["성별", gender, f"{gender_pct}%"],
                ["거주지역", region, ""], ["직업", job, f"{job_pct}%"],
                ["관심사", top_n_pct(prof["라이프스타일_관심사"], 2), ""],
                ["선호 스타일", top_n_pct(prof["선호_스타일"], 2), ""],
                ["선호 컬러", color, f"{color_pct}%"],
                ["소비성향", top_n_pct(prof["소비성향_유형"], 2), ""],
            ],
            "metrics": [
                ["최근 구매까지", f"{purch['최근성_일'].mean():.1f}일"],
                ["월평균 구매빈도", f"{purch['구매_빈도_월평균'].mean():.2f}회"],
                ["12개월 누적 매출", f"{purch['구매_금액_총합_원'].mean() / 10000:.1f}만원"],
                ["평균 객단가", f"{purch['평균_객단가_AOV_원'].mean():,.0f}원"],
                ["정상가 비중", f"{purch['정상가_구매_비중_pct'].mean():.1f}%"],
                ["재구매 주기", f"{purch['재구매_주기_일'].mean():.1f}일"],
                ["30일 세션 수", f"{click['세션수_30일'].mean():.2f}회"],
                ["장바구니→구매 전환율", f"{click['장바구니_구매전환율'].mean():.3f}"],
                ["평균 체류시간", f"{click['평균_체류시간_초'].mean():.1f}초"],
                ["최다 관심 카테고리", top_n_pct(click["최다_관심_카테고리"], 3)],
                ["주요 이탈 지점", top_n_pct(click["주요_이탈_지점"], 2)],
                ["SNS 좋아요 수", f"{sns_['좋아요_수'].mean():.1f}회"],
                ["SNS 저장 수", f"{sns_['저장_수'].mean():.1f}회"],
                ["SNS 즉시이탈 수", f"{sns_['즉시이탈_횟수'].mean():.1f}회"],
                ["선호 채널", top_n_pct(sns_["채널"], 3)],
                ["반응 캠페인 유형", top_n_pct(sns_["캠페인_유형"], 2)],
                ["반응 해시태그", top_n_pct(sns_["주요_해시태그"], 2)],
            ],
            "messages": build_campaign_messages(seg_id, sns_),
        })

    summary = {
        "snapshot_date": NOW.strftime("%Y-%m-%d"),
        "source_file": "data/customer_embedding_training_data_2000_segmented.xlsx",
        "method": f"이벤트 로그 집계 + PCA 임베딩 + Dense Fusion + K-means (k={best_k}, silhouette={sil_score:.3f})",
        "segments": segments,
    }
    return summary, customer_segment_map


def update_trends_and_migration(seg_ids):
    snapshot_files = sorted(f for f in os.listdir(SNAPSHOT_DIR) if f.endswith(".json"))
    months = [f[:-5] for f in snapshot_files]

    trends = {"months": [], **{sid: [] for sid in seg_ids}, "new_entrants": []}
    prev_map = None
    for month in months:
        with open(os.path.join(SNAPSHOT_DIR, f"{month}.json"), encoding="utf-8") as f:
            snap = json.load(f)
        trends["months"].append(month)
        for sid in seg_ids:
            trends[sid].append(sum(1 for v in snap.values() if v == sid))
        if prev_map is None:
            trends["new_entrants"].append(len(snap))
        else:
            changed = sum(1 for cid, seg in snap.items() if prev_map.get(cid) != seg)
            trends["new_entrants"].append(changed)
        prev_map = snap

    # data/segment_trends_backfill.json 이 있으면(scripts/backfill_trends.py로 생성),
    # 실제 스냅샷이 아직 없는 과거 구간을 근사치로 앞에 붙여줍니다.
    # 실제 스냅샷이 있는 달과 겹치면 항상 "실제 값"이 우선합니다.
    backfill_path = os.path.join(DATA_DIR, "segment_trends_backfill.json")
    if os.path.exists(backfill_path):
        with open(backfill_path, encoding="utf-8") as f:
            backfill = json.load(f)
        keep_idx = [i for i, m in enumerate(backfill["months"]) if m not in trends["months"]]
        if keep_idx:
            trends["months"] = [backfill["months"][i] for i in keep_idx] + trends["months"]
            for sid in seg_ids:
                backfill_vals = backfill.get(sid, [0] * len(backfill["months"]))
                trends[sid] = [backfill_vals[i] for i in keep_idx] + trends[sid]
            trends["new_entrants"] = [backfill["new_entrants"][i] for i in keep_idx] + trends["new_entrants"]
            trends["approximate_until"] = backfill["months"][keep_idx[-1]]
            trends["approximate_note"] = backfill.get("approximate_note", "")
            print(f"근사 추이 {len(keep_idx)}개월 병합 완료 (~{trends['approximate_until']}까지 근사치)")

    with open(os.path.join(DATA_DIR, "segment_trends.json"), "w", encoding="utf-8") as f:
        json.dump(trends, f, ensure_ascii=False, indent=2)

    # migration.json은 항상 생성합니다 (server.js가 부팅 시 require()로 이 파일을 읽기 때문에,
    # 파일이 아예 없으면 서버가 기동조차 못 합니다). 스냅샷이 1개뿐이면 "전원 자기 세그먼트 유지"로
    # 채운 항등행렬을 기본값으로 씁니다.
    matrix = {a: {b: 0 for b in seg_ids} for a in seg_ids}
    if len(months) >= 2:
        with open(os.path.join(SNAPSHOT_DIR, f"{months[-2]}.json"), encoding="utf-8") as f:
            prev = json.load(f)
        with open(os.path.join(SNAPSHOT_DIR, f"{months[-1]}.json"), encoding="utf-8") as f:
            curr = json.load(f)
        for cid, curr_seg in curr.items():
            prev_seg = prev.get(cid, curr_seg)
            if prev_seg in matrix and curr_seg in matrix[prev_seg]:
                matrix[prev_seg][curr_seg] += 1
        print(f"migration.json 갱신: {months[-2]} -> {months[-1]} (실측)")
    else:
        with open(os.path.join(SNAPSHOT_DIR, f"{months[-1]}.json"), encoding="utf-8") as f:
            curr = json.load(f)
        for seg in curr.values():
            if seg in matrix:
                matrix[seg][seg] += 1
        print("스냅샷이 1개뿐이라 migration.json을 항등행렬(전원 유지)로 채웠습니다 — 다음 실행부터 실제 이동량이 반영됩니다.")

    with open(os.path.join(DATA_DIR, "segment_migration.json"), "w", encoding="utf-8") as f:
        json.dump(matrix, f, ensure_ascii=False, indent=2)


def main():
    print(f"[{NOW.isoformat()}] 클러스터링 배치 시작")
    summary, customer_segment_map = run_pipeline()

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "segment_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("segment_summary.json 저장 완료:", [(s["id"], s["n"]) for s in summary["segments"]])

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    with open(os.path.join(SNAPSHOT_DIR, f"{RUN_MONTH}.json"), "w", encoding="utf-8") as f:
        json.dump(customer_segment_map, f, ensure_ascii=False)
    print(f"스냅샷 저장 완료: segment_snapshots/{RUN_MONTH}.json")

    seg_ids = [s["id"] for s in summary["segments"]]
    update_trends_and_migration(seg_ids)
    print("배치 완료")


if __name__ == "__main__":
    main()
