import pytest
import os
import tempfile
import shutil
import numpy as np
from unittest.mock import patch, MagicMock
from analyzer_tools.analysis import partial_data_assessor

class TestPartialDataAssessor:
    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.reports_dir = os.path.join(self.test_dir, 'reports')
        os.makedirs(self.reports_dir)
        self.data_dir = os.path.join(self.test_dir, 'data')
        os.makedirs(self.data_dir)

    def teardown_method(self):
        shutil.rmtree(self.test_dir)

    @patch('matplotlib.pyplot.savefig')
    def test_assess_data_set_creates_report_with_metrics(self, mock_savefig):
        # Arrange
        set_id = '218281'
        data_dir = os.path.abspath('tests/sample_data/partial')

        # Act
        partial_data_assessor.assess_data_set(set_id, data_dir, self.reports_dir)

        # Assert
        report_path = os.path.join(self.reports_dir, f'report_{set_id}.md')
        assert os.path.exists(report_path)

        with open(report_path, 'r') as f:
            report_content = f.read()

        assert "## Partial Data Assessment" in report_content
        assert "### Overlap Metrics (Chi-squared)" in report_content
        assert "Parts 1↔2" in report_content
        assert "Parts 2↔3" in report_content
        
        # Check that the plot was created
        mock_savefig.assert_called_once()

    def test_get_data_files(self):
        # Create test files
        test_files = [
            'REFL_123_1_001_partial.txt',
            'REFL_123_2_002_partial.txt', 
            'REFL_123_3_003_partial.txt',
            'REFL_456_1_004_partial.txt'  # Different set
        ]
        
        for filename in test_files:
            filepath = os.path.join(self.data_dir, filename)
            with open(filepath, 'w') as f:
                f.write("# Q R dR dQ\n")
                f.write("0.01 1.0 0.1 0.001\n")

        files = partial_data_assessor.get_data_files('123', self.data_dir)
        
        assert len(files) == 3
        assert all('REFL_123' in f for f in files)
        assert files == sorted(files)  # Should be sorted

    def test_read_data(self):
        # Create test data file
        test_data = """# Q R dR dQ
0.01 1.0 0.1 0.001
0.02 0.9 0.08 0.002
0.03 0.8 0.06 0.003
"""
        test_file = os.path.join(self.data_dir, 'test_data.txt')
        with open(test_file, 'w') as f:
            f.write(test_data)

        data = partial_data_assessor.read_data(test_file)
        
        assert data.shape == (3, 4)
        assert np.allclose(data[0], [0.01, 1.0, 0.1, 0.001])
        assert np.allclose(data[1], [0.02, 0.9, 0.08, 0.002])
        assert np.allclose(data[2], [0.03, 0.8, 0.06, 0.003])

    def test_find_overlap_regions_with_overlap(self):
        # Create overlapping data
        data1 = np.array([[0.01, 1.0, 0.1, 0.001],
                         [0.02, 0.9, 0.08, 0.002],
                         [0.03, 0.8, 0.06, 0.003]])
        
        data2 = np.array([[0.025, 0.85, 0.07, 0.0025],
                         [0.03, 0.82, 0.065, 0.003],
                         [0.04, 0.7, 0.05, 0.004]])
        
        overlaps = partial_data_assessor.find_overlap_regions([data1, data2])
        
        assert len(overlaps) == 1
        overlap1, overlap2 = overlaps[0]
        
        # Should contain overlapping Q values
        assert len(overlap1) > 0
        assert len(overlap2) > 0

    def test_find_overlap_regions_no_overlap(self):
        # Create non-overlapping data
        data1 = np.array([[0.01, 1.0, 0.1, 0.001],
                         [0.02, 0.9, 0.08, 0.002]])
        
        data2 = np.array([[0.05, 0.7, 0.05, 0.005],
                         [0.06, 0.6, 0.04, 0.006]])
        
        overlaps = partial_data_assessor.find_overlap_regions([data1, data2])
        
        assert len(overlaps) == 0

    def test_find_overlap_regions_empty_input(self):
        # Test with empty or insufficient data
        assert partial_data_assessor.find_overlap_regions([]) == []
        assert partial_data_assessor.find_overlap_regions([np.array([[0.01, 1.0, 0.1, 0.001]])]) == []


if __name__ == "__main__":
    pytest.main()
