from datetime import datetime, timedelta, timezone

from passlib.context import CryptContext
from jose import jwt, JWTError, ExpiredSignatureError
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm, SecurityScopes
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload
from loguru import logger

from app.core.config import settings
from app.models import User, Role, Permission
from app.core.deps import AsyncSessionDep
from app.repositories.user_repository import UserRepository, RoleRepository, PermissionRepository

# 使用 passlib 库加密密码, deprecated="auto" 不使用废弃的加密方法
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# 使用 jose 库生成 JWT 令牌
def create_token(data: dict):
    to_encode = data.copy()

    # 设置过期时间
    access_token_expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_TIME)
    to_encode.update({"exp": access_token_expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    return encoded_jwt


# 解码 JWT 令牌
def decode_token(token: str):
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        return payload
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# 加密用户名的密码
def get_password_hash(password: str):
    return pwd_context.hash(password)



# 验证密码
def verify_password(plain, hashed):
    try:
        logger.info(f"plain: {plain}, hashed: {hashed}")
        return pwd_context.verify(plain, hashed)

    except Exception as e:
        logger.error(f"验证密码时出错: {e}")
        return False


# logger.info(verify_password("Heytime01!","$2b$12$JZZD/JyiK2tXBfLMxiFKq.oGoDzncNiSNPNR9QQXPjTzJxbhcbriG"))


# 注册密码时候就用hashed_password = pwd_context.hash("password")加密

# 验证用户
async def verify_user(username, password, db):
    """
    验证用户名和密码

    Args:
        username: 用户名
        password: 明文密码
        db: 数据库会话

    Returns:
        User: 验证成功返回用户对象，验证失败返回None
    """

    try:
        # 查询用户
        logger.info(f"验证用户: {username}")
        user = await UserRepository(db).get_one_by_username(username)
        logger.info(f"获取用户: {user}")

        if user and verify_password(password, user.password):
            return user
        return None
    except Exception as e:
        logger.error(f"验证用户时出错: {e}")
        return None



# 通过token获取用户
def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = decode_token(token)
    username = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username


# 通过用户名获取所有的权限
async def get_user_permission(*,username: str = Depends(get_current_user), db: AsyncSessionDep):
    """
    获取用户的所有权限
    
    Args:
        username: 用户名
        db: 数据库会话
        
    Returns:
        List[Permission]: 用户拥有的权限列表
    """
    try:
        # 查询用户，并预加载角色和权限
        # 使用 selectinload 来预加载关联的  Role 和  Permission 数据，减少数据库查询次数

        stmt = select(User).where(User.username == username).options(
            selectinload(User.roles).selectinload(Role.permissions)
        )
        result = await db.execute(stmt)
        #  如果使用joinedload, 需要使用 unique() 确保结果不重复
        user = result.scalar_one_or_none()

        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 检查用户是否有关联的角色
        if not user.roles:
            logger.warning(f"用户 {username} 没有关联的角色")
            return []  # 返回空列表表示没有角色关联

        # 收集用户所有角色的所有权限
        all_permissions = []
        for role in user.roles:
            if role.permissions:
                all_permissions.extend(role.permissions)
            else:
                logger.warning(f"角色 {role.name} 没有关联的权限")

        # 去重，其中 perm.id 作为字典的键，perm 作为值。
        unique_permissions = list({perm.id: perm for perm in all_permissions}.values())

        logger.info(f"用户 {username} 的权限: {[perm.name for perm in unique_permissions]}")
        return unique_permissions

    except Exception as e:
        logger.error(f"获取用户权限时出错: {e}")
        return []


def check_user_permission(security_scopes: SecurityScopes, user_permissions=Depends(get_user_permission)):
    """
    检查用户权限

    Args:
        security_scopes: 需要的权限列表
        user_permissions: 用户拥有的权限列表

    Returns:
        None: 检查通过
        HTTPException: 检查不通过
    """

    # 获取 security_scopes 中的权限列表
    required_scopes = security_scopes.scopes
    logger.info(f"需要权限: {required_scopes}")

    # 如果没有指定权限要求，则直接通过
    if not required_scopes:
        return

    # 将用户权限转换为权限名称列表
    user_permission_names = [permission.name for permission in user_permissions]
    logger.info(f"用户权限: {user_permission_names}")

    # 检查用户是否拥有所需的所有权限
    missing_permissions = [scope for scope in required_scopes if scope not in user_permission_names]


    if missing_permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"权限不足: 需要 {', '.join(missing_permissions)} 权限",
            headers={"WWW-Authenticate": f'Bearer scope="{security_scopes.scope_str}"'},
        )

    # # 检查用户是否拥有所需的所有权限
    # for scope in required_scopes:
    #     if scope not in user_permission_names:
    #         raise HTTPException(
    #             status_code=status.HTTP_403_FORBIDDEN,
    #             detail=f"权限不足: 需要 {scope} 权限",
    #             headers={"WWW-Authenticate": f'Bearer scope="{security_scopes.scope_str}"'},
    #         )
