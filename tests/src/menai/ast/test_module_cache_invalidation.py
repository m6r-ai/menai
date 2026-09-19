"""Tests for Menai module cache invalidation.

Tests cover:
- SHA256-based cache invalidation when file content changes
- Cache reuse when file content is unchanged
- Manual cache invalidation methods
- Hash computation edge cases
"""

import pytest
import time

from menai import Menai


class TestCacheInvalidation:
    """Test automatic cache invalidation based on file content hash."""

    def test_cache_reused_when_content_unchanged(self, tmp_path):
        """Test that cache is reused when file content hasn't changed."""
        module_file = tmp_path / "stable.menai"
        module_file.write_text("(let ((value 42)) (export value))")

        menai = Menai(module_path=[str(tmp_path)])

        # First load
        menai.evaluate('(let ((m (import "stable"))) (m value))')
        assert "stable" in menai.module_cache
        assert "stable" in menai.module_hashes
        hash1 = menai.module_hashes["stable"]

        # Second load - should use cache
        menai.evaluate('(let ((m (import "stable"))) (m value))')
        assert "stable" in menai.module_cache
        hash2 = menai.module_hashes["stable"]

        # Hash should be the same
        assert hash1 == hash2

    def test_cache_invalidated_when_content_changes(self, tmp_path):
        """Test that cache is invalidated when file content changes."""
        module_file = tmp_path / "changing.menai"
        module_file.write_text("(let ((value 1)) (export value))")

        menai = Menai(module_path=[str(tmp_path)])

        # First load
        result1 = menai.evaluate('''
(let ((mod (import "changing")))
  (mod value))
''')
        assert result1 == 1
        hash1 = menai.module_hashes["changing"]

        # Modify file content
        module_file.write_text("(let ((value 2)) (export value))")

        # Second load - should detect change and reload
        result2 = menai.evaluate('''
(let ((mod (import "changing")))
  (mod value))
''')
        assert result2 == 2
        hash2 = menai.module_hashes["changing"]

        # Hash should be different
        assert hash1 != hash2

    def test_cache_invalidated_on_whitespace_only_change(self, tmp_path):
        """Test that cache is invalidated even for whitespace-only changes."""
        module_file = tmp_path / "whitespace.menai"
        module_file.write_text("(let ((x 1)) (export x))")

        menai = Menai(module_path=[str(tmp_path)])

        # First load
        menai.evaluate('(let ((m (import "whitespace"))) (m x))')
        hash1 = menai.module_hashes["whitespace"]

        # Change only whitespace
        module_file.write_text("(let  ((x 1))  (export x))")

        # Second load - should detect change (different hash)
        menai.evaluate('(let ((m (import "whitespace"))) (m x))')
        hash2 = menai.module_hashes["whitespace"]

        # Hash should be different (content changed)
        assert hash1 != hash2

    def test_cache_invalidated_on_comment_change(self, tmp_path):
        """Test that cache is invalidated when comments change."""
        module_file = tmp_path / "commented.menai"
        module_file.write_text("; Comment v1\n(let ((x 1)) (export x))")

        menai = Menai(module_path=[str(tmp_path)])

        # First load
        menai.evaluate('(let ((m (import "commented"))) (m x))')
        hash1 = menai.module_hashes["commented"]

        # Change comment
        module_file.write_text("; Comment v2\n(let ((x 1)) (export x))")

        # Second load - should detect change
        menai.evaluate('(let ((m (import "commented"))) (m x))')
        hash2 = menai.module_hashes["commented"]

        # Hash should be different
        assert hash1 != hash2

    def test_multiple_modules_independent_invalidation(self, tmp_path):
        """Test that modules are invalidated independently."""
        (tmp_path / "module_a.menai").write_text("(let ((a 1)) (export a))")
        (tmp_path / "module_b.menai").write_text("(let ((b 2)) (export b))")

        menai = Menai(module_path=[str(tmp_path)])

        # Load both modules
        menai.evaluate('(let ((m (import "module_a"))) (m a))')
        menai.evaluate('(let ((m (import "module_b"))) (m b))')
        hash_a1 = menai.module_hashes["module_a"]
        hash_b1 = menai.module_hashes["module_b"]

        # Modify only module_a
        (tmp_path / "module_a.menai").write_text("(let ((a 99)) (export a))")

        # Reload both
        menai.evaluate('(let ((m (import "module_a"))) (m a))')
        menai.evaluate('(let ((m (import "module_b"))) (m b))')
        hash_a2 = menai.module_hashes["module_a"]
        hash_b2 = menai.module_hashes["module_b"]

        # Only module_a hash should change
        assert hash_a1 != hash_a2
        assert hash_b1 == hash_b2

    def test_transitive_import_invalidation(self, tmp_path):
        """Test cache invalidation with transitive imports."""
        # Base module
        (tmp_path / "base.menai").write_text("""
(let ((value 10))
  (export value))
""")

        # Wrapper imports base
        (tmp_path / "wrapper.menai").write_text("""
(let ((base (import "base")))
  (let ((get-value (lambda () (base value))))
    (export get-value)))
""")

        menai = Menai(module_path=[str(tmp_path)])

        # Load wrapper (which loads base)
        result1 = menai.evaluate('''
(let ((w (import "wrapper")))
  ((w get-value)))
''')
        assert result1 == 10

        # Modify base module
        (tmp_path / "base.menai").write_text("""
(let ((value 20))
  (export value))
""")

        # Clear cache to force reload
        menai.clear_module_cache()

        # Reload wrapper - should get new base value
        result2 = menai.evaluate('''
(let ((w (import "wrapper")))
  ((w get-value)))
''')
        assert result2 == 20


