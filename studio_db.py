# -*- coding: utf-8 -*-
"""
studio_db.py —— 多租户数据层（真·SaaS 地基）
============================================
设计要点：
- SQLite(studio.db) 存结构化配置/索引，所有行带 tenant_id 做行级隔离；
- 媒体文件（音视频/字幕包）仍走文件系统，按 data/<tenant_slug>/ 隔离（blob 不进 DB）；
- 旧单租户数据自动迁为「默认租户 huigentang」，不丢历史；
- 配置以 JSON 落 tenant_config 表（key→value），前端按需读写。

本模块只负责「数据 + 租户」，不碰出片业务逻辑。
"""
from __future__ import annotations
import sqlite3
import json
import time
import secrets
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
DB_PATH = BASE / "studio.db"
DATA_ROOT = ROOT / "data"            # data/<tenant_slug>/... 媒体隔离根

DEFAULT_TENANT_SLUG = "huigentang"
DEFAULT_TENANT_NAME = "慧根堂工作室"

# 标准 5 类默认分类体系（租户可在此基础上自定义增删改/排序/启停）
STANDARD_CATEGORIES = [
    {"id": "ip_knowledge",   "name": "IP知识类",   "desc": "个人IP打造、人设定位、内容选题", "order": 1, "enabled": True},
    {"id": "construction",   "name": "建筑工程类", "desc": "建筑行业财税、工程核算、项目税务", "order": 2, "enabled": True},
    {"id": "tax_compliance", "name": "税务合规类", "desc": "税务合规、稽查应对、风险规避",     "order": 3, "enabled": True},
    {"id": "business_agent", "name": "工商代理类", "desc": "工商注册、变更、注销代理",       "order": 4, "enabled": True},
    {"id": "qualification",  "name": "资质代办类", "desc": "各类资质代办、许可申请",         "order": 5, "enabled": True},
]

# 旧分类 → 新分类 id 映射（迁移用；value 为可多选的新分类 id 列表）
LEGACY_CATEGORY_MAP = {
    "财税IP打造类": ["ip_knowledge", "tax_compliance"],
    "财税知识类":   ["tax_compliance", "ip_knowledge"],
    "营销引流类":   ["ip_knowledge"],
    "营销类":       ["ip_knowledge"],
    "未分类":       [],
}

# 模特命名规范：{品牌}_{角色}_{场景}_{版本}，例 HGTT_zhanglao_taxlecture_v1
MODEL_NAME_PATTERN = r"^[A-Za-z0-9]+_[a-z0-9]+_[a-z0-9]+_v\d+$"

# 数字人来源标识（source_tag）
SOURCE_TAGS = {
    "local_clone":  "本地真人克隆（HEYGEM）",
    "official_saas":"官方SaaS预制数字人（即创/硅基/智影等）",
    "scroll_card":  "滚动字幕卡（不出镜·双声）",
}


def _conn() -> sqlite3.Connection:
    cx = sqlite3.connect(str(DB_PATH))
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode=WAL")
    return cx


def default_settings() -> dict:
    """系统级可配置项（模块三收口对象）。"""
    return {
        "render_defaults": {           # 出片默认参数
            "bg": "默认海景滚动",
            "subtitle_style": "white_black_simhei",
            "resolution": "1080x1920",
        },
        "tts_voice_map": {             # 音色映射（角色→voice_id）；新租户初始为空，须由租户克隆/选择
            "male":   "",
            "female": "",
        },
        "platform_limits": {           # 平台限制开关（次要约束，不阻断核心功能）
            "official_saas_requires_org": True,   # 即创需组织认证
            "moments_auto_risk": "high",          # 朋友圈自动化封号风险
        },
        "qc_enabled": True,            # 出片后 QC 评分卡开关（模块四）
    }


def default_routing() -> dict:
    """数字人路由规则（来源 → 出片方式）。"""
    return {
        "rules": [
            {"when": "source=local_clone&voice=solo", "use": "face2face", "desc": "真人克隆单声→出镜驱动"},
            {"when": "source=local_clone&voice=dual", "use": "scroll_card", "desc": "真人克隆双声→不出镜字幕卡"},
            {"when": "source=official_saas",          "use": "saas_api",   "desc": "官方数字人→SaaS API"},
        ],
        "default": "face2face",
    }


def _gen_token() -> str:
    return "hgt-" + secrets.token_hex(20)


