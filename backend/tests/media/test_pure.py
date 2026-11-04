"""media 包的纯函数和缩略图（spec 0011）。"""

from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from harvester.media import policy
from harvester.media.thumbnail import inspect_image, needs_thumbnail
from harvester.media.urls import (
    discord_attachment_id,
    file_prefix,
    is_discord_hosted,
    link_expires_at,
    object_key,
    strip_query,
    thumb_key,
)

HOSTED = "https://cdn.discordapp.com/attachments/1/22/a.png?ex=69000000&is=1&hm=2"


def test_hosted_rule() -> None:  # covers: AC-2
    assert is_discord_hosted(HOSTED)
    assert is_discord_hosted("https://media.discordapp.net/ephemeral-attachments/1/2/a.png")
    for bad in (
        "https://example.com/a.png",
        "http://cdn.discordapp.com/attachments/1/2/a.png",
        "https://cdn.discordapp.com.evil.com/attachments/1/2/a.png",
        "https://media.discordapp.net/external/abc/a.png",
        "https://cdn.discordapp.com/avatars/1/a.png",
        "not a url",
    ):
        assert not is_discord_hosted(bad)


def test_url_helpers() -> None:
    assert strip_query(HOSTED) == "https://cdn.discordapp.com/attachments/1/22/a.png"
    assert discord_attachment_id(HOSTED) == 22
    assert link_expires_at(HOSTED) == datetime.fromtimestamp(0x69000000, tz=UTC)
    assert link_expires_at("https://cdn.discordapp.com/attachments/1/2/a.png") is None
    assert link_expires_at("https://cdn.discordapp.com/attachments/1/2/a.png?ex=zz") is None


def test_storage_keys() -> None:  # covers: AC-4
    prefix = file_prefix(123, datetime(2026, 11, 30, 23, 59, tzinfo=UTC))
    assert prefix == "123/2026-11"
    assert object_key(prefix, "ab", "Shot.PNG") == "123/2026-11/ab.png"
    assert object_key(prefix, "ab", "noext") == "123/2026-11/ab"
    assert object_key(prefix, "ab", "x.t?x*t") == "123/2026-11/ab.txt"
    assert object_key(prefix, "ab", "x." + "a" * 30) == "123/2026-11/ab." + "a" * 10
    assert thumb_key(prefix, "ab") == "123/2026-11/thumbs/ab.webp"


def test_backoff_and_classification() -> None:  # covers: AC-10
    minutes = [policy.next_backoff(n).total_seconds() / 60 for n in (1, 2, 3, 4)]
    assert minutes == [1, 5, 30, 120]
    classify = policy.classify_download_error
    assert classify(None, recently_refreshed=False) == "retry"
    assert classify(503, recently_refreshed=False) == "retry"
    assert classify(429, recently_refreshed=True) == "retry"
    assert classify(403, recently_refreshed=False) == "refresh"
    assert classify(403, recently_refreshed=True) == "retry"
    assert classify(404, recently_refreshed=False) == "refresh"
    assert classify(404, recently_refreshed=True) == "gone"
    assert classify(302, recently_refreshed=False) == "gone"
    assert classify(410, recently_refreshed=False) == "gone"


def test_admission() -> None:  # covers: AC-6, AC-7
    assert policy.admit(10, 50, 0, 100) == "ok"
    assert policy.admit(51, 50, 0, 100) == "too_large"
    assert policy.admit(None, 50, 100, 100) == "no_space"
    assert policy.admit(60, 50, 100, 100) == "too_large"


def test_content_disposition() -> None:  # covers: AC-14
    assert policy.content_disposition("image/png", "a.png", download=False) == "inline"
    assert policy.content_disposition("audio/ogg", "a.ogg", download=False) == "inline"
    svg = policy.content_disposition("image/svg+xml", "x.svg", download=False)
    assert svg.startswith("attachment;")
    forced = policy.content_disposition("image/png", "a.png", download=True)
    assert forced.startswith('attachment; filename="a.png"')
    nasty = policy.content_disposition("text/html", 'a"\r\nSet-Cookie: x\\.html', download=False)
    assert "\r" not in nasty and "\n" not in nasty and '\\"' not in nasty
    unicode_name = policy.content_disposition("text/plain", "设计稿.txt", download=True)
    assert 'filename="___.txt"' in unicode_name
    assert "filename*=UTF-8''%E8%AE%BE%E8%AE%A1%E7%A8%BF.txt" in unicode_name


def test_link_refresh_margin() -> None:  # covers: AC-11
    now = datetime.fromtimestamp(0x69000000, tz=UTC)
    assert policy.link_needs_refresh(HOSTED, now)  # 正好过期
    far = datetime.fromtimestamp(0x69000000 - 3600, tz=UTC)
    assert not policy.link_needs_refresh(HOSTED, far)
    soon = datetime.fromtimestamp(0x69000000 - 200, tz=UTC)
    assert policy.link_needs_refresh(HOSTED, soon)


def test_thumbnail_rules(tmp_path: Path) -> None:  # covers: AC-5
    assert needs_thumbnail(801, 10)
    assert not needs_thumbnail(800, 800)
    assert not needs_thumbnail(5001, 5001)  # 超过 2500 万像素：只存原图

    big = tmp_path / "big.png"
    Image.new("RGB", (1600, 400), "red").save(big)
    info = inspect_image(big, tmp_path / "t.webp")
    assert info is not None and info.content_type == "image/png"
    assert info.thumb is not None and (info.thumb.width, info.thumb.height) == (800, 200)
    assert (tmp_path / "t.webp").stat().st_size == info.thumb.size_bytes

    small = tmp_path / "small.png"
    Image.new("RGB", (100, 100)).save(small)
    small_info = inspect_image(small, tmp_path / "s.webp")
    assert small_info is not None and small_info.thumb is None

    gif = tmp_path / "anim.gif"
    first = Image.new("P", (900, 900))
    first.save(gif, save_all=True, append_images=[Image.new("P", (900, 900))])
    gif_info = inspect_image(gif, tmp_path / "g.webp")
    assert gif_info is not None and gif_info.thumb is not None

    # 扩展名骗人：以 Pillow 识别的格式为准。
    jpeg_named_png = tmp_path / "x.png"
    Image.new("RGB", (50, 50)).save(jpeg_named_png, format="JPEG")
    liar = inspect_image(jpeg_named_png, tmp_path / "l.webp")
    assert liar is not None and liar.content_type == "image/jpeg"

    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"not an image")
    assert inspect_image(junk, tmp_path / "j.webp") is None
