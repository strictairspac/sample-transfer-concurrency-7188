from pydantic import BaseModel, Field
class TrialInput(BaseModel):
    name: str=Field(min_length=2,max_length=120)
    transfer_id: int=Field(gt=0)
    timeout_ms: int=Field(default=250,ge=50,le=2000)
