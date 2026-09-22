from backend.llm.provider import NullProvider
from backend.models import PostContext
from backend.post_generator import generate_draft, revise_post


def test_generate_draft_with_null_provider_does_not_crash():
    ctx = PostContext(reason_for_page="My brother had surgery this week.")
    draft = generate_draft(ctx, "", NullProvider())
    assert isinstance(draft, str)
    assert len(draft) > 0


def test_revise_post_with_null_provider_echoes_something():
    ctx = PostContext(reason_for_page="reason")
    revised = revise_post("Original post text.", "make it shorter", ctx, NullProvider())
    assert isinstance(revised, str)