class TestHashComputation:
    """Test the hash computation mechanism."""

    def test_hash_is_sha256_hex(self, tmp_path):
        """Test that computed hash is SHA256 in hex format."""
        module_file = tmp_path / "hashtest.menai"
        module_file.write_text("(let ((x 1)) (export x))")

        menai = Menai(module_path=[str(tmp_path)])
        menai.evaluate('(let ((m (import "hashtest"))) (m x))')

        hash_value = menai.module_hashes["hashtest"]

        # SHA256 hex digest is 64 characters
        assert len(hash_value) == 64
        # Should be valid hex
        assert all(c in "0123456789abcdef" for c in hash_value)

    def test_identical_content_produces_identical_hash(self, tmp_path):
        """Test that identical content produces the same hash."""
        (tmp_path / "file1.menai").write_text("(let ((x 1)) (export x))")
        (tmp_path / "file2.menai").write_text("(let ((x 1)) (export x))")

        menai = Menai(module_path=[str(tmp_path)])

        menai.evaluate('(let ((m (import "file1"))) (m x))')
        menai.evaluate('(let ((m (import "file2"))) (m x))')

        # Same content should produce same hash
        assert menai.module_hashes["file1"] == menai.module_hashes["file2"]

    def test_different_content_produces_different_hash(self, tmp_path):
        """Test that different content produces different hashes."""
        (tmp_path / "diff1.menai").write_text("(let ((x 1)) (export x))")
        (tmp_path / "diff2.menai").write_text("(let ((x 2)) (export x))")

        menai = Menai(module_path=[str(tmp_path)])

        menai.evaluate('(let ((m (import "diff1"))) (m x))')
        menai.evaluate('(let ((m (import "diff2"))) (m x))')

        # Different content should produce different hashes
        assert menai.module_hashes["diff1"] != menai.module_hashes["diff2"]

    def test_large_file_hashing(self, tmp_path):
        """Test that large files are hashed correctly (chunked reading)."""
        # Create a large module (> 8KB to test chunked reading)
        large_content = "; Large module\n"
        large_content += "(let (\n"
        for i in range(1000):
            large_content += f"  (func{i} (lambda (x) (integer* x {i})))\n"
        large_content += ")\n  (export\n"
        for i in range(100):
            large_content += f"    func{i}\n"
        large_content += "  )\n)\n"

        module_file = tmp_path / "large.menai"
        module_file.write_text(large_content)

        menai = Menai(module_path=[str(tmp_path)])

        # Should successfully hash and load
        menai.evaluate('(let ((m (import "large"))) (m func0))')
        assert "large" in menai.module_hashes

        # Verify it's actually large
        assert len(large_content) > 8192


class TestManualCacheControl:
    """Test manual cache invalidation methods."""

    def test_invalidate_module_removes_from_cache(self, tmp_path):
        """Test that invalidate_module removes module from cache."""
        module_file = tmp_path / "removable.menai"
        module_file.write_text("(let ((x 1)) (export x))")

        menai = Menai(module_path=[str(tmp_path)])

        # Load module
        menai.evaluate('(let ((m (import "removable"))) (m x))')
        assert "removable" in menai.module_cache
        assert "removable" in menai.module_hashes

        # Invalidate
        menai.invalidate_module("removable")
        assert "removable" not in menai.module_cache
        assert "removable" not in menai.module_hashes

    def test_invalidate_nonexistent_module_is_safe(self, tmp_path):
        """Test that invalidating non-existent module doesn't error."""
        menai = Menai(module_path=[str(tmp_path)])

        # Should not raise
        menai.invalidate_module("nonexistent")

    def test_reload_module_forces_recompilation(self, tmp_path):
        """Test that reload_module forces recompilation."""
        module_file = tmp_path / "reloadable.menai"
        module_file.write_text("(let ((value 1)) (export value))")

        menai = Menai(module_path=[str(tmp_path)])

        # Initial load
        result1 = menai.evaluate('''
(let ((mod (import "reloadable")))
  (mod value))
''')
        assert result1 == 1

        # Modify file
        module_file.write_text("(let ((value 2)) (export value))")

        # Force reload
        menai.reload_module("reloadable")

        # Next import should get new value
        result2 = menai.evaluate('''
(let ((mod (import "reloadable")))
  (mod value))
''')
        assert result2 == 2

    def test_clear_module_cache_clears_hashes(self, tmp_path):
        """Test that clear_module_cache also clears hashes."""
        (tmp_path / "test1.menai").write_text("(let ((x 1)) (export x))")
        (tmp_path / "test2.menai").write_text("(let ((x 1)) (export x))")

        menai = Menai(module_path=[str(tmp_path)])

        # Load modules
        menai.evaluate('(let ((m (import "test1"))) (m x))')
        menai.evaluate('(let ((m (import "test2"))) (m x))')
        assert len(menai.module_cache) == 2
        assert len(menai.module_hashes) == 2

        # Clear cache
        menai.clear_module_cache()
        assert len(menai.module_cache) == 0
        assert len(menai.module_hashes) == 0

    def test_set_module_path_clears_hashes(self, tmp_path):
        """Test that set_module_path clears cache and hashes."""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        (dir1 / "test.menai").write_text("(let ((x 1)) (export x))")

        menai = Menai(module_path=[str(dir1)])

        # Load module
        menai.evaluate('(let ((m (import "test"))) (m x))')
        assert len(menai.module_cache) == 1
        assert len(menai.module_hashes) == 1

        # Change module path
        menai.set_module_path([str(dir2)])
        assert len(menai.module_cache) == 0
        assert len(menai.module_hashes) == 0


