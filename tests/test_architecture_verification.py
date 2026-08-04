from evaluation.architecture_verification import verify


def test_import_boundaries_are_clean():
    assert verify() == []
