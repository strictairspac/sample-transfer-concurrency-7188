def record(obj):
    return {col.name:(getattr(obj,col.name).isoformat() if hasattr(getattr(obj,col.name),'isoformat') else getattr(obj,col.name)) for col in obj.__table__.columns}
