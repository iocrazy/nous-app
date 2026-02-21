# app/tasks/__init__.py

"""
Celery 任务模块

所有 @shared_task 由 celery_app.autodiscover_tasks() 自动发现，
新增 task 文件无需在此手动注册。
"""
