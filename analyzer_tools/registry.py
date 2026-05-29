"""
Tool Registry for Neutron Reflectometry Data Analysis

Centralized catalog of available analysis tools with descriptions,
usage examples, and workflow definitions.
"""

from typing import Dict, List

class ToolInfo:
    """Information about an analysis tool."""
    
    def __init__(self, name: str, module: str, description: str, 
                 usage: str, examples: List[str], data_type: str = "both"):
        self.name = name
        self.module = module
        self.description = description
        self.usage = usage
        self.examples = examples
        self.data_type = data_type  # "partial", "combined", or "both"

# Registry of all available tools
TOOLS = {
    "partial_data_assessor": ToolInfo(
        name="Partial Data Assessor",
        module="analyzer_tools.analysis.partial_data_assessor",
        description="Assess quality of partial reflectometry data by analyzing overlap regions between data parts. Calculates chi-squared metrics and generates visualization reports.",
        usage="assess-partial <set_id>",
        examples=[
            "assess-partial 218281",
            "assess-partial 218328"
        ],
        data_type="partial"
    ),
    
    "run_fit": ToolInfo(
        name="Reflectivity Fit Runner",
        module="analyzer_tools.analysis.run_fit",
        description="Run a reflectivity fit on a complete refl1d-ready Python script (e.g. one produced by create-model). The script must define a module-level `problem = FitProblem(...)` and load its own data. Results are written to <results-dir>/<name> and an assessment is written to <reports-dir>.",
        usage="run-fit SCRIPT [--results-dir DIR] [--reports-dir DIR] [--name NAME] [--fit FITTER] [--samples N] [--burn N] [--no-assess]",
        examples=[
            "run-fit Models/cu_thf.py",
            "run-fit Models/corefine-226667-226670.py --fit dream --samples 20000",
        ],
        data_type="combined"
    ),
    
    "result_assessor": ToolInfo(
        name="Fit Result Assessor",
        module="analyzer_tools.analysis.result_assessor",
        description="Assess a refl1d fit output directory: overlay all reflectivity curves, plot every distinct SLD profile with 90%% CL bands, parse parameters/uncertainties, and write a markdown report. The directory's basename is used as the report tag (e.g. results/cu_thf → report_cu_thf.md). Optionally appends an AuRE LLM evaluation.",
        usage="assess-result <results_dir> [--output-dir DIR] [--context TEXT | --sample-description FILE] [--skip-aure-eval]",
        examples=[
            "assess-result results/cu_thf",
            "assess-result results/Cu-D2O-corefine-226642-226652-parts --skip-aure-eval",
        ],
        data_type="combined"
    ),
    
    "create_model_script": ToolInfo(
        name="Model Script Creator",
        module="analyzer_tools.analysis.create_model",
        description="Generate an analyzer-convention refl1d model script. Mode A converts an existing AuRE problem JSON (ModelDefinition or bumps-draft-03). Mode B is driven by a YAML/JSON config file (--config) with a top-level 'states:' list; each state groups data files that share one physical sample, auto-detecting per-state whether the data is one combined file (QProbe) or N partials of one set_id (make_probe per segment with one shared Sample). Structural parameters are tied across states via shared_parameters / unshared_parameters. To create many models in one shot, drive create-model from analyzer-batch.",
        usage="create-model [SOURCE.json | --config FILE.yaml] [--out models/<name>.py] [--model-name NAME]",
        examples=[
            "create-model path/to/problem.json --out models/cu_thf.py",
            "create-model --config model-creation.yaml",
            "create-model --config model-creation.yaml --out models/corefine.py --model-name corefine",
        ],
        data_type="combined"
    ),

    "theta_offset": ToolInfo(
        name="Theta Offset Calculator",
        module="analyzer_tools.analysis.theta_offset",
        description="Compute the theta offset for a Liquids Reflectometer (BL-4B) run by fitting the specular peak on the detector and comparing with the motor-log angle. Requires a NeXus event file and a pre-processed direct-beam file.",
        usage="theta-offset <nexus_file> --db <db_file>",
        examples=[
            "theta-offset REF_L_226642.nxs.h5 --db DB_226559.dat",
            "theta-offset REF_L_226642.nxs.h5 --db DB_226559.dat --ymin 135 --ymax 170",
            "theta-offset REF_L_226642.nxs.h5 --db DB_226559.dat --log offsets.csv"
        ],
        data_type="both"
    ),

    "analyze_sample": ToolInfo(
        name="Sample Pipeline Orchestrator",
        module="analyzer_tools.pipeline",
        description="End-to-end pipeline for one sample: partial assessment → reduction-issue gate → create-model → run-fit (with assess-result) → optional AuRE evaluation. Takes a YAML config file in the same shape as `create-model --config` (see analyzer_tools/skills/create-model). Emits a reduction_batch.yaml manifest when re-reduction is required, but never re-runs reduction automatically.",
        usage="analyze-sample <config.yaml> [--dry-run] [--no-reduction-gate] [--skip-aure-eval]",
        examples=[
            "analyze-sample sample_218281.yaml",
            "analyze-sample sample_218281.yaml --dry-run",
            "analyze-sample sample_218281.yaml --skip-aure-eval",
        ],
        data_type="both"
    ),

    "check_llm": ToolInfo(
        name="LLM Health Check",
        module="analyzer_tools.analysis.check_llm",
        description="Verify that the analyzer's LLM integration is ready: the aure CLI is installed, aure.llm is importable, and aure check-llm reports a working endpoint. Run this at the start of an analysis session.",
        usage="check-llm [--json] [--no-test]",
        examples=[
            "check-llm",
            "check-llm --json",
            "check-llm --no-test"
        ],
        data_type="both"
    ),

    "plan_data": ToolInfo(
        name="Data Arrival Planner",
        module="analyzer_tools.analysis.plan_data",
        description="On arrival of a new partial data file, emit a job config YAML ready for create-model --config / analyze-sample. LLM-driven: reads the data file plus a free-text context file and a set of skills, and writes a states-list config. Job-control flags (perform_assembly, notes) live in a metadata block that downstream tools ignore.",
        usage="plan-data DATA_FILE CONTEXT_FILE --output-dir DIR [--sequence-total N] [--skill NAME ...] [--result-out FILE]",
        examples=[
            "plan-data data/partial/REFL_218281_1_218281_partial.txt context.md -o plans/",
            "plan-data REFL_218281_1_218281_partial.txt context.md -o plans/ --sequence-total 3",
        ],
        data_type="partial"
    ),

    "analyzer_batch": ToolInfo(
        name="Manifest Batch Runner",
        module="analyzer_tools.batch",
        description="Dispatch multiple analyzer-tool jobs from a single YAML manifest. The manifest is pure orchestration: each job names a tool (create-model, run-fit, assess-result, analyze-sample, theta-offset, ...) and the argv to pass it. Failures in one job don't stop the others.",
        usage="analyzer-batch MANIFEST [--dry-run] [--jobs name1,name2]",
        examples=[
            "analyzer-batch manifest.yaml",
            "analyzer-batch manifest.yaml --dry-run",
            "analyzer-batch manifest.yaml --jobs pipeline_218281",
        ],
        data_type="both"
    ),

    "simple_reduction": ToolInfo(
        name="Simple Event Reduction",
        module="analyzer_tools.reduction.reduction",
        description="Reduce neutron events to a partial reflectivity curve using a Mantid reduction template, applying a theta offset given literally (--theta-offset) or looked up from a theta-offset batch CSV (--offset-csv + --offset-run). Mantid-based; Docker recommended. Can write a JSON summary (--json) and a neutral ndip-tool-result manifest (--result-out).",
        usage="simple-reduction --event-file FILE --template FILE [--output-dir DIR] [--theta-offset VAL | --offset-csv CSV --offset-run RUN] [--json FILE] [--result-out FILE]",
        examples=[
            "simple-reduction --event-file REF_L_226642.nxs.h5 --template template.xml --theta-offset -0.005",
            "simple-reduction --event-file REF_L_226642.nxs.h5 --template template.xml --offset-csv offsets.csv --offset-run 226642",
        ],
        data_type="partial"
    ),

}

