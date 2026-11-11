"""
Tests for the shared line-width clipping helper.

The rendering helpers return complete lines by default.  A command-line tool
that wants a fixed output width clips at the point of printing, so these tests
pin the clipping behaviour independently of any one tool.
"""

from menai_render.menai_render_width import clip, clip_lines


class TestClip:
    """A line is clipped only when a positive width is supplied."""

    def test_none_means_no_clipping(self):
        assert clip("a long line", None) == "a long line"

    def test_short_line_is_unchanged(self):
        assert clip("short", 20) == "short"

    def test_line_at_width_is_unchanged(self):
        assert clip("12345", 5) == "12345"

    def test_long_line_is_clipped_to_width(self):
        assert clip("123456789", 5) == "12345"

    def test_zero_width_means_no_clipping(self):
        assert clip("123456789", 0) == "123456789"

    def test_negative_width_means_no_clipping(self):
        assert clip("123456789", -1) == "123456789"

    def test_ansi_escapes_do_not_count_towards_width(self):
        line = "\x1b[33mabcdef\x1b[0m"
        assert clip(line, 4) == "\x1b[33mabcd\x1b[0m"

    def test_short_coloured_line_is_unchanged(self):
        line = "\x1b[33mabc\x1b[0m"
        assert clip(line, 4) == line

    def test_clipped_coloured_line_is_reset(self):
        line = "\x1b[33mabcdef"
        assert clip(line, 3).endswith("\x1b[0m")


class TestClipLines:
    """A list of lines is clipped line by line."""

    def test_none_means_no_clipping(self):
        lines = ["one", "two"]
        assert clip_lines(lines, None) == lines

    def test_each_line_is_clipped_independently(self):
        assert clip_lines(["12345", "abc", "123456789"], 5) == ["12345", "abc", "12345"]
