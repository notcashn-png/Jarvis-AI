import pytest

from titan.memory import MAX_FILE_BYTES, MemoryError_, MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory")


# ---- the security boundary -------------------------------------------------


@pytest.mark.parametrize(
    "bad_path",
    [
        "../escape.md",
        "../../etc/passwd",
        "projects/../../escape.md",
        "/etc/passwd",
        "/tmp/absolute.md",
        "..",
        ".",
        "./../x.md",
        "a/../../b.md",
        "",
        "   ",
        "notes/../../../x.md",
        "\\..\\windows.md",
    ],
)
def test_traversal_and_absolute_paths_are_rejected(store, bad_path):
    for op in (
        lambda: store.read(bad_path),
        lambda: store.write(bad_path, "x"),
        lambda: store.append(bad_path, "x"),
        lambda: store.delete(bad_path),
    ):
        with pytest.raises(MemoryError_):
            op()


def test_traversal_does_not_create_files_outside_root(store, tmp_path):
    victim = tmp_path / "victim.md"
    with pytest.raises(MemoryError_):
        store.write("../victim.md", "owned")
    assert not victim.exists()


def test_symlink_pointing_outside_root_is_rejected(store, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    (store.root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(MemoryError_):
        store.read("link/secret.md")


def test_symlinked_file_is_not_listed(store, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret.md"
    target.write_text("secret", encoding="utf-8")
    (store.root / "sneaky.md").symlink_to(target)

    assert "sneaky.md" not in store.list()


def test_odd_segment_characters_are_rejected(store):
    for bad in ["notes/a b.md", "notes/a$b.md", "notes/a\x00b.md", "notes/a;b.md"]:
        with pytest.raises(MemoryError_):
            store.write(bad, "x")


def test_non_string_path_is_rejected(store):
    with pytest.raises(MemoryError_):
        store.read(None)  # type: ignore[arg-type]


# ---- normal operation ------------------------------------------------------


def test_write_read_roundtrip(store):
    written = store.write("profile.md", "# Profile\nFounder, solo.")
    assert written == "profile.md"
    assert store.read("profile.md") == "# Profile\nFounder, solo."


def test_write_creates_nested_directories(store):
    store.write("projects/newsletter.md", "state: active")
    assert store.read("projects/newsletter.md") == "state: active"
    assert "projects/newsletter.md" in store.list()


def test_write_overwrites(store):
    store.write("goals.md", "old")
    store.write("goals.md", "new")
    assert store.read("goals.md") == "new"


def test_write_leaves_no_temp_files(store):
    store.write("notes/a.md", "x")
    assert store.list() == ["notes/a.md"]


def test_append_creates_then_extends(store):
    store.append("log/2026-07-26.md", "first")
    store.append("log/2026-07-26.md", "second")
    assert store.read("log/2026-07-26.md") == "first\nsecond\n"


def test_append_does_not_double_newline(store):
    store.write("log/x.md", "line\n")
    store.append("log/x.md", "next")
    assert store.read("log/x.md") == "line\nnext\n"


def test_read_missing_file_raises(store):
    with pytest.raises(MemoryError_, match="no such memory file"):
        store.read("nope.md")


def test_delete_removes_file(store):
    store.write("tmp.md", "x")
    assert store.delete("tmp.md") == "tmp.md"
    assert store.list() == []


def test_delete_missing_raises(store):
    with pytest.raises(MemoryError_):
        store.delete("nope.md")


def test_list_is_sorted_and_recursive(store):
    store.write("z.md", "1")
    store.write("a.md", "2")
    store.write("projects/b.md", "3")
    assert store.list() == ["a.md", "projects/b.md", "z.md"]


def test_list_with_prefix(store):
    store.write("projects/a.md", "1")
    store.write("projects/b.md", "2")
    store.write("other.md", "3")
    assert store.list("projects") == ["projects/a.md", "projects/b.md"]


def test_list_missing_prefix_is_empty(store):
    assert store.list("nothing-here") == []


def test_oversized_write_is_rejected(store):
    with pytest.raises(MemoryError_, match="Split this"):
        store.write("big.md", "x" * (MAX_FILE_BYTES + 1))


def test_non_string_content_is_rejected(store):
    with pytest.raises(MemoryError_):
        store.write("x.md", {"not": "a string"})  # type: ignore[arg-type]


# ---- search ----------------------------------------------------------------


def test_search_finds_matches_case_insensitively(store):
    store.write("profile.md", "Runs a Newsletter business")
    store.write("goals.md", "Grow revenue")
    hits = store.search("newsletter")
    assert len(hits) == 1
    assert hits[0].path == "profile.md"
    assert hits[0].line_number == 1


def test_search_reports_no_matches(store):
    store.write("a.md", "nothing relevant")
    assert store.search("quantum") == []


def test_search_respects_limit(store):
    store.write("a.md", "\n".join(["match"] * 100))
    assert len(store.search("match", limit=5)) == 5


def test_empty_search_query_is_rejected(store):
    with pytest.raises(MemoryError_):
        store.search("   ")


# ---- helpers ---------------------------------------------------------------


def test_overview_on_empty_store(store):
    assert "empty" in store.overview().lower()


def test_overview_lists_files(store):
    store.write("profile.md", "x")
    overview = store.overview()
    assert "profile.md" in overview
    assert "1 file" in overview


def test_overview_truncates(store):
    for i in range(10):
        store.write(f"n{i}.md", "x")
    assert "and 7 more" in store.overview(max_entries=3)


def test_today_log_path_shape(store):
    path = store.today_log_path()
    assert path.startswith("log/") and path.endswith(".md")
    store.append(path, "entry")  # must be a writable path
