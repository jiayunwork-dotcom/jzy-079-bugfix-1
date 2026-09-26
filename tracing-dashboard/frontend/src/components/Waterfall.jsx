import React, { useEffect, useMemo, useState } from 'react';
import { getTrace } from '../api.js';
import SpanDetail from './SpanDetail.jsx';

// 把树拍平成带缩进层级的行序列，占位节点也算一行
function flatten(nodes, rows = []) {
  for (const node of nodes) {
    rows.push(node);
    flatten(node.children || [], rows);
  }
  return rows;
}

// 树状瀑布图：每个片段一条横条，长度=耗时，缩进=调用层级
export default function Waterfall({ traceId, refreshTick }) {
  const [tree, setTree] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    let cancelled = false;
    getTrace(traceId)
      .then((data) => {
        if (!cancelled) {
          setTree(data);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(String(err.message || err));
      });
    return () => {
      cancelled = true;
    };
  }, [traceId, refreshTick]);

  const rows = useMemo(() => (tree ? flatten(tree.roots) : []), [tree]);
  const criticalSet = useMemo(
    () => new Set(tree?.critical_path || []),
    [tree]
  );

  if (error) return <div className="error-banner">{error}</div>;
  if (!tree) return <div className="placeholder-panel">加载中…</div>;

  const traceStart = tree.trace_start;
  const traceDuration = Math.max(tree.duration, 1);

  return (
    <section className="waterfall">
      <div className="waterfall-header">
        <h2>
          Trace <span className="mono">{tree.trace_id}</span>
        </h2>
        <div className="meta">
          <span>总耗时 {tree.duration.toFixed(0)} ms</span>
          <span>{tree.span_count} 个片段</span>
          {!tree.complete && (
            <span className="pending-badge">
              {tree.pending_count} 个片段等待父片段
            </span>
          )}
        </div>
      </div>

      <div className="breakdown">
        {tree.service_breakdown.map((b) => (
          <div key={b.service} className="breakdown-item">
            <span className="svc">{b.service}</span>
            <div className="bar-track">
              <div className="bar-fill" style={{ width: `${b.percent}%` }} />
            </div>
            <span className="pct">{b.percent}%</span>
          </div>
        ))}
      </div>

      <div className="rows">
        {rows.map((node) => {
          if (node.type === 'placeholder') {
            return (
              <div key={node.span_id} className="row placeholder-row">
                <div
                  className="label"
                  style={{ paddingLeft: node.depth * 18 }}
                >
                  ⚠ 父片段缺失（{node.missing_parent_span_id}）
                  {!node.committed && ' · 等待中'}
                </div>
                <div className="track" />
              </div>
            );
          }
          const left = ((node.start_time - traceStart) / traceDuration) * 100;
          const width = Math.max((node.duration / traceDuration) * 100, 0.4);
          const classes = ['row'];
          if (node.is_error) classes.push('error-row');
          if (criticalSet.has(node.span_id)) classes.push('critical-row');
          return (
            <div
              key={node.span_id}
              className={classes.join(' ')}
              onClick={() => setSelected(node)}
            >
              <div className="label" style={{ paddingLeft: node.depth * 18 }}>
                <span className="svc">{node.service}</span>
                <span className="mono sid">{node.span_id}</span>
              </div>
              <div className="track">
                <div
                  className={`span-bar ${node.is_error ? 'error' : ''}`}
                  style={{ left: `${left}%`, width: `${width}%` }}
                  title={`${node.duration.toFixed(1)} ms`}
                />
              </div>
            </div>
          );
        })}
      </div>

      {selected && (
        <SpanDetail span={selected} onClose={() => setSelected(null)} />
      )}
    </section>
  );
}
