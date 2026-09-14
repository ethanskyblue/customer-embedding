// graph.js — Neo4j 그래프 DB 연동 (고객 유사도 탐색)
//
// 이전에 만든 지식그래프(Customer-Session-Product-Category)에 접속해
// "이 고객과 비슷한 상품을 본 다른 고객"을 찾는 기능을 제공합니다.
//
// 필요 환경변수: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, (선택) NEO4J_DATABASE
// Aura 사용 시 NEO4J_URI는 콘솔에서 받은 'neo4j+s://xxxx.databases.neo4j.io' 형식입니다.

const neo4j = require("neo4j-driver");

let driver = null;

function isConfigured() {
  return !!(process.env.NEO4J_URI && process.env.NEO4J_USER && process.env.NEO4J_PASSWORD);
}

function getDriver() {
  if (driver) return driver;
  if (!isConfigured()) return null;
  driver = neo4j.driver(
    process.env.NEO4J_URI,
    neo4j.auth.basic(process.env.NEO4J_USER, process.env.NEO4J_PASSWORD)
  );
  return driver;
}

// 신경써야 할 부분: neo4j-driver는 큰 정수를 자바스크립트 number로 바로 못 바꾸는 경우가
// 있어 Integer 객체로 옵니다. toNumber()로 안전하게 변환합니다.
function toJsNumber(value) {
  if (value && typeof value.toNumber === "function") return value.toNumber();
  return value;
}

/**
 * 지정한 고객과 같은 상품을 많이 조회한 다른 고객을 찾습니다.
 * @param {string} customerId
 * @param {number} limit
 * @returns {Promise<{configured: boolean, results: Array<{customerId:string, sharedProducts:number}>}>}
 */
async function findSimilarCustomers(customerId, limit = 10) {
  const drv = getDriver();
  if (!drv) return { configured: false, results: [] };

  const session = drv.session({ database: process.env.NEO4J_DATABASE || "neo4j" });
  try {
    const result = await session.run(
      `MATCH (target:Customer {customerId: $customerId})-[:HAS_SESSION]->(:Session)-[:VIEWED]->(p:Product)
       WITH target, collect(DISTINCT p) AS targetProducts
       UNWIND targetProducts AS p
       MATCH (p)<-[:VIEWED]-(:Session)<-[:HAS_SESSION]-(other:Customer)
       WHERE other <> target
       WITH other, count(DISTINCT p) AS sharedProducts
       RETURN other.customerId AS customerId, sharedProducts
       ORDER BY sharedProducts DESC
       LIMIT $limit`,
      { customerId, limit: neo4j.int(limit) }
    );

    const results = result.records.map((r) => ({
      customerId: r.get("customerId"),
      sharedProducts: toJsNumber(r.get("sharedProducts")),
    }));
    return { configured: true, results };
  } finally {
    await session.close();
  }
}

/**
 * 비슷한 고객들이 봤지만 이 고객은 아직 안 본 상품을 추천합니다 (간단한 협업 필터링).
 * @param {string} customerId
 * @param {number} limit
 */
async function findRecommendedProducts(customerId, limit = 5) {
  const drv = getDriver();
  if (!drv) return { configured: false, results: [] };

  const session = drv.session({ database: process.env.NEO4J_DATABASE || "neo4j" });
  try {
    const result = await session.run(
      `MATCH (target:Customer {customerId: $customerId})-[:HAS_SESSION]->(:Session)-[:VIEWED]->(seen:Product)
       WITH target, collect(DISTINCT seen) AS seenProducts
       MATCH (target)-[:HAS_SESSION]->(:Session)-[:VIEWED]->(p:Product)<-[:VIEWED]-(:Session)<-[:HAS_SESSION]-(other:Customer)
       WHERE other <> target
       WITH target, seenProducts, other, count(DISTINCT p) AS overlap
       ORDER BY overlap DESC
       LIMIT 20
       MATCH (other)-[:HAS_SESSION]->(:Session)-[:VIEWED]->(rec:Product)
       WHERE NOT rec IN seenProducts
       RETURN rec.productId AS productId, rec.name AS name, rec.brand AS brand,
              count(DISTINCT other) AS recommendedByNCustomers
       ORDER BY recommendedByNCustomers DESC
       LIMIT $limit`,
      { customerId, limit: neo4j.int(limit) }
    );

    const results = result.records.map((r) => ({
      productId: r.get("productId"),
      name: r.get("name"),
      brand: r.get("brand"),
      recommendedByNCustomers: toJsNumber(r.get("recommendedByNCustomers")),
    }));
    return { configured: true, results };
  } finally {
    await session.close();
  }
}

