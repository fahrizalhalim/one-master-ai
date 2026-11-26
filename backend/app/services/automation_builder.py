"""Compiler workflow n8n (No-Code & AI) untuk Panel Admin OM.AI."""
import json, re, uuid
from typing import Any, Literal
import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field
from app.core.config import (
    GROQ_API_KEY, GROQ_BASE_URL, OMAI_TELEGRAM_BOT_TOKEN,
    OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL,
)

class BuilderInputField(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=200)
    field_type: Literal["string", "number", "boolean"] = "string"
    required: bool = False

class BuilderStep(BaseModel):
    type: Literal["wait", "rss", "http", "ai", "code", "telegram"]
    name: str = Field(default="", max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)

class AutomationBuildRequest(BaseModel):
    mode: Literal["ai", "builder"] = "builder"
    ai_prompt: str = Field(default="", max_length=4000)
    name: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=5000)
    delivery_mode: Literal["sync", "async_telegram"] = "sync"
    ack_message: str = Field(default="Permintaan diterima. Hasil dikirim ke Telegram.", max_length=1000)
    input_fields: list[BuilderInputField] = Field(default_factory=list)
    steps: list[BuilderStep] = Field(default_factory=list)
    access_mode: Literal["all", "selected"] = "all"
    allowed_user_ids: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=120, ge=5, le=300)
    auto_publish: bool = True

def get_llm_credentials() -> tuple[str, str, str]:
    if OPENAI_API_KEY:
        return OPENAI_BASE_URL.rstrip("/"), OPENAI_API_KEY, (OPENAI_MODEL or "gpt-4o-mini")
    if GROQ_API_KEY:
        return GROQ_BASE_URL.rstrip("/"), GROQ_API_KEY, "llama-3.3-70b-versatile"
    raise HTTPException(status_code=500, detail="API Key LLM belum dikonfigurasi.")

def build_json_schema(fields: list[BuilderInputField]) -> dict[str, Any]:
    props, req = {}, []
    for f in fields:
        k = re.sub(r"[^a-zA-Z0-9_]", "_", f.name.strip())
        if not k:
            continue
        props[k] = {"type": f.field_type, "description": (f.label or k).strip()}
        if f.required:
            req.append(k)
    out: dict[str, Any] = {"type": "object", "properties": props}
    if req:
        out["required"] = req
    return out

async def generate_plan_with_ai(prompt_text: str, pref: str) -> dict[str, Any]:
    base_url, api_key, model = get_llm_credentials()
    sys_msg = (
        'Balas HANYA JSON tanpa markdown: {"name":"...","description":"...",'
        '"delivery_mode":"sync|async_telegram","ack_message":"...",'
        '"input_fields":[{"name":"topik","label":"Topik","field_type":"string","required":true}],'
        '"steps":[{"type":"wait|rss|http|ai|code|telegram","name":"...","config":{}}]}. '
        'Config: wait={amount:5,unit:"minutes"}, rss={url:"https://news.google.com/rss?hl=id&gl=ID&ceid=ID:id",max_items:10}, '
        'http={method:"GET|POST",url:"https://...",body_json:""}, ai={system_prompt:"...",user_prompt:"{{prev_data}} {{topik}}"}, '
        'code={js_code:"return [{json:{message:...}}];"}, telegram={template:"{{message}}"}. '
        'Gunakan delivery_mode="async_telegram" jika ada jeda wait atau kirim Telegram.'
    )
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "temperature": 0.2, "messages": [
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": f"Instruksi: {prompt_text.strip()}\nMode: {pref}"},
                ]},
            )
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI Generator gagal: {exc}") from None
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError()
        return parsed
    except Exception:
        raise HTTPException(status_code=502, detail="Format rencana AI tidak valid.") from None

