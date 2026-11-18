from pydantic import BaseModel, Field
class SampleInput(BaseModel):
    code: str=Field(min_length=2,max_length=80)
    title: str=Field(min_length=2,max_length=160)
    quantity: int=Field(ge=1,le=100000)
    location: str=Field(min_length=1,max_length=120)
