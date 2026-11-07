"""Deterministic reader-facing view of frozen engineering report facts."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any


VERDICTS = {
    "compliant": "符合设计要求",
    "deviation_detected": "发现偏差",
    "insufficient_evidence": "资料不足",
    "needs_review": "需人工判断",
    "not_applicable": "不适用",
    "aligned": "符合设计要求",
}
POSITIONS = {"passed": "位于工单范围内", "failed": "超出工单范围", "skipped": "未执行", "unavailable": "无法核验"}
FIELDS = {
    "spatial_check": "采集位置", "visible_pipe_count": "可见管线数量", "quantity": "数量",
    "trench_stage": "施工阶段", "object_visibility": "对象可见性",
    "visible_material_or_specification": "材料与规格", "spacing_m": "间距（米）",
    "min_depth_m": "最小深度（米）", "depth_m": "深度（米）", "specification": "规格",
}
VALUES = {
    "laying": "敷设", "excavation": "开挖", "backfill": "回填", "completed": "已完成",
    "visible": "清晰可见", "partially_visible": "部分可见", **POSITIONS,
    "passed_or_present": "有可核验的位置",
}
CASE_NAMES = {
    "COMPLIANCE_DEVIATION_DETECTED": "设计要求存在偏差",
    "COMPLIANCE_INSUFFICIENT_EVIDENCE": "核验资料不足",
    "COMPLIANCE_NEEDS_REVIEW": "施工条件需人工复核",
}
CASE_STATES = {
    "pending_triage": "待确认", "open": "待整改", "remediation_in_progress": "整改中",
    "verification_pending": "待复验", "closed": "已关闭", "dismissed": "已排除",
}
PROCEDURES = {
    "TRENCH-BEFORE-BACKFILL": "沟槽回填前检查",
    "PPE-RECHECK": "现场防护复验",
}
BEIJING = timezone(timedelta(hours=8))


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _time(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    try:
        date = datetime.fromisoformat(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return date.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError):
        return ""


def _value(value: Any) -> str:
    if isinstance(value, str):
        return VALUES.get(value, value)
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "、".join(filter(None, map(_value, value)))
    return ""


def _requirement(value: Any) -> str:
    if not isinstance(value, dict):
        return _value(value)
    if "equals" in value:
        return _value(value["equals"])
    if "one_of" in value:
        return _value(value["one_of"])
    if "min" in value:
        return "不低于 " + _value(value["min"])
    if "max" in value:
        return "不高于 " + _value(value["max"])
    operator = {"eq": "等于", "gte": "不低于", "lte": "不高于", ">=": "不低于", "<=": "不高于"}.get(value.get("operator"))
    if operator:
        return f"{operator} {_value(value.get('value'))}"
    return "见设计基线"


def _note(value: Any) -> str:
    return "\n".join(
        line for line in _value(value).splitlines()
        if not line.startswith((
            "submitted reviewer label: ", "仅批准合成演示闭环",
            "确认已阅读安全占位输出边界", "仅批准远程单样本推理记录",
        ))
    ).strip()


def _section(
    heading: str,
    *,
    facts: Sequence[tuple[str, Any]] = (),
    paragraphs: Sequence[str] = (),
    checks: Sequence[dict[str, str]] = (),
) -> dict[str, Any]:
    return {
        "heading": heading,
        "facts": [{"label": label, "value": text} for label, value in facts if (text := _value(value))],
        "paragraphs": list(filter(None, paragraphs)),
        "checks": list(checks),
    }


def build_readable_report(content: dict[str, Any]) -> dict[str, Any]:
    project = _record(content.get("project"))
    work_order = _record(content.get("work_order"))
    baseline = _record(content.get("design_baseline"))
    evidence = _record(content.get("evidence"))
    capture = _record(content.get("capture"))
    analysis = _record(content.get("analysis"))
    compliance = _record(analysis.get("compliance_evaluation"))
    alignment = _record(analysis.get("alignment"))
    review = _record(content.get("human_review"))
    context = _record(content.get("remediation_context"))
    attempt = _record(context.get("attempt"))
    resolution = {"resolved": "本轮复验通过", "not_resolved": "继续整改"}.get(attempt.get("resolution_decision"), "")
    verdict = VERDICTS.get(compliance.get("verdict") or alignment.get("status"), "未形成规则判定")
    decision = {"approve": "已批准", "reject": "已退回"}.get(review.get("decision"), "待确认")
    mode = analysis.get("analysis_mode")
    analysis_mode = {"stub": "人工复核", "remote_http": "辅助识别", "demo_fixture": "演示规则"}.get(mode, "见核验记录")
    checks = []
    for item in alignment.get("differences") or ():
        row = _record(item)
        field = FIELDS.get(row.get("field"))
        if field:
            checks.append({
                "item": field,
                "expected": _requirement(row.get("expected")) or "见设计基线",
                "observed": _value(row.get("observed")) or "缺少记录",
                "status": VERDICTS.get(row.get("status"), "待核对"),
            })
    procedure = baseline.get("procedure_code")
    reviewer = _value(review.get("reviewer"))
    if reviewer.startswith("api-key:") or reviewer in {"合成演示复核员", "合成整改复验员"}:
        reviewer = "复核员"
    sections = [
        _section("复核意见", facts=[
            ("处理结果", resolution or decision), ("复核人", reviewer), ("复核时间（北京时间）", _time(review.get("reviewed_at"))),
        ], paragraphs=[_note(review.get("note"))]),
        _section("工程与设计依据", facts=[
            ("所属项目", project.get("name")), ("施工位置", project.get("location")),
            ("工单", work_order.get("code")), ("工程对象", work_order.get("object_name")),
            ("工点", baseline.get("site_id")), ("工序", PROCEDURES.get(procedure, procedure)),
            ("设计版本", baseline.get("version")),
        ]),
    ]
    position = POSITIONS.get(capture.get("spatial_check_status") or compliance.get("spatial_check_status"))
    if checks or position or verdict != "未形成规则判定":
        sections.append(_section("核验记录", facts=[
            ("核验方式", analysis_mode), ("规则初判", verdict), ("定位结果", position),
        ], checks=checks))
    if attempt:
        case = _record(context.get("case"))
        sections.append(_section("整改复验", facts=[
            ("原问题", CASE_NAMES.get(case.get("finding_code"), "现场整改事项")),
            ("复验轮次", attempt.get("attempt_no")),
            ("登记时间（北京时间）", _time(attempt.get("submitted_at"))),
        ], paragraphs=[_value(attempt.get("action_description"))]))
    else:
        cases = [_record(case) for case in (content.get("finding_cases") or [])]
        confirmed = [case for case in cases if case.get("status_at_seal") in {"open", "remediation_in_progress", "verification_pending", "closed"}]
        if confirmed:
            sections.append(_section("整改事项", paragraphs=[
                f"{CASE_NAMES.get(case.get('finding_code'), '现场整改事项')} · "
                f"{CASE_STATES.get(case.get('status_at_seal'), '待确认')}"
                + (f"。{case['decision_reason']}" if case.get("decision_reason") else "")
                for case in confirmed
            ]))
    captured_at = _time(evidence.get("captured_at"))
    sections.append(_section("影像与附属资料", facts=[
        ("影像文件", evidence.get("original_name")),
        ("采集时间（北京时间）", captured_at or "未登记"),
        ("关联采集记录", len(content.get("related_sensor_events") or [])),
    ]))
    return {
        "version": 2,
        "title": "施工整改复验报告" if attempt else "施工核验报告",
        "summary": "",
        "sections": sections,
    }


def render_readable_html(view: dict[str, Any], report_id: str, created_at: Any) -> bytes:
    def text(value: str) -> str:
        return escape(value, quote=True)

    blocks = []
    for section in view["sections"]:
        facts = "".join(
            f"<div><dt>{text(fact['label'])}</dt><dd>{text(fact['value'])}</dd></div>"
            for fact in section["facts"]
        )
        paragraphs = "".join(f"<p>{text(paragraph)}</p>" for paragraph in section["paragraphs"])
        checks = "".join(
            "<tr>" + "".join(f"<td>{text(row[key])}</td>" for key in ("item", "expected", "observed", "status")) + "</tr>"
            for row in section["checks"]
        )
        table = ("<table><thead><tr><th>核验项</th><th>设计要求</th><th>观测记录</th><th>判定</th></tr>"
                 f"</thead><tbody>{checks}</tbody></table>") if checks else ""
        blocks.append(f"<section><h2>{text(section['heading'])}</h2><dl>{facts}</dl>{paragraphs}{table}</section>")
    title = text(view["title"])
    # Replaying an already sealed v1 report must reproduce its original bytes.
    brand = "烽眸智鉴" if view["version"] == 2 else "锋眸智鉴"
    summary = f"<p>{text(view['summary'])}</p>" if view["version"] == 1 else ""
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title><style>"
        "body{font:15px/1.7 'PingFang SC','Microsoft YaHei UI',sans-serif;color:#17334a;"
        "max-width:760px;margin:40px auto;padding:0 24px;overflow-wrap:anywhere}"
        "header{border-bottom:2px solid #2e5574;margin-bottom:24px}h1{margin:6px 0;font-size:28px}"
        "h2{font-size:16px;color:#173c59}section{margin:22px 0}"
        "dl div{display:grid;grid-template-columns:minmax(100px,28%) 1fr;gap:12px;padding:3px 0}dt,small{color:#526678}dd{margin:0}"
        "p{white-space:pre-line}table{border-collapse:collapse;width:100%;font-size:14px}"
        "td,th{padding:7px;text-align:left;border-bottom:1px solid #d5e0e9}th{background:#edf3f7}"
        "@media print{section{break-inside:avoid}body{margin:0;padding:0}}"
        f"</style></head><body><header><small>{brand} · 工程交付记录</small>"
        f"<h1>{title}</h1><small>封存时间（北京时间） {text(_time(created_at))}</small>"
        f"{summary}</header>{''.join(blocks)}"
        f"<footer><small>档案关联编号 {text(report_id)}</small></footer></body></html>"
    ).encode("utf-8")
