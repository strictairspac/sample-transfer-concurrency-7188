from fastapi import APIRouter,Depends
from src.config.database import db_session
from src.utils.security import current,writer
from src.schemas.trial import TrialInput
from src.services import trials as service
router=APIRouter()
@router.get('/trials')
def listing(db=Depends(db_session),user=Depends(current)): return service.listing(db)
@router.post('/trials',status_code=201)
def create(data:TrialInput,db=Depends(db_session),user=Depends(writer)): return service.create(db,data,user)
from fastapi.responses import Response
import json
@router.get('/trials/{ident}')
def detail(ident:int,db=Depends(db_session),user=Depends(current)): return service.detail(db,ident)
@router.get('/trials/{ident}/export')
def export(ident:int,db=Depends(db_session),user=Depends(current)):
    obj=service.detail(db,ident)
    return Response(json.dumps(obj,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="trial-{ident}.json"'})
