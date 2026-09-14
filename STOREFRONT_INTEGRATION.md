# 상품 코디 추천 API — 쇼핑몰 연동 가이드

| 항목 | 내용 |
|---|---|
| 문서 버전 | v1.0 |
| 대상 독자 | 쇼핑몰(스토어프론트) 프론트엔드/백엔드 개발팀 |
| API 제공처 | Customer Embedding App 백엔드 (`https://customer-embedding-0.onrender.com`) |
| 인증 | 없음 (공개 API, 민감 정보 미포함) |

---

## 1. 이 API가 하는 일

같은 세션에서 자주 "함께 조회된" 카테고리·상품을 계산해서, 지금 보고 있는 상품과 코디하면 좋은 상품을 추천합니다.
예를 들어 원피스 상품 페이지에서 이 API를 호출하면 "가방", "액세서리" 카테고리와 그 안의 실제 상품(이름·브랜드·가격)을 돌려줍니다.

**추천 로직 요약**: 그래프 DB(Neo4j)에 적재된 `(Session)-[:VIEWED]->(Product)-[:BELONGS_TO]->(Category)` 관계를 바탕으로,
동일 세션에서 함께 조회된 빈도가 높은 카테고리 순으로 추천합니다. 별도의 실시간 계산 없이 그래프 순회만으로 응답하므로 빠릅니다.

---

## 2. 엔드포인트 스펙

### `GET /api/v1/products/{productId}/coordination`

**Path Parameter**

| 이름 | 타입 | 설명 |
|---|---|---|
| `productId` | string | 상품 ID (예: `P00001`) |

**Query Parameter**: 없음 (카테고리 수·카테고리당 상품 수는 서버 기본값 사용 — 각각 3개)

**요청 예시**
```
GET https://customer-embedding-0.onrender.com/api/v1/products/P00001/coordination
```

**성공 응답 (200)**
```json
{
  "configured": true,
  "productId": "P00001",
  "categories": [
    {
      "category": "가방",
      "coViewSessions": 98,
      "products": [
        { "productId": "P00131", "name": "가방 스타일 001", "brand": "Common Days", "price": 124000 },
        { "productId": "P00132", "name": "가방 스타일 002", "brand": "Atelier Blanc", "price": 156000 },
        { "productId": "P00135", "name": "가방 스타일 005", "brand": "Nine Stitch", "price": 98000 }
      ]
    },
    {
      "category": "액세서리",
      "coViewSessions": 67,
      "products": [ ... ]
    }
  ]
}
```

| 필드 | 설명 |
|---|---|
| `configured` | 그래프 DB 연결 여부. `false`면 `categories`는 항상 빈 배열 |
| `categories[].category` | 추천 카테고리명 |
| `categories[].coViewSessions` | 이 카테고리가 원본 상품과 함께 조회된 세션 수 (추천 신뢰도 지표로 활용 가능) |
| `categories[].products` | 해당 카테고리의 대표 상품 (최대 3개) |

**응답이 비어 있는 경우 (200, categories: [])**
- 해당 상품의 동시조회 데이터가 부족한 경우 (신상품 등). **위젯 자체를 숨기세요.**

**오류 응답 (500)**
```json
{ "error": "graph_query_failed", "message": "코디 추천 조회에 실패했습니다." }
```
- 그래프 DB 조회 자체가 실패한 경우입니다. 이때도 **위젯을 숨기고 넘어가세요** (쇼핑 플로우를 막으면 안 됩니다).

**존재하지 않는 상품ID**: 빈 `categories` 배열과 함께 200 응답을 반환합니다 (별도의 404 처리 없음).

---

## 3. 언제 호출해야 하는가

| 시점 | 목적 | 권장 트리거 |
|---|---|---|
| 상품 상세페이지 | 이탈 방지 | 페이지 진입 후 **일정 시간(예: 15~20초) 체류** 또는 **50% 이상 스크롤** 시점에 호출 |
| 장바구니 담기 직후 | 객단가 상승 | 담기 완료 직후, 장바구니 화면 내 "함께 구매하면 좋은 상품" 영역에 호출 |
| 구매 완료 페이지 | 재방문·교차구매 유도 | 주문 완료 화면 하단에 호출 |

상세페이지에서는 **페이지 로드 즉시 호출하지 말고 지연 호출**을 권장합니다. 바로 뜨면 탐색을 방해하는 배너처럼 느껴지고,
실제로 저희 세그먼트 분석에서도 이탈이 상세조회 초반이 아니라 **체류 후반부**에 몰려 있었습니다 (관련 근거는 `PRD.md`/`TRD.md` 참고).

