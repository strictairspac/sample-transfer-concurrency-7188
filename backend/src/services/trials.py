import json
from sqlalchemy import select
from redis import Redis
from fastapi import HTTPException
from src.config.settings import REDIS_URL
from src.models.entities import Trial,Transfer,Sample,TrialBalance
from src.utils.serialization import record
from .common import save
from src.constants.enums import RUN_STATES
queue=Redis.from_url(REDIS_URL,decode_responses=True)
def listing(db): return [record(x) for x in db.scalars(select(Trial).order_by(Trial.id.desc()))]
def detail(db,ident):
    obj=db.get(Trial,ident)
    if not obj: raise HTTPException(404,'演练不存在')
    return record(obj)
def create(db,data,user):
    transfer=db.get(Transfer,data.transfer_id)
    if not transfer: raise HTTPException(404,'流转申请不存在')
    sample=db.get(Sample,transfer.sample_id)
    if transfer.quantity>sample.quantity: raise HTTPException(422,'当前样本量不足')
    obj=Trial(**data.model_dump(),status=RUN_STATES[0])
    result=save(db,obj,user,'并发演练入队 '+data.name)
    db.add(TrialBalance(trial_id=obj.id,remaining=sample.quantity,received=0));db.commit()
    try: queue.rpush('locklab:queue',str(obj.id))
    except Exception:
        obj.status='failed';obj.error='队列不可用，请稍后重新创建';db.commit();raise HTTPException(503,obj.error)
    return result
