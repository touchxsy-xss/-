#!/usr/bin/env python3
"""Small, dependency-free API for the Raspberry Pi demo deployment."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import base64
import binascii
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

DATABASE_PATH = Path(os.environ.get("SHENGBIAN_DB_PATH", "/srv/homeserver/compose/healthpal/data/shengbian.db"))
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "sqlite"
UPLOADS_DIR = Path(os.environ.get("SHENGBIAN_UPLOADS_DIR", str(DATABASE_PATH.parent / "uploads")))
MAX_REQUEST_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024
ALLOWED_ATTACHMENT_TYPES = {"image/jpeg", "image/png", "image/webp", "video/mp4", "video/quicktime"}
STATUS_LABELS = {
    "new": "待处理",
    "processing": "处理中",
    "awaiting_vendor": "等待第三方",
    "awaiting_confirmation": "待居民确认",
    "resolved": "已解决",
    "reopened": "已重新打开",
}
ALLOWED_TICKET_TRANSITIONS = {
    "new": {"processing"},
    "processing": {"awaiting_vendor", "awaiting_confirmation"},
    "awaiting_vendor": {"processing", "awaiting_confirmation"},
    "reopened": {"processing"},
}


class ApiError(Exception):
    def __init__(self, code: int, detail: str):
        self.code = code
        self.detail = detail


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def next_ticket_public_id(db: sqlite3.Connection) -> str:
    """Generate a non-sequential public ID instead of deriving one from a mutable count."""
    prefix = f"SB-{datetime.now():%Y%m%d}"
    while True:
        candidate = f"{prefix}-{secrets.token_hex(4).upper()}"
        if not db.execute("SELECT 1 FROM repair_tickets WHERE public_id = ?", (candidate,)).fetchone():
            return candidate


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_database() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS property_companies (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS communities (
                id INTEGER PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                city TEXT NOT NULL,
                district TEXT NOT NULL,
                resident_count INTEGER NOT NULL DEFAULT 0,
                property_company_id INTEGER NOT NULL REFERENCES property_companies(id)
            );
            CREATE TABLE IF NOT EXISTS buildings (
                id INTEGER PRIMARY KEY,
                community_id INTEGER NOT NULL REFERENCES communities(id),
                name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS units (
                id INTEGER PRIMARY KEY,
                building_id INTEGER NOT NULL REFERENCES buildings(id),
                number TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                demo_key TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('platform', 'property', 'resident')),
                phone TEXT,
                community_id INTEGER REFERENCES communities(id),
                unit_id INTEGER REFERENCES units(id)
            );
            CREATE TABLE IF NOT EXISTS repair_tickets (
                id INTEGER PRIMARY KEY,
                public_id TEXT NOT NULL UNIQUE,
                community_id INTEGER NOT NULL REFERENCES communities(id),
                resident_id INTEGER NOT NULL REFERENCES users(id),
                category TEXT NOT NULL,
                location TEXT NOT NULL,
                description TEXT NOT NULL,
                contact TEXT,
                status TEXT NOT NULL,
                assignee TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS repair_events (
                id INTEGER PRIMARY KEY,
                ticket_id INTEGER NOT NULL REFERENCES repair_tickets(id) ON DELETE CASCADE,
                actor_name TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                status TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY,
                actor_id INTEGER NOT NULL REFERENCES users(id),
                community_id INTEGER REFERENCES communities(id),
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS work_materials (
                id INTEGER PRIMARY KEY,
                community_id INTEGER NOT NULL REFERENCES communities(id),
                title TEXT NOT NULL,
                material_type TEXT NOT NULL,
                category TEXT NOT NULL,
                item_count INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'ready',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY,
                community_id INTEGER NOT NULL REFERENCES communities(id),
                author_id INTEGER NOT NULL REFERENCES users(id),
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('draft', 'submitted', 'published')),
                published_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS article_reviews (
                id INTEGER PRIMARY KEY,
                article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                reviewer_id INTEGER NOT NULL REFERENCES users(id),
                decision TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY,
                community_id INTEGER NOT NULL REFERENCES communities(id),
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL,
                published_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS repair_tickets_community_idx ON repair_tickets(community_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS repair_tickets_resident_idx ON repair_tickets(resident_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS articles_community_idx ON articles(community_id, status, updated_at DESC);
            """
        )
        apply_migrations(db)
        ensure_runtime_schema(db)
        if not db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            seed_database(db)
        ensure_demo_workers(db)


def apply_migrations(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if migration.name.startswith("."):
            continue
        version = migration.stem
        if db.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (version,)).fetchone():
            continue
        db.executescript(migration.read_text(encoding="utf-8"))
        db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (version, now()))


