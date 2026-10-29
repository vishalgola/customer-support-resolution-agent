from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TicketCreate(BaseModel):
    order_id: int | None = None
    summary: str = Field(min_length=1, max_length=2000)


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    order_id: int | None
    category: str | None
    status: str
    summary: str
    created_at: datetime
    