---

## 4. 연동 예시 코드

### Vanilla JS
```js
async function loadCoordinationWidget(productId, mountEl) {
  try {
    const res = await fetch(`https://customer-embedding-0.onrender.com/api/v1/products/${productId}/coordination`);
    if (!res.ok) return; // 실패 시 위젯 없이 조용히 넘어감
    const data = await res.json();
    if (!data.configured || data.categories.length === 0) return;

    mountEl.innerHTML = data.categories.map(cat => `
      <div class="coord-category">
        <h4>${cat.category}와(과) 함께 코디해보세요</h4>
        ${cat.products.map(p => `
          <div class="coord-product">
            <span>${p.name}</span><span>${p.brand}</span><span>${p.price.toLocaleString()}원</span>
          </div>`).join("")}
      </div>`).join("");
  } catch (e) {
    console.warn("코디 추천 로드 실패, 위젯 생략:", e);
  }
}

// 상세페이지: 18초 체류 후 호출
setTimeout(() => loadCoordinationWidget(currentProductId, document.getElementById("coord-widget")), 18000);

// 장바구니 담기 직후 호출
onAddToCart((productId) => loadCoordinationWidget(productId, document.getElementById("cart-coord-widget")));
```

### React (예시)
```jsx
function CoordinationWidget({ productId }) {
  const [categories, setCategories] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`https://customer-embedding-0.onrender.com/api/v1/products/${productId}/coordination`)
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (!cancelled && data?.configured && data.categories.length > 0) {
          setCategories(data.categories);
        }
      })
      .catch(() => {}); // 실패 시 조용히 무시
    return () => { cancelled = true; };
  }, [productId]);

  if (!categories) return null; // 데이터 없으면 아예 렌더링하지 않음

  return (
    <div className="coordination-widget">
      {categories.map(cat => (
        <div key={cat.category}>
          <h4>{cat.category}와(과) 함께 코디해보세요</h4>
          {cat.products.map(p => (
            <div key={p.productId}>{p.name} · {p.brand} · {p.price.toLocaleString()}원</div>
          ))}
        </div>
      ))}
    </div>
  );
}
```

### Next.js (App Router, TypeScript)

Next.js는 서버 컴포넌트가 기본이지만, 이 위젯은 체류 시간 타이머·장바구니 클릭 등 **브라우저 이벤트가 필요해서 클라이언트 컴포넌트**로 작성합니다.
API 주소는 브라우저에서 직접 호출하므로 `NEXT_PUBLIC_` 접두사가 붙은 환경변수로 노출해야 합니다.

**`.env.local`**
```
NEXT_PUBLIC_COORDINATION_API_BASE=https://customer-embedding-0.onrender.com
```

**`lib/coordination.ts`** — 타입 정의 + 타임아웃 포함 fetch 헬퍼
```ts
export type CoordinationProduct = {
  productId: string;
  name: string;
  brand: string;
  price: number;
};

export type CoordinationCategory = {
  category: string;
  coViewSessions: number;
  products: CoordinationProduct[];
};

export type CoordinationResponse = {
  configured: boolean;
  productId: string;
  categories: CoordinationCategory[];
};

const API_BASE = process.env.NEXT_PUBLIC_COORDINATION_API_BASE ?? "";

/**
 * 코디 추천을 가져옵니다. 실패 시(네트워크 오류, 타임아웃, 500 등) null을 반환하고
 * 절대 throw하지 않습니다 — 호출부에서 위젯을 조용히 숨기기만 하면 되도록 설계했습니다.
 */
export async function fetchCoordination(
  productId: string,
  timeoutMs = 4000
): Promise<CoordinationCategory[] | null> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(`${API_BASE}/api/v1/products/${encodeURIComponent(productId)}/coordination`, {
      signal: controller.signal,
    });
    if (!res.ok) return null;

    const data: CoordinationResponse = await res.json();
    if (!data.configured || data.categories.length === 0) return null;

    return data.categories;
  } catch {
    return null; // 네트워크 오류/타임아웃 — 조용히 무시
  } finally {
    clearTimeout(timeout);
  }
}
```

**`components/CoordinationWidget.tsx`** — 클라이언트 컴포넌트 (체류 시간 트리거 내장)
```tsx
"use client";

