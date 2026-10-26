from ghost_audit.assign import hungarian_max


def test_global_optimum_beats_greedy():
    # greedy would give row0->col0 (5) and leave row1 with col1 (1) = 6; optimum is 4+4 = 8 ... plus 2 matched rows
    w = [[5, 4], [4, 1]]
    assert sorted(hungarian_max(w)) == [(0, 1), (1, 0)]


def test_forbidden_pairs_and_rectangular():
    w = [[None, 2.0, None], [None, None, 3.0]]
    assert sorted(hungarian_max(w)) == [(0, 1), (1, 2)]
    assert hungarian_max([[None, None]]) == []


def test_matches_as_many_rows_as_possible():
    w = [[1.0, None], [1.0, None]]
    assert len(hungarian_max(w)) == 1