def ensure_runtime_schema(db: sqlite3.Connection) -> None:
    """Keep the demo database compatible with the API even when it predates a migration.

    The public demo has been deployed from more than one database snapshot.  The
    original bootstrap schema did not include the columns/tables used by the live
    endpoints, so a clean install could render the page but fail on the first
    ticket, feedback, or announcement request.  This idempotent check is safe to
    run on every startup and also repairs an older snapshot without destructive
    changes.
    """
    def ensure_column(table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    ensure_column("repair_tickets", "version", "INTEGER NOT NULL DEFAULT 1")
    ensure_column("repair_tickets", "expected_at", "TEXT")
    ensure_column("repair_tickets", "source", "TEXT NOT NULL DEFAULT 'resident_app'")
    ensure_column("repair_tickets", "worker_id", "INTEGER")
    for column, definition in (
        ("image_url", "TEXT"),
        ("link_url", "TEXT"),
        ("link_type", "TEXT NOT NULL DEFAULT 'announcement'"),
        ("sort_order", "INTEGER NOT NULL DEFAULT 0"),
    ):
        ensure_column("announcements", column, definition)

    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS repair_attachments (
            id INTEGER PRIMARY KEY,
            ticket_id INTEGER NOT NULL REFERENCES repair_tickets(id) ON DELETE CASCADE,
            uploader_id INTEGER NOT NULL REFERENCES users(id),
            storage_key TEXT NOT NULL UNIQUE,
            file_name TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS repair_attachments_ticket_idx ON repair_attachments(ticket_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS feedback_tickets (
            id INTEGER PRIMARY KEY,
            community_id INTEGER NOT NULL REFERENCES communities(id),
            resident_id INTEGER NOT NULL REFERENCES users(id),
            type TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS feedback_messages (
            id INTEGER PRIMARY KEY,
            feedback_id INTEGER NOT NULL REFERENCES feedback_tickets(id) ON DELETE CASCADE,
            author_id INTEGER NOT NULL REFERENCES users(id),
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS feedback_tickets_community_idx ON feedback_tickets(community_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS feedback_messages_feedback_idx ON feedback_messages(feedback_id, id);
        """
    )


def ensure_demo_workers(db: sqlite3.Connection) -> None:
    """Provision separate, least-privilege demo identities for the worker mini program.

    The original users table has a fixed role constraint.  Worker accounts retain
    the compatible ``property`` database role, while this profile table is the
    authorization boundary used by worker-only endpoints.  That lets existing
    deployments migrate without rebuilding their users table.
    """
    community = db.execute("SELECT id FROM communities WHERE slug = 'pengyi'").fetchone()
    if not community:
        return
    timestamp = now()
    profiles = (("worker-wang", "王师傅", "水电与公共照明"), ("worker-li", "李师傅", "给排水与公共设施"))
    for demo_key, display_name, specialty in profiles:
        db.execute(
            "INSERT OR IGNORE INTO users(demo_key, display_name, role, community_id) VALUES (?, ?, 'property', ?)",
            (demo_key, display_name, community["id"]),
        )
        user = db.execute("SELECT id FROM users WHERE demo_key = ?", (demo_key,)).fetchone()
        db.execute(
            "INSERT OR IGNORE INTO repair_workers(user_id, demo_key, display_name, community_id, specialty, active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (user["id"], demo_key, display_name, community["id"], specialty, timestamp),
        )


def one_id(db: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return int(db.execute(sql, params).fetchone()[0])


def seed_database(db: sqlite3.Connection) -> None:
    for name in ("浦发物业", "安居服务", "华景物业"):
        db.execute("INSERT INTO property_companies(name) VALUES (?)", (name,))
    company_ids = {row["name"]: row["id"] for row in db.execute("SELECT id, name FROM property_companies")}
    communities = [
        ("pengyi", "彭一小区", "上海", "浦东新区", 2384, "浦发物业"),
        ("binjiang", "滨江家园", "上海", "徐汇区", 1958, "浦发物业"),
        ("jinyue", "金悦府", "上海", "闵行区", 1415, "安居服务"),
        ("xingang", "新港花园", "上海", "杨浦区", 2126, "华景物业"),
    ]
    for slug, name, city, district, residents, company in communities:
        db.execute(
            "INSERT INTO communities(slug, name, city, district, resident_count, property_company_id) VALUES (?, ?, ?, ?, ?, ?)",
            (slug, name, city, district, residents, company_ids[company]),
        )
    community_ids = {row["slug"]: row["id"] for row in db.execute("SELECT id, slug FROM communities")}
    db.execute("INSERT INTO buildings(community_id, name) VALUES (?, ?)", (community_ids["pengyi"], "16 号楼 2 单元"))
    db.execute("INSERT INTO buildings(community_id, name) VALUES (?, ?)", (community_ids["pengyi"], "3 号楼 1 单元"))
    building_16 = one_id(db, "SELECT id FROM buildings WHERE name = ?", ("16 号楼 2 单元",))
    building_3 = one_id(db, "SELECT id FROM buildings WHERE name = ?", ("3 号楼 1 单元",))
    db.execute("INSERT INTO units(building_id, number) VALUES (?, ?)", (building_16, "502"))
    db.execute("INSERT INTO units(building_id, number) VALUES (?, ?)", (building_3, "301"))
    unit_502 = one_id(db, "SELECT id FROM units WHERE building_id = ?", (building_16,))
    unit_301 = one_id(db, "SELECT id FROM units WHERE building_id = ?", (building_3,))
    users = [
        ("platform-admin", "陈总", "platform", None, None, None),
        ("property-pengyi", "周敏", "property", "021-6808 0228", community_ids["pengyi"], None),
        ("resident-li", "李女士", "resident", "138****6272", community_ids["pengyi"], unit_502),
        ("resident-zhang", "张先生", "resident", "139****2031", community_ids["pengyi"], unit_301),
    ]
    db.executemany(
        "INSERT INTO users(demo_key, display_name, role, phone, community_id, unit_id) VALUES (?, ?, ?, ?, ?, ?)", users
    )
    user_ids = {row["demo_key"]: row["id"] for row in db.execute("SELECT id, demo_key FROM users")}

    def add_ticket(slug: str, resident_key: str, public_id: str, category: str, location: str, description: str, ticket_status: str, assignee: str | None, events: list[tuple[str, str, str, str]]) -> None:
        created_at = now()
        cursor = db.execute(
            """INSERT INTO repair_tickets(public_id, community_id, resident_id, category, location, description, contact, status, assignee, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (public_id, community_ids[slug], user_ids[resident_key], category, location, description, "138****6272", ticket_status, assignee, created_at, created_at),
        )
        db.executemany(
            "INSERT INTO repair_events(ticket_id, actor_name, actor_role, status, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(cursor.lastrowid, actor, role, event_status, note, now()) for actor, role, event_status, note in events],
        )

    add_ticket("pengyi", "resident-li", "SB-20260817-10248", "照明", "16 号楼 2 单元 1 层楼道", "楼道照明不亮，晚上经过时看不清台阶，希望尽快处理。", "awaiting_vendor", "照明维保单位", [("李女士", "resident", "new", "居民提交报修，已上传 2 张图片。"), ("周敏", "property", "processing", "物业已受理并分派工程处理。"), ("周敏", "property", "awaiting_vendor", "已联系照明维保单位，最近一次催办：09:42。")])
    add_ticket("pengyi", "resident-zhang", "SB-20260817-10247", "电梯", "3 号楼 1 单元", "电梯运行时有明显异响，居民已上传 1 段视频。", "awaiting_vendor", "永达电梯维保", [("张先生", "resident", "new", "居民提交电梯异响问题。"), ("周敏", "property", "processing", "客服已受理并联系工程部。"), ("周敏", "property", "awaiting_vendor", "维保单位承诺 11:00 到场，已记录催办。")])
    add_ticket("pengyi", "resident-li", "SB-20260816-10242", "给排水", "B2 区地下车库", "车库排水需要复查。", "awaiting_confirmation", "李师傅", [("李女士", "resident", "new", "居民提交车库排水检查。"), ("周敏", "property", "processing", "工程部已接单。"), ("周敏", "property", "awaiting_confirmation", "已完成检查，等待居民确认。")])
    add_ticket("pengyi", "resident-zhang", "SB-20260816-10239", "门禁", "9 号楼大门", "门禁识别异常。", "new", None, [("张先生", "resident", "new", "居民提交门禁识别异常。")])
    add_ticket("binjiang", "resident-li", "SB-20260817-20101", "停车秩序", "东门车库", "夜间临停占位。", "processing", "秩序队", [("平台巡检", "platform", "processing", "已同步给项目负责人处理。")])
    add_ticket("jinyue", "resident-li", "SB-20260817-30101", "环境卫生", "7 号楼公共区域", "楼道堆物待清理。", "new", None, [("平台巡检", "platform", "new", "项目待处理。")])
    add_ticket("xingang", "resident-li", "SB-20260817-40101", "电梯", "12 号楼", "电梯停靠异常。", "awaiting_vendor", "第三方维保", [("平台巡检", "platform", "awaiting_vendor", "等待第三方到场。")])
    created_at = now()
    db.executemany(
        "INSERT INTO work_materials(community_id, title, material_type, category, item_count, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (community_ids["pengyi"], "地下车库排水检查", "image", "工程 / 给排水", 6, "ready", created_at),
            (community_ids["pengyi"], "16 号楼公共照明维修", "video", "工程 / 公共区域", 1, "ready", created_at),
            (community_ids["pengyi"], "夏季绿化养护", "image", "绿化", 18, "ready", created_at),
            (community_ids["pengyi"], "本周工作总结与下周计划", "text", "客服", 1, "needs_input", created_at),
        ],
    )
    db.executemany(
        "INSERT INTO articles(community_id, author_id, title, body, status, published_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (community_ids["pengyi"], user_ids["property-pengyi"], "这一周，我们把小区的每一处细节放在心上", "工程团队完成地下车库排水专项检查，并对 16 号楼公共照明进行维修；绿化团队持续开展夏季养护。关于居民关心的电梯问题，物业已联系第三方维保单位并持续跟进。", "published", created_at, created_at, created_at),
            (community_ids["pengyi"], user_ids["property-pengyi"], "彭一小区第 33 周物业周记", "本周围绕照明、电梯、绿化与车库排水开展了重点服务。请平台审核后向居民发布。", "submitted", None, created_at, created_at),
        ],
    )
    db.execute("INSERT INTO announcements(community_id, title, body, status, published_at) VALUES (?, ?, ?, ?, ?)", (community_ids["pengyi"], "3 号楼电梯例行检修通知", "8 月 19 日 9:00-12:00 进行例行检修，请合理安排出行。", "published", created_at))


def require_role(user: sqlite3.Row, *roles: str) -> None:
    if user["role"] not in roles:
        raise ApiError(HTTPStatus.FORBIDDEN, "Role is not allowed for this action")


def current_user(db: sqlite3.Connection, headers) -> sqlite3.Row:
    demo_key = headers.get("X-Demo-User")
    if not demo_key:
        raise ApiError(HTTPStatus.UNAUTHORIZED, "Missing demo identity")
    user = db.execute("SELECT * FROM users WHERE demo_key = ?", (demo_key,)).fetchone()
    if not user:
        raise ApiError(HTTPStatus.UNAUTHORIZED, "Unknown demo identity")
    return user


def community_for(db: sqlite3.Connection, user: sqlite3.Row, slug: str | None = None) -> sqlite3.Row:
    if slug:
        community = db.execute("SELECT * FROM communities WHERE slug = ?", (slug,)).fetchone()
    elif user["community_id"]:
        community = db.execute("SELECT * FROM communities WHERE id = ?", (user["community_id"],)).fetchone()
    else:
        community = None
    if not community:
        raise ApiError(HTTPStatus.NOT_FOUND, "Community not found")
    if user["role"] != "platform" and user["community_id"] != community["id"]:
        raise ApiError(HTTPStatus.FORBIDDEN, "Community is outside this account scope")
    return community


def serialize_community(db: sqlite3.Connection, community: sqlite3.Row) -> dict:
    company = db.execute("SELECT name FROM property_companies WHERE id = ?", (community["property_company_id"],)).fetchone()["name"]
    return {"id": community["id"], "slug": community["slug"], "name": community["name"], "city": community["city"], "district": community["district"], "residentCount": community["resident_count"], "propertyCompany": company}


def serialize_user(db: sqlite3.Connection, user: sqlite3.Row) -> dict:
    community = db.execute("SELECT slug FROM communities WHERE id = ?", (user["community_id"],)).fetchone() if user["community_id"] else None
    unit = None
    if user["unit_id"]:
        unit_row = db.execute("SELECT buildings.name AS building_name, units.number FROM units JOIN buildings ON buildings.id = units.building_id WHERE units.id = ?", (user["unit_id"],)).fetchone()
        unit = f"{unit_row['building_name']} {unit_row['number']}"
    role = "worker" if is_repair_worker(db, user) else user["role"]
    return {"id": user["id"], "name": user["display_name"], "role": role, "community": community["slug"] if community else None, "unit": unit}


def ticket_row(db: sqlite3.Connection, ticket_id: int) -> sqlite3.Row:
    row = db.execute("SELECT repair_tickets.*, communities.slug AS community_slug, communities.name AS community_name, users.display_name AS resident_name, users.demo_key AS resident_demo_key FROM repair_tickets JOIN communities ON communities.id = repair_tickets.community_id JOIN users ON users.id = repair_tickets.resident_id WHERE repair_tickets.id = ?", (ticket_id,)).fetchone()
    if not row:
        raise ApiError(HTTPStatus.NOT_FOUND, "Ticket not found")
    return row


def serialize_ticket(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    events = db.execute("SELECT * FROM repair_events WHERE ticket_id = ? ORDER BY id", (row["id"],)).fetchall()
    attachments = db.execute("SELECT repair_attachments.id, repair_attachments.file_name, repair_attachments.mime_type, repair_attachments.byte_size, repair_attachments.created_at, COALESCE(repair_attachment_stages.stage, 'resident_report') AS stage FROM repair_attachments LEFT JOIN repair_attachment_stages ON repair_attachment_stages.attachment_id = repair_attachments.id WHERE repair_attachments.ticket_id = ? ORDER BY repair_attachments.created_at DESC", (row["id"],)).fetchall()
    work_logs = db.execute("SELECT * FROM repair_work_logs WHERE ticket_id = ? ORDER BY id", (row["id"],)).fetchall()
    review = db.execute("SELECT * FROM repair_reviews WHERE ticket_id = ?", (row["id"],)).fetchone()
    return {
        "id": row["id"], "publicId": row["public_id"], "community": row["community_slug"], "communityName": row["community_name"], "resident": row["resident_name"], "residentKey": row["resident_demo_key"], "category": row["category"], "location": row["location"], "description": row["description"], "contact": row["contact"], "status": row["status"], "statusLabel": STATUS_LABELS[row["status"]], "assignee": row["assignee"], "workerId": row["worker_id"], "version": row["version"], "expectedAt": row["expected_at"], "createdAt": row["created_at"], "updatedAt": row["updated_at"], "checkInAt": row["check_in_at"], "checkInNote": row["check_in_note"], "completionNote": row["completion_note"], "completedAt": row["completed_at"],
        "attachments": [{"id": item["id"], "fileName": item["file_name"], "mimeType": item["mime_type"], "byteSize": item["byte_size"], "stage": item["stage"], "createdAt": item["created_at"], "url": f"/api/repairs/{row['id']}/attachments/{item['id']}"} for item in attachments],
        "events": [{"id": event["id"], "actor": event["actor_name"], "role": event["actor_role"], "status": event["status"], "statusLabel": STATUS_LABELS[event["status"]], "note": event["note"], "createdAt": event["created_at"]} for event in events],
        "workLogs": [{"id": log["id"], "workerName": log["worker_name"], "action": log["action"], "note": log["note"], "latitude": log["latitude"], "longitude": log["longitude"], "createdAt": log["created_at"]} for log in work_logs],
        "review": serialize_review(db, review) if review else None,
    }


def article_row(db: sqlite3.Connection, article_id: int) -> sqlite3.Row:
    row = db.execute("SELECT articles.*, communities.slug AS community_slug, communities.name AS community_name, users.display_name AS author_name FROM articles JOIN communities ON communities.id = articles.community_id JOIN users ON users.id = articles.author_id WHERE articles.id = ?", (article_id,)).fetchone()
    if not row:
        raise ApiError(HTTPStatus.NOT_FOUND, "Article not found")
    return row


def serialize_article(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "community": row["community_slug"], "communityName": row["community_name"], "title": row["title"], "body": row["body"], "status": row["status"], "author": row["author_name"], "publishedAt": row["published_at"], "createdAt": row["created_at"]}


def serialize_material(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    attachments = db.execute(
        "SELECT id, file_name, mime_type, byte_size, created_at FROM work_material_attachments WHERE material_id = ? ORDER BY created_at DESC",
        (row["id"],),
    ).fetchall()
    runs = db.execute(
        "SELECT id, provider, status, result_json, error_message, started_at, completed_at FROM work_material_generation_runs WHERE material_id = ? ORDER BY id DESC",
        (row["id"],),
    ).fetchall()
    logs = db.execute(
        "SELECT work_material_status_logs.*, users.display_name AS actor_name FROM work_material_status_logs JOIN users ON users.id = work_material_status_logs.actor_id WHERE material_id = ? ORDER BY work_material_status_logs.id",
        (row["id"],),
    ).fetchall()
    analysis = json.loads(row["analysis_json"] or "{}")
    return {
        "id": row["id"], "community": row["community_slug"], "communityName": row["community_name"],
        "uploader": row["uploader_name"], "weeklySummary": row["weekly_summary"],
        "incompleteRepairReasons": row["incomplete_repair_reasons"], "nextWeekPlan": row["next_week_plan"],
        "status": row["status"], "articleId": row["article_id"], "analysis": analysis,
        "generatedAt": row["generated_at"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        "attachments": [{"id": item["id"], "fileName": item["file_name"], "mimeType": item["mime_type"], "byteSize": item["byte_size"], "createdAt": item["created_at"], "url": f"/api/work-materials/{row['id']}/attachments/{item['id']}"} for item in attachments],
        "generationRuns": [{"id": run["id"], "provider": run["provider"], "status": run["status"], "result": json.loads(run["result_json"] or "{}"), "error": run["error_message"], "startedAt": run["started_at"], "completedAt": run["completed_at"]} for run in runs],
        "statusLogs": [{"id": log["id"], "actor": log["actor_name"], "fromStatus": log["from_status"], "toStatus": log["to_status"], "note": log["note"], "createdAt": log["created_at"]} for log in logs],
    }


FEEDBACK_STATUS_LABELS = {"open": "待处理", "processing": "处理中", "resolved": "已解决", "closed": "已关闭"}


def serialize_feedback(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    messages = db.execute("SELECT feedback_messages.id, feedback_messages.body, feedback_messages.created_at, users.display_name AS author_name, users.role AS author_role FROM feedback_messages JOIN users ON users.id = feedback_messages.author_id WHERE feedback_messages.feedback_id = ? ORDER BY feedback_messages.id", (row["id"],)).fetchall()
    return {"id": row["id"], "type": row["type"], "subject": row["subject"], "body": row["body"], "status": row["status"], "statusLabel": FEEDBACK_STATUS_LABELS.get(row["status"], row["status"]), "createdAt": row["created_at"], "updatedAt": row["updated_at"], "messages": [{"id": item["id"], "body": item["body"], "author": item["author_name"], "role": item["author_role"], "createdAt": item["created_at"]} for item in messages]}


def serialize_announcement(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "title": row["title"], "body": row["body"], "status": row["status"], "imageUrl": row["image_url"], "linkUrl": row["link_url"], "linkType": row["link_type"], "sortOrder": row["sort_order"], "publishedAt": row["published_at"]}


def serialize_review(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    attachments = db.execute("SELECT id, file_name, mime_type, byte_size, created_at FROM review_attachments WHERE review_id = ? ORDER BY created_at DESC", (row["id"],)).fetchall()
    return {
        "id": row["id"], "ticketId": row["ticket_id"], "score": row["score"], "body": row["body"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        "attachments": [{"id": item["id"], "fileName": item["file_name"], "mimeType": item["mime_type"], "byteSize": item["byte_size"], "createdAt": item["created_at"], "url": f"/api/reviews/{row['id']}/attachments/{item['id']}"} for item in attachments],
    }


def serialize_points(db: sqlite3.Connection, user: sqlite3.Row, community: sqlite3.Row) -> dict:
    account = db.execute("SELECT * FROM resident_points_accounts WHERE resident_id = ?", (user["id"],)).fetchone()
    if not account:
        timestamp = now()
        db.execute("INSERT INTO resident_points_accounts(resident_id, balance, lifetime_earned, updated_at) VALUES (?, 0, 0, ?)", (user["id"], timestamp))
        account = db.execute("SELECT * FROM resident_points_accounts WHERE resident_id = ?", (user["id"],)).fetchone()
    ledger = db.execute("SELECT amount, balance_after, action, note, created_at FROM resident_point_ledger WHERE resident_id = ? ORDER BY id DESC LIMIT 8", (user["id"],)).fetchall()
    rewards = db.execute("SELECT * FROM point_rewards WHERE community_id = ? AND status = 'available' ORDER BY points_cost, id", (community["id"],)).fetchall()
    return {
        "balance": account["balance"], "lifetimeEarned": account["lifetime_earned"],
        "ledger": [{"amount": item["amount"], "balanceAfter": item["balance_after"], "action": item["action"], "note": item["note"], "createdAt": item["created_at"]} for item in ledger],
        "rewards": [{"id": item["id"], "name": item["name"], "description": item["description"], "pointsCost": item["points_cost"], "stock": item["stock"], "canRedeem": account["balance"] >= item["points_cost"] and item["stock"] > 0} for item in rewards],
    }


def optional_public_url(value: object, field_name: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    if candidate.startswith("/") or parsed.scheme in {"http", "https"}:
        return candidate[:500]
    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, f"{field_name} must be an http(s) or site-relative URL")


def save_attachments(db: sqlite3.Connection, ticket_id: int, user: sqlite3.Row, attachments: object, stage: str = "resident_report") -> None:
    if attachments in (None, ""):
        return
    if not isinstance(attachments, list) or len(attachments) > 3:
        raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "At most three attachments can be uploaded")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    for item in attachments:
        if not isinstance(item, dict):
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Invalid attachment")
        mime_type = str(item.get("mimeType") or "").lower()
        file_name = Path(str(item.get("fileName") or "attachment")).name[:120]
        encoded = str(item.get("data") or "")
        if mime_type not in ALLOWED_ATTACHMENT_TYPES or not encoded:
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Unsupported attachment type")
        if encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[-1]
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Invalid attachment data") from error
        if not content or len(content) > MAX_ATTACHMENT_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Each attachment must be 2 MB or smaller")
        storage_key = f"{secrets.token_urlsafe(18)}-{file_name}"
        (UPLOADS_DIR / storage_key).write_bytes(content)
        created_at = now()
        cursor = db.execute("INSERT INTO repair_attachments(ticket_id, uploader_id, storage_key, file_name, mime_type, byte_size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (ticket_id, user["id"], storage_key, file_name, mime_type, len(content), created_at))
        db.execute("INSERT OR REPLACE INTO repair_attachment_stages(attachment_id, stage, recorded_at) VALUES (?, ?, ?)", (cursor.lastrowid, stage, created_at))


def save_review_attachments(db: sqlite3.Connection, review_id: int, user: sqlite3.Row, attachments: object) -> None:
    if attachments in (None, ""):
        return
    if not isinstance(attachments, list) or len(attachments) > 3:
        raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "At most three review attachments can be uploaded")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    for item in attachments:
        if not isinstance(item, dict):
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Invalid review attachment")
        mime_type = str(item.get("mimeType") or "").lower()
        file_name = Path(str(item.get("fileName") or "review-attachment")).name[:120]
        encoded = str(item.get("data") or "")
        if mime_type not in ALLOWED_ATTACHMENT_TYPES or not encoded:
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Unsupported review attachment type")
        if encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[-1]
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Invalid review attachment data") from error
        if not content or len(content) > MAX_ATTACHMENT_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Each review attachment must be 2 MB or smaller")
        storage_key = f"review-{secrets.token_urlsafe(18)}-{file_name}"
        (UPLOADS_DIR / storage_key).write_bytes(content)
        db.execute("INSERT INTO review_attachments(review_id, uploader_id, storage_key, file_name, mime_type, byte_size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (review_id, user["id"], storage_key, file_name, mime_type, len(content), now()))


def add_event(db: sqlite3.Connection, ticket_id: int, user: sqlite3.Row, ticket_status: str, note: str) -> None:
    db.execute("INSERT INTO repair_events(ticket_id, actor_name, actor_role, status, note, created_at) VALUES (?, ?, ?, ?, ?, ?)", (ticket_id, user["display_name"], user["role"], ticket_status, note, now()))


def is_repair_worker(db: sqlite3.Connection, user: sqlite3.Row) -> sqlite3.Row | None:
    return db.execute("SELECT repair_workers.*, communities.slug AS community_slug, communities.name AS community_name FROM repair_workers JOIN communities ON communities.id = repair_workers.community_id WHERE repair_workers.user_id = ? AND repair_workers.active = 1", (user["id"],)).fetchone()


def require_worker(db: sqlite3.Connection, user: sqlite3.Row) -> sqlite3.Row:
    worker = is_repair_worker(db, user)
    if not worker:
        raise ApiError(HTTPStatus.FORBIDDEN, "A worker identity is required for this action")
    return worker


def material_row(db: sqlite3.Connection, material_id: int) -> sqlite3.Row:
    row = db.execute(
        "SELECT work_material_submissions.*, communities.slug AS community_slug, communities.name AS community_name, users.display_name AS uploader_name FROM work_material_submissions JOIN communities ON communities.id = work_material_submissions.community_id JOIN users ON users.id = work_material_submissions.uploader_id WHERE work_material_submissions.id = ?",
        (material_id,),
    ).fetchone()
    if not row:
        raise ApiError(HTTPStatus.NOT_FOUND, "Work-material submission not found")
    return row


def save_material_attachments(db: sqlite3.Connection, material_id: int, user: sqlite3.Row, attachments: object) -> None:
    if attachments in (None, ""):
        return
    if not isinstance(attachments, list) or len(attachments) > 6:
        raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "At most six work-material attachments can be uploaded")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    for item in attachments:
        if not isinstance(item, dict):
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Invalid work-material attachment")
        mime_type = str(item.get("mimeType") or "").lower()
        file_name = Path(str(item.get("fileName") or "material-attachment")).name[:120]
        encoded = str(item.get("data") or "")
        if mime_type not in ALLOWED_ATTACHMENT_TYPES or not encoded:
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Unsupported work-material attachment type")
        if encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[-1]
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Invalid work-material attachment data") from error
        if not content or len(content) > MAX_ATTACHMENT_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Each work-material attachment must be 2 MB or smaller")
        created_at = now()
        storage_key = f"material-{secrets.token_urlsafe(18)}-{file_name}"
        (UPLOADS_DIR / storage_key).write_bytes(content)
        db.execute("INSERT INTO work_material_attachments(material_id, uploader_id, storage_key, file_name, mime_type, byte_size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (material_id, user["id"], storage_key, file_name, mime_type, len(content), created_at))


def local_material_generation(material: sqlite3.Row, attachments: list[sqlite3.Row]) -> dict:
    """Replaceable local generation boundary used when no external AI key is configured."""
    summary = material["weekly_summary"].strip()
    incomplete = material["incomplete_repair_reasons"].strip()
    plan = material["next_week_plan"].strip()
    media_classification = [
        {
            "fileName": attachment["file_name"], "mimeType": attachment["mime_type"],
            "kind": "video" if attachment["mime_type"].startswith("video/") else "image",
            "category": "物业服务现场素材",
            "recommendation": "保留为处理过程素材，优先用于文章配图。" if attachment["mime_type"].startswith("image/") else "截取 15-60 秒关键片段，用作公众号短视频素材。",
        }
        for attachment in attachments
    ]
    return {
        "provider": "local-rule-based-v1",
        "analysis": {
            "focus": ["工程维修", "居民服务", "社区运营"],
            "summaryLength": len(summary), "incompleteReasonLength": len(incomplete), "planLength": len(plan),
            "attachmentCount": len(attachments), "mediaClassification": media_classification,
            "attachmentRecommendations": ["优先选择清晰的现场前后对比图", "视频建议截取 15-60 秒关键片段", "公众号封面使用横向、光线充足的图片"],
            "contentTags": ["物业周记", "本周服务", "下周计划"],
        },
        "title": "这一周，我们把居民关心的每件小事放在心上",
        "body": f"本周重点工作：{summary}\n\n报修跟进：{incomplete}\n\n下周计划：{plan}\n\n本次共整理 {len(attachments)} 个公众号图片或视频素材，已完成素材归类，待平台管理员审核后向居民发布。",
    }


def add_material_status(db: sqlite3.Connection, material_id: int, actor: sqlite3.Row, from_status: str | None, to_status: str, note: str) -> None:
    db.execute("INSERT INTO work_material_status_logs(material_id, actor_id, from_status, to_status, note, created_at) VALUES (?, ?, ?, ?, ?, ?)", (material_id, actor["id"], from_status, to_status, note[:1000], now()))


def add_audit(db: sqlite3.Connection, user: sqlite3.Row, action: str, resource_type: str, resource_id: int | str, community_id: int | None, details: str) -> None:
    db.execute("INSERT INTO audit_logs(actor_id, community_id, action, resource_type, resource_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (user["id"], community_id, action, resource_type, str(resource_id), details, now()))


def award_points(db: sqlite3.Connection, resident_id: int, amount: int, action: str, resource_type: str, resource_id: int | str, note: str) -> bool:
    event_key = f"{action}:{resource_type}:{resource_id}"
    if db.execute("SELECT 1 FROM resident_point_ledger WHERE event_key = ?", (event_key,)).fetchone():
        return False
    timestamp = now()
    db.execute("INSERT OR IGNORE INTO resident_points_accounts(resident_id, balance, lifetime_earned, updated_at) VALUES (?, 0, 0, ?)", (resident_id, timestamp))
    account = db.execute("SELECT * FROM resident_points_accounts WHERE resident_id = ?", (resident_id,)).fetchone()
    next_balance = account["balance"] + amount
    if next_balance < 0:
        raise ApiError(HTTPStatus.CONFLICT, "Not enough community points")
    lifetime_earned = account["lifetime_earned"] + max(amount, 0)
    db.execute("UPDATE resident_points_accounts SET balance = ?, lifetime_earned = ?, updated_at = ? WHERE resident_id = ?", (next_balance, lifetime_earned, timestamp, resident_id))
    db.execute("INSERT INTO resident_point_ledger(resident_id, amount, balance_after, action, resource_type, resource_id, note, event_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (resident_id, amount, next_balance, action, resource_type, str(resource_id), note[:240], event_key, timestamp))
    return True


def attachment_for(db: sqlite3.Connection, user: sqlite3.Row, ticket_id: int, attachment_id: int) -> sqlite3.Row:
    attachment = db.execute("SELECT repair_attachments.*, repair_tickets.resident_id, repair_tickets.community_id, repair_tickets.worker_id, communities.slug AS community_slug FROM repair_attachments JOIN repair_tickets ON repair_tickets.id = repair_attachments.ticket_id JOIN communities ON communities.id = repair_tickets.community_id WHERE repair_attachments.id = ? AND repair_attachments.ticket_id = ?", (attachment_id, ticket_id)).fetchone()
    if not attachment:
        raise ApiError(HTTPStatus.NOT_FOUND, "Attachment not found")
    worker = is_repair_worker(db, user)
    if worker:
        if attachment["worker_id"] != worker["id"]:
            raise ApiError(HTTPStatus.NOT_FOUND, "Attachment not found")
    elif user["role"] == "resident":
        if attachment["resident_id"] != user["id"] or attachment["community_id"] != user["community_id"]:
            raise ApiError(HTTPStatus.NOT_FOUND, "Attachment not found")
    else:
        community_for(db, user, attachment["community_slug"])
    return attachment


def review_attachment_for(db: sqlite3.Connection, user: sqlite3.Row, review_id: int, attachment_id: int) -> sqlite3.Row:
    attachment = db.execute("SELECT review_attachments.*, repair_reviews.resident_id, repair_tickets.community_id, communities.slug AS community_slug FROM review_attachments JOIN repair_reviews ON repair_reviews.id = review_attachments.review_id JOIN repair_tickets ON repair_tickets.id = repair_reviews.ticket_id JOIN communities ON communities.id = repair_tickets.community_id WHERE review_attachments.id = ? AND review_attachments.review_id = ?", (attachment_id, review_id)).fetchone()
    if not attachment:
        raise ApiError(HTTPStatus.NOT_FOUND, "Review attachment not found")
    if is_repair_worker(db, user):
        raise ApiError(HTTPStatus.NOT_FOUND, "Review attachment not found")
    if user["role"] == "resident":
        if attachment["resident_id"] != user["id"] or attachment["community_id"] != user["community_id"]:
            raise ApiError(HTTPStatus.NOT_FOUND, "Review attachment not found")
    else:
        community_for(db, user, attachment["community_slug"])
    return attachment


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "ShengbianAPI/0.1"

    def log_message(self, format: str, *args) -> None:
        print(f"{self.address_string()} - {format % args}", flush=True)

    def json_response(self, payload: dict, code: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_REQUEST_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body is too large")
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid JSON request") from error

    def dispatch(self, method: str) -> tuple[dict, int]:
        request = urlparse(self.path)
        query = parse_qs(request.query)
        path = request.path
        if method == "GET" and path == "/healthz":
            return {"status": "ok"}, HTTPStatus.OK
        with connect() as db:
            user = current_user(db, self.headers)
            # Worker identities are intentionally confined to the worker portal;
            # they cannot impersonate property-console actions with the same
            # demo header.
            if is_repair_worker(db, user) and not (path == "/api/me" or path.startswith("/api/worker")):
                raise ApiError(HTTPStatus.FORBIDDEN, "Use the repair-worker portal for this identity")
            if method == "GET" and path == "/api/me":
                return {"user": serialize_user(db, user), "authentication": "demo-header"}, HTTPStatus.OK
            if method == "GET" and path == "/api/worker/dashboard":
                worker = require_worker(db, user)
                assignment_rows = db.execute("SELECT DISTINCT repair_tickets.id FROM repair_tickets JOIN repair_assignments ON repair_assignments.ticket_id = repair_tickets.id WHERE repair_tickets.worker_id = ? OR repair_assignments.worker_id = ? ORDER BY repair_tickets.updated_at DESC", (worker["id"], worker["id"])).fetchall()
                tickets = [serialize_ticket(db, ticket_row(db, item["id"])) for item in assignment_rows]
                return {"user": serialize_user(db, user), "worker": {"id": worker["id"], "name": worker["display_name"], "specialty": worker["specialty"], "community": worker["community_slug"], "communityName": worker["community_name"]}, "tickets": tickets}, HTTPStatus.OK
            if method == "GET" and path == "/api/dashboard/platform":
                require_role(user, "platform")
                communities = db.execute("SELECT * FROM communities ORDER BY id").fetchall()
                cards = []
                for community in communities:
                    tickets = db.execute("SELECT status FROM repair_tickets WHERE community_id = ?", (community["id"],)).fetchall()
                    cards.append({**serialize_community(db, community), "openTickets": sum(item["status"] != "resolved" for item in tickets), "riskTickets": sum(item["status"] in {"new", "awaiting_vendor", "reopened"} for item in tickets), "materialCount": one_id(db, "SELECT COUNT(*) FROM work_materials WHERE community_id = ?", (community["id"],))})
                pending = db.execute("SELECT articles.id FROM articles WHERE status = 'submitted' ORDER BY updated_at DESC").fetchall()
                pending_announcements = db.execute("SELECT announcements.*, communities.name AS community_name FROM announcements JOIN communities ON communities.id = announcements.community_id WHERE announcements.status = 'submitted' ORDER BY announcements.id DESC").fetchall()
                pending_announcement_payload = [{**serialize_announcement(item), "communityName": item["community_name"]} for item in pending_announcements]
                pending_materials = db.execute("SELECT id FROM work_material_submissions WHERE status = 'submitted_for_review' ORDER BY updated_at DESC").fetchall()
                return {"user": serialize_user(db, user), "metrics": {"companies": one_id(db, "SELECT COUNT(*) FROM property_companies"), "communities": len(communities), "residents": sum(community["resident_count"] for community in communities), "openTickets": one_id(db, "SELECT COUNT(*) FROM repair_tickets WHERE status != 'resolved'"), "riskTickets": one_id(db, "SELECT COUNT(*) FROM repair_tickets WHERE status IN ('new', 'awaiting_vendor', 'reopened')"), "pendingArticles": len(pending), "pendingAnnouncements": len(pending_announcements), "pendingMaterials": len(pending_materials), "materials": one_id(db, "SELECT COUNT(*) FROM work_materials")}, "communities": cards, "pendingArticles": [serialize_article(article_row(db, item["id"])) for item in pending], "pendingAnnouncements": pending_announcement_payload, "pendingMaterials": [serialize_material(db, material_row(db, item["id"])) for item in pending_materials]}, HTTPStatus.OK
            if method == "GET" and path == "/api/dashboard/property":
                require_role(user, "property", "platform")
                community = community_for(db, user, query.get("communityId", [None])[0])
                ticket_ids = db.execute("SELECT id FROM repair_tickets WHERE community_id = ? ORDER BY updated_at DESC", (community["id"],)).fetchall()
                tickets = [serialize_ticket(db, ticket_row(db, row["id"])) for row in ticket_ids]
                materials = db.execute("SELECT * FROM work_materials WHERE community_id = ? ORDER BY id DESC", (community["id"],)).fetchall()
                article_ids = db.execute("SELECT id FROM articles WHERE community_id = ? ORDER BY updated_at DESC", (community["id"],)).fetchall()
                feedback_rows = db.execute("SELECT * FROM feedback_tickets WHERE community_id = ? ORDER BY updated_at DESC LIMIT 20", (community["id"],)).fetchall()
                residents = db.execute("SELECT * FROM users WHERE role = 'resident' AND community_id = ? ORDER BY id", (community["id"],)).fetchall()
                resident_payload = [{**serialize_user(db, resident), "phone": resident["phone"]} for resident in residents]
                material_ids = db.execute("SELECT id FROM work_material_submissions WHERE community_id = ? ORDER BY id DESC LIMIT 20", (community["id"],)).fetchall()
                return {"user": serialize_user(db, user), "community": serialize_community(db, community), "metrics": {"openTickets": sum(ticket["status"] != "resolved" for ticket in tickets), "awaitingVendor": sum(ticket["status"] == "awaiting_vendor" for ticket in tickets), "awaitingConfirmation": sum(ticket["status"] == "awaiting_confirmation" for ticket in tickets), "materials": len(materials), "openFeedback": sum(item["status"] == "open" for item in feedback_rows)}, "tickets": tickets, "residents": resident_payload, "materials": [{"id": item["id"], "title": item["title"], "type": item["material_type"], "category": item["category"], "itemCount": item["item_count"], "status": item["status"]} for item in materials], "materialSubmissions": [serialize_material(db, material_row(db, item["id"])) for item in material_ids], "articles": [serialize_article(article_row(db, row["id"])) for row in article_ids], "feedback": [serialize_feedback(db, item) for item in feedback_rows]}, HTTPStatus.OK
            if method == "GET" and path == "/api/dashboard/resident":
                require_role(user, "resident")
                community = community_for(db, user)
                ticket_ids = db.execute("SELECT id FROM repair_tickets WHERE resident_id = ? AND community_id = ? ORDER BY updated_at DESC", (user["id"], community["id"])).fetchall()
                article_ids = db.execute("SELECT id FROM articles WHERE community_id = ? AND status = 'published' ORDER BY published_at DESC", (community["id"],)).fetchall()
                announcements = db.execute("SELECT * FROM announcements WHERE community_id = ? AND status = 'published' ORDER BY sort_order DESC, published_at DESC", (community["id"],)).fetchall()
                feedback_rows = db.execute("SELECT * FROM feedback_tickets WHERE resident_id = ? ORDER BY updated_at DESC LIMIT 20", (user["id"],)).fetchall()
                carousel = [{**serialize_announcement(item), "contentType": "announcement"} for item in announcements]
                carousel.extend({**serialize_article(article_row(db, row["id"])), "contentType": "article", "imageUrl": None, "linkUrl": f"/shengbian-resident-demo.html?articleId={row['id']}", "linkType": "article"} for row in article_ids)
                carousel.sort(key=lambda item: (item.get("sortOrder", 0), item.get("publishedAt") or ""), reverse=True)
                return {"user": serialize_user(db, user), "community": serialize_community(db, community), "tickets": [serialize_ticket(db, ticket_row(db, row["id"])) for row in ticket_ids], "articles": [serialize_article(article_row(db, row["id"])) for row in article_ids], "announcements": [serialize_announcement(item) for item in announcements], "carousel": carousel, "feedback": [serialize_feedback(db, item) for item in feedback_rows], "points": serialize_points(db, user, community)}, HTTPStatus.OK
            if method == "GET" and path == "/api/points":
                require_role(user, "resident")
                community = community_for(db, user)
                return {"points": serialize_points(db, user, community)}, HTTPStatus.OK
            if method == "GET" and path == "/api/work-materials":
                require_role(user, "property", "platform")
                community = community_for(db, user, query.get("communityId", [None])[0])
                material_ids = db.execute("SELECT id FROM work_material_submissions WHERE community_id = ? ORDER BY id DESC LIMIT 30", (community["id"],)).fetchall()
                return {"materials": [serialize_material(db, material_row(db, item["id"])) for item in material_ids]}, HTTPStatus.OK
            payload = self.read_json()
            if method == "POST" and path == "/api/work-materials":
                require_role(user, "property")
                community = community_for(db, user)
                fields = {key: str(payload.get(key) or "").strip() for key in ("weeklySummary", "incompleteRepairReasons", "nextWeekPlan")}
                if any(len(value) < 5 for value in fields.values()):
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "The three written work-material fields are required")
                attachments = payload.get("attachments")
                if not isinstance(attachments, list) or not attachments:
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "At least one public-account image or video is required")
                timestamp = now()
                cursor = db.execute("INSERT INTO work_material_submissions(community_id, uploader_id, weekly_summary, incomplete_repair_reasons, next_week_plan, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'uploaded', ?, ?)", (community["id"], user["id"], fields["weeklySummary"][:5000], fields["incompleteRepairReasons"][:5000], fields["nextWeekPlan"][:5000], timestamp, timestamp))
                material_id = cursor.lastrowid
                save_material_attachments(db, material_id, user, attachments)
                db.execute("INSERT INTO work_materials(community_id, title, material_type, category, item_count, status, created_at) VALUES (?, ?, 'mixed', '物业周记 / 待审核', ?, 'submitted_for_review', ?)", (community["id"], "本周工作资料提交", len(attachments), timestamp))
                add_material_status(db, material_id, user, None, "uploaded", "物业提交本周四项工作资料和公众号素材。")
                run_started = now()
                run = db.execute("INSERT INTO work_material_generation_runs(material_id, provider, status, started_at) VALUES (?, 'local-rule-based-v1', 'running', ?)", (material_id, run_started))
                material = material_row(db, material_id)
                try:
                    material_attachments = db.execute("SELECT file_name, mime_type FROM work_material_attachments WHERE material_id = ? ORDER BY id", (material_id,)).fetchall()
                    generated = local_material_generation(material, material_attachments)
                    article_cursor = db.execute("INSERT INTO articles(community_id, author_id, title, body, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'draft', ?, ?)", (community["id"], user["id"], generated["title"][:240], generated["body"][:10000], timestamp, timestamp))
                    article_id = article_cursor.lastrowid
                    db.execute("UPDATE articles SET status = 'submitted', updated_at = ? WHERE id = ?", (now(), article_id))
                    completed = now()
                    db.execute("UPDATE work_material_generation_runs SET status = 'completed', result_json = ?, completed_at = ? WHERE id = ?", (json.dumps(generated, ensure_ascii=False), completed, run.lastrowid))
                    db.execute("UPDATE work_material_submissions SET status = 'submitted_for_review', article_id = ?, analysis_json = ?, generated_at = ?, updated_at = ? WHERE id = ?", (article_id, json.dumps(generated["analysis"], ensure_ascii=False), completed, completed, material_id))
                    add_material_status(db, material_id, user, "uploaded", "submitted_for_review", "本地可替换生成服务完成分析并将文章初稿提交平台审核。")
                    add_audit(db, user, "work_material.generated", "work_material_submission", material_id, community["id"], f"生成文章并提交审核，article_id={article_id}")
                    add_audit(db, user, "article.submitted", "article", article_id, community["id"], "由工作资料生成服务提交平台审核")
                except Exception as error:
                    db.execute("UPDATE work_material_generation_runs SET status = 'failed', error_message = ?, completed_at = ? WHERE id = ?", (str(error)[:500], now(), run.lastrowid))
                    db.execute("UPDATE work_material_submissions SET status = 'generation_failed', updated_at = ? WHERE id = ?", (now(), material_id))
                    add_material_status(db, material_id, user, "uploaded", "generation_failed", "文章生成失败，请检查资料后重试。")
                    raise
                return {"material": serialize_material(db, material_row(db, material_id)), "article": serialize_article(article_row(db, article_id))}, HTTPStatus.CREATED

            if method == "POST" and path.startswith("/api/worker/repairs/"):
                worker = require_worker(db, user)
                parts = path.strip("/").split("/")
                ticket_id = int(parts[3])
                ticket = ticket_row(db, ticket_id)
                if ticket["community_id"] != worker["community_id"] or ticket["worker_id"] != worker["id"]:
                    raise ApiError(HTTPStatus.NOT_FOUND, "This ticket is not assigned to you")
                assignment = db.execute("SELECT * FROM repair_assignments WHERE ticket_id = ? AND worker_id = ? ORDER BY id DESC LIMIT 1", (ticket_id, worker["id"])).fetchone()
                if not assignment:
                    raise ApiError(HTTPStatus.CONFLICT, "The property team has not assigned this ticket to you")
                action = parts[4] if len(parts) > 4 else ""
                expected_version = payload.get("expectedVersion")
                if expected_version is not None and int(expected_version) != ticket["version"]:
                    raise ApiError(HTTPStatus.CONFLICT, "Ticket changed elsewhere; reload before trying again")
                timestamp = now()
                if action == "accept":
                    expected_at = str(payload.get("expectedAt") or "").strip()
                    if len(expected_at) < 10:
                        raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Expected visit time is required")
                    db.execute("UPDATE repair_assignments SET accepted_at = ? WHERE id = ?", (timestamp, assignment["id"]))
                    db.execute("UPDATE repair_tickets SET status = CASE WHEN status = 'new' THEN 'processing' ELSE status END, expected_at = ?, version = version + 1, updated_at = ? WHERE id = ?", (expected_at[:80], timestamp, ticket_id))
                    db.execute("INSERT INTO repair_worker_events(ticket_id, worker_id, action, note, created_at) VALUES (?, ?, 'accepted', ?, ?)", (ticket_id, worker["id"], f"{worker['display_name']} 已接单。", timestamp))
                    db.execute("INSERT INTO repair_worker_events(ticket_id, worker_id, action, note, created_at) VALUES (?, ?, 'expected_visit', ?, ?)", (ticket_id, worker["id"], f"预计上门时间：{expected_at[:80]}", timestamp))
                    add_event(db, ticket_id, user, "processing", f"{worker['display_name']} 已接单。")
                    add_event(db, ticket_id, user, "processing", f"{worker['display_name']} 填写预计上门时间：{expected_at[:80]}。")
                    add_audit(db, user, "repair.worker_accepted", "repair_ticket", ticket_id, ticket["community_id"], "维修人员独立端接单并填写预计上门时间")
                elif action in {"problem-media", "arrive", "complete"}:
                    if action == "problem-media":
                        attachments = payload.get("attachments")
                        if not attachments:
                            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Problem media is required")
                        save_attachments(db, ticket_id, user, attachments, "problem")
                        note = str(payload.get("note") or "维修人员上传现场问题图片或视频。").strip()[:1000]
                        db.execute("INSERT INTO repair_worker_events(ticket_id, worker_id, action, note, created_at) VALUES (?, ?, 'problem_media', ?, ?)", (ticket_id, worker["id"], note, timestamp))
                        add_event(db, ticket_id, user, ticket["status"], note)
                        add_audit(db, user, "repair.worker_problem_media", "repair_ticket", ticket_id, ticket["community_id"], "维修人员上传现场问题凭证")
                    elif action == "arrive":
                        note = str(payload.get("note") or "维修人员已到达现场，开始处理。 ").strip()
                        if len(note) < 2:
                            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Arrival note is required")
                        latitude, longitude = str(payload.get("latitude") or "").strip()[:40], str(payload.get("longitude") or "").strip()[:40]
                        db.execute("UPDATE repair_tickets SET status = 'processing', check_in_at = ?, check_in_note = ?, version = version + 1, updated_at = ? WHERE id = ?", (timestamp, note[:1000], timestamp, ticket_id))
                        db.execute("INSERT INTO repair_work_logs(ticket_id, actor_id, worker_name, action, note, latitude, longitude, created_at) VALUES (?, ?, ?, 'check_in', ?, ?, ?, ?)", (ticket_id, user["id"], worker["display_name"], note[:1000], latitude, longitude, timestamp))
                        db.execute("INSERT INTO repair_worker_events(ticket_id, worker_id, action, note, latitude, longitude, created_at) VALUES (?, ?, 'arrived', ?, ?, ?, ?)", (ticket_id, worker["id"], note[:1000], latitude, longitude, timestamp))
                        add_event(db, ticket_id, user, "processing", f"{worker['display_name']} 到场打卡：{note[:800]}")
                        add_audit(db, user, "repair.worker_arrived", "repair_ticket", ticket_id, ticket["community_id"], "维修人员独立端到场打卡")
                    else:
                        note = str(payload.get("note") or "").strip()
                        if len(note) < 5:
                            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Completion note must be at least 5 characters")
                        if not ticket["check_in_at"]:
                            raise ApiError(HTTPStatus.CONFLICT, "Arrive check-in is required before completion")
                        attachments = payload.get("attachments")
                        if not isinstance(attachments, list) or not attachments:
                            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Completion image or video is required")
                        db.execute("UPDATE repair_tickets SET status = 'awaiting_confirmation', completion_note = ?, completed_at = ?, version = version + 1, updated_at = ? WHERE id = ?", (note[:2000], timestamp, timestamp, ticket_id))
                        save_attachments(db, ticket_id, user, attachments, "completion")
                        db.execute("INSERT INTO repair_work_logs(ticket_id, actor_id, worker_name, action, note, created_at) VALUES (?, ?, ?, 'completed', ?, ?)", (ticket_id, user["id"], worker["display_name"], note[:2000], timestamp))
                        db.execute("INSERT INTO repair_worker_events(ticket_id, worker_id, action, note, created_at) VALUES (?, ?, 'completed', ?, ?)", (ticket_id, worker["id"], note[:2000], timestamp))
                        add_event(db, ticket_id, user, "awaiting_confirmation", f"{worker['display_name']} 已提交完工说明和凭证，等待居民确认。")
                        add_audit(db, user, "repair.worker_completed", "repair_ticket", ticket_id, ticket["community_id"], "维修人员独立端提交完工说明和凭证")
                else:
                    raise ApiError(HTTPStatus.NOT_FOUND, "Unknown worker repair action")
                return {"ticket": serialize_ticket(db, ticket_row(db, ticket_id))}, HTTPStatus.OK
            if method == "POST" and path == "/api/repairs":
                require_role(user, "resident")
                community = community_for(db, user)
                category, location, description = (str(payload.get(key, "")).strip() for key in ("category", "location", "description"))
                if not category or len(location) < 2 or len(description) < 3:
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Category, location and description are required")
                timestamp = now()
                cursor = db.execute("INSERT INTO repair_tickets(public_id, community_id, resident_id, category, location, description, contact, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)", (next_ticket_public_id(db), community["id"], user["id"], category[:60], location[:200], description[:2000], str(payload.get("contact") or user["phone"] or "")[:80], timestamp, timestamp))
                save_attachments(db, cursor.lastrowid, user, payload.get("attachments"))
                add_event(db, cursor.lastrowid, user, "new", "居民提交报修，等待物业受理。")
                add_audit(db, user, "repair.created", "repair_ticket", cursor.lastrowid, community["id"], "居民创建报修工单")
                return {"ticket": serialize_ticket(db, ticket_row(db, cursor.lastrowid))}, HTTPStatus.CREATED
            if method == "POST" and path == "/api/repairs/recorded":
                require_role(user, "property")
                community = community_for(db, user)
                try:
                    resident_id = int(payload.get("residentId") or 0)
                except (TypeError, ValueError):
                    resident_id = 0
                resident = db.execute("SELECT * FROM users WHERE id = ? AND role = 'resident' AND community_id = ?", (resident_id, community["id"])).fetchone()
                category, location, description = (str(payload.get(key, "")).strip() for key in ("category", "location", "description"))
                if not resident or not category or len(location) < 2 or len(description) < 3:
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Resident, category, location and description are required")
                timestamp = now()
                cursor = db.execute("INSERT INTO repair_tickets(public_id, community_id, resident_id, category, location, description, contact, source, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'property_console', 'new', ?, ?)", (next_ticket_public_id(db), community["id"], resident["id"], category[:60], location[:200], description[:2000], str(payload.get("contact") or resident["phone"] or "")[:80], timestamp, timestamp))
                add_event(db, cursor.lastrowid, user, "new", f"物业代 {resident['display_name']} 登记报修，等待处理。")
                add_audit(db, user, "repair.recorded", "repair_ticket", cursor.lastrowid, community["id"], "物业控制台代居民登记工单")
                return {"ticket": serialize_ticket(db, ticket_row(db, cursor.lastrowid))}, HTTPStatus.CREATED
            if method == "POST" and path.startswith("/api/repairs/") and path.endswith("/assign"):
                require_role(user, "property")
                ticket_id = int(path.split("/")[3])
                ticket = ticket_row(db, ticket_id)
                community_for(db, user, ticket["community_slug"])
                worker_name = str(payload.get("workerName") or "").strip()
                if len(worker_name) < 2:
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Worker name is required")
                worker_profile = db.execute("SELECT id FROM repair_workers WHERE community_id = ? AND display_name = ? AND active = 1", (ticket["community_id"], worker_name)).fetchone()
                expected_version = payload.get("expectedVersion")
                if expected_version is not None and int(expected_version) != ticket["version"]:
                    raise ApiError(HTTPStatus.CONFLICT, "Ticket changed elsewhere; reload before trying again")
                next_status = "processing" if ticket["status"] == "new" else ticket["status"]
                timestamp = now()
                update_sql = "UPDATE repair_tickets SET status = ?, assignee = ?, worker_id = ?, version = version + 1, updated_at = ? WHERE id = ?"
                update_args = (next_status, worker_name[:80], worker_profile["id"] if worker_profile else None, timestamp, ticket_id)
                if expected_version is not None:
                    update_sql += " AND version = ?"
                    update_args += (int(expected_version),)
                if db.execute(update_sql, update_args).rowcount != 1:
                    raise ApiError(HTTPStatus.CONFLICT, "Ticket changed elsewhere; reload before trying again")
                db.execute("INSERT INTO repair_assignments(ticket_id, worker_id, assignee_name, vendor_name, assigned_by, started_at, created_at) VALUES (?, ?, ?, NULL, ?, ?, ?)", (ticket_id, worker_profile["id"] if worker_profile else None, worker_name[:80], user["id"], timestamp, timestamp))
                db.execute("INSERT INTO repair_work_logs(ticket_id, actor_id, worker_name, action, note, created_at) VALUES (?, ?, ?, 'assigned', ?, ?)", (ticket_id, user["id"], worker_name[:80], f"物业已分派 {worker_name} 上门处理。", timestamp))
                add_event(db, ticket_id, user, next_status, f"物业已分派 {worker_name} 上门处理。")
                add_audit(db, user, "repair.assigned", "repair_ticket", ticket_id, ticket["community_id"], f"分派维修人员: {worker_name}")
                return {"ticket": serialize_ticket(db, ticket_row(db, ticket_id))}, HTTPStatus.OK
            if method == "POST" and path.startswith("/api/repairs/") and path.endswith("/worker-check-in"):
                require_role(user, "property")
                ticket_id = int(path.split("/")[3])
                ticket = ticket_row(db, ticket_id)
                community_for(db, user, ticket["community_slug"])
                if not ticket["assignee"]:
                    raise ApiError(HTTPStatus.CONFLICT, "Assign a repair worker before check-in")
                if ticket["status"] in {"resolved", "awaiting_confirmation"}:
                    raise ApiError(HTTPStatus.CONFLICT, "This ticket cannot be checked in at its current status")
                note = str(payload.get("note") or "维修人员已到达现场，开始处理。").strip()
                if len(note) < 2:
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Check-in note is required")
                expected_version = payload.get("expectedVersion")
                if expected_version is not None and int(expected_version) != ticket["version"]:
                    raise ApiError(HTTPStatus.CONFLICT, "Ticket changed elsewhere; reload before trying again")
                timestamp = now()
                latitude = str(payload.get("latitude") or "").strip()[:40]
                longitude = str(payload.get("longitude") or "").strip()[:40]
                update_sql = "UPDATE repair_tickets SET status = 'processing', check_in_at = ?, check_in_note = ?, version = version + 1, updated_at = ? WHERE id = ?"
                update_args = (timestamp, note[:1000], timestamp, ticket_id)
                if expected_version is not None:
                    update_sql += " AND version = ?"
                    update_args += (int(expected_version),)
                if db.execute(update_sql, update_args).rowcount != 1:
                    raise ApiError(HTTPStatus.CONFLICT, "Ticket changed elsewhere; reload before trying again")
                db.execute("INSERT INTO repair_work_logs(ticket_id, actor_id, worker_name, action, note, latitude, longitude, created_at) VALUES (?, ?, ?, 'check_in', ?, ?, ?, ?)", (ticket_id, user["id"], ticket["assignee"], note[:1000], latitude, longitude, timestamp))
                add_event(db, ticket_id, user, "processing", f"{ticket['assignee']} 已到场打卡：{note[:800]}")
                add_audit(db, user, "repair.worker_checked_in", "repair_ticket", ticket_id, ticket["community_id"], "维修人员到场打卡")
                return {"ticket": serialize_ticket(db, ticket_row(db, ticket_id))}, HTTPStatus.OK
            if method == "POST" and path.startswith("/api/repairs/") and path.endswith("/worker-complete"):
                require_role(user, "property")
                ticket_id = int(path.split("/")[3])
                ticket = ticket_row(db, ticket_id)
                community_for(db, user, ticket["community_slug"])
                if not ticket["assignee"] or not ticket["check_in_at"]:
                    raise ApiError(HTTPStatus.CONFLICT, "Worker check-in is required before completion")
                if ticket["status"] in {"resolved", "awaiting_confirmation"}:
                    raise ApiError(HTTPStatus.CONFLICT, "This ticket is already completed or awaiting confirmation")
                note = str(payload.get("note") or "").strip()
                if len(note) < 5:
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Completion note must be at least 5 characters")
                expected_version = payload.get("expectedVersion")
                if expected_version is not None and int(expected_version) != ticket["version"]:
                    raise ApiError(HTTPStatus.CONFLICT, "Ticket changed elsewhere; reload before trying again")
                timestamp = now()
                update_sql = "UPDATE repair_tickets SET status = 'awaiting_confirmation', completion_note = ?, completed_at = ?, version = version + 1, updated_at = ? WHERE id = ?"
                update_args = (note[:2000], timestamp, timestamp, ticket_id)
                if expected_version is not None:
                    update_sql += " AND version = ?"
                    update_args += (int(expected_version),)
                if db.execute(update_sql, update_args).rowcount != 1:
                    raise ApiError(HTTPStatus.CONFLICT, "Ticket changed elsewhere; reload before trying again")
                save_attachments(db, ticket_id, user, payload.get("attachments"))
                db.execute("INSERT INTO repair_work_logs(ticket_id, actor_id, worker_name, action, note, created_at) VALUES (?, ?, ?, 'completed', ?, ?)", (ticket_id, user["id"], ticket["assignee"], note[:2000], timestamp))
                add_event(db, ticket_id, user, "awaiting_confirmation", f"{ticket['assignee']} 已完成维修：{note[:800]}，已上传现场凭证，等待居民确认。")
                add_audit(db, user, "repair.worker_completed", "repair_ticket", ticket_id, ticket["community_id"], "维修人员提交完工说明和凭证")
                return {"ticket": serialize_ticket(db, ticket_row(db, ticket_id))}, HTTPStatus.OK
            if method == "PATCH" and path.startswith("/api/repairs/") and path.endswith("/transition"):
                require_role(user, "property")
                ticket_id = int(path.split("/")[3])
                ticket = ticket_row(db, ticket_id)
                community_for(db, user, ticket["community_slug"])
                next_status = str(payload.get("status", ""))
                note = str(payload.get("note", "")).strip()
                if next_status not in STATUS_LABELS or len(note) < 2:
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Valid status and note are required")
                if next_status not in ALLOWED_TICKET_TRANSITIONS.get(ticket["status"], set()):
                    raise ApiError(HTTPStatus.CONFLICT, f"Cannot transition ticket from {ticket['status']} to {next_status}")
                expected_version = payload.get("expectedVersion")
                if expected_version is not None and int(expected_version) != ticket["version"]:
                    raise ApiError(HTTPStatus.CONFLICT, "Ticket changed elsewhere; reload before trying again")
                assignee = ticket["assignee"] if "assignee" not in payload else (str(payload.get("assignee") or "").strip() or None)
                update_sql = "UPDATE repair_tickets SET status = ?, assignee = ?, version = version + 1, updated_at = ? WHERE id = ?"
                update_args = (next_status, assignee, now(), ticket_id)
                if expected_version is not None:
                    update_sql += " AND version = ?"
                    update_args += (int(expected_version),)
                cursor = db.execute(update_sql, update_args)
                if cursor.rowcount != 1:
                    raise ApiError(HTTPStatus.CONFLICT, "Ticket changed elsewhere; reload before trying again")
                add_event(db, ticket_id, user, next_status, note[:1000])
                add_audit(db, user, "repair.transitioned", "repair_ticket", ticket_id, ticket["community_id"], f"工单状态变更为 {next_status}")
                return {"ticket": serialize_ticket(db, ticket_row(db, ticket_id))}, HTTPStatus.OK
            if method == "POST" and path.startswith("/api/repairs/") and path.endswith("/remind"):
                require_role(user, "property")
                ticket_id = int(path.split("/")[3])
                ticket = ticket_row(db, ticket_id)
                community_for(db, user, ticket["community_slug"])
                if ticket["status"] in {"resolved"}:
                    raise ApiError(HTTPStatus.CONFLICT, "Resolved tickets cannot be reminded")
                expected_version = payload.get("expectedVersion")
                if expected_version is not None and int(expected_version) != ticket["version"]:
                    raise ApiError(HTTPStatus.CONFLICT, "Ticket changed elsewhere; reload before trying again")
                note = str(payload.get("note") or "物业已向责任人发送催办，并记录本次跟进。").strip()[:1000]
                update_sql = "UPDATE repair_tickets SET version = version + 1, updated_at = ? WHERE id = ?"
                update_args = (now(), ticket_id)
                if expected_version is not None:
                    update_sql += " AND version = ?"
                    update_args += (int(expected_version),)
                if db.execute(update_sql, update_args).rowcount != 1:
                    raise ApiError(HTTPStatus.CONFLICT, "Ticket changed elsewhere; reload before trying again")
                add_event(db, ticket_id, user, ticket["status"], note)
                add_audit(db, user, "repair.reminded", "repair_ticket", ticket_id, ticket["community_id"], "物业记录工单催办")
                return {"ticket": serialize_ticket(db, ticket_row(db, ticket_id))}, HTTPStatus.OK
            if method == "POST" and path.startswith("/api/repairs/") and path.endswith("/resident-confirmation"):
                require_role(user, "resident")
                ticket_id = int(path.split("/")[3])
                ticket = ticket_row(db, ticket_id)
                if ticket["resident_id"] != user["id"] or ticket["community_id"] != user["community_id"]:
                    raise ApiError(HTTPStatus.NOT_FOUND, "Ticket not found")
                if ticket["status"] != "awaiting_confirmation":
                    raise ApiError(HTTPStatus.CONFLICT, "Only tickets awaiting resident confirmation can be confirmed or reopened")
                expected_version = payload.get("expectedVersion")
                if expected_version is not None and int(expected_version) != ticket["version"]:
                    raise ApiError(HTTPStatus.CONFLICT, "Ticket changed elsewhere; reload before trying again")
                resolved = bool(payload.get("resolved"))
                next_status = "resolved" if resolved else "reopened"
                note = str(payload.get("note") or ("居民确认问题已解决。" if resolved else "居民反馈问题尚未解决，已重新打开。"))
                update_sql = "UPDATE repair_tickets SET status = ?, version = version + 1, updated_at = ? WHERE id = ?"
                update_args = (next_status, now(), ticket_id)
                if expected_version is not None:
                    update_sql += " AND version = ?"
                    update_args += (int(expected_version),)
                cursor = db.execute(update_sql, update_args)
                if cursor.rowcount != 1:
                    raise ApiError(HTTPStatus.CONFLICT, "Ticket changed elsewhere; reload before trying again")
                add_event(db, ticket_id, user, next_status, note[:1000])
                add_audit(db, user, "repair.confirmed", "repair_ticket", ticket_id, ticket["community_id"], f"居民确认结果: {next_status}")
                return {"ticket": serialize_ticket(db, ticket_row(db, ticket_id))}, HTTPStatus.OK
            if method == "POST" and path.startswith("/api/repairs/") and path.endswith("/review"):
                require_role(user, "resident")
                ticket_id = int(path.split("/")[3])
                ticket = ticket_row(db, ticket_id)
                if ticket["resident_id"] != user["id"] or ticket["community_id"] != user["community_id"]:
                    raise ApiError(HTTPStatus.NOT_FOUND, "Ticket not found")
                if ticket["status"] != "resolved":
                    raise ApiError(HTTPStatus.CONFLICT, "Only resolved tickets can be reviewed")
                if db.execute("SELECT 1 FROM repair_reviews WHERE ticket_id = ?", (ticket_id,)).fetchone():
                    raise ApiError(HTTPStatus.CONFLICT, "This ticket already has a review")
                try:
                    score = int(payload.get("score") or 0)
                except (TypeError, ValueError):
                    score = 0
                body = str(payload.get("body") or "").strip()
                if score not in range(1, 6):
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Score must be between 1 and 5")
                if len(body) > 2000:
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Review body is too long")
                timestamp = now()
                cursor = db.execute("INSERT INTO repair_reviews(ticket_id, resident_id, score, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)", (ticket_id, user["id"], score, body, timestamp, timestamp))
                save_review_attachments(db, cursor.lastrowid, user, payload.get("attachments"))
                award_points(db, user["id"], 20, "review", "repair_ticket", ticket_id, "完成一次报修评价，获得社区积分")
                add_event(db, ticket_id, user, "resolved", f"居民提交 {score} 星评价：{body[:800] or '未填写文字评价'}")
                add_audit(db, user, "repair.reviewed", "repair_ticket", ticket_id, ticket["community_id"], f"居民提交 {score} 星评价")
                review = db.execute("SELECT * FROM repair_reviews WHERE id = ?", (cursor.lastrowid,)).fetchone()
                return {"review": serialize_review(db, review), "ticket": serialize_ticket(db, ticket_row(db, ticket_id))}, HTTPStatus.CREATED
            if method == "POST" and path == "/api/points/redeem":
                require_role(user, "resident")
                community = community_for(db, user)
                try:
                    reward_id = int(payload.get("rewardId") or 0)
                except (TypeError, ValueError):
                    reward_id = 0
                reward = db.execute("SELECT * FROM point_rewards WHERE id = ? AND community_id = ? AND status = 'available'", (reward_id, community["id"])).fetchone()
                if not reward:
                    raise ApiError(HTTPStatus.NOT_FOUND, "Reward not found")
                if reward["stock"] < 1:
                    raise ApiError(HTTPStatus.CONFLICT, "This reward is out of stock")
                account = db.execute("SELECT * FROM resident_points_accounts WHERE resident_id = ?", (user["id"],)).fetchone()
                if not account or account["balance"] < reward["points_cost"]:
                    raise ApiError(HTTPStatus.CONFLICT, "Not enough community points")
                timestamp = now()
                cursor = db.execute("INSERT INTO point_redemptions(resident_id, reward_id, points_cost, status, created_at) VALUES (?, ?, ?, 'pending', ?)", (user["id"], reward_id, reward["points_cost"], timestamp))
                if db.execute("UPDATE point_rewards SET stock = stock - 1 WHERE id = ? AND stock > 0", (reward_id,)).rowcount != 1:
                    raise ApiError(HTTPStatus.CONFLICT, "This reward is out of stock")
                award_points(db, user["id"], -reward["points_cost"], "redeem", "point_redemption", cursor.lastrowid, f"兑换：{reward['name']}")
                add_audit(db, user, "points.redeemed", "point_reward", reward_id, community["id"], f"居民兑换积分礼品: {reward['name']}")
                return {"redemption": {"id": cursor.lastrowid, "rewardName": reward["name"], "pointsCost": reward["points_cost"], "status": "pending", "createdAt": timestamp}, "points": serialize_points(db, user, community)}, HTTPStatus.CREATED
            if method == "POST" and path == "/api/feedback":
                require_role(user, "resident")
                community = community_for(db, user)
                feedback_type = str(payload.get("type") or "suggestion").strip()
                subject, body = str(payload.get("subject") or "").strip(), str(payload.get("body") or "").strip()
                if feedback_type not in {"complaint", "suggestion", "praise"} or len(subject) < 2 or len(body) < 5:
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Feedback type, subject and body are required")
                timestamp = now()
                cursor = db.execute("INSERT INTO feedback_tickets(community_id, resident_id, type, subject, body, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)", (community["id"], user["id"], feedback_type, subject[:240], body[:5000], timestamp, timestamp))
                db.execute("INSERT INTO feedback_messages(feedback_id, author_id, body, created_at) VALUES (?, ?, ?, ?)", (cursor.lastrowid, user["id"], body[:5000], timestamp))
                add_audit(db, user, "feedback.created", "feedback_ticket", cursor.lastrowid, community["id"], "居民提交投诉建议")
                feedback = db.execute("SELECT * FROM feedback_tickets WHERE id = ?", (cursor.lastrowid,)).fetchone()
                return {"feedback": serialize_feedback(db, feedback)}, HTTPStatus.CREATED
            if method == "POST" and path.startswith("/api/feedback/") and path.endswith("/reply"):
                require_role(user, "property")
                feedback_id = int(path.split("/")[3])
                feedback = db.execute("SELECT feedback_tickets.*, communities.slug AS community_slug FROM feedback_tickets JOIN communities ON communities.id = feedback_tickets.community_id WHERE feedback_tickets.id = ?", (feedback_id,)).fetchone()
                if not feedback:
                    raise ApiError(HTTPStatus.NOT_FOUND, "Feedback not found")
                community_for(db, user, feedback["community_slug"])
                body = str(payload.get("body") or "").strip()
                next_status = str(payload.get("status") or "processing").strip()
                if len(body) < 2 or next_status not in FEEDBACK_STATUS_LABELS:
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Reply body and valid status are required")
                timestamp = now()
                db.execute("INSERT INTO feedback_messages(feedback_id, author_id, body, created_at) VALUES (?, ?, ?, ?)", (feedback_id, user["id"], body[:5000], timestamp))
                db.execute("UPDATE feedback_tickets SET status = ?, updated_at = ? WHERE id = ?", (next_status, timestamp, feedback_id))
                add_audit(db, user, "feedback.replied", "feedback_ticket", feedback_id, feedback["community_id"], f"物业回复居民反馈并更新为 {next_status}")
                return {"feedback": serialize_feedback(db, db.execute("SELECT * FROM feedback_tickets WHERE id = ?", (feedback_id,)).fetchone())}, HTTPStatus.OK
            if method == "POST" and path == "/api/articles":
                require_role(user, "property")
                community = community_for(db, user)
                title, body = str(payload.get("title", "")).strip(), str(payload.get("body", "")).strip()
                if len(title) < 3 or len(body) < 10:
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Title and body are required")
                timestamp = now()
                cursor = db.execute("INSERT INTO articles(community_id, author_id, title, body, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'draft', ?, ?)", (community["id"], user["id"], title[:240], body[:10000], timestamp, timestamp))
                add_audit(db, user, "article.created", "article", cursor.lastrowid, community["id"], "物业创建周记草稿")
                return {"article": serialize_article(article_row(db, cursor.lastrowid))}, HTTPStatus.CREATED
            if method == "POST" and path.startswith("/api/articles/") and path.endswith("/submit"):
                require_role(user, "property")
                article_id = int(path.split("/")[3])
                article = article_row(db, article_id)
                community_for(db, user, article["community_slug"])
                if article["status"] != "draft":
                    raise ApiError(HTTPStatus.CONFLICT, "Only draft articles can be submitted")
                db.execute("UPDATE articles SET status = 'submitted', updated_at = ? WHERE id = ?", (now(), article_id))
                add_audit(db, user, "article.submitted", "article", article_id, article["community_id"], "物业提交周记审核")
                return {"article": serialize_article(article_row(db, article_id))}, HTTPStatus.OK
            if method == "POST" and path.startswith("/api/articles/") and path.endswith("/approve"):
                require_role(user, "platform")
                article_id = int(path.split("/")[3])
                article = article_row(db, article_id)
                if article["status"] != "submitted":
                    raise ApiError(HTTPStatus.CONFLICT, "Only submitted articles can be approved")
                timestamp = now()
                db.execute("UPDATE articles SET status = 'published', published_at = ?, updated_at = ? WHERE id = ?", (timestamp, timestamp, article_id))
                db.execute("INSERT INTO article_reviews(article_id, reviewer_id, decision, note, created_at) VALUES (?, ?, 'approved', ?, ?)", (article_id, user["id"], "平台审核通过并发布。", timestamp))
                add_audit(db, user, "article.published", "article", article_id, article["community_id"], "平台审核通过并发布周记")
                return {"article": serialize_article(article_row(db, article_id))}, HTTPStatus.OK
            if method == "POST" and path == "/api/announcements":
                require_role(user, "property")
                community = community_for(db, user)
                title, body = str(payload.get("title") or "").strip(), str(payload.get("body") or "").strip()
                if len(title) < 3 or len(body) < 5:
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "Announcement title and body are required")
                timestamp = now()
                image_url = optional_public_url(payload.get("imageUrl"), "imageUrl")
                link_url = optional_public_url(payload.get("linkUrl"), "linkUrl")
                try:
                    sort_order = max(0, min(999, int(payload.get("sortOrder") or 0)))
                except (TypeError, ValueError):
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "sortOrder must be a number")
                cursor = db.execute("INSERT INTO announcements(community_id, title, body, status, published_at, image_url, link_url, link_type, sort_order) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?)", (community["id"], title[:240], body[:5000], timestamp, image_url, link_url, str(payload.get("linkType") or "announcement")[:40], sort_order))
                add_audit(db, user, "announcement.created", "announcement", cursor.lastrowid, community["id"], "物业创建公告草稿")
                return {"announcement": serialize_announcement(db.execute("SELECT * FROM announcements WHERE id = ?", (cursor.lastrowid,)).fetchone())}, HTTPStatus.CREATED
            if method == "POST" and path.startswith("/api/announcements/") and path.endswith("/submit"):
                require_role(user, "property")
                announcement_id = int(path.split("/")[3])
                announcement = db.execute("SELECT * FROM announcements WHERE id = ?", (announcement_id,)).fetchone()
                if not announcement:
                    raise ApiError(HTTPStatus.NOT_FOUND, "Announcement not found")
                community_for(db, user, db.execute("SELECT * FROM communities WHERE id = ?", (announcement["community_id"],)).fetchone()["slug"])
                if announcement["status"] != "draft":
                    raise ApiError(HTTPStatus.CONFLICT, "Only draft announcements can be submitted")
                db.execute("UPDATE announcements SET status = 'submitted' WHERE id = ?", (announcement_id,))
                add_audit(db, user, "announcement.submitted", "announcement", announcement_id, announcement["community_id"], "物业提交公告审核")
                return {"announcement": serialize_announcement(db.execute("SELECT * FROM announcements WHERE id = ?", (announcement_id,)).fetchone())}, HTTPStatus.OK
            if method == "POST" and path.startswith("/api/announcements/") and path.endswith("/publish"):
                require_role(user, "platform")
                announcement_id = int(path.split("/")[3])
                announcement = db.execute("SELECT * FROM announcements WHERE id = ?", (announcement_id,)).fetchone()
                if not announcement or announcement["status"] != "submitted":
                    raise ApiError(HTTPStatus.CONFLICT, "Only submitted announcements can be published")
                timestamp = now()
                db.execute("UPDATE announcements SET status = 'published', published_at = ? WHERE id = ?", (timestamp, announcement_id))
                add_audit(db, user, "announcement.published", "announcement", announcement_id, announcement["community_id"], "平台审核通过并发布公告")
                return {"announcement": serialize_announcement(db.execute("SELECT * FROM announcements WHERE id = ?", (announcement_id,)).fetchone())}, HTTPStatus.OK
        raise ApiError(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def handle_request(self, method: str) -> None:
        try:
            payload, code = self.dispatch(method)
            self.json_response(payload, code)
        except ApiError as error:
            self.json_response({"detail": error.detail}, error.code)
        except (ValueError, IndexError):
            self.json_response({"detail": "Malformed request path"}, HTTPStatus.BAD_REQUEST)
        except Exception as error:  # Keep internal details out of the browser response.
            print(f"Unhandled API error: {error}", flush=True)
            self.json_response({"detail": "Internal server error"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_GET(self) -> None:
        request = urlparse(self.path)
        parts = request.path.strip("/").split("/")
        if len(parts) == 5 and parts[:2] == ["api", "work-materials"] and parts[3] == "attachments":
            try:
                material_id, attachment_id = int(parts[2]), int(parts[4])
            except ValueError:
                self.handle_request("GET")
                return
            try:
                with connect() as db:
                    user = current_user(db, self.headers)
                    material = material_row(db, material_id)
                    if is_repair_worker(db, user):
                        raise ApiError(HTTPStatus.NOT_FOUND, "Material attachment not found")
                    if user["role"] == "resident" or (user["role"] != "platform" and user["community_id"] != material["community_id"]):
                        raise ApiError(HTTPStatus.NOT_FOUND, "Material attachment not found")
                    attachment = db.execute("SELECT * FROM work_material_attachments WHERE id = ? AND material_id = ?", (attachment_id, material_id)).fetchone()
                    if not attachment:
                        raise ApiError(HTTPStatus.NOT_FOUND, "Material attachment not found")
                    file_path = UPLOADS_DIR / Path(attachment["storage_key"]).name
                    if not file_path.is_file():
                        raise ApiError(HTTPStatus.NOT_FOUND, "Material attachment file not found")
                    body = file_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", attachment["mime_type"])
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{quote(attachment['file_name'])}")
                self.send_header("Cache-Control", "private, no-store")
                self.end_headers()
                self.wfile.write(body)
            except ApiError as error:
                self.json_response({"detail": error.detail}, error.code)
            except Exception as error:
                print(f"Unhandled work material attachment error: {error}", flush=True)
                self.json_response({"detail": "Internal server error"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if len(parts) == 5 and parts[:2] == ["api", "reviews"] and parts[3] == "attachments":
            try:
                review_id, attachment_id = int(parts[2]), int(parts[4])
            except ValueError:
                self.handle_request("GET")
                return
            try:
                with connect() as db:
                    user = current_user(db, self.headers)
                    attachment = review_attachment_for(db, user, review_id, attachment_id)
                    storage_key = Path(attachment["storage_key"]).name
                    file_path = UPLOADS_DIR / storage_key
                    if not file_path.is_file():
                        raise ApiError(HTTPStatus.NOT_FOUND, "Review attachment file not found")
                    body = file_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", attachment["mime_type"])
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{quote(attachment['file_name'])}")
                self.send_header("Cache-Control", "private, no-store")
                self.end_headers()
                self.wfile.write(body)
            except ApiError as error:
                self.json_response({"detail": error.detail}, error.code)
            except Exception as error:
                print(f"Unhandled review attachment error: {error}", flush=True)
                self.json_response({"detail": "Internal server error"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if len(parts) == 5 and parts[:2] == ["api", "repairs"] and parts[3] == "attachments":
            try:
                ticket_id, attachment_id = int(parts[2]), int(parts[4])
            except ValueError:
                self.handle_request("GET")
                return
            try:
                with connect() as db:
                    user = current_user(db, self.headers)
                    attachment = attachment_for(db, user, ticket_id, attachment_id)
                    storage_key = Path(attachment["storage_key"]).name
                    file_path = UPLOADS_DIR / storage_key
                    if not file_path.is_file():
                        raise ApiError(HTTPStatus.NOT_FOUND, "Attachment file not found")
                    body = file_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", attachment["mime_type"])
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{quote(attachment['file_name'])}")
                self.send_header("Cache-Control", "private, no-store")
                self.end_headers()
                self.wfile.write(body)
            except ApiError as error:
                self.json_response({"detail": error.detail}, error.code)
            except Exception as error:
                print(f"Unhandled attachment error: {error}", flush=True)
                self.json_response({"detail": "Internal server error"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.handle_request("GET")

    def do_POST(self) -> None:
        self.handle_request("POST")

    def do_PATCH(self) -> None:
        self.handle_request("PATCH")


if __name__ == "__main__":
    init_database()
    bind_host = os.environ.get("SHENGBIAN_BIND_HOST", "127.0.0.1")
    httpd = ThreadingHTTPServer((bind_host, 8000), ApiHandler)
    print(f"Shengbian API listening on {bind_host}:8000 using {DATABASE_PATH}", flush=True)
    httpd.serve_forever()
