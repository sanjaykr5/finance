from pydantic import BaseModel


class TagCreate(BaseModel):
    name: str
    color: str | None = None


class RuleCreate(BaseModel):
    tag_id: int
    match_type: str  # 'contains' | 'regex'
    pattern: str
    priority: int = 0
    account_id: int | None = None  # restrict the rule to one account, or None for all


class TransactionUpdate(BaseModel):
    tag_id: int | None = None
    notes: str | None = None
    audited: bool | None = None


class RulesApply(BaseModel):
    tag_id: int | None = None
