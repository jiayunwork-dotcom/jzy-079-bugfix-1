import React from 'react';

// 点击瀑布图某个片段后展开的完整信息面板
export default function SpanDetail({ span, onClose }) {
  return (
    <div className="span-detail">
      <div className="detail-header">
        <h3>片段详情</h3>
        <button onClick={onClose}>关闭</button>
      </div>
      <dl>
        <dt>片段编号</dt>
        <dd className="mono">{span.span_id}</dd>
        <dt>父片段编号</dt>
        <dd className="mono">{span.parent_span_id ?? '（根片段）'}</dd>
        <dt>服务</dt>
        <dd>{span.service}</dd>
        <dt>开始时间</dt>
        <dd>{span.start_time}</dd>
        <dt>结束时间</dt>
        <dd>{span.end_time}</dd>
        <dt>耗时</dt>
        <dd>{span.duration.toFixed(1)} ms</dd>
        <dt>状态码</dt>
        <dd className={span.is_error ? 'has-error' : ''}>{span.status_code}</dd>
      </dl>
    </div>
  );
}
