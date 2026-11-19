from fastapi import APIRouter,Depends,Header
from src.config.database import db_session
from src.utils.security import current,writer
from src.schemas.sample import SampleInput
from src.services import samples as service
from src.services.read_split import mark_session_write
router=APIRouter()
def _sid(x_session_id:str|None=Header(default=None)): return (x_session_id or '').strip()[:80]
@router.get('/samples')
def listing(db=Depends(db_session),user=Depends(current)): return service.listing(db)
@router.post('/samples',status_code=201)
def create(data:SampleInput,db=Depends(db_session),user=Depends(writer),sid:str=Depends(_sid)):
    obj=service.create(db,data,user)
    mark_session_write(sid)
    return obj
@router.put('/samples/{ident}')
def update(ident:int,data:SampleInput,db=Depends(db_session),user=Depends(writer),sid:str=Depends(_sid)):
    obj=service.update(db,ident,data,user)
    mark_session_write(sid)
    return obj
