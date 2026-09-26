import React, { useEffect, useState } from 'react';
import TraceList from './components/TraceList.jsx';
import Waterfall from './components/Waterfall.jsx';
import DependencyGraph from './components/DependencyGraph.jsx';
import { subscribeEvents } from './api.js';

// 两个页面：请求检索（含瀑布图）和服务依赖图
export default function App() {
  const [page, setPage] = useState('traces');
  const [selectedTrace, setSelectedTrace] = useState(null);
  // 每次收到后端推送就 +1，触发各组件重新拉数据
  const [refreshTick, setRefreshTick] = useState(0);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const sub = subscribeEvents({
      hello: () => setConnected(true),
      span_update: () => setRefreshTick((n) => n + 1),
      graph_update: () => setRefreshTick((n) => n + 1),
    });
    return () => sub.close();
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <h1>链路追踪看板</h1>
        <nav>
          <button
            className={page === 'traces' ? 'active' : ''}
            onClick={() => setPage('traces')}
          >
            请求检索
          </button>
          <button
            className={page === 'graph' ? 'active' : ''}
            onClick={() => setPage('graph')}
          >
            服务依赖图
          </button>
        </nav>
        <span className={`conn ${connected ? 'on' : 'off'}`}>
          {connected ? '实时推送已连接' : '推送连接中…'}
        </span>
      </header>
      <main>
        {page === 'traces' ? (
          <div className="traces-page">
            <TraceList onSelect={setSelectedTrace} refreshTick={refreshTick} />
            {selectedTrace ? (
              <Waterfall traceId={selectedTrace} refreshTick={refreshTick} />
            ) : (
              <div className="placeholder-panel">从左侧列表选择一次请求查看瀑布图</div>
            )}
          </div>
        ) : (
          <DependencyGraph refreshTick={refreshTick} />
        )}
      </main>
    </div>
  );
}
