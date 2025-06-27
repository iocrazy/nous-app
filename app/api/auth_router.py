from fastapi import APIRouter, Depends, HTTPException, status, Security
from fastapi.security import OAuth2PasswordRequestForm
from typing import Annotated
from loguru import logger

from app.core.auth import verify_user, create_token ,verify_password
from app.core.deps import AsyncSessionDep
from app.models import User, Permission, Role
from app.core.enums import RoleType
from app.core.auth import get_password_hash, get_user_permission, check_user_permission, get_current_user
from app.schemas.user import (
    UserCreate, UserRoleAssign, UserPermissionsResponse,UserRolesResponse,UserRoleRemove,
    RolePermissionAssign, RolePermissionsResponse, RolePermissionRemove,
    PermissionCreate, PermissionResponse, UserUpdate )
from app.repositories.user_repository import UserRepository, PermissionRepository, RoleRepository

router = APIRouter(
    responses={401: {"description": "Unauthorized"}},
)


@router.post("/login")
async def login(form_data: Annotated[OAuth2PasswordRequestForm, Depends()], db: AsyncSessionDep):
    try:
        user = await verify_user(form_data.username, form_data.password, db)
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
async def signup(*, user_data: UserCreate, db: AsyncSessionDep):
    # 在这里实现用户注册逻辑
    try:
        # 检查用户名是否已存在
        user_repo = UserRepository(db)
        user = await user_repo.get_one_by_username(user_data.username)
        logger.info(f"用户 {user_data.username} 注册")
        if user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists",
            )

        # 创建新用户
        hashed_password = get_password_hash(user_data.password)
        new_user = User(username=user_data.username, password=hashed_password)

        # 获取默认的 USER 角色
        role_repo = RoleRepository(db)
        user_role = await role_repo.get_one_by_name(RoleType.USER.value)

        if not user_role:
            # 如果角色不存在，创建它（通常不应该发生，因为角色应该在初始化时创建）
            logger.warning(f"角色 {RoleType.USER.value} 不存在，正在创建...")
            user_role = Role(name=RoleType.USER.value, description="普通用户")
            db.add(user_role)
            await db.flush()  # 刷新会话，获取新创建角色的ID，但不提交事务

        # 将角色添加到用户的 role 列表中
        new_user.roles.append(user_role)

        # 添加用户并提交事务
        db.add(new_user)
        await db.commit()

        logger.info(f"用户 {user_data.username} 注册成功")
        return {"message": "User created successfully"}
    except Exception as e:
        logger.error(f"用户注册失败: {e}")
        raise HTTPException(status_code=500, detail=f"注册失败: {str(e)}")


# 0. 创建权限
@router.post("/create_permission", dependencies=[Security(check_user_permission, scopes=["create_permission"])])
async def create_permission(permission_data: PermissionCreate, db: AsyncSessionDep):
    try:
        # 使用仓储类创建权限
        permission_repo = PermissionRepository(db)
        permission = await permission_repo.create(permission_data.model_dump())

        # 添加提交事务
        await db.commit()

        if permission:
            logger.info(f"权限 {permission_data.name} 创建成功")
            return {"message": "Permission created successfully"}
        else:
            raise HTTPException(status_code=500, detail="创建权限失败")
    except Exception as e:
        logger.error(f"创建权限失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建权限失败: {str(e)}")


# 1. 为用户分配角色
@router.post("/assign_role_to_user", dependencies=[Security(check_user_permission, scopes=["manage_users"])])
async def assign_role_to_user(assignment: UserRoleAssign, db: AsyncSessionDep):
    """
    为用户分配角色

    需要 manage_users 权限
    """
    try:
        # 获取用户
        user_repo = UserRepository(db)
        #
        user = await user_repo.get_one_by_username_with_roles(assignment.username)

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"用户 '{assignment.username}' 不存在"
            )
        logger.info(f"获取用户: {user}")

        # 获取角色
        role_repo = RoleRepository(db)
        added_roles = []
        for role_name in assignment.role_names:

            role = await role_repo.get_one_by_name(role_name)

            if not role:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"角色 '{role_name}' 不存在"
                )
            logger.info(f"获取角色: {role}")

            # 检查用户是否已经拥有该角色
            if role not in user.roles:
                logger.info(f"为用户 {user.username} 分配角色: {role.name}")
                user.roles.append(role)
                added_roles.append(role)

        # 使用 try/except 包装 commit 操作
        try:
            await db.commit()
        except Exception as commit_error:
            logger.error(f"提交事务失败: {commit_error}")
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"提交事务失败: {str(commit_error)}"
            )

        return {
            "message": f"已成功为用户 '{user.username}' 分配角色 '{', '.join(role.name.value for role in added_roles)} '"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"分配角色失败: {e}")
        raise HTTPException(status_code=500, detail=f"分配角色失败: {str(e)}")


