import pytest
import os
import tempfile
import shutil
import json
import numpy as np
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from analyzer_tools.analysis import result_assessor

class TestResultAssessor:
    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.reports_dir = os.path.join(self.test_dir, 'reports')
        os.makedirs(self.reports_dir)

    def teardown_method(self):
        shutil.rmtree(self.test_dir)

    @patch('matplotlib.pyplot.savefig')
    @patch('analyzer_tools.utils.summary_plots.plot_sld')
    def test_assess_result_creates_files_and_report(self, mock_plot_sld, mock_savefig):
        # Arrange
        set_id = '123'
        model_name = 'test_model'
        fit_results_dir = os.path.join(self.test_dir, 'fit_results')
        os.makedirs(fit_results_dir)

        # Create dummy data files
        refl_data = np.array([[1, 2, 3, 4, 5], [1, 2, 3, 4, 5], [1, 2, 3, 4, 5], [0.1, 0.1, 0.1, 0.1, 0.1], [1, 2, 3, 4, 5]]).T
        np.savetxt(os.path.join(fit_results_dir, 'test-refl.dat'), refl_data)
        profile_data = np.array([[1,2],[3,4]])
        np.savetxt(os.path.join(fit_results_dir, 'problem-1-profile.dat'), profile_data)

        # Act
        result_assessor.assess_result(fit_results_dir, self.reports_dir)

        # Assert
        report_path = os.path.join(self.reports_dir, f'report_fit_results.md')
        assert os.path.exists(report_path)

        with open(report_path, 'r') as f:
            report_content = f.read()

        assert '## Fit results' in report_content
        assert 'Chi-squared' in report_content
        assert f'![Fit result](fit_result_fit_results_reflectivity.svg)' in report_content
        assert f'![SLD profile](fit_result_fit_results_profile.svg)' in report_content

        assert mock_savefig.call_count == 2
        mock_plot_sld.assert_called_once()

    @patch('matplotlib.pyplot.savefig')
    @patch('analyzer_tools.utils.summary_plots.plot_sld')
    def test_assess_result_with_json_files(self, mock_plot_sld, mock_savefig):
        # Arrange
        set_id = '218281'
        model_name = 'cu_thf'
        fit_results_dir = os.path.join(self.test_dir, 'fit_results')
        os.makedirs(fit_results_dir)

        # Create dummy reflectivity data
        refl_data = np.array([[1, 2, 3, 4, 5], [1, 2, 3, 4, 5], [1, 2, 3, 4, 5], [0.1, 0.1, 0.1, 0.1, 0.1], [1, 2, 3, 4, 5]]).T
        np.savetxt(os.path.join(fit_results_dir, 'problem-1-refl.dat'), refl_data)
        
        # Create profile data
        profile_data = np.array([[1,2],[3,4]])
        np.savetxt(os.path.join(fit_results_dir, 'problem-1-profile.dat'), profile_data)

        # Create parameter file
        par_content = """intensity 0.955846
THF interface 20.9006
THF rho 5.96664
material thickness 62.0802
Cu thickness 500.102"""
        with open(os.path.join(fit_results_dir, 'problem.par'), 'w') as f:
            f.write(par_content)

        # Create error JSON file
        err_json_data = {
            "intensity": {"std": 0.0029, "mean": 0.9558},
            "THF interface": {"std": 0.69, "mean": 20.9006},
            "THF rho": {"std": 0.01, "mean": 5.96664},
            "material thickness": {"std": 0.66, "mean": 62.0802},
            "Cu thickness": {"std": 0.31, "mean": 500.102}
        }
        with open(os.path.join(fit_results_dir, 'problem-err.json'), 'w') as f:
            json.dump(err_json_data, f)

        # Create experiment JSON file
        expt_json_data = {
            "references": {
                "ref1": {"name": "intensity", "bounds": [0.95, 1.05]},
                "ref2": {"name": "THF interface", "bounds": [1.0, 25.0]},
                "ref3": {"name": "THF rho", "bounds": [4.5, 6.4]},
                "ref4": {"name": "material thickness", "bounds": [10.0, 200.0]},
                "ref5": {"name": "Cu thickness", "bounds": [400.0, 1000.0]}
            }
        }
        with open(os.path.join(fit_results_dir, 'problem-1-expt.json'), 'w') as f:
            json.dump(expt_json_data, f)

        # Create output file
        out_content = """[chisq=2.208(19), nllf=796.088]
[overall chisq=2.208(19), nllf=796.088]"""
        with open(os.path.join(fit_results_dir, 'problem.out'), 'w') as f:
            f.write(out_content)

        # Act
        result_assessor.assess_result(fit_results_dir, self.reports_dir)

        # Assert
        report_path = os.path.join(self.reports_dir, f'report_fit_results.md')
        assert os.path.exists(report_path)

        with open(report_path, 'r') as f:
            report_content = f.read()

        # Check enhanced features
        assert '| Layer | Parameter | Fitted Value | Uncertainty | Min | Max | Units |' in report_content
        assert '**Final Chi-squared**: 2.208(19) - Good fit quality' in report_content
        assert '±0.0029' in report_content  # Uncertainty from JSON
        assert '0.95' in report_content  # Min value from bounds
        assert '1.05' in report_content  # Max value from bounds

    @patch('matplotlib.pyplot.savefig')
    @patch('analyzer_tools.utils.summary_plots.plot_sld')
    def test_assess_result_with_malformed_json(self, mock_plot_sld, mock_savefig):
        # Arrange
        set_id = '456'
        model_name = 'test_model'
        fit_results_dir = os.path.join(self.test_dir, 'fit_results')
        os.makedirs(fit_results_dir)

        # Create dummy data files
        refl_data = np.array([[1, 2, 3], [1, 2, 3], [1, 2, 3], [0.1, 0.1, 0.1], [1, 2, 3]]).T
        np.savetxt(os.path.join(fit_results_dir, 'test-refl.dat'), refl_data)
        
        profile_data = np.array([[1,2],[3,4]])
        np.savetxt(os.path.join(fit_results_dir, 'problem-1-profile.dat'), profile_data)

        # Create malformed JSON file
        with open(os.path.join(fit_results_dir, 'problem-err.json'), 'w') as f:
            f.write('invalid json content')

        # Act & Assert - should not raise exception
        result_assessor.assess_result(fit_results_dir, self.reports_dir)

        report_path = os.path.join(self.reports_dir, f'report_fit_results.md')
        assert os.path.exists(report_path)

    @patch('matplotlib.pyplot.savefig')
    @patch('analyzer_tools.utils.summary_plots.plot_sld')
    def test_assess_result_no_data_file(self, mock_plot_sld, mock_savefig):
        # Arrange
        set_id = '789'
        model_name = 'test_model'
        fit_results_dir = os.path.join(self.test_dir, 'fit_results')
        os.makedirs(fit_results_dir)
        # No data files created

        # Act
        result_assessor.assess_result(fit_results_dir, self.reports_dir)

        # Assert - should handle missing files gracefully
        assert mock_savefig.call_count == 0
        assert mock_plot_sld.call_count == 0

    def test_main_function(self):
        # Test the main function with minimal arguments
        runner = CliRunner()
        
        # Create a test directory for the CLI test
        test_data_dir = os.path.join(self.test_dir, 'data')
        os.makedirs(test_data_dir)
        
        with patch('analyzer_tools.analysis.result_assessor.assess_result') as mock_assess:
            with patch('analyzer_tools.analysis.result_assessor.get_config') as mock_get_config:
                mock_config_instance = MagicMock()
                mock_config_instance.get_reports_dir.return_value = self.reports_dir
                mock_get_config.return_value = mock_config_instance
                
                result = runner.invoke(
                    result_assessor.main, 
                    [test_data_dir]
                )
                
                assert result.exit_code == 0, f"CLI failed: {result.output}"
                mock_assess.assert_called_once()

if __name__ == "__main__":
    pytest.main()