def compile_n8n_workflow(*, wf_name: str, webhook_path: str, delivery_mode: str, ack_message: str, steps: list[BuilderStep]) -> dict[str, Any]:
    nodes, conns, x_pos = [], {}, 220

    def add_node(name: str, ntype: str, ver: float, params: dict[str, Any], extra: dict[str, Any] | None = None) -> str:
        nonlocal x_pos
        used = {n["name"] for n in nodes}
        fname = name if name not in used else f"{name} {len(nodes)+1}"
        node: dict[str, Any] = {"parameters": params, "id": str(uuid.uuid4()), "name": fname, "type": ntype, "typeVersion": ver, "position": [x_pos, 300]}
        if extra:
            node.update(extra)
        nodes.append(node)
        x_pos += 240
        return fname

    def link(src: str, dst: str) -> None:
        conns.setdefault(src, {"main": [[]]})["main"][0].append({"node": dst, "type": "main", "index": 0})

    prev = add_node("Webhook", "n8n-nodes-base.webhook", 2, {"httpMethod": "POST", "path": webhook_path, "responseMode": "responseNode", "options": {}}, {"webhookId": str(uuid.uuid4())})
    if delivery_mode == "async_telegram":
        ack_json = json.dumps({"success": True, "message": ack_message or "Permintaan diterima."}, ensure_ascii=False)
        ack = add_node("Respond Konfirmasi", "n8n-nodes-base.respondToWebhook", 1.1, {"respondWith": "json", "responseBody": ack_json, "options": {}})
        link(prev, ack); prev = ack

    eff = list(steps) or [BuilderStep(type="code", name="Proses", config={"js_code": "return [{ json: { message: 'Otomatisasi selesai.' } }];"})]
    if delivery_mode == "async_telegram" and not any(s.type == "telegram" for s in eff):
        eff.append(BuilderStep(type="telegram", name="Kirim Telegram", config={"template": "{{message}}"}))

    for idx, step in enumerate(eff, start=1):
        cfg = step.config or {}
        label = (step.name or f"Langkah {idx}").strip()[:70]
        if step.type == "wait":
            amt = cfg.get("amount", 5)
            if isinstance(amt, str) and not amt.strip().startswith("="):
                amt = int(amt) if amt.strip().isdigit() else 5
            unit = cfg.get("unit", "minutes")
            unit = unit if unit in ("seconds", "minutes", "hours") else "minutes"
            n = add_node(label, "n8n-nodes-base.wait", 1.1, {"resume": "timeInterval", "amount": amt, "unit": unit}, {"webhookId": str(uuid.uuid4())})
            link(prev, n); prev = n
        elif step.type == "rss":
            url = str(cfg.get("url") or "https://news.google.com/rss?hl=id&gl=ID&ceid=ID:id").strip()
            mx = max(1, min(int(cfg.get("max_items", 10) or 10), 30))
            n1 = add_node(label, "n8n-nodes-base.rssFeedRead", 1.1, {"url": url, "options": {}})
            n2 = add_node(f"Batasi {mx}", "n8n-nodes-base.limit", 1, {"maxItems": mx})
            n3 = add_node("Gabung List", "n8n-nodes-base.aggregate", 1, {"aggregate": "aggregateAllItemData", "destinationFieldName": "items", "options": {}})
            link(prev, n1); link(n1, n2); link(n2, n3); prev = n3
        elif step.type == "http":
            m = str(cfg.get("method") or "GET").upper()
            m = m if m in ("GET", "POST", "PUT", "PATCH", "DELETE") else "GET"
            url = str(cfg.get("url") or "").strip()
            if not url:
                raise HTTPException(status_code=422, detail=f"URL pada '{label}' wajib diisi.")
            p: dict[str, Any] = {"method": m, "url": url, "options": {}}
            b = str(cfg.get("body_json") or "").strip()
            if m != "GET" and b:
                p.update({"sendBody": True, "specifyBody": "json", "jsonBody": b if b.startswith("=") else f"={b}"})
            n = add_node(label, "n8n-nodes-base.httpRequest", 4.2, p)
            link(prev, n); prev = n
        elif step.type == "ai":
            ai_base, ai_key, ai_model = get_llm_credentials()
            sys_p = str(cfg.get("system_prompt") or "Anda asisten AI. Jawab ringkas dan rapi dalam bahasa Indonesia.")
            usr_p = str(cfg.get("user_prompt") or "Rangkum data berikut:\n{{prev_data}}")
            prep_js = "\n".join([
                "const wb = $('Webhook').item.json.body || {};",
                "const cur = $input.first().json || {};",
                "const prev = JSON.stringify(cur.items || cur, null, 2).slice(0, 14000);",
                f"let p = {json.dumps(usr_p)};",
                r"p = p.replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (_, k) => {",
                "  if (k === 'prev_data') return prev;",
                "  if (k === 'message') return String(cur.message ?? '');",
                "  const v = wb[k] !== undefined ? wb[k] : cur[k];",
                "  return v == null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v));",
                "});",
                f"return [{{ json: {{ ai_body: {{ model: {json.dumps(ai_model)}, messages: [{{ role: 'system', content: {json.dumps(sys_p)} }}, {{ role: 'user', content: p }}] }} }} }}];",
            ])
            n1 = add_node("Siapkan AI", "n8n-nodes-base.code", 2, {"jsCode": prep_js})
            n2 = add_node(label, "n8n-nodes-base.httpRequest", 4.2, {
                "method": "POST", "url": f"{ai_base}/chat/completions", "sendHeaders": True,
                "headerParameters": {"parameters": [{"name": "Authorization", "value": f"Bearer {ai_key}"}]},
                "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify($json.ai_body) }}", "options": {},
            })
            n3 = add_node("Hasil AI", "n8n-nodes-base.code", 2, {"jsCode": "const r = $input.first().json || {}; return [{ json: { message: r?.choices?.[0]?.message?.content || 'AI tidak membalas.' } }];"})
            link(prev, n1); link(n1, n2); link(n2, n3); prev = n3
        elif step.type == "code":
            js = str(cfg.get("js_code") or "return [{ json: { message: 'Selesai', data: $input.first().json } }];")
            n = add_node(label, "n8n-nodes-base.code", 2, {"jsCode": js})
            link(prev, n); prev = n
        elif step.type == "telegram":
            if not OMAI_TELEGRAM_BOT_TOKEN:
                raise HTTPException(status_code=500, detail="OMAI_TELEGRAM_BOT_TOKEN belum dikonfigurasi.")
            tpl = str(cfg.get("template") or cfg.get("text_expr") or "{{message}}").strip()
            tg_js = "\n".join([
                "const wb = $('Webhook').item.json.body || {};",
                "const cur = $input.first().json || {};",
                f"let t = {json.dumps(tpl)};",
                r"t = t.replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (_, k) => {",
                "  if (k === 'message') return String(cur.message ?? cur.text ?? '');",
                "  if (k === 'prev_data') return JSON.stringify(cur);",
                "  const v = wb[k] !== undefined ? wb[k] : cur[k];",
                "  return v == null ? '' : String(v);",
                "});",
                "if (!t.trim()) t = String(cur.message || cur.text || 'Otomatisasi selesai.');",
                "return [{ json: { chat_id: wb.telegram_chat_id, text: t.slice(0, 4000), message: t } }];",
            ])
            n1 = add_node("Format Telegram", "n8n-nodes-base.code", 2, {"jsCode": tg_js})
            n2 = add_node(label, "n8n-nodes-base.httpRequest", 4.2, {
                "method": "POST", "url": f"https://api.telegram.org/bot{OMAI_TELEGRAM_BOT_TOKEN}/sendMessage",
                "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify({ chat_id: $json.chat_id, text:$json.text }) }}", "options": {},
            })
            link(prev, n1); link(n1, n2); prev = n2

    if delivery_mode == "sync":
        resp = add_node("Respond Hasil", "n8n-nodes-base.respondToWebhook", 1.1, {
            "respondWith": "json",
            "responseBody": "={{ JSON.stringify({ success: true, message: $json.message || $json.text || 'Workflow selesai.', data:$json }) }}",
            "options": {},
        })
        link(prev, resp)
    return {"name": wf_name, "nodes": nodes, "connections": conns, "settings": {"executionOrder": "v1"}}

