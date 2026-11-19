from fastapi import APIRouter,Depends,Header
from src.config.database import db_session
from src.utils.security import current,writer
from src.schemas.transfer import TransferInput
from src.services import transfers as service
from src.services.read_split import mark_session_write
router=APIRouter()
def _sid(x_session_id:str|None=Header(default=None)): return (x_session_id or '').strip()[:80]
@router.get('/transfers')
def listing(db=Depends(db_session),user=Depends(current)): return service.listing(db)
@router.post('/transfers',status_code=201)
def create(data:TransferInput,db=Depends(db_session),user=Depends(writer),sid:str=Depends(_sid)):
    obj=service.create(db,data,user)
    # 提交点之后登记会话水位：换任何一台后端，下一次读都必须看到这条新单
    mark_session_write(sid)
    return obj
