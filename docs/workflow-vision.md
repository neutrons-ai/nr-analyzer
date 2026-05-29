# Reflectometry Data Pipeline

> The product vision for how this package is used end-to-end. (The Spring-2026
> "upgrade project" status checklist that used to follow this narrative has been
> removed — that work shipped; the code is the source of truth, and the git
> history of `mdoucet/analyzer` retains the original plan.)

## During the Experiment
- Every experiment has a uniquer identifier, which follows the format `IPTS-<N>`, where N is a number.
- Users will align their sample and measure it. At the facility, the raw data goes into a folder named `/SNS/REF_L/IPTS-<N>/nexus`.
- A full reflectometry measurement is often make of several segments, each acquired in a separate configuration of the instrument. This is done by changing the angle of reflection and the wavelength band.
- The raw data is "reduced" from neutron events to R(Q). These are stored in `/SNS/REF_L/IPTS-<N>/shared/autoreduce`. When several runs/segments belong together, a file that combines them is also produced.

## Assessing Data Reduction
- Before moving to analysis, a SME will look at the data reduction and assess its correctness.
- This may be done by looking to artefacts in the data, or by looking at the overlap region between segments. This can point out issues like misalignment.
- Problem with the data reduction can also show up during analysis, so a coarse analysis is usually performed be moving to the full analysis phase.
- When issues are found, the reduction parameters/options may be changed, and the data for a given sample may be re-processed in batch.

## Starting the Analysis process
- Since the reflectivity data is small, it is often copied on the user's system. All the data (partial segments and combined data) are usually in the same folder.
- We will assume that the user will have a markdown file for each sample, describing the sample and how it was measured.
- From the description, we will use AuRE to generate an appropriate refl1d model file.
- The user may load that file in refl1d, or use AuRE for automated fitting.
- We then use AuRE to assess the results and produce a final human-readable output, and a markdown file with fit parameters and plots.