/**
 * 특정 상품과 "함께 조회되는" 카테고리·상품을 찾습니다 (코디 추천용).
 * 실제 쇼핑몰의 상품 상세페이지·장바구니 화면에서 이 API를 호출해 위젯을 그리면 됩니다.
 * @param {string} productId
 */
async function findCoordinationRecommendations(productId, categoryLimit = 3, productsPerCategory = 3) {
  const drv = getDriver();
  if (!drv) return { configured: false, categories: [] };

  const session = drv.session({ database: process.env.NEO4J_DATABASE || "neo4j" });
  try {
    // 카테고리 동시조회는 이제 xlsx의 '카테고리_동시조회_매트릭스' 시트에서 그대로 가져온
    // CO_VIEWED_WITH 관계를 1홉만 타면 되므로, 예전 방식(VIEWED 2홉 탐색)보다 훨씬 가볍습니다.
    const catResult = await session.run(
      `MATCH (p:Product {productId: $productId})-[:BELONGS_TO]->(cat:Category)
       MATCH (cat)-[r:CO_VIEWED_WITH]-(otherCat:Category)
       WHERE otherCat <> cat
       RETURN otherCat.category AS category, r.count AS coViewSessions
       ORDER BY coViewSessions DESC
       LIMIT $categoryLimit`,
      { productId, categoryLimit: neo4j.int(categoryLimit) }
    );

    const categories = [];
    for (const record of catResult.records) {
      const category = record.get("category");
      const coViewSessions = toJsNumber(record.get("coViewSessions"));

      // 1순위: '상품_동시조회_TOP쌍' 시트에 실제로 이 상품과 함께 조회된 기록이 있는 상품
      const directResult = await session.run(
        `MATCH (p:Product {productId: $productId})-[:CO_VIEWED_WITH]-(rec:Product)-[:BELONGS_TO]->(:Category {category: $category})
         RETURN rec.productId AS productId, rec.name AS name, rec.brand AS brand, rec.price AS price
         ORDER BY rec.price
         LIMIT $limit`,
        { productId, category, limit: neo4j.int(productsPerCategory) }
      );

      let products = directResult.records.map((r) => ({
        productId: r.get("productId"), name: r.get("name"),
        brand: r.get("brand"), price: toJsNumber(r.get("price")),
      }));

      // 2순위(부족분 채우기): 직접 동시조회 기록은 없지만 같은 카테고리인 상품
      if (products.length < productsPerCategory) {
        const seen = new Set([productId, ...products.map((p) => p.productId)]);
        const fallbackResult = await session.run(
          `MATCH (rec:Product)-[:BELONGS_TO]->(:Category {category: $category})
           WHERE NOT rec.productId IN $seen
           RETURN rec.productId AS productId, rec.name AS name, rec.brand AS brand, rec.price AS price
           LIMIT $limit`,
          { category, seen: Array.from(seen), limit: neo4j.int(productsPerCategory - products.length) }
        );
        products = products.concat(fallbackResult.records.map((r) => ({
          productId: r.get("productId"), name: r.get("name"),
          brand: r.get("brand"), price: toJsNumber(r.get("price")),
        })));
      }

      categories.push({ category, coViewSessions, products });
    }
    return { configured: true, categories };
  } finally {
    await session.close();
  }
}

async function closeDriver() {
  if (driver) {
    await driver.close();
    driver = null;
  }
}

module.exports = { findSimilarCustomers, findRecommendedProducts, findCoordinationRecommendations, isConfigured, closeDriver };
