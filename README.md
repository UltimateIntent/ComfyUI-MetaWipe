# ComfyUI-MetaWipe

MetaWipe adds two practical media utility nodes to ComfyUI:
- **MetaWipe (All-in-One)**: creates cleaned copies of media files with non-essential metadata removed.
- **MetaWipe Metadata Inspector**: inspects media metadata before/after cleanup with a built-in per-file viewer.

![MetaWipe Workflow](metawipe_workflow.PNG)

## Why Use This
- Verify metadata before and after processing in one workflow
- Process inputs from many upstream sources (`STRING`, `IMAGE`, `VIDEO`, `AUDIO`, wrappers)
- Keep final outputs organized while handling intermediate files automatically

## Installation
1. Place this repository in `ComfyUI/custom_nodes/`.
2. Install Python dependencies:

```bash
pip install -r requirements.txt
```

3. Restart ComfyUI.
4. Add **MetaWipe (All-in-One)** and **MetaWipe Metadata Inspector** nodes.

## Runtime Requirements
- Python packages: `pillow`, `numpy`
- System dependency: `ffmpeg` available in PATH

## Node: MetaWipe (All-in-One)
Creates cleaned media copies with stable sequential naming.

### Inputs
- `any_input` (required): accepts media from:
  - file path strings (single or multiline)
  - directory paths
  - `IMAGE` tensors
  - `VIDEO` / `AUDIO` objects
  - compatible wrapper objects
- `recursive` (BOOLEAN, default `false`): recursively scan subfolders when directory input is provided.
- `output_subfolder` (STRING, default `%Y-%m-%d`): target folder under ComfyUI output directory.
  - Supports `strftime` tokens (`%Y`, `%m`, `%d`, etc.)
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
- `recursive` (BOOLEAN, default `false`): recursively scan subfolders for directory input.

### Outputs
- `metadata_text` (STRING): readable metadata summary.
- `metadata_json` (STRING): structured JSON metadata.
- `filepaths_out` (STRING): durable (non-ephemeral) resolved file paths.
- `any_out` (Any): structured inspection records for graph chaining.

### Viewer Behavior
- Dropdown selector lets you switch file-by-file.
- Viewer panel displays pretty JSON for the selected item.

## Typical Workflows
- **Before/after verification**
  - `Any source -> Metadata Inspector -> MetaWipe -> Metadata Inspector`
- **Direct cleanup**
  - `Any source -> MetaWipe`
- **Path-based chaining**
  - `MetaWipe.output_files -> Metadata Inspector.any_input`

## Notes on Intermediate Files
- Intermediate tensor/object materialization is stored in ComfyUI temp (`temp/metawipe_tmp/...`), not in output folders.
- Nodes perform best-effort cleanup each run.
- On Windows, if a file is temporarily locked, residue may remain only in temp and is safe to clear later.