async def create_workflow_service(payload: AutomationBuildRequest, admin_id: str, db):
    import secrets
    from app.models.automation import AutomationWorkflow, AutomationWorkflowAccess
    from app.services.n8n_service import N8NServiceError, n8n_api_request
    wf_name = payload.name.strip()
    wf_desc = payload.description.strip()
    delivery = payload.delivery_mode
    ack_msg = payload.ack_message.strip()
    fields, steps = list(payload.input_fields), list(payload.steps)
    if payload.mode == "ai":
        if not payload.ai_prompt.strip():
            raise HTTPException(422, "Isi instruksi AI.")
        plan = await generate_plan_with_ai(payload.ai_prompt, delivery)
        wf_name = wf_name or str(plan.get("name") or "Otomatisasi AI")[:255]
        wf_desc = wf_desc or str(plan.get("description") or "")[:300]
        if plan.get("delivery_mode") in ("sync", "async_telegram"):
            delivery = plan["delivery_mode"]
        if plan.get("ack_message"):
            ack_msg = str(plan["ack_message"])[:1000]
        if isinstance(plan.get("input_fields"), list) and not fields:
            for rf in plan["input_fields"]:
                if isinstance(rf, dict) and rf.get("name"):
                    ft = rf.get("field_type")
                    if ft not in ("string", "number", "boolean"):
                        ft = "string"
                    fields.append(BuilderInputField(
                        name=str(rf["name"])[:64], label=str(rf.get("label") or rf["name"])[:200],
                        field_type=ft, required=bool(rf.get("required", False))))
        if isinstance(plan.get("steps"), list):
            ok = ("wait", "rss", "http", "ai", "code", "telegram")
            steps = [BuilderStep(type=r["type"], name=str(r.get("name") or "")[:100],
                config=r.get("config") if isinstance(r.get("config"), dict) else {})
                for r in plan["steps"] if isinstance(r, dict) and r.get("type") in ok]
    if not wf_name:
        raise HTTPException(422, "Nama otomatisasi wajib diisi.")
    wh_path = secrets.token_hex(24)
    body = compile_n8n_workflow(
        wf_name=wf_name, webhook_path=wh_path,
        delivery_mode=delivery, ack_message=ack_msg, steps=steps)
    try:
        created = await n8n_api_request("POST", "workflows", json=body)
    except N8NServiceError as exc:
        raise HTTPException(502, str(exc)) from None
    n8n_id = str(created.get("id") or "").strip() if isinstance(created, dict) else ""
    if not n8n_id:
        raise HTTPException(502, "n8n tidak mengembalikan ID workflow.")
    activated = False
    for m, u, b in (("POST", f"workflows/{n8n_id}/activate", None), ("PATCH", f"workflows/{n8n_id}", {"active": True})):
        try:
            await n8n_api_request(m, u, json=b); activated = True; break
        except N8NServiceError:
            continue
    is_live = bool(payload.auto_publish and activated)
    wf = AutomationWorkflow(
        n8n_workflow_id=n8n_id, name=wf_name[:255], description=wf_desc[:5000],
        webhook_path=wh_path, input_schema=build_json_schema(fields), output_schema={},
        access_mode=payload.access_mode, is_published=is_live, is_active=is_live,
        timeout_seconds=payload.timeout_seconds, created_by=admin_id)
    try:
        db.add(wf); db.flush()
        if payload.access_mode == "selected":
            for uid in set(payload.allowed_user_ids or [admin_id]):
                db.add(AutomationWorkflowAccess(workflow_id=wf.id, user_id=uid, is_allowed=True, granted_by=admin_id))
        db.commit(); db.refresh(wf)
    except Exception:
        db.rollback()
        raise HTTPException(500, "Gagal menyimpan ke Registry OM.AI.") from None
    return wf, is_live
