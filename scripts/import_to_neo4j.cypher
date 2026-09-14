// ============================================================
// 세션/이벤트/상품 데이터를 Neo4j Aura에 적재하는 스크립트
// data/graph/ 의 CSV는 scripts/export_graph_csvs.py 로 생성합니다.
// console.neo4j.io → 인스턴스 → Query 화면에서 구간별로 붙여넣어 실행하세요.
//
// 저장소 경로가 다르면 아래 RAW_BASE 부분의 GitHub 계정/저장소명만 바꿔주세요.
// RAW_BASE = https://raw.githubusercontent.com/{계정}/{저장소}/{브랜치}/data/graph
// ============================================================

// ---------- 0. 제약조건 (중복 방지 + 조회 속도 향상, 최초 1회만) ----------
CREATE CONSTRAINT customer_id IF NOT EXISTS FOR (c:Customer) REQUIRE c.customerId IS UNIQUE;
CREATE CONSTRAINT session_id  IF NOT EXISTS FOR (s:Session)  REQUIRE s.sessionId  IS UNIQUE;
CREATE CONSTRAINT product_id  IF NOT EXISTS FOR (p:Product)  REQUIRE p.productId  IS UNIQUE;
CREATE CONSTRAINT device_type IF NOT EXISTS FOR (d:Device)   REQUIRE d.deviceType IS UNIQUE;
CREATE CONSTRAINT exit_type   IF NOT EXISTS FOR (e:ExitEvent) REQUIRE e.exitEventType IS UNIQUE;
CREATE CONSTRAINT category_name IF NOT EXISTS FOR (k:Category) REQUIRE k.category IS UNIQUE;

// ---------- 1. 노드 생성 ----------

