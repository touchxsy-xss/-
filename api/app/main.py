from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import secrets
from pathlib import Path
from typing import Generator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, desc, func, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL must be set before starting the PostgreSQL API")
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


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def next_ticket_public_id(session: Session) -> str:
    """Generate a non-sequential public ID so concurrent ticket creation cannot reuse a count."""
    prefix = f"SB-{now_utc():%Y%m%d}"
    while True:
        candidate = f"{prefix}-{secrets.token_hex(4).upper()}"
        exists = session.scalar(select(RepairTicket.id).where(RepairTicket.public_id == candidate))
        if not exists:
            return candidate


class Base(DeclarativeBase):
    pass


class PropertyCompany(Base):
    __tablename__ = "property_companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    communities: Mapped[list["Community"]] = relationship(back_populates="property_company")


class Community(Base):
    __tablename__ = "communities"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    city: Mapped[str] = mapped_column(String(80))
    district: Mapped[str] = mapped_column(String(80))
    resident_count: Mapped[int] = mapped_column(Integer, default=0)
    property_company_id: Mapped[int] = mapped_column(ForeignKey("property_companies.id"))
    property_company: Mapped[PropertyCompany] = relationship(back_populates="communities")
    buildings: Mapped[list["Building"]] = relationship(back_populates="community")
    users: Mapped[list["User"]] = relationship(back_populates="community")
    tickets: Mapped[list["RepairTicket"]] = relationship(back_populates="community")
    materials: Mapped[list["WorkMaterial"]] = relationship(back_populates="community")
    articles: Mapped[list["Article"]] = relationship(back_populates="community")
    announcements: Mapped[list["Announcement"]] = relationship(back_populates="community")


class Building(Base):
    __tablename__ = "buildings"

    id: Mapped[int] = mapped_column(primary_key=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"))
    name: Mapped[str] = mapped_column(String(80))
    community: Mapped[Community] = relationship(back_populates="buildings")
    units: Mapped[list["Unit"]] = relationship(back_populates="building")


class Unit(Base):
    __tablename__ = "units"

    id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id"))
    number: Mapped[str] = mapped_column(String(40))
    building: Mapped[Building] = relationship(back_populates="units")
    residents: Mapped[list["User"]] = relationship(back_populates="unit")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    demo_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(30), index=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    community_id: Mapped[int | None] = mapped_column(ForeignKey("communities.id"), nullable=True)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.id"), nullable=True)
    community: Mapped[Community | None] = relationship(back_populates="users")
    unit: Mapped[Unit | None] = relationship(back_populates="residents")


class RepairTicket(Base):
    __tablename__ = "repair_tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), index=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String(60))
    location: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    contact: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="new", index=True)
    assignee: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    community: Mapped[Community] = relationship(back_populates="tickets")
    resident: Mapped[User] = relationship(foreign_keys=[resident_id])
    events: Mapped[list["RepairEvent"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="RepairEvent.created_at"
    )


class RepairEvent(Base):
    __tablename__ = "repair_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("repair_tickets.id"), index=True)
    actor_name: Mapped[str] = mapped_column(String(80))
    actor_role: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    ticket: Mapped[RepairTicket] = relationship(back_populates="events")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    community_id: Mapped[int | None] = mapped_column(ForeignKey("communities.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str] = mapped_column(String(80))
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class WorkMaterial(Base):
    __tablename__ = "work_materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    material_type: Mapped[str] = mapped_column(String(30))
    category: Mapped[str] = mapped_column(String(80))
    item_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    community: Mapped[Community] = relationship(back_populates="materials")


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(240))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    community: Mapped[Community] = relationship(back_populates="articles")
    author: Mapped[User] = relationship(foreign_keys=[author_id])
    reviews: Mapped[list["ArticleReview"]] = relationship(back_populates="article", cascade="all, delete-orphan")


class ArticleReview(Base):
    __tablename__ = "article_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    article: Mapped[Article] = relationship(back_populates="reviews")
    reviewer: Mapped[User] = relationship(foreign_keys=[reviewer_id])


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), index=True)
    title: Mapped[str] = mapped_column(String(240))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="published")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    community: Mapped[Community] = relationship(back_populates="announcements")


engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "postgresql"


def apply_postgres_migrations() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
            )
        )
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if migration.name.startswith("."):
                continue
            version = migration.stem
            applied = connection.execute(
                text("SELECT 1 FROM schema_migrations WHERE version = :version"), {"version": version}
            ).scalar()
            if applied:
                continue
            statements = [statement.strip() for statement in migration.read_text(encoding="utf-8").split(";") if statement.strip()]
            for statement in statements:
                connection.exec_driver_sql(statement)
            connection.execute(
                text("INSERT INTO schema_migrations(version) VALUES (:version)"), {"version": version}
            )


class RepairCreate(BaseModel):
    category: str = Field(min_length=1, max_length=60)
    location: str = Field(min_length=2, max_length=200)
    description: str = Field(min_length=3, max_length=2000)
    contact: str | None = Field(default=None, max_length=80)


class TicketTransition(BaseModel):
    status: str = Field(min_length=1, max_length=40)
    note: str = Field(min_length=2, max_length=1000)
    assignee: str | None = Field(default=None, max_length=100)
    expected_version: int | None = Field(default=None, alias="expectedVersion", ge=1)


class ResidentConfirmation(BaseModel):
    resolved: bool
    note: str | None = Field(default=None, max_length=1000)
    expected_version: int | None = Field(default=None, alias="expectedVersion", ge=1)


class ArticleCreate(BaseModel):
    title: str = Field(min_length=3, max_length=240)
    body: str = Field(min_length=10, max_length=10000)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def get_current_user(
    x_demo_user: str | None = Header(default=None, alias="X-Demo-User"),
    session: Session = Depends(get_session),
) -> User:
    if not x_demo_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing demo identity")
    user = session.scalar(select(User).where(User.demo_key == x_demo_user))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown demo identity")
    return user


def require_role(user: User, *roles: str) -> None:
    if user.role not in roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Role is not allowed for this action")


def scoped_community(session: Session, user: User, slug: str | None = None) -> Community:
    if slug:
        community = session.scalar(select(Community).where(Community.slug == slug))
    elif user.community_id:
        community = session.get(Community, user.community_id)
    else:
        community = None
    if not community:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community not found")
    if user.role != "platform" and user.community_id != community.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Community is outside this account scope")
    return community


def serialize_community(community: Community) -> dict:
    return {
        "id": community.id,
        "slug": community.slug,
        "name": community.name,
        "city": community.city,
        "district": community.district,
        "residentCount": community.resident_count,
        "propertyCompany": community.property_company.name,
    }


def serialize_user(user: User) -> dict:
    unit_label = None
    if user.unit:
        unit_label = f"{user.unit.building.name} {user.unit.number}"
    return {
        "id": user.id,
        "name": user.display_name,
        "role": user.role,
        "community": user.community.slug if user.community else None,
        "unit": unit_label,
    }


def serialize_ticket(ticket: RepairTicket, include_events: bool = True) -> dict:
    data = {
        "id": ticket.id,
        "publicId": ticket.public_id,
        "community": ticket.community.slug,
        "communityName": ticket.community.name,
        "resident": ticket.resident.display_name,
        "category": ticket.category,
        "location": ticket.location,
        "description": ticket.description,
        "contact": ticket.contact,
        "status": ticket.status,
        "statusLabel": STATUS_LABELS[ticket.status],
        "assignee": ticket.assignee,
        "version": ticket.version,
        "createdAt": ticket.created_at.isoformat(),
        "updatedAt": ticket.updated_at.isoformat(),
    }
    if include_events:
        data["events"] = [
            {
                "id": event.id,
                "actor": event.actor_name,
                "role": event.actor_role,
                "status": event.status,
                "statusLabel": STATUS_LABELS[event.status],
                "note": event.note,
                "createdAt": event.created_at.isoformat(),
            }
            for event in ticket.events
        ]
    return data


def serialize_article(article: Article) -> dict:
    return {
        "id": article.id,
        "community": article.community.slug,
        "communityName": article.community.name,
        "title": article.title,
        "body": article.body,
        "status": article.status,
        "author": article.author.display_name,
        "publishedAt": article.published_at.isoformat() if article.published_at else None,
        "createdAt": article.created_at.isoformat(),
    }


