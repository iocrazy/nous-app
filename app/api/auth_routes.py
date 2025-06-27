from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import select
from loguru import logger

from app.core.auth import verify_user, create_token
from app.core.auth import decode_token
from app.core.deps import AsyncSessionDep
from app.models import User, RoleType
from app.core.auth import get_password_hash



router = APIRouter(
    responses={401: {"description": "Unauthorized"}},
)


@router.post("/token")
async def login(username: str, password: str, db: AsyncSessionDep):
    try:
        user = await verify_user(username, password, db)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = create_token({"sub": user.username})
        return {"access_token": token, "token_type": "bearer"}
    except HTTPException:
        # 重新抛出 HTTPException，保持原始状态码
        raise
    except Exception as e:
        # 其他异常转换为 500 错误
        raise HTTPException(status_code=500, detail=f"登录失败: {str(e)}")


@router.post("/signup")
async def signup(username: str, password: str, role:RoleType=Query(default=RoleType.USER) , db: AsyncSessionDep):
    # 在这里实现用户注册逻辑
    try:
        # 检查用户名是否已存在
        stmt = select(User).where(User.username == username)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists",
            )

        # 创建新用户
        hashed_password = get_password_hash(password)
        new_user = User(username=username, password=hashed_password)
        db.add(new_user)
        await db.commit()
        logger.info(f"用户 {username} 注册成功")
        return {"message": "User created successfully"}
    except Exception as e:
        logger.error(f"用户注册失败: {e}")
        raise HTTPException(status_code=500, detail=f"注册失败: {str(e)}")




