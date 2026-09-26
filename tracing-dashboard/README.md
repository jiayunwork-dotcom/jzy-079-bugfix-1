# tracing-dashboard

轻量分布式链路追踪看板。服务把调用片段（span）批量上报到后端，后端负责：

1. **校验与去重**：非法片段（缺追踪/片段编号、时间缺失或倒置等）被拒绝并返回结构化错误；同一 `span_id` 重复上报只保留最早一份。
2. **乱序拼接调用树**：子片段先到会先挂起等待父片段；父片段一到立即归位；超过可配置的最长等待时间仍等不到（或父编号根本不存在）则归到「父片段缺失」占位节点下，不影响其它片段入树。
3. **关键路径与耗时占比**：从根到最耗时叶子的路径、各服务在本次请求中的耗时占比。
4. **依赖图聚合与环检测**：持续从拼好的调用树提取「谁调用谁」，按最近 1 小时 / 1 天窗口聚合调用次数与平均耗时，Tarjan 算法标记成环边；切换窗口重新聚合，新旧窗口不串数据。
5. **实时推送**：新片段入库与依赖图更新通过 SSE 推到前端。

前端是纯展示看板：请求检索列表 + 树状瀑布图（失败片段高亮、点击看详情）+ 服务依赖图（节点大小按频次、环边高亮、窗口切换）。

## 目录结构

```
backend/
  app/
    main.py            # ASGI 入口与生命周期
    config.py          # 环境变量配置（最长等待时间、DB 路径等）
    models.py          # 请求/响应 schema 与结构化错误
    validation.py      # 片段校验（脏数据拒绝）
    storage.py         # SQLite 持久化
    tree.py            # 调用树拼接：挂起等待、占位节点、关键路径、耗时占比
    dependencies.py    # 依赖图聚合 + Tarjan 环检测
    events.py          # SSE 实时事件总线
    service.py         # 门面：串联存储 / 拼接 / 聚合
    api/
      __init__.py
      http.py          # HTTP 路由：上报、检索、树查询、依赖图
      push.py          # SSE 推送路由
      deps.py          # FastAPI 依赖注入
  tests/               # 自动化测试（乱序归位/去重/缺父不中断/顺序无关/环检测/脏数据拒绝/窗口切换）
frontend/
  src/
    main.jsx           # React 入口
    App.jsx            # 页面外壳与 Tab 切换
    api.js             # HTTP + SSE 客户端
    components/
      TraceList.jsx    # 请求检索列表
      Waterfall.jsx    # 树状瀑布图
      SpanDetail.jsx   # 片段详情
      DependencyGraph.jsx # 服务依赖图（含环边高亮）
docker-compose.yml     # 后端(python:3.12-slim) + 前端构建(node:20-alpine) + nginx(alpine)
```

## 一次构建启动

```bash
docker compose up --build
# 打开 http://localhost:8080
```

## 本地开发

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
pytest
cd ../frontend
npm install && npm run dev
```

## 上报示例

```bash
curl -X POST http://localhost:8080/api/spans \
  -H 'Content-Type: application/json' \
  -d '{"spans": [{
    "trace_id": "t1", "span_id": "s1", "parent_span_id": null,
    "service": "gateway", "start_time": 1000, "end_time": 1300,
    "status_code": 200
  }]}'
```

响应中 `accepted` / `rejected` 分别列出接受和拒绝的片段；被拒绝项带机器可读的 `code` 与字段级说明。