# 为角色分配权限
@router.post("/assign_permissions_to_role", dependencies=[Security(check_user_permission, scopes=["manage_users"])])
async def assign_permissions_to_role(assignment: RolePermissionAssign, db: AsyncSessionDep):
    """
    为角色分配权限 - 追加模式
    """
    try:
        # 获取角色
        role_repo = RoleRepository(db)
        role = await role_repo.get_one_by_role_name_with_permissions(assignment.role_name)

        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"角色 '{assignment.role_name}' 不存在"
            )

        # 获取所有指定的权限
        permission_repo = PermissionRepository(db)
        added_permissions = []
        for perm_id in assignment.permission_ids:
            permission = await permission_repo.get_by_id(perm_id)
            if not permission:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"权限ID {perm_id} 不存在"
                )
            logger.info(f"获取权限: {permission.name}")

            # 检查权限是否已经存在于角色的权限列表中
            if permission not in role.permissions:
                logger.info(f"为角色 {role.name} 分配权限: {permission.name}")
                role.permissions.append(permission)
                added_permissions.append(permission)
            else:
                logger.info(f"角色 {role.name} 已经拥有权限: {permission.name}")

        # 使用 try/except 包装 commit 操作
        try:
            await db.commit()
        except Exception as commit_error:
            logger.error(f"提交事务失败: {commit_error}")
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"提交事务失败: {str(commit_error)}"
            )

        return {
            "message": f"已成功为角色 '{role.name}' 分配权限 '{', '.join(permission.name for permission in added_permissions)} '"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"分配权限失败: {e}")
        raise HTTPException(status_code=500, detail=f"分配权限失败: {str(e)}")


#  查看用户的角色
@router.get("/user_role/{username}", response_model=UserRolesResponse)
async def get_user_roles(username: str, db: AsyncSessionDep):
    """获取用户的角色"""
    try:
        # 获取用户及其角色
        user_repo = UserRepository(db)
        user = await user_repo.get_one_by_username_with_roles(username)

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"用户 '{username}' 不存在"
            )


        return UserRolesResponse(username=user.username, role_names =  [role.name.value for role in user.roles])

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户角色失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取用户角色失败: {str(e)}")




# 获取当前用户权限
@router.get("/get_current_user_permissions", response_model=UserPermissionsResponse)
async def get_current_user_permissions(permissions: list[Permission] = Depends(get_user_permission),
                                       username: str = Depends(get_current_user)):
    """获取当前用户的所有权限"""
    # 将 Permission 对象转换为 PermissionResponse
    permission_responses = [
        PermissionResponse(
            id=perm.id,
            name=perm.name,
            description=perm.description
        ) for perm in permissions
    ]
    return UserPermissionsResponse(username=username, permissions=permission_responses)


# 查看角色的权限
@router.get("/role_permissions/{role_name}", response_model=RolePermissionsResponse)
async def get_role_permissions(role_name: str, db: AsyncSessionDep):
    """获取角色的所有权限"""
    try:
        # 获取角色及其权限
        role_repo = RoleRepository(db)
        role = await role_repo.get_one_by_role_name_with_permissions(role_name)

        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"角色 '{role_name}' 不存在"
            )

        permission_responses = [
            PermissionResponse(
                id=perm.id,
                name=perm.name,
                description=perm.description
            ) for perm in role.permissions
        ]
        return RolePermissionsResponse(role_name=role.name, permissions=permission_responses)


    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取角色权限失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取角色权限失败: {str(e)}")


