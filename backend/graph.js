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

async function closeDriver() {
  if (driver) {
    await driver.close();
    driver = null;
  }
}

module.exports = { findSimilarCustomers, isConfigured, closeDriver };