# ───────────────────────── 初始化 / 迁移 ─────────────────────────
def init_db() -> dict:
    cx = _conn()
    cx.executescript("""
    CREATE TABLE IF NOT EXISTS tenants(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        slug        TEXT UNIQUE NOT NULL,
        name        TEXT NOT NULL,
        token       TEXT UNIQUE NOT NULL,
        plan        TEXT DEFAULT 'free',
        status      TEXT DEFAULT 'active',
        created_at  INTEGER
    );
    CREATE TABLE IF NOT EXISTS users(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id   INTEGER NOT NULL,
        username    TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role        TEXT DEFAULT 'editor',
        created_at  INTEGER,
        UNIQUE(tenant_id, username)
    );
    CREATE TABLE IF NOT EXISTS tenant_config(
        tenant_id   INTEGER NOT NULL,
        cfg_key     TEXT NOT NULL,
        cfg_value   TEXT,
        updated_at  INTEGER,
        PRIMARY KEY(tenant_id, cfg_key)
    );
    CREATE TABLE IF NOT EXISTS projects_index(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id   INTEGER NOT NULL,
        name        TEXT NOT NULL,
        title       TEXT,
        categories  TEXT DEFAULT '[]',
        meta        TEXT DEFAULT '{}',
        created_at  INTEGER,
        UNIQUE(tenant_id, name)
    );
    CREATE INDEX IF NOT EXISTS idx_proj_tenant ON projects_index(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_proj_cat   ON projects_index(tenant_id, categories);
    """)
    row = cx.execute("SELECT * FROM tenants WHERE slug=?", (DEFAULT_TENANT_SLUG,)).fetchone()
    if not row:
        tok = _gen_token()
        cur = cx.execute(
            "INSERT INTO tenants(slug,name,token,plan,status,created_at) VALUES(?,?,?,?,?,?)",
            (DEFAULT_TENANT_SLUG, DEFAULT_TENANT_NAME, tok, "free", "active", int(time.time())),
        )
        tid = cur.lastrowid
        cx.commit(); cx.close()   # 先提交并关闭，避免与下方 set_config 新连接互相锁
        set_config(tid, "categories", {"version": 1, "items": STANDARD_CATEGORIES})
        set_config(tid, "settings", default_settings())
        set_config(tid, "models_registry", {"items": []})
        set_config(tid, "routing", default_routing())
        set_config(tid, "publish_accounts", {"items": []})
        set_config(tid, "schedules", {"items": []})
        cx = _conn()
        row = cx.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone()
    cx.close()
    return dict(row)


# ───────────────────────── 租户解析 / 鉴权 ─────────────────────────
def get_tenant_by_token(token: str) -> dict | None:
    if not token:
        return None
    cx = _conn()
    row = cx.execute("SELECT * FROM tenants WHERE token=?", (token,)).fetchone()
    cx.close()
    return dict(row) if row else None


def get_default_tenant() -> dict:
    cx = _conn()
    row = cx.execute("SELECT * FROM tenants WHERE slug=?", (DEFAULT_TENANT_SLUG,)).fetchone()
    cx.close()
    return dict(row) if row else {}


def list_tenants() -> list:
    cx = _conn()
    rows = cx.execute("SELECT id,slug,name,plan,status,created_at FROM tenants ORDER BY id").fetchall()
    cx.close()
    return [dict(r) for r in rows]


def create_tenant(slug: str, name: str, plan: str = "free") -> dict:
    cx = _conn()
    tok = _gen_token()
    try:
        cur = cx.execute(
            "INSERT INTO tenants(slug,name,token,plan,status,created_at) VALUES(?,?,?,?,?,?)",
            (slug, name, tok, plan, "active", int(time.time())),
        )
        tid = cur.lastrowid
        cx.commit(); cx.close()   # 先提交并关闭，避免与下方 set_config 新连接互相锁
        set_config(tid, "categories", {"version": 1, "items": STANDARD_CATEGORIES})
        set_config(tid, "settings", default_settings())
        set_config(tid, "models_registry", {"items": []})
        set_config(tid, "routing", default_routing())
        set_config(tid, "publish_accounts", {"items": []})
        set_config(tid, "schedules", {"items": []})
        cx = _conn()
        row = cx.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone()
        cx.close()
        return {"ok": True, **dict(row)}
    except sqlite3.IntegrityError as e:
        cx.rollback()
        return {"ok": False, "error": f"租户已存在或非法：{e}"}
    finally:
        cx.close()


# ───────────────────────── 配置读写 ─────────────────────────
def get_config(tenant_id: int, key: str, default=None):
    cx = _conn()
    row = cx.execute("SELECT cfg_value FROM tenant_config WHERE tenant_id=? AND cfg_key=?",
                     (tenant_id, key)).fetchone()
    cx.close()
    if not row or row["cfg_value"] is None:
        return default
    try:
        return json.loads(row["cfg_value"])
    except Exception:
        return default