# 从角色中移除权限
@router.post("/remove_permissions_from_role", dependencies=[Security(check_user_permission, scopes=["manage_users","delete_record"])])
async def remove_permissions_from_role(assignment: RolePermissionRemove, db: AsyncSessionDep):
    """
    从角色中移除权限
    
    需要 manage_users 权限
    """
    try:
        # 获取角色及其权限
        role_repo = RoleRepository(db)
        role = await role_repo.get_one_by_role_name_with_permissions(assignment.role_name)

        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"角色 '{assignment.role_name}' 不存在"
            )

        # 获取所有指定的权限
        permission_repo = PermissionRepository(db)
        removed_permissions = []
        for perm_id in assignment.permission_ids:
            permission = await permission_repo.get_by_id(perm_id)
            if not permission:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"权限ID {perm_id} 不存在"
                )
            logger.info(f"获取权限: {permission.name}")

            # 检查权限是否存在于角色的权限列表中
            if permission in role.permissions:
                logger.info(f"从角色 {role.name} 移除权限: {permission.name}")
                role.permissions.remove(permission)
                removed_permissions.append(permission)
            else:
                logger.info(f"角色 {role.name} 没有权限: {permission.name}")

        # 提交更改
        await db.commit()

        if not removed_permissions:
            return {"message": f"角色 '{role.name}' 没有需要移除的权限"}

        # 构建权限名称列表
        removed_perm = [f"{perm.id}:{perm.name}"  for perm in removed_permissions]


        return {
            "message": f"已成功从角色 '{role.name}' 移除权限 '{', '.join(removed_perm)}'"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"移除权限失败: {e}")
        raise HTTPException(status_code=500, detail=f"移除权限失败: {str(e)}")


# 从用户中删除角色
@router.post("/remove_roles_from_user", dependencies=[Security(check_user_permission, scopes=["manage_users","delete_record"])])
async def remove_roles_from_user(assignment: UserRoleRemove, db: AsyncSessionDep):
    """
    从用户中移除角色

    需要 manage_users 权限
    """
    try:
        # 获取用户及其角色
        user_repo = UserRepository(db)
        user = await user_repo.get_one_by_username_with_roles(assignment.username)

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"用户 '{assignment.username}' 不存在"
            )
        logger.info(f"获取用户: {user}")

        # 获取要移除的角色
        role_repo = RoleRepository(db)
        removed_roles = []
        for role_name in assignment.role_names:
            role = await role_repo.get_one_by_name(role_name)

            if not role:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"角色 '{role_name}' 不存在"
                )
            logger.info(f"获取角色: {role.name.value}")

            # 检查用户是否已经拥有该角色
            for role in user.roles:
                logger.info(f"从用户 {user.username} 移除角色: {role.name}")
                user.roles.remove(role)
                removed_roles.append(role)
            else:
                logger.info(f"用户 {user.username} 没有角色: {role.name}")

        # 提交更改
        await db.commit()

        if not removed_roles:
            return {"message": f"用户 '{user.username}' 没有需要移除的角色"}

        # 构建角色名称列表
        removed_role_list = [role.name.value for role in removed_roles]

        return {
            "message": f"已成功从用户 '{user.username}' 移除角色 '{', '.join(removed_role_list)}'"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"移除角色失败: {e}")
        raise HTTPException(status_code=500, detail=f"移除角色失败: {str(e)}")



# 修改用户密码
@router.post("/update_password")
async def update_password(password_data: UserUpdate, db: AsyncSessionDep, current_user: str = Depends(get_current_user)):
    """
    修改用户密码

    用户需要提供当前用户名和密码进行验证，然后可以更新密码
    """
    try:

        # 检查是否是当前用户
        if password_data.username != current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="您没有权限修改此用户的密码",
                headers={"WWW-Authenticate": "Bearer"},
                )

        # 验证当前用户名和密码
        user = await verify_user(password_data.username, password_data.current_password, db)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码不正确",
                headers={"WWW-Authenticate": "Bearer"},
            )


        # 检查新密码是否与当前密码相同
        if verify_password(password_data.new_password, user.password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="新密码不能与当前密码相同",
                headers={"WWW-Authenticate": "Bearer"},
                )

        # 加密新密码
        hashed_password = get_password_hash(password_data.new_password)

        # 更新用户密码
        user_repo = UserRepository(db)
        updated_user = await user_repo.update(user.id, {"password": hashed_password})

        # 添加提交事务
        await db.commit()

        if not updated_user:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="更新密码失败",
            )

        return {"message": "密码更新成功"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新密码失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新密码失败: {str(e)}")



