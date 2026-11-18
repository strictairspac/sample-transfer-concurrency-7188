from sqlalchemy import select
from fastapi import HTTPException
from src.models.entities import Sample
from .common import save
from src.utils.serialization import record
def listing(db): return [record(x) for x in db.scalars(select(Sample).order_by(Sample.id))]
def create(db,data,user): return save(db,Sample(**data.model_dump()),user,'创建样本 '+data.code)
def update(db,ident,data,user):
    obj=db.get(Sample,ident)
    if not obj: raise HTTPException(404,'样本不存在')
    for k,v in data.model_dump().items(): setattr(obj,k,v)
    return save(db,obj,user,'更新样本 '+data.code)
