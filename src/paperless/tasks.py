from celery import shared_task

from paperless.remote_import import run_remote_import_task


@shared_task(bind=True)
def import_remote_documents(
    self,
    *,
    base_url: str,
    api_token: str,
    selected_document_ids: list[int] | None = None,
    query: str = "",
    import_all: bool = False,
    create_missing_items: bool = True,
    import_notes: bool = True,
    owner_id: int | None = None,
):
    return run_remote_import_task(
        task_id=self.request.id,
        base_url=base_url,
        api_token=api_token,
        selected_document_ids=selected_document_ids,
        query=query,
        import_all=import_all,
        create_missing_items=create_missing_items,
        import_notes=import_notes,
        owner_id=owner_id,
    )