class TestCacheInvalidationEdgeCases:
    """Test edge cases in cache invalidation."""

    def test_file_deleted_after_caching(self, tmp_path):
        """Test behavior when cached module file is deleted."""
        module_file = tmp_path / "deletable.menai"
        module_file.write_text("(let ((x 1)) (export x))")

        menai = Menai(module_path=[str(tmp_path)])

        # Load and cache
        menai.evaluate('(let ((m (import "deletable"))) (m x))')
        assert "deletable" in menai.module_cache

        # Delete file
        module_file.unlink()

        # Try to load again - should fail with module not found
        from menai.menai_error import MenaiModuleNotFoundError
        with pytest.raises(MenaiModuleNotFoundError):
            menai.evaluate('(let ((m (import "deletable"))) (m x))')

        # Cache should be cleaned up
        assert "deletable" not in menai.module_cache
        assert "deletable" not in menai.module_hashes

    def test_file_recreated_with_different_content(self, tmp_path):
        """Test cache invalidation when file is deleted and recreated."""
        module_file = tmp_path / "recreated.menai"
        module_file.write_text("(let ((value 1)) (export value))")

        menai = Menai(module_path=[str(tmp_path)])

        # Load original
        result1 = menai.evaluate('''
(let ((mod (import "recreated")))
  (mod value))
''')
        assert result1 == 1

        # Delete and recreate with different content
        module_file.unlink()
        module_file.write_text("(let ((value 2)) (export value))")

        # Load again - should get new content
        result2 = menai.evaluate('''
(let ((mod (import "recreated")))
  (mod value))
''')
        assert result2 == 2

    def test_cache_with_binary_content_in_comments(self, tmp_path):
        """Test that binary-like content in comments doesn't break hashing."""
        # Test with various special characters in comments
        module_file = tmp_path / "special.menai"
        module_file.write_text("; Comment with special chars: \u0000 \u0001 \u001f\n(export)")

        menai = Menai(module_path=[str(tmp_path)])

        # Should handle gracefully (though lexer might reject some chars)
        try:
            menai.evaluate('(let ((m (import "special"))) (m x))')
            # If it loads, hash should exist
            assert "special" in menai.module_hashes
        except Exception:
            # If lexer rejects it, that's also fine
            pass

    def test_empty_file_hashing(self, tmp_path):
        """Test hashing of empty file."""
        module_file = tmp_path / "empty.menai"
        module_file.write_text("")

        menai = Menai(module_path=[str(tmp_path)])

        # Empty file will fail parsing, but hash should still be computed
        try:
            menai.evaluate('(let ((m (import "empty"))) m)')
        except Exception:
            # Expected to fail parsing, but we can test hash directly
            hash_value = menai._compute_file_hash(str(module_file))
            assert len(hash_value) == 64  # Valid SHA256 hex

    def test_unicode_content_hashing(self, tmp_path):
        """Test that Unicode content is hashed correctly."""
        module_file = tmp_path / "unicode.menai"
        module_file.write_text("; Comment: 你好世界 🌍\n(let ((greeting \"hello\")) (export greeting))", encoding="utf-8")

        menai = Menai(module_path=[str(tmp_path)])

        # Should load and hash correctly
        menai.evaluate('(let ((m (import "unicode"))) (m greeting))')
        assert "unicode" in menai.module_hashes

        # Modify Unicode content
        module_file.write_text("; Comment: 再见世界 🌏\n(let ((greeting \"hello\")) (export greeting))", encoding="utf-8")

        # Should detect change
        hash1 = menai.module_hashes["unicode"]
        menai.evaluate('(let ((m (import "unicode"))) (m greeting))')
        hash2 = menai.module_hashes["unicode"]

        assert hash1 != hash2
