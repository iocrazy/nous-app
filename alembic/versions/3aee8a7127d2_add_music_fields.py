"""add_music_fields

Revision ID: 3aee8a7127d2
Revises: 2cd3ca7a99f0
Create Date: 2025-06-22 21:13:25.125056

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from app.models.base import JsonList

# revision identifiers, used by Alembic.
revision: str = '3aee8a7127d2'
down_revision: Union[str, None] = '2cd3ca7a99f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 只添加新列


def downgrade() -> None:
    """Downgrade schema."""
    # 只删除新列
