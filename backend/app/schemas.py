from pydantic import BaseModel


class TagCreate(BaseModel):
    name: str
    color: str | None = None


class RuleCreate(BaseModel):
    tag_id: int
    match_type: str  # 'contains' | 'regex'
    pattern: str
    priority: int = 0


class TagAssign(BaseModel):
    tag_id: int | None = None