def set_config(tenant_id: int, key: str, value) -> bool:
    cx = _conn()
    cx.execute(
        "INSERT INTO tenant_config(tenant_id,cfg_key,cfg_value,updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(tenant_id,cfg_key) DO UPDATE SET cfg_value=excluded.cfg_value, updated_at=excluded.updated_at",
        (tenant_id, key, json.dumps(value, ensure_ascii=False), int(time.time())),
    )
    cx.commit()
    cx.close()
    return True


def get_all_config(tenant_id: int) -> dict:
    cx = _conn()
    rows = cx.execute("SELECT cfg_key,cfg_value FROM tenant_config WHERE tenant_id=?", (tenant_id,)).fetchall()
    cx.close()
    out = {}
    for r in rows:
        try:
            out[r["cfg_key"]] = json.loads(r["cfg_value"])
        except Exception:
            out[r["cfg_key"]] = r["cfg_value"]
    return out


# ───────────────────────── 项目索引（分类检索） ─────────────────────────
def upsert_project(tenant_id: int, name: str, title: str = "", categories: list | None = None, meta: dict | None = None) -> bool:
    cx = _conn()
    existing = cx.execute("SELECT id FROM projects_index WHERE tenant_id=? AND name=?",
                          (tenant_id, name)).fetchone()
    if existing:
        cx.execute(
            "UPDATE projects_index SET title=?, categories=?, meta=?, updated_at=? WHERE tenant_id=? AND name=?",
            (title, json.dumps(categories or [], ensure_ascii=False),
             json.dumps(meta or {}, ensure_ascii=False), int(time.time()), tenant_id, name),
        )
    else:
        cx.execute(
            "INSERT INTO projects_index(tenant_id,name,title,categories,meta,created_at) VALUES(?,?,?,?,?,?)",
            (tenant_id, name, title, json.dumps(categories or [], ensure_ascii=False),
             json.dumps(meta or {}, ensure_ascii=False), int(time.time())),
        )
    cx.commit()
    cx.close()
    return True


def list_projects(tenant_id: int, category: str | None = None) -> list:
    cx = _conn()
    if category:
        # 分类检索：categories JSON 数组包含该 id
        rows = cx.execute(
            "SELECT * FROM projects_index WHERE tenant_id=? AND categories LIKE ? ORDER BY created_at DESC",
            (tenant_id, f'%"{category}"%'),
        ).fetchall()
    else:
        rows = cx.execute(
            "SELECT * FROM projects_index WHERE tenant_id=? ORDER BY created_at DESC",
            (tenant_id,),
        ).fetchall()
    cx.close()
    return [dict(r) for r in rows]


def migrate_legacy_account_type(tenant_id: int, legacy_type: str) -> list:
    """旧 account_type → 新分类 id 列表（迁移辅助）。"""
    return LEGACY_CATEGORY_MAP.get(legacy_type, [])


# ───────────────────────── 媒体隔离路径 ─────────────────────────
def tenant_data_dir(tenant_slug: str) -> Path:
    d = DATA_ROOT / tenant_slug
    d.mkdir(parents=True, exist_ok=True)
    return d


# ───────────────────────── 设备管理 ─────────────────────────
def init_devices_table() -> None:
    cx = _conn()
    cx.executescript("""
    CREATE TABLE IF NOT EXISTS devices(
        id          TEXT PRIMARY KEY,            -- device_id (UUID)
        tenant_id   INTEGER NOT NULL,
        name        TEXT,                        -- 用户自定义设备名
        fingerprint TEXT NOT NULL,               -- 机器指纹 hash
        is_primary  INTEGER DEFAULT 0,           -- 是否主控制台
        status      TEXT DEFAULT 'offline',      -- online / offline
        verified    INTEGER DEFAULT 0,           -- 新设备是否通过验证
        verify_code TEXT,                        -- 一次性验证码
        bound_at    INTEGER,
        last_seen   INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_dev_tenant ON devices(tenant_id);
    """)
    cx.close()


