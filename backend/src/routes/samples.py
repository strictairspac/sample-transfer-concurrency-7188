from fastapi import APIRouter,Depends
from src.config.database import db_session
from src.utils.security import current,writer
from src.schemas.sample import SampleInput
from src.services import samples as service
router=APIRouter()
@router.get('/samples')
def listing(db=Depends(db_session),user=Depends(current)): return service.listing(db)
@router.post('/samples',status_code=201)
def create(data:SampleInput,db=Depends(db_session),user=Depends(writer)): return service.create(db,data,user)
@router.put('/samples/{ident}')
def update(ident:int,data:SampleInput,db=Depends(db_session),user=Depends(writer)): return service.update(db,ident,data,user)