// Customer: 이번에 추가된 월매출 12개월치 + 매출 증감률을 속성으로 반영
// (구매이력 시트의 월매출_YYYY-MM_원 컬럼이 revenue_YYYY_MM 속성으로 들어갑니다)
LOAD CSV WITH HEADERS FROM 'https://raw.githubusercontent.com/ethanskyblue/customer-embedding/main/data/graph/nodes_customers.csv' AS row
CALL (row) {
  MERGE (c:Customer {customerId: row.customerId})
  SET c.totalAmount12m = toInteger(row.totalAmount12m),
      c.salesGrowthPct = toFloat(row.salesGrowthPct),
      c.revenue_2025_07 = toInteger(row.revenue_2025_07),
      c.revenue_2025_08 = toInteger(row.revenue_2025_08),
      c.revenue_2025_09 = toInteger(row.revenue_2025_09),
      c.revenue_2025_10 = toInteger(row.revenue_2025_10),
      c.revenue_2025_11 = toInteger(row.revenue_2025_11),
      c.revenue_2025_12 = toInteger(row.revenue_2025_12),
      c.revenue_2026_01 = toInteger(row.revenue_2026_01),
      c.revenue_2026_02 = toInteger(row.revenue_2026_02),
      c.revenue_2026_03 = toInteger(row.revenue_2026_03),
      c.revenue_2026_04 = toInteger(row.revenue_2026_04),
      c.revenue_2026_05 = toInteger(row.revenue_2026_05),
      c.revenue_2026_06 = toInteger(row.revenue_2026_06)
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'https://raw.githubusercontent.com/ethanskyblue/customer-embedding/main/data/graph/nodes_sessions.csv' AS row
CALL (row) {
  MERGE (s:Session {sessionId: row.sessionId})
  SET s.startTime = datetime(replace(row.startTime, ' ', 'T')),
      s.endTime    = datetime(replace(row.endTime, ' ', 'T')),
      s.totalEvents = toInteger(row.totalEvents),
      s.converted   = (row.converted = 'True')
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'https://raw.githubusercontent.com/ethanskyblue/customer-embedding/main/data/graph/nodes_devices.csv' AS row
CALL (row) {
  MERGE (d:Device {deviceType: row.deviceType})
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'https://raw.githubusercontent.com/ethanskyblue/customer-embedding/main/data/graph/nodes_exitevents.csv' AS row
CALL (row) {
  MERGE (e:ExitEvent {exitEventType: row.exitEventType})
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'https://raw.githubusercontent.com/ethanskyblue/customer-embedding/main/data/graph/nodes_products.csv' AS row
CALL (row) {
  MERGE (p:Product {productId: row.productId})
  SET p.name  = row.name,
      p.brand = row.brand,
      p.price = toInteger(row.price)
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'https://raw.githubusercontent.com/ethanskyblue/customer-embedding/main/data/graph/nodes_categories.csv' AS row
CALL (row) {
  MERGE (k:Category {category: row.category})
} IN TRANSACTIONS OF 1000 ROWS;

// ---------- 2. 관계 생성 ----------
LOAD CSV WITH HEADERS FROM 'https://raw.githubusercontent.com/ethanskyblue/customer-embedding/main/data/graph/rel_has_session.csv' AS row
CALL (row) {
  MATCH (c:Customer {customerId: row.customerId})
  MATCH (s:Session {sessionId: row.sessionId})
  MERGE (c)-[:HAS_SESSION]->(s)
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'https://raw.githubusercontent.com/ethanskyblue/customer-embedding/main/data/graph/rel_used_device.csv' AS row
CALL (row) {
  MATCH (s:Session {sessionId: row.sessionId})
  MATCH (d:Device {deviceType: row.deviceType})
  MERGE (s)-[:USED_DEVICE]->(d)
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'https://raw.githubusercontent.com/ethanskyblue/customer-embedding/main/data/graph/rel_ended_at.csv' AS row
CALL (row) {
  MATCH (s:Session {sessionId: row.sessionId})
  MATCH (e:ExitEvent {exitEventType: row.exitEventType})
  MERGE (s)-[:ENDED_AT]->(e)
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'https://raw.githubusercontent.com/ethanskyblue/customer-embedding/main/data/graph/rel_viewed.csv' AS row
CALL (row) {
  MATCH (s:Session {sessionId: row.sessionId})
  MATCH (p:Product {productId: row.productId})
  // eventTime을 매칭 조건에 포함시켜, 같은 세션에서 같은 상품을 여러 이벤트(상세조회 -> 찜하기 등)로
  // 봤을 때도 각각 별도의 관계로 남도록 합니다. (eventTime만으로는 완전히 유일하지 않을 수 있어
  // eventType까지 함께 매칭 조건에 둡니다.)
  MERGE (s)-[r:VIEWED {eventType: row.eventType, eventTime: datetime(replace(row.eventTime, ' ', 'T'))}]->(p)
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'https://raw.githubusercontent.com/ethanskyblue/customer-embedding/main/data/graph/rel_belongs_to.csv' AS row
CALL (row) {
  MATCH (p:Product {productId: row.productId})
  MATCH (k:Category {category: row.category})
  MERGE (p)-[:BELONGS_TO]->(k)
} IN TRANSACTIONS OF 1000 ROWS;

// 카테고리 동시조회 (구매이력 워크북의 '카테고리_동시조회_매트릭스' 시트를 그대로 반영)
LOAD CSV WITH HEADERS FROM 'https://raw.githubusercontent.com/ethanskyblue/customer-embedding/main/data/graph/rel_category_cooccurrence.csv' AS row
CALL (row) {
  MATCH (a:Category {category: row.categoryA})
  MATCH (b:Category {category: row.categoryB})
  MERGE (a)-[r:CO_VIEWED_WITH]->(b)
  SET r.count = toInteger(row.count)
} IN TRANSACTIONS OF 1000 ROWS;

// 상품 동시조회 ('상품_동시조회_TOP쌍' 시트를 그대로 반영)
LOAD CSV WITH HEADERS FROM 'https://raw.githubusercontent.com/ethanskyblue/customer-embedding/main/data/graph/rel_product_cooccurrence.csv' AS row
CALL (row) {
  MATCH (a:Product {productId: row.productIdA})
  MATCH (b:Product {productId: row.productIdB})
  MERGE (a)-[r:CO_VIEWED_WITH]->(b)
  SET r.count = toInteger(row.count)
} IN TRANSACTIONS OF 1000 ROWS;

// ---------- 3. 적재 확인 ----------
MATCH (n) RETURN labels(n)[0] AS 노드타입, count(*) AS 개수 ORDER BY 개수 DESC;

MATCH ()-[r]->() RETURN type(r) AS 관계타입, count(*) AS 개수 ORDER BY 개수 DESC;

// 신규 반영 확인: 특정 고객의 월매출 이력이 잘 들어갔는지
// MATCH (c:Customer {customerId:'CUST00001'}) RETURN c.revenue_2026_06, c.salesGrowthPct;