def register_device(tenant_id: int, device_id: str, fingerprint: str, name: str = "",
                    verify_code: str = "") -> dict:
    """绑定/登记设备。已存在则更新指纹与最后心跳；新设备 verified=0 待审批。"""
    cx = _conn()
    row = cx.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    now = int(time.time())
    if row:
        cx.execute("UPDATE devices SET fingerprint=?, name=?, last_seen=? WHERE id=?",
                   (fingerprint, name or row["name"], now, device_id))
        cx.commit(); cx.close()
        return {"ok": True, "need_verify": not row["verified"], "existing": True,
                "is_primary": row["is_primary"]}
    # 新设备：若租户尚无任何设备 → 自动设为主控且免验证；否则待验证
    cnt = cx.execute("SELECT COUNT(*) c FROM devices WHERE tenant_id=?", (tenant_id,)).fetchone()["c"]
    is_primary = 1 if cnt == 0 else 0
    vc = verify_code if cnt > 0 else ""
    cx.execute(
        "INSERT INTO devices(id,tenant_id,name,fingerprint,is_primary,status,verified,verify_code,bound_at,last_seen) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (device_id, tenant_id, name, fingerprint, is_primary, "online",
         1 if cnt == 0 else 0, vc, now, now),
    )
    cx.commit(); cx.close()
    return {"ok": True, "need_verify": cnt > 0, "existing": False, "is_primary": is_primary}


def verify_device(device_id: str, code: str = "", admin: bool = False) -> dict:
    """新设备验证：验证码匹配 或 管理员直接批准 → verified=1。"""
    cx = _conn()
    row = cx.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    if not row:
        cx.close(); return {"ok": False, "error": "设备不存在"}
    if admin or (code and row["verify_code"] and code == row["verify_code"]):
        cx.execute("UPDATE devices SET verified=1, verify_code=NULL, status='online' WHERE id=?",
                   (device_id,))
        cx.commit(); ok = True
    else:
        ok = False
    cx.close()
    return {"ok": ok, "verified": ok}


def set_device_primary(tenant_id: int, device_id: str) -> dict:
    cx = _conn()
    cx.execute("UPDATE devices SET is_primary=0 WHERE tenant_id=?", (tenant_id,))
    cx.execute("UPDATE devices SET is_primary=1 WHERE id=? AND tenant_id=?", (device_id, tenant_id))
    cx.commit(); cx.close()
    return {"ok": True}


def unbind_device(tenant_id: int, device_id: str) -> dict:
    cx = _conn()
    cx.execute("DELETE FROM devices WHERE id=? AND tenant_id=?", (device_id, tenant_id))
    cx.commit(); cx.close()
    return {"ok": True}


def heartbeat_device(device_id: str) -> dict:
    cx = _conn()
    cx.execute("UPDATE devices SET last_seen=?, status='online' WHERE id=?",
               (int(time.time()), device_id))
    cx.commit(); cx.close()
    return {"ok": True}


def refresh_device_status() -> None:
    """后台定时：超过 90s 未心跳标记 offline。"""
    cx = _conn()
    cutoff = int(time.time()) - 90
    cx.execute("UPDATE devices SET status='offline' WHERE last_seen < ? AND status='online'",
               (cutoff,))
    cx.commit(); cx.close()


def list_devices(tenant_id: int) -> list:
    cx = _conn()
    rows = cx.execute("SELECT * FROM devices WHERE tenant_id=? ORDER BY is_primary DESC, bound_at",
                      (tenant_id,)).fetchall()
    cx.close()
    return [dict(r) for r in rows]


# ───────────────────────── 权限模板 / 租户权限 ─────────────────────────
def init_permissions_table() -> None:
    cx = _conn()
    cx.executescript("""
    CREATE TABLE IF NOT EXISTS permission_templates(
        id          TEXT PRIMARY KEY,
        tenant_id   INTEGER,                    -- NULL=系统内置，可复用
        name        TEXT,
        module_switches TEXT DEFAULT '{}',      -- {"创作台":true,"批量生成":false,...}
        data_scope  TEXT DEFAULT '{}',          -- {"categories":[...],"date_range":"all"}
        rate_limits TEXT DEFAULT '{}',          -- {"api_per_min":60,"video_per_day":50}
        billing_mode TEXT DEFAULT 'metered',    -- metered / subscription
        thresholds  TEXT DEFAULT '{}',          -- {"video_month":1000,"alert_at":0.8}
        inherit_from TEXT
    );
    CREATE TABLE IF NOT EXISTS tenant_permissions(
        tenant_id   INTEGER PRIMARY KEY,
        template_id TEXT,
        overrides   TEXT DEFAULT '{}'           -- 在模板基础上按需自定义
    );
    CREATE TABLE IF NOT EXISTS audit_logs(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id   INTEGER, actor TEXT, action TEXT, target TEXT,
        detail TEXT, ip TEXT, created_at INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_logs(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
    """)
    cx.close()


