import React, { useCallback, useEffect, useState } from 'react';
import { listTraces } from '../api.js';

// 请求检索列表：按追踪编号或服务名过滤，点击行查看瀑布图
export default function TraceList({ onSelect, refreshTick }) {
  const [traceId, setTraceId] = useState('');
  const [service, setService] = useState('');
  const [traces, setTraces] = useState([]);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const data = await listTraces({
        traceId: traceId.trim() || undefined,
        service: service.trim() || undefined,
      });
      setTraces(data.traces);
      setError(null);
    } catch (err) {
      setError(String(err.message || err));
    }
  }, [traceId, service]);

  useEffect(() => {
    load();
  }, [load, refreshTick]);

  return (
    <section className="trace-list">
      <div className="filters">
        <input
          placeholder="追踪编号"
          value={traceId}
          onChange={(e) => setTraceId(e.target.value)}
        />
        <input
          placeholder="服务名"
          value={service}
          onChange={(e) => setService(e.target.value)}
        />
        <button onClick={load}>查询</button>
      </div>
      {error && <div className="error-banner">{error}</div>}
      <table>
        <thead>
          <tr>
            <th>追踪编号</th>
            <th>服务</th>
            <th>片段数</th>
            <th>耗时</th>
            <th>错误</th>
          </tr>
        </thead>
        <tbody>
          {traces.map((t) => (
            <tr key={t.trace_id} onClick={() => onSelect(t.trace_id)}>
              <td className="mono">{t.trace_id}</td>
              <td>{t.services.join(', ')}</td>
              <td>{t.span_count}</td>
              <td>{t.duration.toFixed(0)} ms</td>
              <td className={t.error_count > 0 ? 'has-error' : ''}>
                {t.error_count > 0 ? t.error_count : '—'}
              </td>
            </tr>
          ))}
          {traces.length === 0 && (
            <tr>
              <td colSpan={5} className="empty">
                暂无数据
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  );
}
