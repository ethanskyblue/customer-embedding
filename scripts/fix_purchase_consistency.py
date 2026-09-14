"""
scripts/fix_purchase_consistency.py — '구매이력' 시트의 논리적 모순을 바로잡는 1회성 패치 스크립트.

문제: 원본 데이터 생성 시 '최근_구매시점'과 '월매출_YYYY-MM_원' 12개 컬럼이 서로 완전히
독립적인 난수로 만들어져서, "최근 구매가 3월인데 4~6월에도 매출이 찍혀있는" 논리적으로
불가능한 상태가 2,000명 중 1,384명(69.2%)에서 발생했습니다.

이 스크립트가 하는 일:
  1) 고객별로 '최근_구매시점'이 속한 달 이후의 월매출을 전부 0으로 정리
  2) '구매_금액_총합_원'(12개월 합), '최근3개월_평균매출_원', '이전3개월_평균매출_원',
     '매출_증감률_pct'를 정리된 월매출 기준으로 재계산
  3) xlsx의 '구매이력' 시트만 교체 (다른 시트는 그대로 보존)

실행: python scripts/fix_purchase_consistency.py
"""

import os
import shutil

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")
XLSX_PATH = os.path.join(DATA_DIR, "customer_embedding_training_data_2000_segmented.xlsx")


def main():
    backup_path = XLSX_PATH + ".bak"
    if not os.path.exists(backup_path):
        shutil.copy(XLSX_PATH, backup_path)
        print(f"원본 백업 저장: {backup_path}")

    sheets = pd.read_excel(XLSX_PATH, sheet_name=None)
    df = sheets["구매이력"].copy()

    month_cols = sorted(c for c in df.columns if c.startswith("월매출_"))
    months = [c.replace("월매출_", "").replace("_원", "") for c in month_cols]  # "2025-07" ...

    last_purchase_month = pd.to_datetime(df["최근_구매시점"]).dt.strftime("%Y-%m")

    fixed_count = 0
    for i, row_month in enumerate(last_purchase_month):
        for m, col in zip(months, month_cols):
            if m > row_month and df.at[i, col] > 0:
                df.at[i, col] = 0
                fixed_count += 1

    print(f"0으로 정리한 셀 수: {fixed_count}")

    # 파생 컬럼 재계산
    df["구매_금액_총합_원"] = df[month_cols].sum(axis=1).astype(int)

    recent3_cols = month_cols[-3:]   # 최근 3개월 (2026-04, 05, 06)
    prior3_cols = month_cols[-6:-3]  # 그 이전 3개월 (2026-01, 02, 03)
    df["최근3개월_평균매출_원"] = df[recent3_cols].mean(axis=1).round().astype(int)
    df["이전3개월_평균매출_원"] = df[prior3_cols].mean(axis=1).round().astype(int)
    df["매출_증감률_pct"] = (
        (df["최근3개월_평균매출_원"] - df["이전3개월_평균매출_원"])
        / df["이전3개월_평균매출_원"].replace(0, pd.NA) * 100
    ).fillna(0).round(1)

    sheets["구매이력"] = df

    with pd.ExcelWriter(XLSX_PATH, engine="openpyxl") as writer:
        for name, sheet_df in sheets.items():
            sheet_df.to_excel(writer, sheet_name=name, index=False)

    print("xlsx 저장 완료 (구매이력 시트만 갱신, 나머지 시트는 그대로)")

    # 검증: 모순이 남아있는지 재확인
    check = pd.read_excel(XLSX_PATH, sheet_name="구매이력")
    check_last_month = pd.to_datetime(check["최근_구매시점"]).dt.strftime("%Y-%m")
    remaining = 0
    for i, row_month in enumerate(check_last_month):
        for m, col in zip(months, month_cols):
            if m > row_month and check.at[i, col] > 0:
                remaining += 1
                break
    print(f"검증: 여전히 모순이 남은 고객 수: {remaining}명 (0이어야 정상)")


if __name__ == "__main__":
    main()
