from bot.constants import MAX_MESSAGE_LEN
from bot.ui import chunk_lines


def test_short_list_is_one_message():
    assert chunk_lines(["- a", "  - b"]) == ["- a\n  - b"]


def test_long_list_is_split_between_lines():
    lines = [f"- <@{i:018d}>" for i in range(200)]
    chunks = chunk_lines(lines)
    assert len(chunks) > 1
    assert all(len(c) <= MAX_MESSAGE_LEN for c in chunks)
    assert "\n".join(chunks).split("\n") == lines  # nothing lost, nothing split


def test_empty_list_sends_nothing():
    assert chunk_lines([]) == []