# Workflow definitions
WORKFLOWS = {
    "partial_data_quality": {
        "name": "Partial Data Quality Assessment",
        "description": "Assess the quality of partial reflectometry data before combining",
        "steps": [
            "1. Use partial_data_assessor to check overlap quality",
            "2. Review chi-squared metrics (< 2.0 is typically good)",
            "3. Examine overlap plots for systematic deviations",
            "4. Identify problematic datasets for further investigation"
        ],
        "tools": ["partial_data_assessor"]
    },
    
    "standard_fitting": {
        "name": "Standard Reflectivity Fitting",
        "description": "Complete workflow for fitting reflectivity data",
        "steps": [
            "1. Use create_model_script to generate a refl1d model (AuRE)",
            "2. Use run_fit to perform fitting",
            "3. Use result_assessor to evaluate fit quality and get an LLM verdict"
        ],
        "tools": ["create_model_script", "run_fit", "result_assessor"]
    },

    "full_pipeline": {
        "name": "End-to-end Sample Pipeline",
        "description": "Single-command pipeline from partial data to final report with reduction-issue gate.",
        "steps": [
            "1. Write a sample_<id>.yaml using the create-model `--config` schema (states list, describe, model_name, ...)",
            "2. Run analyze-sample sample_<id>.yaml",
            "3. If status is needs-reprocessing, edit and run reduction_batch.yaml with analyzer-batch, then re-run analyze-sample"
        ],
        "tools": ["analyze_sample"]
    },
    
}

def get_all_tools() -> Dict[str, ToolInfo]:
    """Get all available tools."""
    return TOOLS

def get_tool(tool_name: str) -> ToolInfo:
    """Get information about a specific tool."""
    return TOOLS.get(tool_name)

def get_tools_by_data_type(data_type: str) -> Dict[str, ToolInfo]:
    """Get tools that work with a specific data type."""
    return {name: tool for name, tool in TOOLS.items() 
            if tool.data_type == data_type or tool.data_type == "both"}

def get_workflows() -> Dict[str, Dict]:
    """Get all available workflows."""
    return WORKFLOWS
