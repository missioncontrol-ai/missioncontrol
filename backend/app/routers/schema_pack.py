from fastapi import APIRouter, Request

router = APIRouter(prefix="/schema-pack", tags=["schema-pack"])


@router.get("")
def get_schema_pack(request: Request):
    schema_pack = getattr(request.app.state, "schema_pack", None)
    if not schema_pack:
        return {"loaded": False, "schema_pack": {}}
    return {"loaded": True, "schema_pack": schema_pack}