def _default_module_switches() -> dict:
    return {"创作台": True, "AI二创": True, "批量生成": False, "多账号分发": True,
            "公众号自动化": False, "朋友圈自动化": False, "QC评分": True}


def create_template(name: str, tenant_id: int | None = None,
                    module_switches: dict | None = None, data_scope: dict | None = None,
                    rate_limits: dict | None = None, billing_mode: str = "metered",
                    thresholds: dict | None = None, inherit_from: str = "") -> dict:
    cx = _conn()
    tid = "tpl_" + secrets.token_hex(8)
    cx.execute(
        "INSERT INTO permission_templates(id,tenant_id,name,module_switches,data_scope,rate_limits,billing_mode,thresholds,inherit_from) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (tid, tenant_id, name,
         json.dumps(module_switches or _default_module_switches(), ensure_ascii=False),
         json.dumps(data_scope or {}, ensure_ascii=False),
         json.dumps(rate_limits or {}, ensure_ascii=False),
         billing_mode, json.dumps(thresholds or {}, ensure_ascii=False), inherit_from),
    )
    cx.commit(); cx.close()
    return {"ok": True, "id": tid}


def list_templates(tenant_id: int | None = None) -> list:
    cx = _conn()
    if tenant_id is None:
        rows = cx.execute("SELECT * FROM permission_templates ORDER BY tenant_id IS NOT NULL, name").fetchall()
    else:
        rows = cx.execute(
            "SELECT * FROM permission_templates WHERE tenant_id=? OR tenant_id IS NULL ORDER BY tenant_id IS NOT NULL, name",
            (tenant_id,)).fetchall()
    cx.close()
    return [dict(r) for r in rows]


def get_template(tpl_id: str) -> dict | None:
    cx = _conn()
    row = cx.execute("SELECT * FROM permission_templates WHERE id=?", (tpl_id,)).fetchone()
    cx.close()
    return dict(row) if row else None


def _merge_permissions(tpl: dict, overrides: dict) -> dict:
    """模板 + 覆盖项合并（覆盖项优先）。"""
    def _load(s):
        try: return json.loads(s or "{}")
        except Exception: return {}
    base = {
        "module_switches": _load(tpl["module_switches"]),
        "data_scope": _load(tpl["data_scope"]),
        "rate_limits": _load(tpl["rate_limits"]),
        "billing_mode": tpl["billing_mode"],
        "thresholds": _load(tpl["thresholds"]),
    }
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k].update(v)
        else:
            base[k] = v
    return base


def set_tenant_permissions(tenant_id: int, template_id: str, overrides: dict | None = None) -> dict:
    cx = _conn()
    cx.execute(
        "INSERT INTO tenant_permissions(tenant_id,template_id,overrides) VALUES(?,?,?) "
        "ON CONFLICT(tenant_id) DO UPDATE SET template_id=excluded.template_id, overrides=excluded.overrides",
        (tenant_id, template_id, json.dumps(overrides or {}, ensure_ascii=False)),
    )
    cx.commit(); cx.close()
    return {"ok": True}


def get_tenant_permissions(tenant_id: int) -> dict:
    """实时权限解析（每次请求调用，无需重登）。"""
    cx = _conn()
    row = cx.execute("SELECT * FROM tenant_permissions WHERE tenant_id=?", (tenant_id,)).fetchone()
    cx.close()
    if not row or not row["template_id"]:
        # 默认全开（兼容旧单租户）
        return {"module_switches": _default_module_switches(), "data_scope": {},
                "rate_limits": {}, "billing_mode": "metered", "thresholds": {}}
    tpl = get_template(row["template_id"])
    if not tpl:
        return {"module_switches": _default_module_switches(), "data_scope": {},
                "rate_limits": {}, "billing_mode": "metered", "thresholds": {}}
    try:
        overrides = json.loads(row["overrides"] or "{}")
    except Exception:
        overrides = {}
    return _merge_permissions(tpl, overrides)


def check_module_enabled(tenant_id: int, module: str) -> bool:
    return bool(get_tenant_permissions(tenant_id).get("module_switches", {}).get(module, True))


# ───────────────────────── 操作日志审计 ─────────────────────────
def add_audit(tenant_id: int, actor: str, action: str, target: str = "", detail: str = "", ip: str = "") -> None:
    cx = _conn()
    cx.execute(
        "INSERT INTO audit_logs(tenant_id,actor,action,target,detail,ip,created_at) VALUES(?,?,?,?,?,?,?)",
        (tenant_id, actor, action, target, detail, ip, int(time.time())),
    )
    cx.commit(); cx.close()


