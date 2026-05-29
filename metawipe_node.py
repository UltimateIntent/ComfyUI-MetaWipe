import datetime
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import folder_paths  # ComfyUI runtime helper
except Exception:
    folder_paths = None

from PIL import Image

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".wmv", ".flv", ".mpeg", ".mpg"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".wma"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS


class AnyMediaNormalizer:
    def _expand_directory_media(self, dir_path: Path, source_kind: str, recursive: bool = False) -> List[dict]:
        out: List[dict] = []
        try:
            iterator = dir_path.rglob("*") if recursive else dir_path.iterdir()
            for p in sorted(iterator):
                if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                    out.append(self._path_record(p.resolve(), source_kind))
            if not out:
                out.append(
                    {
                        "source_kind": source_kind,
                        "media_kind": "unknown",
                        "path": str(dir_path),
                        "materialized": False,
                        "errors": ["Directory contains no supported media files."],
                    }
                )
        except Exception as ex:
            out.append(
                {
                    "source_kind": source_kind,
                    "media_kind": "unknown",
                    "path": str(dir_path),
                    "materialized": False,
                    "errors": [f"Failed to enumerate directory: {ex}"],
                }
            )
        return out

    def _resolve_base_dir(self, source_type: str) -> Path:
        source_type = (source_type or "").lower().strip()
        if folder_paths is not None:
            try:
                if source_type == "input" and hasattr(folder_paths, "get_input_directory"):
                    return Path(folder_paths.get_input_directory())
                if source_type == "temp" and hasattr(folder_paths, "get_temp_directory"):
                    return Path(folder_paths.get_temp_directory())
                if source_type == "output" and hasattr(folder_paths, "get_output_directory"):
                    return Path(folder_paths.get_output_directory())
            except Exception:
                pass
        return Path.cwd()

    def _resolve_annotated_path_dict(self, payload: dict) -> Optional[Path]:
        filename = payload.get("filename")
        if not isinstance(filename, str) or not filename.strip():
            return None
        subfolder = payload.get("subfolder") if isinstance(payload.get("subfolder"), str) else ""
        base = self._resolve_base_dir(payload.get("type") if isinstance(payload.get("type"), str) else "")
        return (base / subfolder / filename).expanduser().resolve()

    def _resolve_plugin_temp_root(self) -> Path:
        # Prefer ComfyUI temp dir so intermediate artifacts are not mixed with user outputs.
        base = self._resolve_base_dir("temp")
        if base == Path.cwd():
            base = Path.cwd() / "temp"
        root = base / "metawipe_tmp"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _create_run_temp_dir(self, scope: str) -> Path:
        scope_dir = self._resolve_plugin_temp_root() / scope
        scope_dir.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="run_", dir=str(scope_dir)))

    def _safe_rmtree(self, path: Path, retries: int = 8, delay_s: float = 0.08) -> None:
        if not path.exists():
            return

        def _onerror(func, p, exc_info):
            try:
                os.chmod(p, 0o700)
            except Exception:
                pass
            try:
                func(p)
            except Exception:
                pass

        for attempt in range(retries):
            try:
                shutil.rmtree(path, onerror=_onerror)
                return
            except Exception:
                if attempt == retries - 1:
                    return
                time.sleep(delay_s * (attempt + 1))

    def _prune_empty_temp_tree(self, scope: str) -> None:
        # Remove empty scope dir and plugin temp root when possible.
        root = self._resolve_plugin_temp_root()
        scope_dir = root / scope
        try:
            if scope_dir.exists():
                next(scope_dir.iterdir())
            # not empty
            return
        except StopIteration:
            try:
                scope_dir.rmdir()
            except Exception:
                pass
        except Exception:
            return

        try:
            next(root.iterdir())
        except StopIteration:
            try:
                root.rmdir()
            except Exception:
                pass
        except Exception:
            pass

    def _path_record(self, path: Path, source_kind: str, materialized: bool = False, errors: Optional[List[str]] = None) -> dict:
        ext = path.suffix.lower()
        media_kind = "unknown"
        if ext in IMAGE_EXTS:
            media_kind = "image"
        elif ext in VIDEO_EXTS:
            media_kind = "video"
        elif ext in AUDIO_EXTS:
            media_kind = "audio"
        return {
            "source_kind": source_kind,
            "media_kind": media_kind,
            "path": str(path),
            "materialized": materialized,
            "errors": errors or [],
        }

    def _materialize_from_tensor(self, tensor_like: Any, temp_dir: Path) -> List[dict]:
        arr = tensor_like.cpu().numpy() if hasattr(tensor_like, "cpu") else np.asarray(tensor_like)
        if arr.ndim == 3:
            arr = np.expand_dims(arr, axis=0)
        if arr.ndim != 4:
            return [{"source_kind": "image_tensor", "media_kind": "image", "path": None, "materialized": False, "errors": ["Unsupported IMAGE tensor shape."]}]

        out: List[dict] = []
        tmp_dir = temp_dir / ".metawipe_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        for frame in arr:
            frame_u8 = np.clip(frame * 255.0, 0, 255).astype(np.uint8)
            img = Image.fromarray(frame_u8)
            tmp_path = Path(tempfile.mkstemp(prefix="mw_tensor_", suffix=".png", dir=str(tmp_dir))[1])
            img.save(tmp_path, format="PNG", optimize=True)
            out.append(self._path_record(tmp_path.resolve(), "image_tensor", materialized=True))
        return out

    def _materialize_with_save_to(self, payload: Any, temp_dir: Path, ext_hint: str = ".bin") -> Optional[Path]:
        if not hasattr(payload, "save_to"):
            return None
        tmp_dir = temp_dir / ".metawipe_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = Path(tempfile.mkstemp(prefix="mw_obj_", suffix=ext_hint, dir=str(tmp_dir))[1])
        try:
            payload.save_to(str(tmp_path))
            if tmp_path.exists() and tmp_path.is_file():
                return tmp_path.resolve()
        except Exception:
            pass
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        return None

    def normalize_any_input(self, any_input: Any, temp_dir: Path, recursive: bool = False) -> List[dict]:
        records: List[dict] = []

        def walk(value: Any, seen: Optional[set] = None):
            nonlocal records
            if seen is None:
                seen = set()
            if value is None:
                return
            vid = id(value)
            if vid in seen:
                return
            seen.add(vid)

            if isinstance(value, str):
                lines = [ln.strip().strip('"').strip("'") for ln in value.splitlines()]
                for candidate in lines:
                    if not candidate:
                        continue
                    p = Path(candidate).expanduser().resolve()
                    if p.exists() and p.is_dir():
                        records.extend(self._expand_directory_media(p, "string_dir", recursive=recursive))
                    elif p.exists() and p.is_file():
                        records.append(self._path_record(p, "string_path"))
                    else:
                        records.append({"source_kind": "string_path", "media_kind": "unknown", "path": str(p), "materialized": False, "errors": ["Path does not exist or is not a file."]})
                return

            if isinstance(value, Path):
                p = value.expanduser().resolve()
                if p.exists() and p.is_dir():
                    records.extend(self._expand_directory_media(p, "path_dir", recursive=recursive))
                elif p.exists() and p.is_file():
                    records.append(self._path_record(p, "path_obj"))
                else:
                    records.append({"source_kind": "path_obj", "media_kind": "unknown", "path": str(p), "materialized": False, "errors": ["Path does not exist or is not a file."]})
                return

            if isinstance(value, dict):
                annotated = self._resolve_annotated_path_dict(value)
                if annotated is not None:
                    if annotated.exists() and annotated.is_dir():
                        records.extend(self._expand_directory_media(annotated, "annotated_dir", recursive=recursive))
                    elif annotated.exists() and annotated.is_file():
                        records.append(self._path_record(annotated, "annotated_dict"))
                    else:
                        records.append({"source_kind": "annotated_dict", "media_kind": "unknown", "path": str(annotated), "materialized": False, "errors": ["Annotated file path does not exist."]})
                    return

                for key in ("path", "full_path", "fullpath", "filepath", "filename", "file", "video", "audio", "image", "source", "sources", "files"):
                    if key in value:
                        walk(value[key], seen)
                return

            if isinstance(value, (list, tuple, set)):
                for item in value:
                    walk(item, seen)
                return

            # IMAGE tensors / ndarray
            if hasattr(value, "cpu") or isinstance(value, np.ndarray):
                records.extend(self._materialize_from_tensor(value, temp_dir))
                return

            # fspath-capable object
            if hasattr(value, "__fspath__"):
                try:
                    walk(value.__fspath__(), seen)
                    return
                except Exception:
                    pass

            # common attrs
            for attr in (
                "path",
                "full_path",
                "fullpath",
                "filepath",
                "full_file_path",
                "file_path",
                "filename",
                "file",
                "source",
                "folder",
                "directory",
            ):
                if hasattr(value, attr):
                    try:
                        walk(getattr(value, attr), seen)
                        return
                    except Exception:
                        pass

            # private file attrs in wrappers
            for attr in dir(value):
                if "file" not in attr.lower():
                    continue
                if attr.startswith("__") and attr.endswith("__"):
                    continue
                try:
                    nested = getattr(value, attr)
                except Exception:
                    continue
                if isinstance(nested, int):
                    continue
                before = len(records)
                walk(nested, seen)
                if len(records) > before:
                    return

            # save_to fallback
            mat = self._materialize_with_save_to(value, temp_dir, ".mp4")
            if mat is not None:
                records.append(self._path_record(mat, type(value).__name__, materialized=True))
                return

            records.append({"source_kind": type(value).__name__, "media_kind": "unknown", "path": None, "materialized": False, "errors": ["Unsupported input type."]})

        walk(any_input)
        if not records:
            records.append(
                {
                    "source_kind": type(any_input).__name__,
                    "media_kind": "unknown",
                    "path": None,
                    "materialized": False,
                    "errors": ["No resolvable media paths were extracted from any_input payload."],
                }
            )
        return records


