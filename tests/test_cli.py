"""Tests for analyzer_tools.cli module."""

import pytest
from unittest.mock import patch
from click.testing import CliRunner

from analyzer_tools.cli import main


class TestCliMain:
    """Test the main CLI function."""
    
    @patch('analyzer_tools.cli.print_tool_overview')
    def test_list_tools_option(self, mock_overview):
        """Test --list-tools option."""
        runner = CliRunner()
        result = runner.invoke(main, ['--list-tools'])
        
        # Should call print_tool_overview
        assert result.exit_code == 0
        mock_overview.assert_called_once()
    
class TestCliHelpers:
    """Test CLI helper functions and edge cases."""


class TestCliIntegration:
    """Integration tests for CLI functionality."""

    def test_help_option(self):
        """Test --help option."""
        runner = CliRunner()
        result = runner.invoke(main, ['--help'])

        assert result.exit_code == 0
        assert "Neutron Reflectometry Data Analysis Tools" in result.output



