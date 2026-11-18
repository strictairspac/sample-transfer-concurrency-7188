from sqlalchemy import select
from fastapi import HTTPException
from src.models.entities import Transfer,Sample
from src.utils.serialization import record
from .common import save
def listing(db): return [record(x) for x in db.scalars(select(Transfer).order_by(Transfer.id))]
def create(db,data,user):
    sample=db.get(Sample,data.sample_id)
    if not sample: raise HTTPException(404,'样本不存在')
    if data.quantity>sample.quantity: raise HTTPException(422,'移交量超过样本可用量')
    return save(db,Transfer(**data.model_dump()),user,'创建流转申请 '+data.code)
