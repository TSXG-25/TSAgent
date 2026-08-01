"""Fuzzy filename matcher — Phase 1 scope: exact, prefix, substring.

No Levenshtein, no BM25, no embedding. Keep it simple.
"""
from pathlib import Path
from agent.workspace import PathMatch, MatchSource


def match_filename(name: str, candidates: list[Path]) -> list[PathMatch]:
    """Match a name against a list of file paths.

    Phase 1 scope: exact → prefix → substring.
    Returns sorted PathMatch list.
    """
    name_lower = name.lower()
    name_stem = Path(name).stem.lower()
    # 含路径分隔符的 spec 是路径语义：只做路径子串匹配，
    # 不做 stem 匹配——否则 "repo/x/main.py" 会命中任何同名 main.py。
    is_path_spec = "/" in name or "\\" in name
    results: list[PathMatch] = []

    for path in candidates:
        stem = path.stem.lower()
        full_lower = path.name.lower()

        # Exact match on stem
        if stem == name_lower or (not is_path_spec and stem == name_stem):
            results.append(PathMatch(
                path=path,
                score=1.0,
                source=MatchSource.EXACT,
                reason=f"Exact filename match: {path.name}",
            ))
            continue

        # Exact match on full filename
        if full_lower == name_lower:
            results.append(PathMatch(
                path=path,
                score=0.95,
                source=MatchSource.EXACT,
                reason=f"Exact filename match: {path.name}",
            ))
            continue

        # Prefix match on stem
        if stem.startswith(name_lower) or (not is_path_spec and stem.startswith(name_stem)):
            results.append(PathMatch(
                path=path,
                score=0.8,
                source=MatchSource.PREFIX,
                reason=f"Filename prefix match: {path.name} starts with '{name}'",
            ))
            continue

        # Substring match on stem
        if name_lower in stem or (not is_path_spec and name_stem in stem):
            results.append(PathMatch(
                path=path,
                score=0.6,
                source=MatchSource.FUZZY,
                reason=f"Filename substring match: '{name}' found in {path.name}",
            ))
            continue

        # Substring match in full path (lower priority)
        if name_lower in str(path).lower():
            results.append(PathMatch(
                path=path,
                score=0.4,
                source=MatchSource.FUZZY,
                reason=f"Path substring match: '{name}' found in path",
            ))

    # Sort: score DESC → source priority → path ASC
    source_priority = {s: i for i, s in enumerate(MatchSource)}
    results.sort(key=lambda m: (-m.score, source_priority.get(m.source, 99), str(m.path)))

    return results