def list_audit(tenant_id: int | None = None, action: str = "", actor: str = "",
               start: int = 0, end: int = 0, limit: int = 200) -> list:
    cx = _conn()
    sql = "SELECT * FROM audit_logs WHERE 1=1"
    args = []
    if tenant_id is not None:
        sql += " AND tenant_id=?"; args.append(tenant_id)
    if action:
        sql += " AND action=?"; args.append(action)
    if actor:
        sql += " AND actor=?"; args.append(actor)
    if start:
        sql += " AND created_at>=?"; args.append(start)
    if end:
        sql += " AND created_at<=?"; args.append(end)
    sql += " ORDER BY created_at DESC LIMIT ?"; args.append(limit)
    rows = cx.execute(sql, args).fetchall()
    cx.close()
    return [dict(r) for r in rows]


# ───────────────────────── 计量 / 计费 / 阈值 ─────────────────────────
def init_billing_table() -> None:
    cx = _conn()
    cx.executescript("""
    CREATE TABLE IF NOT EXISTS usage_records(
        tenant_id   INTEGER NOT NULL,
        metric      TEXT NOT NULL,              -- videos / minutes / api_calls
        period      TEXT NOT NULL,              -- 2026-07 (月) 或 daily:2026-07-30
        value       INTEGER DEFAULT 0,
        updated_at  INTEGER,
        PRIMARY KEY(tenant_id, metric, period)
    );
    CREATE TABLE IF NOT EXISTS billing_events(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id   INTEGER, type TEXT, metric TEXT,
        threshold   REAL, triggered_at INTEGER, handled INTEGER DEFAULT 0
    );
    """)
    cx.close()


def add_usage(tenant_id: int, metric: str, delta: int = 1, period: str = "") -> dict:
    """累加用量；返回是否触发阈值（alert/limit）。"""
    if not period:
        period = time.strftime("%Y-%m")
    cx = _conn()
    cx.execute(
        "INSERT INTO usage_records(tenant_id,metric,period,value,updated_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(tenant_id,metric,period) DO UPDATE SET value=value+excluded.value, updated_at=excluded.updated_at",
        (tenant_id, metric, period, delta, int(time.time())),
    )
    cx.commit(); cx.close()
    # 阈值检测
    perms = get_tenant_permissions(tenant_id)
    thr = perms.get("thresholds", {})
    limit = thr.get(metric)
    # 重新读取当前值
    cx2 = _conn()
    val = cx2.execute("SELECT value FROM usage_records WHERE tenant_id=? AND metric=? AND period=?",
                      (tenant_id, metric, period)).fetchone()
    cx2.close()
    cur_val = val["value"] if val else delta
    events = []
    if limit:
        ratio = cur_val / float(limit)
        if ratio >= 1.0:
            _fire_billing_event(tenant_id, "limit", metric, limit)
            events.append({"type": "limit", "metric": metric, "value": cur_val, "limit": limit})
        elif ratio >= thr.get("alert_at", 0.8):
            _fire_billing_event(tenant_id, "alert", metric, limit)
            events.append({"type": "alert", "metric": metric, "value": cur_val, "limit": limit})
    return {"ok": True, "value": cur_val, "events": events}


def _fire_billing_event(tenant_id: int, etype: str, metric: str, threshold: float) -> None:
    cx = _conn()
    cx.execute(
        "INSERT INTO billing_events(tenant_id,type,metric,threshold,triggered_at) VALUES(?,?,?,?,?)",
        (tenant_id, etype, metric, threshold, int(time.time())),
    )
    cx.commit(); cx.close()


# ───────────────────────── 会话（登录态） ─────────────────────────
def init_sessions_table() -> None:
    cx = _conn()
    cx.executescript("""
    CREATE TABLE IF NOT EXISTS sessions(
        token      TEXT PRIMARY KEY,
        tenant_id  INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        created_at INTEGER,
        expires_at INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_sess_tenant ON sessions(tenant_id);
    """)
    cx.close()


def create_session(tenant_id, user_id, ttl: int = 86400) -> str:
    tok = "s-" + secrets.token_hex(24)
    now = int(time.time())
    cx = _conn()
    cx.execute(
        "INSERT INTO sessions(token,tenant_id,user_id,created_at,expires_at) VALUES(?,?,?,?,?)",
        (tok, tenant_id, user_id, now, now + ttl),
    )
    cx.commit(); cx.close()
    return tok


