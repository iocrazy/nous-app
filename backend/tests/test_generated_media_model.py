"""Test for GeneratedMedia ORM model."""


def test_generated_media_model_shape():
    from app.models import GeneratedMedia

    cols = set(GeneratedMedia.__table__.columns.keys())
    assert {
        "id",
        "scope_id",
        "creator_id",
        "media_kind",
        "file_path",
        "origin_kind",
        "origin_run_id",
        "canvas_id",
        "params",
        "promoted_resource_id",
        "created_at",
    } <= cols
    assert GeneratedMedia.__tablename__ == "generated_media"
