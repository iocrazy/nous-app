from fastapi import APIRouter
from app.api.auth_router import router as auth_router
from app.api.douyin_router import router as douyin_router


api_router = APIRouter()

api_router.include_router(
    router=auth_router,
    prefix="/auth",
    tags=["auth"]
)

api_router.include_router(
    router=douyin_router,
    prefix="/douyin",
    tags=["douyin"]
)
