"""AC #2: registration rules and payload validation."""

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

import pytest
from pydantic import BaseModel, Field

from app.jobs import queue, registry
from app.jobs.registry import Identifier, InvalidPayloadError, JobContext, PayloadModelError
from tests.conftest import PgDatabase
from tests.jobs.conftest import SENSITIVE, NoopPayload, Recorder, fetch_job


class Template(Enum):
    SHORT = "short"


class Inner(BaseModel):
    sheet: Identifier
    row: int


class GoodPayload(BaseModel):
    document_id: uuid.UUID
    other_ids: list[uuid.UUID] = Field(default_factory=list)
    template: Template = Template.SHORT
    kind: Literal["a", "b"] = "a"
    pages: tuple[int, ...] = ()
    as_of: date | None = None
    at: datetime | None = None
    force: bool = False
    batch: int = Field(default=1, ge=1)
    key: Identifier = "default"
    inner: Inner | None = None


async def _handler(ctx: JobContext, payload: Any) -> None:
    return None


@pytest.fixture(autouse=True)
def _cleanup() -> Any:
    yield
    for name in ("good_job", "bad_job"):
        registry.unregister(name)


def test_allowed_field_types_register() -> None:
    job_type = registry.register("good_job", GoodPayload, _handler)
    assert registry.get("good_job") is job_type
    assert job_type.llm_heavy is False


class Comment(BaseModel):
    comment: str


class OptionalText(BaseModel):
    note: str | None = None


class TextList(BaseModel):
    tags: list[str]


class NestedText(BaseModel):
    inner: Comment


class FloatField(BaseModel):
    amount: float


class DictField(BaseModel):
    extra: dict[str, int]


class AnyField(BaseModel):
    value: Any


class BytesField(BaseModel):
    blob: bytes


class PatternOnly(BaseModel):
    # A pattern alone isn't enough: only the Identifier marker counts.
    name: str = Field(pattern=r".*")


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (Comment, "comment"),
        (OptionalText, "note"),
        (TextList, "tags"),
        (NestedText, "comment"),
        (FloatField, "amount"),
        (DictField, "extra"),
        (AnyField, "value"),
        (BytesField, "blob"),
        (PatternOnly, "name"),
    ],
)
def test_content_capable_fields_raise_at_registration(model: type[BaseModel], field: str) -> None:
    with pytest.raises(PayloadModelError, match=field):
        registry.register("bad_job", model, _handler)
    assert "bad_job" not in registry.registered()


def test_bad_type_names_and_duplicates() -> None:
    with pytest.raises(ValueError, match="must match"):
        registry.register("Bad Name", NoopPayload, _handler)
    registry.register("good_job", NoopPayload, _handler)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("good_job", NoopPayload, _handler)


def test_unknown_type() -> None:
    with pytest.raises(registry.UnknownJobTypeError):
        registry.get("nope")


async def test_enqueue_validates_payload(jobs_db: PgDatabase, job_types: Recorder) -> None:
    with pytest.raises(InvalidPayloadError) as info:
        await queue.enqueue("noop", {"n": SENSITIVE})
    # The error never echoes the rejected value.
    assert SENSITIVE not in str(info.value)
    with pytest.raises(InvalidPayloadError):
        await queue.enqueue("coded_fail", {"code": "has spaces!"})
    with pytest.raises(registry.UnknownJobTypeError):
        await queue.enqueue("nope", {})


async def test_enqueue_stores_the_validated_payload(
    jobs_db: PgDatabase, job_types: Recorder
) -> None:
    user, space = uuid.uuid4(), uuid.uuid4()
    job_id = await queue.enqueue(
        "noop", {"n": "7", "ignored": "dropped"}, user_id=user, space_id=space, priority=10
    )
    job = await fetch_job(job_id)
    assert job.payload == {"n": 7}
    assert (job.type, job.user_id, job.space_id, job.priority) == ("noop", user, space, 10)
    assert (job.status, job.attempts, job.max_attempts) == ("queued", 0, 3)
    assert job.id.version == 7

    model_id = await queue.enqueue("noop", NoopPayload(n=3))
    assert (await fetch_job(model_id)).payload == {"n": 3}
