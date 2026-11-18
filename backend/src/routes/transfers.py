from fastapi import APIRouter,Depends
from src.config.database import db_session
from src.utils.security import current,writer
from src.schemas.transfer import TransferInput
from src.services import transfers as service
router=APIRouter()
@router.get('/transfers')
def listing(db=Depends(db_session),user=Depends(current)): return service.listing(db)
@router.post('/transfers',status_code=201)
def create(data:TransferInput,db=Depends(db_session),user=Depends(writer)): return service.create(db,data,user)