import { useEffect, useState } from "react";
import { fetchCoordination, type CoordinationCategory } from "@/lib/coordination";

type Props = {
  productId: string;
  /** 페이지 진입 후 몇 ms 뒤에 호출할지 (상세페이지 이탈 방지용, 기본 18초) */
  delayMs?: number;
};

export default function CoordinationWidget({ productId, delayMs = 18000 }: Props) {
  const [categories, setCategories] = useState<CoordinationCategory[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(async () => {
      const result = await fetchCoordination(productId);
      if (!cancelled) setCategories(result);
    }, delayMs);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [productId, delayMs]);

  if (!categories) return null; // 데이터 없거나 아직 로딩 전이면 아예 렌더링 안 함

  return (
    <div className="coordination-widget">
      {categories.map((cat) => (
        <div key={cat.category} className="coordination-category">
          <h4>{cat.category}와(과) 함께 코디해보세요</h4>
          <div className="coordination-products">
            {cat.products.map((p) => (
              <div key={p.productId} className="coordination-product">
                <span>{p.name}</span>
                <span>{p.brand}</span>
                <span>{p.price.toLocaleString()}원</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
```

**상품 상세페이지에서 사용** (`app/products/[productId]/page.tsx`, 서버 컴포넌트)
```tsx
import CoordinationWidget from "@/components/CoordinationWidget";

export default async function ProductPage({ params }: { params: { productId: string } }) {
  // ... 상품 상세 정보 조회 (서버 컴포넌트라 여기서 자유롭게 DB/API 호출 가능)

  return (
    <main>
      {/* 상품 상세 UI */}
      <CoordinationWidget productId={params.productId} delayMs={18000} />
    </main>
  );
}
```

**장바구니 담기 직후 즉시 호출하고 싶을 때** (delay 없이 바로 트리거)
```tsx
"use client";
import { useState } from "react";
import { fetchCoordination, type CoordinationCategory } from "@/lib/coordination";

export function AddToCartButton({ productId }: { productId: string }) {
  const [afterCartCategories, setAfterCartCategories] = useState<CoordinationCategory[] | null>(null);

  async function handleAddToCart() {
    await addToCart(productId); // 실제 장바구니 담기 로직
    const result = await fetchCoordination(productId); // delay 없이 바로 호출
    setAfterCartCategories(result);
  }

  return (
    <>
      <button onClick={handleAddToCart}>장바구니 담기</button>
      {afterCartCategories && (
        <div className="cart-coordination">
          {/* 위 CoordinationWidget과 동일한 방식으로 렌더링 */}
        </div>
      )}
    </>
  );
}
```

---

## 5. 실패 처리 원칙 (중요)

- **이 API 실패가 쇼핑 플로우를 막아서는 안 됩니다.** 네트워크 오류, 500 에러, 빈 응답 등 모든 실패 케이스에서 위젯을 그냥 숨기고 넘어가세요 (사용자에게 에러 메시지를 보여주지 마세요).
- 타임아웃을 3~5초로 짧게 설정해서, 응답이 느려도 페이지 로딩이 지연되지 않게 해주세요.
- 위 예시 코드처럼 try/catch로 감싸고, 실패 시 조용히 종료하는 패턴을 유지해주세요.

---

## 6. 캐싱 권장

같은 상품의 코디 추천 결과는 하루 안에는 거의 바뀌지 않습니다 (배치로 계산되는 그래프 데이터 기반). 아래 중 하나를 권장합니다.
- 브라우저 `sessionStorage`에 `productId`별로 캐싱 (같은 세션 내 재호출 방지)
- CDN/프록시 레이어에서 짧은 TTL(예: 1시간) 캐싱

---

## 7. 사전 테스트 방법

배포 전 아래 URL을 브라우저로 직접 열어 응답을 확인해보세요 (상품ID는 `P00001`~`P00180` 범위).
```
https://customer-embedding-0.onrender.com/api/v1/products/P00001/coordination
```

## 8. 향후 확장 (현재 미구현)

- 위젯 클릭/노출 이벤트를 다시 수집해 추천 성과(클릭률, 전환 기여도)를 측정하는 별도 엔드포인트
- 카테고리 수·상품 수를 쿼리 파라미터로 조절하는 기능
- 상품이 아닌 "장바구니 전체 구성"을 입력으로 받는 다중 상품 코디 추천

필요해지면 알려주세요 — 위 확장 기능도 이어서 추가할 수 있습니다.
