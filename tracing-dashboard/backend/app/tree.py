"""调用树拼接核心。

职责：
* 按 trace_id 聚拢片段，按 parent_span_id 拼树；
* 处理三类乱序/异常情况：
  - 父片段晚到：子片段先进入 pending 挂起，父片段一到立即归位；
  - 超过 max_wait_ms 仍等不到父片段：归到「父片段缺失」占位节点；
  - 父编号指向不存在的片段：同样走超时占位流程，单条坏引用不会让整棵树失败；
* 同一 span_id 重复上报：只认最早一份（add_span 返回 duplicate）；
* 关键路径：根到最耗时叶子的最长路径（路径权重 = 路径上各片段耗时之和）；
* 服务耗时占比：按各片段的独占时间（扣掉被子片段覆盖的区间）汇总，避免嵌套重复计数。

本模块不依赖 FastAPI、不依赖存储，可独立单测；时间通过参数注入，测试里可自由拨动时钟。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .models import SpanRecord

# 占位节点 id 前缀，完整 id = 前缀 + 缺失的父片段编号
PLACEHOLDER_PREFIX = "__missing_parent__:"


def placeholder_id(missing_parent_span_id: str) -> str:
    return f"{PLACEHOLDER_PREFIX}{missing_parent_span_id}"


def is_placeholder(node_id: str) -> bool:
    return node_id.startswith(PLACEHOLDER_PREFIX)


@dataclass
class _TraceState:
    trace_id: str
    spans: dict[str, SpanRecord] = field(default_factory=dict)
    # 子节点 -> 父节点（父可能是真实片段，也可能是已提交的占位节点）
    parent_of: dict[str, str] = field(default_factory=dict)
    # 父节点 id -> 子片段 id 列表（真实片段或占位节点都可能做 key）
    children: dict[str, list[str]] = field(default_factory=dict)
    # 仍在等待父片段的子片段: span_id -> (父片段 id, 到达时间 ms)
    pending: dict[str, tuple[str, float]] = field(default_factory=dict)
    # 已提交的占位节点: 缺失的父编号 -> 占位节点 id
    placeholders: dict[str, str] = field(default_factory=dict)
    # 显式根片段（parent_span_id 为 None）的集合
    root_span_ids: set[str] = field(default_factory=set)


class TraceAssembler:
    def __init__(self, max_wait_ms: float = 30_000.0) -> None:
        self.max_wait_ms = max_wait_ms
        self._traces: dict[str, _TraceState] = {}

    # ------------------------------------------------------------------ 入库

    def add_span(
        self, span: SpanRecord, now_ms: Optional[float] = None
    ) -> str:
        """接收一个已校验片段。

        返回 ``"added"`` 或 ``"duplicate"``。重复 span_id 直接忽略，
        同一次调用不会在树里出现两次。
        """
        state = self._traces.setdefault(span.trace_id, _TraceState(span.trace_id))
        if span.span_id in state.spans:
            return "duplicate"
        if now_ms is None:
            now_ms = time.time() * 1000.0

        state.spans[span.span_id] = span

        if span.parent_span_id is None:
            state.parent_of[span.span_id] = None  # type: ignore[assignment]
            state.root_span_ids.add(span.span_id)
        elif span.parent_span_id in state.spans:
            self._attach(state, span.span_id, span.parent_span_id)
        else:
            # 父片段还没到：先挂起等待，不直接归到占位节点
            state.pending[span.span_id] = (span.parent_span_id, now_ms)

        # 本片段可能正是别的挂起子片段在等的父片段——立即让它们归位，
        # 即使这些子片段此前已超时落到占位节点下，父片段出现也要重新挂正。
        self._resolve_waiters(state, span.span_id)
        return "added"

    def _attach(self, state: _TraceState, span_id: str, parent_id: str) -> None:
        state.parent_of[span_id] = parent_id
        state.children.setdefault(parent_id, []).append(span_id)

    def _resolve_waiters(self, state: _TraceState, arrived_parent_id: str) -> None:
        # 1) 仍在 pending 的子片段立即挂到真实父片段下
        for child_id, (parent_id, _at) in list(state.pending.items()):
            if parent_id == arrived_parent_id:
                del state.pending[child_id]
                self._attach(state, child_id, arrived_parent_id)

        # 2) 父片段在子片段超时之后才到：把占位节点下的孩子挪回真实父片段，
        #    占位节点没有其它孩子时一并移除
        ph_id = state.placeholders.pop(arrived_parent_id, None)
        if ph_id is not None:
            for child_id in list(state.children.get(ph_id, [])):
                state.children[ph_id].remove(child_id)
                self._attach(state, child_id, arrived_parent_id)
            state.children.pop(ph_id, None)

    # ------------------------------------------------------------- 超时占位

    def flush_expired(self, now_ms: Optional[float] = None) -> list[str]:
        """把等待超过 max_wait_ms 的子片段归到占位节点下。

        返回发生了占位归并的 trace_id 列表（供推送层决定是否刷新）。
        """
        if now_ms is None:
            now_ms = time.time() * 1000.0
        changed: list[str] = []
        for trace_id, state in self._traces.items():
            touched = False
            for child_id, (parent_id, arrived_at) in list(state.pending.items()):
                if now_ms - arrived_at >= self.max_wait_ms:
                    ph_id = state.placeholders.get(parent_id)
                    if ph_id is None:
                        ph_id = placeholder_id(parent_id)
                        state.placeholders[parent_id] = ph_id
                    del state.pending[child_id]
                    self._attach(state, child_id, ph_id)
                    touched = True
            if touched:
                changed.append(trace_id)
        return changed

    # ------------------------------------------------------------------ 查询

    def trace_ids(self) -> list[str]:
        return list(self._traces.keys())

    def has_trace(self, trace_id: str) -> bool:
        return trace_id in self._traces

    def build_tree(
        self, trace_id: str, now_ms: Optional[float] = None
    ) -> Optional[dict]:
        """构造给前端的完整调用树视图。

        仍然挂起（未超时）的片段会临时挂到一个“未提交”占位节点下展示，
        但不会改变内部状态——父片段一到，下次构造时它们就出现在真实父节点下。
        """
        state = self._traces.get(trace_id)
        if state is None or not state.spans:
            return None
        self.flush_expired(now_ms)

        provisional: dict[str, list[str]] = {}
        for child_id, (parent_id, _at) in state.pending.items():
            provisional.setdefault(parent_id, []).append(child_id)

        root_nodes: list[dict] = []
        for span_id in state.root_span_ids:
            root_nodes.append(self._build_span_node(state, span_id, 0))
        for missing_id, ph_id in state.placeholders.items():
            root_nodes.append(self._build_placeholder_node(state, missing_id, ph_id, 0))
        for missing_id, child_ids in provisional.items():
            ph_id = placeholder_id(missing_id)
            node = {
                "type": "placeholder",
                "span_id": ph_id,
                "missing_parent_span_id": missing_id,
                "committed": False,
                "service": "unknown",
                "start_time": None,
                "end_time": None,
                "duration": 0,
                "status_code": None,
                "is_error": False,
                "depth": 0,
                "children": [
                    self._build_span_node(state, cid, 1)
                    for cid in self._sorted_children(child_ids, state)
                ],
            }
            root_nodes.append(node)

        root_nodes.sort(key=lambda n: (n["start_time"] is None, n["start_time"] or 0.0,
                                       n["span_id"]))

        all_spans = list(state.spans.values())
        trace_start = min(s.start_time for s in all_spans)
        trace_end = max(s.end_time for s in all_spans)
        critical_path, critical_duration = self._critical_path(root_nodes)
        breakdown = self._service_breakdown(state, max(trace_end - trace_start, 0.0))

        return {
            "trace_id": trace_id,
            "roots": root_nodes,
            "span_count": len(state.spans),
            "pending_count": len(state.pending),
            "complete": not state.pending,
            "trace_start": trace_start,
            "trace_end": trace_end,
            "duration": trace_end - trace_start,
            "critical_path": critical_path,
            "critical_path_duration": critical_duration,
            "service_breakdown": breakdown,
        }

    # ------------------------------------------------------------- 树构造细节

    def _sorted_children(self, child_ids: list[str], state: _TraceState) -> list[str]:
        # 按开始时间、再按 span_id 排序：无论上报顺序如何，树结构都一致
        return sorted(
            child_ids,
            key=lambda sid: (state.spans[sid].start_time, sid),
        )

    def _build_span_node(
        self, state: _TraceState, span_id: str, depth: int
    ) -> dict:
        span = state.spans[span_id]
        child_ids = self._sorted_children(state.children.get(span_id, []), state)
        return {
            "type": "span",
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "service": span.service,
            "start_time": span.start_time,
            "end_time": span.end_time,
            "duration": span.duration,
            "status_code": span.status_code,
            "is_error": span.is_error,
            "depth": depth,
            "children": [
                self._build_span_node(state, cid, depth + 1) for cid in child_ids
            ],
        }

    def _build_placeholder_node(
        self, state: _TraceState, missing_id: str, ph_id: str, depth: int
    ) -> dict:
        child_ids = self._sorted_children(state.children.get(ph_id, []), state)
        starts = [state.spans[c].start_time for c in child_ids]
        ends = [state.spans[c].end_time for c in child_ids]
        return {
            "type": "placeholder",
            "span_id": ph_id,
            "missing_parent_span_id": missing_id,
            "committed": True,
            "service": "unknown",
            "start_time": min(starts) if starts else None,
            "end_time": max(ends) if ends else None,
            "duration": (max(ends) - min(starts)) if starts else 0,
            "status_code": None,
            "is_error": False,
            "depth": depth,
            "children": [
                self._build_span_node(state, cid, depth + 1) for cid in child_ids
            ],
        }

    # --------------------------------------------------------------- 关键路径

    def _critical_path(self, roots: list[dict]) -> tuple[list[str], float]:
        """根到叶子的最长路径；占位节点权重为 0。"""
        best_path: list[str] = []
        best_weight = -1.0

        def walk(node: dict, path: list[str], weight: float) -> None:
            nonlocal best_path, best_weight
            if node["type"] == "span":
                path = path + [node["span_id"]]
                weight += node["duration"]
            kids = node["children"]
            if not kids:
                if weight > best_weight:
                    best_weight = weight
                    best_path = path
                return
            for kid in kids:
                walk(kid, path, weight)

        for root in roots:
            walk(root, [], 0.0)
        return best_path, max(best_weight, 0.0)

    # ------------------------------------------------------------ 服务耗时占比

    def _service_breakdown(
        self, state: _TraceState, trace_duration: float
    ) -> list[dict]:
        exclusive_by_service: dict[str, float] = {}
        for span in state.spans.values():
            covered = 0.0
            intervals: list[tuple[float, float]] = []
            for cid in state.children.get(span.span_id, []):
                child = state.spans[cid]
                # 只统计落在父片段区间内的部分（脏区间已在入口拦截，这里再夹一次）
                lo = max(child.start_time, span.start_time)
                hi = min(child.end_time, span.end_time)
                if hi > lo:
                    intervals.append((lo, hi))
            if intervals:
                intervals.sort()
                cur_lo, cur_hi = intervals[0]
                for lo, hi in intervals[1:]:
                    if lo <= cur_hi:
                        cur_hi = max(cur_hi, hi)
                    else:
                        covered += cur_hi - cur_lo
                        cur_lo, cur_hi = lo, hi
                covered += cur_hi - cur_lo
            exclusive = max(span.duration - covered, 0.0)
            exclusive_by_service[span.service] = (
                exclusive_by_service.get(span.service, 0.0) + exclusive
            )

        breakdown = [
            {
                "service": service,
                "exclusive_ms": round(exclusive, 3),
                "percent": round(exclusive / trace_duration * 100, 2)
                if trace_duration > 0
                else 0.0,
            }
            for service, exclusive in exclusive_by_service.items()
        ]
        breakdown.sort(key=lambda b: (-b["exclusive_ms"], b["service"]))
        return breakdown

    # --------------------------------------------------------------- 依赖边

    def extract_edges(self, trace_id: str) -> list[tuple[str, str, float]]:
        """提取本次调用树里“谁调用了谁”的边。

        只取真实父子片段对（parent -> child），返回
        (调用方服务, 被调方服务, 本次调用耗时=子片段耗时) 列表。
        挂在占位节点下的片段因为不知道父服务，不产生边。
        """
        state = self._traces.get(trace_id)
        if state is None:
            return []
        edges: list[tuple[str, str, float]] = []
        for parent_id, child_ids in state.children.items():
            if is_placeholder(parent_id):
                continue
            parent = state.spans.get(parent_id)
            if parent is None:
                continue
            for cid in child_ids:
                child = state.spans[cid]
                edges.append((parent.service, child.service, child.duration))
        return edges

    def load_from_storage(self, spans: list[SpanRecord]) -> None:
        """重启后从持久化数据重建状态。

        按开始时间依次入库（正常调用里父片段总是先开始），真正缺失父编号的
        片段随后一次性按超时归入占位节点。
        """
        for span in sorted(spans, key=lambda s: (s.start_time, s.span_id)):
            self.add_span(span, now_ms=0.0)
        self.flush_expired(now_ms=float("inf"))
