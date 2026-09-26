import React, { useEffect, useMemo, useState } from 'react';
import { getGraph, WINDOWS } from '../api.js';

// 轻量力导向布局：不引图库，几十次迭代足够看板使用
function layoutGraph(nodes, edges, width, height) {
  const positions = new Map();
  const n = nodes.length;
  nodes.forEach((node, i) => {
    const angle = (2 * Math.PI * i) / Math.max(n, 1);
    positions.set(node.id, {
      x: width / 2 + Math.cos(angle) * width * 0.3,
      y: height / 2 + Math.sin(angle) * height * 0.3,
    });
  });
  const repulsion = 9000;
  const spring = 0.02;
  const ideal = Math.min(width, height) / 3;
  for (let iter = 0; iter < 300; iter++) {
    const forces = new Map(nodes.map((nd) => [nd.id, { fx: 0, fy: 0 }]));
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const a = positions.get(nodes[i].id);
        const b = positions.get(nodes[j].id);
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let dist2 = dx * dx + dy * dy || 1;
        const f = repulsion / dist2;
        const dist = Math.sqrt(dist2);
        dx /= dist;
        dy /= dist;
        forces.get(nodes[i].id).fx += dx * f;
        forces.get(nodes[i].id).fy += dy * f;
        forces.get(nodes[j].id).fx -= dx * f;
        forces.get(nodes[j].id).fy -= dy * f;
      }
    }
    for (const edge of edges) {
      const a = positions.get(edge.source);
      const b = positions.get(edge.target);
      if (!a || !b) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (dist - ideal) * spring;
      forces.get(edge.source).fx += (dx / dist) * f;
      forces.get(edge.source).fy += (dy / dist) * f;
      forces.get(edge.target).fx -= (dx / dist) * f;
      forces.get(edge.target).fy -= (dy / dist) * f;
    }
    for (const node of nodes) {
      const pos = positions.get(node.id);
      const { fx, fy } = forces.get(node.id);
      pos.x = Math.min(width - 60, Math.max(60, pos.x + fx * 0.5));
      pos.y = Math.min(height - 40, Math.max(40, pos.y + fy * 0.5));
    }
  }
  return positions;
}

const WIDTH = 900;
const HEIGHT = 560;

// 服务依赖图：节点大小按调用频次，环边红色高亮，窗口可切换
export default function DependencyGraph({ refreshTick }) {
  const [windowKey, setWindowKey] = useState('1h');
  const [graph, setGraph] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    getGraph(windowKey)
      .then((data) => {
        if (!cancelled) {
          setGraph(data);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(String(err.message || err));
      });
    return () => {
      cancelled = true;
    };
  }, [windowKey, refreshTick]);

  const positions = useMemo(() => {
    if (!graph || graph.nodes.length === 0) return new Map();
    return layoutGraph(graph.nodes, graph.edges, WIDTH, HEIGHT);
  }, [graph]);

  const maxCalls = useMemo(
    () => Math.max(1, ...(graph?.nodes.map((n) => n.call_count) || [1])),
    [graph]
  );

  return (
    <section className="dep-graph">
      <div className="graph-toolbar">
        <div className="window-switch">
          {WINDOWS.map((w) => (
            <button
              key={w.key}
              className={windowKey === w.key ? 'active' : ''}
              onClick={() => setWindowKey(w.key)}
            >
              {w.label}
            </button>
          ))}
        </div>
        {graph && (
          <span className="graph-stats">
            {graph.nodes.length} 个服务 · {graph.edges.length} 条调用边
            {graph.edges.some((e) => e.in_cycle) && (
              <span className="cycle-warning">⚠ 检测到循环依赖</span>
            )}
          </span>
        )}
      </div>
      {error && <div className="error-banner">{error}</div>}
      {!graph || graph.nodes.length === 0 ? (
        <div className="placeholder-panel">当前窗口内没有调用数据</div>
      ) : (
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="graph-svg">
          <defs>
            <marker
              id="arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#8a94a6" />
            </marker>
            <marker
              id="arrow-cycle"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#e5484d" />
            </marker>
          </defs>
          {graph.edges.map((edge) => {
            const a = positions.get(edge.source);
            const b = positions.get(edge.target);
            if (!a || !b) return null;
            const selfLoop = edge.source === edge.target;
            const midX = (a.x + b.x) / 2;
            const midY = (a.y + b.y) / 2 - (selfLoop ? 60 : 20);
            const cls = edge.in_cycle ? 'edge cycle' : 'edge';
            return (
              <g key={`${edge.source}->${edge.target}`}>
                {selfLoop ? (
                  <circle
                    cx={a.x}
                    cy={a.y - 45}
                    r={22}
                    className={cls}
                    fill="none"
                    markerEnd={`url(#${edge.in_cycle ? 'arrow-cycle' : 'arrow'})`}
                  />
                ) : (
                  <path
                    d={`M ${a.x} ${a.y} Q ${midX} ${midY} ${b.x} ${b.y}`}
                    className={cls}
                    fill="none"
                    markerEnd={`url(#${edge.in_cycle ? 'arrow-cycle' : 'arrow'})`}
                  />
                )}
                <text x={midX} y={midY - 4} className="edge-label">
                  {edge.count} 次 · 均 {edge.avg_duration.toFixed(0)}ms
                  {edge.in_cycle ? ' · 环' : ''}
                </text>
              </g>
            );
          })}
          {graph.nodes.map((node) => {
            const pos = positions.get(node.id);
            if (!pos) return null;
            const r = 16 + 22 * (node.call_count / maxCalls);
            return (
              <g key={node.id}>
                <circle cx={pos.x} cy={pos.y} r={r} className="node" />
                <text x={pos.x} y={pos.y + 4} className="node-label">
                  {node.id}
                </text>
                <text x={pos.x} y={pos.y + r + 14} className="node-sub">
                  {node.call_count} 次调用
                </text>
              </g>
            );
          })}
        </svg>
      )}
    </section>
  );
}
