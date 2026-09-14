"""
scripts/export_graph_csvs.py — Neo4j 그래프 DB 적재용 CSV를 xlsx에서 직접 생성하는 스크립트.

이전에는 이 CSV들(nodes_*.csv, rel_*.csv)을 분석 과정에서 1회성으로만 만들어서
GitHub data/graph/ 폴더에 수동으로 올렸습니다. 이 스크립트는 그 작업을
저장소 안에서 **몇 번이고 재현 가능한 절차**로 만든 것입니다.

xlsx 데이터가 바뀔 때마다(예: 새로운 세션·이벤트가 쌓이거나, 카테고리 동시조회 패턴이
바뀌었을 때) 아래 명령 한 줄로 Neo4j 적재용 CSV를 다시 뽑을 수 있습니다.

    python scripts/export_graph_csvs.py

산출물은 data/graph/ 에 저장됩니다. import_aura.cypher는 이 폴더의 파일들을
그대로 참조하므로, 새로 뽑은 CSV를 GitHub에 올리고 Neo4j에서 다시 임포트하면
그래프 DB가 최신 데이터로 갱신됩니다.

이번 버전에서 반영한 것 (xlsx에 새로 추가된 시트/컬럼):
  - '구매이력' 시트의 월매출_YYYY-MM_원 12개 컬럼 → Customer 노드 속성으로 반영
    (그래프에서도 "이 고객이 몇 월에 얼마를 썼는지"를 조회할 수 있게 됨)
  - '카테고리_동시조회_매트릭스' 시트 → 그대로 category_cooccurrence.csv로 저장
  - '상품_동시조회_TOP쌍' 시트 → 그대로 product_cooccurrence.csv로 저장
    (이전에는 이벤트 로그에서 직접 재계산했는데, 이제는 xlsx의 값을 그대로 신뢰합니다 —
     xlsx 시트가 "정답"이고 그래프는 그걸 그대로 옮겨 담는 것으로 역할을 명확히 했습니다)
"""

import os

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")
XLSX_PATH = os.path.join(DATA_DIR, "customer_embedding_training_data_2000_segmented.xlsx")
OUT_DIR = os.path.join(DATA_DIR, "graph")