def get_session(token) -> dict | None:
    if not token:
        return None
    cx = _conn()
    row = cx.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    cx.close()
    if not row:
        return None
    if row["expires_at"] and row["expires_at"] < int(time.time()):
        _ec = _conn()
        _ec.execute("DELETE FROM sessions WHERE token=?", (token,))
        _ec.commit(); _ec.close()
        return None
    return dict(row)


def delete_session(token) -> None:
    cx = _conn()
    cx.execute("DELETE FROM sessions WHERE token=?", (token,))
    cx.commit(); cx.close()


def get_usage(tenant_id: int, metric: str, period: str = "") -> int:
    if not period:
        period = time.strftime("%Y-%m")
    cx = _conn()
    row = cx.execute("SELECT value FROM usage_records WHERE tenant_id=? AND metric=? AND period=?",
                     (tenant_id, metric, period)).fetchall()
    cx.close()
    return row[0]["value"] if row else 0


# ───────────────────────── 租户形象 / 声音隔离（Phase 2+） ─────────────────────────
def init_avatar_voice_table() -> None:
    cx = _conn()
    cx.executescript("""
    CREATE TABLE IF NOT EXISTS tenant_avatars(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id   INTEGER NOT NULL,
        avatar_name TEXT NOT NULL,
        model_path  TEXT NOT NULL,
        role        TEXT DEFAULT 'solo',
        source      TEXT DEFAULT 'local_clone',
        status      TEXT DEFAULT 'active',
        is_default  INTEGER DEFAULT 0,
        created_at  INTEGER
    );
    CREATE TABLE IF NOT EXISTS tenant_voices(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id   INTEGER NOT NULL,
        voice_label TEXT NOT NULL,
        cosy_voice_id TEXT NOT NULL,
        ref_audio   TEXT DEFAULT '',
        gender      TEXT DEFAULT 'male',
        status      TEXT DEFAULT 'active',
        is_default  INTEGER DEFAULT 0,
        created_at  INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_av_tenant ON tenant_avatars(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_vc_tenant ON tenant_voices(tenant_id);
    """)
    cx.close()


def register_tenant_avatar(tenant_id, avatar_name, model_path, role="solo",
                           source="local_clone", is_default=0) -> dict:
    cx = _conn()
    cx.execute(
        "INSERT INTO tenant_avatars(tenant_id,avatar_name,model_path,role,source,status,is_default,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (tenant_id, avatar_name, model_path, role, source, "active", is_default, int(time.time())),
    )
    cx.commit(); cx.close()
    return {"ok": True}


def list_tenant_avatars(tenant_id) -> list:
    cx = _conn()
    rows = cx.execute("SELECT * FROM tenant_avatars WHERE tenant_id=? ORDER BY is_default DESC, id",
                      (tenant_id,)).fetchall()
    cx.close()
    return [dict(r) for r in rows]


def register_tenant_voice(tenant_id, voice_label, cosy_voice_id, gender="male",
                          ref_audio="", is_default=0) -> dict:
    cx = _conn()
    cx.execute(
        "INSERT INTO tenant_voices(tenant_id,voice_label,cosy_voice_id,ref_audio,gender,status,is_default,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (tenant_id, voice_label, cosy_voice_id, ref_audio, gender, "active", is_default, int(time.time())),
    )
    cx.commit(); cx.close()
    return {"ok": True}


def list_tenant_voices(tenant_id) -> list:
    cx = _conn()
    rows = cx.execute("SELECT * FROM tenant_voices WHERE tenant_id=? ORDER BY is_default DESC, gender, id",
                      (tenant_id,)).fetchall()
    cx.close()
    return [dict(r) for r in rows]


def get_tenant_avatar_path(tenant_id, role="solo") -> str:
    """该租户默认形象（静音驱动视频）路径；无则返回创建者默认。"""
    cx = _conn()
    row = cx.execute(
        "SELECT model_path FROM tenant_avatars WHERE tenant_id=? AND status='active' "
        "ORDER BY is_default DESC, id LIMIT 1", (tenant_id,)
    ).fetchone()
    cx.close()
    if row:
        return row["model_path"]
    return str(BASE / "face2face" / "BGZSP20260721_t18_silent.mp4")


def get_tenant_voice_id(tenant_id, gender="male") -> str:
    """该租户指定性别默认声音 cosy_voice_id；无则回退创建者默认。"""
    cx = _conn()
    row = cx.execute(
        "SELECT cosy_voice_id FROM tenant_voices WHERE tenant_id=? AND gender=? AND status='active' "
        "ORDER BY is_default DESC, id LIMIT 1", (tenant_id, gender)
    ).fetchone()
    cx.close()
    if row:
        return row["cosy_voice_id"]
    return ""  # 无克隆声线 → 返回空；上层须提示「请先克隆或选择声音」，禁止回退到特定克隆音


