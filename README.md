# ComfyUI-MetaWipe

MetaWipe adds two practical media utility nodes to ComfyUI:
- **MetaWipe (All-in-One)**: creates cleaned copies of media files with non-essential metadata removed.
- **MetaWipe Metadata Inspector**: inspects media metadata before/after cleanup with a built-in per-file viewer.

![MetaWipe Workflow](metawipe_workflow.PNG)

## Why Use This
- View and clear sensitive metadata before publishing to the web
- Clean metadata from many sources - single files or entire folders and subfolders of mixed images, videos, audios
- Keep final outputs organized while retaining your original copies

## Installation
1. Clone this repository in `ComfyUI/custom_nodes/`.
```bash
git clone https://github.com/UltimateIntent/ComfyUI-MetaWipe/
```
2. Restart ComfyUI.

3. Add **MetaWipe (All-in-One)** and/or **MetaWipe Metadata Inspector** nodes to your ComfyUI workflow

## Runtime Requirements
- Python packages: `pillow`, `numpy`
- System dependency: `ffmpeg` available in PATH

## Node: MetaWipe (All-in-One)
Creates cleaned media copies with stable sequential naming.

### Inputs
- `any_input` (required): accepts media from:
  - file path strings (single or multiline)
  - directory paths
  - `IMAGE` `VIDEO` and `AUDIO` types
  - compatible wrapper objects
- `recursive` (BOOLEAN, default `false`): if true, includes subfolders in metadata cleaning
- `output_subfolder` (STRING, default `%Y-%m-%d`): target folder under ComfyUI output directory.
  - Supports `strftime` formatting (`%Y`, `%m`, `%d`, etc.)
  - Supports environment variable expansion (Windows-style `%VAR%`)
- `output_filename_prefix` (STRING, default `clean_`): prefix for saved cleaned files.

### Outputs
- `output_files` (STRING): newline-separated list of created file paths.
- `any_out` (Any): passthrough-oriented output for downstream chaining.

### Naming Rules
- Uses incrementing sequence numbers (`00000`, `00001`, ...).
- Continues from highest existing sequence in the destination folder.
- Includes original source filename in saved copy names.
- Handles collisions with `_(1)`, `_(2)`, etc.

## Node: MetaWipe Metadata Inspector
Inspects metadata and provides a selectable per-file JSON viewer in the node UI.

### Inputs
- `any_input` (required): accepts the same input forms as MetaWipe.
- `recursive` (BOOLEAN, default `false`): if true, includes subfolders in metadata viewer

### Outputs
- `metadata_text` (STRING): readable metadata summary.
- `metadata_json` (STRING): structured JSON metadata.
- `filepaths_out` (STRING): list of inspected file paths.
- `any_out` (Any): output of input for graph chaining

### Viewer Behavior
- Dropdown selector lets you switch file-by-file.
- Viewer panel displays pretty JSON for the selected item.

## Typical Workflows
- **Before/after verification**
  - `Any source -> Metadata Inspector (precleaned) -> MetaWipe -> Metadata Inspector (postclean verification)`
- **Direct cleanup**
  - `Any source -> MetaWipe`
- **Path-based chaining**
  - `MetaWipe.output_files -> Metadata Inspector.any_input`