MONTH_COL_PREFIX = "월매출_"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    sheets = pd.read_excel(XLSX_PATH, sheet_name=None)

    df_session = sheets["세션_마스터"]
    df_event = sheets["이벤트_로그"]
    df_product = sheets["상품_마스터"]
    df_purchase = sheets["구매이력"]
    df_cat_cooc = sheets["카테고리_동시조회_매트릭스"]
    df_prod_cooc = sheets["상품_동시조회_TOP쌍"]

    # ---------------- 노드 CSV ----------------
    month_cols = [c for c in df_purchase.columns if c.startswith(MONTH_COL_PREFIX)]

    customers = df_purchase[["고객ID"]].rename(columns={"고객ID": "customerId"}).drop_duplicates()
    customers = customers.merge(
        df_purchase[["고객ID", "구매_금액_총합_원", "매출_증감률_pct"] + month_cols]
        .rename(columns={
            "고객ID": "customerId",
            "구매_금액_총합_원": "totalAmount12m",
            "매출_증감률_pct": "salesGrowthPct",
        }),
        on="customerId", how="left",
    )
    # 월매출 컬럼명을 Neo4j 속성명으로 쓰기 좋게 정리 (월매출_2025-07_원 -> revenue_2025_07)
    rename_map = {c: "revenue_" + c.replace(MONTH_COL_PREFIX, "").replace("-", "_").replace("_원", "") for c in month_cols}
    customers = customers.rename(columns=rename_map)
    customers.to_csv(os.path.join(OUT_DIR, "nodes_customers.csv"), index=False, encoding="utf-8-sig")

    sessions = df_session.rename(columns={
        "세션ID": "sessionId", "세션_시작시각": "startTime", "세션_종료시각": "endTime",
        "총_이벤트수": "totalEvents", "구매_전환_여부": "converted",
    })[["sessionId", "startTime", "endTime", "totalEvents", "converted"]]
    sessions.to_csv(os.path.join(OUT_DIR, "nodes_sessions.csv"), index=False, encoding="utf-8-sig")

    devices = pd.DataFrame({"deviceType": df_session["디바이스_유형"].dropna().unique()})
    devices.to_csv(os.path.join(OUT_DIR, "nodes_devices.csv"), index=False, encoding="utf-8-sig")

    exitevents = pd.DataFrame({"exitEventType": df_session["최종_이벤트_유형"].dropna().unique()})
    exitevents.to_csv(os.path.join(OUT_DIR, "nodes_exitevents.csv"), index=False, encoding="utf-8-sig")

    products = df_product.rename(columns={
        "상품ID": "productId", "상품명": "name", "카테고리": "category", "브랜드": "brand", "가격": "price",
    })
    products[["productId", "name", "brand", "price"]].to_csv(
        os.path.join(OUT_DIR, "nodes_products.csv"), index=False, encoding="utf-8-sig")

    categories = pd.DataFrame({"category": df_product["카테고리"].dropna().unique()})
    categories.to_csv(os.path.join(OUT_DIR, "nodes_categories.csv"), index=False, encoding="utf-8-sig")

    # ---------------- 관계 CSV ----------------
    df_session.rename(columns={"고객ID": "customerId", "세션ID": "sessionId"})[["customerId", "sessionId"]] \
        .to_csv(os.path.join(OUT_DIR, "rel_has_session.csv"), index=False, encoding="utf-8-sig")

    df_session.rename(columns={"세션ID": "sessionId", "디바이스_유형": "deviceType"})[["sessionId", "deviceType"]] \
        .to_csv(os.path.join(OUT_DIR, "rel_used_device.csv"), index=False, encoding="utf-8-sig")

    df_session.rename(columns={"세션ID": "sessionId", "최종_이벤트_유형": "exitEventType"})[["sessionId", "exitEventType"]] \
        .to_csv(os.path.join(OUT_DIR, "rel_ended_at.csv"), index=False, encoding="utf-8-sig")

    df_event.dropna(subset=["상품ID"]).rename(columns={
        "세션ID": "sessionId", "상품ID": "productId", "이벤트_유형": "eventType", "이벤트_시각": "eventTime",
    })[["sessionId", "productId", "eventType", "eventTime"]].to_csv(
        os.path.join(OUT_DIR, "rel_viewed.csv"), index=False, encoding="utf-8-sig")

    df_product.rename(columns={"상품ID": "productId", "카테고리": "category"})[["productId", "category"]] \
        .to_csv(os.path.join(OUT_DIR, "rel_belongs_to.csv"), index=False, encoding="utf-8-sig")

    # ---------------- 동시조회 데이터 (xlsx 시트를 그대로 신뢰) ----------------
    df_cat_cooc.to_csv(os.path.join(OUT_DIR, "category_cooccurrence.csv"), index=False, encoding="utf-8-sig")
    df_prod_cooc.to_csv(os.path.join(OUT_DIR, "product_cooccurrence.csv"), index=False, encoding="utf-8-sig")

    # 위 두 시트는 그 자체로는 Neo4j에 적재되지 않으므로(참고용 CSV일 뿐),
    # 그래프에 실제 관계로 들어갈 수 있도록 긴 형식(long format)으로도 함께 만듭니다.
    cat_col = df_cat_cooc.columns[0]  # "카테고리"
    other_cats = [c for c in df_cat_cooc.columns if c != cat_col]
    cat_pairs = []
    for _, row in df_cat_cooc.iterrows():
        a = row[cat_col]
        for b in other_cats:
            if a < b and row[b] > 0:  # 대칭 행렬이라 한 방향만, 자기 자신 제외
                cat_pairs.append({"categoryA": a, "categoryB": b, "count": int(row[b])})
    pd.DataFrame(cat_pairs).to_csv(
        os.path.join(OUT_DIR, "rel_category_cooccurrence.csv"), index=False, encoding="utf-8-sig")

    df_prod_cooc.rename(columns={
        "상품ID_A": "productIdA", "상품ID_B": "productIdB", "동시조회_횟수": "count",
    })[["productIdA", "productIdB", "count"]].to_csv(
        os.path.join(OUT_DIR, "rel_product_cooccurrence.csv"), index=False, encoding="utf-8-sig")

    print("생성 완료 ->", OUT_DIR)
    for f in sorted(os.listdir(OUT_DIR)):
        path = os.path.join(OUT_DIR, f)
        n_rows = sum(1 for _ in open(path, encoding="utf-8-sig")) - 1
        print(f"  {f}: {n_rows}행")


if __name__ == "__main__":
    main()
