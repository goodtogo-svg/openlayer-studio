import numpy as np
import pytest
from ali.occlusion import partition_support, depth_order


def test_hidden_gift_does_not_cut_hole_in_architecture():
    support = np.zeros((20, 20), bool)
    support[2:18, 3:19] = True
    gift = np.zeros_like(support)
    gift[9:19, 11:] = True
    result = partition_support(support, gift)
    assert result['inferred'][12, 14]
    assert not result['visible'][12, 14]
    assert np.array_equal(result['visible'] | result['inferred'], support)
    assert not (result['visible'] & result['inferred']).any()


def test_real_key_hole_is_not_invented_support():
    support = np.ones((10, 10), bool)
    support[3:7, 3:7] = False
    result = partition_support(support, np.ones_like(support))
    assert not result['amodal'][5, 5]


def test_depth_cycle_and_dimension_errors_are_explicit():
    with pytest.raises(ValueError):
        partition_support(np.zeros((4, 3)), np.zeros((3, 4)))
    with pytest.raises(ValueError, match='cycle'):
        depth_order(['portal', 'gift'], [('portal', 'gift'), ('gift', 'portal')])
    assert depth_order(['gift', 'portal'], [('portal', 'gift')]) == ['portal', 'gift']
