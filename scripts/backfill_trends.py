"""
scripts/backfill_trends.py — 실제 월별 스냅샷이 쌓이기 전까지, '구매이력' 시트의
과거 12개월 매출 데이터(월매출_YYYY-MM_원)를 이용해 세그먼트 추이를 근사로 채우는 스크립트.

한계 (반드시 읽어주세요):
  세그먼트는 세션·이벤트·SNS·인구통계 4개 데이터를 합쳐 클러스터링한 결과입니다.
  과거 시점의 세션·이벤트·SNS 로그는 데이터 자체가 없어서, 과거 세그먼트를
  "정확히" 재현하는 건 불가능합니다. 이 스크립트는 매출 증감률만으로
  "그 시점에 어느 세그먼트에 가까웠는지"를 근사할 뿐입니다.

방법:
  1) run_clustering.py가 만든 실제 스냅샷(data/segment_snapshots/*.json)에서
     오늘 기준 각 세그먼트의 평균 매출_증감률_pct를 "기준점(centroid)"으로 삼는다.
  2) 월매출 12개월치로 6개월 이동 윈도우(최근3개월 평균 vs 이전3개월 평균)를 만들어
     각 고객의 과거 시점별 매출_증감률을 계산한다 (계산 가능한 구간은 7개월).
  3) 각 시점의 증감률이 어느 세그먼트 기준점에 가장 가까운지로 근사 배정한다.
  4) data/segment_trends_backfill.json 에 저장 (run_clustering.py가 이 파일을 읽어서
     실제 스냅샷 이전 구간을 채우는 데 사용함)

실행: python scripts/backfill_trends.py
"""

import json
import os

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")
XLSX_PATH = os.path.join(DATA_DIR, "customer_embedding_training_data_2000_segmented.xlsx")
SNAPSHOT_DIR = os.path.join(DATA_DIR, "segment_snapshots")
BACKFILL_PATH = os.path.join(DATA_DIR, "segment_trends_backfill.json")


def latest_real_snapshot():
    files = sorted(f for f in os.listdir(SNAPSHOT_DIR) if f.endswith(".json"))
    if not files:
        raise SystemExit("실제 스냅샷이 하나도 없습니다. 먼저 run_clustering.py를 한 번 실행해주세요.")
    with open(os.path.join(SNAPSHOT_DIR, files[-1]), encoding="utf-8") as f:
        return files[-1][:-5], json.load(f)


def main():
    real_month, real_map = latest_real_snapshot()
    print(f"기준 스냅샷: {real_month} ({len(real_map)}명)")

    df = pd.read_excel(XLSX_PATH, sheet_name="구매이력")
    month_cols = sorted(c for c in df.columns if c.startswith("월매출_"))
    months = [c.replace("월매출_", "").replace("_원", "") for c in month_cols]  # "2025-07" 형태
    revenue = df.set_index("고객ID")[month_cols]
    revenue.columns = months

    # 오늘 기준 세그먼트 비율을 그대로 과거 달에도 적용한다 (매출 증감률 순위 기준).
    # 이렇게 해야 근사 구간의 마지막 달이 실제 스냅샷과 규모 면에서 자연스럽게 이어짐.
    df["seg_today"] = df["고객ID"].map(real_map)
    seg_counts_today = df["seg_today"].value_counts()
    seg_ids = sorted(seg_counts_today.index, key=lambda s: -df.loc[df["seg_today"] == s, "매출_증감률_pct"].mean())
    seg_props = {sid: seg_counts_today[sid] / len(df) for sid in seg_ids}
    print("오늘 세그먼트 비율:", {k: f"{v:.1%}" for k, v in seg_props.items()})

    # 6개월 이동 윈도우로 계산 가능한 구간: index 5 ~ 11 (0-based), 총 7개월
    computable_months = months[5:]
    trends = {"months": [], **{sid: [] for sid in seg_ids}, "new_entrants": []}
    prev_assignment = None

    for i, month in enumerate(computable_months):
        idx = 5 + i  # months 리스트 기준 실제 인덱스
        recent3 = revenue.iloc[:, idx - 2: idx + 1].mean(axis=1)
        prior3 = revenue.iloc[:, idx - 5: idx - 2].mean(axis=1)
        growth = (recent3 - prior3) / prior3.replace(0, np.nan) * 100
        growth = growth.fillna(0)

        # 매출 증감률 순위를 매기고, 오늘과 같은 비율로 상위=growth / 중위=stable / 하위=dormant 배정
        ranks = growth.rank(pct=True, ascending=False)  # 1.0에 가까울수록 증감률 낮음(순위 뒤)
        cum = 0.0
        assignment = pd.Series(index=growth.index, dtype=object)
        remaining = pd.Series(True, index=growth.index)
        for sid in seg_ids:
            cum += seg_props[sid]
            mask = remaining & (ranks <= cum)
            assignment[mask] = sid
            remaining &= ~mask
        assignment[remaining] = seg_ids[-1]  # 반올림 오차로 남는 인원은 마지막 세그먼트로

        trends["months"].append(month)
        for sid in seg_ids:
            trends[sid].append(int((assignment == sid).sum()))
        if prev_assignment is None:
            trends["new_entrants"].append(0)  # 근사 구간의 첫 달은 비교 대상이 없어 0으로 표기
        else:
            changed = int((assignment.values != prev_assignment.reindex(assignment.index).values).sum())
            trends["new_entrants"].append(changed)
        prev_assignment = assignment

    trends["approximate"] = True
    trends["approximate_note"] = (
        "이 구간은 월별 매출 증감률만으로 근사 추정한 값입니다 (세션·SNS·인구통계 데이터가 "
        "과거 시점에는 없어 실제 재클러스터링이 불가능함). 실제 배치가 쌓이면 해당 월부터 "
        "정확한 값으로 자동 대체됩니다."
    )

    with open(BACKFILL_PATH, "w", encoding="utf-8") as f:
        json.dump(trends, f, ensure_ascii=False, indent=2)

    print(f"근사 추이 {len(trends['months'])}개월치 저장 완료 -> {BACKFILL_PATH}")
    print("월별 세그먼트 규모:")
    for i, m in enumerate(trends["months"]):
        print(" ", m, {sid: trends[sid][i] for sid in seg_ids})


if __name__ == "__main__":
    main()