class MetaWipeAllInOne(AnyMediaNormalizer):
    def __init__(self):
        self._next_num_cache = {}

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "any_input": ("*", {"forceInput": True}),
                "output_subfolder": ("STRING", {"default": "%Y-%m-%d", "multiline": False}),
                "output_filename_prefix": ("STRING", {"default": "clean_", "multiline": False}),
                "recursive": ("BOOLEAN", {"default": False}),
            }
        }

    RETURN_TYPES = ("STRING", "*")
    RETURN_NAMES = ("output_files", "any_out")
    FUNCTION = "process"
    CATEGORY = "MetaWipe"
    OUTPUT_NODE = True

    def process(
        self,
        any_input: Any,
        output_subfolder: str,
        output_filename_prefix: str,
        recursive: bool = False,
    ) -> Tuple[str, Any]:
        out_dir = self._resolve_output_dir(output_subfolder)
        out_dir.mkdir(parents=True, exist_ok=True)
        run_temp = self._create_run_temp_dir("metawipe")
        try:
            normalized = self.normalize_any_input(any_input, run_temp, recursive=recursive)
            output_paths: List[Path] = []

            for rec in normalized:
                p = rec.get("path")
                errs = rec.get("errors") or []
                if errs or not p:
                    continue
                src = Path(p)
                if not (src.exists() and src.is_file()):
                    continue
                if src.suffix.lower() not in MEDIA_EXTS:
                    continue
                output_paths.append(self._duplicate_media_file(src, out_dir, output_filename_prefix))

            if not output_paths:
                raise ValueError("No valid media inputs were resolved from any_input.")

            out_records = [{"path": str(p), "media_kind": self._media_kind_for_suffix(p.suffix.lower())} for p in output_paths]
            any_out = self._build_any_out_passthrough(any_input, out_records)
            return ("\n".join(str(p) for p in output_paths), any_out)
        finally:
            self._safe_rmtree(run_temp)
            self._prune_empty_temp_tree("metawipe")

    def _build_any_out_passthrough(self, any_input: Any, fallback_records: List[dict]) -> Any:
        # Preserve media-compatible passthrough for downstream typed nodes.
        # IMAGE tensors: keep tensor-like object for SaveImage compatibility.
        if hasattr(any_input, "shape") and hasattr(any_input, "dtype"):
            return any_input
        if hasattr(any_input, "cpu") and hasattr(any_input, "numpy"):
            return any_input
        if isinstance(any_input, np.ndarray):
            return any_input

        # VIDEO/AUDIO wrapper objects in Comfy often expose save_to().
        # Keep these objects as passthrough so downstream typed nodes can consume them.
        if hasattr(any_input, "save_to") and not isinstance(any_input, (str, Path, dict, list, tuple, set)):
            return any_input

        # Single-item list/tuple/set wrappers should preserve typed media objects too.
        if isinstance(any_input, (list, tuple, set)) and len(any_input) == 1:
            first = next(iter(any_input))
            if hasattr(first, "save_to") or (hasattr(first, "shape") and hasattr(first, "dtype")):
                return any_input

        return fallback_records

    def _media_kind_for_suffix(self, ext: str) -> str:
        if ext in IMAGE_EXTS:
            return "image"
        if ext in VIDEO_EXTS:
            return "video"
        if ext in AUDIO_EXTS:
            return "audio"
        return "unknown"

    def _resolve_output_dir(self, subfolder: str) -> Path:
        if folder_paths is not None:
            base = Path(folder_paths.get_output_directory())
        else:
            base = Path.cwd() / "output"
        rendered = datetime.datetime.now().strftime(subfolder.strip())
        rendered = os.path.expandvars(rendered)
        return base / rendered

    def _next_number(self, out_dir: Path, prefix: str) -> int:
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)(?:_.+)?$")
        max_num = -1
        for p in out_dir.iterdir():
            if not p.is_file():
                continue
            m = pattern.match(p.stem)
            if m:
                try:
                    max_num = max(max_num, int(m.group(1)))
                except ValueError:
                    pass
        return max_num + 1

    def _sanitize_label(self, label: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", (label or "").strip())
        cleaned = re.sub(r"\s+", "_", cleaned)
        return cleaned[:120] if cleaned else "file"

    def _build_target_path(self, out_dir: Path, prefix: str, ext: str, source_label: Optional[str] = None) -> Path:
        key = (str(out_dir), prefix)
        if key not in self._next_num_cache:
            self._next_num_cache[key] = self._next_number(out_dir, prefix)

        num = self._next_num_cache[key]
        safe_label = self._sanitize_label(source_label) if source_label else ""
        base_name = f"{prefix}{num:05d}"
        if safe_label:
            base_name = f"{base_name}_{safe_label}"

        candidate = out_dir / f"{base_name}{ext}"
        if not candidate.exists():
            self._next_num_cache[key] = num + 1
            return candidate

        i = 1
        while True:
            alt = out_dir / f"{base_name}_({i}){ext}"
            if not alt.exists():
                self._next_num_cache[key] = num + 1
                return alt
            i += 1

    def _duplicate_media_file(self, src: Path, out_dir: Path, prefix: str) -> Path:
        ext = src.suffix.lower()
        dst = self._build_target_path(out_dir, prefix, ext, source_label=src.stem)
        if ext in IMAGE_EXTS:
            self._rewrite_image_clean(src, dst)
        else:
            self._rewrite_av_clean(src, dst)
        return dst

    def _rewrite_image_clean(self, src: Path, dst: Path) -> None:
        with Image.open(src) as im:
            cleaned = Image.new(im.mode, im.size)
            cleaned.putdata(list(im.getdata()))
            save_kwargs = {}
            if im.format == "JPEG":
                save_kwargs.update({"quality": 95, "optimize": True})
            elif im.format == "PNG":
                save_kwargs.update({"optimize": True})
            cleaned.save(dst, format=im.format, **save_kwargs)

    def _rewrite_av_clean(self, src: Path, dst: Path) -> None:
        cmd = [
            "ffmpeg", "-y", "-i", str(src), "-map", "0", "-map_metadata", "-1", "-map_chapters", "-1", "-c", "copy", str(dst)
        ]
        run = subprocess.run(cmd, capture_output=True, text=True)
        if run.returncode == 0:
            return
        fallback = ["ffmpeg", "-y", "-i", str(src), "-map_metadata", "-1", "-map_chapters", "-1", str(dst)]
        run2 = subprocess.run(fallback, capture_output=True, text=True)
        if run2.returncode != 0:
            raise RuntimeError(f"ffmpeg failed for {src}: {run2.stderr or run.stderr}")


class MetaWipeMetadataInspector(AnyMediaNormalizer):
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "any_input": ("*", {"forceInput": True}),
                "recursive": ("BOOLEAN", {"default": False}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "*")
    RETURN_NAMES = ("metadata_text", "metadata_json", "filepaths_out", "any_out")
    FUNCTION = "inspect"
    CATEGORY = "MetaWipe"
    OUTPUT_NODE = True

    def inspect(self, any_input: Any, recursive: bool = False) -> dict:
        temp_base = self._create_run_temp_dir("inspector")
        try:
            normalized = self.normalize_any_input(any_input, temp_base, recursive=recursive)
            entries: List[dict] = []
            for rec in normalized:
                p = rec.get("path")
                if p:
                    entries.append(self._inspect_path(Path(p), rec))
                else:
                    entries.append(
                        {
                            "path": None,
                            "name": None,
                            "extension": None,
                            "exists": False,
                            "is_file": False,
                            "status": "error",
                            "source_kind": rec.get("source_kind", "unknown"),
                            "materialized": rec.get("materialized", False),
                            "errors": rec.get("errors") or ["No path could be resolved from input item."],
                        }
                    )

            if not entries:
                raise ValueError(
                    f"No inputs were provided on required connector 'any_input' (payload_type={type(any_input).__name__})."
                )

            metadata_text = self._format_metadata_text(entries)
            metadata_json = json.dumps(entries, indent=2, ensure_ascii=False)
            # Durable output: exclude ephemeral/materialized temp paths that will be removed during cleanup.
            filepaths = [e["path"] for e in entries if e.get("path") and not e.get("materialized")]
            filepaths_out = "\n".join(filepaths)

            # Keep full pass-through records for graph chaining, but mark ephemeral entries explicitly.
            any_out_entries = []
            for e in entries:
                out_e = dict(e)
                if out_e.get("materialized"):
                    out_e["ephemeral_path"] = True
                    out_e.setdefault("warnings", []).append(
                        "Path is materialized in a temporary folder and will be deleted after node execution."
                    )
                else:
                    out_e["ephemeral_path"] = False
                any_out_entries.append(out_e)

            ui_items = [
                {
                    "label": f"{(e.get('name') or 'unknown')}" if i == 0 else f"{e.get('name') or 'unknown'}",
                    "path": e.get("path"),
                    "status": e.get("status", "ok"),
                    "metadata_text": self._format_single_entry(e),
                    "metadata_json": json.dumps(e, indent=2, ensure_ascii=False),
                }
                for i, e in enumerate(entries)
            ]

            return {
                "ui": {"metadata_items": ui_items},
                "result": (metadata_text, metadata_json, filepaths_out, any_out_entries),
            }
        finally:
            self._safe_rmtree(temp_base)
            self._prune_empty_temp_tree("inspector")

    def _inspect_path(self, path: Path, rec: dict) -> dict:
        entry = {
            "path": str(path),
            "name": path.name,
            "extension": path.suffix.lower(),
            "exists": path.exists(),
            "is_file": path.is_file(),
            "status": "ok",
            "source_kind": rec.get("source_kind", "unknown"),
            "materialized": rec.get("materialized", False),
            "errors": list(rec.get("errors") or []),
        }

        if not path.exists():
            entry["status"] = "error"
            entry["errors"].append("File does not exist.")
            return entry
        if not path.is_file():
            entry["status"] = "error"
            entry["errors"].append("Path exists but is not a file.")
            return entry

        try:
            st = path.stat()
            entry["size_bytes"] = st.st_size
            entry["modified"] = datetime.datetime.fromtimestamp(st.st_mtime).isoformat()
        except Exception as ex:
            entry["errors"].append(f"stat failed: {ex}")
            entry["status"] = "warning"

        ffprobe = self._ffprobe(path)
        if ffprobe is not None:
            entry["ffprobe"] = ffprobe

        if path.suffix.lower() in IMAGE_EXTS:
            image_info = self._image_metadata(path)
            if image_info:
                entry["image"] = image_info

        if not entry.get("ffprobe") and not entry.get("image"):
            entry["status"] = "warning" if entry["status"] == "ok" else entry["status"]
            entry["errors"].append("No media metadata was extracted (ffprobe/PIL unavailable or unsupported file).")

        return entry

    def _ffprobe(self, path: Path) -> Optional[dict]:
        cmd = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
        try:
            run = subprocess.run(cmd, capture_output=True, text=True)
            if run.returncode != 0:
                return None
            data = json.loads(run.stdout or "{}")
            out = {}
            fmt = data.get("format")
            if isinstance(fmt, dict):
                out["format"] = {
                    "format_name": fmt.get("format_name"),
                    "duration": fmt.get("duration"),
                    "size": fmt.get("size"),
                    "bit_rate": fmt.get("bit_rate"),
                    "tags": fmt.get("tags", {}),
                }
            streams = data.get("streams")
            if isinstance(streams, list):
                out["streams"] = [
                    {
                        "index": s.get("index"),
                        "codec_type": s.get("codec_type"),
                        "codec_name": s.get("codec_name"),
                        "width": s.get("width"),
                        "height": s.get("height"),
                        "sample_rate": s.get("sample_rate"),
                        "channels": s.get("channels"),
                        "tags": s.get("tags", {}),
                    }
                    for s in streams
                    if isinstance(s, dict)
                ]
            return out or None
        except Exception:
            return None

    def _image_metadata(self, path: Path) -> Optional[dict]:
        try:
            with Image.open(path) as im:
                out = {"format": im.format, "mode": im.mode, "width": im.width, "height": im.height}
                info = {}
                for k, v in (im.info or {}).items():
                    try:
                        info[str(k)] = str(v)[:500]
                    except Exception:
                        pass
                if info:
                    out["info"] = info
                try:
                    exif_data = im.getexif()
                    if exif_data:
                        out["exif"] = {str(k): str(v)[:500] for k, v in exif_data.items()}
                except Exception:
                    pass
                return out
        except Exception:
            return None

    def _format_single_entry(self, entry: dict) -> str:
        lines = [
            f"Path: {entry.get('path', '')}",
            f"Status: {entry.get('status', 'ok')}",
            f"Source: {entry.get('source_kind', '')}",
            f"Extension: {entry.get('extension', '')}",
        ]
        if "size_bytes" in entry:
            lines.append(f"Size (bytes): {entry.get('size_bytes')}")
        if "modified" in entry:
            lines.append(f"Modified: {entry.get('modified')}")
        ff = entry.get("ffprobe")
        if isinstance(ff, dict):
            fmt = ff.get("format", {})
            lines.append(f"Format: {fmt.get('format_name', '')}")
            lines.append(f"Duration: {fmt.get('duration', '')}")
            lines.append(f"Bitrate: {fmt.get('bit_rate', '')}")
            streams = ff.get("streams") or []
            lines.append(f"Streams: {len(streams)}")
        im = entry.get("image")
        if isinstance(im, dict):
            lines.append(f"Image: {im.get('format', '')} {im.get('width', '')}x{im.get('height', '')} {im.get('mode', '')}")
        errs = entry.get("errors") or []
        if errs:
            lines.append("Errors:")
            lines.extend([f"- {e}" for e in errs])
        return "\n".join(lines)

    def _format_metadata_text(self, entries: List[dict]) -> str:
        parts = []
        for i, e in enumerate(entries):
            parts.append(f"[{i + 1}] {Path(e.get('path') or 'unknown').name}")
            parts.append(self._format_single_entry(e))
            parts.append("")
        return "\n".join(parts).strip()


NODE_CLASS_MAPPINGS = {
    "MetaWipeAllInOne": MetaWipeAllInOne,
    "MetaWipeMetadataInspector": MetaWipeMetadataInspector,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MetaWipeAllInOne": "MetaWipe (All-in-One)",
    "MetaWipeMetadataInspector": "MetaWipe Metadata Inspector",
}