def _seed_default_avatar_voice(tenant_id) -> None:
    """预置创建者默认形象/声音（仅当该租户尚无记录）。"""
    cx = _conn()
    acnt = cx.execute("SELECT COUNT(*) c FROM tenant_avatars WHERE tenant_id=?", (tenant_id,)).fetchone()["c"]
    vcnt = cx.execute("SELECT COUNT(*) c FROM tenant_voices WHERE tenant_id=?", (tenant_id,)).fetchone()["c"]
    cx.close()
    if acnt == 0:
        register_tenant_avatar(tenant_id, "张老师·财税主讲",
                               str(BASE / "face2face" / "BGZSP20260721_t18_silent.mp4"),
                               role="solo", is_default=1)
    if vcnt == 0:
        # 不再自动播种自带声音：新租户初始无声音，须自行克隆或选公开模板（遵循通用行业平台铁律）
        pass
    # 预置默认管理员账号（仅当该租户尚无用户）
    _ucx = _conn()
    ucnt = _ucx.execute("SELECT COUNT(*) c FROM users WHERE tenant_id=?", (tenant_id,)).fetchone()["c"]
    _ucx.close()
    if ucnt == 0:
        create_user(tenant_id, "admin", "admin888", role="admin")


# ───────────────────────── 账号 / 会话（多租户鉴权） ─────────────────────────
import hashlib
_PW_SALT = "hgt_pw_salt_v1"

def _hash_pw(pw: str) -> str:
    return hashlib.sha256((pw + _PW_SALT).encode("utf-8")).hexdigest()

def create_user(tenant_id, username, password, role="editor") -> dict:
    cx = _conn()
    try:
        cx.execute("INSERT INTO users(tenant_id,username,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                   (tenant_id, username, _hash_pw(password), role, int(time.time())))
        cx.commit(); cx.close(); return {"ok": True}
    except sqlite3.IntegrityError as e:
        cx.rollback(); cx.close(); return {"ok": False, "error": str(e)}

def verify_user(username: str, password: str) -> dict | None:
    cx = _conn()
    row = cx.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    cx.close()
    if not row:
        return None
    if row["password_hash"] != _hash_pw(password):
        return None
    return dict(row)

def get_user_by_id(user_id: int) -> dict | None:
    cx = _conn()
    row = cx.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    cx.close()
    return dict(row) if row else None


# 初始化时一并建表
def init_all() -> dict:
    t = init_db()
    init_devices_table()
    init_permissions_table()
    init_billing_table()
    init_sessions_table()
    init_avatar_voice_table()
    _seed_default_avatar_voice(t["id"])
    # 默认权限模板（系统内置，可复用）
    cx = _conn()
    has = cx.execute("SELECT COUNT(*) c FROM permission_templates").fetchone()["c"]
    cx.close()
    if has == 0:
        create_template("基础版", tenant_id=None,
                        module_switches={"创作台": True, "AI二创": True, "批量生成": False,
                                         "多账号分发": True, "公众号自动化": False,
                                         "朋友圈自动化": False, "QC评分": True},
                        rate_limits={"api_per_min": 30, "video_per_day": 20},
                        billing_mode="metered", thresholds={"videos": 500, "alert_at": 0.8})
        create_template("专业版", tenant_id=None,
                        module_switches={"创作台": True, "AI二创": True, "批量生成": True,
                                         "多账号分发": True, "公众号自动化": True,
                                         "朋友圈自动化": False, "QC评分": True},
                        rate_limits={"api_per_min": 120, "video_per_day": 100},
                        billing_mode="subscription", thresholds={"videos": 2000, "alert_at": 0.8})
        create_template("企业版", tenant_id=None,
                        module_switches={"创作台": True, "AI二创": True, "批量生成": True,
                                         "多账号分发": True, "公众号自动化": True,
                                         "朋友圈自动化": True, "QC评分": True},
                        rate_limits={"api_per_min": 600, "video_per_day": 500},
                        billing_mode="subscription", thresholds={"videos": 10000, "alert_at": 0.85})
        # 默认租户套用基础版
        tpl = list_templates()
        if tpl:
            set_tenant_permissions(t["id"], tpl[0]["id"])
    return t


if __name__ == "__main__":
    t = init_all()
    print("init_all OK, default tenant:", t.get("slug"), "token:", t.get("token"))
    print("tenants:", list_tenants())
    print("templates:", [x["name"] for x in list_templates()])
