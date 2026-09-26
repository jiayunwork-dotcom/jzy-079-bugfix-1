"""生成演示数据：正常调用树、失败片段、乱序上报、以及一条带环的依赖。

用法：python scripts/seed_demo.py [API_BASE_URL]
默认 POST 到 http://localhost:8000（容器内），宿主机用 http://localhost:8080。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
now = time.time() * 1000.0


def post(batch: list[dict]) -> None:
    req = urllib.request.Request(
        f"{BASE}/api/spans",
        data=json.dumps({"spans": batch}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        print(resp.status, resp.read().decode()[:200])


# 正常请求：gateway -> order -> db，含一个 500 失败片段
t1 = "demo-order-001"
post([
    {"trace_id": t1, "span_id": "gw", "parent_span_id": None,
     "service": "gateway", "start_time": now - 60_000, "end_time": now - 59_200,
     "status_code": 200},
    # 故意先发子片段再发父片段，演示乱序归位
    {"trace_id": t1, "span_id": "check", "parent_span_id": "order",
     "service": "inventory", "start_time": now - 59_900, "end_time": now - 59_600,
     "status_code": 200},
    {"trace_id": t1, "span_id": "charge", "parent_span_id": "order",
     "service": "payment", "start_time": now - 59_600, "end_time": now - 59_300,
     "status_code": 500},
    {"trace_id": t1, "span_id": "order", "parent_span_id": "gw",
     "service": "order", "start_time": now - 59_950, "end_time": now - 59_250,
     "status_code": 200},
])

# 带环依赖：gateway -> user -> profile -> user（profile 绕回调 user）
t2 = "demo-cycle-002"
post([
    {"trace_id": t2, "span_id": "r", "parent_span_id": None,
     "service": "gateway", "start_time": now - 30_000, "end_time": now - 29_000,
     "status_code": 200},
    {"trace_id": t2, "span_id": "u1", "parent_span_id": "r",
     "service": "user", "start_time": now - 29_900, "end_time": now - 29_200,
     "status_code": 200},
    {"trace_id": t2, "span_id": "p1", "parent_span_id": "u1",
     "service": "profile", "start_time": now - 29_800, "end_time": now - 29_400,
     "status_code": 200},
    {"trace_id": t2, "span_id": "u2", "parent_span_id": "p1",
     "service": "user", "start_time": now - 29_700, "end_time": now - 29_500,
     "status_code": 200},
])

# 父片段永远不到的孤儿片段（需要等待超时才会进占位节点）
t3 = "demo-orphan-003"
post([
    {"trace_id": t3, "span_id": "o1", "parent_span_id": "never-arrives",
     "service": "notify", "start_time": now - 10_000, "end_time": now - 9_500,
     "status_code": 200},
])

# 一条非法片段，展示结构化拒绝
post([
    {"trace_id": "", "span_id": "x", "parent_span_id": None,
     "service": "bad", "start_time": now, "end_time": now - 1,
     "status_code": 200},
])

print(f"\n演示数据已上报到 {BASE}，打开前端查看。")
