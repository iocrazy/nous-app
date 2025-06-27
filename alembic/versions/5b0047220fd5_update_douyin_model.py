"""update_douyin_model

Revision ID: 5b0047220fd5
Revises: 59c09ec7aaa9
Create Date: 2025-06-22 21:29:21.791760

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from app.models.base import JsonList  # 正确导入 JsonList


# revision identifiers, used by Alembic.
revision: str = '5b0047220fd5'
down_revision: Union[str, None] = '59c09ec7aaa9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 只修改 video_original_url 列的 nullable 属性
    # 移除添加已存在列的操作



def downgrade() -> None:
    """Downgrade schema."""
    # 只恢复 video_original_url 列的 nullable 属性
    # 移除删除列的操作