def add_event(ticket: RepairTicket, actor: User, ticket_status: str, note: str) -> None:
    ticket.events.append(
        RepairEvent(
            actor_name=actor.display_name,
            actor_role=actor.role,
            status=ticket_status,
            note=note,
        )
    )


def add_audit(
    session: Session,
    actor: User,
    action: str,
    resource_type: str,
    resource_id: str | int,
    community_id: int | None,
    details: str,
) -> None:
    session.add(
        AuditLog(
            actor_id=actor.id,
            community_id=community_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            details=details,
        )
    )


def seed_ticket(
    session: Session,
    community: Community,
    resident: User,
    public_id: str,
    category: str,
    location: str,
    description: str,
    ticket_status: str,
    assignee: str | None,
    events: list[tuple[str, str, str, str]],
) -> RepairTicket:
    ticket = RepairTicket(
        public_id=public_id,
        community=community,
        resident=resident,
        category=category,
        location=location,
        description=description,
        contact=resident.phone,
        status=ticket_status,
        assignee=assignee,
    )
    session.add(ticket)
    session.flush()
    for actor_name, actor_role, event_status, note in events:
        ticket.events.append(
            RepairEvent(actor_name=actor_name, actor_role=actor_role, status=event_status, note=note)
        )
    return ticket


def seed_database(session: Session) -> None:
    if session.scalar(select(func.count()).select_from(User)):
        return

    pufa = PropertyCompany(name="浦发物业")
    anju = PropertyCompany(name="安居服务")
    huajing = PropertyCompany(name="华景物业")
    session.add_all([pufa, anju, huajing])
    session.flush()

    pengyi = Community(
        slug="pengyi",
        name="彭一小区",
        city="上海",
        district="浦东新区",
        resident_count=2384,
        property_company=pufa,
    )
    binjiang = Community(
        slug="binjiang",
        name="滨江家园",
        city="上海",
        district="徐汇区",
        resident_count=1958,
        property_company=pufa,
    )
    jinyue = Community(
        slug="jinyue",
        name="金悦府",
        city="上海",
        district="闵行区",
        resident_count=1415,
        property_company=anju,
    )
    xingang = Community(
        slug="xingang",
        name="新港花园",
        city="上海",
        district="杨浦区",
        resident_count=2126,
        property_company=huajing,
    )
    session.add_all([pengyi, binjiang, jinyue, xingang])
    session.flush()

    building_16 = Building(community=pengyi, name="16 号楼 2 单元")
    building_3 = Building(community=pengyi, name="3 号楼 1 单元")
    unit_502 = Unit(building=building_16, number="502")
    unit_301 = Unit(building=building_3, number="301")
    session.add_all([building_16, building_3, unit_502, unit_301])
    session.flush()

    platform = User(demo_key="platform-admin", display_name="陈总", role="platform")
    property_manager = User(
        demo_key="property-pengyi",
        display_name="周敏",
        role="property",
        phone="021-6808 0228",
        community=pengyi,
    )
    resident_li = User(
        demo_key="resident-li",
        display_name="李女士",
        role="resident",
        phone="138****6272",
        community=pengyi,
        unit=unit_502,
    )
    resident_zhang = User(
        demo_key="resident-zhang",
        display_name="张先生",
        role="resident",
        phone="139****2031",
        community=pengyi,
        unit=unit_301,
    )
    session.add_all([platform, property_manager, resident_li, resident_zhang])
    session.flush()

    seed_ticket(
        session,
        pengyi,
        resident_li,
        "SB-20260817-10248",
        "照明",
        "16 号楼 2 单元 1 层楼道",
        "楼道照明不亮，晚上经过时看不清台阶，希望尽快处理。",
        "awaiting_vendor",
        "照明维保单位",
        [
            ("李女士", "resident", "new", "居民提交报修，已上传 2 张图片。"),
            ("周敏", "property", "processing", "物业已受理并分派工程处理。"),
            ("周敏", "property", "awaiting_vendor", "已联系照明维保单位，最近一次催办：09:42。"),
        ],
    )
    seed_ticket(
        session,
        pengyi,
        resident_zhang,
        "SB-20260817-10247",
        "电梯",
        "3 号楼 1 单元",
        "电梯运行时有明显异响，居民已上传 1 段视频。",
        "awaiting_vendor",
        "永达电梯维保",
        [
            ("张先生", "resident", "new", "居民提交电梯异响问题。"),
            ("周敏", "property", "processing", "客服已受理并联系工程部。"),
            ("周敏", "property", "awaiting_vendor", "维保单位承诺 11:00 到场，已记录催办。"),
        ],
    )
    seed_ticket(
        session,
        pengyi,
        resident_li,
        "SB-20260816-10242",
        "给排水",
        "B2 区地下车库",
        "车库排水需要复查。",
        "awaiting_confirmation",
        "李师傅",
        [
            ("李女士", "resident", "new", "居民提交车库排水检查。"),
            ("周敏", "property", "processing", "工程部已接单。"),
            ("周敏", "property", "awaiting_confirmation", "已完成检查，等待居民确认。"),
        ],
    )
    seed_ticket(
        session,
        pengyi,
        resident_zhang,
        "SB-20260816-10239",
        "门禁",
        "9 号楼大门",
        "门禁识别异常。",
        "new",
        None,
        [("张先生", "resident", "new", "居民提交门禁识别异常。")],
    )
    seed_ticket(
        session,
        binjiang,
        resident_li,
        "SB-20260817-20101",
        "停车秩序",
        "东门车库",
        "夜间临停占位。",
        "processing",
        "秩序队",
        [("平台巡检", "platform", "processing", "已同步给项目负责人处理。")],
    )
    seed_ticket(
        session,
        jinyue,
        resident_li,
        "SB-20260817-30101",
        "环境卫生",
        "7 号楼公共区域",
        "楼道堆物待清理。",
        "new",
        None,
        [("平台巡检", "platform", "new", "项目待处理。")],
    )
    seed_ticket(
        session,
        xingang,
        resident_li,
        "SB-20260817-40101",
        "电梯",
        "12 号楼",
        "电梯停靠异常。",
        "awaiting_vendor",
        "第三方维保",
        [("平台巡检", "platform", "awaiting_vendor", "等待第三方到场。")],
    )

    session.add_all(
        [
            WorkMaterial(community=pengyi, title="地下车库排水检查", material_type="image", category="工程 / 给排水", item_count=6),
            WorkMaterial(community=pengyi, title="16 号楼公共照明维修", material_type="video", category="工程 / 公共区域", item_count=1),
            WorkMaterial(community=pengyi, title="夏季绿化养护", material_type="image", category="绿化", item_count=18),
            WorkMaterial(community=pengyi, title="本周工作总结与下周计划", material_type="text", category="客服", item_count=1, status="needs_input"),
        ]
    )
    session.add_all(
        [
            Article(
                community=pengyi,
                author=property_manager,
                title="这一周，我们把小区的每一处细节放在心上",
                body="工程团队完成地下车库排水专项检查，并对 16 号楼公共照明进行维修；绿化团队持续开展夏季养护。关于居民关心的电梯问题，物业已联系第三方维保单位并持续跟进。",
                status="published",
                published_at=now_utc(),
            ),
            Article(
                community=pengyi,
                author=property_manager,
                title="彭一小区第 33 周物业周记",
                body="本周围绕照明、电梯、绿化与车库排水开展了重点服务。请平台审核后向居民发布。",
                status="submitted",
            ),
        ]
    )
    session.add(
        Announcement(
            community=pengyi,
            title="3 号楼电梯例行检修通知",
            body="8 月 19 日 9:00-12:00 进行例行检修，请合理安排出行。",
        )
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    apply_postgres_migrations()
    with SessionLocal.begin() as session:
        seed_database(session)
    yield


app = FastAPI(title="Shengbian Demo API", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return {"user": serialize_user(user), "authentication": "demo-header"}


@app.get("/api/dashboard/platform")
def platform_dashboard(session: Session = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    require_role(user, "platform")
    communities = session.scalars(select(Community).order_by(Community.id)).all()
    cards = []
    for community in communities:
        tickets = session.scalars(select(RepairTicket).where(RepairTicket.community_id == community.id)).all()
        open_tickets = [ticket for ticket in tickets if ticket.status not in {"resolved"}]
        cards.append(
            {
                **serialize_community(community),
                "openTickets": len(open_tickets),
                "riskTickets": len([ticket for ticket in open_tickets if ticket.status in {"new", "awaiting_vendor", "reopened"}]),
                "materialCount": session.scalar(
                    select(func.count()).select_from(WorkMaterial).where(WorkMaterial.community_id == community.id)
                ),
            }
        )
    pending_articles = session.scalars(
        select(Article).where(Article.status == "submitted").order_by(desc(Article.updated_at))
    ).all()
    return {
        "user": serialize_user(user),
        "metrics": {
            "communities": len(communities),
            "openTickets": session.scalar(
                select(func.count()).select_from(RepairTicket).where(RepairTicket.status != "resolved")
            ),
            "pendingArticles": len(pending_articles),
            "materials": session.scalar(select(func.count()).select_from(WorkMaterial)),
        },
        "communities": cards,
        "pendingArticles": [serialize_article(article) for article in pending_articles],
    }


@app.get("/api/dashboard/property")
def property_dashboard(
    community_id: str | None = Query(default=None, alias="communityId"),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    require_role(user, "property", "platform")
    community = scoped_community(session, user, community_id)
    tickets = session.scalars(
        select(RepairTicket)
        .where(RepairTicket.community_id == community.id)
        .order_by(desc(RepairTicket.updated_at))
    ).all()
    materials = session.scalars(
        select(WorkMaterial).where(WorkMaterial.community_id == community.id).order_by(desc(WorkMaterial.created_at))
    ).all()
    articles = session.scalars(
        select(Article).where(Article.community_id == community.id).order_by(desc(Article.updated_at))
    ).all()
    return {
        "user": serialize_user(user),
        "community": serialize_community(community),
        "metrics": {
            "openTickets": len([ticket for ticket in tickets if ticket.status != "resolved"]),
            "awaitingVendor": len([ticket for ticket in tickets if ticket.status == "awaiting_vendor"]),
            "awaitingConfirmation": len([ticket for ticket in tickets if ticket.status == "awaiting_confirmation"]),
            "materials": len(materials),
        },
        "tickets": [serialize_ticket(ticket) for ticket in tickets],
        "materials": [
            {
                "id": material.id,
                "title": material.title,
                "type": material.material_type,
                "category": material.category,
                "itemCount": material.item_count,
                "status": material.status,
            }
            for material in materials
        ],
        "articles": [serialize_article(article) for article in articles],
    }


@app.get("/api/dashboard/resident")
def resident_dashboard(session: Session = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    require_role(user, "resident")
    community = scoped_community(session, user)
    tickets = session.scalars(
        select(RepairTicket)
        .where(RepairTicket.resident_id == user.id)
        .order_by(desc(RepairTicket.updated_at))
    ).all()
    articles = session.scalars(
        select(Article)
        .where(Article.community_id == community.id, Article.status == "published")
        .order_by(desc(Article.published_at))
    ).all()
    announcements = session.scalars(
        select(Announcement)
        .where(Announcement.community_id == community.id, Announcement.status == "published")
        .order_by(desc(Announcement.published_at))
    ).all()
    return {
        "user": serialize_user(user),
        "community": serialize_community(community),
        "tickets": [serialize_ticket(ticket) for ticket in tickets],
        "articles": [serialize_article(article) for article in articles],
        "announcements": [
            {"id": item.id, "title": item.title, "body": item.body, "publishedAt": item.published_at.isoformat()}
            for item in announcements
        ],
    }


@app.post("/api/repairs", status_code=status.HTTP_201_CREATED)
def create_repair(
    payload: RepairCreate,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    require_role(user, "resident")
    community = scoped_community(session, user)
    ticket = RepairTicket(
        public_id=next_ticket_public_id(session),
        community=community,
        resident=user,
        category=payload.category.strip(),
        location=payload.location.strip(),
        description=payload.description.strip(),
        contact=payload.contact.strip() if payload.contact else user.phone,
        status="new",
    )
    session.add(ticket)
    session.flush()
    add_event(ticket, user, "new", "居民提交报修，等待物业受理。")
    add_audit(session, user, "repair.created", "repair_ticket", ticket.id, community.id, "居民创建报修工单")
    session.commit()
    session.refresh(ticket)
    return {"ticket": serialize_ticket(ticket)}


@app.patch("/api/repairs/{ticket_id}/transition")
def transition_repair(
    ticket_id: int,
    payload: TicketTransition,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    require_role(user, "property")
    ticket = session.get(RepairTicket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    scoped_community(session, user, ticket.community.slug)
    if payload.expected_version is not None and ticket.version != payload.expected_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ticket changed elsewhere; reload before trying again")
    allowed_statuses = ALLOWED_TICKET_TRANSITIONS.get(ticket.status, set())
    if payload.status not in allowed_statuses:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot transition ticket from {ticket.status} to {payload.status}",
        )
    ticket.status = payload.status
    if payload.assignee is not None:
        ticket.assignee = payload.assignee.strip() or None
    ticket.version += 1
    add_event(ticket, user, payload.status, payload.note.strip())
    add_audit(session, user, "repair.transitioned", "repair_ticket", ticket.id, ticket.community_id, f"工单状态变更为 {payload.status}")
    session.commit()
    session.refresh(ticket)
    return {"ticket": serialize_ticket(ticket)}


@app.post("/api/repairs/{ticket_id}/resident-confirmation")
def confirm_repair(
    ticket_id: int,
    payload: ResidentConfirmation,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    require_role(user, "resident")
    ticket = session.get(RepairTicket, ticket_id)
    if not ticket or ticket.resident_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    if ticket.status != "awaiting_confirmation":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only tickets awaiting resident confirmation can be confirmed or reopened",
        )
    if payload.expected_version is not None and ticket.version != payload.expected_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ticket changed elsewhere; reload before trying again")
    ticket.status = "resolved" if payload.resolved else "reopened"
    ticket.version += 1
    note = payload.note or ("居民确认问题已解决。" if payload.resolved else "居民反馈问题尚未解决，已重新打开。")
    add_event(ticket, user, ticket.status, note)
    add_audit(session, user, "repair.confirmed", "repair_ticket", ticket.id, ticket.community_id, f"居民确认结果: {ticket.status}")
    session.commit()
    session.refresh(ticket)
    return {"ticket": serialize_ticket(ticket)}


@app.post("/api/articles", status_code=status.HTTP_201_CREATED)
def create_article(
    payload: ArticleCreate,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    require_role(user, "property")
    community = scoped_community(session, user)
    article = Article(
        community=community,
        author=user,
        title=payload.title.strip(),
        body=payload.body.strip(),
        status="draft",
    )
    session.add(article)
    session.commit()
    session.refresh(article)
    add_audit(session, user, "article.created", "article", article.id, community.id, "物业创建周记草稿")
    session.commit()
    return {"article": serialize_article(article)}


@app.post("/api/articles/{article_id}/submit")
def submit_article(
    article_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    require_role(user, "property")
    article = session.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    scoped_community(session, user, article.community.slug)
    if article.status != "draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only draft articles can be submitted")
    article.status = "submitted"
    add_audit(session, user, "article.submitted", "article", article.id, article.community_id, "物业提交周记审核")
    session.commit()
    session.refresh(article)
    return {"article": serialize_article(article)}


@app.post("/api/articles/{article_id}/approve")
def approve_article(
    article_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    require_role(user, "platform")
    article = session.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    if article.status != "submitted":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only submitted articles can be approved")
    article.status = "published"
    article.published_at = now_utc()
    article.reviews.append(ArticleReview(reviewer=user, decision="approved", note="平台审核通过并发布。"))
    add_audit(session, user, "article.published", "article", article.id, article.community_id, "平台审核通过并发布周记")
    session.commit()
    session.refresh(article)
    return {"article": serialize_article(article)}
