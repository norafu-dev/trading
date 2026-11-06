"""spec 0013 AC-6：搜索文字的生成（纯函数）。"""

from harvester.db.search_text import build_search_text
from harvester.db.snapshots import EmbedData, ForwardedData


def test_discord_markup_is_cleaned() -> None:  # covers: AC-6
    text = build_search_text(
        "Hi <@123> <@&9> <#5> <t:1700000000:R> <:PePe:777> <a:dance:8> </ping:42>  BTC\nLong",
        None,
        None,
        [],
    )
    assert text == "hi :pepe: :dance: /ping btc long"
    assert "123" not in text


def test_embed_forwarded_and_attachment_parts() -> None:  # covers: AC-6
    embed: EmbedData = {
        "title": "Title",
        "description": "Desc",
        "fields": [{"name": "FName", "value": "FValue"}],
        "author": {"name": "Auth"},
        "footer_text": "Foot",
    }
    forwarded: ForwardedData = {
        "message_id": 1,
        "channel_id": 2,
        "guild_id": None,
        "content": "Fwd body",
        "embeds": [{"title": "FwdEmbed"}],
        "attachment_names": ["Chart.PNG"],
    }
    text = build_search_text("body", forwarded, [embed], ["report.PDF"])
    assert text.split("\n") == [
        "body",
        "title",
        "desc",
        "fname",
        "fvalue",
        "auth",
        "foot",
        "fwd body",
        "fwdembed",
        "chart.png",
        "report.pdf",
    ]


def test_empty_parts_are_skipped() -> None:
    assert build_search_text("   ", None, [{"title": ""}], []) == ""
